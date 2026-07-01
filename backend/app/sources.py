"""
Sources view — raw-source file browser + preview (nashsu/llm_wiki Sources tab parity).

GET  /sources                   — recursive listing of vault/raw/sources/ tree (I7-bounded)
GET  /sources/content?path=<rel> — metadata + preview payload for one file
GET  /sources/raw?path=<rel>     — stream raw bytes for inline image/PDF preview
GET  /sources/derived-pages?path=<rel> — pages derived from this source (sources[] pivot)
DELETE /sources?path=<rel>       — delete raw file + cascade-delete derived pages (I1/I2)

Path-safety: EVERY client-supplied path is routed through resolve_under_sources()
from app.upload (ADR-0020 §2.2 — belt-and-braces containment check). 404 on escape.

Invariants honoured:
  I1  — listing/content/raw are read-only; delete reuses incremental cascade (no rescan).
  I2  — DELETE bumps data_version + notifies _graph_cache via cascade machinery.
  I6  — zero InferenceProvider calls.
  I7  — bounded listing (SOURCES_LIST_MAX entries), bounded text preview (SOURCES_TEXT_MAX_CHARS),
        bounded raw bytes (SOURCES_RAW_MAX_BYTES), hard 413 for oversize raw.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.config import settings
from app.upload import resolve_under_sources

logger = logging.getLogger(__name__)


async def _cascade_delete_page(page_uuid: uuid.UUID) -> None:
    """
    Thin wrapper around ops.cascade_delete.cascade_delete (monkeypatch seam for tests).

    Defined at module level so tests can patch 'app.sources._cascade_delete_page'
    without fighting local-import scoping.  Keeps I6/I7 invariants in
    ops/cascade_delete.py intact — this wrapper is strictly a routing shim.
    """
    from app.ops.cascade_delete import cascade_delete as _cd

    await _cd(page_uuid)

# ── Bounds (I7) ───────────────────────────────────────────────────────────────

# Maximum total entries returned by GET /sources (I7 listing cap)
SOURCES_LIST_MAX: int = int(os.environ.get("SOURCES_LIST_MAX", "5000"))

# Maximum text preview characters for text-like files (I7 text cap)
SOURCES_TEXT_MAX_CHARS: int = int(os.environ.get("SOURCES_TEXT_MAX_CHARS", str(200_000)))

# Maximum raw bytes streamed by GET /sources/raw (I7 size guard)
SOURCES_RAW_MAX_BYTES: int = int(os.environ.get("SOURCES_RAW_MAX_BYTES", str(50 * 1024 * 1024)))

router = APIRouter(prefix="/sources", tags=["sources"])

# ── Category mapping (mirrors llm_wiki getFileCategory) ──────────────────────
# Each category drives the frontend's display mode.

_EXT_TO_CATEGORY: dict[str, str] = {
    # Text / plaintext
    ".txt": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    # Code
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".jsx": "code",
    ".tsx": "code",
    ".json": "code",
    ".yaml": "code",
    ".yml": "code",
    ".toml": "code",
    ".ini": "code",
    ".cfg": "code",
    ".sh": "code",
    ".bash": "code",
    ".zsh": "code",
    ".fish": "code",
    ".html": "code",
    ".htm": "code",
    ".css": "code",
    ".xml": "code",
    ".csv": "data",
    ".tsv": "data",
    ".log": "text",
    ".rst": "text",
    ".tex": "text",
    # Documents (extractable via extract.py)
    ".pdf": "pdf",
    ".docx": "document",
    ".pptx": "document",
    ".xlsx": "data",
    # Images
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".svg": "image",
    ".bmp": "image",
    ".ico": "image",
    ".tiff": "image",
    ".tif": "image",
    # AV
    ".mp3": "av",
    ".mp4": "av",
    ".wav": "av",
    ".m4a": "av",
    ".ogg": "av",
    ".flac": "av",
    ".avi": "av",
    ".mov": "av",
    ".mkv": "av",
    ".webm": "av",
}

_TEXT_CATEGORIES: frozenset[str] = frozenset({"text", "markdown", "code", "data"})
_EXTRACT_CATEGORIES: frozenset[str] = frozenset({"pdf", "document"})


def _get_category(ext: str) -> str:
    """
    Map a lowercased file extension to a display category.

    Mirrors llm_wiki's getFileCategory().
    Returns one of: text, markdown, image, pdf, document, data, code, av, other.
    """
    return _EXT_TO_CATEGORY.get(ext.lower(), "other")


# ── MIME type helper ──────────────────────────────────────────────────────────

_EXT_TO_MIME: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".ico": "image/x-icon",
    ".mp4": "video/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".xml": "application/xml",
    ".csv": "text/csv",
}


def _mime_for_ext(ext: str) -> str:
    """Return a Content-Type for the extension; fall back to mimetypes then octet-stream."""
    lower = ext.lower()
    if lower in _EXT_TO_MIME:
        return _EXT_TO_MIME[lower]
    guessed, _ = mimetypes.guess_type(f"file{lower}")
    return guessed or "application/octet-stream"


# ── Response models ───────────────────────────────────────────────────────────


class SourceEntry(BaseModel):
    """One file or directory entry in the sources tree."""

    path: str
    """Relative path from vault/raw/sources/ root, using forward slashes."""

    name: str
    """Basename."""

    is_dir: bool
    ext: str | None = None
    """Lowercased extension (files only; None for directories)."""

    size_bytes: int | None = None
    """File size in bytes (files only)."""

    mtime: str | None = None
    """ISO-8601 mtime (files only)."""


class SourcesListResponse(BaseModel):
    """Response for GET /sources."""

    entries: list[SourceEntry]
    total: int
    truncated: bool
    """True when the listing was capped at SOURCES_LIST_MAX (I7)."""


class SourceDerivedPage(BaseModel):
    """One derived page row for GET /sources/derived-pages and GET /sources/content."""

    id: str
    title: str | None = None
    page_type: str | None = None
    file_path: str


class SourceContentResponse(BaseModel):
    """Response for GET /sources/content."""

    path: str
    name: str
    ext: str
    size_bytes: int
    mtime: str
    category: str
    is_text: bool
    text: str | None = None
    """Extracted or raw text (present for text/code/data/markdown and extractable binaries)."""

    ingested: bool
    """True when at least one derived page exists (sources[] pivot)."""

    page_ids: list[str]
    """UUIDs of derived wiki pages (non-deleted) whose sources[] includes this file."""


class SourceDeleteResponse(BaseModel):
    """Response for DELETE /sources."""

    deleted_source: str
    pages_deleted: int


# ── DB helpers ────────────────────────────────────────────────────────────────


async def _get_derived_pages(rel_path: str) -> list[SourceDerivedPage]:
    """
    Query pages whose sources[] JSONB list references this source file (non-deleted).

    `rel_path` is relative to raw/sources/ (e.g. "chat-x.md"), but write_wiki_page stamps the
    VAULT-relative origin path ("raw/sources/chat-x.md") into sources[]. Match against a set of
    candidate forms — full vault-relative path, the bare rel_path, and the basename — so the
    linkage works regardless of which form a page recorded (K6/F13 traceability).

    Uses Python-side list filter for SQLite compat in tests; Postgres JSONB comes back as a list.
    """
    from sqlalchemy import select

    from app.db import get_session
    from app.models import Page

    # Candidate strings a page's sources[] might use to reference this file.
    candidates = {rel_path, f"raw/sources/{rel_path}", rel_path.rsplit("/", 1)[-1]}

    async with get_session() as session:
        rows = await session.execute(
            select(Page.id, Page.title, Page.page_type, Page.file_path, Page.sources).where(
                Page.deleted_at.is_(None),
            )
        )
        results: list[SourceDerivedPage] = []
        for page_id, title, page_type, file_path, sources in rows.all():
            if sources is None:
                continue
            # JSONB comes back as list in Postgres; as JSON string in SQLite — normalise
            if isinstance(sources, str):
                import json as _json

                try:
                    sources = _json.loads(sources)
                except Exception:  # noqa: BLE001, S112 — skip a row with malformed sources JSON
                    continue
            if not isinstance(sources, list):
                continue
            if candidates.intersection(sources):
                results.append(
                    SourceDerivedPage(
                        id=str(page_id),
                        title=title,
                        page_type=page_type,
                        file_path=file_path,
                    )
                )
    return results


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "",
    response_model=SourcesListResponse,
    summary="List raw-sources tree",
    description=(
        "Recursively lists all files and directories under vault/raw/sources/. "
        "Returns SourceEntry objects with path (relative to raw/sources/), name, is_dir, "
        "size_bytes, ext (lowercased), mtime (ISO-8601). "
        "Read-only (I1). "
        f"Capped at SOURCES_LIST_MAX={SOURCES_LIST_MAX} entries (I7); truncated=true if hit. "
        "Returns empty list when raw/sources/ does not exist."
    ),
    responses={
        200: {"description": "Directory listing (may be empty or truncated)"},
    },
)
async def list_sources() -> SourcesListResponse:
    """
    GET /sources — recursive listing of vault/raw/sources/.

    I1: read-only. I7: bounded at SOURCES_LIST_MAX.
    """
    sources_dir = settings.raw_sources_dir

    if not sources_dir.exists():
        return SourcesListResponse(entries=[], total=0, truncated=False)

    entries: list[SourceEntry] = []
    truncated = False

    # Walk the tree (BFS order; consistent with os.walk)
    for dirpath_str, dirnames, filenames in os.walk(sources_dir):
        dirpath = Path(dirpath_str)

        # Directories (excluding the root itself — only subdirs)
        if dirpath != sources_dir:
            rel = dirpath.relative_to(sources_dir)
            if len(entries) >= SOURCES_LIST_MAX:
                truncated = True
                logger.warning(
                    "sources listing truncated at %d entries (SOURCES_LIST_MAX); "
                    "vault/raw/sources/ has more entries.",
                    SOURCES_LIST_MAX,
                )
                break
            entries.append(
                SourceEntry(
                    path=rel.as_posix(),
                    name=dirpath.name,
                    is_dir=True,
                )
            )

        # Sort for deterministic order
        dirnames.sort()
        filenames.sort()

        for fname in filenames:
            if len(entries) >= SOURCES_LIST_MAX:
                truncated = True
                logger.warning(
                    "sources listing truncated at %d entries (SOURCES_LIST_MAX); "
                    "vault/raw/sources/ has more entries.",
                    SOURCES_LIST_MAX,
                )
                break

            fpath = dirpath / fname
            rel = fpath.relative_to(sources_dir)
            try:
                stat = fpath.stat()
                mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
                size = stat.st_size
            except OSError:
                mtime = None
                size = None

            ext = fpath.suffix.lower() if fpath.suffix else None
            entries.append(
                SourceEntry(
                    path=rel.as_posix(),
                    name=fname,
                    is_dir=False,
                    ext=ext,
                    size_bytes=size,
                    mtime=mtime,
                )
            )

        if truncated:
            break

    return SourcesListResponse(entries=entries, total=len(entries), truncated=truncated)


@router.get(
    "/content",
    response_model=SourceContentResponse,
    summary="Metadata + preview payload for one source file",
    description=(
        "Returns metadata and preview payload for a single raw source file. "
        "path must be relative to vault/raw/sources/ — resolved via resolve_under_sources() "
        "(ADR-0020 §2.2 traversal guard). 404 if path escapes or file is absent. "
        "category: one of text/markdown/image/pdf/document/data/code/av/other. "
        "For text/markdown/code/data: includes raw text (capped at SOURCES_TEXT_MAX_CHARS). "
        "For pdf/docx/pptx/xlsx: includes extracted text (via ingest/extract.py, F12). "
        "For image/av/other: no text, is_text=false (use GET /sources/raw for bytes). "
        "ingested + page_ids: derived-page linkage via sources[] JSONB pivot (K6/F13)."
    ),
    responses={
        200: {"description": "File metadata + preview payload"},
        404: {"description": "File not found or path outside raw/sources/"},
        422: {"description": "Path traversal attempt"},
    },
)
async def source_content(
    path: str = Query(..., description="Relative path under vault/raw/sources/"),
) -> SourceContentResponse:
    """
    GET /sources/content?path=<rel> — metadata + text preview for one source file.

    Path-safety: resolve_under_sources(path) (ADR-0020 §2.2).
    I7: text capped at SOURCES_TEXT_MAX_CHARS.
    """
    # Path safety — raises HTTPException(422) on traversal
    try:
        abs_path = resolve_under_sources(path)
    except HTTPException:
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}") from None

    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}")

    stat = abs_path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
    ext = abs_path.suffix.lower()
    category = _get_category(ext)

    # Normalise relative path to forward slashes (for sources[] JSONB lookup)
    sources_dir = settings.raw_sources_dir
    rel_path = abs_path.relative_to(sources_dir).as_posix()

    # Determine text extraction strategy
    text: str | None = None
    is_text: bool = False

    if category in _TEXT_CATEGORIES:
        # Read raw text (cap at I7 limit)
        is_text = True
        try:
            raw = abs_path.read_text(encoding="utf-8", errors="replace")
            if len(raw) > SOURCES_TEXT_MAX_CHARS:
                logger.info(
                    "sources/content: text preview capped at %d chars for %s",
                    SOURCES_TEXT_MAX_CHARS,
                    rel_path,
                )
                raw = raw[:SOURCES_TEXT_MAX_CHARS]
            text = raw
        except OSError as exc:
            logger.warning("sources/content: cannot read text for %s: %s", rel_path, exc)
            text = None
            is_text = False

    elif category in _EXTRACT_CATEGORIES:
        # Extract text via F12 machinery (no provider call — I6)
        try:
            from app.ingest.extract import UnsupportedFormatError, extract_text

            extracted = extract_text(abs_path)
            if len(extracted) > SOURCES_TEXT_MAX_CHARS:
                extracted = extracted[:SOURCES_TEXT_MAX_CHARS]
            text = extracted
            is_text = True
        except UnsupportedFormatError:
            is_text = False
        except Exception as exc:  # noqa: BLE001
            # Extraction may fail for malformed files; degrade gracefully
            logger.warning("sources/content: extraction failed for %s: %s", rel_path, exc)
            is_text = False
    # else: image/av/other — no text, is_text=False

    # Derived-page linkage (K6 / F13 pivot)
    derived = await _get_derived_pages(rel_path)
    page_ids = [p.id for p in derived]

    return SourceContentResponse(
        path=rel_path,
        name=abs_path.name,
        ext=ext,
        size_bytes=stat.st_size,
        mtime=mtime,
        category=category,
        is_text=is_text,
        text=text,
        ingested=len(page_ids) > 0,
        page_ids=page_ids,
    )


@router.get(
    "/raw",
    summary="Stream raw bytes of a source file for inline preview",
    description=(
        "Streams the raw bytes of a source file for inline browser preview "
        "(images, PDFs, text). "
        "path must be relative to vault/raw/sources/ — resolved via resolve_under_sources() "
        "(ADR-0020 §2.2 traversal guard). 404 if absent/outside. "
        f"Returns 413 if file exceeds SOURCES_RAW_MAX_BYTES={SOURCES_RAW_MAX_BYTES} bytes (I7). "
        "Content-Type derived from extension. Content-Disposition: inline."
    ),
    responses={
        200: {"description": "Raw file bytes with correct Content-Type"},
        404: {"description": "File not found or path outside raw/sources/"},
        413: {"description": "File exceeds SOURCES_RAW_MAX_BYTES limit"},
        422: {"description": "Path traversal attempt"},
    },
)
async def source_raw(
    path: str = Query(..., description="Relative path under vault/raw/sources/"),
) -> Response:
    """
    GET /sources/raw?path=<rel> — stream raw bytes for inline preview.

    Path-safety: resolve_under_sources(path) (ADR-0020 §2.2).
    I7: 413 if file > SOURCES_RAW_MAX_BYTES.
    """
    # Path safety
    try:
        abs_path = resolve_under_sources(path)
    except HTTPException:
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}") from None

    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}")

    # Size guard (I7)
    size = abs_path.stat().st_size
    if size > SOURCES_RAW_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"File {abs_path.name!r} exceeds SOURCES_RAW_MAX_BYTES "
                f"({size} > {SOURCES_RAW_MAX_BYTES} bytes)."
            ),
        )

    ext = abs_path.suffix.lower()
    content_type = _mime_for_ext(ext)

    # Use FileResponse for efficient streaming; Content-Disposition: inline
    return FileResponse(
        path=str(abs_path),
        media_type=content_type,
        filename=abs_path.name,
        headers={"Content-Disposition": f'inline; filename="{abs_path.name}"'},
    )


@router.get(
    "/derived-pages",
    response_model=list[SourceDerivedPage],
    summary="List wiki pages derived from a raw source",
    description=(
        "Returns the wiki pages whose sources[] JSONB contains the given source path. "
        "path must be relative to vault/raw/sources/ — resolved via resolve_under_sources(). "
        "Returns an empty list when no pages are derived from this source. "
        "Non-deleted pages only. 404 when path escapes or file is absent."
    ),
    responses={
        200: {"description": "List of derived wiki pages (may be empty)"},
        404: {"description": "Source file not found or path outside raw/sources/"},
    },
)
async def source_derived_pages(
    path: str = Query(..., description="Relative path under vault/raw/sources/"),
) -> list[SourceDerivedPage]:
    """
    GET /sources/derived-pages?path=<rel> — derived pages for a source file.

    Path-safety: resolve_under_sources(path).
    """
    # Path safety
    try:
        abs_path = resolve_under_sources(path)
    except HTTPException:
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}") from None

    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}")

    sources_dir = settings.raw_sources_dir
    rel_path = abs_path.relative_to(sources_dir).as_posix()

    return await _get_derived_pages(rel_path)


@router.delete(
    "",
    response_model=SourceDeleteResponse,
    summary="Delete a raw source file and cascade-delete its derived pages",
    description=(
        "Deletes the raw source file from disk and cascade-deletes all derived wiki pages "
        "(soft-delete pages, remove Qdrant points, clean dead [[wikilinks]], update index.md). "
        "Reuses the existing cascade-delete machinery (ops/cascade_delete.py — F13/ADR-0026). "
        "Bumps data_version + notifies GraphCache (I2). "
        "path must be relative to vault/raw/sources/ — resolved via resolve_under_sources(). "
        "404 when path escapes or file is absent. "
        "If the source has no derived pages, the raw file is still deleted. "
        "To re-ingest: POST /ingest/trigger with {\"file_path\": \"<absolute-or-relative-path>\"}."
    ),
    responses={
        200: {"description": "Source deleted; derived pages cascade-deleted"},
        404: {"description": "Source file not found or path outside raw/sources/"},
        422: {"description": "Path traversal attempt"},
    },
)
async def delete_source(
    path: str = Query(..., description="Relative path under vault/raw/sources/"),
) -> SourceDeleteResponse:
    """
    DELETE /sources?path=<rel> — delete raw source + cascade-delete derived pages (F13/I1/I2).

    Path-safety: resolve_under_sources(path) (ADR-0020 §2.2).
    Reuses cascade_delete (ops/cascade_delete.py) for each derived page.
    Deletes raw file last (after cascade completes) so a crash mid-cascade doesn't lose the file.
    Bumps data_version + notifies GraphCache EXACTLY ONCE per derived page (handled by cascade);
    if there are no derived pages, bumps once manually to signal the sources-tree change.
    """
    # Path safety
    try:
        abs_path = resolve_under_sources(path)
    except HTTPException:
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}") from None

    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}")

    sources_dir = settings.raw_sources_dir
    rel_path = abs_path.relative_to(sources_dir).as_posix()

    # Find all derived pages (non-deleted) via sources[] JSONB pivot
    derived = await _get_derived_pages(rel_path)

    pages_deleted = 0

    if derived:
        # Cascade-delete each derived page using the existing machinery (F13/ADR-0026).
        # _cascade_delete_page() wraps ops.cascade_delete.cascade_delete — which computes
        # the plan + applies it (soft-delete, Qdrant, wikilinks, index.md, raw-source file
        # cleanup, data_version bump). Since the raw SOURCE file we are deleting is NOT a
        # Page row itself (it's the raw file on disk), we call cascade on each DERIVED WIKI
        # PAGE — not on the source file's page entry.
        from app.ops.cascade_delete import PageNotFoundError

        for derived_page in derived:
            try:
                page_uuid = uuid.UUID(derived_page.id)
                await _cascade_delete_page(page_uuid)
                pages_deleted += 1
                logger.info(
                    "sources.delete: cascade-deleted page %s (derived from %s)",
                    derived_page.id,
                    rel_path,
                )
            except PageNotFoundError:
                # Already deleted in a previous iteration or by a race — skip
                logger.debug(
                    "sources.delete: page %s already deleted (skipping)", derived_page.id
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "sources.delete: cascade_delete failed for page %s: %s",
                    derived_page.id,
                    exc,
                )

    # Delete the raw source file from disk
    try:
        abs_path.unlink(missing_ok=True)
        logger.info("sources.delete: raw source file deleted: %s", rel_path)
    except Exception as exc:  # noqa: BLE001
        logger.error("sources.delete: failed to delete raw file %s: %s", rel_path, exc)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete source file: {exc}",
        ) from exc

    # If no derived pages existed, still bump data_version + notify graph cache
    # so the frontend's sources-tree and graph are kept consistent (I2).
    if not derived:
        await _bump_version_no_derived()

    return SourceDeleteResponse(deleted_source=rel_path, pages_deleted=pages_deleted)


async def _bump_version_no_derived() -> None:
    """
    Bump data_version by 1 when a source with no derived pages is deleted (I2).

    Mirrors cascade_delete._bump_version_and_notify() but for the no-derived-pages case.
    We still need to signal the frontend that the sources tree changed.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select, update

    from app.db import get_session
    from app.models import VaultState

    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            state = VaultState(vault_id=settings.vault_id, data_version=1)
            state.updated_at = datetime.now(UTC)
            session.add(state)
            new_version = 1
        else:
            await session.execute(
                update(VaultState)
                .where(VaultState.vault_id == settings.vault_id)
                .values(
                    data_version=VaultState.data_version + 1,
                    updated_at=datetime.now(UTC),
                )
            )
            result = await session.execute(
                select(VaultState.data_version).where(VaultState.vault_id == settings.vault_id)
            )
            new_version = result.scalar_one_or_none() or 0

    # Notify GraphCache (debounced FA2 recompute — I2)
    try:
        from app.main import _graph_cache

        if _graph_cache is not None:
            _graph_cache.notify_bump(new_version)
    except Exception:  # noqa: BLE001
        logger.debug("sources._bump_version_no_derived: graph cache notify skipped (not ready)")
