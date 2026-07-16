"""
GraphCache unit tests — debounce, hit/miss, in-flight guard (I2, ADR-0014).

Infra-free: fake GraphEngine, injectable clock, no real asyncio.sleep, no DB.

Coverage:
  AC-F16db-1  Single bump → exactly one recompute after settle (debounce fires once)
  AC-F16db-2  N bursting bumps within window → exactly one recompute (burst collapse)
  AC-F16db-3  Bump during in-flight run → exactly one follow-up (queue depth ≤ 1)
  ADR-0014 §5 get_graph() HIT: same data_version → pure read, no recompute (cached=True)
  ADR-0014 §5 get_graph() MISS: stale/none → one inline recompute (cached=False)
  ADR-0014 §4 injectable clock/tick — NO real sleep in tests

Test strategy:
  - FakeEngine tracks recompute() call count; returns a deterministic GraphSnapshot.
  - FakeClock advances manually; no wall-clock dependency.
  - tick() is called explicitly after advancing the clock.
  - get_graph() is called to assert HIT/MISS without FA2.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from app.graph.cache import GraphCache
from app.graph.engine import GraphSnapshot

# ── Fakes ──────────────────────────────────────────────────────────────────────


class FakeClock:
    """Injectable monotonic clock that advances only when we say so."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class FakeEngine:
    """
    Fake GraphEngine: tracks recompute() calls.
    Returns a deterministic snapshot with a configurable node list.
    """

    def __init__(self) -> None:
        self.call_count = 0
        self._snapshot = GraphSnapshot(
            nodes=[],
            edges=[],
            data_version=0,
        )

    async def recompute(
        self, vault_id: str, *, session: Any = None
    ) -> GraphSnapshot:  # noqa: ARG002
        self.call_count += 1
        return self._snapshot


def _make_cache(
    debounce: float = 5.0,
) -> tuple[GraphCache, FakeEngine, FakeClock]:
    """Return a GraphCache wired with fake dependencies."""
    engine = FakeEngine()
    clock = FakeClock()
    cache = GraphCache(
        engine=engine,  # type: ignore[arg-type]
        vault_id="test",
        debounce_seconds=debounce,
        clock=clock,
    )
    # Patch _read_data_version so it doesn't need a DB
    cache._read_data_version = AsyncMock(return_value=1)  # type: ignore[method-assign]
    return cache, engine, clock


# ── AC-F16db-1: single bump → one recompute ───────────────────────────────────


class TestDebounceFiresOnce:
    """AC-F16db-1: A single bump fires exactly one recompute after the window."""

    async def test_single_bump_one_recompute(self) -> None:
        cache, engine, clock = _make_cache(debounce=5.0)
        assert engine.call_count == 0

        # Bump with version=1
        cache.notify_bump(1)

        # Before window expires: no recompute
        clock.advance(3.0)
        await cache.tick()
        assert engine.call_count == 0, "No recompute before debounce window"

        # After window expires: exactly one recompute
        clock.advance(2.1)  # now at 5.1 > 5.0
        await cache.tick()
        assert engine.call_count == 1, "Exactly one recompute after debounce window"

        # Additional tick: no more recomputes
        clock.advance(10.0)
        await cache.tick()
        assert engine.call_count == 1, "No additional recompute without another bump"

    async def test_no_bump_no_recompute(self) -> None:
        cache, engine, clock = _make_cache(debounce=5.0)
        clock.advance(100.0)
        await cache.tick()
        assert engine.call_count == 0, "No bump → no recompute"


# ── AC-F16db-2: burst collapse → one recompute ────────────────────────────────


class TestBurstCollapse:
    """AC-F16db-2: N bumps within the window collapse to exactly one recompute."""

    async def test_three_bumps_one_recompute(self) -> None:
        cache, engine, clock = _make_cache(debounce=5.0)

        # Three rapid bumps at t=0, t=1, t=2
        cache.notify_bump(1)
        clock.advance(1.0)
        cache.notify_bump(2)
        clock.advance(1.0)
        cache.notify_bump(3)

        # Advance past the window from the LAST bump (reset on each bump)
        clock.advance(5.5)
        await cache.tick()

        assert (
            engine.call_count == 1
        ), f"Burst of 3 bumps must collapse to 1 recompute, got {engine.call_count}"

    async def test_burst_then_settle(self) -> None:
        """Five bumps in quick succession → one recompute after settle."""
        cache, engine, clock = _make_cache(debounce=5.0)
        for i in range(5):
            cache.notify_bump(i + 1)
            clock.advance(0.5)

        clock.advance(5.5)
        await cache.tick()
        assert engine.call_count == 1

        # Further ticks without bumps do nothing
        clock.advance(20.0)
        await cache.tick()
        assert engine.call_count == 1


# ── BE-PERF-9: debounce max-wait ────────────────────────────────────────────────


class TestDebounceMaxWait:
    """BE-PERF-9: a CONTINUOUS burst of bumps must not push the recompute back forever —
    it fires at most debounce_max_wait_seconds after the burst's first bump."""

    async def test_continuous_bumps_force_recompute_at_max_wait(self) -> None:
        engine = FakeEngine()
        clock = FakeClock()
        cache = GraphCache(
            engine=engine,  # type: ignore[arg-type]
            vault_id="test",
            debounce_seconds=5.0,
            clock=clock,
            debounce_max_wait_seconds=12.0,
        )
        cache._read_data_version = AsyncMock(return_value=99)  # type: ignore[method-assign]

        # A bump every 3s (well under the 5s debounce window) would, WITHOUT the max-wait
        # ceiling, keep resetting fire_at forever. With it, fire_at is capped at
        # first_bump_at (t=0) + 12.0 = 12.0, regardless of how many more bumps arrive.
        cache.notify_bump(1)
        assert cache._fire_at == 5.0  # first bump: candidate = 0+5, cap = 0+12 → 5.0
        clock.advance(3.0)
        cache.notify_bump(2)
        assert cache._fire_at == 8.0  # candidate = 3+5=8, cap = 0+12=12 → 8.0
        clock.advance(3.0)  # t=6
        cache.notify_bump(3)
        assert cache._fire_at == 11.0  # candidate = 6+5=11, cap=12 → 11.0
        clock.advance(3.0)  # t=9
        cache.notify_bump(4)
        assert cache._fire_at == 12.0, "capped at burst_start + max_wait, not 9+5=14"

        # tick() at t=9 does not fire yet (fire_at=12 > 9).
        await cache.tick()
        assert engine.call_count == 0

        # More bumps keep arriving, but the ceiling holds.
        clock.advance(2.0)  # t=11
        cache.notify_bump(5)
        assert cache._fire_at == 12.0, "still capped even though a bump just arrived"

        clock.advance(1.0)  # t=12 → fire_at reached
        await cache.tick()
        assert engine.call_count == 1, "recompute must fire at the max-wait ceiling"


# ── AC-F16db-3: bump during in-flight → one follow-up ─────────────────────────


class TestInFlightPending:
    """
    AC-F16db-3: A bump that arrives while a recompute is in-flight results in exactly
    one follow-up recompute (queue depth = 1, I7).
    """

    async def test_bump_during_inflight_causes_one_followup(self) -> None:
        cache, engine, clock = _make_cache(debounce=5.0)

        # Start a debounced recompute
        cache.notify_bump(1)
        clock.advance(5.5)

        # Run tick in a task so it overlaps with our bump
        # We simulate: tick starts → sets _in_flight=True → we bump → tick completes
        # Since our FakeEngine is not truly concurrent, we simulate in-flight manually.

        # Manually set in-flight to simulate a running recompute
        cache._in_flight = True
        cache._fire_at = None  # consumed by the "in-flight" run

        # Bump arrives while in-flight
        cache.notify_bump(2)
        assert cache._pending is True, "Bump during in-flight must set pending flag"

        # Simulate the in-flight run completing
        cache._in_flight = False
        cache._snapshot = GraphSnapshot()
        cache._marker = 1

        # tick() should schedule the follow-up
        # The follow-up is scheduled by tick() when _pending was True on completion.
        # But in our manual simulation, we need to trigger the pending → schedule logic.
        # Let's do a full realistic flow instead:

        # Reset and do it properly with tick()
        cache2, engine2, clock2 = _make_cache(debounce=1.0)

        # First bump → will trigger recompute at t=1
        cache2.notify_bump(1)
        clock2.advance(1.5)

        # Hijack: set in_flight=True before tick fires, then bump, then complete
        # We can do this by running tick() which sets in_flight, and having a coroutine
        # that bumps concurrently. Since asyncio is cooperative, we can sequence it.

        fired = []

        async def _slow_recompute(vault_id: str, *, session: Any = None) -> GraphSnapshot:
            fired.append("start")
            # Yield so we can do something "between" start and end
            await asyncio.sleep(0)
            fired.append("end")
            return GraphSnapshot()

        engine2.recompute = _slow_recompute  # type: ignore[method-assign]

        # Task 1: tick that fires recompute
        tick_task = asyncio.create_task(cache2.tick())

        # Let the recompute start
        await asyncio.sleep(0)
        assert "start" in fired, "Recompute should have started"
        assert cache2._in_flight is True

        # Now bump while in-flight
        cache2.notify_bump(2)
        assert cache2._pending is True, "notify_bump during in-flight must set pending"

        # Let tick complete
        await tick_task
        assert "end" in fired
        assert cache2._in_flight is False

        # The pending was consumed: a follow-up should now be scheduled
        assert cache2._fire_at is not None, "Pending flag must trigger a follow-up schedule"
        # engine2 was replaced with _slow_recompute so call_count is 0 (not tracked)
        assert engine2.call_count == 0

        # Advance past follow-up window: but we replaced the engine, so manually check
        # that fire_at is set (the mechanism is correct)
        assert cache2._fire_at > clock2(), "fire_at must be in the future"


# ── HIT / MISS behaviour (ADR-0014 §5) ────────────────────────────────────────


class TestHitMiss:
    """ADR-0014 §5: get_graph() returns (snapshot, cached=True) on HIT, False on MISS."""

    async def test_miss_on_first_call(self) -> None:
        """First call with no snapshot → MISS; recompute called once."""
        cache, engine, clock = _make_cache()

        snapshot, cached = await cache.get_graph(current_version=1)
        assert cached is False, "First call must be a MISS (no snapshot yet)"
        assert engine.call_count == 1, "MISS must trigger exactly one recompute"
        assert snapshot is not None

    async def test_hit_on_same_version(self) -> None:
        """Second call at same data_version → HIT; no additional recompute."""
        cache, engine, clock = _make_cache()

        # First call: miss
        await cache.get_graph(current_version=5)
        assert engine.call_count == 1

        # Second call at same version: hit
        snapshot2, cached2 = await cache.get_graph(current_version=5)
        assert cached2 is True, "Same data_version → HIT (G2 requirement)"
        assert engine.call_count == 1, "HIT must NOT trigger a second recompute"

    async def test_miss_on_version_advance(self) -> None:
        """
        After data_version advances, the next get_graph() is still reported as a MISS, but
        (BE-PERF-9) it now serves the last good snapshot immediately — stale-while-revalidate
        — instead of blocking on a synchronous recompute. The recompute happens in the
        background; once it's awaited, the marker catches up and the FOLLOWING get_graph() at
        the same version is a HIT.
        """
        cache, engine, clock = _make_cache()

        # Seed with version=3 (no prior snapshot yet → this one DOES block inline).
        await cache.get_graph(current_version=3)
        assert engine.call_count == 1

        # Version advances to 4 → miss, served from the (fresh, age=0) stale snapshot
        # immediately; the recompute is only KICKED, not yet run.
        snapshot2, cached2 = await cache.get_graph(current_version=4)
        assert cached2 is False, "Advanced data_version → MISS"
        assert snapshot2 is not None
        assert engine.call_count == 1, (
            "BE-PERF-9: a MISS with a usable (non-stale) snapshot must NOT block this "
            "request on a synchronous recompute — the recompute is only kicked in the "
            "background"
        )
        assert cache._revalidate_task is not None

        # The live data_version is now 4 (mirrors what _read_data_version would see for real).
        cache._read_data_version = AsyncMock(return_value=4)  # type: ignore[method-assign]

        # Let the background revalidate actually run.
        await cache._revalidate_task
        assert engine.call_count == 2, "The background revalidate must still run exactly once"
        assert cache._marker == 4

        # A subsequent get_graph() at the now-current version is a HIT — no further recompute.
        snapshot3, cached3 = await cache.get_graph(current_version=4)
        assert cached3 is True
        assert engine.call_count == 2

    async def test_miss_beyond_stale_bound_blocks_inline(self) -> None:
        """
        BE-PERF-9: when the last good snapshot is OLDER than stale_max_seconds, a MISS falls
        back to the original blocking behaviour (one inline synchronous recompute) rather than
        serving arbitrarily old data forever.
        """
        engine = FakeEngine()
        clock = FakeClock()
        cache = GraphCache(
            engine=engine,  # type: ignore[arg-type]
            vault_id="test",
            debounce_seconds=5.0,
            clock=clock,
            stale_max_seconds=10.0,
        )
        cache._read_data_version = AsyncMock(return_value=1)  # type: ignore[method-assign]

        # Seed a snapshot at t=0 (no prior snapshot → blocks inline, as always).
        await cache.get_graph(current_version=1)
        assert engine.call_count == 1

        # Advance well past the staleness bound, then bump the version → MISS.
        clock.advance(50.0)
        snapshot, cached = await cache.get_graph(current_version=2)
        assert cached is False
        assert snapshot is not None
        assert engine.call_count == 2, (
            "A snapshot older than stale_max_seconds must NOT be served — the request "
            "blocks on a fresh inline recompute instead"
        )
        assert cache._revalidate_task is None, "The stale path must not have been taken"

    async def test_force_recompute_never_serves_stale(self) -> None:
        """force_recompute() (the 'Regenerate graph' button) always blocks for a fresh
        synchronous recompute — BE-PERF-9's stale-while-revalidate path must never apply."""
        cache, engine, clock = _make_cache()

        await cache.get_graph(current_version=1)
        assert engine.call_count == 1

        # Even though the snapshot is fresh (age=0, well within the default stale bound),
        # force_recompute() must still trigger an actual new recompute inline.
        await cache.force_recompute(current_version=1)
        assert engine.call_count == 2
        assert cache._revalidate_task is None, "force_recompute must not take the SWR path"

    async def test_background_tick_hit_on_next_get(self) -> None:
        """Background tick fires recompute; subsequent get_graph is a HIT."""
        cache, engine, clock = _make_cache(debounce=5.0)

        # Bump + settle
        cache.notify_bump(7)
        clock.advance(5.5)
        cache._read_data_version = AsyncMock(return_value=7)  # type: ignore[method-assign]
        await cache.tick()  # background recompute fires

        assert engine.call_count == 1
        assert cache._marker == 7

        # get_graph at version=7 → HIT
        _, cached = await cache.get_graph(current_version=7)
        assert cached is True, "After background recompute, get_graph must be HIT"
        assert engine.call_count == 1, "HIT must not call recompute again"

    async def test_marker_none_is_miss(self) -> None:
        """Cache with marker=None is always a MISS (fresh startup)."""
        cache, engine, clock = _make_cache()
        assert cache._marker is None
        _, cached = await cache.get_graph(current_version=0)
        assert cached is False

    async def test_snapshot_content_on_hit(self) -> None:
        """HIT returns the same snapshot object that was stored by the MISS."""
        cache, engine, clock = _make_cache()

        snap_miss, _ = await cache.get_graph(current_version=10)
        snap_hit, cached = await cache.get_graph(current_version=10)

        assert cached is True
        assert snap_miss is snap_hit, "HIT must return the cached snapshot object"


# ── No real sleep ──────────────────────────────────────────────────────────────


class TestNoRealSleep:
    """ADR-0014 §4: tests must not call real asyncio.sleep against wall clock."""

    async def test_tick_driven_by_injected_clock(self) -> None:
        """
        Verify that the debounce is purely a function of the injected clock,
        NOT wall-clock time. The test completes without any real sleep() call.
        """
        cache, engine, clock = _make_cache(debounce=3600.0)  # 1-hour window — unimportant

        cache.notify_bump(1)

        # Does not fire before the clock advances
        await cache.tick()
        assert engine.call_count == 0, "Should not fire before clock advance"

        # Advance the fake clock by exactly the debounce window
        clock.advance(3600.0)
        await cache.tick()
        assert engine.call_count == 1, "Should fire exactly when clock >= fire_at"
