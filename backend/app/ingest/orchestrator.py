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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import frontmatter  # python-frontmatter
import httpx

from app.config import settings
from app.db import get_session
from app.embeddings import get_embedding_client
from app.ingest.loop import IngestCancelled, LoopResult, run_orchestrated_loop
from app.ingest.provider import resolve_provider
from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.queue_manager import ingest_queue
from app.ingest.schemas import (
    INDEX_TYPE,
    OVERVIEW_TYPE,
    Analysis,
    WikiPage,
    type_subdir,
)
from app.models import IngestRun, Page, VaultState
from app.qdrant_client import delete_point, upsert_point

logger = logging.getLogger(__name__)

# Cost-anomaly threshold (AQ-v0.2-8 / ADR-0009 §3) — inline WARNING site, not a hook.
COST_ANOMALY_THRESHOLD_USD = 1.00

# R8-2 / F12: image extensions routed through the vision caption seam (app.ingest.vision).
# Mirrors extract.PLACEHOLDER image set; AV extensions are handled by R8-3, not here.
_VISION_IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp"})

# R8-3 / F12: AV extensions routed through the Whisper transcription seam
# (app.ingest.transcription). Kept separate from _VISION_IMAGE_EXTENSIONS (I6).
_AV_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".wav", ".m4a", ".mp4"})


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
        # ── R8-2 / F12: vision captioning for image files ────────────────────────
        # For an image extension, replace the (garbage) decoded-bytes source_text with a
        # provider-generated caption when VISION_CAPTIONS_ENABLED and the provider supports
        # vision (cache-first, bounded, cost folded into this run's ledger — I7). On any
        # miss/failure the caption is None and we keep the extract.py placeholder text so the
        # pre-R8-2 behaviour is unchanged.
        seed_usage: object | None = None
        if path.suffix.lower() in _VISION_IMAGE_EXTENSIONS:
            from app.ingest.provider.base import UsageAccumulator as _VisAcc
            from app.ingest.vision import maybe_caption_image

            _vis_acc = _VisAcc()
            caption = await maybe_caption_image(
                provider_config_row=provider_cfg,
                raw_bytes=raw_bytes,
                origin_source=rel,
                accumulator=_vis_acc,
            )
            if caption is not None:
                source_text = caption
                seed_usage = _vis_acc.snapshot()
            else:
                # No vision → keep the pure extract.py placeholder (ADR-0051: no inference there).
                from app.ingest.extract import extract_text as _extract_text

                try:
                    source_text = _extract_text(path)
                except Exception as _ex_exc:  # noqa: BLE001 — placeholder is best-effort context
                    logger.debug("vision fallback extract_text failed for %s: %s", rel, _ex_exc)
        elif path.suffix.lower() in _AV_EXTENSIONS:
            # ── R8-3 / F12: Whisper transcription for AV files ──────────────────────
            # When AV_TRANSCRIPTION_ENABLED is True and the per-run cap allows it, replace
            # the (garbage) decoded-bytes source_text with the Whisper transcript so the
            # normal analyze→generate flow receives real text content. On any miss/failure
            # the transcript is None and we keep the extract.py placeholder (pre-R8-3
            # behaviour unchanged). No inference cost: Whisper is a local service
            # (total_cost_usd=0.00, I7 accounting — transcription.py logs this).
            from app.ingest.transcription import maybe_transcribe_av as _maybe_transcribe_av

            transcript = await _maybe_transcribe_av(
                raw_bytes=raw_bytes,
                origin_source=rel,
            )
            if transcript is not None:
                source_text = transcript
            else:
                # No transcript → keep the pure extract.py placeholder (AV path, no decode).
                from app.ingest.extract import extract_text as _extract_text_av

                try:
                    source_text = _extract_text_av(path)
                except Exception as _av_exc:  # noqa: BLE001 — placeholder is best-effort context
                    logger.debug("AV fallback extract_text failed for %s: %s", rel, _av_exc)
        # abs_source is the canonical queue key (ADR-0046 path-normalization fix).
        # path.resolve() is the absolute path; rel stays relative for DB storage (I1/I5).
        abs_source = str(path.resolve())
        await run_ingest_pipeline(
            provider_config_row=provider_cfg,
            source_text=source_text,
            origin_source=rel,
            abs_source=abs_source,
            seed_usage=seed_usage,
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
    _tags: list[str] | None = [str(t) for t in _tags_raw] if isinstance(_tags_raw, list) else None
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


def _seed_accumulator(accumulator: UsageAccumulator, seed_usage: object | None) -> None:
    """
    Fold a pre-loop Usage (R8-2 image caption cost) into the run-scoped accumulator (I7).

    Kept out of run_ingest_pipeline so the routing region stays free of isinstance/type checks
    (the I6 static guard). A non-Usage / None value is ignored.
    """
    from app.ingest.schemas import Usage as _Usage

    if isinstance(seed_usage, _Usage):
        accumulator.add(seed_usage)


async def run_ingest_pipeline(
    *,
    provider_config_row: object,
    source_text: str,
    origin_source: str,
    abs_source: str | None = None,
    seed_usage: object | None = None,
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

    ADR-0046: inserts a `status="running"` row at the START of the pipeline (before the route
    try-block), registers the run with ingest_queue, and UPDATE-finalises at both terminal sites
    instead of INSERT-ing a new row at the end.  This makes in-flight runs visible to
    GET /ingest/runs and enables cooperative cancel via the queue manager.

    ``abs_source`` is the absolute path used as the canonical queue key (ADR-0046
    path-normalization fix). When not provided (e.g. tests or direct REST callers that
    pass a relative origin_source), it falls back to resolving origin_source against
    the process CWD — callers that hold the absolute path should always supply it.
    The ingest_runs DB column (source_path) is set from ``origin_source`` (relative)
    and is never changed by this parameter.

    ``seed_usage`` (R8-2 / I7): an optional Usage to pre-load onto the run-scoped accumulator so a
    cost incurred BEFORE the loop (e.g. a vision caption call in ingest_file) is folded into this
    run's ingest_runs ledger — the caption is part of the same logical ingest run.
    """
    provider = resolve_provider(provider_config_row)
    accumulator = UsageAccumulator()
    # R8-2: fold any pre-loop cost (image captioning) into this run's ledger (I7).
    _seed_accumulator(accumulator, seed_usage)
    provider.bind_accumulator(accumulator)
    caps = provider.capabilities()

    started_at = datetime.now(UTC)
    pages: list[WikiPage] = []
    analysis: Analysis | None = None
    iterations = 0
    delegated_pages_written = 0
    converged = False
    route: Literal["orchestrated", "delegated"] = "orchestrated"

    # ── ADR-0046 path-normalization fix: derive the absolute queue key ────────
    # The watcher passes absolute paths to admit/should_skip; the queue must use
    # the SAME key end-to-end so cancel suppression matches (ADR-0046 fix).
    # origin_source (relative) is kept for ALL DB / file / log uses below.
    _queue_key: str = abs_source if abs_source is not None else str(Path(origin_source).resolve())

    # ── ADR-0046: open a "running" row + register with the queue manager ──────
    run_id = await _open_ingest_run(
        origin_source=origin_source,
        provider_name=caps.name,
        provider_type=caps.mode,
        model_id=str(getattr(provider_config_row, "model_id", "")),
        route=route,  # will be overwritten on delegate; the row uses the resolved value
        started_at=started_at,
        retry_count=ingest_queue.get_retry_count(_queue_key),
    )
    handle = ingest_queue.open_run(run_id, _queue_key)
    cancel_event = handle.cancel_event

    # ── Store resolved route on the handle so snapshot() can compute ETA ─────
    # Set before the route try-block so the handle always has a route when active.
    # Will be overwritten to "delegated" inside the try-block if the CLI path is taken.
    ingest_queue.set_route(run_id, route)

    # ── ROUTE: the single capability check (I6) ──────────────────────────────
    # Wrapped so a route failure still persists an ingest_runs row with status="failed" and the
    # error_message + accumulated cost (BUG A2 / I7), then re-raises so the REST/watcher caller
    # surfaces the error unchanged.
    # ── F3/K3 cross-ingest connectivity: assemble the provider context ONCE ──────
    # purpose.md + schema.md + the existing-pages catalogue ("LINK TO THESE"). Built here in
    # the async pipeline (the catalogue needs an async DB query) and threaded into BOTH the
    # delegated (CLI) and orchestrated (API/Local) paths so the LLM links to existing pages on
    # every backend → one connected graph instead of isolated islands (I6 — guidance is in the
    # context STRING, never in provider code).
    ingest_context = await _load_ingest_context()
    # R7-6: prepend the folderContext hint (subfolder topical context) so it reaches BOTH the
    # orchestrated analyze() vault_context and the delegated/CLI system_prompt (I6 — the hint is
    # in the STRING, not provider code). "" when the source has no subfolder path.
    _folder_block = _folder_context_block(origin_source)
    if _folder_block:
        ingest_context = f"{_folder_block}\n\n{ingest_context}" if ingest_context else _folder_block

    try:
        if caps.supports_agentic_loop:
            route = "delegated"
            ingest_queue.set_route(run_id, route)
            # Coarse phase for delegated/CLI runs (opaque agent loop — I6 forbids finer phases)
            ingest_queue.set_phase(run_id, "agent running")
            converged, delegated_pages_written, delegated_page_ids = await _delegate_ingest(
                provider=provider,
                source_text=source_text,
                origin_source=origin_source,
                system_prompt=ingest_context,
            )
            # ── ADR-0046 §3: deferred cancel check for delegated route (I6) ──────────
            # We cannot inject a cancel boundary into the provider's own agent loop (I6
            # forbids touching provider internals). Check once after _delegate_ingest
            # returns, BEFORE post-write hooks — if set, skip hooks and raise to the
            # IngestCancelled handler above.
            if cancel_event.is_set():
                raise IngestCancelled(origin_source)

            # ── F3 delegated-route overview regen (nashsu/llm_wiki parity) ──────────────
            # The CLI agent writes pages via MCP write_page but does NOT maintain overview.md.
            # Drive the SAME bounded, degrade-safe overview seam once after its run so the single
            # auto-maintained Overview note is regenerated + indexed on BOTH routes (no
            # provider_type branch — I6). Fire-and-forget: NEVER raises into ingest (I7). Analysis
            # is None on the
            # delegated route (the agent owns its own analysis); the seam degrades to titles-only.
            try:
                await _update_overview(None, origin_source)
            except Exception as _ov_exc:  # noqa: BLE001
                logger.warning(
                    "run_ingest_pipeline: F3 delegated overview regen hook failed "
                    "(non-fatal): %s",
                    _ov_exc,
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
            # ── R9-3 delegated-route purpose drift check (F2, ADR §R9-3) ──────────────
            # Bounded single provider call (max_tokens 300, no retry). Analysis is None on the
            # delegated route → the seam degrades to titles-only. Fire-and-forget: any exception
            # logs a WARNING and NEVER fails the ingest run (AC-R9-3-3).
            try:
                await _purpose_suggestion_for_delegated(
                    vault_id=settings.vault_id,
                    written_page_ids=delegated_page_ids,
                    origin_source=origin_source,
                )
            except Exception as _ps_d_exc:  # noqa: BLE001
                logger.warning(
                    "run_ingest_pipeline: R9-3 delegated purpose-suggestion hook failed "
                    "(non-fatal): %s",
                    _ps_d_exc,
                )
            # ── R9-4 delegated-route schema.md co-evolution check (K6, ADR §R9-4) ──────
            # Runs AFTER the delegated purpose check. Bounded single provider call (max_tokens
            # 400, no retry). DEFAULT OFF. Fire-and-forget: any exception logs a WARNING and
            # NEVER fails the ingest run (AC-R9-4-3).
            try:
                await _schema_suggestion_for_delegated(
                    vault_id=settings.vault_id,
                    written_page_ids=delegated_page_ids,
                    origin_source=origin_source,
                )
            except Exception as _ss_d_exc:  # noqa: BLE001
                logger.warning(
                    "run_ingest_pipeline: R9-4 delegated schema-suggestion hook failed "
                    "(non-fatal): %s",
                    _ss_d_exc,
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
                vault_context=ingest_context,
                cancel_event=cancel_event,
                on_phase=lambda p: ingest_queue.set_phase(run_id, p),
            )
            pages = loop_result.pages
            analysis = loop_result.analysis
            iterations = loop_result.iterations
            converged = loop_result.converged
            # Guarantee a source-summary page (F3) even if the provider omitted it.
            pages = _ensure_source_summary(pages, analysis, origin_source)
            ingest_queue.set_phase(run_id, "writing")
            written_pages: list[Page] = []
            for page in pages:
                written_page = await write_wiki_page(None, page, origin_source)
                written_pages.append(written_page)
                # ADR-0046: record the page_id so cancel can cascade-delete it
                ingest_queue.record_written(run_id, written_page.id)
            await _update_overview(analysis, origin_source)

            # ── F18 post-write hook: domain auto-tag (ADR-0054 §3) ───────────────────
            # Runs AFTER the write loop + overview (pages exist on disk + DB, I1) and BESIDE the
            # ADR-0036 enrichment hook, BEFORE propose_reviews. Non-fatal: a classification failure
            # leaves the page written+untagged and never fails the ingest (§3.4). Dormant vocabulary
            # ⇒ zero provider calls (§3.2). Reuses the run-scoped accumulator bound above so the
            # classification cost folds into this run's total_cost_usd (I7). No second data_version
            # bump — apply_domain_tags writes tags without bumping (§3.2, Do-NOT #3).
            try:
                await _auto_tag_written_pages(
                    provider=provider,
                    written_pages=written_pages,
                    origin_source=origin_source,
                )
            except Exception as _tag_exc:  # noqa: BLE001
                logger.warning(
                    "run_ingest_pipeline: F18 domain auto-tag hook failed (non-fatal): %s",
                    _tag_exc,
                )

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

            # ── R9-3 post-ingest purpose drift check (F2, ADR §R9-3) ──────────────────
            # Bounded single provider call (max_tokens 300, no retry, cost logged). Compares the
            # run's analysis topics/summary against vault purpose.md; on scope drift emits ONE
            # purpose-suggestion ReviewItem (throttled: max 1 pending + ≥N sources since last
            # check). Fire-and-forget: any exception logs a WARNING and NEVER fails ingest
            # (AC-R9-3-3).
            try:
                from app.ops.review import generate_purpose_suggestion as _gen_purpose_sugg

                await _gen_purpose_sugg(
                    vault_id=settings.vault_id,
                    analysis=analysis,
                    written_pages=written_pages,
                    origin_source=origin_source,
                )
            except Exception as _ps_exc:  # noqa: BLE001
                logger.warning(
                    "run_ingest_pipeline: R9-3 purpose-suggestion hook failed (non-fatal): %s",
                    _ps_exc,
                )

            # ── R9-4 post-ingest schema.md co-evolution check (K6, ADR §R9-4) ─────────
            # Runs AFTER the R9-3 purpose check (AC-R9-4-3). Bounded single provider call
            # (max_tokens 400, no retry, cost logged). Compares the written pages' actual
            # frontmatter/type/tag usage against vault schema.md; on a recurring un-codified
            # convention emits ONE schema-suggestion ReviewItem (throttled: max 1 pending + ≥N
            # sources since last check; DEFAULT OFF — see settings.schema_suggestion_enabled).
            # Fire-and-forget: any exception logs a WARNING and NEVER fails ingest.
            try:
                from app.ops.review import generate_schema_suggestion as _gen_schema_sugg

                await _gen_schema_sugg(
                    vault_id=settings.vault_id,
                    written_pages=written_pages,
                    origin_source=origin_source,
                )
            except Exception as _ss_exc:  # noqa: BLE001
                logger.warning(
                    "run_ingest_pipeline: R9-4 schema-suggestion hook failed (non-fatal): %s",
                    _ss_exc,
                )
    except IngestCancelled as _cancelled_exc:
        # ── ADR-0046 §3: cooperative cancel — cascade-delete partial output (I1) ──
        finished_at = datetime.now(UTC)
        _written_ids = handle.written_page_ids[:]
        logger.info(
            "ingest_run CANCELLED provider=%s origin=%s written_pages=%d",
            caps.name,
            origin_source,
            len(_written_ids),
        )
        # Cascade-delete each derived page written so far, excluding raw/sources/ pages
        # (the raw source file stays so the user can retry — ADR-0046 §3).
        for _pid in _written_ids:
            try:
                if not await _is_raw_sources_page(_pid):
                    from app.ops.cascade_delete import cascade_delete as _cascade_delete

                    await _cascade_delete(_pid)
                else:
                    logger.debug(
                        "cancel cleanup: skipping raw/sources/ page_id=%s (ADR-0046 §3)", _pid
                    )
            except Exception as _cd_exc:  # noqa: BLE001
                # Non-fatal: log and continue; the ledger records cancellation regardless.
                logger.warning(
                    "cancel cleanup: cascade_delete page_id=%s failed (non-fatal): %s",
                    _pid,
                    _cd_exc,
                )
        # Finalize the run as cancelled (I7: cost incurred before abort still recorded)
        await _finalize_ingest_run(
            run_id=run_id,
            provider_name=caps.name,
            provider_type=caps.mode,
            model_id=str(getattr(provider_config_row, "model_id", "")),
            route=route,
            max_iter_used=iterations,
            total_tokens=accumulator.total_tokens,
            total_cost_usd=round(accumulator.total_cost_usd, 4),
            converged=False,
            cost_anomaly=round(accumulator.total_cost_usd, 4) > COST_ANOMALY_THRESHOLD_USD,
            finished_at=finished_at,
            pages_created=0,
            error_message="cancelled by user",
            status_override="cancelled",
        )
        ingest_queue.finalize(run_id, "cancelled", error="cancelled by user")
        # Do NOT re-raise — cancel is a normal, user-initiated terminal state (ADR-0046 §3).
        return IngestRunResult(
            route=route,
            pages_written=0,
            total_tokens=accumulator.total_tokens,
            total_cost_usd=round(accumulator.total_cost_usd, 4),
            converged=False,
            cost_anomaly=round(accumulator.total_cost_usd, 4) > COST_ANOMALY_THRESHOLD_USD,
        )

    except Exception as exc:
        # Persist a failed-run UPDATE (I7 ledger stays truthful: cost incurred before the failure is
        # still recorded) then re-raise so the caller's error handling is unchanged.
        finished_at = datetime.now(UTC)
        await _finalize_ingest_run(
            run_id=run_id,
            provider_name=caps.name,
            provider_type=caps.mode,
            model_id=str(getattr(provider_config_row, "model_id", "")),
            route=route,
            max_iter_used=iterations,
            total_tokens=accumulator.total_tokens,
            total_cost_usd=round(accumulator.total_cost_usd, 4),
            converged=False,
            cost_anomaly=round(accumulator.total_cost_usd, 4) > COST_ANOMALY_THRESHOLD_USD,
            finished_at=finished_at,
            pages_created=0,
            error_message=str(exc) or exc.__class__.__name__,
        )
        ingest_queue.finalize(run_id, "failed", error=str(exc) or exc.__class__.__name__)
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

    # ── Finalize accumulator → ingest_runs row UPDATE (I7, ADR-0008 §4 / ADR-0046 §2) ──────
    total_tokens = accumulator.total_tokens
    total_cost_usd = round(accumulator.total_cost_usd, 4)
    cost_anomaly = total_cost_usd > COST_ANOMALY_THRESHOLD_USD

    await _finalize_ingest_run(
        run_id=run_id,
        provider_name=caps.name,
        provider_type=caps.mode,
        model_id=str(getattr(provider_config_row, "model_id", "")),
        route=route,
        max_iter_used=iterations,
        total_tokens=total_tokens,
        total_cost_usd=total_cost_usd,
        converged=converged,
        cost_anomaly=cost_anomaly,
        finished_at=finished_at,
        pages_created=pages_written,
    )

    # ── ADR-0046: notify queue manager of terminal success ────────────────────
    terminal_status = _derive_run_status(converged=converged, error_message=None)
    ingest_queue.finalize(run_id, terminal_status)

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
    vault_context: str | None = None,
    cancel_event: asyncio.Event | None = None,
    on_phase: Callable[[str], None] | None = None,
) -> LoopResult:
    """Run the bounded loop with optional single fallback (I7, ADR-0009 §4).

    cancel_event is threaded from run_ingest_pipeline (ADR-0046 §3); passed to
    run_orchestrated_loop so IngestCancelled is raised at loop boundaries only.
    IngestCancelled propagates up — it is NOT a fallback-eligible exception.

    vault_context is assembled by run_ingest_pipeline (F2/F3: purpose + schema +
    existing-pages catalogue) and threaded in so the same context reaches primary AND fallback.
    Falls back to purpose+schema only (no catalogue) when the caller omits it — this keeps
    direct callers working without a DB round-trip.

    on_phase: optional callback threaded from run_ingest_pipeline (phase reporting, pure
    reporting — no loop semantics changed). Passed through to both the primary and fallback
    run_orchestrated_loop calls so phases remain visible across the fallback transition.
    """
    max_iter = int(getattr(config_row, "max_iter", None) or 3)
    token_budget = int(getattr(config_row, "token_budget", None) or 60_000)
    if vault_context is None:
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
            cancel_event=cancel_event,
            on_phase=on_phase,
        )
    except IngestCancelled:
        # Cooperative cancel is not a provider fault — propagate directly without fallback.
        raise
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
                cancel_event=cancel_event,
                on_phase=on_phase,
            )
        except IngestCancelled:
            raise
        except Exception as exc2:  # no chains (AC-K2-7) — one attempt only
            if not _is_fallback_eligible(exc2):
                raise
            raise IngestError("primary and fallback providers both failed") from exc2


async def _delegate_ingest(
    *,
    provider: InferenceProvider,
    source_text: str,
    origin_source: str,
    system_prompt: str | None = None,
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
    # system_prompt is assembled by run_ingest_pipeline (F2/F3: purpose + schema +
    # existing-pages catalogue) so the CLI agent links to existing pages too (I6 — guidance
    # lives in the context string, not in provider code). Falls back to purpose+schema only
    # when the caller omits it (keeps direct callers working without a DB round-trip).
    if system_prompt is None:
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

        # Pass origin_source so the SDK write_page tool stamps it into sources[] for every
        # page written during this delegated run — server-side traceability (K6/F3/F13).
        # The bound value wins over whatever the CLI agent passes in the tool call, so the
        # raw file path is never lost regardless of agent behaviour (Option B, ADR-0010 §2).
        _mcp_server = build_sdk_mcp_server(origin_source=origin_source)
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
        suggested_pages=[SuggestedPage(title=t, type=PageType.CONCEPT) for t in titles[:1]]
        or [SuggestedPage(title="(delegated ingest)", type=PageType.CONCEPT)],
        summary=None,
    )

    await _propose_reviews(
        vault_id=vault_id,
        analysis=synthesized,
        written_pages=rows,
        origin_source=origin_source,
    )


async def _purpose_suggestion_for_delegated(
    *,
    vault_id: str,
    written_page_ids: list[str],
    origin_source: str,
) -> None:
    """
    Drive the R9-3 purpose drift check for the delegated (CLI) route.

    Loads the Page rows the CLI agent wrote through MCP write_page (recorded ids) and calls the
    SAME `generate_purpose_suggestion(...)` seam the orchestrated route uses. Analysis is None on
    the delegated route (the agent owns its own analysis); the seam degrades gracefully — it
    still reads purpose.md and the written-page titles. Bounded single call, no retry. Empty
    recorded set → early-return (zero cost). NO provider-type branch (I6).
    """
    if not written_page_ids:
        logger.debug("delegated purpose-suggestion: no recorded write_page ids — skip (zero cost)")
        return

    from sqlalchemy import String as _SAString
    from sqlalchemy import cast, select

    from app.models import Page
    from app.ops.review import generate_purpose_suggestion as _gen_purpose_sugg

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
        logger.debug("delegated purpose-suggestion: recorded ids resolved to no live pages")
        return

    await _gen_purpose_sugg(
        vault_id=vault_id,
        analysis=None,
        written_pages=rows,
        origin_source=origin_source,
    )


async def _schema_suggestion_for_delegated(
    *,
    vault_id: str,
    written_page_ids: list[str],
    origin_source: str,
) -> None:
    """
    Drive the R9-4 schema.md co-evolution check for the delegated (CLI) route.

    Loads the Page rows the CLI agent wrote through MCP write_page (recorded ids) and calls the
    SAME `generate_schema_suggestion(...)` seam the orchestrated route uses. The seam reads the
    written pages' real frontmatter (type/tags/sources) — available regardless of route — and
    schema.md. Bounded single call, no retry. DEFAULT OFF (the seam self-gates on
    schema_suggestion_enabled). Empty recorded set → early-return (zero cost). NO provider-type
    branch (I6).
    """
    if not written_page_ids:
        logger.debug("delegated schema-suggestion: no recorded write_page ids — skip (zero cost)")
        return

    from sqlalchemy import String as _SAString
    from sqlalchemy import cast, select

    from app.models import Page
    from app.ops.review import generate_schema_suggestion as _gen_schema_sugg

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
        logger.debug("delegated schema-suggestion: recorded ids resolved to no live pages")
        return

    await _gen_schema_sugg(
        vault_id=vault_id,
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


# ── F18 / R12-2: domain auto-tag post-write hook (ADR-0054 §3/§4) ──────────────


async def apply_domain_tags(page: Page, new_tags: list[str]) -> None:
    """
    Rewrite *page*'s frontmatter ``tags`` to *new_tags* and persist incrementally (I1), WITHOUT
    a second ``data_version`` bump (ADR-0054 §3.2 — one ingest ⇒ at most one bump).

    Reads the on-disk file, replaces ONLY the ``tags`` key in the YAML frontmatter (all other
    frontmatter — type/title/sources/lang — is preserved), rewrites the file atomically, refreshes
    ``pages.tags`` + ``content_hash`` via ``persist_metadata``, and re-embeds the body. The K5
    ``links`` are unaffected by a frontmatter-only tag change, so they are left as-is. Reuses the
    same single-page primitives ``write_wiki_page`` uses (I1 — only this page is touched, no
    re-scan). This is the shared write-back seam for BOTH the ingest hook and the backfill.
    """
    from app.ops.enrich_wikilinks import _rejoin, _split_frontmatter

    abs_path = (settings.vault_root / page.file_path).resolve()
    text = abs_path.read_text(encoding="utf-8")
    fm_block, body = _split_frontmatter(text)

    # Parse the whole file so python-frontmatter round-trips every key; set tags authoritatively.
    post = frontmatter.loads(text)
    cleaned = [t for t in new_tags if t]
    if cleaned:
        post["tags"] = cleaned
    else:
        post.metadata.pop("tags", None)
    new_file_text = frontmatter.dumps(post) + "\n"

    # Fallback: if the parse-round-trip somehow lost the frontmatter block, keep the original
    # split-and-rejoin body (defence-in-depth; never corrupt the page).
    if not new_file_text.strip():
        new_file_text = _rejoin(fm_block, body)

    new_bytes = new_file_text.encode("utf-8")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(new_file_text, encoding="utf-8")

    await persist_metadata(
        page_id=page.id,
        vault_id=page.vault_id,
        file_path=page.file_path,
        title=page.title,
        page_type=page.page_type,
        sources=page.sources,
        tags=cleaned or None,
        content_hash=_sha256(new_bytes),
        source_mtime_ns=page.source_mtime_ns or 0,
    )
    # Re-embed the body (unchanged text, but keeps Qdrant payload consistent — cheap, I1).
    body_for_embedding = frontmatter.loads(new_file_text).content
    await upsert_vector(
        page_id=page.id,
        text=body_for_embedding,
        file_path=page.file_path,
        title=page.title,
        page_type=page.page_type,
    )
    # Reflect the new tags on the in-memory ORM object so callers see the merged set.
    page.tags = cleaned or None
    # NO bump_version() here — the ingest already bumped once (ADR-0054 §3.2, Do-NOT #3).


async def _auto_tag_written_pages(
    *,
    provider: InferenceProvider,
    written_pages: list[Page],
    origin_source: str,
) -> None:
    """
    ADR-0054 §3 auto-tag hook: classify each just-written page against the effective domain
    vocabulary and merge ``domain/*`` tags. Non-fatal per page (a failure leaves that page
    written+untagged; ingest continues). Dormant vocabulary ⇒ zero provider calls, one debug
    line max (Do-NOT #2). The provider's usage is already bound to this run's accumulator by the
    caller, so classification cost folds into the ingest run's ``total_cost_usd`` (I7, §3.3).
    """
    from app.config_overrides import effective_domain_vocabulary  # noqa: PLC0415
    from app.ingest.domain_tagger import (  # noqa: PLC0415
        classify_page_domains,
        merge_domain_tags,
    )

    vocabulary = effective_domain_vocabulary()
    if not vocabulary:
        # Dormant: no vocabulary ⇒ zero provider calls, zero log noise (I6, §3.2).
        logger.debug("_auto_tag_written_pages: vocabulary dormant — skip origin=%s", origin_source)
        return

    taggable = [p for p in written_pages if p.title and (p.file_path or "").startswith("wiki/")]
    for page in taggable:
        try:
            body = _read_body_for_classification(page)
            classified = await classify_page_domains(
                provider,
                page_title=page.title or "",
                page_content=body,
                vocabulary=vocabulary,
            )
            merged = merge_domain_tags(page.tags, classified)
            if merged != (page.tags or []):
                await apply_domain_tags(page, merged)
            logger.info(
                "auto_tag: page=%s domains=%s origin=%s",
                page.id,
                classified,
                origin_source,
            )
        except Exception as exc:  # noqa: BLE001 — non-fatal: page stays untagged (§3.4, Do-NOT #6)
            logger.warning(
                "auto_tag: classification failed for page=%s (non-fatal, page stays untagged): %s",
                page.id,
                exc,
            )


def _read_body_for_classification(page: Page) -> str:
    """Read the page body (frontmatter stripped) for the classifier; '' if unreadable."""
    from app.ops.enrich_wikilinks import _split_frontmatter

    abs_path = (settings.vault_root / page.file_path).resolve()
    try:
        text = abs_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    return _split_frontmatter(text)[1]


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


OVERVIEW_REL_PATH = "wiki/overview.md"


async def _update_overview(analysis: Analysis | None, origin_source: str) -> None:
    """
    REGENERATE the single auto-maintained overview.md note (F3, nashsu/llm_wiki parity).

    Mirrors llm_wiki: overview.md is a SINGLE note, fully OVERWRITTEN on each ingest with a
    concise narrative of the wiki's current themes/context — NOT an append-only marker log.

    Pipeline (bounded, degrade-safe):
      1. Build a compact context prompt from purpose.md (if present), a bounded set of existing
         page titles+types (indexed read — I1, no vault re-scan), and the just-ingested analysis.
      2. Make AT MOST ONE InferenceProvider call resolved via resolve_provider_config("ingest")
         (I6 — never a hardcoded backend), wrapped in wait_for(overview_timeout_seconds) and
         bounded by the resolved row's token_budget / overview_token_budget (I7). Cost logged.
      3. OVERWRITE vault/wiki/overview.md with valid Obsidian frontmatter (type: overview,
         title: <overview_title>) + the narrative body (I5).
      4. Index overview.md as a Page(type="overview") via the shared persist primitives so it
         surfaces in GET /pages and populates the nav "Overview" section (count 1).

    Fire-and-forget / degrade-safe (I7): if the provider is unavailable or the call fails/times
    out, the previous overview.md is KEPT (log a warning) and ingest still succeeds. This function
    NEVER raises into the ingest critical path — callers already treat it as best-effort.

    The (analysis, origin_source) signature is preserved so existing call sites / tests are
    unchanged; origin_source is used only for logging context here.
    """
    try:
        # ── Resolve provider (I6 — never hardcode; "no provider" → keep previous) ───
        resolved = await _resolve_overview_provider()
        if resolved is None:
            logger.debug(
                "_update_overview: no ingest provider resolved — keeping previous overview.md "
                "(I6: no silent default). origin=%s",
                origin_source,
            )
            # Still ensure a Page row exists for an already-present overview.md so the nav
            # Overview section can populate even before the first provider-backed regen.
            await _index_existing_overview_if_present()
            return
        provider, config_row = resolved

        # ── Build bounded context (purpose.md + existing titles + analysis) — I1 ────
        existing = await _load_overview_page_digest()
        # Language (F3 parity): use the just-ingested analysis language when available
        # (orchestrated route); otherwise (delegated route, analysis=None) detect the vault's
        # dominant content language from existing pages. If neither yields a language,
        # _build_overview_instruction falls back to the "match purpose + existing pages" directive.
        # settings.overview_language (OVERVIEW_LANGUAGE) FORCES the language when set — e.g. an
        # Italian user reading English source material wants an Italian overview regardless of
        # the content's detected language. Falls back to analysis/detected language otherwise.
        from app.config_overrides import effective_str  # noqa: PLC0415

        _effective_overview_lang = effective_str("overview_language", settings.overview_language)
        if _effective_overview_lang:
            overview_lang: str | None = _effective_overview_lang
        elif analysis is not None and getattr(analysis, "language", None):
            overview_lang = analysis.language
        else:
            overview_lang = await _detect_vault_language()
        instruction = _build_overview_instruction(
            analysis=analysis, existing_digest=existing, lang=overview_lang
        )

        _raw_budget: Any = getattr(config_row, "token_budget", None) or getattr(
            settings, "overview_token_budget", 3_000
        )
        token_budget = int(_raw_budget) if _raw_budget is not None else 3_000
        timeout_s = float(getattr(settings, "overview_timeout_seconds", 30.0))

        # ── Bind a run-scoped Usage ledger (I7 — cost logged out of band) ──────────
        accumulator = UsageAccumulator()
        provider.bind_accumulator(accumulator)

        # ── ONE bounded call, no loop, no retry (I7) ───────────────────────────────
        try:
            narrative = await asyncio.wait_for(
                _overview_chat_collect(provider, instruction, token_budget),
                timeout=timeout_s,
            )
        except TimeoutError:
            logger.warning(
                "_update_overview: provider call timed out after %.1fs — keeping previous "
                "overview.md (degrade, never fail ingest). origin=%s",
                timeout_s,
                origin_source,
            )
            await _index_existing_overview_if_present()
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_update_overview: provider call failed (%s) — keeping previous overview.md. "
                "origin=%s",
                exc,
                origin_source,
            )
            await _index_existing_overview_if_present()
            return
        finally:
            logger.info(
                "overview regen provider call: tokens=%d cost_usd=%.4f calls=%d origin=%s",
                accumulator.total_tokens,
                round(accumulator.total_cost_usd, 4),
                accumulator.calls,
                origin_source,
            )

        narrative = (narrative or "").strip()
        if not narrative:
            logger.warning(
                "_update_overview: provider returned empty narrative — keeping previous "
                "overview.md. origin=%s",
                origin_source,
            )
            await _index_existing_overview_if_present()
            return

        # ── OVERWRITE overview.md with valid frontmatter (I5) + index it ────────────
        await _write_and_index_overview(narrative)
    except Exception as exc:  # noqa: BLE001
        # Belt-and-braces: never let overview maintenance fail an ingest (I7).
        logger.warning(
            "_update_overview: unexpected failure (%s) — keeping previous overview.md. origin=%s",
            exc,
            origin_source,
        )


async def _resolve_overview_provider() -> tuple[InferenceProvider, object] | None:
    """
    Resolve the InferenceProvider for operation='ingest' (I6) for the overview regen call.

    Returns (provider, config_row) or None when no provider_config resolves / DB unavailable.
    NEVER hardcodes a backend; NEVER branches on isinstance/type/class-name (I6). Mirrors
    ops/review.py::_resolve_review_provider and _resolve_ingest_provider_config.
    """
    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    try:
        config_row = await resolve_provider_config("ingest")
    except ConfigNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("_resolve_overview_provider: provider resolution unavailable: %s", exc)
        return None

    try:
        provider = resolve_provider(config_row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_resolve_overview_provider: provider build failed: %s", exc)
        return None
    return provider, config_row


async def _load_overview_page_digest() -> str:
    """
    Compact digest of existing wiki page titles+types (bounded indexed read — I1, no re-scan).

    Excludes the reserved catalogue types (overview/index) so the overview never summarizes
    itself. Capped at overview_max_titles. Returns a newline list "- <title> [<type>]".
    """
    from sqlalchemy import select

    max_titles = int(getattr(settings, "overview_max_titles", 200))
    lines: list[str] = []
    try:
        async with get_session() as session:
            rows = list(
                (
                    await session.execute(
                        select(Page.title, Page.page_type)
                        .where(
                            Page.vault_id == settings.vault_id,
                            Page.deleted_at.is_(None),
                            Page.title.isnot(None),
                            Page.page_type.notin_(["overview", "index"]),
                        )
                        .order_by(Page.updated_at.desc())
                        .limit(max_titles)
                    )
                ).all()
            )
        for title, ptype in rows:
            t = (title or "").strip()
            if not t:
                continue
            lines.append(f"- {t} [{(ptype or '?').strip() or '?'}]")
    except Exception as exc:  # noqa: BLE001
        logger.debug("_load_overview_page_digest: title read failed (non-fatal): %s", exc)
    return "\n".join(lines) if lines else "(no pages yet)"


_ISO_LANG_NAMES = {
    "it": "Italian",
    "en": "English",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
}

# Bounded sample size for vault-language detection (I7 — cheap, no full walk).
_LANG_DETECT_SAMPLE = 25


async def _detect_vault_language() -> str | None:
    """
    Detect the vault's dominant content language from existing wiki pages' `lang` frontmatter
    (nashsu/llm_wiki parity — the overview must match the vault content language, not default
    to English). Used for the DELEGATED ingest route where no per-source Analysis (hence no
    detected language) is available.

    I1 — NO directory walk: the file set comes from a BOUNDED DB query over the pages table
    (most-recently-updated non-meta pages); only those specific files are read for their `lang`
    frontmatter. Returns the modal `lang`, or None when undetectable — the caller then falls back
    to the "match purpose + existing pages" directive. Bounded to _LANG_DETECT_SAMPLE (I7).
    """
    from sqlalchemy import select

    async with get_session() as session:
        rows = await session.execute(
            select(Page.file_path)
            .where(Page.deleted_at.is_(None))
            .where(Page.page_type.not_in(["index", "log", "overview"]))
            .order_by(Page.updated_at.desc())
            .limit(_LANG_DETECT_SAMPLE)
        )
        file_paths = [fp for (fp,) in rows.all() if fp]

    counts: dict[str, int] = {}
    for rel in file_paths:
        path = settings.vault_root / rel
        try:
            post = frontmatter.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001, S112 — tolerant: skip unreadable/malformed files
            continue
        lang = post.metadata.get("lang")
        if isinstance(lang, str) and len(lang) >= 2:
            counts[lang.lower()] = counts.get(lang.lower(), 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda k: counts[k])


def _build_overview_instruction(
    *, analysis: Analysis | None, existing_digest: str, lang: str | None = None
) -> str:
    """
    Build the single overview-regeneration prompt (F3). Inputs: purpose.md (F2, if present),
    the existing page titles+types digest, and the just-ingested analysis. Asks for a concise
    narrative body ONLY (no frontmatter, no title heading — the writer adds valid frontmatter).

    Language (nashsu/llm_wiki buildLanguageDirective parity): the overview MUST be written in
    the vault's language, not defaulted to English. When the detected `lang` is known (from the
    just-ingested analysis) it is stated explicitly; in all cases the model is told to match the
    language of the purpose.md + existing pages provided below (covers the delegated route where
    analysis — hence lang — is None).
    """
    if lang:
        lang_name = _ISO_LANG_NAMES.get(lang.lower(), lang)
        lang_directive = (
            f"MANDATORY OUTPUT LANGUAGE: {lang_name} ({lang}). Write the ENTIRE overview in "
            f"{lang_name}. Do NOT translate to English.\n\n"
        )
    else:
        lang_directive = (
            "MANDATORY OUTPUT LANGUAGE: write the overview in the SAME LANGUAGE as the wiki "
            "purpose and existing pages shown below. Do NOT default to English.\n\n"
        )
    purpose_parts: list[str] = []
    for name in ("purpose.md",):
        path = settings.vault_root / name
        if path.exists():
            try:
                purpose_parts.append(path.read_text(encoding="utf-8").strip())
            except OSError:
                pass
    purpose_block = "\n\n".join(purpose_parts).strip() or "(no purpose.md)"

    analysis_block = "(none)"
    if analysis is not None:
        topics = ", ".join(analysis.topics[:12]) if analysis.topics else "(none)"
        entities = ", ".join(analysis.entities[:12]) if analysis.entities else "(none)"
        summary = (analysis.summary or "").strip() or "(none)"
        analysis_block = f"topics: {topics}\nentities: {entities}\nsummary: {summary}"

    return (
        lang_directive
        + "You maintain the single OVERVIEW note of a self-organizing wiki. Regenerate it now to "
        "capture the CURRENT big picture of the whole wiki: its main themes, how the pages relate, "
        "and the key context a reader needs before diving in.\n\n"
        "STYLE — write a flowing, DISCURSIVE narrative, like a well-written encyclopedia "
        "overview essay (NOT a bulleted index):\n"
        "  - Open with a short paragraph on what this wiki covers and why.\n"
        "  - Organize the rest into a few thematic paragraphs; you MAY put a short `## Heading` "
        "before each major theme, but the body of each theme MUST be PROSE, not a list.\n"
        "  - Weave the [[wikilinks]] INLINE into full sentences — explain how pages relate and "
        "connect, e.g. 'Software discovery starts with [[X]], which feeds normalization in [[Y]] "
        "and reconciliation in [[Z]].' Do NOT emit long bulleted lists of "
        "`- [[Page]] — description`.\n"
        "  - Link generously to the existing pages below using their EXACT titles, but always "
        "embedded in the narrative. Favor readable flowing prose over enumeration.\n"
        "Do NOT output YAML frontmatter, a top-level `#` title heading, or any preamble like "
        "'Here is' — output ONLY the Markdown body (starting with the opening paragraph).\n\n"
        f"# Wiki purpose\n{purpose_block}\n\n"
        f"# Existing pages (title [type])\n{existing_digest}\n\n"
        f"# Most recent ingest analysis\n{analysis_block}\n"
    )


async def _overview_chat_collect(
    provider: InferenceProvider, instruction: str, token_budget: int
) -> str:
    """
    Run ONE capability-agnostic provider.chat() turn and collect the full text (I6/I7).

    Rides the existing chat() seam (backend-neutral — no isinstance/type branch). Usage is
    recorded out of band onto the bound accumulator by the provider. token_budget is surfaced in
    the prompt only for provider hints; the hard bounds are the single call + wait_for timeout.
    """
    from app.ingest.schemas import Message

    chunks: list[str] = []
    async for chunk in await provider.chat(
        messages=[Message(role="user", content=instruction)],
        retrieval_context="",
    ):
        chunks.append(chunk)
    return "".join(chunks).strip()


async def _write_and_index_overview(narrative: str) -> None:
    """
    OVERWRITE vault/wiki/overview.md with valid frontmatter (I5) + index it as a Page (I1).

    Frontmatter: type: overview, title: <overview_title>. The file is rebuilt from scratch (full
    overwrite — F3 regeneration). Then a Page row is upserted via persist_metadata (key by
    (vault_id, file_path), hash over the exact file bytes) and embedded via upsert_vector so
    GET /pages returns it and the nav Overview section shows count 1.
    """
    title = str(getattr(settings, "overview_title", "Overview")) or "Overview"
    post = frontmatter.Post(narrative, type="overview", title=title)
    serialized = frontmatter.dumps(post)
    file_text = serialized + "\n"

    overview_path = settings.wiki_dir / "overview.md"
    overview_path.parent.mkdir(parents=True, exist_ok=True)
    overview_path.write_text(file_text, encoding="utf-8")

    await _index_overview_file(file_text, title)
    logger.info("_update_overview: regenerated + indexed overview.md (title=%r)", title)


async def _index_overview_file(file_text: str, title: str) -> None:
    """
    Upsert the Page row for wiki/overview.md (type="overview") from the given file bytes (I1).

    Reuses the existing live row's id when present (upsert by (vault_id, file_path)); content_hash
    hashes the EXACT file bytes (matches GET /pages/{id}/content recompute). Embeds the body via
    upsert_vector. Does NOT touch index.md / log.md (those stay disk-only by design).
    """
    from sqlalchemy import select

    async with get_session() as _id_sess:
        existing = (
            await _id_sess.execute(
                select(Page).where(
                    Page.vault_id == settings.vault_id,
                    Page.file_path == OVERVIEW_REL_PATH,
                    Page.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
    page_id = existing.id if existing is not None else uuid.uuid4()

    file_bytes = file_text.encode("utf-8")
    await persist_metadata(
        page_id=page_id,
        vault_id=settings.vault_id,
        file_path=OVERVIEW_REL_PATH,
        title=title,
        page_type="overview",
        sources=None,
        tags=None,
        content_hash=_sha256(file_bytes),
        source_mtime_ns=0,
    )
    # Embed the narrative body (frontmatter excluded) for retrieval parity with wiki pages.
    body_for_embedding = _strip_leading_frontmatter(file_text)
    await upsert_vector(
        page_id=page_id,
        text=body_for_embedding,
        file_path=OVERVIEW_REL_PATH,
        title=title,
        page_type="overview",
    )


async def _index_existing_overview_if_present() -> None:
    """
    If overview.md already exists on disk but is not yet indexed as a Page, index it (degrade
    path). Ensures the nav Overview section can populate from a previously-regenerated file even
    when the current run's provider call is unavailable/failed. Best-effort — never raises.
    """
    overview_path = settings.wiki_dir / "overview.md"
    if not overview_path.exists():
        return
    try:
        file_text = overview_path.read_text(encoding="utf-8")
        meta = frontmatter.loads(file_text).metadata
        title = str(meta.get("title") or getattr(settings, "overview_title", "Overview"))
        await _index_overview_file(file_text, title)
    except Exception as exc:  # noqa: BLE001
        logger.debug("_index_existing_overview_if_present: skipped (non-fatal): %s", exc)


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


async def _open_ingest_run(
    *,
    origin_source: str,
    provider_name: str,
    provider_type: str,
    model_id: str,
    route: str,
    started_at: datetime,
    retry_count: int = 0,
) -> uuid.UUID:
    """
    INSERT a status="running" row at the START of the pipeline (ADR-0046 §2).

    Returns the generated run_id so the pipeline can thread it to the queue manager
    and to the terminal UPDATE in _finalize_ingest_run.

    finished_at is set to started_at as a placeholder (the ADR says non-null; the
    terminal UPDATE overwrites it).  GET /ingest/runs already nulls completed_at when
    status == "running" (main.py _ingest_run_to_response — no change needed there).
    """
    run_id = uuid.uuid4()
    async with get_session() as session:
        session.add(
            IngestRun(
                id=run_id,
                vault_id=settings.vault_id,
                page_id=None,
                provider_name=provider_name,
                provider_type=provider_type,
                model_id=model_id,
                route=route,
                max_iter_used=0,
                total_tokens=0,
                total_cost_usd=0,
                converged=False,
                cost_anomaly=False,
                started_at=started_at,
                finished_at=started_at,  # placeholder; overwritten by _finalize_ingest_run
                pages_created=0,
                status="running",
                error_message=None,
                source_path=origin_source,
                retry_count=retry_count,
            )
        )
    logger.debug("_open_ingest_run: run_id=%s source=%s", run_id, origin_source)
    return run_id


async def _finalize_ingest_run(
    *,
    run_id: uuid.UUID,
    provider_name: str,
    provider_type: str,
    model_id: str,
    route: str,
    max_iter_used: int,
    total_tokens: int,
    total_cost_usd: float,
    converged: bool,
    cost_anomaly: bool,
    finished_at: datetime,
    pages_created: int,
    error_message: str | None = None,
    status_override: str | None = None,
) -> None:
    """
    UPDATE the ingest_runs row opened by _open_ingest_run (ADR-0046 §2).

    Sets all terminal fields — status, finished_at, cost, tokens, pages_created,
    converged, cost_anomaly, error_message.  status_override lets the cancel path
    write status="cancelled" directly without going through _derive_run_status.

    Preserves the provider/cost accounting: the I7 cost ledger is truthful because
    accumulated cost (even partial, from before a cancel or failure) is recorded.
    """
    from sqlalchemy import update as sa_update

    if status_override is not None:
        status = status_override
    else:
        status = _derive_run_status(converged=converged, error_message=error_message)

    async with get_session() as session:
        await session.execute(
            sa_update(IngestRun)
            .where(IngestRun.id == run_id)
            .values(
                provider_name=provider_name,
                provider_type=provider_type,
                model_id=model_id,
                route=route,
                max_iter_used=max_iter_used,
                total_tokens=total_tokens,
                total_cost_usd=total_cost_usd,
                converged=converged,
                cost_anomaly=cost_anomaly,
                finished_at=finished_at,
                pages_created=pages_created,
                status=status,
                error_message=error_message,
            )
        )
    logger.debug("_finalize_ingest_run: run_id=%s status=%s", run_id, status)


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
    Persist ONE terminal ingest_runs row in a single INSERT — the cost-audit system of
    record (I7, ADR-0008 §4).

    This is the standalone (open+finalize collapsed) variant, retained for callers that
    are NOT watcher/queue-driven and therefore have no live "running" row to update —
    notably the review-create generation path (ops/review.py). The watcher/orchestrator
    ingest lifecycle uses _open_ingest_run + _finalize_ingest_run instead (ADR-0046 §2);
    those runs appear in the live activity queue, whereas review-create runs do not.

    source_path / retry_count are left at their column defaults (NULL / 0) — a review-create
    run has no raw source file in the queue.
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
    logger.debug("_write_ingest_run: standalone terminal row status=%s route=%s", status, route)


async def _is_raw_sources_page(page_id: uuid.UUID) -> bool:
    """
    Return True if the page's file_path starts with "raw/sources/" (ADR-0046 §3).

    These pages must NOT be cascade-deleted on cancel — the raw source file stays
    so the user can retry.  The source-summary page (if any) is in wiki/sources/,
    not raw/sources/, so this guard only protects the mechanical source index row.
    """
    from sqlalchemy import select

    async with get_session() as session:
        row = await session.execute(select(Page.file_path).where(Page.id == page_id))
        file_path = row.scalar_one_or_none()
    if file_path is None:
        return False
    return file_path.startswith("raw/sources/")


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


# ── F3 cross-ingest connectivity: existing-pages catalogue (K3) ──────────────────
#
# Each ingest otherwise produces an isolated graph island because the ingest LLM does not
# know which pages already exist, so it invents new titles → [[wikilinks]] don't match →
# links are dangling (no edge). nashsu/llm_wiki feeds the existing index catalogue to the
# LLM so it links to existing pages → one connected web. We inject the catalogue INTO THE
# CONTEXT STRING (never into provider code — I6).
#
# Bounded (I7): capped by title count AND char budget; a one-shot indexed query, no rescan.
_CATALOGUE_MAX_TITLES = 400
_CATALOGUE_MAX_CHARS = 8000
# Meta/infra page types the LLM should never link to as content pages.
_CATALOGUE_EXCLUDED_TYPES = frozenset({INDEX_TYPE, OVERVIEW_TYPE, "log"})


async def _load_existing_pages_catalogue() -> str:
    """
    Build the "Existing wiki pages — LINK TO THESE" catalogue (F3/K3 cross-ingest connectivity).

    Query live pages (deleted_at IS NULL), EXCLUDING meta/infra pages (index/log/overview page
    types AND anything under raw/sources/). Group the remaining real wiki-page titles by
    page_type and format a compact section instructing the LLM to link with the EXACT existing
    title instead of inventing a duplicate.

    Bounded (I7): capped at _CATALOGUE_MAX_TITLES titles and _CATALOGUE_MAX_CHARS chars. When the
    vault exceeds the cap we keep the most-recently-updated subset, append an explicit truncation
    note, and log.warning the count dropped (never silent). Returns "" when there is nothing to
    link to yet (first-ever ingest).
    """
    from sqlalchemy import select

    async with get_session() as session:
        result = await session.execute(
            select(Page.title, Page.page_type).where(
                Page.deleted_at.is_(None),
                Page.title.is_not(None),
                Page.page_type.not_in(_CATALOGUE_EXCLUDED_TYPES),
                Page.file_path.not_like("raw/sources/%"),
            )
            # Most-recent first so truncation keeps the freshest pages (F3 intent).
            .order_by(Page.updated_at.desc())
        )
        rows = result.all()

    if not rows:
        return ""

    total = len(rows)
    truncated = total > _CATALOGUE_MAX_TITLES
    kept_rows = rows[:_CATALOGUE_MAX_TITLES]

    # Group titles by page_type, preserving the most-recent-first order within each group.
    grouped: dict[str, list[str]] = {}
    for title, page_type in kept_rows:
        grouped.setdefault(page_type or "other", []).append(title)

    header = (
        "# Existing wiki pages — LINK TO THESE\n"
        "When a concept/entity you write about already exists below, you MUST reference it with "
        "its EXACT title in a [[wikilink]] instead of creating a duplicate page. Only create a "
        "new page when nothing below fits."
    )
    sections: list[str] = [header]
    for page_type in sorted(grouped):
        titles = grouped[page_type]
        lines = "\n".join(f"- {t}" for t in titles)
        sections.append(f"## {page_type}\n{lines}")

    catalogue = "\n\n".join(sections)

    # Char-budget cap (I7): titles cap is the primary bound, but very long titles could still
    # blow the char budget — trim on a line boundary and note it.
    char_truncated = False
    if len(catalogue) > _CATALOGUE_MAX_CHARS:
        char_truncated = True
        cut = catalogue[:_CATALOGUE_MAX_CHARS]
        # Trim back to the last complete line so we never emit a half title.
        nl = cut.rfind("\n")
        catalogue = cut[:nl] if nl > 0 else cut

    if truncated or char_truncated:
        catalogue += (
            f"\n\n_(catalogue truncated: showing a subset of {total} existing pages, "
            "most recent first — link to any exact title you know exists.)_"
        )
        logger.warning(
            "_load_existing_pages_catalogue: vault has %d linkable pages; catalogue truncated "
            "to fit budget (max_titles=%d, max_chars=%d) — F3/I7",
            total,
            _CATALOGUE_MAX_TITLES,
            _CATALOGUE_MAX_CHARS,
        )

    return catalogue


async def _load_ingest_context() -> str:
    """
    Full ingest provider context (F2/F3): purpose.md + schema.md + the existing-pages catalogue.

    Assembled once per ingest in the async pipeline and threaded into BOTH the orchestrated loop
    and the delegated/CLI path so the LLM links to existing pages on every backend (I6 — the
    guidance lives in the context STRING, not in any provider). The catalogue is appended so it
    never shadows the schema/purpose rules.
    """
    base = _load_vault_context()
    try:
        catalogue = await _load_existing_pages_catalogue()
    except Exception as exc:  # noqa: BLE001
        # Best-effort enhancement — a DB hiccup must never fail ingest (I7). Degrade to
        # purpose+schema only; the LLM simply won't get the existing-pages hint this run.
        logger.warning(
            "_load_ingest_context: existing-pages catalogue unavailable (%s) — "
            "ingesting without it (F3 degrade)",
            exc,
        )
        catalogue = ""
    if not catalogue:
        return base
    return f"{base}\n\n{catalogue}" if base else catalogue


# ── R7-6: folderContext hint (F3 topical context from subfolder layout) ──────────
#
# When a source lives in subfolders under the import root (e.g. raw/sources/servicenow/itam/
# sam/foo.md), the relative folder path is a strong topical hint the LLM should use when
# classifying + writing pages. We derive a compact "servicenow / itam / sam" string from the
# origin_source relative path and inject it INTO THE CONTEXT STRING (never provider code — I6),
# so it reaches BOTH the orchestrated analyze() and the delegated/CLI system prompt.
#
# Bounded (I7): capped at _FOLDER_CONTEXT_MAX_SEGMENTS segments and _FOLDER_CONTEXT_MAX_CHARS.
_FOLDER_CONTEXT_MAX_SEGMENTS = 8
_FOLDER_CONTEXT_MAX_CHARS = 500
# Leading path prefixes stripped before computing the topical segments (the "import root").
_FOLDER_CONTEXT_ROOTS = ("raw/sources/", "raw/", "wiki/")


def _folder_context(origin_source: str) -> str:
    """
    Derive a compact folderContext hint from *origin_source* (R7-6), or "" when the file sits
    directly under a known root (no subfolders → no hint).

    "raw/sources/servicenow/itam/sam/foo.md" → "servicenow / itam / sam".
    Bounded to _FOLDER_CONTEXT_MAX_SEGMENTS segments and _FOLDER_CONTEXT_MAX_CHARS chars (I7).
    """
    if not origin_source:
        return ""
    # Normalize separators (F15 path normalization) and drop the filename.
    rel = origin_source.replace("\\", "/").lstrip("/")
    for root in _FOLDER_CONTEXT_ROOTS:
        if rel.startswith(root):
            rel = rel[len(root) :]
            break
    parts = [p for p in rel.split("/") if p]
    # Drop the trailing filename segment; only the directory path is topical context.
    segments = parts[:-1]
    if not segments:
        return ""
    segments = segments[:_FOLDER_CONTEXT_MAX_SEGMENTS]
    joined = " / ".join(segments)
    if len(joined) > _FOLDER_CONTEXT_MAX_CHARS:
        joined = joined[:_FOLDER_CONTEXT_MAX_CHARS].rstrip()
    return joined


def _folder_context_block(origin_source: str) -> str:
    """
    Build the folderContext section appended to the ingest context (R7-6), or "" when there is
    no subfolder hint. Phrased as an explicit topical hint for the analysis/classification step.
    """
    fc = _folder_context(origin_source)
    if not fc:
        return ""
    return (
        "# folderContext\n"
        f"This document comes from the folder path: {fc} — use it as topical context when "
        "classifying the document and naming/linking pages."
    )


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
    from app.config_overrides import effective_bool  # noqa: PLC0415

    if not effective_bool("embeddings_enabled", settings.embeddings_enabled):
        logger.info(
            "upsert_vector: embeddings disabled (effective EMBEDDINGS_ENABLED=false) — "
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
