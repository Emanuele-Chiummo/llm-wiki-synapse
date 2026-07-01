"""
Thin ingest seam — the ONLY path through which files enter Postgres and Qdrant (ADR-0003, I6).

Public API (called by watcher.py and POST /ingest/trigger):
  ingest_file(file_path)  -> IngestResult   (K6, ADR-0001, ADR-0002)
  delete_file(file_path)  -> None           (soft-delete, ADR-0005)

v0.1 is MECHANICAL ONLY — no LLM call, no provider, no model id.
The mtime-then-hash gate (ADR-0001) lives here so both the watcher and the REST
endpoint share the same change-detection logic.

Factored helpers that v0.2's orchestrated loop will reuse as primitives:
  persist_metadata  — Postgres upsert
  upsert_vector     — embed via EmbeddingClient + Qdrant upsert
  append_log        — K4 append-only log line
  bump_version      — vault_state.data_version +1

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  F17 / I6 EXTENSION POINT — v0.2 slot (DO NOT IMPLEMENT HERE)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  After the hash gate confirms a real content change, v0.2 will:
    1. Resolve an InferenceProvider from provider_config (ADR-0003, CLAUDE.md §5).
    2. Call provider.capabilities() to determine routing:
         - supports_agentic_loop == True (CliAgentProvider):
             delegate full ingest to the CLI agent; skip the orchestrated loop.
         - otherwise (OllamaProvider / ApiProvider):
             run the ORCHESTRATED LOOP:
               analyze(source_text) → Analysis
               generate(analysis)  → list[WikiPage]
               validate(pages)     → ok | augment & retry (max_iter, token_budget)
             log total_cost_usd per run (I7).
    3. After the provider produces WikiPage(s), call persist_metadata /
       upsert_vector / append_log / bump_version for each page (reusing helpers below).

  In v0.1 the branch below the hash gate goes directly to the mechanical helpers.
  v0.2 inserts the provider selection and loop before persist_metadata is called.
  The callers (watcher, REST endpoint) are NOT touched in v0.2 (ADR-0003 guarantee).
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import frontmatter  # python-frontmatter
import httpx

from app.config import settings
from app.db import get_session
from app.embeddings import get_embedding_client
from app.ingest.loop import LoopResult, run_orchestrated_loop
from app.ingest.provider import resolve_provider
from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.schemas import (
    Analysis,
    WikiPage,
    type_subdir,
)
from app.models import IngestRun, Page, VaultState
from app.qdrant_client import delete_point, upsert_point

logger = logging.getLogger(__name__)

# Cost-anomaly threshold (AQ-v0.2-8 / ADR-0009 §3) — inline WARNING site, not a hook.
COST_ANOMALY_THRESHOLD_USD = 1.00


# ── Result type ────────────────────────────────────────────────────────────────


class IngestResult:
    """Return value of ingest_file; carries status for the REST response (ADR-0006)."""

    __slots__ = ("page_id", "status")

    def __init__(
        self,
        page_id: uuid.UUID,
        status: Literal["completed", "skipped"],
    ) -> None:
        self.page_id = page_id
        self.status = status

    def __repr__(self) -> str:
        return f"IngestResult(page_id={self.page_id}, status={self.status!r})"


# ── Public entry points ────────────────────────────────────────────────────────


async def ingest_file(file_path: str | Path) -> IngestResult:
    """
    Ingest a single file from vault/raw/sources/ into Postgres + Qdrant.

    Change gate (ADR-0001 mtime-then-hash):
      1. stat → if mtime_ns == stored → SKIP (fast path, no I/O).
      2. mtime differs → read + sha256; if hash == stored → touch mtime only, SKIP.
      3. hash differs (or new file) → upsert Postgres → embed → upsert Qdrant →
         append log.md → bump data_version.

    Returns IngestResult with status="completed"|"skipped".

    The F17 extension point (v0.2) slots in at step 3, between the hash gate and
    persist_metadata — see module docstring above.
    """
    path = Path(file_path)
    rel = _relative_path(path)

    # ── Stat ──────────────────────────────────────────────────────────────────
    try:
        stat = path.stat()
    except FileNotFoundError:
        logger.warning("ingest_file: path not found %s — skipping", path)
        raise

    current_mtime_ns: int = stat.st_mtime_ns

    # ── Load existing DB row (if any) ─────────────────────────────────────────
    existing = await _load_page(rel)

    # ── Fast path: mtime unchanged → skip (ADR-0001 step 1) ──────────────────
    if (
        existing is not None
        and existing.source_mtime_ns is not None
        and existing.source_mtime_ns == current_mtime_ns
    ):
        logger.debug("ingest_file: mtime unchanged — skip %s", rel)
        return IngestResult(page_id=existing.id, status="skipped")

    # ── Read bytes + compute hash (ADR-0001 step 2) ───────────────────────────
    raw_bytes = path.read_bytes()
    current_hash = _sha256(raw_bytes)

    # ── Hash unchanged → touch mtime only, skip (ADR-0001 step 2b) ───────────
    if existing is not None and existing.content_hash == current_hash:
        logger.debug("ingest_file: mtime changed but hash identical — touch mtime %s", rel)
        await _touch_mtime(existing.id, current_mtime_ns)
        return IngestResult(page_id=existing.id, status="skipped")

    # ── Parse frontmatter (K6 — tolerant: missing fields → NULL) ─────────────
    meta = _parse_frontmatter(raw_bytes, rel)

    # ─────────────────────────────────────────────────────────────────────────
    # F17 EXTENSION POINT (v0.2): if a provider is configured for this vault, run the
    # capability-aware pipeline (analyze → generate → validate loop OR CLI delegation)
    # to produce wiki pages from the source, BEFORE the source row is persisted
    # (ADR-0003). When no provider_config row resolves, fall through to the v0.1
    # mechanical path (source-only indexing) — never silently pick a backend (I6).
    # ─────────────────────────────────────────────────────────────────────────
    provider_cfg = await _resolve_ingest_provider_config()
    if provider_cfg is not None:
        source_text = raw_bytes.decode("utf-8", errors="replace")
        await run_ingest_pipeline(
            provider_config_row=provider_cfg,
            source_text=source_text,
            origin_source=rel,
        )

    # ── Persist metadata to Postgres (step 3) ─────────────────────────────────
    page_id = existing.id if existing is not None else uuid.uuid4()
    _title_val = meta.get("title")
    _title: str | None = str(_title_val) if _title_val is not None else None
    _type_val = meta.get("type")
    _type: str | None = str(_type_val) if _type_val is not None else None
    _sources_raw = meta.get("sources")
    _sources: list[str] | None = (
        [str(s) for s in _sources_raw] if isinstance(_sources_raw, list) else None
    )
    # K6 navigation tags — round-trip any tags present in a raw source's frontmatter (additive;
    # absent → NULL). Kept tolerant/mechanical here, exactly like sources.
    _tags_raw = meta.get("tags")
    _tags: list[str] | None = (
        [str(t) for t in _tags_raw] if isinstance(_tags_raw, list) else None
    )
    await persist_metadata(
        page_id=page_id,
        vault_id=settings.vault_id,
        file_path=rel,
        title=_title,
        page_type=_type,
        sources=_sources,
        tags=_tags,
        content_hash=current_hash,
        source_mtime_ns=current_mtime_ns,
    )

    # ── Embed + upsert Qdrant ─────────────────────────────────────────────────
    text_for_embedding = raw_bytes.decode("utf-8", errors="replace")
    await upsert_vector(
        page_id=page_id,
        text=text_for_embedding,
        file_path=rel,
        title=_title,
        page_type=_type,
    )

    # ── K4 append log line ────────────────────────────────────────────────────
    await append_log(rel)

    # ── Bump vault_state.data_version ─────────────────────────────────────────
    await bump_version()

    # ── Notify GraphCache of the version bump (I2, ADR-0014 §2) ──────────────
    # Minimal hook: call notify_bump() on the module-level cache singleton if it
    # has been initialised (lifespan). No-op in test envs without the lifespan.
    # DO NOT alter provider/loop logic here (NB-1/NB-4 guard).
    try:
        from app.main import _graph_cache

        if _graph_cache is not None:
            async with get_session() as _vs_sess:
                from sqlalchemy import select

                _vs_row = await _vs_sess.execute(
                    select(VaultState).where(VaultState.vault_id == settings.vault_id)
                )
                _vs = _vs_row.scalar_one_or_none()
                _new_version = _vs.data_version if _vs is not None else 0
            _graph_cache.notify_bump(_new_version)
    except Exception:  # noqa: BLE001
        # Non-fatal: the graph cache will self-heal via the polling fallback
        logger.debug("ingest_file: graph cache notify_bump skipped (cache not ready)")

    logger.info("ingest_file: completed %s page_id=%s", rel, page_id)
    return IngestResult(page_id=page_id, status="completed")


async def delete_file(file_path: str | Path) -> None:
    """
    Soft-delete the page for *file_path* and hard-remove its Qdrant point (ADR-0005).

    Sets pages.deleted_at = now(); leaves all other columns intact.
    The Qdrant point is hard-deleted (soft-deleted pages must not surface in search).
    Does NOT bump data_version in v0.1 (ADR-0005 note; revisited in v0.3 with graph).
    """
    from sqlalchemy import select, update

    rel = _relative_path(Path(file_path))

    async with get_session() as session:
        row = await session.execute(
            select(Page).where(
                Page.vault_id == settings.vault_id,
                Page.file_path == rel,
                Page.deleted_at.is_(None),
            )
        )
        page = row.scalar_one_or_none()
        if page is None:
            logger.warning("delete_file: no live page found for %s — nothing to do", rel)
            return

        page_id = page.id
        now = datetime.now(UTC)
        await session.execute(
            update(Page).where(Page.id == page_id).values(deleted_at=now, updated_at=now)
        )

    # Hard-delete Qdrant point (ADR-0002 asymmetric soft/hard)
    await delete_point(page_id)
    logger.info("delete_file: soft-deleted %s page_id=%s", rel, page_id)


# ── F17 capability-aware pipeline (v0.2) ──────────────────────────────────────


@dataclass
class IngestRunResult:
    """Summary of one F17 ingest run (returned by run_ingest_pipeline)."""

    route: Literal["orchestrated", "delegated"]
    pages_written: int
    total_tokens: int
    total_cost_usd: float
    converged: bool
    cost_anomaly: bool


async def _resolve_ingest_provider_config() -> object | None:
    """
    Resolve the provider_config row for operation='ingest' via the ConfigResolver
    (operation>vault>global, ADR-0008 §2). Returns None when no row is configured so that
    the v0.1 mechanical path runs — never silently defaulting a backend (I6).

    This is the ONLY place the orchestrator obtains a provider config (centralized resolution).
    Tests inject a config row by monkeypatching this function.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    try:
        return await resolve_provider_config("ingest")
    except ConfigNotFoundError:
        # No provider configured for this vault → v0.1 mechanical path (source-only indexing).
        logger.debug(
            "_resolve_ingest_provider_config: no provider_config row found — "
            "falling through to mechanical ingest (I6: no silent backend default)"
        )
        return None
    except (SQLAlchemyError, OSError):
        # DB unreachable or table missing (e.g. test env without migration, no live Postgres).
        # Fall through to the v0.1 mechanical path — the migration gates provider use.
        logger.debug(
            "_resolve_ingest_provider_config: DB unavailable / table missing — "
            "falling through to mechanical ingest"
        )
        return None


async def run_ingest_pipeline(
    *,
    provider_config_row: object,
    source_text: str,
    origin_source: str,
) -> IngestRunResult:
    """
    Capability-aware ingest (F17 / I6). Resolves the provider from config, reads
    capabilities(), and ROUTES:

      capabilities().supports_agentic_loop is True  → delegate the whole ingest (CLI)
      otherwise                                     → run the orchestrated bounded loop

    Routing reads ONLY `supports_agentic_loop` — NEVER isinstance / type / class-name /
    provider_type (the I6 hard rule, ADR-0007 §3). Writes each produced WikiPage via the
    shared `write_wiki_page` primitive (I1/I5), updates overview.md (F3), writes one
    `ingest_runs` row (I7), and runs the inline $1 cost-anomaly check (AQ-v0.2-8).
    """
    provider = resolve_provider(provider_config_row)
    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)
    caps = provider.capabilities()

    started_at = datetime.now(UTC)
    pages: list[WikiPage] = []
    analysis: Analysis | None = None
    iterations = 0
    delegated_pages_written = 0
    converged = False
    route: Literal["orchestrated", "delegated"] = "orchestrated"

    # ── ROUTE: the single capability check (I6) ──────────────────────────────
    # Wrapped so a route failure still persists an ingest_runs row with status="failed" and the
    # error_message + accumulated cost (BUG A2 / I7), then re-raises so the REST/watcher caller
    # surfaces the error unchanged.
    try:
        if caps.supports_agentic_loop:
            route = "delegated"
            converged, delegated_pages_written, delegated_page_ids = await _delegate_ingest(
                provider=provider,
                source_text=source_text,
                origin_source=origin_source,
            )
            # ── F9 delegated-route proposals (ADR-0044 §4.2, closes ADR-0034 §9 risk 1) ─
            # Load the pages the CLI agent wrote through MCP write_page, synthesize a minimal
            # Analysis, and drive the SAME bounded propose_reviews seam (≤1 provider call, same
            # degrade). Empty record → early-return (its `if not written_pages` guard) → zero
            # cost, zero proposals. Fire-and-forget: NEVER raises into ingest (Do-NOT #5).
            # Capability-agnostic — no isinstance/provider_type branch (I6).
            try:
                await _propose_reviews_for_delegated(
                    vault_id=settings.vault_id,
                    written_page_ids=delegated_page_ids,
                    origin_source=origin_source,
                )
            except Exception as _f9d_exc:  # noqa: BLE001
                logger.warning(
                    "run_ingest_pipeline: F9 delegated propose_reviews hook failed "
                    "(non-fatal): %s",
                    _f9d_exc,
                )
            # Sweep after the delegated run too (same fire-and-forget contract as orchestrated).
            try:
                from app.ops.review import sweep_reviews as _sweep_reviews_deleg

                await _sweep_reviews_deleg(settings.vault_id)
            except Exception as _sweep_d_exc:  # noqa: BLE001
                logger.warning(
                    "run_ingest_pipeline: F9 delegated sweep_reviews hook failed "
                    "(non-fatal): %s",
                    _sweep_d_exc,
                )
        else:
            # route is already "orchestrated" (default above) — explicit for readers.
            route = "orchestrated"
            loop_result = await _run_orchestrated(
                provider=provider,
                accumulator=accumulator,
                source_text=source_text,
                origin_source=origin_source,
                config_row=provider_config_row,
            )
            pages = loop_result.pages
            analysis = loop_result.analysis
            iterations = loop_result.iterations
            converged = loop_result.converged
            # Guarantee a source-summary page (F3) even if the provider omitted it.
            pages = _ensure_source_summary(pages, analysis, origin_source)
            written_pages: list[Page] = []
            for page in pages:
                written_page = await write_wiki_page(None, page, origin_source)
                written_pages.append(written_page)
            await _update_overview(analysis, origin_source)

            # ── F4 post-write hook: wikilink enrichment (ADR-0036) ───────────────────
            # Runs BEFORE propose_reviews (so proposals see the enriched link graph) and AFTER
            # all pages are written (so every just-written title is linkable). Fire-and-forget:
            # NEVER raises into the ingest critical path — pages are already written and valid
            # (ADR-0036 §4 / Do-NOT #9). Restores the F4 "direct link ×3" signal.
            try:
                from app.ops.enrich_wikilinks import enrich_wikilinks as _enrich_wikilinks

                _enrich = await _enrich_wikilinks(written_pages, settings.vault_id)
                logger.info(
                    "run_ingest_pipeline: wikilink enrichment pages=%d links=%d cost_usd=%.4f%s",
                    _enrich.pages_enriched,
                    _enrich.links_added,
                    _enrich.total_cost_usd,
                    f" (skipped: {_enrich.skipped_reason})" if _enrich.skipped_reason else "",
                )
            except Exception as _enrich_exc:  # noqa: BLE001
                logger.warning(
                    "run_ingest_pipeline: wikilink enrichment hook failed (non-fatal): %s",
                    _enrich_exc,
                )

            # ── F9 post-write hook: propose_reviews + sweep_reviews (ADR-0034 §4/§6) ─
            # Fire-and-forget: NEVER raises into the ingest critical path (Do-NOT #5, ADR-0034 §10).
            # Replaces _enqueue_review_items from ADR-0025. Runs only on the orchestrated branch
            # (delegated/CLI path is a reserved follow-up — ADR-0034 §9 risk 1).
            try:
                from app.ops.review import propose_reviews as _propose_reviews

                await _propose_reviews(
                    vault_id=settings.vault_id,
                    analysis=analysis,
                    written_pages=written_pages,
                    origin_source=origin_source,
                )
            except Exception as _f9_exc:  # noqa: BLE001
                # Intentionally swallowed: pages are written; queue is advisory (Do-NOT #5).
                logger.warning(
                    "run_ingest_pipeline: F9 propose_reviews hook failed (non-fatal): %s",
                    _f9_exc,
                )

            # Sweep: auto-resolve stale missing-page/duplicate proposals now that the wiki grew.
            # Also fire-and-forget; never fails ingest.
            try:
                from app.ops.review import sweep_reviews as _sweep_reviews_post

                await _sweep_reviews_post(settings.vault_id)
            except Exception as _sweep_exc:  # noqa: BLE001
                logger.warning(
                    "run_ingest_pipeline: F9 sweep_reviews hook failed (non-fatal): %s",
                    _sweep_exc,
                )
    except Exception as exc:
        # Persist a failed-run row (I7 ledger stays truthful: cost incurred before the failure is
        # still recorded) then re-raise so the caller's error handling is unchanged.
        finished_at = datetime.now(UTC)
        await _write_ingest_run(
            page_id=None,
            provider_name=caps.name,
            provider_type=caps.mode,
            model_id=str(getattr(provider_config_row, "model_id", "")),
            route=route,
            max_iter_used=iterations,
            total_tokens=accumulator.total_tokens,
            total_cost_usd=round(accumulator.total_cost_usd, 4),
            converged=False,
            cost_anomaly=round(accumulator.total_cost_usd, 4) > COST_ANOMALY_THRESHOLD_USD,
            started_at=started_at,
            finished_at=finished_at,
            pages_created=0,
            error_message=str(exc) or exc.__class__.__name__,
        )
        logger.warning(
            "ingest_run FAILED provider=%s origin=%s error=%s",
            caps.name,
            origin_source,
            exc,
        )
        raise

    finished_at = datetime.now(UTC)

    # Actual pages persisted this run (BUG A2): the orchestrated branch writes len(pages)
    # (post source-summary guarantee); the delegated branch reports its own count.
    pages_written = delegated_pages_written if caps.supports_agentic_loop else len(pages)

    # ── Finalize accumulator → ingest_runs row (I7, ADR-0008 §4) ──────────────
    total_tokens = accumulator.total_tokens
    total_cost_usd = round(accumulator.total_cost_usd, 4)
    cost_anomaly = total_cost_usd > COST_ANOMALY_THRESHOLD_USD

    await _write_ingest_run(
        page_id=None,
        provider_name=caps.name,
        provider_type=caps.mode,
        model_id=str(getattr(provider_config_row, "model_id", "")),
        route=route,
        max_iter_used=iterations,
        total_tokens=total_tokens,
        total_cost_usd=total_cost_usd,
        converged=converged,
        cost_anomaly=cost_anomaly,
        started_at=started_at,
        finished_at=finished_at,
        pages_created=pages_written,
    )

    # Structured log line for live tail (ADR-0008 §4).
    logger.info(
        "ingest_run provider=%s route=%s converged=%s tokens=%d cost_usd=%.4f origin=%s",
        caps.name,
        route,
        converged,
        total_tokens,
        total_cost_usd,
        origin_source,
    )

    # ── Inline $1 cost-anomaly WARNING (AQ-v0.2-8), AFTER the run row is written ──
    if cost_anomaly:
        logger.warning(
            "COST ANOMALY: ingest run total_cost_usd=%.4f exceeds $%.2f "
            "(provider=%s origin=%s) — investigate runaway/misconfiguration",
            total_cost_usd,
            COST_ANOMALY_THRESHOLD_USD,
            caps.name,
            origin_source,
        )

    return IngestRunResult(
        route=route,
        pages_written=pages_written,
        total_tokens=total_tokens,
        total_cost_usd=total_cost_usd,
        converged=converged,
        cost_anomaly=cost_anomaly,
    )


# Connection/timeout errors that engage the single bounded fallback (ADR-0009 §4).
# httpx connect/timeout failures are subclasses of these in httpx, but we list the httpx
# transport bases explicitly so a literal transport error (not just a stdlib TimeoutError)
# also triggers the fallback. HTTPStatusError is handled separately (5xx only, see below).
_FALLBACK_TRANSPORT_ERRORS: tuple[type[Exception], ...] = (
    TimeoutError,
    ConnectionError,
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.NetworkError,
)


def _is_fallback_eligible(exc: BaseException) -> bool:
    """
    True if *exc* should engage the single bounded provider fallback (ADR-0009 §4).

    Eligible: timeouts / connection failures, AND an HTTP 5xx from the provider endpoint
    (e.g. a literal 503 from Ollama/Anthropic — a server-side / transient failure).
    NOT eligible: HTTP 4xx (client errors / bad request) — those are real defects that must
    surface, not be masked by a fallback (NB-1). Anything else also surfaces unchanged.
    """
    if isinstance(exc, _FALLBACK_TRANSPORT_ERRORS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        # 5xx → engage fallback; 4xx → surface (do NOT broaden to client errors).
        return 500 <= exc.response.status_code < 600
    return False


async def _run_orchestrated(
    *,
    provider: InferenceProvider,
    accumulator: UsageAccumulator,
    source_text: str,
    origin_source: str,
    config_row: object,
) -> LoopResult:
    """Run the bounded loop with optional single fallback (I7, ADR-0009 §4)."""
    max_iter = int(getattr(config_row, "max_iter", None) or 3)
    token_budget = int(getattr(config_row, "token_budget", None) or 60_000)
    vault_context = _load_vault_context()
    retrieval_context = ""  # F5 4-phase retrieval lands in v0.5; empty context for v0.2.

    try:
        return await run_orchestrated_loop(
            provider=provider,
            accumulator=accumulator,
            source_text=source_text,
            vault_context=vault_context,
            retrieval_context=retrieval_context,
            origin_source=origin_source,
            max_iter=max_iter,
            token_budget=token_budget,
        )
    except Exception as exc:
        # Provider fallback — bounded to EXACTLY ONCE (I7, ADR-0009 §4). Only timeouts,
        # connection errors, and HTTP 5xx are eligible (NB-1); 4xx and anything else re-raise.
        if not _is_fallback_eligible(exc):
            raise
        logger.warning("primary provider failed (%s) — attempting single fallback", exc)
        fallback_row = await _resolve_fallback_provider_config()
        if fallback_row is None:
            raise IngestError("primary provider failed and no fallback configured") from exc
        fallback = resolve_provider(fallback_row)
        fallback.bind_accumulator(accumulator)
        try:
            return await run_orchestrated_loop(
                provider=fallback,
                accumulator=accumulator,
                source_text=source_text,
                vault_context=vault_context,
                retrieval_context=retrieval_context,
                origin_source=origin_source,
                max_iter=int(getattr(fallback_row, "max_iter", None) or 3),
                token_budget=int(getattr(fallback_row, "token_budget", None) or 60_000),
            )
        except Exception as exc2:  # no chains (AC-K2-7) — one attempt only
            if not _is_fallback_eligible(exc2):
                raise
            raise IngestError("primary and fallback providers both failed") from exc2


async def _delegate_ingest(
    *,
    provider: InferenceProvider,
    source_text: str,
    origin_source: str,
) -> tuple[bool, int, list[str]]:
    """
    Delegate the whole ingest to an agentic provider (CLI). The provider runs its own bounded
    agent loop and writes pages through the MCP write_page tool (which reuses write_wiki_page,
    ADR-0010 §2), so I1/I5 hold without the orchestrator touching the pages here.

    Returns (converged, pages_written, written_page_ids). The MCP server object + system prompt
    assembly are the backend-engineer/SDK wiring seam; v0.2 surfaces a clear error if invoked
    without it.

    ADR-0044 §4.2 (Phase E): the delegated run is wrapped in `delegated_write_capture()` so the
    ids/titles the agent writes through MCP write_page are side-recorded (no new table). The
    recorded ids are returned so the pipeline can drive the SAME propose_reviews seam afterward —
    capability-agnostic (no isinstance/provider_type branch; empty record → no proposals, I6/I7).
    """
    delegate = getattr(provider, "delegate_ingest", None)
    if delegate is None:
        raise IngestError(
            "agentic provider exposes no delegate_ingest() — cannot delegate (ADR-0007 §3)"
        )
    system_prompt = _load_vault_context()
    # ── MCP wiring seam (ADR-0010 §2) ──────────────────────────────────────────
    # Import lazily to avoid a circular import; app.mcp.server imports from orchestrator.
    # The CLI delegated path needs an IN-PROCESS SDK MCP server (McpSdkServerConfig dict), NOT
    # the FastMCP object — passing FastMCP to the SDK raises "Object of type FastMCP is not JSON
    # serializable". build_sdk_mcp_server() constructs the SDK server from the same _*_body
    # functions (one write path, I1/I5). Degrade to None (cli.py then raises the I1/I5 guard).
    _mcp_server: Any | None = None
    written_page_ids: list[str] = []
    try:
        from app.mcp.server import build_sdk_mcp_server

        _mcp_server = build_sdk_mcp_server()
    except Exception as _mcp_exc:  # noqa: BLE001
        logger.warning("MCP server unavailable; delegate_ingest will run without it: %s", _mcp_exc)

    # ADR-0044 §4.2: capture the pages the delegated agent writes via MCP write_page.
    from app.mcp.server import delegated_write_capture

    with delegated_write_capture() as _write_record:
        result = await delegate(
            source_text=source_text,
            system_prompt=system_prompt,
            vault_dir=str(settings.vault_root),
            mcp_server=_mcp_server,  # McpSdkServerConfig dict (ADR-0010); cli.py seam
        )
        written_page_ids = list(_write_record.ids)

    converged = bool(getattr(result, "converged", False))
    pages_written = int(getattr(result, "pages_written", 0))
    return converged, pages_written, written_page_ids


async def _propose_reviews_for_delegated(
    *,
    vault_id: str,
    written_page_ids: list[str],
    origin_source: str,
) -> None:
    """
    Drive propose_reviews for the delegated (CLI) route (ADR-0044 §4.2, Phase E).

    Loads the Page rows the CLI agent wrote through MCP write_page (recorded ids), synthesizes a
    minimal Analysis from their titles, and calls the SAME `propose_reviews(...)` seam the
    orchestrated route uses — so the rule-based dangling-link path + the single bounded LLM
    proposal call both run on the written set. NO provider-type branch (I6).

    Empty recorded set → returns immediately (propose_reviews' own `if not written_pages` guard
    would early-return anyway; we short-circuit here to avoid even loading). Zero cost.
    """
    if not written_page_ids:
        logger.debug(
            "delegated propose_reviews: no recorded write_page ids — no proposals (zero cost)"
        )
        return

    # Load the written pages (bounded indexed read by id — I1; no vault re-scan).
    # Compare on the string form of the id so the read is dialect-portable (SQLite stores the
    # id as TEXT via with_variant; CAST keeps Postgres native-UUID columns matchable too).
    from sqlalchemy import String as _SAString
    from sqlalchemy import cast, select

    from app.ingest.schemas import Analysis, PageType, SuggestedPage
    from app.models import Page
    from app.ops.review import propose_reviews as _propose_reviews

    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(Page).where(
                        cast(Page.id, _SAString).in_([str(i) for i in written_page_ids]),
                        Page.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for r in rows:
            session.expunge(r)

    if not rows:
        logger.debug("delegated propose_reviews: recorded ids resolved to no live pages")
        return

    # Synthesize a minimal Analysis from the written titles. No suggested_pages (the rule-based
    # dangling-link path + the LLM path run on the written set; ADR-0044 §4.2). Analysis requires
    # ≥1 topic and ≥1 suggested_page by schema, so we seed both from the written titles — these
    # are already-written pages, so they never re-propose themselves (the not-written filter
    # drops them). language is left generic; the proposal prompt does not depend on it.
    titles = [(r.title or "").strip() for r in rows if (r.title or "").strip()]
    synthesized = Analysis(
        topics=titles[:8] or ["ingest"],
        entities=[],
        language="en",
        suggested_pages=[
            SuggestedPage(title=t, type=PageType.CONCEPT) for t in titles[:1]
        ]
        or [SuggestedPage(title="(delegated ingest)", type=PageType.CONCEPT)],
        summary=None,
    )

    await _propose_reviews(
        vault_id=vault_id,
        analysis=synthesized,
        written_pages=rows,
        origin_source=origin_source,
    )


async def _resolve_fallback_provider_config() -> object | None:
    """
    Return the fallback ProviderConfig row (is_fallback=True) at the narrowest matching scope,
    or None if no fallback is configured (ADR-0009 §fallback). Bounded to exactly one attempt
    by the caller (_run_orchestrated — I7).
    """
    from app.provider_config_service import resolve_fallback_provider_config

    return await resolve_fallback_provider_config()


# ── Wiki page writer (reused by the MCP write_page tool — ADR-0010 §2) ─────────


def _strip_leading_frontmatter(body: str) -> str:
    """
    Defensively remove ONE stray leading YAML frontmatter block from a page *body*.

    The write path composes the file as `serialized frontmatter + body` (ADR-0011 —
    content excludes frontmatter). Some providers (notably the CLI agent via the MCP
    write_page tool) ignore that contract and pass a `content` that ALREADY begins with a
    `---\\n...\\n---` block, which would then be duplicated. This strips exactly one such
    leading block so the composed file has a single frontmatter block.

    Rules (conservative — never corrupt legitimate content):
      * If, after optional leading blank lines, the body does NOT start with a line that is
        exactly `---`, it is returned unchanged.
      * Otherwise the NEXT line that is exactly `---` or `...` (a YAML document terminator)
        closes the block; everything through that fence — plus any immediately following
        blank lines — is removed.
      * If no closing fence is found, the body is returned UNCHANGED (a later `---`
        horizontal rule must never be mistaken for a fence, and we never truncate content).
    """
    # Preserve leading blank lines' effect: split on \n, find first non-blank line.
    lines = body.split("\n")
    start = 0
    while start < len(lines) and lines[start].strip() == "":
        start += 1

    # First meaningful line must be exactly the opening fence `---`.
    if start >= len(lines) or lines[start] != "---":
        return body

    # Find the closing fence: the NEXT line that is exactly `---` or `...`.
    close = None
    for i in range(start + 1, len(lines)):
        if lines[i] == "---" or lines[i] == "...":
            close = i
            break

    # No closing fence → conservative: leave the body untouched.
    if close is None:
        return body

    # Drop everything through the closing fence, plus any immediately following blanks.
    rest = close + 1
    while rest < len(lines) and lines[rest].strip() == "":
        rest += 1

    return "\n".join(lines[rest:])


async def write_wiki_page(
    session: object | None,
    page: WikiPage,
    origin_source: str,
) -> Page:
    """
    Serialize *page* to vault/wiki/<type-plural>/<slug>.md with valid frontmatter (I5) and
    persist it incrementally via the v0.1 primitives (I1): persist_metadata → upsert_vector →
    append_log → bump_version. Returns the persisted `Page` ORM row.

    This is the SINGLE write path shared by the orchestrated loop and (via the MCP server's
    write_page tool, ADR-0010 §2) the CLI delegated path — import-clean so the MCP server
    reuses it directly. The frontmatter block is rebuilt from the typed WikiFrontmatter so the
    body and metadata are serialized exactly once (ADR-0011 — content excludes frontmatter).

    `session` is accepted for the MCP-tool call convention (the tool may hold a session); the
    underlying primitives manage their own sessions, so it may be None. The returned Page is
    re-loaded post-commit so the caller gets the live row.
    """
    from sqlalchemy import select

    page_type = page.type.value
    subdir = type_subdir(page.type)
    slug = _slugify(page.title)
    rel_path = f"wiki/{subdir}/{slug}.md"
    abs_path = settings.vault_root / subdir_path(subdir) / f"{slug}.md"

    # Reuse the existing LIVE page's id when this slug already exists — e.g. the same entity is
    # (re-)generated from a second source, or the same source is re-ingested. persist_metadata
    # keys on page.id, so a fresh uuid4() would always take the INSERT branch and violate the
    # (vault_id, file_path) "_live" unique constraint. Mirrors the watcher/file path which reuses
    # existing.id. deleted_at IS NULL → only adopt a live row's id (a soft-deleted same-path row
    # does not collide with the partial _live index; it resurrects only on the file-ingest path).
    async with get_session() as _id_sess:
        existing_page = (
            await _id_sess.execute(
                select(Page).where(
                    Page.vault_id == settings.vault_id,
                    Page.file_path == rel_path,
                    Page.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    page_id = existing_page.id if existing_page is not None else uuid.uuid4()

    sources = list(page.frontmatter.sources)
    if origin_source and origin_source not in sources:
        sources.append(origin_source)
    # Preserve provenance across re-generation: union with the prior row's sources so a page
    # supported by multiple sources keeps all of them (drives F13 shared-entity detection, and
    # avoids silently dropping sources on the UPDATE branch of persist_metadata).
    if existing_page is not None and existing_page.sources:
        for _prior_source in existing_page.sources:
            if _prior_source not in sources:
                sources.append(_prior_source)

    # Build the .md file: frontmatter block + body (ADR-0011).
    # DEFENSIVE: strip a stray leading frontmatter block from the body before composing, so a
    # provider that violated the "content is body-only" contract (e.g. the CLI agent passing a
    # `content` that already begins with `---\n...\n---`) does not produce a DUPLICATED
    # frontmatter block. Applies to BOTH the orchestrated loop and the MCP/CLI write path since
    # this is the single shared write seam (ADR-0010 §2). All downstream uses (file bytes, hash,
    # Qdrant text, wikilink parse) use `body` so nothing desyncs.
    body = _strip_leading_frontmatter(page.content)
    fm_dump = page.frontmatter.model_dump()
    fm_dump["sources"] = sources
    fm_dump["type"] = page_type  # serialize enum as its string value for Obsidian (I5)
    # K6 navigation tags (nashsu/llm_wiki parity): the WikiFrontmatter validator already
    # trimmed/lowercased/deduped/capped them. Serialize as an Obsidian-valid YAML list ONLY when
    # non-empty so pages without tags keep a clean, minimal frontmatter block (I5).
    tags = list(page.frontmatter.tags)
    if tags:
        fm_dump["tags"] = tags
    else:
        fm_dump.pop("tags", None)
    post = frontmatter.Post(body, **fm_dump)
    serialized = frontmatter.dumps(post)
    # content_hash MUST hash the exact bytes written to disk (serialized + trailing newline), NOT
    # `serialized` alone — otherwise the stored hash never matches the file and every on-disk hash
    # comparison (GET/PUT /pages/{id}/content optimistic-lock, ADR-0035) sees a spurious mismatch.
    # reindex_wiki_page_body() already hashes the full file bytes; mirror it here.
    file_text = serialized + "\n"
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(file_text, encoding="utf-8")

    await persist_metadata(
        page_id=page_id,
        vault_id=settings.vault_id,
        file_path=rel_path,
        title=page.title,
        page_type=page_type,
        sources=sources,
        tags=tags or None,
        content_hash=_sha256(file_text.encode("utf-8")),
        source_mtime_ns=0,
    )
    await upsert_vector(
        page_id=page_id,
        text=body,
        file_path=rel_path,
        title=page.title,
        page_type=page_type,
    )
    await append_log(rel_path)
    await bump_version()

    # ── K5: parse + persist wikilinks (incremental, I1) ──────────────────────
    from app.wiki.links import parse_wikilinks, persist_links

    parsed = parse_wikilinks(body)
    async with get_session() as wl_sess:
        await persist_links(wl_sess, page_id, parsed)

    # ── K3: regenerate index.md catalogue (idempotent, I1) ───────────────────
    from app.wiki.index import update_index

    async with get_session() as idx_sess:
        await update_index(idx_sess, settings.vault_root)

    logger.info("write_wiki_page: wrote %s page_id=%s", rel_path, page_id)

    async with get_session() as sess:
        row = await sess.execute(select(Page).where(Page.id == page_id))
        result = row.scalar_one()
        sess.expunge(result)
        return result


def subdir_path(subdir: str) -> Path:
    """vault/wiki/<subdir> relative segment for the writer."""
    return Path("wiki") / subdir


async def reindex_wiki_page_body(
    *,
    page: Page,
    new_file_text: str,
    body_for_embedding: str,
    bump: bool = True,
) -> None:
    """
    Atomically rewrite an already-existing wiki page file with *new_file_text* and re-index it
    INCREMENTALLY (I1) — the shared single-page re-index primitive (ADR-0035 / ADR-0036 §2.1 §7).

    This is the seam that wikilink enrichment (ADR-0036) and any in-place body edit reuse so the
    re-index logic lives in exactly one place. It:
      1. writes the new bytes atomically (temp file + os.replace — crash-safe, no partial file),
      2. refreshes ``pages.content_hash`` via ``persist_metadata`` (metadata unchanged: title/type/
         sources are preserved from the existing row — enrichment never touches frontmatter, I5),
      3. re-embeds the body into Qdrant (``upsert_vector``),
      4. re-derives the K5 ``links`` rows from the new body (``parse_wikilinks``/``persist_links``);
         this is where the new ``[[wikilinks]]`` become F4 *direct link ×3* edges,
      5. optionally bumps ``data_version`` ONCE (``bump=True``). When enriching a batch, the caller
         passes ``bump=False`` per page and bumps once for the whole pass (I1 — one version bump).

    Only THIS page is touched (no rescan, no vault walk — I1). ``index.md`` is NOT regenerated here
    (the link targets already exist; the catalogue is unchanged by adding an inline link). The
    caller is responsible for the single ``bump_version()`` when batching with ``bump=False``.
    """
    import os
    import tempfile

    abs_path = (settings.vault_root / page.file_path).resolve()
    new_bytes = new_file_text.encode("utf-8")

    def _atomic_write() -> None:
        tmp_fd, tmp_name = tempfile.mkstemp(dir=str(abs_path.parent), suffix=".enrich_tmp")
        try:
            os.write(tmp_fd, new_bytes)
            os.close(tmp_fd)
            Path(tmp_name).replace(abs_path)
        except Exception:
            try:
                os.close(tmp_fd)
            except OSError:
                pass
            Path(tmp_name).unlink(missing_ok=True)
            raise

    await asyncio.get_event_loop().run_in_executor(None, _atomic_write)

    # Refresh content_hash; preserve existing metadata verbatim (frontmatter untouched, I5).
    await persist_metadata(
        page_id=page.id,
        vault_id=page.vault_id,
        file_path=page.file_path,
        title=page.title,
        page_type=page.page_type,
        sources=page.sources,
        tags=page.tags,
        content_hash=_sha256(new_bytes),
        source_mtime_ns=page.source_mtime_ns or 0,
    )
    await upsert_vector(
        page_id=page.id,
        text=body_for_embedding,
        file_path=page.file_path,
        title=page.title,
        page_type=page.page_type,
    )

    # K5: re-derive wikilinks from the new body (the new [[links]] land in `links` → F4 ×3 signal).
    from app.wiki.links import parse_wikilinks, persist_links

    parsed = parse_wikilinks(body_for_embedding)
    async with get_session() as wl_sess:
        await persist_links(wl_sess, page.id, parsed)

    if bump:
        await bump_version()


def _ensure_source_summary(
    pages: list[WikiPage], analysis: Analysis | None, origin_source: str
) -> list[WikiPage]:
    """
    Guarantee at least one page traceable to the source (F3). If the provider produced no
    pages (e.g. non-convergence), synthesize a minimal source-summary page from the analysis
    so the source is never silently dropped.
    """
    if pages:
        return pages
    from app.ingest.schemas import PageType, WikiFrontmatter

    lang = analysis.language if analysis is not None else "en"
    title = f"Source summary: {Path(origin_source).stem}"
    summary = (analysis.summary if analysis and analysis.summary else None) or (
        "Auto-generated source summary (provider produced no pages)."
    )
    fm = WikiFrontmatter(type=PageType.SOURCE, title=title, sources=[origin_source], lang=lang)
    return [WikiPage(title=title, type=PageType.SOURCE, content=summary, frontmatter=fm)]


async def _update_overview(analysis: Analysis | None, origin_source: str) -> None:
    """
    Append a one-line entry for this source to vault/wiki/overview.md (F3 auto-overview).

    Keeps overview.md a valid Obsidian page (I5). Append-only-ish: a marker line is added per
    ingested source; full regeneration is a v0.3+ concern.
    """
    overview_path = settings.wiki_dir / "overview.md"
    if not overview_path.exists():
        overview_path.parent.mkdir(parents=True, exist_ok=True)
        overview_path.write_text(
            "---\ntype: overview\ntitle: Synapse Overview\n---\n\n", encoding="utf-8"
        )
    summary = analysis.summary if analysis and analysis.summary else origin_source
    line = f"- [[{Path(origin_source).stem}]] — {summary}\n"
    with overview_path.open("a", encoding="utf-8") as f:
        f.write(line)


def _derive_run_status(*, converged: bool, error_message: str | None) -> str:
    """
    Map a finished run to its lifecycle status (BUG A2, ADR-0018 §7).

    Returns one of:
      • "failed"          — the run raised/errored (error_message is set).
      • "converged_false" — the loop ran but never produced a valid batch (max_iter / budget).
      • "completed"       — the run converged successfully.

    Note: the IngestRun.status column comment uses "converged_false" (not "non-converged") as the
    canonical non-convergence value; we keep that exact token so the REST view and any historical
    backfill agree.
    """
    if error_message is not None:
        return "failed"
    if not converged:
        return "converged_false"
    return "completed"


async def _write_ingest_run(
    *,
    page_id: uuid.UUID | None,
    provider_name: str,
    provider_type: str,
    model_id: str,
    route: str,
    max_iter_used: int,
    total_tokens: int,
    total_cost_usd: float,
    converged: bool,
    cost_anomaly: bool,
    started_at: datetime,
    finished_at: datetime,
    pages_created: int,
    error_message: str | None = None,
) -> None:
    """
    Persist one ingest_runs row — the cost-audit system of record (I7, ADR-0008 §4).

    Sets pages_created/status/error_message from the actual run outcome (BUG A2): previously
    these defaulted to 0/"completed"/NULL regardless of reality, so successful multi-page runs
    and failed/non-converged runs were indistinguishable in the REST view (ADR-0018 §7).
    """
    status = _derive_run_status(converged=converged, error_message=error_message)
    async with get_session() as session:
        session.add(
            IngestRun(
                id=uuid.uuid4(),
                vault_id=settings.vault_id,
                page_id=page_id,
                provider_name=provider_name,
                provider_type=provider_type,
                model_id=model_id,
                route=route,
                max_iter_used=max_iter_used,
                total_tokens=total_tokens,
                total_cost_usd=total_cost_usd,
                converged=converged,
                cost_anomaly=cost_anomaly,
                started_at=started_at,
                finished_at=finished_at,
                pages_created=pages_created,
                status=status,
                error_message=error_message,
            )
        )


def _load_vault_context() -> str:
    """
    Assemble the provider vault context (F2/F3): purpose.md + schema.md content. Used as the
    orchestrated analyze() context and as the CLI delegated system prompt. Missing files →
    empty section (tolerant).
    """
    parts: list[str] = []
    for name in ("purpose.md", "schema.md"):
        path = settings.vault_root / name
        if path.exists():
            parts.append(f"# {name}\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    """Filesystem-safe, unicode-tolerant slug for a page filename (I5-friendly)."""
    slug = _SLUG_RE.sub("-", title.strip().lower()).strip("-")
    return slug or "untitled"


class IngestError(RuntimeError):
    """Raised when an ingest run cannot complete (surfaced as HTTP 500 by the REST path)."""


# ── Factored helpers (reused by v0.2 orchestrated loop) ───────────────────────


async def persist_metadata(
    *,
    page_id: uuid.UUID,
    vault_id: str,
    file_path: str,
    title: str | None,
    page_type: str | None,
    sources: list[str] | None,
    content_hash: str,
    source_mtime_ns: int,
    tags: list[str] | None = None,
) -> None:
    """
    Upsert the `pages` row for *page_id* inside a single Postgres transaction.

    Handles both INSERT (new page) and UPDATE (re-ingest of existing page).
    Clears deleted_at on resurrection (ADR-0005 — same file_path recreated).

    `tags` (K6 navigation, nashsu/llm_wiki parity) is persisted exactly like `sources`
    (JSONB list; None when absent). Additive keyword — existing callers that omit it write
    NULL, preserving backward compatibility.
    """
    from sqlalchemy import select

    now = datetime.now(UTC)

    async with get_session() as session:
        row = await session.execute(select(Page).where(Page.id == page_id))
        page = row.scalar_one_or_none()

        if page is None:
            page = Page(
                id=page_id,
                vault_id=vault_id,
                file_path=file_path,
                title=title,
                page_type=page_type,
                sources=sources,
                tags=tags,
                content_hash=content_hash,
                source_mtime_ns=source_mtime_ns,
                qdrant_point_id=page_id,  # == pages.id (ADR-0002)
                deleted_at=None,
                created_at=now,
                updated_at=now,
            )
            session.add(page)
        else:
            page.title = title
            page.page_type = page_type
            page.sources = sources
            page.tags = tags
            page.content_hash = content_hash
            page.source_mtime_ns = source_mtime_ns
            page.qdrant_point_id = page_id
            page.deleted_at = None  # resurrect if previously deleted
            page.updated_at = now


async def upsert_vector(
    *,
    page_id: uuid.UUID,
    text: str,
    file_path: str,
    title: str | None,
    page_type: str | None,
) -> None:
    """
    Compute an embedding via EmbeddingClient (I9 — calls EMBEDDING_URL) and upsert to Qdrant.

    Point id == page_id (ADR-0002).
    Payload = {file_path, title, type} (AC-QD-2).

    When ``settings.embeddings_enabled`` is False (ADR-0030 §2.2) this returns early WITHOUT
    embedding or upserting: no EmbeddingClient call, no Qdrant point. Every other ingest step
    (Postgres metadata, K5 wikilinks, K4 log, dataVersion bump) still runs in the caller, so
    the page stays fully indexed in Postgres and ingest remains a single incremental pass (I1).
    Toggling the flag never triggers a bulk re-embed.
    """
    if not settings.embeddings_enabled:
        logger.info(
            "upsert_vector: embeddings disabled (EMBEDDINGS_ENABLED=false) — "
            "skipping embed + Qdrant upsert for page_id=%s (file_path=%s)",
            page_id,
            file_path,
        )
        return

    client = get_embedding_client()
    vector = await client.embed(text)
    await upsert_point(
        page_id=page_id,
        vector=vector,
        file_path=file_path,
        title=title,
        page_type=page_type,
    )


async def append_log(rel_path: str) -> None:
    """
    Append one INDEXED line to vault/wiki/log.md (K4, AC-K4-1).

    Format: YYYY-MM-DDTHH:MM:SSZ | INDEXED | <relative_path>

    File is opened in 'a' (append) mode — never truncated (AC-K4-2).
    Never writes to vault/raw/ (AC-K1-5).
    """
    log_path = settings.log_md_path
    # Ensure log.md exists (vault bootstrap normally creates it, but be defensive)
    if not log_path.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n", encoding="utf-8")

    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{timestamp} | INDEXED | {rel_path}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)


async def bump_version() -> None:
    """
    Increment vault_state.data_version by 1 for this vault (AC-F16dv-2).

    Monotonic non-decreasing; only called on successful content-changing ingest.
    Startup, restart, deletion, GET requests, and skipped ingests do NOT call this.
    """
    from sqlalchemy import select, update

    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Seed it now if somehow missing (startup should have done this)
            state = VaultState(vault_id=settings.vault_id, data_version=1)
            state.updated_at = datetime.now(UTC)
            session.add(state)
        else:
            await session.execute(
                update(VaultState)
                .where(VaultState.vault_id == settings.vault_id)
                .values(
                    data_version=VaultState.data_version + 1,
                    updated_at=datetime.now(UTC),
                )
            )


# ── Private helpers ────────────────────────────────────────────────────────────


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative_path(path: Path) -> str:
    """
    Return a consistent relative path string for use as Postgres file_path key.

    Prefer a path relative to vault_root; fall back to the absolute string if
    the path is outside the vault (unusual but handled gracefully).
    """
    try:
        return str(path.resolve().relative_to(settings.vault_root))
    except ValueError:
        return str(path.resolve())


async def _load_page(rel_path: str) -> Page | None:
    """Load a live Page row by relative file_path, or None if absent/deleted."""
    from sqlalchemy import select

    async with get_session() as session:
        row = await session.execute(
            select(Page).where(
                Page.vault_id == settings.vault_id,
                Page.file_path == rel_path,
                Page.deleted_at.is_(None),
            )
        )
        page = row.scalar_one_or_none()
        # Expunge from session so we can use the object outside the context manager
        if page is not None:
            session.expunge(page)
        return page


async def _touch_mtime(page_id: uuid.UUID, mtime_ns: int) -> None:
    """Update only source_mtime_ns so the next event re-hits the fast path."""
    from sqlalchemy import update

    async with get_session() as session:
        await session.execute(
            update(Page).where(Page.id == page_id).values(source_mtime_ns=mtime_ns)
        )


# _enqueue_review_items is REMOVED (ADR-0034 §4 — replaced by propose_reviews in ops/review.py).
# The per-page question-spam hook (ADR-0025 §3.3) is superseded by the single bounded
# once-per-run propose_reviews stage. The call site above now imports propose_reviews directly.


def _parse_frontmatter(raw_bytes: bytes, rel_path: str) -> dict[str, object]:
    """
    Parse YAML frontmatter from raw file bytes (K6).

    Tolerant: missing fields → empty dict (caller treats missing keys as NULL).
    No exception raised for missing frontmatter block (AC-K6-2/3).
    Issues a warning for missing required fields.
    """
    text = raw_bytes.decode("utf-8", errors="replace")
    try:
        doc = frontmatter.loads(text)
        meta: dict[str, object] = dict(doc.metadata)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ingest_file: frontmatter parse error in %s: %s — treating as empty metadata",
            rel_path,
            exc,
        )
        return {}

    for required in ("type", "title", "sources"):
        if required not in meta:
            logger.warning(
                "ingest_file: missing frontmatter field %r in %s (AC-K6-2/3)",
                required,
                rel_path,
            )

    return meta
