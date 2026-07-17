"""Ingest pipeline — moved from orchestrator.py (1.7.0 PR2).

The public entry points (``ingest_file`` / ``delete_file``), the F17 capability-aware
``run_ingest_pipeline`` + its orchestrated/delegated route helpers, the source-summary
guarantee, the language guard, and the ingest_runs lifecycle helpers. Behaviour is
unchanged; patched / orchestrator-resident symbols are reached via ``orch.<name>`` so
``app.ingest.orchestrator`` remains the single monkeypatch surface.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import frontmatter
import httpx

import app.ingest.orchestrator as orch
from app import runtime_state
from app.config import settings
from app.ingest.provider.base import InferenceProvider, ProviderTransientError, UsageAccumulator
from app.ingest.schemas import Analysis, PageType, WikiFrontmatter, WikiPage
from app.ingest.validate import IngestCancelled
from app.models import IngestRun, Page, VaultState
from app.wiki.summary import extract_first_paragraph_summary

logger = logging.getLogger(__name__)


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
    rel = orch._relative_path(path)

    # ── Stat ──────────────────────────────────────────────────────────────────
    try:
        stat = path.stat()
    except FileNotFoundError:
        logger.warning("ingest_file: path not found %s — skipping", path)
        raise

    current_mtime_ns: int = stat.st_mtime_ns

    # ── Load existing DB row (if any) ─────────────────────────────────────────
    existing = await orch._load_page(rel)

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
    current_hash = orch._sha256(raw_bytes)

    # ── Hash unchanged → touch mtime only, skip (ADR-0001 step 2b) ───────────
    if existing is not None and existing.content_hash == current_hash:
        logger.debug("ingest_file: mtime changed but hash identical — touch mtime %s", rel)
        await orch._touch_mtime(existing.id, current_mtime_ns)
        return IngestResult(page_id=existing.id, status="skipped")

    # ── Parse frontmatter (K6 — tolerant: missing fields → NULL) ─────────────
    meta = orch._parse_frontmatter(raw_bytes, rel)

    # ─────────────────────────────────────────────────────────────────────────
    # F17 EXTENSION POINT (v0.2): if a provider is configured for this vault, run the
    # capability-aware pipeline (analyze → generate → validate loop OR CLI delegation)
    # to produce wiki pages from the source, BEFORE the source row is persisted
    # (ADR-0003). When no provider_config row resolves, fall through to the v0.1
    # mechanical path (source-only indexing) — never silently pick a backend (I6).
    # ─────────────────────────────────────────────────────────────────────────
    provider_cfg = await orch._resolve_ingest_provider_config()
    if provider_cfg is not None:
        source_text = raw_bytes.decode("utf-8", errors="replace")
        # ── R8-2 / F12: vision captioning for image files ────────────────────────
        # For an image extension, replace the (garbage) decoded-bytes source_text with a
        # provider-generated caption when VISION_CAPTIONS_ENABLED and the provider supports
        # vision (cache-first, bounded, cost folded into this run's ledger — I7). On any
        # miss/failure the caption is None and we keep the extract.py placeholder text so the
        # pre-R8-2 behaviour is unchanged.
        seed_usage: object | None = None
        if path.suffix.lower() in orch._VISION_IMAGE_EXTENSIONS:
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
        elif path.suffix.lower() in orch._AV_EXTENSIONS:
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
        await orch.run_ingest_pipeline(
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
    _generation_key_raw = meta.get("synapse_generation_key")
    _generation_key = (
        str(_generation_key_raw).strip().lower() if _generation_key_raw is not None else None
    )
    if _generation_key is not None:
        try:
            # Mechanical ingest remains tolerant of arbitrary user frontmatter, but the
            # reserved corpus identity must cross the same strict I5 boundary as generated
            # pages before it can participate in the live unique index.
            if _type is None:
                raise ValueError("generation key requires a page type")
            _generation_key = WikiFrontmatter(
                type=PageType(_type),
                title=_title or path.stem,
                synapse_generation_key=_generation_key,
            ).synapse_generation_key
        except (TypeError, ValueError):
            logger.warning(
                "Ignoring invalid synapse_generation_key during raw ingest: file=%s",
                rel,
            )
            _generation_key = None

    # Frontmatter-stripped body, computed BEFORE persist_metadata so the K3 gloss summary
    # (1.9.4 W6) and the Qdrant embedding both derive from the exact same clean text.
    # Embedding the BODY with a title breadcrumb — NOT the raw bytes. Embedding the YAML
    # frontmatter block (type/sources/tags/lang) injects boilerplate tokens that pollute vector
    # similarity; nashsu/llm_wiki strips frontmatter (text-chunker.ts) and prepends the
    # title/heading path. We keep the single whole-page point (ADR-0002) but feed it clean
    # "<title>\n\n<body>" text. (Per-page embed-time chunking — llm_wiki's N-points-per-page —
    # remains an ADR-gated change.)
    _decoded = raw_bytes.decode("utf-8", errors="replace")
    try:
        _embed_body = frontmatter.loads(_decoded).content
    except Exception:  # noqa: BLE001 — malformed/binary: fall back to the raw decoded text
        _embed_body = _decoded

    await orch.persist_metadata(
        page_id=page_id,
        vault_id=settings.vault_id,
        file_path=rel,
        title=_title,
        page_type=_type,
        sources=_sources,
        tags=_tags,
        generation_key=_generation_key,
        summary=extract_first_paragraph_summary(_embed_body),
        content_hash=current_hash,
        source_mtime_ns=current_mtime_ns,
    )

    # ── Embed + upsert Qdrant ─────────────────────────────────────────────────
    text_for_embedding = f"{_title}\n\n{_embed_body}".strip() if _title else _embed_body.strip()
    await orch.upsert_vector(
        page_id=page_id,
        text=text_for_embedding,
        file_path=rel,
        title=_title,
        page_type=_type,
        vault_id=settings.vault_id,
    )

    # ── K4 append log line ────────────────────────────────────────────────────
    await orch.append_log(rel, page_type=_type or "source", title=_title)

    # ── Bump vault_state.data_version ─────────────────────────────────────────
    await orch.bump_version()

    # ── Notify GraphCache of the version bump (I2, ADR-0014 §2) ──────────────
    # Minimal hook: call notify_bump() on the module-level cache singleton if it
    # has been initialised (lifespan). No-op in test envs without the lifespan.
    # DO NOT alter provider/loop logic here (NB-1/NB-4 guard).
    try:
        _cache = runtime_state.graph_cache()

        if _cache is not None:
            async with orch.get_session() as _vs_sess:
                from sqlalchemy import select

                _vs_row = await _vs_sess.execute(
                    select(VaultState).where(VaultState.vault_id == settings.vault_id)
                )
                _vs = _vs_row.scalar_one_or_none()
                _new_version = _vs.data_version if _vs is not None else 0
            _cache.notify_bump(_new_version)
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

    rel = orch._relative_path(Path(file_path))

    async with orch.get_session() as session:
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
    await orch.delete_point(page_id)
    logger.info("delete_file: soft-deleted %s page_id=%s", rel, page_id)


# ── F17 capability-aware pipeline (v0.2) ──────────────────────────────────────


@dataclass
class IngestRunResult:
    """Summary of one F17 ingest run (returned by run_ingest_pipeline)."""

    route: Literal["orchestrated"]
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


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Classify *exc* as a provider rate-limit (429) failure (BE-QUEUE-1, 1.9.4 W3).

    ``ProviderTransientError`` (raised by the CLI backend, provider/base.py) already folds
    429 / overloaded / SDK-execution failures into one type since the SDK gives no finer
    signal — treated as rate-limit-class here because 429 is its documented primary cause.
    The API / Ollama backends surface a raw ``httpx.HTTPStatusError`` via
    ``resp.raise_for_status()`` (provider/api.py, provider/ollama.py); only status 429 counts
    as a rate-limit — a 500/other 4xx must NOT auto-pause the whole queue.
    """
    if isinstance(exc, ProviderTransientError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429
    return False


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
    provider = orch.resolve_provider(provider_config_row)
    accumulator = UsageAccumulator()
    # R8-2: fold any pre-loop cost (image captioning) into this run's ledger (I7).
    _seed_accumulator(accumulator, seed_usage)
    provider.bind_accumulator(accumulator)
    caps = provider.capabilities()

    started_at = datetime.now(UTC)
    written_pages: list[Page] = []
    iterations = 0
    converged = False
    route: Literal["orchestrated"] = "orchestrated"
    # 1.9.1 W5 (NC-1): last loop's non-convergence diagnostics ({stop_reason, iterations,
    # last_errors, tokens_used, token_budget}); persisted verbatim onto the ingest_runs row below.
    diagnostics: dict[str, object] | None = None

    # ── ADR-0046 path-normalization fix: derive the absolute queue key ────────
    # The watcher passes absolute paths to admit/should_skip; the queue must use
    # the SAME key end-to-end so cancel suppression matches (ADR-0046 fix).
    # origin_source (relative) is kept for ALL DB / file / log uses below.
    _queue_key: str = abs_source if abs_source is not None else str(Path(origin_source).resolve())

    # ── ADR-0046: open a "running" row + register with the queue manager ──────
    run_id = await orch._open_ingest_run(
        origin_source=origin_source,
        provider_name=caps.name,
        provider_type=caps.mode,
        model_id=str(getattr(provider_config_row, "model_id", "")),
        route=route,  # will be overwritten on delegate; the row uses the resolved value
        started_at=started_at,
        retry_count=orch.ingest_queue.get_retry_count(_queue_key),
    )
    handle = orch.ingest_queue.open_run(run_id, _queue_key)
    cancel_event = handle.cancel_event

    # ── Store resolved route on the handle so snapshot() can compute ETA ─────
    # Set before the route try-block so the handle always has a route when active.
    # Will be overwritten to "delegated" inside the try-block if the CLI path is taken.
    orch.ingest_queue.set_route(run_id, route)

    # ── BE-QUEUE-2 (1.9.4 W3): per-capability concurrency cap (I7) ────────────
    # Bounds how many CLI/API/Local runs execute their provider calls at once, on top of
    # the flat INGEST_MAX_CONCURRENCY gate in watcher.py. Acquired here (dispatch point,
    # right before the provider is actually called) and released at every exit below.
    await orch.ingest_queue.acquire_capability_slot(caps.mode)

    # ── ROUTE: the single capability check (I6) ──────────────────────────────
    # Wrapped so a route failure still persists an ingest_runs row with status="failed" and the
    # error_message + accumulated cost (BUG A2 / I7), then re-raises so the REST/watcher caller
    # surfaces the error unchanged.
    try:
        # ── F3/K3 cross-ingest connectivity: assemble the provider context ONCE ──────
        # purpose.md + schema.md + the existing-pages catalogue ("LINK TO THESE"). Built INSIDE
        # the try-block (B3 fix) so a TOCTOU FileNotFoundError on purpose.md/schema.md — file
        # removed between exists() and read_text() — is caught by the except block below and
        # finalises the run as "failed" instead of stranding it as "running" forever.
        # Threaded into BOTH the delegated (CLI) and orchestrated (API/Local) paths so the LLM
        # links to existing pages on every backend → one connected graph instead of isolated
        # islands (I6 — guidance is in the context STRING, never in provider code).
        ingest_context = await orch._load_ingest_context()
        # R7-6: prepend the folderContext hint (subfolder topical context) so it reaches BOTH the
        # orchestrated analyze() vault_context and the delegated/CLI system_prompt (I6 — the hint
        # is in the STRING, not provider code). "" when the source has no subfolder path.
        _folder_block = orch._folder_context_block(origin_source)
        if _folder_block:
            ingest_context = (
                f"{_folder_block}\n\n{ingest_context}" if ingest_context else _folder_block
            )

        # ── ADR-0076 / 2.0.0: block loop is the only surviving ingest path ──────────
        # All providers (Local, API, CLI) run the block loop via provider.complete().
        # The legacy JSON loop and the CLI delegated path were removed in 2.0.0 (ADR-0076).
        converged, iterations, written_pages, diagnostics = await _run_orchestrated_blocks(
            provider=provider,
            accumulator=accumulator,
            source_text=source_text,
            origin_source=origin_source,
            config_row=provider_config_row,
            run_id=run_id,
            cancel_event=cancel_event,
        )
        # overview.md is not touched on the block path (ADR-0078 ownership change).
        # D4 graph-node parity: upsert index/log Page rows.
        try:
            await orch._index_index_and_log_files()
        except Exception as _il_b_exc:  # noqa: BLE001
            logger.warning(
                "run_ingest_pipeline: block-path index/log node hook failed " "(non-fatal): %s",
                _il_b_exc,
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
        await orch._finalize_ingest_run(
            run_id=run_id,
            provider_name=caps.name,
            provider_type=caps.mode,
            model_id=str(getattr(provider_config_row, "model_id", "")),
            route=route,
            max_iter_used=iterations,
            total_tokens=accumulator.total_tokens,
            total_cost_usd=round(accumulator.total_cost_usd, 4),
            converged=False,
            cost_anomaly=round(accumulator.total_cost_usd, 4) > orch.COST_ANOMALY_THRESHOLD_USD,
            finished_at=finished_at,
            pages_created=0,
            error_message="cancelled by user",
            status_override="cancelled",
        )
        orch.ingest_queue.finalize(run_id, "cancelled", error="cancelled by user")
        orch.ingest_queue.release_capability_slot(caps.mode)
        # Do NOT re-raise — cancel is a normal, user-initiated terminal state (ADR-0046 §3).
        return IngestRunResult(
            route=route,
            pages_written=0,
            total_tokens=accumulator.total_tokens,
            total_cost_usd=round(accumulator.total_cost_usd, 4),
            converged=False,
            cost_anomaly=round(accumulator.total_cost_usd, 4) > orch.COST_ANOMALY_THRESHOLD_USD,
        )

    except Exception as exc:
        # Persist a failed-run UPDATE (I7 ledger stays truthful: cost incurred before the failure is
        # still recorded) then re-raise so the caller's error handling is unchanged.
        finished_at = datetime.now(UTC)
        await orch._finalize_ingest_run(
            run_id=run_id,
            provider_name=caps.name,
            provider_type=caps.mode,
            model_id=str(getattr(provider_config_row, "model_id", "")),
            route=route,
            max_iter_used=iterations,
            total_tokens=accumulator.total_tokens,
            total_cost_usd=round(accumulator.total_cost_usd, 4),
            converged=False,
            cost_anomaly=round(accumulator.total_cost_usd, 4) > orch.COST_ANOMALY_THRESHOLD_USD,
            finished_at=finished_at,
            # Truthful ledger (I7): report the pages actually persisted before the failure, not a
            # literal 0. record_written tracks each successful write, so handle.written_page_ids is
            # the count of pages that survive in Postgres + on disk. (The cancel path above keeps 0
            # because it cascade-deletes its partial output first.)
            pages_created=len(handle.written_page_ids),
            error_message=str(exc) or exc.__class__.__name__,
        )
        orch.ingest_queue.finalize(run_id, "failed", error=str(exc) or exc.__class__.__name__)
        orch.ingest_queue.release_capability_slot(caps.mode)
        # BE-QUEUE-1 (1.9.4 W3): a 429/rate-limit failure auto-pauses the queue with a
        # decaying-cooldown auto-resume (I7 — cooldown is capped, never grows unbounded).
        # Never overrides an existing MANUAL pause (queue_manager.pause_for_rate_limit).
        if _is_rate_limit_error(exc):
            orch.ingest_queue.pause_for_rate_limit()
        logger.warning(
            "ingest_run FAILED provider=%s origin=%s error=%s",
            caps.name,
            origin_source,
            exc,
        )
        raise

    finished_at = datetime.now(UTC)

    # Actual pages persisted this run (2.0.0: always the orchestrated block path).
    pages_written = len(written_pages)
    page_type_counts = _page_type_counts(written_pages)

    # ── Finalize accumulator → ingest_runs row UPDATE (I7, ADR-0008 §4 / ADR-0046 §2) ──────
    total_tokens = accumulator.total_tokens
    total_cost_usd = round(accumulator.total_cost_usd, 4)
    cost_anomaly = total_cost_usd > orch.COST_ANOMALY_THRESHOLD_USD

    await orch._finalize_ingest_run(
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
        page_type_counts=page_type_counts,
        diagnostics=diagnostics,
    )

    # ── ADR-0046: notify queue manager of terminal success ────────────────────
    terminal_status = _derive_run_status(converged=converged, error_message=None)
    orch.ingest_queue.finalize(run_id, terminal_status)
    orch.ingest_queue.release_capability_slot(caps.mode)
    # BE-QUEUE-1 (1.9.4 W3): any successful run resets the rate-limit backoff ladder so the
    # NEXT 429 starts cooling down from the base tier again (I7 — no permanently-escalated cap).
    orch.ingest_queue.reset_rate_limit_backoff()

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
            orch.COST_ANOMALY_THRESHOLD_USD,
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


# ── ADR-0076 / 2.0.0: block-based orchestrated route ────────────────────────────────────────────


def _read_vault_root_file(name: str) -> str:
    """Read a vault-root file (schema.md / purpose.md) tolerantly — "" when absent (llm_wiki reads
    schema.md / purpose.md from the project root)."""
    try:
        return (settings.vault_root / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_wiki_file(name: str) -> str:
    """Read a wiki/ aggregate file (index.md / overview.md) tolerantly — "" when absent."""
    try:
        return (settings.wiki_dir / name).read_text(encoding="utf-8")
    except OSError:
        return ""


async def _vault_output_language() -> str | None:
    """Return ``vault_state.output_language`` (the F3 target output language) or None. Bounded
    indexed read; degrade-safe (any error → None → the block prompts omit the language directive).
    """
    from sqlalchemy import select

    try:
        async with orch.get_session() as session:
            row = await session.execute(
                select(VaultState.output_language).where(VaultState.vault_id == settings.vault_id)
            )
            value = row.scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 — degrade-safe: no language directive on any error
        logger.debug("_vault_output_language: read failed (non-fatal): %s", exc)
        return None
    return value if isinstance(value, str) and value.strip() else None


def _block_has_source_summary(written_pages: list[Page], origin_source: str) -> bool:
    """True when a written block is a ``source`` page traceable to *origin_source* (the F3
    source-summary guarantee dedupe check, mirroring _ensure_source_summary)."""
    for page in written_pages:
        if (getattr(page, "page_type", None) or "") == "source" and origin_source in (
            getattr(page, "sources", None) or []
        ):
            return True
    return False


async def _run_orchestrated_blocks(
    *,
    provider: InferenceProvider,
    accumulator: UsageAccumulator,
    source_text: str,
    origin_source: str,
    config_row: object,
    run_id: uuid.UUID,
    cancel_event: asyncio.Event | None = None,
) -> tuple[bool, int, list[Page], dict[str, object]]:
    """Block-based orchestrated ingest (ADR-0076, nashsu/llm_wiki v0.6.3 parity).

    Loads schema.md / purpose.md / wiki index+overview, runs the bounded block loop
    (:func:`app.ingest.block_loop.run_block_loop`), writes each FILE block through
    :func:`app.ingest.block_writer.write_block_page` (custom page types persist as the raw
    ``pages.type`` string), and guarantees a source-summary page via the SAME
    :func:`_ensure_source_summary` fallback the JSON route uses. Returns
    ``(converged, iterations, written_pages, diagnostics)`` — ``diagnostics`` is
    :meth:`app.ingest.block_loop.BlockLoopResult.diagnostics` (1.9.1 W5, NC-1), persisted verbatim
    onto the ``ingest_runs`` row by the caller. REVIEW blocks are logged but NOT enqueued here —
    that is a later PR (WS-C). Bounds (max_iter + token_budget) and cost accounting (I7) are the
    block loop's; the ingest_runs lifecycle stays the caller's.
    """
    from app.config_overrides import effective_int
    from app.ingest import block_loop, block_writer
    from app.ingest.prompts import language_prompt_name
    from app.wiki.schema import parse_page_type_routing

    schema_md = _read_vault_root_file("schema.md")
    purpose_md = _read_vault_root_file("purpose.md")
    index_md = _read_wiki_file("index.md")
    overview_md = _read_wiki_file("overview.md")
    routing = parse_page_type_routing(schema_md)

    # Language: vault_state.output_language → display name (else None → no directive).
    language_name = language_prompt_name(await _vault_output_language())

    max_iter = int(getattr(config_row, "max_iter", None) or 3)
    token_budget = int(getattr(config_row, "token_budget", None) or 60_000)
    max_context_chars = effective_int(
        "ingest_context_char_budget", settings.ingest_context_char_budget
    )
    review_min_chars = effective_int(
        "ingest_review_stage_min_chars", settings.ingest_review_stage_min_chars
    )
    review_min_blocks = effective_int(
        "ingest_review_stage_min_file_blocks", settings.ingest_review_stage_min_file_blocks
    )

    # Source filename hint for the generation prompt (llm_wiki sourceFileName): the source
    # identity (raw/sources/ stripped) falls back to the bare basename.
    source_filename = orch._source_identity(origin_source) or Path(origin_source).name

    result = await block_loop.run_block_loop(
        provider=provider,
        accumulator=accumulator,
        source_text=source_text,
        purpose=purpose_md,
        schema=schema_md,
        index=index_md,
        source_filename=source_filename,
        origin_source=origin_source,
        language_name=language_name,
        max_iter=max_iter,
        token_budget=token_budget,
        cancel_event=cancel_event,
        on_phase=lambda p: orch.ingest_queue.set_phase(run_id, p),
        overview=overview_md,
        max_context_chars=max_context_chars,
        review_stage_min_chars=review_min_chars,
        review_stage_min_file_blocks=review_min_blocks,
    )

    written_pages: list[Page] = []
    orch.ingest_queue.set_phase(run_id, "writing")
    # BE-PERF-2: one resolver-maps bulk query for the WHOLE document (FILE blocks + the possible
    # source-summary fallback below), instead of one per block. write_block_page/write_wiki_page
    # fold each page they write into `_link_maps` in memory, so link resolution across this
    # document's own pages is unaffected (see build_resolver_maps/add_page_to_resolver_maps docs).
    # index.md regeneration + the data_version bump are likewise deferred to ONE call at the end.
    _link_maps = None
    if result.file_blocks:
        from app.wiki.links import build_resolver_maps as _build_link_maps

        async with orch.get_session() as _maps_sess:
            _link_maps = await _build_link_maps(_maps_sess, settings.vault_id)
    for file_block in result.file_blocks:
        page = await block_writer.write_block_page(
            rel_path=file_block.path,
            content=file_block.content,
            origin_source=origin_source,
            routing=routing,
            provider=provider,
            resolver_maps=_link_maps,
            skip_index_update=True,
            skip_version_bump=True,
        )
        if page is not None:
            written_pages.append(page)
            orch.ingest_queue.record_written(run_id, page.id)

    # F3 source-summary guarantee (nashsu/llm_wiki hasSourceSummary parity): when the model
    # omitted a source page traceable to this origin, synthesize one via the SHARED WikiPage
    # fallback + JSON writer (a source page is a base type, so write_wiki_page handles it).
    if not _block_has_source_summary(written_pages, origin_source):
        if _link_maps is None:
            from app.wiki.links import build_resolver_maps as _build_link_maps

            async with orch.get_session() as _maps_sess:
                _link_maps = await _build_link_maps(_maps_sess, settings.vault_id)
        for fallback_page in _ensure_source_summary([], None, origin_source):
            written = await orch.write_wiki_page(
                None,
                fallback_page,
                origin_source,
                resolver_maps=_link_maps,
                skip_index_update=True,
                skip_version_bump=True,
            )
            written_pages.append(written)
            orch.ingest_queue.record_written(run_id, written.id)

    if written_pages:
        # One index.md regeneration + one data_version bump for the whole document (BE-PERF-2).
        from app.wiki.index import update_index as _update_index_once

        async with orch.get_session() as _idx_sess:
            await _update_index_once(_idx_sess, settings.vault_root)
        await orch.bump_version()

    # ── WS-C (ADR-0079): enqueue REVIEW blocks from block loop (PR5c TODO closed) ──
    # Each ReviewBlock from run_block_loop is persisted as a ReviewItem via the same
    # enqueue_review seam propose_reviews uses. content_key dedup prevents duplicates on
    # re-ingest. Soft-capped at _BLOCK_REVIEW_ENQUEUE_CAP/run (I7). proposal_origin="ai"
    # (the LLM produced these blocks — no new origin value needed). Fire-and-forget: any
    # failure logs a WARNING and NEVER fails the ingest run (blocks are advisory).
    _BLOCK_REVIEW_ENQUEUE_CAP = 50
    if result.review_blocks:
        try:
            from app.ops.review import _content_key as _rev_content_key  # noqa: PLC0415
            from app.ops.review import enqueue_review as _blk_enqueue  # noqa: PLC0415

            vault_id = settings.vault_id
            _known_types = frozenset({"missing-page", "suggestion", "contradiction", "duplicate"})
            blocks_to_enqueue = result.review_blocks[:_BLOCK_REVIEW_ENQUEUE_CAP]
            _dropped = len(result.review_blocks) - len(blocks_to_enqueue)
            if _dropped:
                logger.info(
                    "run_ingest_pipeline: block reviews capped at %d (dropped %d) origin=%s",
                    _BLOCK_REVIEW_ENQUEUE_CAP,
                    _dropped,
                    origin_source,
                )
            for _rb in blocks_to_enqueue:
                _rb_type = _rb.type if _rb.type in _known_types else "suggestion"
                _ckey = _rev_content_key(
                    vault_id=vault_id,
                    item_type=_rb_type,
                    proposed_title=_rb.title or None,
                )
                await _blk_enqueue(
                    vault_id=vault_id,
                    item_type=_rb_type,
                    proposed_title=_rb.title or None,
                    rationale=_rb.description or None,
                    search_queries=_rb.search_queries or None,
                    proposal_origin="ai",
                    content_key=_ckey,
                )
        except Exception as _blk_rev_exc:  # noqa: BLE001
            logger.warning(
                "run_ingest_pipeline: block review enqueue failed (non-fatal): %s",
                _blk_rev_exc,
            )

    logger.info(
        "run_ingest_pipeline: block route converged=%s iters=%d pages=%d reviews=%d origin=%s",
        result.converged,
        result.iterations,
        len(written_pages),
        len(result.review_blocks),
        origin_source,
    )
    return result.converged, result.iterations, written_pages, result.diagnostics()


async def _delegate_ingest(
    *,
    provider: InferenceProvider,
    source_text: str,
    origin_source: str,
    system_prompt: str | None = None,
    generation_key: str | None = None,
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
        system_prompt = orch._load_vault_context()
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
        if generation_key is None:
            _mcp_server = build_sdk_mcp_server(origin_source=origin_source)
        else:
            _mcp_server = build_sdk_mcp_server(
                origin_source=origin_source,
                generation_key=generation_key,
            )
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


async def _enrich_wikilinks_for_delegated(
    *,
    vault_id: str,
    written_page_ids: list[str],
) -> None:
    """
    Run the wikilink-enrichment post-pass for the delegated (CLI) route (ADR-0067, F4 parity).

    The CLI agent writes pages via MCP write_page; the orchestrated post-write enrich hook never
    sees them, so without this the delegated route yields graph-sparse pages (no back-links /
    ``related:``). Loads the written pages by id (bounded indexed read, I1 — no vault re-scan) and
    drives the SAME ``enrich_wikilinks`` seam the orchestrated route uses (I6 — no provider-type
    branch). Short-circuits on an empty set; ``enrich_wikilinks`` itself never raises.
    """
    if not written_page_ids:
        return
    # String-form id compare keeps the read dialect-portable (mirrors the propose_reviews loader).
    from sqlalchemy import String as _SAString
    from sqlalchemy import cast, select

    from app.models import Page
    from app.ops.enrich_wikilinks import enrich_wikilinks as _enrich_wikilinks

    async with orch.get_session() as session:
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
    result = await _enrich_wikilinks(rows, vault_id)
    logger.info(
        "run_ingest_pipeline: delegated wikilink enrichment pages=%d links=%d cost_usd=%.4f",
        result.pages_enriched,
        result.links_added,
        result.total_cost_usd,
    )


async def _ensure_source_summary_for_delegated(
    *,
    vault_id: str,
    written_page_ids: list[str],
    origin_source: str,
) -> Page | None:
    """
    Deterministic source-summary guarantee for the delegated (CLI) route (nashsu/llm_wiki
    parity — ingest.ts:1209-1244 ``hasSourceSummary`` fallback).

    The orchestrated route calls ``_ensure_source_summary`` before write; the delegated agent
    writes its own pages via MCP ``write_page`` and may omit the source summary. This ADDITIVE
    guarantee mirrors llm_wiki, which writes the same fallback source file when no source summary
    was produced. It inspects the pages the agent actually wrote and, ONLY when none is a
    ``source`` page traceable to *origin_source*, synthesizes + writes the minimal fallback via the
    shared ``write_wiki_page`` seam.

    I6-safe: additive (creates a page the agent didn't write) — it never mutates or deletes the
    agent's own writes, and it never duplicates an existing source page (the same dedupe guard as
    the orchestrated ``_ensure_source_summary``). Analysis is None on the delegated route, so the
    body degrades to "(Analysis not available)" exactly like llm_wiki's
    ``analysis ? … : "(Analysis not available)"``; the vault language is used for the frontmatter
    ``lang`` so a non-English vault gets a localised stub. Returns the newly written ``Page`` (so
    the caller can thread its id into the downstream delegated hooks) or ``None`` when a source
    page already existed / nothing was written.
    """
    from sqlalchemy import String as _SAString
    from sqlalchemy import cast, select

    from app.ingest.schemas import Analysis, PageType, SuggestedPage
    from app.models import Page as _PageModel

    if written_page_ids:
        async with orch.get_session() as session:
            rows = list(
                (
                    await session.execute(
                        select(_PageModel).where(
                            cast(_PageModel.id, _SAString).in_([str(i) for i in written_page_ids]),
                            _PageModel.deleted_at.is_(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for r in rows:
                session.expunge(r)
        # llm_wiki hasSourceSummary dedupe guard: a source page traceable to the origin already
        # exists → nothing to do (no churn, no duplicate).
        for r in rows:
            if r.page_type == PageType.SOURCE.value and origin_source in (r.sources or []):
                logger.debug(
                    "delegated source-summary: agent already wrote a source page for %s",
                    origin_source,
                )
                return None

    # Synthesize the minimal fallback source page. summary=None → body "(Analysis not available)"
    # (llm_wiki parity); language = the vault language so the stub matches the vault, not English.
    synthesized = Analysis(
        topics=["ingest"],
        entities=[],
        language=(getattr(settings, "overview_language", "") or "en"),
        suggested_pages=[SuggestedPage(title="(delegated ingest)", type=PageType.SOURCE)],
        summary=None,
    )
    fallback_pages = orch._ensure_source_summary([], synthesized, origin_source)
    if not fallback_pages:
        return None
    written = await orch.write_wiki_page(None, fallback_pages[0], origin_source)
    logger.info(
        "delegated source-summary: agent omitted the source page — wrote fallback %r for %s "
        "(nashsu/llm_wiki hasSourceSummary parity)",
        written.title,
        origin_source,
    )
    return written


async def _propose_reviews_for_delegated(
    *,
    vault_id: str,
    written_page_ids: list[str],
    origin_source: str,
    source_text: str,
) -> None:
    """
    Drive propose_reviews for the delegated (CLI) route (ADR-0044 §4.2, Phase E).

    Loads ONLY the Page rows the CLI agent wrote through MCP write_page (recorded ids) and calls
    the SAME `propose_reviews(...)` seam the orchestrated route uses. The delegated agent owns its
    private reasoning, so analysis=None is explicit — no title-only Analysis is fabricated. The raw
    source is forwarded and bounded by the shared review prompt builder; written-page excerpts are
    likewise loaded from these exact ids and bounded there. NO provider-type branch (I6).

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

    from app.models import Page
    from app.ops.review import propose_reviews as _propose_reviews

    async with orch.get_session() as session:
        raw_rows = list(
            (
                await session.execute(
                    select(Page.id, Page.title, Page.page_type, Page.file_path).where(
                        cast(Page.id, _SAString).in_([str(i) for i in written_page_ids]),
                        Page.deleted_at.is_(None),
                    )
                )
            ).all()
        )

    # Keep the delegated review hand-off deliberately narrow: review only needs these four
    # fields. Selecting a whole ORM row couples this seam to every future pages-table column.
    from types import SimpleNamespace

    rows = [
        SimpleNamespace(id=r.id, title=r.title, page_type=r.page_type, file_path=r.file_path)
        for r in raw_rows
    ]

    if not rows:
        logger.debug("delegated propose_reviews: recorded ids resolved to no live pages")
        return

    await _propose_reviews(
        vault_id=vault_id,
        analysis=None,
        written_pages=rows,  # type: ignore[arg-type]  # exact bounded Page projection
        origin_source=origin_source,
        source_text=source_text,
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

    async with orch.get_session() as session:
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

    async with orch.get_session() as session:
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
    by the caller (_run_orchestrated_blocks — I7).
    """
    from app.provider_config_service import resolve_fallback_provider_config

    return await resolve_fallback_provider_config()


def _ensure_source_summary(
    pages: list[WikiPage], analysis: Analysis | None, origin_source: str
) -> list[WikiPage]:
    """
    Guarantee EXACTLY one source-summary page traceable to *origin_source* (F3, nashsu/llm_wiki
    parity — ingest.ts:1209-1244 ``hasSourceSummary`` fallback).

    Semantics changed for llm_wiki page-type parity: previously a source page was synthesized
    ONLY when the batch was empty, which — combined with the flat 5-type generation prompt —
    left most raw files without a `source` page and skewed the distribution. Now we ALWAYS
    ensure a `source`-type page whose sources[] includes *origin_source* exists in the batch:

      • If the model already produced one (dedupe guard) → return *pages* unchanged (no churn,
        no duplicate).
      • Otherwise synthesize a minimal source page from the analysis (title/summary) and APPEND
        it — even when the model produced entity/concept pages but omitted the source summary.

    This restores ~1 source page per raw file (the llm_wiki 132-source distribution). Existing
    pages are preserved and stay first in the list, so callers that read ``pages[0]`` (review
    Create path) keep their model-produced page.

    D3 (ADR-0063 §9, nashsu/llm_wiki parity — ingest.ts:1219-1244): the synthesized page's title
    is ``Source: <identity>`` and its body is ``# Source: <identity>\n\n<analysis text>`` where
    <identity> is the origin path minus the `raw/sources/` prefix (``_source_identity``). This
    matches llm_wiki's fallback source page exactly (previously ``Source summary: <stem>``).
    """
    from app.ingest.schemas import PageType, WikiFrontmatter

    # Dedupe / churn guard (llm_wiki hasSourceSummary): a source page already traceable to the
    # origin exists → leave the batch untouched.
    for page in pages:
        if page.type is PageType.SOURCE and origin_source in (page.frontmatter.sources or []):
            return pages

    lang = analysis.language if analysis is not None else "en"
    identity = orch._source_identity(origin_source) or Path(origin_source).stem
    title = f"Source: {identity}"
    analysis_text = (analysis.summary if analysis and analysis.summary else None) or (
        "(Analysis not available)"
    )
    # Body mirrors llm_wiki's fallback: an H1 `# Source: <identity>` heading + the analysis text.
    body = f"# Source: {identity}\n\n{analysis_text}"
    fm = WikiFrontmatter(type=PageType.SOURCE, title=title, sources=[origin_source], lang=lang)
    source_page = WikiPage(title=title, type=PageType.SOURCE, content=body, frontmatter=fm)
    return [*pages, source_page]


# Page types EXEMPT from the wrong-language drop guard (Feature 3, ADR-0063 §5). `source` is the
# F3 source-summary (traceability — must never be dropped); `entity` pages legitimately quote
# cross-language proper nouns which confuse naive script detection (matches nashsu/llm_wiki, which
# checks only authoritative /concepts/-style content). index/overview/log are never in `pages`.
_LANGUAGE_GUARD_EXEMPT_TYPES: frozenset[PageType] = frozenset({PageType.SOURCE, PageType.ENTITY})


def _drop_wrong_language_pages(pages: list[WikiPage], analysis: Analysis | None) -> list[WikiPage]:
    """
    Feature 3 (ADR-0063 §5): drop generated pages whose body script-family contradicts the
    resolved target output language (``Analysis.language``), before validate/write.

    Deterministic, script-based detection (no provider call, I7-friendly). Only cross-script
    mismatches drop; intra-Latin differences never do (avoids false drops). Exempt: `source` and
    `entity` pages (see ``_LANGUAGE_GUARD_EXEMPT_TYPES``). Disabled config, no analysis, or an
    empty target language → returns *pages* unchanged. NEVER raises into ingest (degrade-safe:
    on any detection error the page is kept).
    """
    from app.config_overrides import effective_bool

    if not effective_bool("ingest_language_guard_enabled", settings.ingest_language_guard_enabled):
        return pages
    target = (analysis.language if analysis is not None else "").strip()
    if not target or not pages:
        return pages

    from app.ingest.language import body_matches_target_language

    kept: list[WikiPage] = []
    for page in pages:
        if page.type in _LANGUAGE_GUARD_EXEMPT_TYPES:
            kept.append(page)
            continue
        try:
            ok = body_matches_target_language(page.content, target)
        except Exception as exc:  # noqa: BLE001 — degrade-safe: keep the page on any error
            logger.debug("language guard: detection error for %r (keeping): %s", page.title, exc)
            kept.append(page)
            continue
        if ok:
            kept.append(page)
        else:
            logger.info(
                "language guard: DROPPED page %r (type=%s) — body language != target %r (F3/§5)",
                page.title,
                page.type.value,
                target,
            )
    return kept


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


def _page_type_counts(pages: list[Page]) -> dict[str, int]:
    """Return an explicit six-type distribution for one successful generation run."""
    counts = {page_type.value: 0 for page_type in PageType}
    for page in pages:
        page_type = getattr(page, "page_type", None)
        if page_type in counts:
            counts[page_type] += 1
    return counts


async def _page_type_counts_for_ids(page_ids: list[str]) -> dict[str, int]:
    """Resolve delegated MCP write ids to the same six-type distribution, bounded to this run."""
    if not page_ids:
        return {page_type.value: 0 for page_type in PageType}

    from sqlalchemy import String as SAString
    from sqlalchemy import cast, select

    async with orch.get_session() as session:
        rows = (
            await session.execute(
                select(Page.page_type).where(
                    Page.vault_id == settings.vault_id,
                    cast(Page.id, SAString).in_([str(page_id) for page_id in page_ids]),
                    Page.deleted_at.is_(None),
                )
            )
        ).scalars()
    counts = {page_type.value: 0 for page_type in PageType}
    for page_type in rows:
        if page_type in counts:
            counts[page_type] += 1
    return counts


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
    async with orch.get_session() as session:
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
    page_type_counts: dict[str, int] | None = None,
    error_message: str | None = None,
    status_override: str | None = None,
    diagnostics: dict[str, object] | None = None,
) -> None:
    """
    UPDATE the ingest_runs row opened by _open_ingest_run (ADR-0046 §2).

    Sets all terminal fields — status, finished_at, cost, tokens, pages_created,
    converged, cost_anomaly, error_message.  status_override lets the cancel path
    write status="cancelled" directly without going through _derive_run_status.

    ``diagnostics`` (1.9.1 W5, NC-1): the loop's stop_reason/last_errors/token accounting
    (:meth:`app.ingest.block_loop.BlockLoopResult.diagnostics`), surfaced to the UI so a
    ``converged_false`` run explains itself instead of a bare "not converged" label. ``None`` on
    the delegated/CLI route (no bounded loop to report).

    Preserves the provider/cost accounting: the I7 cost ledger is truthful because
    accumulated cost (even partial, from before a cancel or failure) is recorded.
    """
    from sqlalchemy import update as sa_update

    if status_override is not None:
        status = status_override
    else:
        status = _derive_run_status(converged=converged, error_message=error_message)

    async with orch.get_session() as session:
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
                page_type_counts=page_type_counts,
                status=status,
                error_message=error_message,
                diagnostics=diagnostics,
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
    page_type_counts: dict[str, int] | None = None,
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
    async with orch.get_session() as session:
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
                page_type_counts=page_type_counts,
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

    async with orch.get_session() as session:
        row = await session.execute(select(Page.file_path).where(Page.id == page_id))
        file_path = row.scalar_one_or_none()
    if file_path is None:
        return False
    return file_path.startswith("raw/sources/")


class IngestError(RuntimeError):
    """Raised when an ingest run cannot complete (surfaced as HTTP 500 by the REST path)."""
