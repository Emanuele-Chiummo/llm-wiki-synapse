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

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from fastmcp import FastMCP

import frontmatter  # python-frontmatter

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
    await persist_metadata(
        page_id=page_id,
        vault_id=settings.vault_id,
        file_path=rel,
        title=_title,
        page_type=_type,
        sources=_sources,
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

    # ── ROUTE: the single capability check (I6) ──────────────────────────────
    if caps.supports_agentic_loop:
        route: Literal["orchestrated", "delegated"] = "delegated"
        converged, delegated_pages_written = await _delegate_ingest(
            provider=provider,
            source_text=source_text,
            origin_source=origin_source,
        )
    else:
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
        for page in pages:
            await write_wiki_page(None, page, origin_source)
        await _update_overview(analysis, origin_source)

    finished_at = datetime.now(UTC)

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
        pages_written=delegated_pages_written if caps.supports_agentic_loop else len(pages),
        total_tokens=total_tokens,
        total_cost_usd=total_cost_usd,
        converged=converged,
        cost_anomaly=cost_anomaly,
    )


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
    except (TimeoutError, ConnectionError) as exc:
        # Provider fallback — bounded to EXACTLY ONCE (I7, ADR-0009 §4).
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
        except (TimeoutError, ConnectionError) as exc2:  # no chains (AC-K2-7)
            raise IngestError("primary and fallback providers both failed") from exc2


async def _delegate_ingest(
    *,
    provider: InferenceProvider,
    source_text: str,
    origin_source: str,
) -> tuple[bool, int]:
    """
    Delegate the whole ingest to an agentic provider (CLI). The provider runs its own bounded
    agent loop and writes pages through the MCP write_page tool (which reuses write_wiki_page,
    ADR-0010 §2), so I1/I5 hold without the orchestrator touching the pages here.

    Returns (converged, pages_written). The MCP server object + system prompt assembly are the
    backend-engineer/SDK wiring seam; v0.2 surfaces a clear error if invoked without it.
    """
    delegate = getattr(provider, "delegate_ingest", None)
    if delegate is None:
        raise IngestError(
            "agentic provider exposes no delegate_ingest() — cannot delegate (ADR-0007 §3)"
        )
    system_prompt = _load_vault_context()
    # ── MCP wiring seam (ADR-0010 §2) ──────────────────────────────────────────
    # Import lazily to avoid a circular import; app.mcp.server imports from orchestrator.
    _mcp_server: FastMCP[Any] | None = None
    try:
        from app.mcp.server import mcp as _mcp_server
    except Exception as _mcp_exc:  # noqa: BLE001
        logger.warning("MCP server unavailable; delegate_ingest will run without it: %s", _mcp_exc)
    result = await delegate(
        source_text=source_text,
        system_prompt=system_prompt,
        vault_dir=str(settings.vault_root),
        mcp_server=_mcp_server,  # FastMCP server (ADR-0010); cli.py seam
    )
    converged = bool(getattr(result, "converged", False))
    pages_written = int(getattr(result, "pages_written", 0))
    return converged, pages_written


async def _resolve_fallback_provider_config() -> object | None:
    """
    Return the fallback ProviderConfig row (is_fallback=True) at the narrowest matching scope,
    or None if no fallback is configured (ADR-0009 §fallback). Bounded to exactly one attempt
    by the caller (_run_orchestrated — I7).
    """
    from app.provider_config_service import resolve_fallback_provider_config

    return await resolve_fallback_provider_config()


# ── Wiki page writer (reused by the MCP write_page tool — ADR-0010 §2) ─────────


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

    sources = list(page.frontmatter.sources)
    if origin_source and origin_source not in sources:
        sources.append(origin_source)

    # Build the .md file: frontmatter block + body (ADR-0011).
    fm_dump = page.frontmatter.model_dump()
    fm_dump["sources"] = sources
    fm_dump["type"] = page_type  # serialize enum as its string value for Obsidian (I5)
    post = frontmatter.Post(page.content, **fm_dump)
    serialized = frontmatter.dumps(post)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(serialized + "\n", encoding="utf-8")

    page_id = uuid.uuid4()
    await persist_metadata(
        page_id=page_id,
        vault_id=settings.vault_id,
        file_path=rel_path,
        title=page.title,
        page_type=page_type,
        sources=sources,
        content_hash=_sha256(serialized.encode("utf-8")),
        source_mtime_ns=0,
    )
    await upsert_vector(
        page_id=page_id,
        text=page.content,
        file_path=rel_path,
        title=page.title,
        page_type=page_type,
    )
    await append_log(rel_path)
    await bump_version()

    # ── K5: parse + persist wikilinks (incremental, I1) ──────────────────────
    from app.wiki.links import parse_wikilinks, persist_links

    parsed = parse_wikilinks(page.content)
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
) -> None:
    """Persist one ingest_runs row — the cost-audit system of record (I7, ADR-0008 §4)."""
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
) -> None:
    """
    Upsert the `pages` row for *page_id* inside a single Postgres transaction.

    Handles both INSERT (new page) and UPDATE (re-ingest of existing page).
    Clears deleted_at on resurrection (ADR-0005 — same file_path recreated).
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
    """
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
