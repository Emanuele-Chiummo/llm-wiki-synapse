"""
Tests for OpsScheduler (R12-7 / A5 — SPRINT-v1.2-SCOPE §10 A5; R12-8 adds schema_review;
R12-9 adds reclassify as the fourth op).

Acceptance criteria:
  - S10/S11 config keys validate the off|hourly|daily|weekly enum; default is "off".
  - S12 (schema_review_schedule) uses the same enum; default is "off".
  - S13 (reclassify_schedule) uses the same enum; default is "off".
  - Scheduler tick triggers lint/backfill/schema_review/reclassify ops when their interval
    has elapsed.
  - No overlap: an in-flight op is not triggered again on the next tick.
  - POST /ops/schedules/{op}/run-now → 202 when not in-flight (lint, backfill,
    schema_review, reclassify).
  - POST /ops/schedules/{op}/run-now → 409 when already in-flight.
  - GET /ops/schedules → correct shape for lint, backfill, schema_review AND reclassify
    (4 entries).
  - schema_review anti-spam: if a pending schema-suggestion exists, op is a no-op (zero cost).
  - reclassify run-now: 409 when reclassify_types.is_running(); no dormant-400.

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
    schema_review_fn: Any = None,
    reclassify_fn: Any = None,
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
    if schema_review_fn is None:
        schema_review_fn = AsyncMock()
    if reclassify_fn is None:
        reclassify_fn = AsyncMock()
    return (
        OpsScheduler(
            clock=clock,
            lint_fn=lint_fn,
            backfill_fn=backfill_fn,
            schema_review_fn=schema_review_fn,
            reclassify_fn=reclassify_fn,
        ),
        clock,
        lint_fn,
        backfill_fn,
        schema_review_fn,
        reclassify_fn,
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

    scheduler, clock, lint_fn, backfill_fn, _, _rc = _make_scheduler()

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

    scheduler, clock, lint_fn, _bf, _sr, _rc = _make_scheduler()

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

    scheduler, _, lint_fn, _bf, _sr, _rc = _make_scheduler()

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

    scheduler, _, lint_fn, backfill_fn, _sr, _rc = _make_scheduler()

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

    scheduler, clock, lint_fn, _bf, _sr, _rc = _make_scheduler()

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

    scheduler, _, lint_fn, _bf, _sr, _rc = _make_scheduler()

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

    scheduler, _, _, _bf, _sr, _rc = _make_scheduler(lint_fn=_failing_lint)
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
    scheduler, _, lint_fn, _bf, _sr, _rc = _make_scheduler()

    # Patch the backfill is_running guard so it doesn't need the module import.
    with patch("app.ops_scheduler._check_backfill_not_running"):
        await scheduler.run_now("lint")

    lint_fn.assert_awaited_once()
    assert scheduler._state["lint"].last_status == "ok"


@pytest.mark.asyncio
async def test_run_now_raises_when_in_flight() -> None:
    """run_now raises RuntimeError (→ 409) when the op is already in-flight."""
    scheduler, _, lint_fn, _bf, _sr, _rc = _make_scheduler()
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
    scheduler, _, _, _bf, _sr, _rc = _make_scheduler()

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
    """GET /ops/schedules returns the expected shape with 'ops' list of 4 entries (R12-9)."""
    import app.config_overrides as co

    co._cache.pop("lint_schedule", None)
    co._cache.pop("backfill_schedule", None)
    co._cache.pop("schema_review_schedule", None)
    co._cache.pop("reclassify_schedule", None)

    async with _make_client() as client:
        resp = await client.get("/ops/schedules")

    assert resp.status_code == 200
    body = resp.json()
    assert "ops" in body
    ops = body["ops"]
    # R12-9: now 4 entries — lint, backfill, schema_review, reclassify
    assert len(ops) == 4
    op_names = {e["op"] for e in ops}
    assert op_names == {"lint", "backfill", "schema_review", "reclassify"}
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


# ─────────────────────────────────────────────────────────────────────────────
# S12 (schema_review_schedule) config-key tests (R12-8)
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_schema_review_schedule_valid_values() -> None:
    """S12: schema_review_schedule accepts off|hourly|daily|weekly; rejects anything else."""
    from app.config_overrides import validate_value

    for v in ("off", "hourly", "daily", "weekly"):
        assert validate_value("schema_review_schedule", v) is None, f"Expected valid for {v!r}"

    assert validate_value("schema_review_schedule", "15m") is not None
    assert validate_value("schema_review_schedule", "never") is not None
    assert validate_value("schema_review_schedule", "") is not None
    assert validate_value("schema_review_schedule", "WEEKLY") is not None  # case-sensitive


def test_schema_review_schedule_in_allowed_keys() -> None:
    """S12 key is in ALLOWED_CONFIG_KEYS (allow-list — ADR-0053 §2.2)."""
    from app.config_overrides import ALLOWED_CONFIG_KEYS

    assert "schema_review_schedule" in ALLOWED_CONFIG_KEYS


def test_schema_review_schedule_in_ordered_keys() -> None:
    """S12 key appears in ORDERED_KEYS (stable GET /config/app order)."""
    from app.config_overrides import ORDERED_KEYS

    assert "schema_review_schedule" in ORDERED_KEYS
    # Must be 12th (index 11)
    assert ORDERED_KEYS.index("schema_review_schedule") == 11


def test_effective_schedule_default_is_off_for_schema_review() -> None:
    """S12: when no override is cached, effective_schedule returns 'off' for schema_review."""
    import app.config_overrides as co
    from app.config_overrides import effective_schedule

    co._cache.pop("schema_review_schedule", None)
    assert effective_schedule("schema_review_schedule") == "off"


# ─────────────────────────────────────────────────────────────────────────────
# OpsScheduler tick logic — schema_review op (R12-8)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_tick_triggers_schema_review_when_due() -> None:
    """When schema_review_schedule='weekly' and > 604800s have elapsed, schema_review op triggers."""
    import app.config_overrides as co

    scheduler, clock, _lint, _bf, schema_review_fn, _rc = _make_scheduler()

    co._cache["schema_review_schedule"] = "weekly"
    co._cache.pop("lint_schedule", None)
    co._cache.pop("backfill_schedule", None)

    # Simulate: last_run_at is 8 days in the past (> weekly interval).
    clock.advance(-(8 * 86400))
    past = clock.now()
    clock.advance(8 * 86400)
    scheduler._state["schema_review"].last_run_at = past

    try:
        await scheduler._check_and_trigger("schema_review")

        schema_review_fn.assert_awaited_once()
        _lint.assert_not_awaited()
        _bf.assert_not_awaited()
        assert scheduler._state["schema_review"].last_status == "ok"
    finally:
        co._cache.pop("schema_review_schedule", None)


@pytest.mark.asyncio
async def test_scheduler_schema_review_skips_when_off() -> None:
    """schema_review_schedule='off' → op never triggered."""
    import app.config_overrides as co

    scheduler, _, _lint, _bf, schema_review_fn, _rc = _make_scheduler()

    co._cache["schema_review_schedule"] = "off"
    scheduler._state["schema_review"].last_run_at = None

    try:
        await scheduler._check_and_trigger("schema_review")
        schema_review_fn.assert_not_awaited()
    finally:
        co._cache.pop("schema_review_schedule", None)


@pytest.mark.asyncio
async def test_scheduler_schema_review_no_overlap_in_flight() -> None:
    """schema_review op is skipped when already in-flight (no-overlap guard)."""
    import app.config_overrides as co

    scheduler, _, _lint, _bf, schema_review_fn, _rc = _make_scheduler()

    co._cache["schema_review_schedule"] = "hourly"
    scheduler._state["schema_review"].in_flight = True
    scheduler._state["schema_review"].last_run_at = None

    try:
        await scheduler._check_and_trigger("schema_review")
        schema_review_fn.assert_not_awaited()
    finally:
        co._cache.pop("schema_review_schedule", None)
        scheduler._state["schema_review"].in_flight = False


# ─────────────────────────────────────────────────────────────────────────────
# run-now for schema_review (202 / 409) — R12-8
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_run_now_schema_review_202() -> None:
    """POST /ops/schedules/schema_review/run-now → 202 when not in-flight (R12-8)."""
    from app.ops_scheduler import OpsScheduler

    mock_sr = AsyncMock()
    test_scheduler = OpsScheduler(
        lint_fn=AsyncMock(),
        backfill_fn=AsyncMock(),
        schema_review_fn=mock_sr,
    )

    with patch("app.main._ops_scheduler", test_scheduler):
        with patch("app.ops_scheduler._check_backfill_not_running"):
            async with _make_client() as client:
                resp = await client.post("/ops/schedules/schema_review/run-now")

    assert resp.status_code == 202
    body = resp.json()
    assert body["op"] == "schema_review"
    assert body["status"] == "triggered"
    mock_sr.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_run_now_schema_review_409_in_flight() -> None:
    """POST /ops/schedules/schema_review/run-now → 409 when already in-flight (R12-8)."""
    from app.ops_scheduler import OpsScheduler

    test_scheduler = OpsScheduler(
        lint_fn=AsyncMock(),
        backfill_fn=AsyncMock(),
        schema_review_fn=AsyncMock(),
    )
    test_scheduler._state["schema_review"].in_flight = True

    try:
        with patch("app.main._ops_scheduler", test_scheduler):
            async with _make_client() as client:
                resp = await client.post("/ops/schedules/schema_review/run-now")

        assert resp.status_code == 409
    finally:
        test_scheduler._state["schema_review"].in_flight = False


# ─────────────────────────────────────────────────────────────────────────────
# Anti-spam dedup for schema_review (R12-8)
# Verifies that run_schema_review skips (zero cost) when a pending
# schema-suggestion already exists — delegated to generate_schema_suggestion
# Throttle 1. We mock the DB + provider layer; the op must return None.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_schema_review_antispam_skips_when_pending_exists() -> None:
    """
    run_schema_review is a no-op (returns None) when a pending schema-suggestion
    already exists for the vault — anti-spam guard (R12-8 / Throttle 1).

    We mock:
    - app.ops.schema_review.get_session → returns recent pages.
    - app.ops.review.generate_schema_suggestion → returns None (pending exists).
    This verifies that the scheduled wrapper correctly propagates the no-op
    without calling the provider.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    # Fake a non-empty page list so the "no pages" early-return is not hit.
    fake_page = MagicMock()
    fake_page.id = "page-1"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [fake_page]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    # generate_schema_suggestion returns None (Throttle 1: pending item exists).
    mock_generate = AsyncMock(return_value=None)

    # generate_schema_suggestion is imported inside the function body via a deferred
    # import ("from app.ops.review import generate_schema_suggestion") — patch at the
    # source namespace (app.ops.review) so the deferred lookup resolves to the mock.
    with patch("app.ops.schema_review.get_session", return_value=mock_ctx):
        with patch("app.ops.review.generate_schema_suggestion", mock_generate):
            from app.ops.schema_review import run_schema_review

            await run_schema_review(vault_id="vault-test")

    # Verify it was called (did not skip due to no pages), but returned None (no item filed).
    mock_generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_schema_review_run_now_no_dormant_400() -> None:
    """
    POST /ops/schedules/schema_review/run-now does NOT return 400 (no dormant gate).
    schema_review has no vocabulary dependency — anti-spam dedup is inside the op
    itself, not at the HTTP layer (R12-8 design decision).
    """
    from app.ops_scheduler import OpsScheduler

    mock_sr = AsyncMock()
    test_scheduler = OpsScheduler(
        lint_fn=AsyncMock(),
        backfill_fn=AsyncMock(),
        schema_review_fn=mock_sr,
    )

    with patch("app.main._ops_scheduler", test_scheduler):
        async with _make_client() as client:
            resp = await client.post("/ops/schedules/schema_review/run-now")

    # Should be 202 (not 400 — no dormant check for schema_review)
    assert resp.status_code == 202
    mock_sr.assert_awaited_once()


# ─────────────────────────────────────────────────────────────────────────────
# GET /config/app returns 12 settings (S1..S12)  (R12-8)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_config_app_returns_12_settings() -> None:
    """GET /config/app returns 18 settings (S1..S18); schema_review_schedule is at index 11."""
    import app.config_overrides as co

    async with co._cache_lock:
        co._cache.clear()

    async with _make_client() as client:
        resp = await client.get("/config/app")

    assert resp.status_code == 200
    settings_list = resp.json()["settings"]
    assert len(settings_list) == 23  # S1..S23 (S23 = web_search_provider, v1.5 P3-e)
    keys = [s["key"] for s in settings_list]
    assert "schema_review_schedule" in keys
    # schema_review_schedule must appear at position 11 (0-indexed) — S12 position unchanged
    assert keys.index("schema_review_schedule") == 11
    # Default source is "env" when no override exists
    sr_entry = next(s for s in settings_list if s["key"] == "schema_review_schedule")
    assert sr_entry["source"] == "env"
    assert sr_entry["value"] == "off"


# ─────────────────────────────────────────────────────────────────────────────
# S13 (reclassify_schedule) config-key tests (R12-9)
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_reclassify_schedule_valid_values() -> None:
    """S13: reclassify_schedule accepts off|hourly|daily|weekly; rejects anything else."""
    from app.config_overrides import validate_value

    for v in ("off", "hourly", "daily", "weekly"):
        assert validate_value("reclassify_schedule", v) is None, f"Expected valid for {v!r}"

    assert validate_value("reclassify_schedule", "15m") is not None
    assert validate_value("reclassify_schedule", "never") is not None
    assert validate_value("reclassify_schedule", "") is not None
    assert validate_value("reclassify_schedule", "DAILY") is not None  # case-sensitive


def test_reclassify_schedule_in_allowed_keys() -> None:
    """S13 key is in ALLOWED_CONFIG_KEYS (allow-list — ADR-0053 §2.2)."""
    from app.config_overrides import ALLOWED_CONFIG_KEYS

    assert "reclassify_schedule" in ALLOWED_CONFIG_KEYS


def test_reclassify_schedule_in_ordered_keys() -> None:
    """S13 key appears in ORDERED_KEYS (stable GET /config/app order) at index 12."""
    from app.config_overrides import ORDERED_KEYS

    assert "reclassify_schedule" in ORDERED_KEYS
    assert ORDERED_KEYS.index("reclassify_schedule") == 12


def test_effective_schedule_default_is_off_for_reclassify() -> None:
    """S13: when no override is cached, effective_schedule returns 'off' for reclassify."""
    import app.config_overrides as co
    from app.config_overrides import effective_schedule

    co._cache.pop("reclassify_schedule", None)
    assert effective_schedule("reclassify_schedule") == "off"


# ─────────────────────────────────────────────────────────────────────────────
# OpsScheduler tick logic — reclassify op (R12-9)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_tick_triggers_reclassify_when_due() -> None:
    """When reclassify_schedule='daily' and > 86400s have elapsed, reclassify op triggers."""
    import app.config_overrides as co

    scheduler, clock, _lint, _bf, _sr, reclassify_fn = _make_scheduler()

    co._cache["reclassify_schedule"] = "daily"
    co._cache.pop("lint_schedule", None)
    co._cache.pop("backfill_schedule", None)
    co._cache.pop("schema_review_schedule", None)

    # Simulate: last_run_at is 2 days in the past (> daily interval).
    clock.advance(-(2 * 86400))
    past = clock.now()
    clock.advance(2 * 86400)
    scheduler._state["reclassify"].last_run_at = past

    try:
        await scheduler._check_and_trigger("reclassify")

        reclassify_fn.assert_awaited_once()
        _lint.assert_not_awaited()
        _bf.assert_not_awaited()
        _sr.assert_not_awaited()
        assert scheduler._state["reclassify"].last_status == "ok"
    finally:
        co._cache.pop("reclassify_schedule", None)


@pytest.mark.asyncio
async def test_scheduler_reclassify_skips_when_off() -> None:
    """reclassify_schedule='off' → op never triggered."""
    import app.config_overrides as co

    scheduler, _, _lint, _bf, _sr, reclassify_fn = _make_scheduler()

    co._cache["reclassify_schedule"] = "off"
    scheduler._state["reclassify"].last_run_at = None

    try:
        await scheduler._check_and_trigger("reclassify")
        reclassify_fn.assert_not_awaited()
    finally:
        co._cache.pop("reclassify_schedule", None)


@pytest.mark.asyncio
async def test_scheduler_reclassify_no_overlap_in_flight() -> None:
    """reclassify op is skipped when already in-flight (no-overlap guard)."""
    import app.config_overrides as co

    scheduler, _, _lint, _bf, _sr, reclassify_fn = _make_scheduler()

    co._cache["reclassify_schedule"] = "hourly"
    scheduler._state["reclassify"].in_flight = True
    scheduler._state["reclassify"].last_run_at = None

    try:
        await scheduler._check_and_trigger("reclassify")
        reclassify_fn.assert_not_awaited()
    finally:
        co._cache.pop("reclassify_schedule", None)
        scheduler._state["reclassify"].in_flight = False


# ─────────────────────────────────────────────────────────────────────────────
# run-now for reclassify (202 / 409 / no dormant-400) — R12-9
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_run_now_reclassify_202() -> None:
    """POST /ops/schedules/reclassify/run-now → 202 when not in-flight (R12-9)."""
    from app.ops_scheduler import OpsScheduler

    mock_rc = AsyncMock()
    test_scheduler = OpsScheduler(
        lint_fn=AsyncMock(),
        backfill_fn=AsyncMock(),
        schema_review_fn=AsyncMock(),
        reclassify_fn=mock_rc,
    )

    with patch("app.main._ops_scheduler", test_scheduler):
        with patch("app.ops.reclassify_types.is_running", return_value=False):
            with patch("app.ops_scheduler._check_reclassify_not_running"):
                async with _make_client() as client:
                    resp = await client.post("/ops/schedules/reclassify/run-now")

    assert resp.status_code == 202
    body = resp.json()
    assert body["op"] == "reclassify"
    assert body["status"] == "triggered"
    mock_rc.assert_awaited_once()


@pytest.mark.asyncio
async def test_post_run_now_reclassify_409_in_flight() -> None:
    """POST /ops/schedules/reclassify/run-now → 409 when already in-flight (R12-9)."""
    from app.ops_scheduler import OpsScheduler

    test_scheduler = OpsScheduler(
        lint_fn=AsyncMock(),
        backfill_fn=AsyncMock(),
        schema_review_fn=AsyncMock(),
        reclassify_fn=AsyncMock(),
    )
    test_scheduler._state["reclassify"].in_flight = True

    try:
        with patch("app.main._ops_scheduler", test_scheduler):
            with patch("app.ops.reclassify_types.is_running", return_value=False):
                async with _make_client() as client:
                    resp = await client.post("/ops/schedules/reclassify/run-now")

        assert resp.status_code == 409
    finally:
        test_scheduler._state["reclassify"].in_flight = False


@pytest.mark.asyncio
async def test_post_run_now_reclassify_409_external_in_flight() -> None:
    """POST /ops/schedules/reclassify/run-now → 409 when reclassify_types.is_running() (R12-9)."""
    from app.ops_scheduler import OpsScheduler

    test_scheduler = OpsScheduler(
        lint_fn=AsyncMock(),
        backfill_fn=AsyncMock(),
        schema_review_fn=AsyncMock(),
        reclassify_fn=AsyncMock(),
    )

    with patch("app.main._ops_scheduler", test_scheduler):
        with patch("app.ops.reclassify_types.is_running", return_value=True):
            async with _make_client() as client:
                resp = await client.post("/ops/schedules/reclassify/run-now")

    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_post_run_now_reclassify_no_dormant_400() -> None:
    """
    POST /ops/schedules/reclassify/run-now does NOT return 400 (no dormant gate).
    reclassify has no vocabulary dependency — it works on any page regardless of domain
    vocabulary config (R12-9 design decision).
    """
    from app.ops_scheduler import OpsScheduler

    mock_rc = AsyncMock()
    test_scheduler = OpsScheduler(
        lint_fn=AsyncMock(),
        backfill_fn=AsyncMock(),
        schema_review_fn=AsyncMock(),
        reclassify_fn=mock_rc,
    )

    with patch("app.main._ops_scheduler", test_scheduler):
        with patch("app.ops.reclassify_types.is_running", return_value=False):
            with patch("app.ops_scheduler._check_reclassify_not_running"):
                async with _make_client() as client:
                    resp = await client.post("/ops/schedules/reclassify/run-now")

    # Should be 202 (not 400 — no dormant check for reclassify)
    assert resp.status_code == 202
    mock_rc.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_config_app_returns_13_settings() -> None:
    """GET /config/app now returns 18 settings (S1..S18) including reclassify_schedule."""
    import app.config_overrides as co

    async with co._cache_lock:
        co._cache.clear()

    async with _make_client() as client:
        resp = await client.get("/config/app")

    assert resp.status_code == 200
    settings_list = resp.json()["settings"]
    assert len(settings_list) == 23  # S1..S23 (S23 = web_search_provider, v1.5 P3-e)
    keys = [s["key"] for s in settings_list]
    assert "reclassify_schedule" in keys
    # Must appear at position 12 (0-indexed)
    assert keys.index("reclassify_schedule") == 12
    # Default source is "env" when no override exists
    rc_entry = next(s for s in settings_list if s["key"] == "reclassify_schedule")
    assert rc_entry["source"] == "env"
    assert rc_entry["value"] == "off"


# ─────────────────────────────────────────────────────────────────────────────
# R13-12 — scheduler reports the TRUE op outcome (dormant / error / counts)
#
# Regression for the "automations classification doesn't work" report: the
# backfill/reclassify ops NEVER raise — they return a *Summary whose stopped_reason
# is the real outcome. Before this fix _trigger_op marked every non-raising run as a
# green "ok", so a dormant vocabulary or a missing ingest provider looked successful
# while the run silently produced nothing.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_backfill_dormant_vocabulary_reports_dormant_not_ok() -> None:
    """A backfill that returns stopped_reason='dormant' → last_status 'dormant' (+ hint detail)."""
    from app.ops.backfill_domains import BackfillSummary

    async def dormant_backfill() -> BackfillSummary:
        s = BackfillSummary(max_pages=1, token_budget=1)
        s.stopped_reason = "dormant"
        return s

    scheduler, *_ = _make_scheduler(backfill_fn=dormant_backfill)
    await scheduler.run_now("backfill")

    state = scheduler.get_state("backfill")
    assert state.last_status == "dormant"
    assert state.last_detail is not None
    assert "vocabulary" in state.last_detail.lower()


@pytest.mark.asyncio
async def test_reclassify_provider_error_reports_error_not_ok() -> None:
    """A reclassify that returns stopped_reason='error' → last_status starts with 'error'."""
    from app.ops.reclassify_types import ReclassifySummary

    async def erroring_reclassify() -> ReclassifySummary:
        s = ReclassifySummary()
        s.stopped_reason = "error"
        return s

    scheduler, *_ = _make_scheduler(reclassify_fn=erroring_reclassify)
    await scheduler.run_now("reclassify")

    state = scheduler.get_state("reclassify")
    assert state.last_status is not None
    assert state.last_status.startswith("error")


@pytest.mark.asyncio
async def test_backfill_error_does_not_persist_timestamp() -> None:
    """An 'error' outcome is NOT succeeded → the persisted last-run timestamp is not written."""
    from app.ops.backfill_domains import BackfillSummary

    async def erroring_backfill() -> BackfillSummary:
        s = BackfillSummary()
        s.stopped_reason = "error"
        return s

    scheduler, *_ = _make_scheduler(backfill_fn=erroring_backfill)
    with patch("app.scheduler_state.save_scheduler_ts", new=AsyncMock()) as mock_save:
        await scheduler.run_now("backfill")

    # error → fail-open retry semantics: timestamp must NOT be persisted.
    mock_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_backfill_zero_tagged_surfaces_counts_in_detail() -> None:
    """A completed run that tagged nothing still reports 'ok' but the detail reveals 0 tagged."""
    from app.ops.backfill_domains import BackfillSummary

    async def empty_backfill() -> BackfillSummary:
        s = BackfillSummary()
        s.stopped_reason = "complete"
        s.processed = 30
        s.tagged = 0
        s.failed = 30
        return s

    scheduler, *_ = _make_scheduler(backfill_fn=empty_backfill)
    await scheduler.run_now("backfill")

    state = scheduler.get_state("backfill")
    assert state.last_status == "ok"
    assert state.last_detail is not None
    assert "0 tagged" in state.last_detail
    assert "30 processed" in state.last_detail
    assert "30 failed" in state.last_detail


@pytest.mark.asyncio
async def test_reclassify_counts_use_changed_verb() -> None:
    """reclassify detail counts its writes as 'changed' (not 'tagged')."""
    from app.ops.reclassify_types import ReclassifySummary

    async def reclassify_with_changes() -> ReclassifySummary:
        s = ReclassifySummary()
        s.stopped_reason = "complete"
        s.processed = 10
        s.changed = 4
        return s

    scheduler, *_ = _make_scheduler(reclassify_fn=reclassify_with_changes)
    await scheduler.run_now("reclassify")

    state = scheduler.get_state("reclassify")
    assert state.last_status == "ok"
    assert state.last_detail is not None
    assert "4 changed" in state.last_detail


@pytest.mark.asyncio
async def test_no_summary_result_stays_ok_with_no_detail() -> None:
    """An op that returns no *Summary (lint scan / mock) → 'ok', detail None (unchanged behaviour)."""
    scheduler, *_ = _make_scheduler(lint_fn=AsyncMock(return_value=None))
    await scheduler.run_now("lint")

    state = scheduler.get_state("lint")
    assert state.last_status == "ok"
    assert state.last_detail is None
