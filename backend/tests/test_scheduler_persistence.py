"""
Tests for persistent scheduler state (R13-4 / T4).

Covers:
  T-SP-001  save_scheduler_ts() + load_scheduler_ts() round-trip (SQLite in-memory)
  T-SP-002  load_scheduler_ts() returns None for absent key
  T-SP-003  save_scheduler_ts() is non-fatal on DB error (logs + swallows)
  T-SP-004  OpsScheduler.initialize() populates last_run_at from persisted value
  T-SP-005  OpsScheduler._trigger_op() persists on success, not on failure
  T-SP-006  A fresh OpsScheduler instance with loaded timestamp skips due-check
  T-SP-007  ImportScheduler.initialize() populates _last_run_at from persisted value
  T-SP-008  ImportScheduler._run() sleeps remaining interval (not full) after restart
  T-SP-009  ImportScheduler._run() persists timestamp on successful scan

All tests are infra-free (SQLite in-memory for DB tests; mock clock for scheduler tests).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Column, MetaData, String, Table, Text
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── SQLite fixture for scheduler_state tests ──────────────────────────────────


def _build_scheduler_meta() -> MetaData:
    """Minimal schema for scheduler_state tests (just app_config)."""
    meta = MetaData()
    Table(
        "app_config",
        meta,
        Column("key", String, primary_key=True),
        Column("value", Text, nullable=False),
        Column("updated_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )
    return meta


@pytest.fixture()
async def db_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """SQLite in-memory environment for scheduler_state DB tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    meta = _build_scheduler_meta()
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    @asynccontextmanager
    async def patched_get_session():  # type: ignore[return]
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    monkeypatch.setattr("app.db.get_session", patched_get_session)

    yield {
        "session_factory": session_factory,
        "get_session": patched_get_session,
    }


# ── T-SP-001: round-trip ───────────────────────────────────────────────────────


class TestSchedulerStateRoundTrip:
    async def test_save_and_load_roundtrip(self, db_env: dict[str, Any]) -> None:
        """T-SP-001: save then load returns the same UTC timestamp."""
        from app.scheduler_state import load_scheduler_ts, save_scheduler_ts

        ts = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
        await save_scheduler_ts("ops_scheduler.last_run.lint", ts)
        loaded = await load_scheduler_ts("ops_scheduler.last_run.lint")
        assert loaded is not None
        assert loaded.year == 2026
        assert loaded.month == 1
        assert loaded.day == 15
        assert loaded.hour == 10
        assert loaded.minute == 30
        assert loaded.tzinfo is not None

    async def test_upsert_overwrites_existing(self, db_env: dict[str, Any]) -> None:
        """Calling save twice on the same key updates the value."""
        from app.scheduler_state import load_scheduler_ts, save_scheduler_ts

        ts1 = datetime(2026, 1, 1, tzinfo=UTC)
        ts2 = datetime(2026, 3, 1, tzinfo=UTC)
        await save_scheduler_ts("import_scheduler.last_run", ts1)
        await save_scheduler_ts("import_scheduler.last_run", ts2)
        loaded = await load_scheduler_ts("import_scheduler.last_run")
        assert loaded is not None
        assert loaded.month == 3


# ── T-SP-002: absent key ───────────────────────────────────────────────────────


class TestSchedulerStateAbsent:
    async def test_load_absent_key_returns_none(self, db_env: dict[str, Any]) -> None:
        """T-SP-002: loading a key that was never saved returns None."""
        from app.scheduler_state import load_scheduler_ts

        result = await load_scheduler_ts("ops_scheduler.last_run.lint")
        assert result is None

    async def test_load_different_key_returns_none(self, db_env: dict[str, Any]) -> None:
        """Loading a different key from what was saved returns None."""
        from app.scheduler_state import load_scheduler_ts, save_scheduler_ts

        await save_scheduler_ts("import_scheduler.last_run", datetime(2026, 1, 1, tzinfo=UTC))
        result = await load_scheduler_ts("ops_scheduler.last_run.lint")
        assert result is None


# ── T-SP-003: non-fatal on DB error ───────────────────────────────────────────


class TestSchedulerStateNonFatal:
    async def test_save_non_fatal_on_db_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-SP-003: save_scheduler_ts swallows DB errors without raising."""
        from app.scheduler_state import save_scheduler_ts

        async def broken_session():  # type: ignore[return]
            raise RuntimeError("DB unavailable")

        monkeypatch.setattr("app.db.get_session", broken_session)
        # Should NOT raise
        await save_scheduler_ts("ops_scheduler.last_run.lint", datetime.now(UTC))

    async def test_load_non_fatal_on_db_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """load_scheduler_ts swallows DB errors and returns None (fail-closed)."""
        from app.scheduler_state import load_scheduler_ts

        async def broken_session():  # type: ignore[return]
            raise RuntimeError("DB unavailable")

        monkeypatch.setattr("app.db.get_session", broken_session)
        result = await load_scheduler_ts("ops_scheduler.last_run.lint")
        assert result is None


# ── T-SP-004: OpsScheduler.initialize() ────────────────────────────────────────


class _MockClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, tzinfo=UTC)
        self._sleep_calls: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self._sleep_calls.append(seconds)
        self._now = self._now + timedelta(seconds=seconds)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


def _make_ops_scheduler(clock: _MockClock | None = None) -> Any:
    from app.ops_scheduler import OpsScheduler

    if clock is None:
        clock = _MockClock()
    return (
        OpsScheduler(
            clock=clock,
            lint_fn=AsyncMock(),
            backfill_fn=AsyncMock(),
            schema_review_fn=AsyncMock(),
            reclassify_fn=AsyncMock(),
        ),
        clock,
    )


class TestOpsSchedulerInitialize:
    async def test_initialize_populates_last_run_at(self, db_env: dict[str, Any]) -> None:
        """T-SP-004: initialize() sets last_run_at from persisted value."""
        from app.scheduler_state import save_scheduler_ts

        ts = datetime(2026, 1, 10, 8, 0, 0, tzinfo=UTC)
        await save_scheduler_ts("ops_scheduler.last_run.lint", ts)

        scheduler, _ = _make_ops_scheduler()
        await scheduler.initialize()

        state = scheduler.get_state("lint")
        assert state.last_run_at is not None
        assert state.last_run_at.day == 10
        assert state.last_run_at.hour == 8

    async def test_initialize_leaves_none_for_absent_key(self, db_env: dict[str, Any]) -> None:
        """initialize() leaves last_run_at as None when no persisted value exists."""
        scheduler, _ = _make_ops_scheduler()
        await scheduler.initialize()
        state = scheduler.get_state("lint")
        assert state.last_run_at is None

    async def test_initialize_all_four_ops(self, db_env: dict[str, Any]) -> None:
        """initialize() loads state for all four ops independently."""
        from app.scheduler_state import save_scheduler_ts

        ts_lint = datetime(2026, 1, 5, tzinfo=UTC)
        ts_backfill = datetime(2026, 1, 6, tzinfo=UTC)
        await save_scheduler_ts("ops_scheduler.last_run.lint", ts_lint)
        await save_scheduler_ts("ops_scheduler.last_run.backfill", ts_backfill)

        scheduler, _ = _make_ops_scheduler()
        await scheduler.initialize()

        assert scheduler.get_state("lint").last_run_at is not None
        assert scheduler.get_state("backfill").last_run_at is not None
        assert scheduler.get_state("schema_review").last_run_at is None
        assert scheduler.get_state("reclassify").last_run_at is None


# ── T-SP-005: persist on success, not on failure ──────────────────────────────


class TestOpsSchedulerPersistOnSuccess:
    async def test_trigger_op_persists_on_success(self, db_env: dict[str, Any]) -> None:
        """T-SP-005: successful op writes timestamp to app_config."""
        from app.scheduler_state import load_scheduler_ts

        scheduler, clock = _make_ops_scheduler()
        # Directly call _trigger_op (bypassing the tick/schedule check)
        await scheduler._trigger_op("lint")

        saved = await load_scheduler_ts("ops_scheduler.last_run.lint")
        assert saved is not None

    async def test_trigger_op_does_not_persist_on_failure(self, db_env: dict[str, Any]) -> None:
        """T-SP-005: failed op does NOT update the persisted timestamp."""
        from app.ops_scheduler import OpsScheduler
        from app.scheduler_state import load_scheduler_ts, save_scheduler_ts

        # Pre-populate a known timestamp
        old_ts = datetime(2026, 1, 1, tzinfo=UTC)
        await save_scheduler_ts("ops_scheduler.last_run.lint", old_ts)

        failing_lint = AsyncMock(side_effect=RuntimeError("lint failed"))
        clock = _MockClock()
        scheduler = OpsScheduler(
            clock=clock,
            lint_fn=failing_lint,
            backfill_fn=AsyncMock(),
            schema_review_fn=AsyncMock(),
            reclassify_fn=AsyncMock(),
        )
        await scheduler._trigger_op("lint")

        # Persisted value should still be the OLD timestamp
        saved = await load_scheduler_ts("ops_scheduler.last_run.lint")
        assert saved is not None
        # Compare just the date to avoid microsecond drift
        assert saved.date() == old_ts.date()


# ── T-SP-006: fresh instance uses persisted value for due-check ───────────────


class TestOpsSchedulerDueCheck:
    async def test_fresh_instance_skips_op_when_not_due(self, db_env: dict[str, Any]) -> None:
        """T-SP-006: a fresh scheduler loaded with a recent timestamp skips the op."""
        from app.ops_scheduler import OpsScheduler
        from app.scheduler_state import save_scheduler_ts

        lint_fn = AsyncMock()
        clock = _MockClock(start=datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC))

        # Persist a "just-ran" timestamp (1 hour ago from clock.now())
        recent_ts = clock.now() - timedelta(minutes=5)
        await save_scheduler_ts("ops_scheduler.last_run.lint", recent_ts)

        scheduler = OpsScheduler(
            clock=clock,
            lint_fn=lint_fn,
            backfill_fn=AsyncMock(),
            schema_review_fn=AsyncMock(),
            reclassify_fn=AsyncMock(),
        )
        await scheduler.initialize()

        # Simulate one tick with "hourly" schedule
        import app.config_overrides as co  # noqa: PLC0415

        original_cache = co._cache.copy()
        co._cache["lint_schedule"] = "hourly"
        try:
            await scheduler._check_and_trigger("lint")
        finally:
            co._cache.clear()
            co._cache.update(original_cache)

        # Lint should NOT have been called (only 5 minutes elapsed, hourly = 3600s)
        lint_fn.assert_not_called()

    async def test_fresh_instance_runs_op_when_overdue(self, db_env: dict[str, Any]) -> None:
        """Fresh scheduler with old persisted timestamp triggers the op."""
        from app.ops_scheduler import OpsScheduler
        from app.scheduler_state import save_scheduler_ts

        lint_fn = AsyncMock()
        clock = _MockClock(start=datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC))

        # Persist a timestamp from 2 hours ago (hourly interval → overdue)
        old_ts = clock.now() - timedelta(hours=2)
        await save_scheduler_ts("ops_scheduler.last_run.lint", old_ts)

        scheduler = OpsScheduler(
            clock=clock,
            lint_fn=lint_fn,
            backfill_fn=AsyncMock(),
            schema_review_fn=AsyncMock(),
            reclassify_fn=AsyncMock(),
        )
        await scheduler.initialize()

        import app.config_overrides as co  # noqa: PLC0415

        original_cache = co._cache.copy()
        co._cache["lint_schedule"] = "hourly"
        try:
            await scheduler._check_and_trigger("lint")
        finally:
            co._cache.clear()
            co._cache.update(original_cache)

        lint_fn.assert_called_once()


# ── T-SP-007: ImportScheduler.initialize() ────────────────────────────────────


class TestImportSchedulerInitialize:
    async def test_initialize_populates_last_run_at(self, db_env: dict[str, Any]) -> None:
        """T-SP-007: initialize() sets _last_run_at from persisted value."""
        from app.import_scheduler import ImportScheduler
        from app.scheduler_state import save_scheduler_ts

        ts = datetime(2026, 2, 20, 12, 0, 0, tzinfo=UTC)
        await save_scheduler_ts("import_scheduler.last_run", ts)

        scheduler = ImportScheduler(clock=MagicMock(), scan_fn=AsyncMock())
        await scheduler.initialize()

        assert scheduler._last_run_at is not None
        assert scheduler._last_run_at.day == 20
        assert scheduler._last_run_at.month == 2

    async def test_initialize_leaves_none_for_absent_key(self, db_env: dict[str, Any]) -> None:
        from app.import_scheduler import ImportScheduler

        scheduler = ImportScheduler(clock=MagicMock(), scan_fn=AsyncMock())
        await scheduler.initialize()
        assert scheduler._last_run_at is None


# ── T-SP-008: ImportScheduler sleeps remaining interval ───────────────────────


class _ImportMockClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
        self.sleep_calls: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        # Raise CancelledError to exit the loop after first sleep
        raise asyncio.CancelledError()

    def now(self) -> datetime:
        return self._now


import asyncio  # noqa: E402


class TestImportSchedulerSleepDuration:
    async def test_sleeps_remaining_when_last_run_recent(
        self, db_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-SP-008: after restart with recent last_run, first sleep < full interval."""
        from app.import_scheduler import FREQ_SECONDS, ImportScheduler

        fixed_now = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
        clock = _ImportMockClock(start=fixed_now)
        # last_run was 30 minutes ago; 1h interval → remaining = 30 minutes = 1800s
        last_run = fixed_now - timedelta(minutes=30)

        mock_cfg = MagicMock()
        mock_cfg.enabled = True
        mock_cfg.frequency = "1h"
        mock_cfg.source_dir = "/some/dir"

        async def mock_load_schedule(vault_id: str) -> Any:
            return mock_cfg

        monkeypatch.setattr("app.import_scheduler.load_schedule", mock_load_schedule)

        scheduler = ImportScheduler(clock=clock, scan_fn=AsyncMock(return_value=(0, "ok", None)))
        scheduler._last_run_at = last_run  # simulate initialize() having run

        # Run until CancelledError from the first sleep
        try:
            await asyncio.wait_for(scheduler._run(), timeout=1.0)
        except (TimeoutError, asyncio.CancelledError):
            pass

        # Should have slept ~1800s (30 min remaining), not 3600s (full hour)
        assert len(clock.sleep_calls) >= 1
        first_sleep = clock.sleep_calls[0]
        assert first_sleep < FREQ_SECONDS["1h"]  # Less than the full interval
        assert first_sleep > 0  # But not zero

    async def test_sleeps_full_interval_when_no_last_run(
        self, db_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-SP-008: when no last_run, first sleep is the full interval."""
        from app.import_scheduler import FREQ_SECONDS, ImportScheduler

        clock = _ImportMockClock(start=datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC))
        mock_cfg = MagicMock()
        mock_cfg.enabled = True
        mock_cfg.frequency = "1h"
        mock_cfg.source_dir = "/some/dir"

        async def mock_load_schedule(vault_id: str) -> Any:
            return mock_cfg

        monkeypatch.setattr("app.import_scheduler.load_schedule", mock_load_schedule)

        scheduler = ImportScheduler(clock=clock, scan_fn=AsyncMock(return_value=(0, "ok", None)))
        # _last_run_at is None (default)

        try:
            await asyncio.wait_for(scheduler._run(), timeout=1.0)
        except (TimeoutError, asyncio.CancelledError):
            pass

        assert len(clock.sleep_calls) >= 1
        assert clock.sleep_calls[0] == FREQ_SECONDS["1h"]


# ── T-SP-009: ImportScheduler persists on successful scan ─────────────────────


class _PersistingClock:
    """Clock that runs one full cycle then stops on the second sleep call."""

    def __init__(self, now: datetime) -> None:
        self._now = now
        self._calls = 0

    async def sleep(self, seconds: float) -> None:
        self._calls += 1
        if self._calls > 1:
            raise asyncio.CancelledError()
        # On first sleep: advance clock by sleep amount so next computation is correct
        self._now = self._now + timedelta(seconds=seconds)

    def now(self) -> datetime:
        return self._now


class TestImportSchedulerPersistOnScan:
    async def test_persists_last_run_after_successful_scan(
        self, db_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-SP-009: after a successful scan, _last_run_at is updated and persisted."""
        from app.import_scheduler import ImportScheduler
        from app.scheduler_state import load_scheduler_ts

        clock = _PersistingClock(now=datetime(2026, 3, 1, tzinfo=UTC))
        mock_cfg = MagicMock()
        mock_cfg.enabled = True
        mock_cfg.frequency = "1h"
        mock_cfg.source_dir = "/some/dir"

        async def mock_load_schedule(vault_id: str) -> Any:
            return mock_cfg

        async def mock_upsert_schedule(vault_id: str, **kwargs: Any) -> None:
            pass

        monkeypatch.setattr("app.import_scheduler.load_schedule", mock_load_schedule)
        monkeypatch.setattr("app.import_scheduler.upsert_schedule", mock_upsert_schedule)

        scan_fn = AsyncMock(return_value=(3, "ok", None))
        scheduler = ImportScheduler(clock=clock, scan_fn=scan_fn)

        try:
            await asyncio.wait_for(scheduler._run(), timeout=2.0)
        except (TimeoutError, asyncio.CancelledError):
            pass

        # _last_run_at should have been updated in-memory
        assert scheduler._last_run_at is not None

        # And persisted to DB
        saved = await load_scheduler_ts("import_scheduler.last_run")
        assert saved is not None
