"""
Live ingest activity queue manager (ADR-0046 §3/§4/§5).

Singleton `ingest_queue` mirrors the `_watcher` singleton pattern (watcher.py:243).
All state mutations happen on the asyncio loop thread — no cross-await locks needed
(same contract as the watcher's _pending/_inflight/_dirty maps, watcher.py:65-76).

Responsibilities:
  • Track in-flight runs (RunHandle keyed by source_path AND run_id).
  • Gate new dispatches while paused (admit/park in _pending).
  • Provide cooperative cancel via cancel_event per run.
  • Cap retries at MAX_INGEST_RETRIES=3 (I7).
  • Suppress re-fire of cancelled paths for 2×WATCH_DEBOUNCE_SECONDS (I1).
  • Expose snapshot() for the GET /ingest/queue endpoint (pure in-memory, no DB scan).

This module is a STATUS MIRROR ONLY — it never enumerates the vault, never scans the
filesystem, and never calls the DB directly (I1 / ADR-0046 §3 last paragraph).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_INGEST_RETRIES: int = 3  # I7 hard cap (ADR-0046 §5)

# Cancel-suppression window: 2× the watcher debounce so the cascade_delete file
# mutations and any editor re-touch do not immediately re-queue (ADR-0046 §3).
_DEBOUNCE_SECONDS: float = float(os.environ.get("WATCH_DEBOUNCE_SECONDS", "1.5"))
_SUPPRESS_WINDOW: float = 2.0 * _DEBOUNCE_SECONDS


# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class RunHandle:
    """Tracks a single in-flight ingest run (ADR-0046 §3 `RunHandle`)."""

    run_id: uuid.UUID
    source_path: str  # absolute path — canonical queue key (ADR-0046 path-normalization fix)
    cancel_event: asyncio.Event
    written_page_ids: list[uuid.UUID] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    status: str = "running"  # "running" | "cancelling"
    # human-facing short key: "queued" | "analyzing" | "generating (N/M)" |
    # "validating" | "writing" | "agent running" — set by orchestrator before routing
    phase: str = "queued"
    route: str | None = None  # "orchestrated" | "delegated" — set by orchestrator before routing


@dataclass
class PendingEntry:
    """A FS event parked while the queue is paused (ADR-0046 §4)."""

    source_path: str  # absolute path — canonical queue key (ADR-0046 path-normalization fix)
    action: str  # "ingest" | "delete"
    first_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Pre-issued run_id so DELETE /ingest/{run_id} can target queued entries (R13-3).
    run_id: uuid.UUID = field(default_factory=uuid.uuid4)


@dataclass
class FailedEntry:
    """A recently-failed run retained for retry (ADR-0046 §4 `_recent_failed`)."""

    run_id: uuid.UUID
    source_path: str  # absolute path — canonical queue key (ADR-0046 path-normalization fix)
    error: str | None
    retry_count: int
    started_at: datetime | None


# ── Phase → progress mapping ──────────────────────────────────────────────────
# Coarse 0..1 progress values for the orchestrated route. Derived from the current
# phase string so the UI can show a determinate progress bar on orchestrated runs.
# Delegated route ("agent running") → None (indeterminate spinner, I6 — the CLI
# agent loop is opaque; a fake % would be misleading).
_PHASE_PROGRESS: dict[str, float] = {
    "queued": 0.0,
    "analyzing": 0.2,
    "validating": 0.8,
    "writing": 0.95,
}


def _phase_to_progress(phase: str) -> float | None:
    """
    Map a phase string to a coarse 0..1 progress value.

    "generating (N/M)" is handled by prefix match → 0.5 regardless of N/M.
    Unknown phases and delegated-route phases ("agent running") return None
    so the UI renders a spinner rather than a fake percentage.
    """
    if phase in _PHASE_PROGRESS:
        return _PHASE_PROGRESS[phase]
    if phase.startswith("generating"):
        return 0.5
    # "agent running", "failed", or any other opaque phase → indeterminate
    return None


# ── Queue manager ─────────────────────────────────────────────────────────────


class IngestQueueManager:
    """
    In-process singleton that tracks ingest queue state for the live activity panel.

    Thread-safety contract (same as watcher.py):
      All mutations happen on the asyncio event loop thread — either called from
      async code (already on the loop) or via call_soon_threadsafe from watchdog.
      No asyncio.Lock needed as long as NO mutation crosses an await point.
    """

    def __init__(self) -> None:
        # source_path → RunHandle (in-flight runs)
        self._active: dict[str, RunHandle] = {}
        # run_id → source_path reverse index (for cancel by run_id)
        self._run_id_to_path: dict[uuid.UUID, str] = {}

        # source_path → PendingEntry (parked while paused)
        self._pending: dict[str, PendingEntry] = {}
        # run_id → source_path reverse index for pending entries (R13-3)
        self._pending_by_run_id: dict[uuid.UUID, str] = {}

        # source_path → retries so far (cleared on success, I7)
        self._retry_counts: dict[str, int] = {}

        # source_path → FailedEntry (retained for retry, dropped on retry/success)
        self._recent_failed: dict[str, FailedEntry] = {}

        self._paused: bool = False
        self._completed_since_idle: int = 0

        # source_path → monotonic deadline (cancel suppression window)
        self._suppress: dict[str, float] = {}

        # Back-reference to the watcher handler (set by set_watcher_handler())
        self._watcher_handler: Any | None = None  # _MarkdownHandler

        # ── WS-C (ADR-0079): drain callback ──────────────────────────────────────
        # Called ONCE when the queue transitions to idle AND at least one run completed
        # since the last idle (llm_wiki parity: onQueueDrained, ingest-queue.ts:636).
        # Debounced with _drain_in_flight to prevent double-fire on rapid successive drains.
        self._on_drained: Callable[[], Awaitable[None]] | None = None
        self._drain_in_flight: bool = False

    # ── Watcher back-reference ─────────────────────────────────────────────────

    def set_watcher_handler(self, handler: Any) -> None:
        """Called from watcher.py after the handler is constructed (lifespan, main.py)."""
        self._watcher_handler = handler

    # ── Drain callback (WS-C, ADR-0079) ───────────────────────────────────────

    def set_on_drained(self, callback: Callable[[], Awaitable[None]] | None) -> None:
        """
        Register (or clear) the async callback to invoke once when the queue drains.

        The callback is called as a fire-and-forget asyncio.Task on the idle transition
        (both _active and _pending empty AND _completed_since_idle > 0). A second rapid
        drain while the task is still running is silently skipped (_drain_in_flight guard).
        Exceptions inside the callback are caught and logged — they never propagate back.

        Called from main.py lifespan (ADR-0079): registers sweep_reviews(vault_id) so the
        review sweep runs once per queue drain rather than after every ingest run.
        """
        self._on_drained = callback

    # ── Run lifecycle ──────────────────────────────────────────────────────────

    def open_run(self, run_id: uuid.UUID, source_path: str) -> RunHandle:
        """
        Register a new in-flight run.  Called from _open_ingest_run in orchestrator.py
        BEFORE the route try-block (ADR-0046 §2).

        Returns the RunHandle so the orchestrator can pass cancel_event down the call stack.
        """
        handle = RunHandle(
            run_id=run_id,
            source_path=source_path,
            cancel_event=asyncio.Event(),
            started_at=datetime.now(UTC),
        )
        self._active[source_path] = handle
        self._run_id_to_path[run_id] = source_path
        # A previously-failed entry is superseded by the new run
        self._recent_failed.pop(source_path, None)
        logger.debug("queue: open run_id=%s path=%s", run_id, source_path)
        return handle

    def record_written(self, run_id: uuid.UUID, page_id: uuid.UUID) -> None:
        """
        Append a page_id to the run's written list.  Called from orchestrator.py
        after each successful write_wiki_page() so cancel can clean up (ADR-0046 §3).
        """
        path = self._run_id_to_path.get(run_id)
        if path is None:
            return
        handle = self._active.get(path)
        if handle is not None:
            handle.written_page_ids.append(page_id)

    def finalize(
        self,
        run_id: uuid.UUID,
        status: str,
        error: str | None = None,
    ) -> None:
        """
        Mark a run terminal and remove it from _active (ADR-0046 §2).

        status: "completed" | "failed" | "converged_false" | "cancelled"

        On success: bump _completed_since_idle; clear _retry_counts for this path.
        On failure/cancelled: retain as FailedEntry in _recent_failed.
        When both _active and _pending empty after this call: do NOT reset
        _completed_since_idle (the ADR says reset on idle TRANSITION — we just let
        snapshot() return the accumulated count until the queue goes idle again
        from a subsequent admit→finalize cycle with zero remaining).
        """
        path = self._run_id_to_path.pop(run_id, None)
        if path is None:
            logger.debug("queue: finalize called for unknown run_id=%s", run_id)
            return
        handle = self._active.pop(path, None)

        is_success = status in ("completed", "converged_false")

        if is_success:
            self._completed_since_idle += 1
            self._retry_counts.pop(path, None)
            logger.debug(
                "queue: finalize OK run_id=%s status=%s completed_since_idle=%d",
                run_id,
                status,
                self._completed_since_idle,
            )
        else:
            # Retain for retry visibility
            started_at = handle.started_at if handle is not None else None
            retry_count = self._retry_counts.get(path, 0)
            self._recent_failed[path] = FailedEntry(
                run_id=run_id,
                source_path=path,
                error=error,
                retry_count=retry_count,
                started_at=started_at,
            )
            logger.debug(
                "queue: finalize FAIL run_id=%s status=%s error=%s",
                run_id,
                status,
                error,
            )

        # If the queue transitions to idle, reset completed_since_idle and (WS-C)
        # fire the drain callback if work happened since last idle (ADR-0079).
        if not self._active and not self._pending:
            count = self._completed_since_idle
            logger.debug(
                "queue: idle — reset completed_since_idle from %d to 0",
                count,
            )
            self._completed_since_idle = 0

            # ── WS-C (ADR-0079): on_drained callback — llm_wiki parity ─────────
            # Schedule callback only when: work happened (count > 0), a callback is
            # registered, and no prior drain task is still in flight (debounce).
            if count > 0 and self._on_drained is not None and not self._drain_in_flight:
                _cb = self._on_drained
                _self_ref = self

                async def _fire_drain(
                    _cb: Callable[[], Awaitable[None]] = _cb,
                    _ref: IngestQueueManager = _self_ref,
                ) -> None:
                    try:
                        await _cb()
                    except Exception as _exc:  # noqa: BLE001
                        logger.warning("queue: on_drained callback failed (non-fatal): %s", _exc)
                    finally:
                        _ref._drain_in_flight = False

                self._drain_in_flight = True
                try:
                    asyncio.create_task(_fire_drain())
                except RuntimeError:
                    # No running event loop (test env without asyncio.run) — skip silently.
                    self._drain_in_flight = False
                    logger.debug("queue: on_drained skipped — no running event loop")

    # ── Cancel ─────────────────────────────────────────────────────────────────

    def cancel(self, run_id: uuid.UUID) -> bool:
        """
        Request cancellation of an in-flight run (ADR-0046 §3).

        Sets the cancel_event; the orchestrated loop checks it at the top of each
        iteration (loop.py). Returns True if the run was found and in flight.
        """
        path = self._run_id_to_path.get(run_id)
        if path is None:
            return False
        handle = self._active.get(path)
        if handle is None:
            return False
        handle.cancel_event.set()
        handle.status = "cancelling"
        logger.info("queue: cancel requested run_id=%s path=%s", run_id, path)
        # Arm the suppress window so cascade_delete mutations don't re-trigger ingest
        self._suppress[path] = time.monotonic() + _SUPPRESS_WINDOW
        return True

    def cancel_pending(self, run_id: uuid.UUID) -> str | None:
        """
        Remove a pending (not-yet-started) entry from the queue (R13-3).

        Returns the source_path if the entry was found and removed; None otherwise.
        This prevents the pending entry from ever being dispatched when the queue
        is resumed. The caller (DELETE /ingest/{run_id}) is responsible for writing
        the cancelled ingest_runs row to the DB if desired.
        """
        path = self._pending_by_run_id.pop(run_id, None)
        if path is None:
            return None
        self._pending.pop(path, None)
        logger.info("queue: cancel_pending run_id=%s path=%s", run_id, path)
        return path

    def get_cancel_event(self, run_id: uuid.UUID) -> asyncio.Event | None:
        """Return the cancel_event for a run, or None if not found."""
        path = self._run_id_to_path.get(run_id)
        if path is None:
            return None
        handle = self._active.get(path)
        return handle.cancel_event if handle is not None else None

    def get_handle(self, run_id: uuid.UUID) -> RunHandle | None:
        """Return the RunHandle for a run_id, or None."""
        path = self._run_id_to_path.get(run_id)
        if path is None:
            return None
        return self._active.get(path)

    def set_phase(self, run_id: uuid.UUID, phase: str) -> None:
        """
        Update the human-facing phase string on the RunHandle (no-op if run absent).

        Phase strings (short keys):
          "queued"              — registered but not yet started
          "analyzing"           — provider.analyze() in progress
          "generating (N/M)"   — provider.generate() iteration N of M
          "validating"          — validate_pages() running
          "writing"             — write_wiki_page() loop
          "agent running"       — delegated/CLI agent loop (opaque, I6)
        Called from the orchestrated loop (via on_phase callback) and the orchestrator
        delegated branch. The phase is surfaced in GET /ingest/queue task items.
        """
        path = self._run_id_to_path.get(run_id)
        if path is None:
            return
        handle = self._active.get(path)
        if handle is not None:
            handle.phase = phase

    def set_route(self, run_id: uuid.UUID, route: str) -> None:
        """
        Store the resolved route on the RunHandle so snapshot() can derive the ETA
        without an extra DB look-up. No-op if run is absent.
        """
        path = self._run_id_to_path.get(run_id)
        if path is None:
            return
        handle = self._active.get(path)
        if handle is not None:
            handle.route = route

    # ── Retry ──────────────────────────────────────────────────────────────────

    def request_retry(self, run_id: uuid.UUID) -> tuple[str, int] | None:
        """
        Increment retry counter and re-dispatch the failed source_path (ADR-0046 §5).

        Returns (abs_source_path, new_retry_count) on success.
        Returns None if run_id unknown.
        Raises ValueError("max_retries_exceeded") if count >= MAX_INGEST_RETRIES.
        Raises ValueError("not_retryable") if run is not in _recent_failed (e.g. still running).
        """
        # Find entry — either in _recent_failed (normal path) or active (sanity)
        source_path: str | None = None

        # Check failed first
        for _path, entry in self._recent_failed.items():
            if entry.run_id == run_id:
                source_path = _path
                break

        if source_path is None:
            # Also check _run_id_to_path (in case still active)
            if run_id in self._run_id_to_path:
                raise ValueError("not_retryable")
            return None  # unknown run_id

        current_count = self._retry_counts.get(source_path, 0)
        if current_count >= MAX_INGEST_RETRIES:
            raise ValueError("max_retries_exceeded")

        new_count = current_count + 1
        self._retry_counts[source_path] = new_count
        self._recent_failed.pop(source_path, None)

        # Re-dispatch via the watcher _arm seam
        if self._watcher_handler is not None:
            self._watcher_handler._arm(source_path, "ingest")
            logger.info(
                "queue: retry dispatched run_id=%s path=%s retry_count=%d",
                run_id,
                source_path,
                new_count,
            )
        else:
            # Watcher not yet attached (test environment or early startup)
            logger.warning(
                "queue: retry — watcher handler not set; cannot re-dispatch path=%s",
                source_path,
            )

        return source_path, new_count

    def get_retry_count(self, source_path: str) -> int:
        """Return the current retry count for a source_path (0 if absent)."""
        return self._retry_counts.get(source_path, 0)

    # ── Pause / resume ─────────────────────────────────────────────────────────

    def pause(self) -> None:
        """Pause dispatch — new FS events are parked in _pending (ADR-0046 §4)."""
        if not self._paused:
            self._paused = True
            logger.info("queue: paused")

    def resume(self) -> int:
        """
        Resume dispatch — drain _pending by calling the watcher's _arm seam
        (ADR-0046 §4). Returns the number of pending entries replayed.
        """
        if self._paused:
            self._paused = False
        pending = dict(self._pending)
        self._pending.clear()
        self._pending_by_run_id.clear()
        count = 0
        for _path, entry in pending.items():
            if self._watcher_handler is not None:
                self._watcher_handler._arm(entry.source_path, entry.action)
                count += 1
            else:
                logger.warning(
                    "queue: resume — watcher handler not set; cannot replay path=%s",
                    entry.source_path,
                )
        if count:
            logger.info("queue: resumed, replayed %d pending entries", count)
        return count

    # ── Admit / suppress ───────────────────────────────────────────────────────

    def admit(self, path: str, action: str) -> bool:
        """
        Called from watcher._fire() before dispatching an event (ADR-0046 §4).

        Returns True  → proceed with dispatch (queue is not paused).
        Returns False → path is parked in _pending; watcher must NOT dispatch.
        """
        if not self._paused:
            return True
        # Paused: park the event; last-writer-wins on duplicate paths.
        # If we're replacing an existing pending entry, remove its old run_id from the
        # reverse index first so the stale run_id doesn't linger (R13-3).
        old_entry = self._pending.get(path)
        if old_entry is not None:
            self._pending_by_run_id.pop(old_entry.run_id, None)
        entry = PendingEntry(source_path=path, action=action)
        self._pending[path] = entry
        self._pending_by_run_id[entry.run_id] = path
        logger.debug(
            "queue: admit parked (paused) path=%s action=%s run_id=%s",
            path,
            action,
            entry.run_id,
        )
        return False

    def should_skip(self, path: str) -> bool:
        """
        Return True if *path* is in the cancel-suppression window (ADR-0046 §3).

        The watcher's _fire() checks this BEFORE dispatching. If True, the event
        is dropped silently; the suppression entry is also cleared since it fired.
        """
        deadline = self._suppress.get(path)
        if deadline is None:
            return False
        now = time.monotonic()
        if now < deadline:
            logger.debug("queue: suppress hit for path=%s (%.2fs remaining)", path, deadline - now)
            return True
        # Window expired — clear and allow
        self._suppress.pop(path, None)
        return False

    # ── Snapshot (GET /ingest/queue) ───────────────────────────────────────────

    def snapshot(self, avg_duration_by_route: dict[str, float] | None = None) -> dict[str, Any]:
        """
        Pure in-memory summary for GET /ingest/queue (ADR-0046 §6, no DB scan).

        task `status` values returned:
          "processing"  — in _active (running or cancelling)
          "pending"     — parked in _pending (paused queue)
          "failed"      — in _recent_failed

        avg_duration_by_route: optional dict mapping route ("orchestrated"|"delegated") →
          average completed-run duration in seconds (from ingest_runs history). When supplied,
          eta_seconds is computed for active tasks as max(0, avg - elapsed). None means no
          history available for that route → eta_seconds stays None. The endpoint (main.py)
          performs the DB query and passes the result in; snapshot() stays DB-free (I1).
        """
        now_utc = datetime.now(UTC)
        avg_by_route: dict[str, float] = avg_duration_by_route or {}

        tasks: list[dict[str, Any]] = []

        for source_path, handle in self._active.items():
            display = self._display_path(source_path)
            filename = Path(source_path).name
            elapsed = (now_utc - handle.started_at).total_seconds()
            progress = _phase_to_progress(handle.phase)
            route = handle.route
            eta: int | None = None
            if route is not None and route in avg_by_route:
                avg = avg_by_route[route]
                eta = max(0, round(avg - elapsed))
            tasks.append(
                {
                    "run_id": str(handle.run_id),
                    "source_path": display,
                    "filename": filename,
                    "status": "processing",
                    "retry_count": self._retry_counts.get(source_path, 0),
                    "error": None,
                    "started_at": handle.started_at.isoformat(),
                    "phase": handle.phase,
                    "progress": progress,
                    "elapsed_seconds": round(elapsed),
                    "eta_seconds": eta,
                }
            )

        for source_path, _entry in self._pending.items():
            display = self._display_path(source_path)
            filename = Path(source_path).name
            tasks.append(
                {
                    "run_id": str(_entry.run_id),
                    "source_path": display,
                    "filename": filename,
                    "status": "pending",
                    "retry_count": self._retry_counts.get(source_path, 0),
                    "error": None,
                    "started_at": None,
                    "phase": "queued",
                    "progress": 0.0,
                    "elapsed_seconds": None,
                    "eta_seconds": None,
                }
            )

        for source_path, entry in self._recent_failed.items():
            display = self._display_path(source_path)
            filename = Path(source_path).name
            tasks.append(
                {
                    "run_id": str(entry.run_id),
                    "source_path": display,
                    "filename": filename,
                    "status": "failed",
                    "retry_count": entry.retry_count,
                    "error": entry.error,
                    "started_at": entry.started_at.isoformat() if entry.started_at else None,
                    "phase": "failed",
                    "progress": None,
                    "elapsed_seconds": None,
                    "eta_seconds": None,
                }
            )

        return {
            "paused": self._paused,
            "pending": len(self._pending),
            "processing": len(self._active),
            "failed": len(self._recent_failed),
            "completed_since_idle": self._completed_since_idle,
            "total": len(self._pending) + len(self._active) + len(self._recent_failed),
            "tasks": tasks,
        }

    # ── Utilities ──────────────────────────────────────────────────────────────

    @staticmethod
    def _display_path(abs_path: str) -> str:
        """
        Convert an absolute internal path to a clean relative display form for the UI.

        Internal keys are always absolute (ADR-0046 path-normalization fix). The UI
        subtitle must show a readable ``raw/sources/...`` form, not ``/vault/raw/...``.

        Strategy: find the ``raw/sources/`` marker and take the suffix; fall back to
        ``Path(p).name`` if the marker is absent (e.g. a non-standard path).
        """
        marker = "raw/sources/"
        idx = abs_path.find(marker)
        if idx != -1:
            return abs_path[idx:]
        return Path(abs_path).name

    def find_failed_by_run_id(self, run_id: uuid.UUID) -> FailedEntry | None:
        """Look up a FailedEntry by its run_id (O(n) scan; list is short)."""
        for entry in self._recent_failed.values():
            if entry.run_id == run_id:
                return entry
        return None

    def is_run_active(self, run_id: uuid.UUID) -> bool:
        """Return True if run_id is currently in _active."""
        return run_id in self._run_id_to_path

    def is_run_pending(self, run_id: uuid.UUID) -> bool:
        """Return True if run_id is in _pending (queued but not yet started). (R13-3)"""
        return run_id in self._pending_by_run_id


# ── Module-level singleton ─────────────────────────────────────────────────────

ingest_queue: IngestQueueManager = IngestQueueManager()
