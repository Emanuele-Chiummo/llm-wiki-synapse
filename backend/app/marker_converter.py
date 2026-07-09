"""
Async Marker PDF→markdown conversion manager [F12, I7].

Maintains in-process batch state for POST /ingest/convert-marker (W0 async rewrite).
The HTTP request handler validates + saves raw PDFs synchronously, then calls
``start_marker_batch()`` to fire a background ``asyncio.Task`` and return immediately.

Marker is a single-GPU service that returns 429 under concurrent load, so the driver
serialises all Marker calls (concurrency=1, I7). Per-file failures do NOT abort the
batch — the other files continue. The batch is single-flight: a 409 is returned if
one is already running.

Mirrors the ingest-all pattern from ``sources.py`` (module-level state + create_task).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Per-file entry ─────────────────────────────────────────────────────────────


@dataclass
class MarkerFileEntry:
    """Tracks one PDF file through the async conversion pipeline."""

    file: str  # original filename  e.g. "report.pdf"
    safe_stem: str  # sanitized stem  e.g. "report"
    pdf_abs_path: str  # absolute path to the already-written raw PDF
    status: str = "pending"  # "pending" | "converting" | "ok" | "failed"
    detail: str | None = None  # error message when status == "failed"
    companion_rel: str | None = None  # vault-relative path to .extracted.md (when ok)


# ── Batch ─────────────────────────────────────────────────────────────────────


@dataclass
class MarkerBatch:
    """One POST /ingest/convert-marker submission."""

    batch_id: uuid.UUID
    entries: list[MarkerFileEntry]
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def done(self) -> int:
        return sum(1 for e in self.entries if e.status in ("ok", "failed"))

    @property
    def running(self) -> bool:
        return self.finished_at is None


# ── Module-level state ─────────────────────────────────────────────────────────
# Only one batch may run at a time (single-GPU Marker service; single-flight guard).
# Mirrors _ingest_all_running / _ingest_all_done / _ingest_all_total in sources.py.

_current_batch: MarkerBatch | None = None
_current_task: asyncio.Task[None] | None = None


def is_running() -> bool:
    """Return True if a conversion batch is currently in progress."""
    return _current_batch is not None and _current_batch.running


def get_current_task() -> asyncio.Task[None] | None:
    """Return the background asyncio.Task.  Used by tests to await completion."""
    return _current_task


def _reset_state() -> None:
    """
    Reset module-level state to initial values.

    FOR TESTING ONLY — not exposed in production paths.  Call this in a pytest
    fixture (autouse=True, scope="function") to prevent state pollution across tests.
    """
    global _current_batch, _current_task
    _current_batch = None
    _current_task = None


def get_marker_batch_progress() -> dict[str, Any]:
    """
    Return a snapshot of the current (or most-recent) batch.

    Consumed by GET /ingest/queue (summary) and GET /ingest/convert-marker/status
    (per-file detail). No I/O — pure in-memory read.
    """
    if _current_batch is None:
        return {
            "running": False,
            "done": 0,
            "total": 0,
            "batch_id": None,
            "eta_seconds": None,
            "files": [],
        }

    batch = _current_batch
    elapsed = (datetime.now(UTC) - batch.started_at).total_seconds()
    done = batch.done
    total = batch.total

    # ETA: elapsed/done × remaining (only when at least one file is done).
    # After the batch finishes eta_seconds is always None (running=False → UI hides it).
    eta: int | None = None
    if done > 0 and batch.running:
        per_file_sec = elapsed / done
        remaining = max(0, total - done)
        eta = int(round(per_file_sec * remaining))

    return {
        "running": batch.running,
        "done": done,
        "total": total,
        "batch_id": str(batch.batch_id),
        "eta_seconds": eta,
        "files": [
            {
                "file": e.file,
                "safe_stem": e.safe_stem,
                "status": e.status,
                "detail": e.detail,
                "companion_path": e.companion_rel,
            }
            for e in batch.entries
        ],
    }


def start_marker_batch(
    entries: list[MarkerFileEntry],
    eff_marker_url: str,
    eff_marker_timeout: float,
    vault_root: Path,
) -> MarkerBatch:
    """
    Arm the batch state and fire the background driver as an asyncio.Task.

    Caller MUST check ``is_running()`` first and return 409 if True (single-flight).
    Returns the ``MarkerBatch`` immediately; background conversion runs concurrently.
    The ``eff_marker_url`` and ``eff_marker_timeout`` are captured at enqueue time so
    runtime config changes do not affect an in-flight batch.
    """
    global _current_batch, _current_task

    batch_id = uuid.uuid4()
    batch = MarkerBatch(batch_id=batch_id, entries=entries)
    _current_batch = batch

    coro = _marker_batch_driver(batch, eff_marker_url, eff_marker_timeout, vault_root)
    _current_task = asyncio.create_task(coro)

    logger.info("marker_converter: batch %s started — %d file(s) queued", batch_id, len(entries))
    return batch


# ── Background driver ─────────────────────────────────────────────────────────


async def _marker_batch_driver(
    batch: MarkerBatch,
    eff_marker_url: str,
    eff_marker_timeout: float,
    vault_root: Path,
) -> None:
    """
    Serial Marker conversion driver (concurrency=1 — single GPU, I7).

    For each entry in the batch:
      1. Set status → "converting"
      2. Re-read the PDF from disk (already persisted by the request handler)
      3. POST to Marker /convert with the MARKER_TIMEOUT_SECONDS bound (I7)
      4. On success: write <safe_stem>.extracted.md companion (I5 YAML frontmatter);
                     set status → "ok"; watcher picks it up (I1 — no extra ingest call)
      5. On failure: set status → "failed" with detail; continue with the next file

    Never raises. Always sets ``batch.finished_at`` in the finally block so
    ``batch.running`` becomes False and the single-flight guard is released.
    """
    import httpx  # noqa: PLC0415

    convert_url = f"{eff_marker_url.rstrip('/')}/convert"
    raw_sources = vault_root / "raw" / "sources"
    raw_sources.mkdir(parents=True, exist_ok=True)

    try:
        for entry in batch.entries:
            # ── Re-read PDF bytes from disk ──────────────────────────────────
            try:
                pdf_bytes = Path(entry.pdf_abs_path).read_bytes()
            except OSError as exc:
                entry.status = "failed"
                entry.detail = f"Cannot read PDF from disk: {exc}"
                logger.error("marker_converter: cannot read %s — %s", entry.pdf_abs_path, exc)
                continue

            # ── Call Marker /convert (I7: bounded by eff_marker_timeout) ────
            entry.status = "converting"
            logger.info("marker_converter: converting %s via %s", entry.file, convert_url)

            marker_ok = False
            marker_markdown = ""
            marker_detail = ""

            try:
                async with httpx.AsyncClient(timeout=eff_marker_timeout) as http:
                    response = await http.post(
                        convert_url,
                        files={"file": (entry.safe_stem + ".pdf", pdf_bytes, "application/pdf")},
                    )
                if response.status_code == 200:
                    data = response.json()
                    md = data.get("markdown")
                    if isinstance(md, str) and md:
                        marker_markdown = md
                        marker_ok = True
                    else:
                        marker_detail = "Marker returned invalid or empty markdown field."
                else:
                    marker_detail = (
                        f"Marker returned HTTP {response.status_code}: " f"{response.text[:200]}"
                    )
            except httpx.TimeoutException as exc:
                marker_detail = f"Marker request timed out after {eff_marker_timeout}s: {exc}"
            except (httpx.ConnectError, httpx.RequestError) as exc:
                marker_detail = f"Marker microservice unreachable ({type(exc).__name__}): {exc}"
            except Exception as exc:  # noqa: BLE001
                marker_detail = f"Unexpected Marker error: {exc}"

            if not marker_ok:
                entry.status = "failed"
                entry.detail = marker_detail
                logger.warning(
                    "marker_converter: %s — FAILED (not aborting batch): %s",
                    entry.file,
                    marker_detail,
                )
                continue

            # ── Write .extracted.md companion (I5 — YAML frontmatter) ───────
            companion_name = f"{entry.safe_stem}.extracted.md"
            companion_dst = raw_sources / companion_name
            pdf_dst = Path(entry.pdf_abs_path)
            raw_rel = str(pdf_dst.relative_to(vault_root))
            companion_content = (
                "---\n"
                f"type: source\n"
                f"title: {entry.safe_stem}\n"
                f'sources: ["{raw_rel}"]\n'
                "---\n\n" + marker_markdown
            )
            try:
                companion_dst.write_text(companion_content, encoding="utf-8")
                entry.companion_rel = str(companion_dst.relative_to(vault_root))
                entry.status = "ok"
                logger.info(
                    "marker_converter: %s → %s (%d chars) — watcher will ingest (I1)",
                    entry.file,
                    companion_name,
                    len(marker_markdown),
                )
            except OSError as exc:
                entry.status = "failed"
                entry.detail = f"Cannot write companion file: {exc}"
                logger.error("marker_converter: cannot write %s — %s", companion_dst, exc)

    finally:
        batch.finished_at = datetime.now(UTC)
        ok_count = sum(1 for e in batch.entries if e.status == "ok")
        fail_count = sum(1 for e in batch.entries if e.status == "failed")
        logger.info(
            "marker_converter: batch %s finished — %d ok / %d failed / %d total",
            batch.batch_id,
            ok_count,
            fail_count,
            len(batch.entries),
        )
