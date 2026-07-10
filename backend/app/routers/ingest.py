"""
Per-domain APIRouter: /ingest/* endpoints.

Covers:
  POST /ingest/trigger              — sync trigger ingest by path
  POST /ingest/upload               — multipart file upload
  POST /ingest/convert-marker       — Marker PDF→markdown conversion
  GET  /ingest/marker-health        — Marker service health
  POST /ingest/from-text            — inline text ingest
  GET  /ingest/runs                 — paginated run history
  GET  /ingest/queue                — live queue with ETA
  POST /ingest/runs/{id}/cancel     — cancel a queued/running run
  POST /ingest/runs/{id}/retry      — retry a failed run
  POST /ingest/queue/pause          — pause the queue
  POST /ingest/queue/resume         — resume the queue
"""

from __future__ import annotations

import logging
import re as _re
import sys as _sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.config import settings
from app.config_overrides import effective_float, effective_str
from app.ingest.orchestrator import IngestResult, ingest_file
from app.models import IngestRun
from app.rate_limit import rate_limit
from app.upload import _SEP_RE, resolve_under_sources, safe_source_name

logger = logging.getLogger(__name__)

router = APIRouter()


class _LazyMain:
    """Lazy proxy to app.main; enables test patches via app.main.* to propagate."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(_sys.modules["app.main"], name)

    def __setattr__(self, name: str, value: object) -> None:
        setattr(_sys.modules["app.main"], name, value)


_m = _LazyMain()


class IngestTriggerRequest(BaseModel):
    file_path: str = Field(..., description="Relative path under vault/raw/sources/")


class IngestTriggerResponse(BaseModel):
    """
    Typed 202 body for POST /ingest/trigger (AC-D4u — task_id appears in OpenAPI schema).

    task_id is None in v0.2 (synchronous path); v0.3 fills it with a real async task UUID.
    status: "completed" | "skipped" (I1 fast-path) | "queued"/"running" (async, v0.3+).
    """

    task_id: uuid.UUID | None = Field(
        default=None,
        description="Async task UUID (None in v0.2 synchronous mode; filled in v0.3+)",
    )
    status: str = Field(
        ...,
        description='"completed" or "skipped" (I1 mtime/hash fast-path)',
    )
    page_id: uuid.UUID = Field(..., description="UUID of the ingested page row")

    model_config = {
        "json_schema_extra": {
            "example": {
                "task_id": None,
                "status": "completed",
                "page_id": "00000000-0000-0000-0000-000000000001",
            }
        }
    }


# ── Ingest run Pydantic models (ADR-0018 §7, AC-BE-IR-1) ──────────────────────


class IngestRunResponse(BaseModel):
    """
    API response shape for one ingest_runs row (ADR-0018 §7, AC-BE-IR-1).

    Column aliases (no DB rename — ADR-0018 §7 decision):
      max_iter_used  → iterations_used
      finished_at    → completed_at
    total_cost_usd serialised as a float; frontend formats to exactly 4dp (I7).
    """

    id: uuid.UUID
    vault_id: str
    status: str = Field(description="running | completed | failed | converged_false (ADR-0018 §7)")
    provider_type: str = Field(description="local | api | cli")
    pages_created: int = Field(description="Wiki pages persisted during this run")
    iterations_used: int = Field(
        description="Iterations consumed (aliases max_iter_used; 0 for delegated)"
    )
    total_cost_usd: float = Field(
        description="Total cost in USD; 0.0 for local/cli; serialised as number (I7)"
    )
    started_at: datetime
    completed_at: datetime | None = Field(
        description="Run finish time (aliases finished_at); null for running rows"
    )
    error_message: str | None = Field(description="Error detail for failed runs; null otherwise")

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "id": "00000000-0000-0000-0000-000000000001",
                "vault_id": "default",
                "status": "completed",
                "provider_type": "api",
                "pages_created": 3,
                "iterations_used": 2,
                "total_cost_usd": 0.0042,
                "started_at": "2026-06-28T10:00:00Z",
                "completed_at": "2026-06-28T10:00:05Z",
                "error_message": None,
            }
        },
    }


class IngestRunListResponse(BaseModel):
    """
    Paginated list response for GET /ingest/runs (ADR-0018 §7, AC-BE-IR-1).
    Ordered started_at DESC (AC-BE-IR-3).
    """

    items: list[IngestRunResponse]
    total: int
    limit: int
    offset: int


# ── Queue Pydantic models (ADR-0046 §6) ───────────────────────────────────────


class QueueTaskItem(BaseModel):
    """One task in the live ingest queue snapshot (ADR-0046 §6)."""

    run_id: str | None = Field(
        description="UUID of the in-flight run; null for pending (not yet dispatched) tasks"
    )
    source_path: str = Field(description="Relative raw source path (raw/sources/…)")
    filename: str = Field(description="Basename of source_path")
    status: str = Field(description="pending | processing | failed")
    retry_count: int = Field(description="Times this source has been retried (I7, max 3)")
    error: str | None = Field(description="Error detail for failed tasks; null otherwise")
    started_at: str | None = Field(description="ISO-8601 start time; null for pending tasks")
    phase: str | None = Field(
        default=None,
        description=(
            "Human-facing current phase: queued | analyzing | generating (N/M) | "
            "validating | writing | agent running. None for pending/failed tasks."
        ),
    )
    progress: float | None = Field(
        default=None,
        description=(
            "Coarse 0..1 progress for orchestrated runs (0.0=queued, 0.2=analyzing, "
            "0.5=generating, 0.8=validating, 0.95=writing). "
            "None for delegated/CLI runs (indeterminate spinner) and non-processing tasks."
        ),
    )
    elapsed_seconds: int | None = Field(
        default=None,
        description="Seconds since the run started; null for pending/failed tasks.",
    )
    eta_seconds: int | None = Field(
        default=None,
        description=(
            "Best-effort estimated seconds until completion, derived from the historical "
            "average duration for this route (last 50 completed runs). "
            "None when no history is available for the route or for non-processing tasks."
        ),
    )


class QueueBatchProgress(BaseModel):
    """
    Whole-batch progress for a POST /sources/ingest-all run (surfaced in the activity panel).

    Lets the UI show "done/total" + a total ETA for the entire bulk index, not just the
    one-file-at-a-time queue view. eta_seconds = remaining files × avg per-file duration
    (best-effort; None when no history).
    """

    running: bool = Field(description="True while an ingest-all batch is in progress")
    done: int = Field(description="Files processed so far in the current batch")
    total: int = Field(description="Total files in the current batch")
    eta_seconds: int | None = Field(
        default=None,
        description="Estimated seconds remaining for the whole batch (None if unknown)",
    )


class QueueSnapshotResponse(BaseModel):
    """Live activity queue snapshot for GET /ingest/queue (ADR-0046 §6)."""

    paused: bool = Field(description="True when the queue is paused (no new dispatches)")
    pending: int = Field(description="Number of FS events parked while queue is paused")
    processing: int = Field(description="Number of currently in-flight ingest runs")
    failed: int = Field(description="Number of recently-failed tasks retained for retry")
    completed_since_idle: int = Field(
        description="Successful completions since the queue last went idle"
    )
    total: int = Field(description="pending + processing + failed")
    tasks: list[QueueTaskItem] = Field(description="All visible tasks (pending+processing+failed)")
    batch: QueueBatchProgress | None = Field(
        default=None,
        description="Whole-batch progress for an in-progress POST /sources/ingest-all (else null)",
    )
    marker_batch: QueueBatchProgress | None = Field(
        default=None,
        description=(
            "Whole-batch progress for an in-progress POST /ingest/convert-marker (else null). "
            "Poll GET /ingest/convert-marker/status for per-file detail."
        ),
    )


class QueueCancelResponse(BaseModel):
    """Response for POST /ingest/runs/{id}/cancel (ADR-0046 §6)."""

    run_id: str
    status: str = Field(description="'cancelling' — abort requested; cleanup completes async")
    cleaned_pages: int = Field(
        default=0,
        description="Always 0 at request time; cascade cleanup happens at the next loop boundary",
    )


class QueueRetryResponse(BaseModel):
    """Response for POST /ingest/runs/{id}/retry (ADR-0046 §6)."""

    run_id_prev: str = Field(description="UUID of the failed run that was retried")
    source_path: str
    retry_count: int = Field(description="New retry count (1..3); I7 hard cap = 3")
    status: str = Field(default="queued", description="'queued' — re-dispatch accepted")


class IngestCancelByIdResponse(BaseModel):
    """
    Response for DELETE /ingest/{run_id} (R13-3).

    status: 'cancelled' (200, was queued — never started) |
            'cancelling' (202, was running — cooperative abort requested).
    """

    status: str = Field(description="'cancelled' (queued) or 'cancelling' (running)")


class QueuePauseResponse(BaseModel):
    """Response for POST /ingest/queue/pause (ADR-0046 §6)."""

    paused: bool = Field(description="Always true; idempotent")


class QueueResumeResponse(BaseModel):
    """Response for POST /ingest/queue/resume (ADR-0046 §6)."""

    paused: bool = Field(description="Always false; idempotent")
    drained: int = Field(description="Number of pending entries replayed to the watcher")


# ── Upload Pydantic models (Feature U, ADR-0020 §2.1) ─────────────────────────


class UploadResponse(BaseModel):
    """
    202 response body for POST /ingest/upload (ADR-0020 §2.1, M4-EXT non-blocking).

    file_path:  saved path relative to vault_root (e.g. "raw/sources/notes.md")
    status:     always "queued" — the watcher picks up the file asynchronously.
    overwritten: true if a same-name file already existed and was replaced on disk.

    page_id is not returned because ingest is async (watcher-driven); poll GET /ingest/runs
    or GET /pages to confirm the page exists after ingest completes (~15-30s).
    """

    file_path: str = Field(
        ...,
        description='Saved path relative to vault_root, e.g. "raw/sources/notes.md"',
    )
    status: str = Field(
        ...,
        description='"queued" — file saved to raw/sources/; watcher ingests asynchronously.',
    )
    overwritten: bool = Field(
        ...,
        description="True if a same-name file already existed and was replaced on disk",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "file_path": "raw/sources/notes.md",
                "status": "queued",
                "overwritten": False,
            }
        }
    }


# ── POST /ingest/from-text Pydantic models (ADR-0019 §2.7, AC-F6-5) ──────────


class IngestFromTextRequest(BaseModel):
    """
    Request body for POST /ingest/from-text (ADR-0019 §2.7, AC-F6-5 save-to-wiki).

    Writes ``text`` to ``vault/raw/sources/chat-{message_id}.md`` (or a derived name)
    and runs the same ``ingest_file`` seam (ADR-0003).  No new ingest logic — only a
    file-materialisation step.
    """

    text: str = Field(
        ...,
        min_length=1,
        description="Raw text to ingest (e.g. an assistant message)",
    )
    source_hint: str | None = Field(
        default=None,
        description=(
            "Optional hint for the output filename stem, e.g. a message_id or short slug. "
            "Sanitised to basename; falls back to 'chat-<uuid>' when omitted or unsafe."
        ),
    )
    vault_id: str | None = Field(default=None, description="Defaults to settings.vault_id")

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": "# Homelab notes\nDocker services on TrueNAS...",
                "source_hint": "chat-homelab-notes",
                "vault_id": None,
            }
        }
    }


class IngestFromTextResponse(BaseModel):
    """202 response for POST /ingest/from-text (ADR-0019 §2.7)."""

    file_path: str = Field(..., description="Path written relative to vault_root")
    status: str = Field(..., description='"queued" — watcher ingests asynchronously')
    page_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Page UUID when ingest completes synchronously (trigger path); "
            "null when async (watcher path)."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "file_path": "raw/sources/chat-homelab-notes.md",
                "status": "queued",
                "page_id": None,
            }
        }
    }


# ── POST /ingest/trigger ───────────────────────────────────────────────────────


@router.post(
    "/ingest/trigger",
    response_model=IngestTriggerResponse,
    status_code=202,
    summary="Manually trigger ingest of a single file",
    description=(
        "Synchronously ingests the file at file_path through the seam. "
        "Returns HTTP 202 with typed {task_id, status, page_id} (ADR-0006, AC-REST-4, AC-D4u). "
        "status is 'completed' or 'skipped' (I1 fast-path). "
        "429 if per-IP rate limit exceeded (R13-9)."
    ),
    responses={
        202: {"description": "Ingest accepted and completed"},
        422: {"description": "Validation error (missing file_path, bad format, or file not found)"},
        429: {"description": "Per-IP rate limit exceeded (R13-9)"},
    },
    dependencies=[Depends(rate_limit)],
)
async def trigger_ingest(body: IngestTriggerRequest) -> IngestTriggerResponse:
    """
    Trigger incremental ingest of a single file (K2 partial, ADR-0006, AC-D4u).

    Resolves the file path under vault_root if relative.
    Runs ingest_file through the seam (ADR-0003); never touches DB/Qdrant directly.
    Returns 202 per ADR-0006 contract with a typed schema so task_id appears in OpenAPI (AC-D4u).
    """
    from pathlib import Path

    # Resolve relative or absolute path
    path = Path(body.file_path)
    if not path.is_absolute():
        path = settings.vault_root / path

    if not path.exists():
        raise HTTPException(
            status_code=422,
            detail=f"File not found: {body.file_path}",
        )

    result: IngestResult = await ingest_file(path)

    return IngestTriggerResponse(
        task_id=None,
        status=result.status,
        page_id=result.page_id,
    )


# ── S1: rel_dir sanitization helper ──────────────────────────────────────────


def _sanitize_rel_dir(raw_rel_dir: str) -> str:
    """
    Sanitize the optional rel_dir form field from a folder upload (S1, B3b).

    Sanitization rules (mirrors safe_source_name for each path segment):
    1. Split on forward-slash only (client MUST normalise separators).
    2. Reject if any segment is empty, ".", or "..".
    3. Reject if any segment contains a path separator char (belt-and-braces).
    4. Strip NUL/control chars from each segment; collapse whitespace.
    5. Reject if any segment is empty after stripping.
    6. Re-join with forward slash.

    Returns the sanitized relative directory string (e.g. "projects/notes").
    Raises HTTPException(422) on any violation.
    """
    if not raw_rel_dir:
        raise HTTPException(status_code=422, detail="rel_dir is empty.")

    segments: list[str] = raw_rel_dir.split("/")
    clean_segments: list[str] = []

    for raw_seg in segments:
        # Skip empty segments from trailing/leading slashes (tolerated, not injected)
        if not raw_seg:
            continue

        # Reject sentinel values
        if raw_seg in {".", ".."}:
            raise HTTPException(
                status_code=422,
                detail=f"rel_dir segment {raw_seg!r} is not allowed (path traversal).",
            )

        # Belt-and-braces: no backslash or slash characters should survive
        if _SEP_RE.search(raw_seg):
            raise HTTPException(
                status_code=422,
                detail=f"rel_dir segment contains an illegal path separator: {raw_seg!r}.",
            )

        # Strip NUL and control characters; collapse whitespace
        seg = "".join(ch for ch in raw_seg if ord(ch) >= 0x20 and ch != "\x7f")
        seg = _re.sub(r"\s+", " ", seg).strip()

        if not seg:
            raise HTTPException(
                status_code=422,
                detail="rel_dir segment is empty or contains only control characters.",
            )

        clean_segments.append(seg)

    if not clean_segments:
        raise HTTPException(status_code=422, detail="rel_dir contains no valid segments.")

    return "/".join(clean_segments)


# ── POST /ingest/upload ────────────────────────────────────────────────────────


@router.post(
    "/ingest/upload",
    response_model=UploadResponse,
    status_code=202,
    summary="Upload a document for async watcher-driven ingest (F12 multi-format)",
    description=(
        "Feature U (ADR-0020 §2, M4-EXT) + F12 Multi-format ingest (ADR-0025 §4.2) "
        "+ S1 folder-upload support (B3b). "
        "Accepts text/markdown (.md/.txt/.markdown), binary formats (.pdf/.docx/.pptx/.xlsx), "
        "and placeholder formats (.png/.jpg/.jpeg/.gif/.webp/.mp3/.mp4/.wav/.m4a). "
        "Optional 'rel_dir' form field: when supplied by the frontend '+ Folder' picker, "
        "the file is written under vault/raw/sources/<rel_dir>/<name> so the subfolder "
        "structure is preserved. rel_dir is sanitized segment-by-segment (same rules as "
        "safe_source_name) and containment-checked via resolve_under_sources — 422 on any "
        "path-traversal attempt. When rel_dir is absent, behaviour is unchanged (writes flat). "
        "For text: writes to vault/raw/sources/[rel_dir/]<name>; watcher ingests asynchronously. "
        "For binary/placeholder: (1) writes original binary to "
        "vault/raw/sources/[rel_dir/]<name>.<ext> "
        "(preserved, I5/K1); (2) synchronously extracts text → companion "
        "<stem>.extracted.md (in the same rel_dir subdirectory) with valid YAML frontmatter "
        "(I5); (3) returns 202. "
        "The watcher ingests ONLY the companion (.md is in _ALLOWED_EXTENSIONS); the binary "
        "is ignored by the watcher (I1). Extraction is upload-time, NEVER in the watcher. "
        "413 on oversize (MAX_UPLOAD_BYTES). 415 for truly unknown types. "
        "422 for unsafe filename or unsafe rel_dir. 202 {file_path, status:'queued', overwritten}. "
        "429 if per-IP rate limit exceeded (R13-9)."
    ),
    responses={
        202: {
            "description": "File saved; watcher will ingest asynchronously (companion for binaries)"
        },
        413: {"description": "File exceeds MAX_UPLOAD_BYTES"},
        415: {"description": "Unsupported file type"},
        422: {"description": "Filename or rel_dir is empty or unsafe after sanitization"},
        429: {"description": "Per-IP rate limit exceeded (R13-9)"},
    },
    dependencies=[Depends(rate_limit)],
)
async def upload_ingest(
    file: UploadFile = File(..., description="The document to upload"),
    rel_dir: str | None = Form(
        default=None,
        description=(
            "Optional relative subdirectory under vault/raw/sources/ for folder uploads (S1, B3b). "
            "Use forward slashes as separator (e.g. 'projects/notes'). "
            "Each segment is sanitized like safe_source_name. "
            "Path-traversal attempts ('..') → 422. "
            "When absent, file is written flat under vault/raw/sources/ (unchanged behaviour)."
        ),
    ),
) -> UploadResponse:
    """
    POST /ingest/upload — non-blocking multipart upload (ADR-0020 Feature U, §2).

    1. Validate extension (hard) + Content-Type (soft advisory) → 415 on non-text.
    2. Stream body to a temp file, abort at MAX_UPLOAD_BYTES              → 413.
    3. safe_source_name(filename)                                          → 422 on unsafe.
    4. If rel_dir supplied: _sanitize_rel_dir(rel_dir)                    → 422 on unsafe.
    5. resolve_under_sources(<rel_dir>/<name>) containment check           → 422 on escape.
    6. overwritten = dst.exists()
    7. Atomically move temp file to dst (same-fs rename inside /vault).
    8. Return 202 {file_path, status:"queued", overwritten} immediately.

    The WATCHER observes the vault/raw/sources/ write and ingests asynchronously.
    This is the same path Feature S (scheduled copy) uses — no double-ingest (I9).
    Poll GET /ingest/runs or GET /pages to confirm ingest completion (~15-30s).

    Security: basename-only; no caller-controlled path segments; containment-checked.
    I1: watcher's mtime/hash gate deduplicates re-uploads of unchanged content.
    I5: writes ONLY to vault/raw/sources/ — never to wiki/ or .obsidian/.
    S1: rel_dir allows the frontend '+ Folder' picker to preserve subdir structure
        without opening any path-traversal surface (segment-level sanitization + containment).
    """
    import tempfile

    max_bytes: int = settings.max_upload_bytes

    # ── Extension check (authoritative; MIME is advisory) ────────────────────
    # Do this BEFORE reading bytes (fail fast)
    raw_name: str = file.filename or ""
    # safe_source_name raises 415 for non-text extensions, 422 for unsafe
    name = safe_source_name(raw_name)

    # ── S1: sanitize optional rel_dir ────────────────────────────────────────
    # When supplied by the frontend folder picker, each segment is sanitized
    # identically to safe_source_name (strip separators, control chars, sentinels).
    # The final destination is still containment-checked via resolve_under_sources.
    clean_rel_dir: str | None = None
    if rel_dir is not None and rel_dir.strip():
        clean_rel_dir = _sanitize_rel_dir(rel_dir.strip())

    # ── Stream body with byte cap (I7) ───────────────────────────────────────
    raw_sources = settings.raw_sources_dir
    raw_sources.mkdir(parents=True, exist_ok=True)

    # Write temp file to raw_sources root (always safe; renamed after validation)
    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(raw_sources), suffix=".upload_tmp")
    bytes_read = 0
    try:
        with open(tmp_fd, "wb") as tmp_file:
            chunk_size = 65_536  # 64 KB chunks
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit."),
                    )
                tmp_file.write(chunk)
    except HTTPException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    except Exception as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload read error: {exc}") from exc
    finally:
        await file.close()

    # ── Containment check (S1: include rel_dir in the resolved path) ────────────
    # When rel_dir is set, the destination is raw/sources/<clean_rel_dir>/<name>.
    # resolve_under_sources performs the belt-and-braces containment check so an
    # adversarially crafted (but sanitized) rel_dir can never escape raw/sources/.
    resolved_name = f"{clean_rel_dir}/{name}" if clean_rel_dir else name
    try:
        dst = resolve_under_sources(resolved_name)
    except HTTPException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    # ── Create subdirectory if needed (S1 folder upload) ────────────────────
    # Only created for legitimate rel_dir paths that cleared the containment check.
    dst.parent.mkdir(parents=True, exist_ok=True)

    # ── Atomic move (same-fs: rename within /vault/raw/sources/) ────────────
    overwritten: bool = dst.exists()
    try:
        Path(tmp_name).replace(dst)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to write file: {exc}") from exc

    # ── F12: synchronous extraction for binary/placeholder uploads (ADR-0025 §4.2) ──
    # If the file is a binary or placeholder extension, extract text NOW (before 202)
    # so the companion .extracted.md exists when the watcher fires.
    # The watcher ignores the binary (not in _ALLOWED_EXTENSIONS); only the companion is
    # ingested. This is the ONLY place extraction happens — never inside the watcher (Do-NOT #12).
    # S1: when rel_dir is set, the companion is written in the SAME subdirectory as the binary
    # so the folder structure is preserved end-to-end.
    suffix_lower = Path(name).suffix.lower()
    from app.upload import _EXTRACTABLE_EXTENSIONS, _PLACEHOLDER_EXTENSIONS

    if suffix_lower in (_EXTRACTABLE_EXTENSIONS | _PLACEHOLDER_EXTENSIONS):
        try:
            from app.ingest.extract import UnsupportedFormatError, extract_text

            extracted = extract_text(dst)
            # Build companion filename: <stem>.extracted.md (same subdir as the binary)
            stem = Path(name).stem
            companion_name = f"{stem}.extracted.md"
            companion_dst = dst.parent / companion_name
            # Write valid Obsidian YAML frontmatter (I5, AC-F12-4, ADR-0025 §4.4)
            raw_rel = str(dst.relative_to(settings.vault_root))
            companion_content = (
                f'---\ntype: source\ntitle: {stem}\nsources: ["{raw_rel}"]\n---\n\n' + extracted
            )
            companion_dst.write_text(companion_content, encoding="utf-8")
            logger.info(
                "upload_ingest: extracted %s → companion %s (%d chars)",
                name,
                companion_name,
                len(extracted),
            )
            # Return the companion path as the queued file (the watcher ingests this)
            rel_path = str(companion_dst.relative_to(settings.vault_root))
        except UnsupportedFormatError as exc:
            # Should not happen (upload guard already validated the extension), but handle cleanly
            raise HTTPException(status_code=415, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            # Extraction failure: log but do NOT block the 202 — the binary is safely saved.
            # The companion will not be created; the watcher will silently skip the binary (I1).
            logger.warning(
                "upload_ingest: extraction failed for %s: %s — companion not created",
                name,
                exc,
            )
            rel_path = str(dst.relative_to(settings.vault_root))
    else:
        rel_path = str(dst.relative_to(settings.vault_root))

    # ── Return 202 immediately — watcher ingests asynchronously ──────────────
    logger.info("upload_ingest: saved %s (%d bytes) — watcher will ingest", name, bytes_read)
    return UploadResponse(
        file_path=rel_path,
        status="queued",
        overwritten=overwritten,
    )


# ── POST /ingest/convert-marker ── R11-1: explicit Marker conversion (W0 async) ──
# ── GET  /ingest/convert-marker/status ── per-file progress poll ─────────────
# ── GET  /ingest/marker-health  ── R11-1: proxy health check (AC-R11-1-4) ────
#
# W0 async redesign: Marker calls are moved to a background asyncio.Task so that
# the HTTP response returns in <1 s (well under the 100 s Cloudflare Tunnel timeout
# that previously caused 524 errors). The driver in marker_converter.py serialises
# calls (concurrency=1) because Marker is a single-GPU service.
#
# Design preserved from ADR-0051: NO silent pypdf fallback on this path.
# Validation (400/413/415) still synchronous — these are fast pre-flight checks.


class MarkerConvertAcceptedFile(BaseModel):
    """One file entry in the 202 response body of POST /ingest/convert-marker."""

    file: str = Field(description="Original filename as submitted (e.g. 'report.pdf')")
    safe_stem: str = Field(description="Sanitised stem used for the output paths (e.g. 'report')")
    pdf_path: str = Field(
        description="Vault-relative path where the raw PDF was saved (raw/sources/<stem>.pdf)"
    )


class MarkerConvertAcceptResponse(BaseModel):
    """
    202 Accepted response for POST /ingest/convert-marker (W0 async rewrite).

    The Marker calls happen in a background task — poll
    GET /ingest/convert-marker/status (or GET /ingest/queue .marker_batch) for progress.
    """

    batch_id: str = Field(description="UUID identifying this conversion batch")
    queued: list[MarkerConvertAcceptedFile] = Field(
        description="Files accepted and queued for background Marker conversion"
    )
    total: int = Field(description="Number of files queued (== len(queued))")


class MarkerBatchStatusFile(BaseModel):
    """Per-file status entry for GET /ingest/convert-marker/status."""

    file: str = Field(description="Original filename (e.g. 'report.pdf')")
    safe_stem: str = Field(description="Sanitised stem (e.g. 'report')")
    status: str = Field(description="'pending' | 'converting' | 'ok' | 'failed'")
    detail: str | None = Field(
        default=None,
        description="Error detail when status='failed'; null otherwise",
    )
    companion_path: str | None = Field(
        default=None,
        description=(
            "Vault-relative path to the written .extracted.md (raw/sources/<stem>.extracted.md). "
            "Set when status='ok'; null otherwise."
        ),
    )


class MarkerBatchStatusResponse(BaseModel):
    """
    Response for GET /ingest/convert-marker/status.

    Returns the current (or most-recent) batch state with per-file detail.
    When no batch has ever run this session, running=false and all counts are 0.
    """

    batch_id: str | None = Field(
        default=None,
        description="UUID of the current/most-recent batch; null if no batch has run",
    )
    running: bool = Field(description="True while the background driver is active")
    total: int = Field(description="Files in this batch")
    done: int = Field(description="Files completed (ok + failed)")
    eta_seconds: int | None = Field(
        default=None,
        description="Estimated seconds remaining (null when done=0 or batch finished)",
    )
    files: list[MarkerBatchStatusFile] = Field(
        description="Per-file status entries (empty when no batch has run)"
    )


@router.post(
    "/ingest/convert-marker",
    response_model=MarkerConvertAcceptResponse,
    status_code=202,
    summary="Queue one or more PDFs for async Marker conversion (R11-1, W0)",
    description=(
        "F12 / R11-1 — explicit Marker PDF conversion endpoint (W0 async). "
        "Accepts multipart files[] (≤10 files, each ≤ MAX_UPLOAD_BYTES, .pdf only). "
        "Validates + saves each raw PDF synchronously, then enqueues background Marker "
        "conversion (concurrency=1 — single-GPU Marker service; I7). "
        "Returns 202 immediately with batch_id and per-file entries. "
        "Poll GET /ingest/convert-marker/status for per-file progress/result. "
        "On success the driver writes <stem>.extracted.md (I5 YAML frontmatter) and the "
        "watcher ingests it incrementally (I1). Per-file Marker failures mark that file "
        "'failed' without aborting the rest of the batch. "
        "NO silent pypdf fallback (ADR-0051). "
        "400 if > 10 files. 409 if a batch is already running. "
        "413 if any file > MAX_UPLOAD_BYTES. 415 for non-.pdf files."
    ),
    responses={
        202: {"description": "Files queued for background Marker conversion."},
        400: {"description": "More than 10 files submitted."},
        409: {"description": "A Marker conversion batch is already running."},
        413: {"description": "A file exceeds MAX_UPLOAD_BYTES."},
        415: {"description": "A non-.pdf file was submitted."},
    },
)
async def convert_marker(
    files: list[UploadFile] = File(..., description="PDF files to convert (≤10)."),
) -> MarkerConvertAcceptResponse:
    """
    POST /ingest/convert-marker — async Marker PDF conversion (R11-1 / W0).

    For each file:
    1. Reject non-.pdf (415), oversize (413).
    2. Write raw PDF bytes synchronously to vault/raw/sources/<stem>.pdf (I5/K1).
    3. Build a MarkerFileEntry and add to the batch.
    After all files are saved:
    4. Fire start_marker_batch() — background driver calls Marker serially (I7, concurrency=1).
    5. Return 202 immediately (well under the 100 s Cloudflare Tunnel timeout).

    Background driver (marker_converter.py):
    - Calls Marker /convert per file (MARKER_TIMEOUT_SECONDS bound, I7).
    - On success: writes <stem>.extracted.md; watcher ingests (I1).
    - On per-file failure: marks that file 'failed'; continues with next file.
    NO silent pypdf fallback — the user explicitly chose Marker (ADR-0051).
    """
    import tempfile  # noqa: PLC0415

    from app.marker_converter import (  # noqa: PLC0415
        MarkerFileEntry,
        is_running,
        start_marker_batch,
    )

    # ── AC-R11-1-1: reject > 10 files ────────────────────────────────────────
    if len(files) > 10:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files: {len(files)} submitted; maximum is 10.",
        )

    # ── Single-flight guard (Marker is single-GPU; one batch at a time) ──────
    if is_running():
        raise HTTPException(
            status_code=409,
            detail=(
                "A Marker conversion batch is already running. "
                "Poll GET /ingest/convert-marker/status and retry when running=false."
            ),
        )

    max_bytes: int = settings.max_upload_bytes

    # ── Read effective Marker settings (captured at enqueue time — ADR-0053) ─
    _eff_marker_url: str = (
        effective_str("marker_service_url", settings.marker_service_url)
        or settings.marker_service_url
    )
    _eff_marker_timeout: float = effective_float(
        "marker_timeout_seconds", settings.marker_timeout_seconds
    )

    raw_sources = settings.raw_sources_dir
    raw_sources.mkdir(parents=True, exist_ok=True)

    entries: list[MarkerFileEntry] = []
    accepted: list[MarkerConvertAcceptedFile] = []

    for upload in files:
        raw_name: str = upload.filename or ""
        stem = Path(raw_name).stem if raw_name else "untitled"
        suffix = Path(raw_name).suffix.lower() if raw_name else ""

        # ── AC-R11-1-1: reject non-pdf ────────────────────────────────────────
        if suffix != ".pdf":
            raise HTTPException(
                status_code=415,
                detail=(
                    f"File {raw_name!r} is not a PDF. "
                    "POST /ingest/convert-marker accepts only .pdf files."
                ),
            )

        # ── Read raw bytes with size cap (AC-R11-1-1 / I7) ───────────────────
        tmp_fd, tmp_name = tempfile.mkstemp(dir=str(raw_sources), suffix=".marker_tmp")
        bytes_read = 0
        try:
            with open(tmp_fd, "wb") as tmp_file:
                chunk_size = 65_536
                while True:
                    chunk = await upload.read(chunk_size)
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                    if bytes_read > max_bytes:
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"File {raw_name!r} exceeds the "
                                f"{max_bytes // (1024 * 1024)} MB upload limit."
                            ),
                        )
                    tmp_file.write(chunk)
        except HTTPException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        except Exception as exc:
            Path(tmp_name).unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Upload read error: {exc}") from exc
        finally:
            await upload.close()

        # ── Write raw PDF bytes to raw/sources/<stem>.pdf ────────────────────
        safe_stem = _re.sub(r"[^a-z0-9_.-]", "_", stem.lower())[:100] or "upload"
        pdf_name = f"{safe_stem}.pdf"
        pdf_dst = raw_sources / pdf_name
        pdf_bytes = Path(tmp_name).read_bytes()
        Path(tmp_name).unlink(missing_ok=True)
        pdf_dst.write_bytes(pdf_bytes)

        pdf_rel = str(pdf_dst.relative_to(settings.vault_root))
        entries.append(
            MarkerFileEntry(
                file=raw_name,
                safe_stem=safe_stem,
                pdf_abs_path=str(pdf_dst),
            )
        )
        accepted.append(
            MarkerConvertAcceptedFile(
                file=raw_name,
                safe_stem=safe_stem,
                pdf_path=pdf_rel,
            )
        )
        logger.info(
            "convert_marker: saved %s → %s; queued for background Marker conversion",
            raw_name,
            pdf_dst,
        )

    # ── Fire background conversion (concurrency=1, I7) ───────────────────────
    batch = start_marker_batch(
        entries=entries,
        eff_marker_url=_eff_marker_url,
        eff_marker_timeout=_eff_marker_timeout,
        vault_root=settings.vault_root,
    )

    return MarkerConvertAcceptResponse(
        batch_id=str(batch.batch_id),
        queued=accepted,
        total=len(accepted),
    )


@router.get(
    "/ingest/convert-marker/status",
    response_model=MarkerBatchStatusResponse,
    summary="Per-file status of the current (or most-recent) Marker conversion batch",
    description=(
        "Returns per-file conversion status for the current or most-recent "
        "POST /ingest/convert-marker batch. "
        "Status values: 'pending' (not yet started) | 'converting' (Marker call in flight) | "
        "'ok' (companion written; watcher will ingest) | 'failed' (Marker error, detail set). "
        "When running=false and total=0, no batch has been submitted this session. "
        "Safe to poll every 2-5 s; pure in-memory, no DB I/O."
    ),
    responses={200: {"description": "Batch status snapshot"}},
)
async def get_convert_marker_status() -> MarkerBatchStatusResponse:
    """GET /ingest/convert-marker/status — live per-file progress snapshot."""
    from app.marker_converter import get_marker_batch_progress  # noqa: PLC0415

    prog = get_marker_batch_progress()
    files = [
        MarkerBatchStatusFile(
            file=f["file"],
            safe_stem=f["safe_stem"],
            status=f["status"],
            detail=f["detail"],
            companion_path=f["companion_path"],
        )
        for f in prog["files"]
    ]
    return MarkerBatchStatusResponse(
        batch_id=prog["batch_id"],
        running=bool(prog["running"]),
        total=int(prog["total"]),
        done=int(prog["done"]),
        eta_seconds=prog.get("eta_seconds"),
        files=files,
    )


class MarkerHealthResponse(BaseModel):
    """Response for GET /ingest/marker-health (R11-1 / AC-R11-1-4)."""

    status: Literal["ok", "offline"] = Field(
        description='"ok" when Marker responds 200; "offline" when unreachable or non-200.'
    )
    detail: str | None = Field(
        default=None,
        description="Error detail when status='offline'; null when ok.",
    )


@router.get(
    "/ingest/marker-health",
    response_model=MarkerHealthResponse,
    summary="Proxy the Marker microservice health check (R11-1)",
    description=(
        "R11-1 — GET {effective MARKER_SERVICE_URL}/health proxy. "
        "Returns {'status':'ok'} (200) when Marker responds 200. "
        "Returns {'status':'offline','detail':'...'} (503) when unreachable or non-200. "
        "Uses the effective Marker URL (env baseline + DB override via config_overrides S2)."
    ),
    responses={
        200: {"description": "Marker is reachable and healthy."},
        503: {"description": "Marker is offline or unreachable."},
    },
)
async def marker_health() -> Response:
    """GET /ingest/marker-health — proxy Marker /health (R11-1 / AC-R11-1-4)."""
    import httpx  # noqa: PLC0415

    _eff_marker_url = (
        effective_str("marker_service_url", settings.marker_service_url)
        or settings.marker_service_url
    )
    health_url = f"{_eff_marker_url.rstrip('/')}/health"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(health_url)
        if resp.status_code == 200:
            return Response(
                content='{"status":"ok"}',
                status_code=200,
                media_type="application/json",
            )
        detail = f"Marker returned HTTP {resp.status_code}: {resp.text[:200]}"
    except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError) as exc:
        detail = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # noqa: BLE001
        detail = f"Unexpected error: {exc}"

    import json  # noqa: PLC0415

    return Response(
        content=json.dumps({"status": "offline", "detail": detail}),
        status_code=503,
        media_type="application/json",
    )


# ── POST /ingest/from-text ────────────────────────────────────────────────────


@router.post(
    "/ingest/from-text",
    response_model=IngestFromTextResponse,
    status_code=202,
    summary="Write inline text to raw/sources/ and queue watcher-driven ingest",
    description=(
        "Save-to-wiki seam (ADR-0019 §2.7, AC-F6-5). "
        "Materialises ``text`` to ``vault/raw/sources/chat-<hint>.md`` and returns 202 "
        "immediately. The watcher picks up the file and runs the full ingest pipeline "
        "(no new ingest logic — ADR-0003 guarantee, I1/I6). "
        "``source_hint`` is sanitised to a safe basename; falls back to ``chat-<uuid>`` when "
        "omitted or unsafe. 422 on empty text. 429 if per-IP rate limit exceeded (R13-9)."
    ),
    responses={
        202: {"description": "Text saved; watcher will ingest asynchronously"},
        422: {"description": "Validation error (text empty or too long)"},
        429: {"description": "Per-IP rate limit exceeded (R13-9)"},
    },
    dependencies=[Depends(rate_limit)],
)
async def ingest_from_text(body: IngestFromTextRequest) -> IngestFromTextResponse:
    """
    POST /ingest/from-text — materialise inline text to raw/sources/ and enqueue watcher.

    1. Derive a safe filename from source_hint (basename-only, slug-safe fallback).
    2. Write the text to vault/raw/sources/<name>.md (atomically via temp → rename).
    3. Return 202 {file_path, status:'queued'} — watcher ingests asynchronously.

    I1: watcher's mtime/hash gate deduplicates re-posts of identical content.
    I5: writes ONLY to vault/raw/sources/ — never to wiki/ or .obsidian/.
    I6: inference goes through the existing ingest pipeline (ADR-0003, no shortcut).
    """
    import re as _re
    import tempfile as _tempfile

    _SLUG_RE_MAIN = _re.compile(r"[^a-z0-9_-]+")

    # Derive a safe filename stem from the hint (or a fresh UUID).
    raw_hint = (body.source_hint or "").strip()
    if raw_hint:
        stem = _SLUG_RE_MAIN.sub("-", raw_hint.lower()).strip("-")[:80]
        if not stem:
            stem = f"chat-{uuid.uuid4().hex[:8]}"
    else:
        stem = f"chat-{uuid.uuid4().hex[:8]}"
    filename = f"{stem}.md"

    raw_sources = settings.raw_sources_dir
    raw_sources.mkdir(parents=True, exist_ok=True)
    dst = raw_sources / filename

    # Atomic write via temp → rename (same approach as upload_ingest).
    tmp_fd, tmp_name = _tempfile.mkstemp(dir=str(raw_sources), suffix=".fromtext_tmp")
    try:
        with open(tmp_fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(body.text)
    except Exception as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to write text: {exc}") from exc

    try:
        Path(tmp_name).replace(dst)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to persist file: {exc}") from exc

    rel_path = str(dst.relative_to(settings.vault_root))
    logger.info(
        "ingest_from_text: saved %s (%d chars) — watcher will ingest",
        filename,
        len(body.text),
    )
    return IngestFromTextResponse(file_path=rel_path, status="queued", page_id=None)


def _ingest_run_to_response(run: IngestRun) -> IngestRunResponse:
    """
    Map IngestRun ORM row → IngestRunResponse.

    Applies the two ADR-0018 §7 aliases:
      max_iter_used  → iterations_used
      finished_at    → completed_at
    total_cost_usd converted from Decimal (Numeric column) to float for JSON serialisation.
    completed_at is None when status == 'running' (run still in progress).
    """
    completed_at: datetime | None = None if run.status == "running" else run.finished_at
    return IngestRunResponse(
        id=run.id,
        vault_id=run.vault_id,
        status=run.status,
        provider_type=run.provider_type,
        pages_created=run.pages_created,
        iterations_used=run.max_iter_used,
        total_cost_usd=float(run.total_cost_usd),
        started_at=run.started_at,
        completed_at=completed_at,
        error_message=run.error_message,
    )


# ── GET /ingest/runs ───────────────────────────────────────────────────────────


@router.get(
    "/ingest/runs",
    response_model=IngestRunListResponse,
    summary="List ingest run history",
    description=(
        "Returns a paginated, started_at DESC list of ingest_runs rows. "
        "Exposes the I7 cost ledger to the user (AC-BE-IR-1..5, ADR-0018 §7). "
        "limit: 1..100 default 20; offset: >=0 default 0; vault_id: optional UUID filter. "
        "Column aliases: max_iter_used→iterations_used, finished_at→completed_at. "
        "total_cost_usd serialised as a number; frontend formats to exactly 4dp (I7)."
    ),
    responses={
        200: {"description": "Paginated ingest run list"},
        422: {"description": "Validation error (limit out of 1..100 or offset < 0)"},
    },
)
async def list_ingest_runs(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Max rows to return (1..100); 422 on out-of-range (AC-BE-IR-2)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Row offset for pagination (>=0); 422 on negative (AC-BE-IR-2)",
    ),
    vault_id: str | None = Query(
        default=None,
        description="Optional vault_id filter; omit to list all vaults (AC-BE-IR-2)",
    ),
) -> IngestRunListResponse:
    """
    GET /ingest/runs — paginated ingest run history (ADR-0018 §7, AC-BE-IR-1..5).

    Plain read query — no heavy computation (pure SELECT, ORDER BY, LIMIT/OFFSET).
    Filters by vault_id when provided.
    Orders by started_at DESC (AC-BE-IR-3).
    422 enforced by Query(ge=1, le=100) / Query(ge=0) validators (AC-BE-IR-5).
    """
    async with _m.get_session() as session:
        # COUNT query (filtered)
        count_stmt = select(func.count()).select_from(IngestRun)
        if vault_id is not None:
            count_stmt = count_stmt.where(IngestRun.vault_id == vault_id)
        total_row = await session.execute(count_stmt)
        total: int = total_row.scalar_one()

        # Data query (filtered, ordered, paginated)
        data_stmt = select(IngestRun)
        if vault_id is not None:
            data_stmt = data_stmt.where(IngestRun.vault_id == vault_id)
        data_stmt = data_stmt.order_by(IngestRun.started_at.desc()).offset(offset).limit(limit)
        rows = await session.execute(data_stmt)
        runs = list(rows.scalars().all())

    items = [_ingest_run_to_response(r) for r in runs]
    return IngestRunListResponse(items=items, total=total, limit=limit, offset=offset)


# ── ETA helper for GET /ingest/queue ─────────────────────────────────────────

_ETA_HISTORY_WINDOW: int = 50  # bounded history window (I7)


async def _compute_avg_duration_by_route() -> dict[str, float]:
    """
    Return a dict mapping route → average completed-run duration in seconds.

    Queries the last _ETA_HISTORY_WINDOW completed/converged_false rows per route from
    ingest_runs (finished_at IS NOT NULL, status IN ('completed','converged_false')).
    Uses portable CAST(col AS TEXT) to avoid dialect differences (SQLite/Postgres).

    Returns {} when the table is absent (pre-migration env) or has no history.
    Called once per GET /ingest/queue — bounded query, read-only (I1-safe).
    """
    result: dict[str, float] = {}
    for route_val in ("orchestrated", "delegated"):
        try:
            async with _m.get_session() as session:
                # avg(extract(epoch from finished_at - started_at)) — Postgres dialect.
                # For SQLite compatibility we use julianday arithmetic converted to seconds.
                # We rely on the fact that started_at and finished_at are stored as
                # timezone-aware datetimes; the ORM returns Python datetime objects.
                # Fallback: load the raw timestamps and compute avg in Python so the
                # query works on BOTH SQLite (tests) and Postgres (production) without
                # dialect-specific SQL (avoids the raw-SQL SQLite vs Postgres pitfall noted
                # in memory/raw-sql-sqlite-tests-vs-postgres-runtime.md).
                stmt = (
                    select(IngestRun.started_at, IngestRun.finished_at)
                    .where(
                        IngestRun.vault_id == settings.vault_id,
                        IngestRun.route == route_val,
                        IngestRun.status.in_(["completed", "converged_false"]),
                        IngestRun.finished_at.isnot(None),
                    )
                    .order_by(IngestRun.started_at.desc())
                    .limit(_ETA_HISTORY_WINDOW)
                )
                rows = list((await session.execute(stmt)).all())
            if rows:
                durations = [
                    (row.finished_at - row.started_at).total_seconds()
                    for row in rows
                    if row.finished_at is not None and row.started_at is not None
                ]
                if durations:
                    result[route_val] = sum(durations) / len(durations)
        except Exception:  # noqa: BLE001,S110
            # Per-route failure is non-fatal; skip this route → eta_seconds=None for it.
            pass
    return result


# ── GET /ingest/queue + POST /ingest/runs/{id}/cancel|retry + pause/resume ───
# ADR-0046 §6 — live activity queue endpoints


@router.get(
    "/ingest/queue",
    response_model=QueueSnapshotResponse,
    summary="Live ingest activity queue snapshot",
    description=(
        "Returns the live in-memory queue state: processing (in-flight), pending (paused), "
        "failed (retained for retry), completed_since_idle, and per-task details. "
        "Pure in-memory — no DB scan. Safe to poll every 5 s (ADR-0046 §6, I3). "
        "(ADR-0046)"
    ),
    responses={200: {"description": "Queue snapshot"}},
)
async def get_ingest_queue() -> QueueSnapshotResponse:
    """GET /ingest/queue — live snapshot from the in-process queue manager (ADR-0046 §6)."""
    from app.ingest.queue_manager import ingest_queue as _iq

    # ── ETA: compute historical average run duration per route (bounded, I7) ───
    # Read ingest_runs WHERE status IN ('completed','converged_false') AND finished_at IS NOT NULL,
    # grouped by route, over last 50 runs per route. Single DB read per endpoint call.
    # Tolerates DB unavailability (avg_by_route = {}) — snapshot() degrades to eta_seconds=None.
    avg_by_route: dict[str, float] = {}
    try:
        avg_by_route = await _compute_avg_duration_by_route()
    except Exception:  # noqa: BLE001
        # Non-fatal: ETA degrades to None; queue still returns all other fields.
        logger.debug("get_ingest_queue: ETA history query failed — eta_seconds will be None")

    snap = _iq.snapshot(avg_duration_by_route=avg_by_route)
    tasks = [QueueTaskItem(**t) for t in snap["tasks"]]

    # ── Batch progress (POST /sources/ingest-all) — surface done/total + a whole-batch ETA ──
    batch: QueueBatchProgress | None = None
    try:
        from app.sources import get_ingest_all_progress

        bp = get_ingest_all_progress()
        b_total = int(bp["total"])
        b_done = int(bp["done"])
        if bp["running"] or (b_total > 0 and b_done < b_total):
            # Per-file estimate: prefer the delegated route (CLI bulk), else orchestrated, else
            # the mean of known route averages. Batch ETA = remaining × per-file (I7 heuristic).
            per_file: float | None = (
                avg_by_route.get("delegated")
                or avg_by_route.get("orchestrated")
                or (sum(avg_by_route.values()) / len(avg_by_route) if avg_by_route else None)
            )
            remaining = max(0, b_total - b_done)
            eta = int(round(per_file * remaining)) if per_file else None
            batch = QueueBatchProgress(
                running=bool(bp["running"]), done=b_done, total=b_total, eta_seconds=eta
            )
    except Exception:  # noqa: BLE001
        logger.debug("get_ingest_queue: ingest-all batch progress unavailable")

    # ── Marker batch progress (POST /ingest/convert-marker) ─────────────────
    marker_batch: QueueBatchProgress | None = None
    try:
        from app.marker_converter import get_marker_batch_progress  # noqa: PLC0415

        mb = get_marker_batch_progress()
        mb_total = int(mb["total"])
        mb_done = int(mb["done"])
        if mb["running"] or (mb_total > 0 and mb_done < mb_total):
            marker_batch = QueueBatchProgress(
                running=bool(mb["running"]),
                done=mb_done,
                total=mb_total,
                eta_seconds=mb.get("eta_seconds"),
            )
    except Exception:  # noqa: BLE001
        logger.debug("get_ingest_queue: marker batch progress unavailable")

    return QueueSnapshotResponse(
        paused=snap["paused"],
        pending=snap["pending"],
        processing=snap["processing"],
        failed=snap["failed"],
        completed_since_idle=snap["completed_since_idle"],
        total=snap["total"],
        tasks=tasks,
        batch=batch,
        marker_batch=marker_batch,
    )


@router.post(
    "/ingest/runs/{run_id}/cancel",
    response_model=QueueCancelResponse,
    status_code=202,
    summary="Request cancellation of an in-flight ingest run",
    description=(
        "Sets the cooperative cancel event for the run. The loop checks the event at the "
        "next iteration boundary (never mid-provider-call, I7/I6). Cascade-deletes any pages "
        "written so far (I1) once the boundary is reached. 202 = cancel requested. "
        "404 = run_id unknown. 409 = run already in a terminal state. (ADR-0046 §3/§6)"
    ),
    responses={
        202: {"description": "Cancel requested — cleanup happens asynchronously"},
        404: {"description": "run_id not found in the active queue"},
        409: {"description": "Run is already in a terminal state (completed/failed/cancelled)"},
    },
)
async def cancel_ingest_run(run_id: uuid.UUID) -> QueueCancelResponse:
    """POST /ingest/runs/{id}/cancel — request cooperative cancellation (ADR-0046 §3)."""
    from app.ingest.queue_manager import ingest_queue as _iq

    # Check if the run exists at all (active or recently failed/completed in DB)
    if _iq.is_run_active(run_id):
        cancelled = _iq.cancel(run_id)
        if not cancelled:
            # Should not happen given is_run_active check, but guard anyway
            raise HTTPException(status_code=409, detail="Run is not in a cancellable state")
        return QueueCancelResponse(run_id=str(run_id), status="cancelling", cleaned_pages=0)

    # Not in active map — check if it's a known failed entry
    failed_entry = _iq.find_failed_by_run_id(run_id)
    if failed_entry is not None:
        raise HTTPException(
            status_code=409,
            detail="Run is already in a terminal state and cannot be cancelled",
        )

    # Unknown run_id
    raise HTTPException(status_code=404, detail="run_id not found in the active queue")


@router.delete(
    "/ingest/{run_id}",
    summary="Cancel a queued or running ingest run (R13-3)",
    description=(
        "QUEUED (not yet started, in _pending): removes the run from the queue so it "
        "never starts. Returns 200 {'status': 'cancelled'}. "
        "RUNNING (in-flight): signals cooperative cancellation via cancel_event. "
        "Returns 202 {'status': 'cancelling'} — cleanup completes asynchronously. "
        "Already terminal (completed/failed/cancelled): 409. "
        "Unknown run_id: 404. (R13-3)"
    ),
    responses={
        200: {"description": "Run was queued and has been immediately cancelled"},
        202: {"description": "Cancel requested — pipeline will abort at next loop boundary"},
        404: {"description": "run_id not found"},
        409: {"description": "Run is already in a terminal state"},
    },
)
async def delete_ingest_run(run_id: uuid.UUID) -> JSONResponse:
    """DELETE /ingest/{run_id} — cancel queued or running run (R13-3)."""
    from app.ingest.queue_manager import ingest_queue as _iq

    # ── Case 1: QUEUED (pending, not yet dispatched) ──────────────────────────
    if _iq.is_run_pending(run_id):
        source_path = _iq.cancel_pending(run_id)
        if source_path is not None:
            # Write a cancelled ingest_runs row to preserve audit trail (R13-3).
            # Non-fatal: if the DB write fails we still return 200 (run is removed
            # from queue; it will never start regardless).
            try:
                from datetime import UTC

                from app.db import get_session as _get_session  # noqa: PLC0415
                from app.models import IngestRun as _IngestRun  # noqa: PLC0415

                now = datetime.now(UTC)
                async with _get_session() as _sess:
                    _sess.add(
                        _IngestRun(
                            id=run_id,
                            vault_id=settings.vault_id,
                            page_id=None,
                            provider_name="unknown",
                            provider_type="unknown",
                            model_id="unknown",
                            route="unknown",
                            max_iter_used=0,
                            total_tokens=0,
                            total_cost_usd=0,
                            converged=False,
                            cost_anomaly=False,
                            started_at=now,
                            finished_at=now,
                            pages_created=0,
                            status="cancelled",
                            error_message="Cancelled before dispatch",
                            source_path=source_path,
                            retry_count=0,
                        )
                    )
            except Exception:  # noqa: BLE001
                logger.debug("DELETE /ingest/%s: failed to write cancelled DB row", run_id)
        return JSONResponse(
            status_code=200,
            content={"status": "cancelled"},
        )

    # ── Case 2: RUNNING (in-flight) ───────────────────────────────────────────
    if _iq.is_run_active(run_id):
        cancelled = _iq.cancel(run_id)
        if not cancelled:
            # Guard: is_run_active was True but cancel failed — race on finalize
            raise HTTPException(status_code=409, detail="Run is not in a cancellable state")
        return JSONResponse(
            status_code=202,
            content={"status": "cancelling"},
        )

    # ── Case 3: TERMINAL — check DB for known completed/failed/cancelled rows ─
    # Use raw SQL to avoid Postgres-UUID type coercion issues in test environments.
    _run_status: str | None = None
    try:
        from sqlalchemy import text as _sa_text  # noqa: PLC0415

        async with _m.get_session() as _sess:
            _res = await _sess.execute(
                _sa_text("SELECT status FROM ingest_runs WHERE CAST(id AS TEXT) = :rid"),
                {"rid": str(run_id)},
            )
            _row = _res.fetchone()
            if _row is not None:
                _run_status = str(_row[0])
    except Exception:  # noqa: BLE001
        _run_status = None

    if _run_status is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Run is already in a terminal state: {_run_status}",
        )

    # Also check recent-failed in-memory (may not be in DB yet)
    failed_entry = _iq.find_failed_by_run_id(run_id)
    if failed_entry is not None:
        raise HTTPException(
            status_code=409,
            detail="Run is already in a terminal state and cannot be cancelled",
        )

    raise HTTPException(status_code=404, detail="run_id not found")


@router.post(
    "/ingest/runs/{run_id}/retry",
    response_model=QueueRetryResponse,
    status_code=202,
    summary="Retry a failed ingest run",
    description=(
        "Re-dispatches the source file for re-ingest, incrementing retry_count. "
        "Hard cap: 3 retries (I7). "
        "202 = re-dispatch accepted. "
        "404 = run_id unknown. "
        "409 detail='max_retries_exceeded' when retry_count >= 3. "
        "409 detail='not_retryable' when run is still active. (ADR-0046 §5/§6)"
    ),
    responses={
        202: {"description": "Retry dispatched"},
        404: {"description": "run_id unknown"},
        409: {"description": "max_retries_exceeded or run is not in a retryable state"},
    },
)
async def retry_ingest_run(run_id: uuid.UUID) -> QueueRetryResponse:
    """POST /ingest/runs/{id}/retry — re-dispatch a failed source file (ADR-0046 §5)."""
    from app.ingest.queue_manager import ingest_queue as _iq

    try:
        result = _iq.request_retry(run_id)
    except ValueError as exc:
        detail = str(exc)
        if detail == "max_retries_exceeded":
            raise HTTPException(status_code=409, detail="max_retries_exceeded") from exc
        if detail == "not_retryable":
            raise HTTPException(
                status_code=409,
                detail="Run is currently active and cannot be retried; cancel it first",
            ) from exc
        raise HTTPException(status_code=409, detail=detail) from exc

    if result is None:
        raise HTTPException(status_code=404, detail="run_id not found")

    source_path, new_retry_count = result
    return QueueRetryResponse(
        run_id_prev=str(run_id),
        source_path=source_path,
        retry_count=new_retry_count,
        status="queued",
    )


@router.post(
    "/ingest/queue/pause",
    response_model=QueuePauseResponse,
    status_code=200,
    summary="Pause the ingest queue",
    description=(
        "Pauses dispatch — new FS events are parked in memory until resume. "
        "Idempotent; calling while already paused is a no-op. (ADR-0046 §4)"
    ),
    responses={200: {"description": "Queue paused (idempotent)"}},
)
async def pause_ingest_queue() -> QueuePauseResponse:
    """POST /ingest/queue/pause — gate new dispatches (ADR-0046 §4)."""
    from app.ingest.queue_manager import ingest_queue as _iq

    _iq.pause()
    return QueuePauseResponse(paused=True)


@router.post(
    "/ingest/queue/resume",
    response_model=QueueResumeResponse,
    status_code=200,
    summary="Resume the ingest queue",
    description=(
        "Resumes dispatch and drains any parked pending events through the watcher's "
        "normal debounce path. Idempotent; calling while not paused replays any stale "
        "pending entries. (ADR-0046 §4)"
    ),
    responses={200: {"description": "Queue resumed; pending entries replayed"}},
)
async def resume_ingest_queue() -> QueueResumeResponse:
    """POST /ingest/queue/resume — drain pending entries (ADR-0046 §4)."""
    from app.ingest.queue_manager import ingest_queue as _iq

    drained = _iq.resume()
    return QueueResumeResponse(paused=False, drained=drained)
