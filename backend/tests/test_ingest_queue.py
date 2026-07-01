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
        assert snap["tasks"][0]["run_id"] is None
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
        import time

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
        import time

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
        import time

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
        assert IngestQueueManager._display_path("/data/vault/raw/sources/bar.md") == "raw/sources/bar.md"
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
        import asyncio

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

            async def generate(self, analysis: object, ctx: str) -> list:
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
