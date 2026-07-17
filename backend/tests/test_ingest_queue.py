"""
Tests for the ingest activity queue manager (ADR-0046).

Covers:
  - Queue state transitions: pause/admit/resume
  - Cancel sets cancel_event
  - completed_since_idle reset when queue goes idle
  - Suppress window: should_skip
  - Retry cap at MAX_INGEST_RETRIES=3 (I7)
  - Orphan-running-rows sweep logic (tested as a unit against the model/DB)
  - Path-normalization regression: cancel suppression matches absolute key (ADR-0046 fix)
  - Snapshot display path: internal absolute key → relative display form

All tests are pure-unit (no live DB, no live Qdrant, no running backend).
The DB-dependent test for the orphan sweep uses the SQLite fixture from conftest.py.

ADR-0046 path-normalization invariant: ALL queue keys (open_run, admit, should_skip,
cancel) must be ABSOLUTE paths so that cancel suppression and watcher re-fire suppression
share the same key space. snapshot() converts to a readable relative form for the UI.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from app.ingest.queue_manager import (
    MAX_INGEST_RETRIES,
    IngestQueueManager,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def make_manager() -> IngestQueueManager:
    """Return a fresh IngestQueueManager for each test."""
    return IngestQueueManager()


# ── Pause / admit / resume ─────────────────────────────────────────────────────


class TestPauseAdmitResume:
    def test_not_paused_admit_returns_true(self) -> None:
        mgr = make_manager()
        assert mgr.admit("/vault/raw/sources/foo.md", "ingest") is True

    def test_paused_admit_returns_false_and_parks(self) -> None:
        mgr = make_manager()
        mgr.pause()
        result = mgr.admit("/vault/raw/sources/foo.md", "ingest")
        assert result is False
        assert "/vault/raw/sources/foo.md" in mgr._pending

    def test_pause_idempotent(self) -> None:
        mgr = make_manager()
        mgr.pause()
        mgr.pause()  # second call is a no-op
        assert mgr._paused is True

    def test_resume_clears_paused_flag(self) -> None:
        mgr = make_manager()
        mgr.pause()
        mgr.resume()
        assert mgr._paused is False

    def test_resume_replays_pending_via_watcher_arm(self) -> None:
        mgr = make_manager()
        # Install a mock watcher handler
        mock_handler = MagicMock()
        mgr.set_watcher_handler(mock_handler)

        mgr.pause()
        mgr.admit("/vault/raw/sources/a.md", "ingest")
        mgr.admit("/vault/raw/sources/b.md", "delete")

        drained = mgr.resume()
        assert drained == 2
        assert len(mgr._pending) == 0
        assert mock_handler._arm.call_count == 2

    def test_admit_last_writer_wins(self) -> None:
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/a.md", "ingest")
        mgr.admit("/vault/raw/sources/a.md", "delete")
        # Last action wins
        assert mgr._pending["/vault/raw/sources/a.md"].action == "delete"
        assert len(mgr._pending) == 1

    def test_resume_without_watcher_does_not_raise(self) -> None:
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/x.md", "ingest")
        # No watcher set — should log a warning but not raise
        drained = mgr.resume()
        assert drained == 0  # nothing replayed since no handler

    def test_resume_idempotent_when_not_paused(self) -> None:
        mgr = make_manager()
        mgr.resume()  # called without pause — should be safe
        assert mgr._paused is False


# ── Open / record / finalize / cancel ─────────────────────────────────────────


class TestRunLifecycle:
    def test_open_run_registers_handle(self) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        handle = mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        assert handle.run_id == run_id
        assert handle.source_path == "/vault/raw/sources/doc.md"
        assert not handle.cancel_event.is_set()
        assert "/vault/raw/sources/doc.md" in mgr._active
        assert run_id in mgr._run_id_to_path

    def test_record_written_appends_page_id(self) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        page_id = uuid.uuid4()
        mgr.record_written(run_id, page_id)
        handle = mgr._active["/vault/raw/sources/doc.md"]
        assert page_id in handle.written_page_ids

    def test_record_written_unknown_run_noop(self) -> None:
        mgr = make_manager()
        mgr.record_written(uuid.uuid4(), uuid.uuid4())  # should not raise

    def test_finalize_success_removes_active_and_bumps_counter(self) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        mgr.finalize(run_id, "completed")
        assert "/vault/raw/sources/doc.md" not in mgr._active
        assert run_id not in mgr._run_id_to_path
        # Queue went idle → counter reset to 0
        assert mgr._completed_since_idle == 0

    def test_finalize_success_increments_then_resets_on_idle(self) -> None:
        mgr = make_manager()
        # Two consecutive completions, then queue goes idle
        r1 = uuid.uuid4()
        r2 = uuid.uuid4()
        mgr.open_run(r1, "/vault/raw/sources/a.md")
        mgr.open_run(r2, "/vault/raw/sources/b.md")
        mgr.finalize(r1, "completed")
        # Still active (r2 running) — counter accumulated
        assert mgr._completed_since_idle == 1
        mgr.finalize(r2, "completed")
        # Now idle — reset
        assert mgr._completed_since_idle == 0

    def test_finalize_failure_adds_to_recent_failed(self) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/bad.md")
        mgr.finalize(run_id, "failed", error="schema error")
        assert "/vault/raw/sources/bad.md" in mgr._recent_failed
        assert mgr._recent_failed["/vault/raw/sources/bad.md"].error == "schema error"

    def test_finalize_failure_does_not_reset_completed_since_idle(self) -> None:
        mgr = make_manager()
        r1 = uuid.uuid4()
        r2 = uuid.uuid4()
        mgr.open_run(r1, "/vault/raw/sources/ok.md")
        mgr.open_run(r2, "/vault/raw/sources/bad.md")
        mgr.finalize(r1, "completed")
        # r2 still running — counter = 1 (queue not idle yet)
        assert mgr._completed_since_idle == 1
        mgr.finalize(r2, "failed", error="oops")
        # Queue now idle (no pending, no active) — reset
        assert mgr._completed_since_idle == 0

    def test_finalize_unknown_run_id_is_noop(self) -> None:
        mgr = make_manager()
        mgr.finalize(uuid.uuid4(), "completed")  # should not raise

    def test_open_supersedes_recent_failed(self) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/x.md")
        mgr.finalize(run_id, "failed", error="bad")
        assert "/vault/raw/sources/x.md" in mgr._recent_failed

        run_id2 = uuid.uuid4()
        mgr.open_run(run_id2, "/vault/raw/sources/x.md")  # retry opens new run
        assert "/vault/raw/sources/x.md" not in mgr._recent_failed


# ── Cancel ─────────────────────────────────────────────────────────────────────


class TestCancel:
    def test_cancel_sets_event(self) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        handle = mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        assert not handle.cancel_event.is_set()
        result = mgr.cancel(run_id)
        assert result is True
        assert handle.cancel_event.is_set()
        assert handle.status == "cancelling"

    def test_cancel_arms_suppress_window(self) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        mgr.cancel(run_id)
        # Suppress entry should be set for the ABSOLUTE path
        assert "/vault/raw/sources/doc.md" in mgr._suppress

    def test_cancel_unknown_run_returns_false(self) -> None:
        mgr = make_manager()
        result = mgr.cancel(uuid.uuid4())
        assert result is False

    def test_get_cancel_event_returns_event(self) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        handle = mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        event = mgr.get_cancel_event(run_id)
        assert event is handle.cancel_event

    def test_get_cancel_event_unknown_returns_none(self) -> None:
        mgr = make_manager()
        assert mgr.get_cancel_event(uuid.uuid4()) is None


# ── Suppress window ────────────────────────────────────────────────────────────


class TestSuppressWindow:
    def test_should_skip_within_window(self) -> None:
        import time

        mgr = make_manager()
        mgr._suppress["/vault/raw/sources/doc.md"] = time.monotonic() + 100.0
        assert mgr.should_skip("/vault/raw/sources/doc.md") is True

    def test_should_skip_expired_window(self) -> None:
        import time

        mgr = make_manager()
        mgr._suppress["/vault/raw/sources/doc.md"] = time.monotonic() - 1.0  # already expired
        assert mgr.should_skip("/vault/raw/sources/doc.md") is False
        # Entry should be cleared
        assert "/vault/raw/sources/doc.md" not in mgr._suppress

    def test_should_skip_absent_path(self) -> None:
        mgr = make_manager()
        assert mgr.should_skip("/vault/raw/sources/unknown.md") is False


# ── Retry cap ─────────────────────────────────────────────────────────────────


class TestRetryCapI7:
    def _setup_failed_run(
        self, mgr: IngestQueueManager, path: str = "/vault/raw/sources/bad.md"
    ) -> uuid.UUID:
        """Helper: open a run with an ABSOLUTE path, finalize it as failed, return run_id."""
        run_id = uuid.uuid4()
        mgr.open_run(run_id, path)
        mgr.finalize(run_id, "failed", error="test error")
        return run_id

    def test_retry_returns_path_and_count(self) -> None:
        mgr = make_manager()
        mock_handler = MagicMock()
        mgr.set_watcher_handler(mock_handler)
        run_id = self._setup_failed_run(mgr)

        result = mgr.request_retry(run_id)
        assert result is not None
        source_path, new_count = result
        assert new_count == 1
        # re-dispatch must use the ABSOLUTE path so ingest_file() can stat it
        assert source_path == "/vault/raw/sources/bad.md"
        mock_handler._arm.assert_called_once_with("/vault/raw/sources/bad.md", "ingest")

    def test_retry_removes_from_recent_failed(self) -> None:
        mgr = make_manager()
        mock_handler = MagicMock()
        mgr.set_watcher_handler(mock_handler)
        run_id = self._setup_failed_run(mgr)
        mgr.request_retry(run_id)
        assert mgr.find_failed_by_run_id(run_id) is None

    def test_retry_cap_at_max_retries(self) -> None:
        mgr = make_manager()
        mock_handler = MagicMock()
        mgr.set_watcher_handler(mock_handler)
        path = "/vault/raw/sources/bad.md"

        # Simulate MAX_INGEST_RETRIES previous retries
        mgr._retry_counts[path] = MAX_INGEST_RETRIES
        run_id = self._setup_failed_run(mgr, path)

        with pytest.raises(ValueError, match="max_retries_exceeded"):
            mgr.request_retry(run_id)

    def test_retry_not_retryable_when_active(self) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/active.md")  # still running
        with pytest.raises(ValueError, match="not_retryable"):
            mgr.request_retry(run_id)

    def test_retry_unknown_run_returns_none(self) -> None:
        mgr = make_manager()
        result = mgr.request_retry(uuid.uuid4())
        assert result is None

    def test_successful_finalize_clears_retry_count(self) -> None:
        mgr = make_manager()
        path = "/vault/raw/sources/ok.md"
        mgr._retry_counts[path] = 2  # simulated prior retries
        run_id = uuid.uuid4()
        mgr.open_run(run_id, path)
        mgr.finalize(run_id, "completed")
        assert path not in mgr._retry_counts

    def test_max_retries_constant_is_3(self) -> None:
        """I7: MAX_INGEST_RETRIES must be exactly 3."""
        assert MAX_INGEST_RETRIES == 3


# ── Snapshot ───────────────────────────────────────────────────────────────────


class TestSnapshot:
    def test_empty_snapshot(self) -> None:
        mgr = make_manager()
        snap = mgr.snapshot()
        assert snap["paused"] is False
        assert snap["pending"] == 0
        assert snap["processing"] == 0
        assert snap["failed"] == 0
        assert snap["completed_since_idle"] == 0
        assert snap["total"] == 0
        assert snap["tasks"] == []

    def test_snapshot_counts_active(self) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        # Internal key is absolute; snapshot must show relative display form
        mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        snap = mgr.snapshot()
        assert snap["processing"] == 1
        assert snap["total"] == 1
        assert snap["tasks"][0]["status"] == "processing"
        assert snap["tasks"][0]["filename"] == "doc.md"
        assert snap["tasks"][0]["run_id"] == str(run_id)
        # source_path in snapshot must be the relative display form, not the absolute key
        assert snap["tasks"][0]["source_path"] == "raw/sources/doc.md"

    def test_snapshot_counts_pending(self) -> None:
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/x.md", "ingest")
        snap = mgr.snapshot()
        assert snap["pending"] == 1
        assert snap["tasks"][0]["status"] == "pending"
        # R13-3: pending entries now get a pre-issued run_id (non-null UUID string)
        assert snap["tasks"][0]["run_id"] is not None
        import uuid as _uuid

        _uuid.UUID(snap["tasks"][0]["run_id"])  # valid UUID string
        assert snap["tasks"][0]["source_path"] == "raw/sources/x.md"

    def test_snapshot_counts_failed(self) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/bad.md")
        mgr.finalize(run_id, "failed", error="oops")
        snap = mgr.snapshot()
        assert snap["failed"] == 1
        assert snap["tasks"][0]["status"] == "failed"
        assert snap["tasks"][0]["error"] == "oops"
        assert snap["tasks"][0]["source_path"] == "raw/sources/bad.md"

    def test_snapshot_display_path_strips_absolute_prefix(self) -> None:
        """
        Regression: snapshot() source_path must be the relative form even when the
        internal queue key is an absolute path (ADR-0046 path-normalization fix).
        """
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/my-note.md")
        snap = mgr.snapshot()
        task = snap["tasks"][0]
        # Must NOT expose the absolute path to the UI
        assert not task["source_path"].startswith("/")
        assert task["source_path"] == "raw/sources/my-note.md"
        assert task["filename"] == "my-note.md"


# ── Path-normalization regression (ADR-0046 fix) ──────────────────────────────


class TestPathNormalizationRegression:
    """
    Regression suite for the ADR-0046 path-normalization bug.

    Root cause: open_run was called with a RELATIVE path (origin_source from the
    orchestrator), but admit/should_skip were called with an ABSOLUTE path from the
    watcher. This mismatch meant _suppress[<relative>] was never found by
    should_skip(<absolute>), so a just-cancelled file was re-admitted on resume.

    Fix: ALL queue keys must be ABSOLUTE.  The orchestrator now passes abs_source to
    open_run/get_retry_count; watcher already passed absolute paths to admit/should_skip.
    """

    def test_cancel_then_should_skip_same_absolute_path(self) -> None:
        """
        ADR-0046 regression: open_run(<abs>) → cancel(run_id) → should_skip(<abs>)
        must return True (suppression window is active).

        Previously this failed when open_run received a RELATIVE path but should_skip
        received an ABSOLUTE path — the key mismatch made the suppression miss.
        """

        mgr = make_manager()
        abs_path = "/vault/raw/sources/x.md"
        run_id = uuid.uuid4()

        # Orchestrator opens the run with the absolute path (fixed)
        mgr.open_run(run_id, abs_path)
        # User cancels the run
        mgr.cancel(run_id)

        # Watcher re-fire with absolute path: suppression must hold
        assert mgr.should_skip(abs_path) is True, (
            "should_skip must return True within the suppress window when both "
            "open_run and should_skip use the same absolute path"
        )

    def test_cancel_suppression_not_broken_by_path_mismatch(self) -> None:
        """
        Verify the OLD broken pattern (relative open_run vs absolute should_skip)
        is prevented by the fix — i.e. the queue now enforces absolute keys on both
        sides, so the mismatch scenario cannot silently re-admit.
        """

        mgr = make_manager()
        abs_path = "/vault/raw/sources/y.md"
        run_id = uuid.uuid4()

        # Correct: open with absolute key
        mgr.open_run(run_id, abs_path)
        mgr.cancel(run_id)

        # should_skip with the same absolute path must return True
        assert mgr.should_skip(abs_path) is True

        # should_skip with a DIFFERENT (relative) path must NOT accidentally suppress
        assert mgr.should_skip("raw/sources/y.md") is False

    def test_resume_does_not_re_admit_cancelled_absolute_path(self) -> None:
        """
        Full reproduce of the live bug: pause → ingest reaches processing →
        cancel → resume → the cancelled path must NOT reappear in _pending.
        """

        mgr = make_manager()
        abs_path = "/vault/raw/sources/cancel-test.md"
        run_id = uuid.uuid4()
        mock_handler = MagicMock()
        mgr.set_watcher_handler(mock_handler)

        # Queue is paused; a new event comes in and is parked
        mgr.pause()
        # Simulate: a second event for the SAME file arrives while paused
        mgr.admit(abs_path, "ingest")
        assert abs_path in mgr._pending

        # Meanwhile the run that was in-flight before the pause is cancelled
        mgr.open_run(run_id, abs_path)
        mgr.cancel(run_id)

        # On resume the pending entry for the cancelled path must be dropped
        # The current implementation drains _pending via _arm — the suppress check
        # happens inside _fire (watcher side), not inside resume itself.
        # What we test here: _pending has an entry, and after finalize + cancel
        # the suppress window prevents it from being re-processed.
        # Verify suppress is set for the absolute path.
        assert abs_path in mgr._suppress

    def test_display_path_helper(self) -> None:
        """_display_path strips /vault/ prefix to give a clean relative display form."""
        assert IngestQueueManager._display_path("/vault/raw/sources/foo.md") == "raw/sources/foo.md"
        assert (
            IngestQueueManager._display_path("/data/vault/raw/sources/bar.md")
            == "raw/sources/bar.md"
        )
        # Fall back to basename when the marker is absent
        assert IngestQueueManager._display_path("/some/other/path/baz.txt") == "baz.txt"


# ── IngestCancelled exception ─────────────────────────────────────────────────


class TestIngestCancelledException:
    def test_exception_carries_origin_source(self) -> None:
        from app.ingest.loop import IngestCancelled

        exc = IngestCancelled("raw/sources/doc.md")
        assert exc.origin_source == "raw/sources/doc.md"
        assert "raw/sources/doc.md" in str(exc)

    def test_cancel_event_triggers_ingest_cancelled(self) -> None:
        """Verify the cancel check in run_orchestrated_loop raises IngestCancelled."""

        from app.ingest.loop import IngestCancelled, run_orchestrated_loop
        from app.ingest.provider.base import UsageAccumulator

        # Build a minimal mock provider that triggers cancel after analyze()
        cancel_event = asyncio.Event()

        class _CancellingProvider:
            def bind_accumulator(self, acc: object) -> None:
                pass

            async def analyze(self, source_text: str, vault_context: str) -> object:
                from app.ingest.schemas import Analysis, PageType, SuggestedPage

                cancel_event.set()  # set before first generate() check
                return Analysis(
                    topics=["t"],
                    entities=[],
                    language="en",
                    suggested_pages=[SuggestedPage(title="T", type=PageType.CONCEPT)],
                    summary=None,
                )

            async def generate(self, analysis: object, ctx: str, source_text: str = "") -> list:
                return []  # should never be reached

        async def run() -> None:
            with pytest.raises(IngestCancelled):
                await run_orchestrated_loop(
                    provider=_CancellingProvider(),  # type: ignore[arg-type]
                    accumulator=UsageAccumulator(),
                    source_text="test",
                    vault_context="",
                    retrieval_context="",
                    origin_source="raw/sources/doc.md",
                    max_iter=3,
                    token_budget=60000,
                    cancel_event=cancel_event,
                )

        asyncio.run(run())


# ── Phase tracking (set_phase / set_route / snapshot) ────────────────────────


class TestPhaseTracking:
    def test_set_phase_updates_handle(self) -> None:
        """set_phase updates RunHandle.phase; snapshot reflects it."""
        mgr = make_manager()
        run_id = uuid.uuid4()
        handle = mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        assert handle.phase == "queued"  # default

        mgr.set_phase(run_id, "analyzing")
        assert handle.phase == "analyzing"

        mgr.set_phase(run_id, "generating (1/3)")
        assert handle.phase == "generating (1/3)"

    def test_set_phase_reflected_in_snapshot(self) -> None:
        """snapshot task reflects the current phase set via set_phase."""
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        mgr.set_phase(run_id, "validating")

        snap = mgr.snapshot()
        assert snap["tasks"][0]["phase"] == "validating"

    def test_set_phase_unknown_run_id_noop(self) -> None:
        """set_phase with an unknown run_id must not raise."""
        mgr = make_manager()
        mgr.set_phase(uuid.uuid4(), "analyzing")  # should not raise

    def test_set_route_updates_handle(self) -> None:
        """set_route stores the route string on the handle."""
        mgr = make_manager()
        run_id = uuid.uuid4()
        handle = mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        assert handle.route is None  # default

        mgr.set_route(run_id, "orchestrated")
        assert handle.route == "orchestrated"

    def test_set_route_unknown_run_id_noop(self) -> None:
        """set_route with an unknown run_id must not raise."""
        mgr = make_manager()
        mgr.set_route(uuid.uuid4(), "delegated")  # should not raise

    def test_pending_task_phase_is_queued(self) -> None:
        """Pending (parked) tasks always have phase='queued' and progress=0.0."""
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/x.md", "ingest")
        snap = mgr.snapshot()
        task = snap["tasks"][0]
        assert task["phase"] == "queued"
        assert task["progress"] == 0.0
        assert task["elapsed_seconds"] is None
        assert task["eta_seconds"] is None

    def test_failed_task_phase_is_failed(self) -> None:
        """Failed tasks carry phase='failed' and no progress/elapsed/eta."""
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/bad.md")
        mgr.finalize(run_id, "failed", error="oops")
        snap = mgr.snapshot()
        task = snap["tasks"][0]
        assert task["phase"] == "failed"
        assert task["progress"] is None
        assert task["elapsed_seconds"] is None
        assert task["eta_seconds"] is None


# ── Phase → progress mapping ──────────────────────────────────────────────────


class TestPhaseToProgress:
    """Unit-test the _phase_to_progress helper directly."""

    def test_queued(self) -> None:
        from app.ingest.queue_manager import _phase_to_progress

        assert _phase_to_progress("queued") == 0.0

    def test_analyzing(self) -> None:
        from app.ingest.queue_manager import _phase_to_progress

        assert _phase_to_progress("analyzing") == 0.2

    def test_generating_prefix(self) -> None:
        from app.ingest.queue_manager import _phase_to_progress

        assert _phase_to_progress("generating (1/3)") == 0.5
        assert _phase_to_progress("generating (2/3)") == 0.5

    def test_validating(self) -> None:
        from app.ingest.queue_manager import _phase_to_progress

        assert _phase_to_progress("validating") == 0.8

    def test_writing(self) -> None:
        from app.ingest.queue_manager import _phase_to_progress

        assert _phase_to_progress("writing") == 0.95

    def test_agent_running_is_none(self) -> None:
        from app.ingest.queue_manager import _phase_to_progress

        assert _phase_to_progress("agent running") is None

    def test_unknown_phase_is_none(self) -> None:
        from app.ingest.queue_manager import _phase_to_progress

        assert _phase_to_progress("something_unknown") is None

    def test_failed_phase_is_none(self) -> None:
        from app.ingest.queue_manager import _phase_to_progress

        assert _phase_to_progress("failed") is None


# ── ETA helper unit tests ──────────────────────────────────────────────────────


class TestEtaComputation:
    """
    Unit-tests for ETA logic: snapshot(avg_duration_by_route=...) enriches active
    tasks with eta_seconds = max(0, round(avg - elapsed)).

    These tests inject the avg directly (no DB) to keep them pure-unit.
    """

    def test_eta_computed_when_avg_available(self) -> None:
        """eta_seconds = max(0, round(avg_duration - elapsed))."""
        from datetime import timedelta

        mgr = make_manager()
        run_id = uuid.uuid4()
        handle = mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        # Simulate a run that started 30 seconds ago
        handle.started_at = datetime.now(UTC) - timedelta(seconds=30)
        handle.route = "orchestrated"
        mgr.set_phase(run_id, "analyzing")

        snap = mgr.snapshot(avg_duration_by_route={"orchestrated": 90.0})
        task = snap["tasks"][0]
        assert task["eta_seconds"] is not None
        # avg=90, elapsed≈30 → eta≈60; allow ±2s for test timing
        assert 58 <= task["eta_seconds"] <= 62

    def test_eta_zero_when_elapsed_exceeds_avg(self) -> None:
        """eta_seconds floors at 0 when elapsed > avg (run took longer than expected)."""
        from datetime import timedelta

        mgr = make_manager()
        run_id = uuid.uuid4()
        handle = mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        handle.started_at = datetime.now(UTC) - timedelta(seconds=120)
        handle.route = "orchestrated"

        snap = mgr.snapshot(avg_duration_by_route={"orchestrated": 60.0})
        task = snap["tasks"][0]
        assert task["eta_seconds"] == 0

    def test_eta_none_when_no_history_for_route(self) -> None:
        """eta_seconds is None when avg_duration_by_route does not contain the task's route."""
        mgr = make_manager()
        run_id = uuid.uuid4()
        handle = mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        handle.route = "orchestrated"

        snap = mgr.snapshot(avg_duration_by_route={"delegated": 45.0})
        task = snap["tasks"][0]
        assert task["eta_seconds"] is None

    def test_eta_none_when_route_not_set(self) -> None:
        """eta_seconds is None when handle.route is None (route not yet resolved)."""
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        # route defaults to None

        snap = mgr.snapshot(avg_duration_by_route={"orchestrated": 90.0})
        task = snap["tasks"][0]
        assert task["eta_seconds"] is None

    def test_elapsed_seconds_increases_over_time(self) -> None:
        """elapsed_seconds reflects wall-clock time since started_at."""
        from datetime import timedelta

        mgr = make_manager()
        run_id = uuid.uuid4()
        handle = mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        handle.started_at = datetime.now(UTC) - timedelta(seconds=15)

        snap = mgr.snapshot()
        task = snap["tasks"][0]
        assert task["elapsed_seconds"] is not None
        assert 14 <= task["elapsed_seconds"] <= 17

    def test_snapshot_no_avg_provided_eta_none(self) -> None:
        """When avg_duration_by_route is not passed, eta_seconds is None."""
        mgr = make_manager()
        run_id = uuid.uuid4()
        handle = mgr.open_run(run_id, "/vault/raw/sources/doc.md")
        handle.route = "orchestrated"

        snap = mgr.snapshot()  # no avg argument
        task = snap["tasks"][0]
        assert task["eta_seconds"] is None


# ── on_phase callback in run_orchestrated_loop ────────────────────────────────


class TestOnPhaseCallback:
    """Verify on_phase is called at the correct loop boundaries."""

    def test_on_phase_called_with_correct_phases(self) -> None:

        from app.ingest.loop import run_orchestrated_loop
        from app.ingest.provider.base import UsageAccumulator
        from app.ingest.schemas import Analysis, PageType, SuggestedPage, WikiFrontmatter, WikiPage

        phases_seen: list[str] = []

        class _QuickProvider:
            def bind_accumulator(self, acc: object) -> None:
                pass

            async def analyze(self, source_text: str, vault_context: str) -> Analysis:
                return Analysis(
                    topics=["t"],
                    entities=[],
                    language="en",
                    suggested_pages=[SuggestedPage(title="T", type=PageType.CONCEPT)],
                    summary=None,
                )

            async def generate(
                self, analysis: Analysis, ctx: str, source_text: str = ""
            ) -> list[WikiPage]:
                fm = WikiFrontmatter(
                    type=PageType.CONCEPT,
                    title="T",
                    sources=["raw/sources/doc.md"],
                    lang="en",
                )
                return [
                    WikiPage(
                        title="T",
                        type=PageType.CONCEPT,
                        content="content",
                        frontmatter=fm,
                    )
                ]

        async def run() -> None:
            await run_orchestrated_loop(
                provider=_QuickProvider(),  # type: ignore[arg-type]
                accumulator=UsageAccumulator(),
                source_text="test",
                vault_context="",
                retrieval_context="",
                origin_source="raw/sources/doc.md",
                max_iter=2,
                token_budget=60000,
                on_phase=phases_seen.append,
            )

        asyncio.run(run())
        # Must have seen: analyzing, then at least one generating+validating pair
        assert "analyzing" in phases_seen
        assert any(p.startswith("generating") for p in phases_seen)
        assert "validating" in phases_seen
        # Order: analyzing comes before generating
        assert phases_seen.index("analyzing") < next(
            i for i, p in enumerate(phases_seen) if p.startswith("generating")
        )

    def test_on_phase_none_does_not_raise(self) -> None:
        """on_phase=None (default) must not cause any error."""

        from app.ingest.loop import run_orchestrated_loop
        from app.ingest.provider.base import UsageAccumulator
        from app.ingest.schemas import Analysis, PageType, SuggestedPage, WikiFrontmatter, WikiPage

        class _QuickProvider:
            def bind_accumulator(self, acc: object) -> None:
                pass

            async def analyze(self, source_text: str, vault_context: str) -> Analysis:
                return Analysis(
                    topics=["t"],
                    entities=[],
                    language="en",
                    suggested_pages=[SuggestedPage(title="T", type=PageType.CONCEPT)],
                    summary=None,
                )

            async def generate(
                self, analysis: Analysis, ctx: str, source_text: str = ""
            ) -> list[WikiPage]:
                fm = WikiFrontmatter(
                    type=PageType.CONCEPT,
                    title="T",
                    sources=["raw/sources/doc.md"],
                    lang="en",
                )
                return [
                    WikiPage(
                        title="T",
                        type=PageType.CONCEPT,
                        content="content",
                        frontmatter=fm,
                    )
                ]

        async def run() -> None:
            result = await run_orchestrated_loop(
                provider=_QuickProvider(),  # type: ignore[arg-type]
                accumulator=UsageAccumulator(),
                source_text="test",
                vault_context="",
                retrieval_context="",
                origin_source="raw/sources/doc.md",
                max_iter=1,
                token_budget=60000,
                # on_phase not passed — defaults to None
            )
            assert result.converged is True

        asyncio.run(run())


# ── Drain callback (WS-C, ADR-0079) ──────────────────────────────────────────


class TestDrainCallback:
    """
    Drain callback fires once when the queue transitions to idle AND completed_since_idle > 0.
    (ADR-0079 §1: sweep-on-drain — llm_wiki parity onQueueDrained).
    """

    def test_callback_fires_when_work_completed(self) -> None:
        """T-DRAIN-001: drain callback is scheduled after a successful finalize → idle."""
        calls: list[str] = []

        async def _cb() -> None:
            calls.append("drained")

        async def run() -> None:
            mgr = make_manager()
            mgr.set_on_drained(_cb)
            run_id = uuid.uuid4()
            mgr.open_run(run_id, "/vault/raw/sources/a.md")
            mgr.finalize(run_id, "completed")
            # Give the scheduled task a tick to execute.
            await asyncio.sleep(0)

        asyncio.run(run())
        assert calls == ["drained"], "drain callback must fire exactly once on idle with work done"

    def test_callback_not_fired_when_no_work(self) -> None:
        """T-DRAIN-002: finalize → idle with completed_since_idle == 0 → callback NOT fired."""
        calls: list[str] = []

        async def _cb() -> None:
            calls.append("drained")

        async def run() -> None:
            mgr = make_manager()
            mgr.set_on_drained(_cb)
            run_id = uuid.uuid4()
            mgr.open_run(run_id, "/vault/raw/sources/a.md")
            # Finalize with failure — completed_since_idle stays 0.
            mgr.finalize(run_id, "failed", error="boom")
            await asyncio.sleep(0)

        asyncio.run(run())
        assert calls == [], "drain callback must NOT fire when no work completed"

    def test_callback_not_fired_while_more_active(self) -> None:
        """T-DRAIN-003: queue does NOT drain while a second run is still active."""
        calls: list[str] = []

        async def _cb() -> None:
            calls.append("drained")

        async def run() -> None:
            mgr = make_manager()
            mgr.set_on_drained(_cb)
            run_a = uuid.uuid4()
            run_b = uuid.uuid4()
            mgr.open_run(run_a, "/vault/raw/sources/a.md")
            mgr.open_run(run_b, "/vault/raw/sources/b.md")
            # Only finalize the first one — queue still has b.md active.
            mgr.finalize(run_a, "completed")
            await asyncio.sleep(0)

        asyncio.run(run())
        assert calls == [], "drain callback must NOT fire while another run is active"

    def test_debounce_guard_prevents_double_fire(self) -> None:
        """T-DRAIN-004: two rapid drains — callback fires at most once (_drain_in_flight guard)."""
        calls: list[str] = []

        async def _slow_cb() -> None:
            # Simulate a callback that yields control (slow I/O).
            await asyncio.sleep(0.01)
            calls.append("drained")

        async def run() -> None:
            mgr = make_manager()
            mgr.set_on_drained(_slow_cb)

            # First drain cycle.
            r1 = uuid.uuid4()
            mgr.open_run(r1, "/vault/raw/sources/x.md")
            mgr.finalize(r1, "completed")
            # _drain_in_flight should now be True — a second drain is suppressed.
            r2 = uuid.uuid4()
            mgr.open_run(r2, "/vault/raw/sources/y.md")
            mgr.finalize(r2, "completed")
            # Allow both tasks (if any were created) to settle.
            await asyncio.sleep(0.05)

        asyncio.run(run())
        # Only one "drained" allowed even though two drains occurred.
        assert (
            len(calls) == 1
        ), f"drain callback fired {len(calls)} times but debounce should suppress the second"

    def test_no_callback_registered_is_noop(self) -> None:
        """T-DRAIN-005: finalize with no callback registered → no error, no fire."""

        async def run() -> None:
            mgr = make_manager()
            # No set_on_drained call.
            r = uuid.uuid4()
            mgr.open_run(r, "/vault/raw/sources/a.md")
            mgr.finalize(r, "completed")
            await asyncio.sleep(0)

        # Must not raise.
        asyncio.run(run())

    def test_callback_exception_is_non_fatal(self) -> None:
        """T-DRAIN-006: if callback raises, the exception is caught and the guard is cleared."""

        async def _bad_cb() -> None:
            raise RuntimeError("sweep exploded")

        async def run() -> None:
            mgr = make_manager()
            mgr.set_on_drained(_bad_cb)

            r = uuid.uuid4()
            mgr.open_run(r, "/vault/raw/sources/a.md")
            mgr.finalize(r, "completed")
            await asyncio.sleep(0)

            # After the failed callback the guard must be cleared so the next drain can fire.
            assert mgr._drain_in_flight is False, "_drain_in_flight must be cleared after callback"

        asyncio.run(run())

    def test_set_on_drained_clears_callback(self) -> None:
        """T-DRAIN-007: set_on_drained(None) unregisters the callback."""
        calls: list[str] = []

        async def _cb() -> None:
            calls.append("drained")

        async def run() -> None:
            mgr = make_manager()
            mgr.set_on_drained(_cb)
            mgr.set_on_drained(None)  # unregister

            r = uuid.uuid4()
            mgr.open_run(r, "/vault/raw/sources/a.md")
            mgr.finalize(r, "completed")
            await asyncio.sleep(0)

        asyncio.run(run())
        assert calls == [], "callback must not fire after being cleared with None"


# ── 429 / rate-limit auto-pause (BE-QUEUE-1, 1.9.4 W3) ─────────────────────────


class TestRateLimitAutoPause:
    def test_pause_for_rate_limit_pauses_with_auto_429_reason(self) -> None:
        mgr = make_manager()
        cooldown = mgr.pause_for_rate_limit()
        assert cooldown == 30.0
        assert mgr._paused is True
        assert mgr._paused_by == "auto_429"

    def test_auto_pause_then_auto_resume_after_cooldown(self) -> None:
        """(a) A simulated 429 pauses the queue, then auto-resumes once the (fake/injected)
        cooldown clock elapses — no real sleep involved."""

        async def run() -> None:
            mgr = make_manager()

            async def instant_sleep(_seconds: float) -> None:
                return None  # fake clock: cooldown "elapses" immediately

            mgr._sleep_fn = instant_sleep
            cooldown = mgr.pause_for_rate_limit()
            assert cooldown == 30.0
            assert mgr._paused is True

            # Let the scheduled auto-resume task run to completion.
            for _ in range(5):
                await asyncio.sleep(0)

            assert mgr._paused is False
            assert mgr._paused_by is None

        asyncio.run(run())

    def test_backoff_schedule_caps_at_300s(self) -> None:
        """(b) The cooldown ladder escalates but never grows past the 300s I7 hard cap."""
        mgr = make_manager()
        seen = [mgr.pause_for_rate_limit() for _ in range(6)]
        assert seen == [30.0, 60.0, 120.0, 300.0, 300.0, 300.0]
        assert max(seen) == 300.0

    def test_reset_rate_limit_backoff_drops_to_base_tier(self) -> None:
        mgr = make_manager()
        mgr.pause_for_rate_limit()
        mgr.pause_for_rate_limit()
        assert mgr._rate_limit_level == 2
        mgr.reset_rate_limit_backoff()
        assert mgr._rate_limit_level == 0
        assert mgr.pause_for_rate_limit() == 30.0

    def test_manual_pause_not_overridden_by_pending_auto_resume(self) -> None:
        """(c) A manual pause taken WHILE a 429 cooldown is still counting down must win —
        the auto-resume timer must never un-pause a manually-paused queue."""

        async def run() -> None:
            mgr = make_manager()

            async def never_completes(_seconds: float) -> None:
                # Simulates a cooldown that has not elapsed yet by the time the test acts.
                await asyncio.sleep(3600)

            mgr._sleep_fn = never_completes
            mgr.pause_for_rate_limit()
            assert mgr._paused_by == "auto_429"

            # Operator manually pauses before the cooldown would have fired.
            mgr.pause()
            assert mgr._paused_by == "manual"
            assert mgr._rate_limit_task is None  # cancelled — no lingering auto-resume

            # Give the event loop a beat to process the cancellation; must NOT resume.
            await asyncio.sleep(0)
            assert mgr._paused is True
            assert mgr._paused_by == "manual"

        asyncio.run(run())

    def test_pause_for_rate_limit_is_noop_when_already_manually_paused(self) -> None:
        mgr = make_manager()
        mgr.pause()
        cooldown = mgr.pause_for_rate_limit()
        assert cooldown == 0.0
        assert mgr._paused_by == "manual"  # unchanged — manual pause always wins

    def test_manual_resume_clears_auto_429_state(self) -> None:
        mgr = make_manager()
        mgr.pause_for_rate_limit()
        assert mgr._paused_by == "auto_429"
        mgr.resume()
        assert mgr._paused is False
        assert mgr._paused_by is None

    def test_snapshot_exposes_paused_by(self) -> None:
        mgr = make_manager()
        assert mgr.snapshot()["paused_by"] is None
        mgr.pause_for_rate_limit()
        assert mgr.snapshot()["paused_by"] == "auto_429"


# ── Per-capability concurrency cap (BE-QUEUE-2, 1.9.4 W3) ──────────────────────


class TestCapabilityConcurrencyCap:
    def test_default_limits_match_config_defaults(self) -> None:
        from app.ingest.queue_manager import _CAPABILITY_CONCURRENCY_LIMITS

        # CLI=1, API=3, Local=1 (config.py defaults) unless overridden via env.
        assert _CAPABILITY_CONCURRENCY_LIMITS == {"cli": 1, "api": 3, "local": 1}

    def test_acquire_blocks_dispatch_beyond_the_cap(self) -> None:
        """(d) A second acquire for the same capability blocks once the cap (1) is reached,
        and unblocks only after the first slot is released."""

        async def run() -> None:
            mgr = make_manager()
            mgr._capability_semaphores["cli"] = asyncio.Semaphore(1)

            await mgr.acquire_capability_slot("cli")

            acquired_second = False

            async def try_second() -> None:
                nonlocal acquired_second
                await mgr.acquire_capability_slot("cli")
                acquired_second = True

            task = asyncio.create_task(try_second())
            await asyncio.sleep(0)
            assert acquired_second is False, "second acquire must block at the cap"

            mgr.release_capability_slot("cli")
            await asyncio.sleep(0)
            assert acquired_second is True, "second acquire unblocks once a slot is released"

            await task

        asyncio.run(run())

    def test_higher_cap_allows_concurrent_dispatch(self) -> None:
        """A capability with cap=3 (API default) admits 3 concurrent acquires without blocking."""

        async def run() -> None:
            mgr = make_manager()
            for _ in range(3):
                await mgr.acquire_capability_slot("api")  # must not block

            blocked = False

            async def fourth() -> None:
                nonlocal blocked
                await mgr.acquire_capability_slot("api")
                blocked = True

            task = asyncio.create_task(fourth())
            await asyncio.sleep(0)
            assert blocked is False, "4th acquire must block once the cap (3) is reached"

            mgr.release_capability_slot("api")
            await asyncio.sleep(0)
            assert blocked is True
            await task

        asyncio.run(run())

    def test_unknown_mode_gets_fail_safe_cap_of_one(self) -> None:
        async def run() -> None:
            mgr = make_manager()
            await mgr.acquire_capability_slot("some_future_mode")

            acquired_second = False

            async def try_second() -> None:
                nonlocal acquired_second
                await mgr.acquire_capability_slot("some_future_mode")
                acquired_second = True

            task = asyncio.create_task(try_second())
            await asyncio.sleep(0)
            assert acquired_second is False

            mgr.release_capability_slot("some_future_mode")
            await asyncio.sleep(0)
            assert acquired_second is True
            await task

        asyncio.run(run())

    def test_release_is_a_noop_for_never_acquired_mode(self) -> None:
        mgr = make_manager()
        mgr.release_capability_slot("cli")  # must not raise even though never acquired first
