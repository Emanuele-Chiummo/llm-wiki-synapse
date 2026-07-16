"""
GraphCache — dataVersion-debounced in-process cache for graph layout (F4, I2, ADR-0014).

Public API:
  GraphCache(engine, vault_id, debounce_seconds, clock)
    .notify_bump(new_version)   — call after vault_state.data_version +1
    .tick()                     — drive the debounce timer (test injection or prod loop)
    .get_graph(current_version) → (GraphSnapshot | None, cached: bool)
    .start_background_loop()    — launch the asyncio background tick task (prod)
    .stop_background_loop()     — cancel the background tick task

I2 guarantee:
  - FA2 runs ONLY inside GraphEngine.recompute() (never here — BE-PERF-9 only changes WHEN/
    how often the server schedules that call, never WHERE it runs).
  - cache.get_graph() hit = pure read from the stored snapshot (no FA2).
  - cache.get_graph() miss = either (a) a bounded stale-while-revalidate serve of the last
    good snapshot while a recompute runs in the background (BE-PERF-9), or (b) — when there
    is no usable snapshot yet, or the one we have is older than GRAPH_STALE_MAX_SECONDS — one
    inline synchronous recompute, then return.
  - Recompute is debounced: burst of bumps → ONE recompute after settle window, but a
    CONTINUOUS burst can no longer push that window back forever (BE-PERF-9): each burst has a
    hard ceiling at GRAPH_DEBOUNCE_MAX_WAIT_SECONDS since the burst's first bump.
  - Bounded queue (I7): at most ONE in-flight + ONE pending. N bumps during a run
    collapse to ONE follow-up (AC-F16db-3).

Testability (ADR-0014 §4):
  - Injectable clock: GraphCache(clock=Callable[[], float]) where the callable
    returns the current time as a float (seconds). Defaults to time.monotonic.
  - tick() is the explicit advance method; tests call it directly, prod uses an
    asyncio loop task. No real asyncio.sleep in tests.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable

from app.graph.engine import GraphEngine, GraphSnapshot

logger = logging.getLogger(__name__)

# Default debounce window in seconds (configurable via GRAPH_DEBOUNCE_SECONDS env)
_DEFAULT_DEBOUNCE = 5.0
GRAPH_DEBOUNCE_SECONDS: float = float(
    os.environ.get("GRAPH_DEBOUNCE_SECONDS", str(_DEFAULT_DEBOUNCE))
)

# BE-PERF-9: hard ceiling on how long a CONTINUOUS burst of bumps may keep pushing the
# debounce deadline back. Without this, an ingest run that bumps data_version every few
# seconds can defer the background recompute indefinitely, eventually forcing GET /graph to
# do the FA2 recompute inline (blocking) because the cached snapshot has gone stale for too
# long. Configurable via GRAPH_DEBOUNCE_MAX_WAIT_SECONDS.
_DEFAULT_DEBOUNCE_MAX_WAIT = 30.0
GRAPH_DEBOUNCE_MAX_WAIT_SECONDS: float = float(
    os.environ.get("GRAPH_DEBOUNCE_MAX_WAIT_SECONDS", str(_DEFAULT_DEBOUNCE_MAX_WAIT))
)

# BE-PERF-9: on a MISS, how old the last good snapshot may be and still be served immediately
# (stale-while-revalidate) instead of blocking the request on an inline FA2 recompute.
# Configurable via GRAPH_STALE_MAX_SECONDS.
_DEFAULT_STALE_MAX = 120.0
GRAPH_STALE_MAX_SECONDS: float = float(
    os.environ.get("GRAPH_STALE_MAX_SECONDS", str(_DEFAULT_STALE_MAX))
)

# Background tick interval (seconds) — how often the prod asyncio task calls tick()
_TICK_INTERVAL = 0.5


class GraphCache:
    """
    In-process debounced cache for graph layout (ADR-0014).

    State machine:
      _snapshot       : last computed GraphSnapshot (None until first compute)
      _marker         : data_version the snapshot corresponds to (None = never computed)
      _fire_at        : debounce deadline (None = no pending recompute)
      _in_flight      : True while recompute() is running
      _pending        : True if a bump arrived while in-flight (collapse to one follow-up)

    A cache HIT is:  _marker is not None AND _marker == current data_version
    A cache MISS is: _marker is None OR _marker != current data_version
    """

    def __init__(
        self,
        engine: GraphEngine,
        vault_id: str,
        debounce_seconds: float = GRAPH_DEBOUNCE_SECONDS,
        clock: Callable[[], float] | None = None,
        debounce_max_wait_seconds: float = GRAPH_DEBOUNCE_MAX_WAIT_SECONDS,
        stale_max_seconds: float = GRAPH_STALE_MAX_SECONDS,
    ) -> None:
        self._engine = engine
        self._vault_id = vault_id
        self._debounce = debounce_seconds
        self._debounce_max_wait = debounce_max_wait_seconds
        self._stale_max = stale_max_seconds
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic

        # State
        self._snapshot: GraphSnapshot | None = None
        self._marker: int | None = None
        self._fire_at: float | None = None
        self._in_flight: bool = False
        self._pending: bool = False
        # BE-PERF-9: timestamp of the FIRST bump in the current uncommitted burst (reset to
        # None once a recompute actually fires) — used to cap how long notify_bump may keep
        # pushing _fire_at back.
        self._first_bump_at: float | None = None
        # BE-PERF-9: clock() timestamp the current _snapshot was produced — used by the
        # stale-while-revalidate MISS path to decide whether it's still safe to serve.
        self._snapshot_at: float | None = None
        # BE-PERF-9: handle to a fire-and-forget background revalidate task kicked from
        # get_graph(); kept only so it isn't garbage-collected mid-flight.
        self._revalidate_task: asyncio.Task[None] | None = None

        # BE-PERF-5: cached vault-wide totals (total_nodes/total_edges, the two indexed
        # COUNT queries GET /graph otherwise re-runs on every request) and the fully
        # pre-serialized JSON response body, both keyed to _marker. Invalidated together
        # whenever the snapshot changes (fresh recompute) or its node positions are patched
        # (patch_node_position) — see the invalidation points below.
        self._total_nodes: int | None = None
        self._total_edges: int | None = None
        self._cached_body: bytes | None = None

        # Background asyncio task handle (prod only)
        self._bg_task: asyncio.Task[None] | None = None

    # ── Public interface ───────────────────────────────────────────────────────

    def notify_bump(self, new_version: int) -> None:
        """
        Called immediately after vault_state.data_version is incremented (ADR-0014 §2).

        Schedules a debounced recompute: if no run is in-flight, set/reset the deadline
        (capped at GRAPH_DEBOUNCE_MAX_WAIT_SECONDS since the burst's first bump — BE-PERF-9,
        so a continuous ingest run cannot defer the recompute forever).
        If a run IS in-flight, set the pending flag (one follow-up on completion, I7 §3).
        """
        now = self._clock()
        if self._in_flight:
            logger.debug(
                "GraphCache.notify_bump: run in-flight, setting pending (v=%d)", new_version
            )
            self._pending = True
            return

        # BE-PERF-9: track the start of this burst so the deadline can be capped below.
        if self._first_bump_at is None:
            self._first_bump_at = now

        # Set/reset the debounce deadline (collapse bursts — AC-F16db-2), but never push it
        # past `debounce_max_wait_seconds` since the burst started (BE-PERF-9 max-wait).
        candidate_fire_at = now + self._debounce
        max_fire_at = self._first_bump_at + self._debounce_max_wait
        self._fire_at = min(candidate_fire_at, max_fire_at)
        logger.debug(
            "GraphCache.notify_bump: debounce scheduled fire_at=%.2f (now=%.2f, "
            "burst_start=%.2f, max_wait=%.1fs, v=%d)",
            self._fire_at,
            now,
            self._first_bump_at,
            self._debounce_max_wait,
            new_version,
        )

    async def tick(self) -> None:
        """
        Advance the debounce timer.

        In tests: called explicitly after advancing the fake clock.
        In production: called every _TICK_INTERVAL seconds by _background_loop().

        Fires the recompute when:
          - fire_at is set AND clock.now() >= fire_at AND NOT in-flight
        After a recompute completes, if pending is set, schedules one follow-up.
        """
        now = self._clock()

        if self._fire_at is not None and now >= self._fire_at and not self._in_flight:
            self._fire_at = None
            self._in_flight = True
            self._pending = False
            logger.debug("GraphCache.tick: firing recompute vault_id=%r", self._vault_id)
            await self._run_recompute_and_settle()

    async def _run_recompute_and_settle(self) -> None:
        """
        Run ONE FA2 recompute and update the cached snapshot/marker — shared by tick(),
        the inline MISS path, and the BE-PERF-9 background stale-while-revalidate kick.

        Precondition: caller has ALREADY set ``self._in_flight = True`` synchronously (no
        intervening ``await``) so this method never races with another recompute (I2/I7).
        Always clears ``_in_flight`` on the way out and schedules exactly one follow-up
        debounce if a bump arrived while this run was in flight (AC-F16db-3).
        """
        self._pending = False  # safe: nothing could have set it between in_flight=True and here
        try:
            # B8 fix: capture data_version BEFORE the recompute starts.
            # If a concurrent bump increments the version DURING the recompute, stamping
            # the marker with the post-recompute version would make a stale snapshot look
            # fresh (next get_graph() would be a HIT returning stale data).  Using the
            # pre-recompute version means the marker will NOT match the bumped version, so
            # the next get_graph() will be a MISS and trigger a fresh recompute.
            version_before = await self._read_data_version()
            snapshot = await self._engine.recompute(self._vault_id)
            self._snapshot = snapshot
            self._marker = version_before  # stamp with pre-recompute version (B8 fix)
            self._snapshot_at = self._clock()
            self._first_bump_at = None  # this burst (if any) is now settled
            # BE-PERF-5: totals + serialized body are stale after ANY recompute — whether
            # triggered by tick(), a background revalidate, or the inline MISS path below —
            # the caller (GET /graph) repopulates them via store_response() on its next miss.
            self._total_nodes = None
            self._total_edges = None
            self._cached_body = None
            logger.info(
                "GraphCache: recompute done marker=%d nodes=%d edges=%d",
                version_before,
                len(snapshot.nodes),
                len(snapshot.edges),
            )
        except Exception:
            logger.exception("GraphCache: recompute failed")
        finally:
            self._in_flight = False

        # One follow-up if bumped during the run (AC-F16db-3, I7)
        if self._pending:
            self._pending = False
            self._first_bump_at = self._clock()
            self._fire_at = self._clock() + self._debounce
            logger.debug("GraphCache: pending flag was set — scheduling one follow-up")

    async def get_graph(
        self, current_version: int, *, force_fresh: bool = False
    ) -> tuple[GraphSnapshot, bool]:
        """
        Return the graph snapshot and a cache-hit boolean (ADR-0014 §5).

        HIT  (marker == current_version):
          Returns (_snapshot, True). No FA2. Sets X-Graph-Cache: hit.

        MISS (marker is None OR marker != current_version):
          BE-PERF-9 stale-while-revalidate: when a usable snapshot already exists and is not
          older than ``stale_max_seconds``, return it IMMEDIATELY (cached=False, i.e. still
          reported as a miss — the payload just isn't freshly computed) and kick a recompute
          in the background (same in-flight guard as tick() — never a second concurrent FA2,
          I2). This is what keeps GET /graph non-blocking under a continuous ingest burst.

          Otherwise (no snapshot yet, or it is older than the bound) runs ONE inline
          synchronous recompute (shared in-flight guard — no concurrent FA2), updates
          _snapshot + _marker, and returns (snapshot, False). Sets X-Graph-Cache: miss.

        On a miss during an in-flight background recompute: waits for the in-flight run
        to complete, then serves its result rather than running a second FA2.
        """
        # HIT path
        hit = (
            self._marker is not None
            and self._marker == current_version
            and self._snapshot is not None
        )
        if hit:
            return self._snapshot, True  # type: ignore[return-value]

        # MISS path — in-flight guard
        if self._in_flight:
            # Wait for the in-flight background recompute to complete
            logger.debug(
                "GraphCache.get_graph: miss but recompute in-flight — waiting for completion"
            )
            await self._wait_for_in_flight()
            if self._snapshot is not None and self._marker == current_version:
                return self._snapshot, True  # the just-completed run now satisfies us

        # BE-PERF-9: stale-while-revalidate — serve the last good snapshot right away
        # (bounded by stale_max_seconds) instead of blocking THIS request on a fresh FA2 run.
        # I2 is unaffected: the recompute still runs entirely server-side, just asynchronously.
        # `force_fresh` (force_recompute() / the "Regenerate graph" button) always wants a
        # synchronous fresh recompute NOW, so it skips this branch entirely.
        if not force_fresh and self._snapshot is not None and not self._in_flight:
            age = self._clock() - (self._snapshot_at if self._snapshot_at is not None else 0.0)
            if age <= self._stale_max:
                logger.debug(
                    "GraphCache.get_graph: miss — serving stale snapshot (age=%.1fs <= %.1fs) "
                    "and kicking a background revalidate vault_id=%r",
                    age,
                    self._stale_max,
                    self._vault_id,
                )
                self._kick_background_revalidate()
                return self._snapshot, False

        # No usable snapshot (first call, or it exceeded the staleness bound) → block inline.
        if not self._in_flight:
            self._in_flight = True
            logger.debug(
                "GraphCache.get_graph: miss — running inline recompute vault_id=%r",
                self._vault_id,
            )
            try:
                snapshot = await self._engine.recompute(self._vault_id)
                self._snapshot = snapshot
                self._marker = current_version
                self._snapshot_at = self._clock()
                self._first_bump_at = None
                self._fire_at = None  # cancel any pending debounce — we just recomputed
                # BE-PERF-5: totals + serialized body are stale after any recompute — the
                # caller (GET /graph) repopulates them via store_response() below.
                self._total_nodes = None
                self._total_edges = None
                self._cached_body = None
            except Exception:
                logger.exception("GraphCache.get_graph: inline recompute failed")
                # Return empty snapshot on failure; do not crash the endpoint
                if self._snapshot is None:
                    self._snapshot = GraphSnapshot(data_version=current_version)
            finally:
                self._in_flight = False

            # One follow-up if bumped during inline run (I7)
            if self._pending:
                self._pending = False
                self._first_bump_at = self._clock()
                self._fire_at = self._clock() + self._debounce

        assert self._snapshot is not None  # mypy
        return self._snapshot, False

    def get_cached_response(self, current_version: int) -> bytes | None:
        """
        Return the fully pre-serialized ``GET /graph`` JSON body for a pure cache HIT
        (BE-PERF-5), or ``None`` when the caller must (re)build it.

        A non-``None`` result means: no totals COUNT queries and no Pydantic
        node/edge/community re-serialization are needed — the caller can respond with the
        cached bytes verbatim (``X-Graph-Cache: hit``).
        """
        if (
            self._marker is not None
            and self._marker == current_version
            and self._snapshot is not None
            and self._cached_body is not None
        ):
            return self._cached_body
        return None

    def get_cached_totals(self, current_version: int) -> tuple[int, int] | None:
        """
        Return cached ``(total_nodes, total_edges)`` (BE-PERF-5) when they still correspond
        to *current_version*, or ``None`` when the caller must re-run the COUNT queries.

        Totals stay valid across a ``patch_node_position`` call (a position patch changes no
        row counts) even though ``get_cached_response`` is invalidated by it — so a drag-drop
        interaction still skips the totals queries on its next GET.
        """
        if (
            self._marker is not None
            and self._marker == current_version
            and self._total_nodes is not None
            and self._total_edges is not None
        ):
            return self._total_nodes, self._total_edges
        return None

    def store_response(
        self,
        current_version: int,
        body: bytes,
        total_nodes: int,
        total_edges: int,
    ) -> None:
        """
        Cache the serialized JSON *body* and *total_nodes*/*total_edges* alongside the
        snapshot marker (BE-PERF-5), so the next matching GET is a pure-read HIT.

        No-op if *current_version* no longer matches ``_marker`` (a concurrent bump/recompute
        raced ahead of this store) — never cache a response for a version that isn't current.
        """
        if self._marker == current_version:
            self._cached_body = body
            self._total_nodes = total_nodes
            self._total_edges = total_edges

    def _kick_background_revalidate(self) -> None:
        """
        Fire-and-forget: start ONE background recompute (BE-PERF-9), guarded by the SAME
        ``_in_flight`` flag tick()/get_graph() use, so this can never run concurrently with
        another recompute (I2/I7). No-op if a recompute is already in flight (the caller
        already gets the benefit — one revalidate is in progress).
        """
        if self._in_flight:
            return
        self._in_flight = True  # set synchronously — no await between check and set (B9-style)
        self._revalidate_task = asyncio.create_task(self._run_recompute_and_settle())

    def patch_node_position(self, node_id: str, x: float, y: float) -> bool:
        """
        Mutate the in-memory snapshot's NodeSnapshot for node_id in place (Feature A).

        Called by PATCH /pages/{id}/position AFTER the DB update so the next
        GET /graph HIT reflects the new position without a recompute or data_version bump.

        Returns True if a matching node was found and updated, False otherwise.
        No-op if no snapshot exists yet (first GET /graph will load from DB naturally).
        Does NOT trigger FR or change _marker / data_version.  O(n) scan; n is small.
        """
        if self._snapshot is None:
            return False
        for node in self._snapshot.nodes:
            if node.id == node_id:
                node.x = x
                node.y = y
                # BE-PERF-5: the pre-serialized response body now has a stale (x, y) for this
                # node — invalidate it so the next GET /graph rebuilds + re-caches the body.
                # Totals (_total_nodes/_total_edges) are untouched: a position patch changes
                # no row counts, so they stay valid and the next GET still skips the COUNT
                # queries via get_cached_totals().
                self._cached_body = None
                logger.debug(
                    "GraphCache.patch_node_position: updated node_id=%s x=%.4f y=%.4f",
                    node_id,
                    x,
                    y,
                )
                return True
        return False

    async def force_recompute(self, current_version: int) -> GraphSnapshot:
        """
        Force a fresh FA2 recompute NOW, bypassing the version-marker hit check.

        Used by POST /graph/recompute (the "Regenerate graph" button) so the user can
        re-run the server-side layout on demand — e.g. after a layout-algorithm change
        (ADR-0045 §5 outlier clamp) whose effect is not reflected in the persisted coords
        until the next recompute, even when data_version has not moved.

        Invalidates the marker then delegates to get_graph(force_fresh=True), which runs
        exactly one inline recompute under the shared in-flight guard (never two concurrent
        FA2 runs — I2) — BE-PERF-9's stale-while-revalidate serve is explicitly skipped here:
        this call is the ONE place that must always block for a synchronously fresh snapshot.
        Returns the fresh snapshot.
        """
        logger.info("GraphCache.force_recompute: invalidating marker + recomputing FA2")
        self._marker = None
        snapshot, _ = await self.get_graph(current_version, force_fresh=True)
        return snapshot

    def start_background_loop(self) -> None:
        """Launch the asyncio background tick task (called from FastAPI lifespan)."""
        if self._bg_task is None or self._bg_task.done():
            self._bg_task = asyncio.create_task(self._background_loop())
            logger.info("GraphCache: background tick loop started")

    def stop_background_loop(self) -> None:
        """Cancel the background tick task (called from FastAPI lifespan shutdown)."""
        if self._bg_task is not None and not self._bg_task.done():
            self._bg_task.cancel()
            logger.info("GraphCache: background tick loop stopped")

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _background_loop(self) -> None:
        """Continuously drive tick() in production."""
        try:
            while True:
                await asyncio.sleep(_TICK_INTERVAL)
                await self.tick()
        except asyncio.CancelledError:
            pass

    async def _wait_for_in_flight(self) -> None:
        """Poll until the in-flight flag clears (max 10s safety bound)."""
        waited = 0.0
        while self._in_flight and waited < 10.0:
            await asyncio.sleep(0.05)
            waited += 0.05

    async def _read_data_version(self) -> int:
        """Read the current data_version from vault_state for the marker stamp."""
        from sqlalchemy import text as sa_text

        from app.db import get_session

        try:
            async with get_session() as sess:
                result = await sess.execute(
                    sa_text(
                        "SELECT data_version FROM vault_state WHERE vault_id = :vid LIMIT 1"
                    ).bindparams(vid=self._vault_id)
                )
                row = result.fetchone()
                return int(row[0]) if row is not None else 0
        except Exception:
            logger.exception("GraphCache._read_data_version: failed, returning 0")
            return 0
