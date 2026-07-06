"""
Sources view — raw-source file browser + preview (nashsu/llm_wiki Sources tab parity).

GET  /sources                    — listing of vault/raw/sources/ OR vault/wiki/ (I7-bounded)
GET  /sources/content?path=<rel> — metadata + preview for one file (sources or wiki root)
GET  /sources/raw?path=<rel>     — stream raw bytes for inline preview (sources or wiki root)
GET  /sources/derived-pages?path=<rel> — pages derived from a source (sources root only)
DELETE /sources?path=<rel>       — delete raw file + cascade-delete pages (sources root only)
POST /sources/ingest-all         — index pre-existing files in raw/sources/ (sources only)
GET  /sources/ingest-all/status  — whether an ingest-all scan is running + progress

root param (GET /sources, /sources/content, /sources/raw):
  root="sources" (default) — operates on vault/raw/sources/ (unchanged behaviour)
  root="wiki"              — operates on vault/wiki/ (read-only; excludes dotfiles/.obsidian)

Path-safety: EVERY client-supplied path is routed through _resolve_under_dir() which
performs a containment check against the chosen root (ADR-0020 §2.2). 404 on escape.
root=wiki stays inside wiki/; root=sources stays inside raw/sources/.

Invariants honoured:
  I1  — listing/content/raw are read-only; delete reuses incremental cascade (no rescan);
        ingest-all reuses the incremental gate in ingest_file (mtime-then-hash), never re-scans.
  I2  — DELETE bumps data_version + notifies _graph_cache via cascade machinery.
  I6  — zero InferenceProvider calls in this module; ingest_file handles provider routing.
  I7  — bounded listing (SOURCES_LIST_MAX entries), bounded text preview (SOURCES_TEXT_MAX_CHARS),
        bounded raw bytes (SOURCES_RAW_MAX_BYTES), hard 413 for oversize raw;
        ingest-all capped at SOURCES_INGEST_ALL_MAX files; BOUNDED-concurrent execution
        (SOURCES_INGEST_ALL_CONCURRENCY workers, default 3 — same cap as Deep Research;
        never unbounded).
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from app.config import settings
from app.upload import resolve_under_sources

logger = logging.getLogger(__name__)


# ── Path-safety helper ────────────────────────────────────────────────────────


def _resolve_under_dir(base_dir: Path, name: str) -> Path:
    """
    Resolve *name* to an absolute path under *base_dir* with a containment check.

    Generalizes resolve_under_sources() from app.upload so the same traversal-guard
    semantics apply to any root directory (raw/sources/ or wiki/).

    Raises HTTPException(422) if the resolved path escapes *base_dir* (traversal,
    absolute path, or symlink pointing outside). Also rejects the empty string.
    Symlinks are followed before the containment check (resolve()) — if a symlink
    points outside the root it is rejected exactly like a traversal attempt.

    Contract: identical to resolve_under_sources() — only the hardcoded root changes.
    """
    if not name:
        raise HTTPException(status_code=422, detail="Path must not be empty.")

    root = base_dir.resolve()
    dst = (root / name).resolve()

    # resolved path MUST start with root/ (trailing sep ensures prefix-safety)
    if dst != root and not str(dst).startswith(str(root) + "/"):
        raise HTTPException(
            status_code=422, detail="Filename is empty or unsafe after sanitization."
        )

    return dst


# ── Wiki listing: hidden-entry filter ────────────────────────────────────────
# Names starting with '.' (dotfiles and .obsidian) are excluded from the wiki
# listing to keep the tree clean and avoid exposing Obsidian internals (I5).


def _is_hidden(name: str) -> bool:
    """Return True if *name* should be excluded from the wiki listing (dotfile/hidden dir)."""
    return name.startswith(".")


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

# Maximum files that may be cascade-deleted in a single directory DELETE (I7 — S2/B3b).
# When the directory contains more files than this cap, DELETE returns 409 with a clear
# message. The user must delete subdirectories or individual files to stay under the cap.
# Default 500 — generous enough for normal use; tight enough to prevent runaway cascades.
# Env var: SOURCES_DELETE_MAX_FILES
SOURCES_DELETE_MAX_FILES: int = int(os.environ.get("SOURCES_DELETE_MAX_FILES", "500"))

# Maximum text preview characters for text-like files (I7 text cap)
SOURCES_TEXT_MAX_CHARS: int = int(os.environ.get("SOURCES_TEXT_MAX_CHARS", str(200_000)))

# Maximum raw bytes streamed by GET /sources/raw (I7 size guard)
SOURCES_RAW_MAX_BYTES: int = int(os.environ.get("SOURCES_RAW_MAX_BYTES", str(50 * 1024 * 1024)))

# Maximum files indexed by POST /sources/ingest-all (I7 — explicit user action; bounded scan).
# Reuses import_scan_max_files (200) by default; override via SOURCES_INGEST_ALL_MAX.
SOURCES_INGEST_ALL_MAX: int = int(
    os.environ.get("SOURCES_INGEST_ALL_MAX", str(settings.import_scan_max_files))
)

# Number of files ingested CONCURRENTLY by the ingest-all driver (I7 — BOUNDED, never unbounded).
# Default 3 mirrors the Deep Research concurrency cap. Clamped to >=1. Set to 1 for serial.
SOURCES_INGEST_ALL_CONCURRENCY: int = max(
    1, int(os.environ.get("SOURCES_INGEST_ALL_CONCURRENCY", "3"))
)

# ── Ingest-all single-flight state ────────────────────────────────────────────

# Module-level flag/task handle for single-flight enforcement.
# Only ONE ingest-all driver may run at a time across the process.
_ingest_all_running: bool = False
_ingest_all_done: int = 0
_ingest_all_total: int = 0


def get_ingest_all_progress() -> dict[str, int | bool]:
    """
    Return the current ingest-all batch progress (read-only snapshot of module state).

    Consumed by GET /ingest/queue to surface a batch counter ("done/total") + a batch ETA
    in the activity panel, so the user sees whole-batch progress, not just the 1-at-a-time
    queue view. No I/O — pure in-memory read.
    """
    return {
        "running": _ingest_all_running,
        "done": _ingest_all_done,
        "total": _ingest_all_total,
    }


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
    """
    Response for DELETE /sources.

    Single-file delete:  deleted_source=<rel_path>, files_deleted=1, pages_cascaded=N,
                         pages_deleted=N (backward-compat alias; always equals pages_cascaded).
    Directory delete:    deleted_source=<rel_dir>,  files_deleted=K, pages_cascaded=M,
                         pages_deleted=M.
    """

    deleted_source: str
    """Relative path (from raw/sources/) of the deleted file or directory."""

    files_deleted: int = 1
    """Number of raw source files removed from disk (1 for single-file delete; K for dir)."""

    pages_cascaded: int = 0
    """Total derived wiki pages cascade-deleted across all files."""

    pages_deleted: int = 0
    """Backward-compat alias for pages_cascaded. Always equals pages_cascaded."""


class IngestAllResponse(BaseModel):
    """Response for POST /sources/ingest-all."""

    started: bool
    """True when the driver task was started (or was already running → 409)."""

    candidate_files: int
    """Number of supported files found (post-cap). 0 → started=false."""


class IngestAllStatusResponse(BaseModel):
    """Response for GET /sources/ingest-all/status."""

    running: bool
    done: int
    """Files processed so far by the current (or most-recent) driver."""

    total: int
    """Total candidate files queued in the current (or most-recent) driver."""


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
    summary="List raw-sources or wiki tree",
    description=(
        "Recursively lists all files and directories under the chosen root. "
        "root='sources' (default): vault/raw/sources/ — the original behaviour. "
        "root='wiki': vault/wiki/ — read-only wiki file tree; hidden entries (dotfiles, "
        ".obsidian) are excluded automatically (I5). "
        "Returns SourceEntry objects with path (relative to the chosen root), name, is_dir, "
        "size_bytes, ext (lowercased), mtime (ISO-8601). "
        "Read-only (I1). "
        f"Capped at SOURCES_LIST_MAX={SOURCES_LIST_MAX} entries (I7); truncated=true if hit. "
        "Returns empty list when the root directory does not exist."
    ),
    responses={
        200: {"description": "Directory listing (may be empty or truncated)"},
    },
)
async def list_sources(
    root: Literal["sources", "wiki"] = Query(
        default="sources",
        description=(
            "Which directory tree to list. "
            "'sources' (default) = vault/raw/sources/; "
            "'wiki' = vault/wiki/ (read-only; hidden dirs excluded)."
        ),
    ),
) -> SourcesListResponse:
    """
    GET /sources[?root=sources|wiki] — recursive listing of the chosen root.

    root='sources' (default): vault/raw/sources/ — unchanged existing behaviour.
    root='wiki': vault/wiki/ — read-only; hidden entries (dotfiles, .obsidian) excluded (I5).
    I1: read-only. I7: bounded at SOURCES_LIST_MAX.
    """
    if root == "wiki":
        base_dir = settings.wiki_dir
        is_wiki = True
    else:
        base_dir = settings.raw_sources_dir
        is_wiki = False

    if not base_dir.exists():
        return SourcesListResponse(entries=[], total=0, truncated=False)

    entries: list[SourceEntry] = []
    truncated = False

    # Walk the tree (BFS order; consistent with os.walk)
    for dirpath_str, dirnames, filenames in os.walk(base_dir):
        dirpath = Path(dirpath_str)

        # For wiki root: prune hidden directories so os.walk does not descend into them.
        if is_wiki:
            dirnames[:] = sorted(d for d in dirnames if not _is_hidden(d))
            filenames = sorted(f for f in filenames if not _is_hidden(f))
        else:
            dirnames.sort()
            filenames.sort()

        # Directories (excluding the root itself — only subdirs)
        if dirpath != base_dir:
            rel = dirpath.relative_to(base_dir)
            if len(entries) >= SOURCES_LIST_MAX:
                truncated = True
                logger.warning(
                    "sources listing truncated at %d entries (SOURCES_LIST_MAX); "
                    "%s has more entries.",
                    SOURCES_LIST_MAX,
                    base_dir,
                )
                break
            entries.append(
                SourceEntry(
                    path=rel.as_posix(),
                    name=dirpath.name,
                    is_dir=True,
                )
            )

        for fname in filenames:
            if len(entries) >= SOURCES_LIST_MAX:
                truncated = True
                logger.warning(
                    "sources listing truncated at %d entries (SOURCES_LIST_MAX); "
                    "%s has more entries.",
                    SOURCES_LIST_MAX,
                    base_dir,
                )
                break

            fpath = dirpath / fname
            rel = fpath.relative_to(base_dir)
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
    summary="Metadata + preview payload for one source or wiki file",
    description=(
        "Returns metadata and preview payload for a single file. "
        "root='sources' (default): path relative to vault/raw/sources/. "
        "root='wiki': path relative to vault/wiki/ (read-only; no derived-page pivot). "
        "path resolved via containment check (ADR-0020 §2.2 traversal guard). "
        "404 if path escapes, is hidden (root=wiki), or file is absent. "
        "category: one of text/markdown/image/pdf/document/data/code/av/other. "
        "For text/markdown/code/data: includes raw text (capped at SOURCES_TEXT_MAX_CHARS). "
        "For pdf/docx/pptx/xlsx: includes extracted text (via ingest/extract.py, F12). "
        "For image/av/other: no text, is_text=false (use GET /sources/raw for bytes). "
        "ingested + page_ids: derived-page linkage via sources[] JSONB pivot (K6/F13); "
        "always ingested=false + page_ids=[] when root='wiki'."
    ),
    responses={
        200: {"description": "File metadata + preview payload"},
        404: {"description": "File not found or path outside chosen root"},
        422: {"description": "Path traversal attempt"},
    },
)
async def source_content(
    path: str = Query(..., description="Relative path under the chosen root directory"),
    root: Literal["sources", "wiki"] = Query(
        default="sources",
        description=(
            "Which directory to resolve path against. "
            "'sources' (default) = vault/raw/sources/; "
            "'wiki' = vault/wiki/ (read-only; hidden files rejected)."
        ),
    ),
) -> SourceContentResponse:
    """
    GET /sources/content?path=<rel>[&root=sources|wiki] — metadata + text preview.

    root='sources' (default): path relative to vault/raw/sources/; derives page linkage.
    root='wiki': path relative to vault/wiki/; read-only; ingested=False, page_ids=[].
    Path-safety: _resolve_under_dir(base_dir, path) — containment-checked (ADR-0020 §2.2).
    I7: text capped at SOURCES_TEXT_MAX_CHARS.
    """
    if root == "wiki":
        base_dir = settings.wiki_dir
    else:
        base_dir = settings.raw_sources_dir

    # Path safety — raises HTTPException(422) on traversal
    try:
        abs_path = _resolve_under_dir(base_dir, path)
    except HTTPException:
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}") from None

    # For wiki root: also reject hidden files/dirs (dotfiles, .obsidian, etc.)
    if root == "wiki":
        # Check every path component for hidden names
        try:
            rel_parts = abs_path.relative_to(base_dir.resolve()).parts
        except ValueError:
            raise HTTPException(status_code=404, detail=f"Source not found: {path!r}") from None
        if any(_is_hidden(part) for part in rel_parts):
            raise HTTPException(status_code=404, detail=f"Source not found: {path!r}")

    if not abs_path.exists() or not abs_path.is_file():
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}")

    stat = abs_path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
    ext = abs_path.suffix.lower()
    category = _get_category(ext)

    # Normalise relative path to forward slashes
    rel_path = abs_path.relative_to(base_dir.resolve()).as_posix()

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

    # Derived-page linkage (K6 / F13 pivot) — sources root only.
    # Wiki files are generated output; the sources[] pivot is not meaningful for them.
    if root == "sources":
        derived = await _get_derived_pages(rel_path)
        page_ids = [p.id for p in derived]
    else:
        page_ids = []

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
    summary="Stream raw bytes of a source or wiki file for inline preview",
    description=(
        "Streams the raw bytes of a file for inline browser preview "
        "(images, PDFs, text). "
        "root='sources' (default): path relative to vault/raw/sources/. "
        "root='wiki': path relative to vault/wiki/ (read-only; hidden files rejected). "
        "path resolved via containment check (ADR-0020 §2.2 traversal guard). "
        "404 if absent/outside chosen root. "
        f"Returns 413 if file exceeds SOURCES_RAW_MAX_BYTES={SOURCES_RAW_MAX_BYTES} bytes (I7). "
        "Content-Type derived from extension. Content-Disposition: inline."
    ),
    responses={
        200: {"description": "Raw file bytes with correct Content-Type"},
        404: {"description": "File not found or path outside chosen root"},
        413: {"description": "File exceeds SOURCES_RAW_MAX_BYTES limit"},
        422: {"description": "Path traversal attempt"},
    },
)
async def source_raw(
    path: str = Query(..., description="Relative path under the chosen root directory"),
    root: Literal["sources", "wiki"] = Query(
        default="sources",
        description=(
            "Which directory to resolve path against. "
            "'sources' (default) = vault/raw/sources/; "
            "'wiki' = vault/wiki/ (read-only; hidden files rejected)."
        ),
    ),
) -> Response:
    """
    GET /sources/raw?path=<rel>[&root=sources|wiki] — stream raw bytes for inline preview.

    root='sources' (default): vault/raw/sources/ — unchanged existing behaviour.
    root='wiki': vault/wiki/ — read-only; hidden files rejected (I5).
    Path-safety: _resolve_under_dir(base_dir, path) — containment-checked (ADR-0020 §2.2).
    I7: 413 if file > SOURCES_RAW_MAX_BYTES.
    """
    if root == "wiki":
        base_dir = settings.wiki_dir
    else:
        base_dir = settings.raw_sources_dir

    # Path safety
    try:
        abs_path = _resolve_under_dir(base_dir, path)
    except HTTPException:
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}") from None

    # For wiki root: reject hidden files/dirs
    if root == "wiki":
        try:
            rel_parts = abs_path.relative_to(base_dir.resolve()).parts
        except ValueError:
            raise HTTPException(status_code=404, detail=f"Source not found: {path!r}") from None
        if any(_is_hidden(part) for part in rel_parts):
            raise HTTPException(status_code=404, detail=f"Source not found: {path!r}")

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
    summary="Delete a raw source file or directory and cascade-delete derived pages",
    description=(
        "Deletes the raw source file or directory (S2, B3b) from disk and cascade-deletes "
        "all derived wiki pages for each contained file "
        "(soft-delete pages, remove Qdrant points, clean dead [[wikilinks]], update index.md). "
        "Reuses the existing cascade-delete machinery (ops/cascade_delete.py — F13/ADR-0026). "
        "Bumps data_version + notifies GraphCache (I2). "
        "path must be relative to vault/raw/sources/ — resolved via resolve_under_sources(). "
        "404 when path escapes or target does not exist. "
        "\n\nFILE delete (is_dir=False): existing behaviour unchanged — cascade then remove file. "
        "If the source has no derived pages, the raw file is still deleted. "
        "Returns {deleted_source, files_deleted:1, pages_cascaded:N}. "
        "\n\nDIRECTORY delete (is_dir=True, S2): enumerates all files in the subtree, "
        "runs the SAME per-file cascade for each, then removes files + empty dirs from disk. "
        f"Bounded at SOURCES_DELETE_MAX_FILES (env, default {SOURCES_DELETE_MAX_FILES}); "
        "if the subtree contains more files, returns 409 with a clear message (I7 — no "
        "unbounded cascade). Returns {deleted_source, files_deleted:K, pages_cascaded:M}. "
        "\n\nPath-safety: resolve_under_sources() for all paths (ADR-0020 §2.2)."
    ),
    responses={
        200: {"description": "Source deleted; derived pages cascade-deleted"},
        404: {"description": "Source file or directory not found or path outside raw/sources/"},
        409: {"description": "Directory exceeds SOURCES_DELETE_MAX_FILES limit (I7)"},
        422: {"description": "Path traversal attempt"},
    },
)
async def delete_source(
    path: str = Query(..., description="Relative path under vault/raw/sources/"),
) -> SourceDeleteResponse:
    """
    DELETE /sources?path=<rel> — delete raw source file or directory (S2, B3b).

    Dispatches on is_dir:
    - File path → _delete_single_source_file (existing behaviour, unchanged).
    - Dir  path → _delete_source_directory   (new; S2; bounded by SOURCES_DELETE_MAX_FILES).

    Path-safety: resolve_under_sources(path) (ADR-0020 §2.2).
    I1: cascade reuses the incremental cascade machinery (ops/cascade_delete.py).
    I2: data_version bumped (once per file, or once for the no-derived case).
    I7: directory delete capped at SOURCES_DELETE_MAX_FILES → 409 beyond.
    """
    # Path safety
    try:
        abs_path = resolve_under_sources(path)
    except HTTPException:
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}") from None

    if not abs_path.exists():
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}")

    sources_dir = settings.raw_sources_dir

    if abs_path.is_dir():
        return await _delete_source_directory(abs_path, sources_dir, path)
    elif abs_path.is_file():
        return await _delete_single_source_file(abs_path, sources_dir)
    else:
        raise HTTPException(status_code=404, detail=f"Source not found: {path!r}")


async def _delete_single_source_file(
    abs_path: Path,
    sources_dir: Path,
) -> SourceDeleteResponse:
    """
    Delete a single raw source file and cascade-delete its derived wiki pages.

    This is the ORIGINAL per-file delete logic, extracted into a helper so it can
    be reused by _delete_source_directory. Behaviour is byte-for-byte identical to
    the pre-S2 implementation (no invariant changes).
    """
    rel_path = abs_path.relative_to(sources_dir).as_posix()

    # Find all derived pages (non-deleted) via sources[] JSONB pivot
    derived = await _get_derived_pages(rel_path)

    pages_cascaded = 0

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
                pages_cascaded += 1
                logger.info(
                    "sources.delete: cascade-deleted page %s (derived from %s)",
                    derived_page.id,
                    rel_path,
                )
            except PageNotFoundError:
                # Already deleted in a previous iteration or by a race — skip
                logger.debug("sources.delete: page %s already deleted (skipping)", derived_page.id)
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

    return SourceDeleteResponse(
        deleted_source=rel_path,
        files_deleted=1,
        pages_cascaded=pages_cascaded,
        pages_deleted=pages_cascaded,
    )


async def _delete_source_directory(
    abs_dir: Path,
    sources_dir: Path,
    client_path: str,
) -> SourceDeleteResponse:
    """
    S2 (B3b) — Delete a directory subtree from raw/sources/ with bounded cascade.

    Steps:
    1. Enumerate ALL files in the subtree (os.walk, alphabetic).
    2. If count > SOURCES_DELETE_MAX_FILES → 409 (I7 — no unbounded cascade).
    3. For each file: run the SAME cascade as _delete_single_source_file.
       (per-file derived-page lookup → cascade each → unlink file).
    4. Remove now-empty subdirectories bottom-up (rmdir; no-op if non-empty due to race).
    5. Bump data_version once if no files were processed (empty-dir case; I2).
    6. Return {deleted_source, files_deleted, pages_cascaded}.

    Path-safety: abs_dir is already resolve_under_sources-checked by the caller.
    I7: capped at SOURCES_DELETE_MAX_FILES → 409 beyond the cap.
    I1: reuses _cascade_delete_page (same seam as per-file delete; no new rescan).
    """
    rel_dir = abs_dir.relative_to(sources_dir).as_posix()

    # ── Step 1: Enumerate files (I7 — count before committing to cascade) ──────
    all_files: list[Path] = []
    dirs_seen: list[Path] = []  # in walk order (for bottom-up rmdir later)

    for dirpath_str, dirnames, filenames in os.walk(abs_dir):
        dirnames.sort()
        filenames.sort()
        dp = Path(dirpath_str)
        if dp != abs_dir:
            dirs_seen.append(dp)
        for fname in filenames:
            fpath = dp / fname
            if len(all_files) > SOURCES_DELETE_MAX_FILES:
                # Exceeded cap — fail BEFORE touching anything on disk
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Directory {client_path!r} contains more than "
                        f"{SOURCES_DELETE_MAX_FILES} files "
                        f"(SOURCES_DELETE_MAX_FILES). "
                        "Delete subdirectories individually or raise the env cap."
                    ),
                )
            all_files.append(fpath)

    # ── Step 2: Final cap check (accounts for exactly-at-limit edge) ───────────
    if len(all_files) > SOURCES_DELETE_MAX_FILES:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Directory {client_path!r} contains more than "
                f"{SOURCES_DELETE_MAX_FILES} files "
                f"(SOURCES_DELETE_MAX_FILES). "
                "Delete subdirectories individually or raise the env cap."
            ),
        )

    logger.info(
        "sources.delete_dir: starting cascade for %d files in %s",
        len(all_files),
        rel_dir,
    )

    # ── Step 3: Cascade each file (reuses existing per-file seam) ─────────────
    from app.ops.cascade_delete import PageNotFoundError

    total_pages_cascaded = 0
    files_deleted = 0

    for fpath in all_files:
        file_rel = fpath.relative_to(sources_dir).as_posix()
        derived = await _get_derived_pages(file_rel)

        if derived:
            for derived_page in derived:
                try:
                    page_uuid = uuid.UUID(derived_page.id)
                    await _cascade_delete_page(page_uuid)
                    total_pages_cascaded += 1
                    logger.info(
                        "sources.delete_dir: cascade-deleted page %s (derived from %s)",
                        derived_page.id,
                        file_rel,
                    )
                except PageNotFoundError:
                    logger.debug(
                        "sources.delete_dir: page %s already deleted (skipping)", derived_page.id
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "sources.delete_dir: cascade_delete failed for page %s: %s",
                        derived_page.id,
                        exc,
                    )

        # Remove the raw file from disk after cascade completes for this file
        try:
            fpath.unlink(missing_ok=True)
            files_deleted += 1
            logger.debug("sources.delete_dir: removed file %s", file_rel)
        except Exception as exc:  # noqa: BLE001
            logger.error("sources.delete_dir: failed to unlink %s: %s", file_rel, exc)

        # If this file had no derived pages, bump data_version (mirrors single-file logic)
        if not derived:
            await _bump_version_no_derived()

    # ── Step 4: Remove now-empty subdirectories bottom-up ──────────────────────
    # dirs_seen is in top-down order from os.walk; reverse it for bottom-up rmdir.
    for subdir in reversed(dirs_seen):
        try:
            subdir.rmdir()
            logger.debug("sources.delete_dir: removed empty subdir %s", subdir)
        except OSError:
            # Non-empty (race condition or unlinked file was skipped) — leave it
            logger.debug("sources.delete_dir: subdir not empty (skipping rmdir) %s", subdir)

    # Remove the top-level directory itself (only if now empty)
    try:
        abs_dir.rmdir()
        logger.info("sources.delete_dir: removed directory %s", rel_dir)
    except OSError:
        logger.debug("sources.delete_dir: directory not empty after cleanup: %s", rel_dir)

    # ── Step 5: bump data_version once if directory was empty (I2) ─────────────
    if not all_files:
        await _bump_version_no_derived()

    logger.info(
        "sources.delete_dir: completed — files_deleted=%d pages_cascaded=%d path=%s",
        files_deleted,
        total_pages_cascaded,
        rel_dir,
    )

    return SourceDeleteResponse(
        deleted_source=rel_dir,
        files_deleted=files_deleted,
        pages_cascaded=total_pages_cascaded,
        pages_deleted=total_pages_cascaded,
    )


@router.post(
    "/ingest-all",
    response_model=IngestAllResponse,
    status_code=202,
    summary="Index all pre-existing files in vault/raw/sources/",
    description=(
        "Enumerates vault/raw/sources/ recursively and starts a background driver that "
        "calls ingest_file() on each supported file with a BOUNDED worker pool "
        "(SOURCES_INGEST_ALL_CONCURRENCY, default 3 — same cap as Deep Research; I7, never "
        "unbounded). "
        "Each file's mtime-then-hash gate skips already-indexed files cheaply (idempotent; I1). "
        "Supported extensions: same allow-list as the watcher (_UPLOAD_ACCEPTED from upload.py). "
        f"Capped at SOURCES_INGEST_ALL_MAX (default={SOURCES_INGEST_ALL_MAX}) files (I7); "
        "truncation is logged. "
        "Returns 202 immediately — do NOT block the caller. "
        "Returns 409 if a scan is already running (single-flight guard). "
        "I6: this endpoint makes zero InferenceProvider calls; ingest_file routes them. "
        "ADR-0006: this is the explicit user-initiated path for pre-existing files, "
        "NOT a startup rescan (I1 is not violated). "
        "Poll GET /sources/ingest-all/status or GET /ingest/queue to track progress."
    ),
    responses={
        202: {"description": "Driver started (or nothing to do if candidate_files=0)"},
        409: {"description": "An ingest-all scan is already running"},
    },
)
async def ingest_all() -> IngestAllResponse:
    """
    POST /sources/ingest-all — index all pre-existing files in vault/raw/sources/.

    I1: reuses ingest_file incremental gate (mtime-then-hash) — idempotent, no rescan.
    I6: zero InferenceProvider calls in this layer; ingest_file handles routing.
    I7: bounded by SOURCES_INGEST_ALL_MAX; bounded-concurrent driver
        (SOURCES_INGEST_ALL_CONCURRENCY workers, default 3 — never unbounded).
    ADR-0006: explicit user action — NOT the startup-rescan anti-pattern.
    Single-flight: 409 if already running.
    """
    global _ingest_all_running, _ingest_all_done, _ingest_all_total

    if _ingest_all_running:
        raise HTTPException(status_code=409, detail="ingest-all already running")

    # Collect candidate files: recursive walk, supported extensions, bounded by cap.
    sources_dir = settings.raw_sources_dir
    candidates: list[Path] = _collect_ingest_all_candidates(sources_dir, SOURCES_INGEST_ALL_MAX)

    if not candidates:
        return IngestAllResponse(started=False, candidate_files=0)

    # Arm the counters before creating the task so status is correct immediately.
    _ingest_all_running = True
    _ingest_all_done = 0
    _ingest_all_total = len(candidates)

    # Start the serial driver as a single fire-and-forget asyncio.Task.
    asyncio.create_task(_ingest_all_driver(candidates))

    return IngestAllResponse(started=True, candidate_files=len(candidates))


@router.get(
    "/ingest-all/status",
    response_model=IngestAllStatusResponse,
    summary="Status of a running (or most-recent) ingest-all scan",
    description=(
        "Returns running=true/false and the done/total counts for the current or most-recent "
        "POST /sources/ingest-all driver. "
        "When running=false and done=total=0 no scan has been started this session. "
        "Cheap read-only poll (no DB I/O)."
    ),
)
async def ingest_all_status() -> IngestAllStatusResponse:
    """GET /sources/ingest-all/status — running flag + progress counters."""
    return IngestAllStatusResponse(
        running=_ingest_all_running,
        done=_ingest_all_done,
        total=_ingest_all_total,
    )


# ── Ingest-all helpers ────────────────────────────────────────────────────────


def _is_ingest_all_supported(path: Path) -> bool:
    """
    Return True if *path* should be included in an ingest-all scan.

    Uses _UPLOAD_ACCEPTED from upload.py as the single source of truth for
    supported extensions — the same set the watcher filter and upload endpoint use
    (ADR-0025 §4.2). This includes text (.md/.txt/.markdown), binary extractables
    (.pdf/.docx/.pptx/.xlsx), and placeholder formats (.png/.jpg/...).
    """
    from app.upload import _UPLOAD_ACCEPTED

    return path.suffix.lower() in _UPLOAD_ACCEPTED


def _collect_ingest_all_candidates(sources_dir: Path, max_files: int) -> list[Path]:
    """
    Walk *sources_dir* recursively and return absolute Paths of supported files.

    Bounded at *max_files* (I7). Logs a WARNING when truncated.
    Returns an empty list if *sources_dir* does not exist.
    """
    if not sources_dir.exists():
        return []

    candidates: list[Path] = []
    truncated = False

    for dirpath_str, dirnames, filenames in os.walk(sources_dir):
        dirnames.sort()
        filenames.sort()
        for fname in filenames:
            if len(candidates) >= max_files:
                truncated = True
                break
            fpath = Path(dirpath_str) / fname
            if _is_ingest_all_supported(fpath):
                candidates.append(fpath)
        if truncated:
            break

    if truncated:
        logger.warning(
            "ingest-all: candidate list truncated at %d files (SOURCES_INGEST_ALL_MAX). "
            "vault/raw/sources/ contains more supported files — run again to index the rest.",
            max_files,
        )

    return candidates


async def _ingest_all_driver(candidates: list[Path]) -> None:
    """
    Bounded-concurrency driver: ingest_file() over all candidates with a fixed worker pool.

    CRITICAL INVARIANT (I7): concurrency is BOUNDED at SOURCES_INGEST_ALL_CONCURRENCY
    (default 3, same cap as Deep Research) — never unbounded. A fixed set of workers pulls
    from a shared index, so at most N files are in flight at once regardless of batch size.
    This keeps the original guard's intent (no resource/cost explosion) while cutting wall-clock
    ~Nx. Concurrency is safe: per-page DB writes are keyed by (vault_id, file_path) — distinct
    per source; bump_version uses an atomic SQL increment; append_log is single-line append-mode;
    index.md/overview.md regen rebuild full valid content (last-writer-wins, self-healing).

    Each call goes through the mtime-then-hash gate — unchanged files are skipped cheaply.
    A per-file try/except ensures one bad file does not abort the whole run.
    `_ingest_all_done` is incremented from the single asyncio event loop (no await between
    read and write) so the counter stays consistent without an explicit lock.
    The module-level single-flight flag is cleared in the finally block.
    """
    global _ingest_all_running, _ingest_all_done

    workers = min(SOURCES_INGEST_ALL_CONCURRENCY, len(candidates))

    try:
        from app.ingest.orchestrator import ingest_file

        # Shared cursor into the candidate list; each worker claims the next index.
        # Safe without a lock: index mutation happens between awaits on a single event loop.
        cursor = 0

        async def _worker(worker_id: int) -> None:
            global _ingest_all_done
            nonlocal cursor
            while True:
                if cursor >= len(candidates):
                    return
                idx = cursor
                cursor += 1
                path = candidates[idx]
                try:
                    result = await ingest_file(str(path))
                    logger.info(
                        "ingest-all[w%d]: %s path=%s",
                        worker_id,
                        result.status,
                        path,
                    )
                except FileNotFoundError:
                    logger.debug("ingest-all: file vanished before ingest %s — skipping", path)
                except Exception:  # noqa: BLE001
                    logger.exception("ingest-all: ingest error for %s — continuing", path)
                finally:
                    _ingest_all_done += 1

        await asyncio.gather(*(_worker(i) for i in range(workers)))

    finally:
        _ingest_all_running = False
        logger.info(
            "ingest-all: driver finished — processed %d / %d files (concurrency=%d)",
            _ingest_all_done,
            len(candidates),
            workers,
        )


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
