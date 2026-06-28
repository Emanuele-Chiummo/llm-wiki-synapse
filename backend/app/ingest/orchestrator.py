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
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import frontmatter  # python-frontmatter

from app.config import settings
from app.db import get_session
from app.embeddings import get_embedding_client
from app.models import Page, VaultState
from app.qdrant_client import delete_point, upsert_point

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
    # F17 EXTENSION POINT (v0.2): resolve InferenceProvider here and run
    # analyze → generate → validate loop BEFORE persist_metadata (ADR-0003).
    # v0.1 leaves this branch empty — no provider, no LLM call.
    # ─────────────────────────────────────────────────────────────────────────

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
