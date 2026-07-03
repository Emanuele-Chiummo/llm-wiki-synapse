"""
Tests for OpsScheduler (R12-7 / A5 — SPRINT-v1.2-SCOPE §10 A5).

Acceptance criteria:
  - S10/S11 config keys validate the off|hourly|daily|weekly enum; default is "off".
  - Scheduler tick triggers lint/backfill ops when their interval has elapsed.
  - No overlap: an in-flight op is not triggered again on the next tick.
  - POST /ops/schedules/{op}/run-now → 202 when not in-flight.
  - POST /ops/schedules/{op}/run-now → 409 when already in-flight.
  - GET /ops/schedules → correct shape for lint and backfill.

Infra-free: all tests use mock clocks, mock op functions, and a mock config cache.
No real asyncio.sleep. No real DB or filesystem access (I7).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

# ── Helpers ──────────────────────────────────────────────────────────────────


class _MockClock:
    """Controllable clock for scheduler tests (replaces asyncio.sleep + datetime.now)."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
        self._sleep_calls: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self._sleep_calls.append(seconds)
        # Advance time without actually sleeping.
        self._now = self._now + timedelta(seconds=seconds)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now = self._now + timedelta(seconds=seconds)


def _make_scheduler(
    lint_fn: Any = None,
    backfill_fn: Any = None,
    clock: _MockClock | None = None,
) -> Any:
    """Create an OpsScheduler with injected dependencies."""
    from app.ops_scheduler import OpsScheduler

    if clock is None:
        clock = _MockClock()
    if lint_fn is None:
        lint_fn = AsyncMock()
    if backfill_fn is None:
        backfill_fn = AsyncMock()
    return (
        OpsScheduler(clock=clock, lint_fn=lint_fn, backfill_fn=backfill_fn),
        clock,
        lint_fn,
        backfill_fn,
    )


# ─────────────────────────────────────────────────────────────────────────────
# S10/S11 config-key validation
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_lint_schedule_valid_values() -> None:
    """S10: lint_schedule accepts off|hourly|daily|weekly; rejects anything else."""
    from app.config_overrides import validate_value

    for v in ("off", "hourly", "daily", "weekly"):
        assert validate_value("lint_schedule", v) is None, f"Expected valid for {v!r}"

    assert validate_value("lint_schedule", "15m") is not None
    assert validate_value("lint_schedule", "never") is not None
    assert validate_value("lint_schedule", "") is not None
    assert validate_value("lint_schedule", "ON") is not None  # case-sensitive


def test_validate_backfill_schedule_valid_values() -> None:
    """S11: backfill_schedule accepts off|hourly|daily|weekly; rejects anything else."""
    from app.config_overrides import validate_value

    for v in ("off", "hourly", "daily", "weekly"):
        assert validate_value("backfill_schedule", v) is None

    assert validate_value("backfill_schedule", "1h") is not None
    assert validate_value("backfill_schedule", "") is not None


def test_effective_schedule_default_is_off() -> None:
    """S10/S11: when no override is cached, effective_schedule returns 'off'."""
    import app.config_overrides as co
    from app.config_overrides import effective_schedule

    # Ensure neither key is cached.
    co._cache.pop("lint_schedule", None)
    co._cache.pop("backfill_schedule", None)

    assert effective_schedule("lint_schedule") == "off"
    assert effective_schedule("backfill_schedule") == "off"


def test_effective_schedule_returns_cached_value() -> None:
    """effective_schedule returns the cached value when a valid override is present."""
    import app.config_overrides as co
    from app.config_overrides import effective_schedule

    co._cache["lint_schedule"] = "daily"
    assert effective_schedule("lint_schedule") == "daily"
    co._cache.pop("lint_schedule", None)


def test_effective_schedule_unknown_value_falls_back_to_off() -> None:
    """effective_schedule falls back to 'off' if a malformed value somehow got cached."""
    import app.config_overrides as co
    from app.config_overrides import effective_schedule

    co._cache["lint_schedule"] = "bogus"
    assert effective_schedule("lint_schedule") == "off"
    co._cache.pop("lint_schedule", None)


def test_lint_backfill_schedule_in_allowed_keys() -> None:
    """S10/S11 keys are in ALLOWED_CONFIG_KEYS (allow-list — ADR-0053 §2.2)."""
    from app.config_overrides import ALLOWED_CONFIG_KEYS

    assert "lint_schedule" in ALLOWED_CONFIG_KEYS
    assert "backfill_schedule" in ALLOWED_CONFIG_KEYS


def test_lint_backfill_schedule_in_ordered_keys() -> None:
    """S10/S11 keys appear in ORDERED_KEYS (stable GET /config/app order)."""
    from app.config_overrides import ORDERED_KEYS

    assert "lint_schedule" in ORDERED_KEYS
    assert "backfill_schedule" in ORDERED_KEYS


# ─────────────────────────────────────────────────────────────────────────────
# OpsScheduler tick logic (mock clock, mock config, mock ops)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_tick_triggers_lint_when_due() -> None:
    """When lint_schedule='hourly' and > 3600s have elapsed, lint op is triggered."""
    import app.config_overrides as co

    scheduler, clock, lint_fn, backfill_fn = _make_scheduler()

    # Seed the cache: lint=hourly, backfill=off
    co._cache["lint_schedule"] = "hourly"
    co._cache.pop("backfill_schedule", None)

    # Simulate: last_run_at is 7200s in the past (2 × hourly interval).
    clock.advance(-7200)
    past = clock.now()
    clock.advance(7200)
    scheduler._state["lint"].last_run_at = past

    try:
        # Run one tick directly (bypassing the sleep loop).
        await scheduler._check_and_trigger("lint")

        # Lint should have been called once.
        lint_fn.assert_awaited_once()
        backfill_fn.assert_not_awaited()
        assert scheduler._state["lint"].last_status == "ok"
    finally:
        co._cache.pop("lint_schedule", None)


@pytest.mark.asyncio
async def test_scheduler_tick_does_not_trigger_before_interval() -> None:
    """Op is NOT triggered when the interval has not yet elapsed."""
    import app.config_overrides as co

    scheduler, clock, lint_fn, _ = _make_scheduler()

    co._cache["lint_schedule"] = "daily"  # 86400s

    # last_run_at = 1000s ago (< 86400)
    clock.advance(-1000)
    past = clock.now()
    clock.advance(1000)
    scheduler._state["lint"].last_run_at = past

    try:
        await scheduler._check_and_trigger("lint")
        lint_fn.assert_not_awaited()
    finally:
        co._cache.pop("lint_schedule", None)


@pytest.mark.asyncio
async def test_scheduler_tick_triggers_when_never_run() -> None:
    """Op IS triggered when last_run_at is None (never run), even if schedule is hourly."""
    import app.config_overrides as co

    scheduler, _, lint_fn, _ = _make_scheduler()

    co._cache["lint_schedule"] = "hourly"
    scheduler._state["lint"].last_run_at = None  # never run

    try:
        await scheduler._check_and_trigger("lint")
        lint_fn.assert_awaited_once()
        assert scheduler._state["lint"].last_status == "ok"
    finally:
        co._cache.pop("lint_schedule", None)


@pytest.mark.asyncio
async def test_scheduler_skips_op_off() -> None:
    """schedule='off' → op is never triggered regardless of elapsed time."""
    import app.config_overrides as co

    scheduler, _, lint_fn, backfill_fn = _make_scheduler()

    co._cache["lint_schedule"] = "off"
    co._cache["backfill_schedule"] = "off"
    scheduler._state["lint"].last_run_at = None

    try:
        await scheduler._check_and_trigger("lint")
        await scheduler._check_and_trigger("backfill")
        lint_fn.assert_not_awaited()
        backfill_fn.assert_not_awaited()
    finally:
        co._cache.pop("lint_schedule", None)
        co._cache.pop("backfill_schedule", None)


# ─────────────────────────────────────────────────────────────────────────────
# No-overlap: in-flight guard
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_overlap_while_in_flight() -> None:
    """If in_flight is True, the scheduler skips the trigger for that op."""
    import app.config_overrides as co

    scheduler, clock, lint_fn, _ = _make_scheduler()

    co._cache["lint_schedule"] = "hourly"
    scheduler._state["lint"].in_flight = True  # already running
    scheduler._state["lint"].last_run_at = None

    try:
        await scheduler._check_and_trigger("lint")
        lint_fn.assert_not_awaited()
    finally:
        co._cache.pop("lint_schedule", None)
        scheduler._state["lint"].in_flight = False


@pytest.mark.asyncio
async def test_in_flight_cleared_after_run() -> None:
    """in_flight is always cleared in the finally block after _trigger_op."""
    import app.config_overrides as co

    scheduler, _, lint_fn, _ = _make_scheduler()

    co._cache["lint_schedule"] = "hourly"
    scheduler._state["lint"].last_run_at = None

    try:
        await scheduler._trigger_op("lint")
        assert scheduler._state["lint"].in_flight is False
        assert scheduler._state["lint"].last_status == "ok"
    finally:
        co._cache.pop("lint_schedule", None)


@pytest.mark.asyncio
async def test_in_flight_cleared_on_error() -> None:
    """in_flight is cleared even when the op raises an exception (I7 non-fatal)."""

    async def _failing_lint() -> None:
        raise RuntimeError("lint exploded")

    scheduler, _, _, _ = _make_scheduler(lint_fn=_failing_lint)
    scheduler._state["lint"].last_run_at = None

    await scheduler._trigger_op("lint")

    assert scheduler._state["lint"].in_flight is False
    assert scheduler._state["lint"].last_status is not None
    assert "error" in scheduler._state["lint"].last_status


# ─────────────────────────────────────────────────────────────────────────────
# run_now — 202 / 409
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_now_triggers_immediately() -> None:
    """run_now triggers the op immediately when not in-flight."""
    scheduler, _, lint_fn, _ = _make_scheduler()

    # Patch the backfill is_running guard so it doesn't need the module import.
    with patch("app.ops_scheduler._check_backfill_not_running"):
        await scheduler.run_now("lint")

    lint_fn.assert_awaited_once()
    assert scheduler._state["lint"].last_status == "ok"


@pytest.mark.asyncio
async def test_run_now_raises_when_in_flight() -> None:
    """run_now raises RuntimeError (→ 409) when the op is already in-flight."""
    scheduler, _, lint_fn, _ = _make_scheduler()
    scheduler._state["lint"].in_flight = True

    try:
        with pytest.raises(RuntimeError, match="in-flight"):
            await scheduler.run_now("lint")
        lint_fn.assert_not_awaited()
    finally:
        scheduler._state["lint"].in_flight = False


@pytest.mark.asyncio
async def test_run_now_unknown_op_raises_valueerror() -> None:
    """run_now raises ValueError for an unknown op name."""
    scheduler, _, _, _ = _make_scheduler()

    with pytest.raises(ValueError, match="Unknown op"):
        await scheduler.run_now("nonexistent")  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# GET /ops/schedules endpoint shape
# ─────────────────────────────────────────────────────────────────────────────


async def _noop_lifespan(app_: Any) -> Any:
    yield


def _make_client() -> Any:
    from contextlib import asynccontextmanager as acm

    from app.main import app
    from fastapi import FastAPI

    @acm
    async def _test_lifespan(app: FastAPI) -> Any:
        yield

    app.router.lifespan_context = _test_lifespan
    from httpx import ASGITransport, AsyncClient

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_get_ops_schedules_shape() -> None:
    """GET /ops/schedules returns the expected shape with 'ops' list of 2 entries."""
    import app.config_overrides as co

    co._cache.pop("lint_schedule", None)
    co._cache.pop("backfill_schedule", None)

    async with _make_client() as client:
        resp = await client.get("/ops/schedules")

    assert resp.status_code == 200
    body = resp.json()
    assert "ops" in body
    ops = body["ops"]
    assert len(ops) == 2
    op_names = {e["op"] for e in ops}
    assert op_names == {"lint", "backfill"}
    for entry in ops:
        assert "schedule" in entry
        assert "last_run_at" in entry
        assert "last_status" in entry
        assert "in_flight" in entry
        # Default with no override and no scheduler: schedule="off"
        assert entry["schedule"] == "off"
        assert entry["in_flight"] is False


@pytest.mark.asyncio
async def test_get_ops_schedules_reflects_override() -> None:
    """GET /ops/schedules returns the effective schedule from the config cache."""
    import app.config_overrides as co

    co._cache["lint_schedule"] = "weekly"
    try:
        async with _make_client() as client:
            resp = await client.get("/ops/schedules")

        assert resp.status_code == 200
        ops = resp.json()["ops"]
        lint_entry = next(e for e in ops if e["op"] == "lint")
        assert lint_entry["schedule"] == "weekly"
    finally:
        co._cache.pop("lint_schedule", None)


# ─────────────────────────────────────────────────────────────────────────────
# POST /ops/schedules/{op}/run-now endpoint
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_run_now_lint_202() -> None:
    """POST /ops/schedules/lint/run-now → 202 when not in-flight."""
    from app.ops_scheduler import OpsScheduler

    mock_lint = AsyncMock()

    test_scheduler = OpsScheduler(lint_fn=mock_lint, backfill_fn=AsyncMock())

    with patch("app.main._ops_scheduler", test_scheduler):
        with patch("app.ops_scheduler._check_backfill_not_running"):
            async with _make_client() as client:
                resp = await client.post("/ops/schedules/lint/run-now")

    assert resp.status_code == 202
    body = resp.json()
    assert body["op"] == "lint"
    assert body["status"] == "triggered"
    mock_lint.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_run_now_409_when_in_flight() -> None:
    """POST /ops/schedules/lint/run-now → 409 when already in-flight."""
    from app.ops_scheduler import OpsScheduler

    test_scheduler = OpsScheduler(lint_fn=AsyncMock(), backfill_fn=AsyncMock())
    test_scheduler._state["lint"].in_flight = True

    try:
        with patch("app.main._ops_scheduler", test_scheduler):
            async with _make_client() as client:
                resp = await client.post("/ops/schedules/lint/run-now")

        assert resp.status_code == 409
    finally:
        test_scheduler._state["lint"].in_flight = False


@pytest.mark.asyncio
async def test_post_run_now_404_unknown_op() -> None:
    """POST /ops/schedules/typo/run-now → 404 unknown op."""
    async with _make_client() as client:
        resp = await client.post("/ops/schedules/typo/run-now")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_post_run_now_backfill_400_dormant() -> None:
    """POST /ops/schedules/backfill/run-now → 400 when vocabulary is empty (dormant)."""
    import app.config_overrides as co

    co._cache.pop("domain_vocabulary", None)  # ensure dormant

    async with _make_client() as client:
        resp = await client.post("/ops/schedules/backfill/run-now")

    assert resp.status_code == 400
    assert "vocabulary" in resp.json()["detail"].lower()
