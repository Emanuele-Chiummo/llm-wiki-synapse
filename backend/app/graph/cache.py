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
  - FA2 runs ONLY inside GraphEngine.recompute() (never here).
  - cache.get_graph() hit = pure read from the stored snapshot (no FA2).
  - cache.get_graph() miss = one inline synchronous recompute, then return.
  - Recompute is debounced: burst of bumps → ONE recompute after settle window.
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
    ) -> None:
        self._engine = engine
        self._vault_id = vault_id
        self._debounce = debounce_seconds
        self._clock: Callable[[], float] = clock if clock is not None else time.monotonic

        # State
        self._snapshot: GraphSnapshot | None = None
        self._marker: int | None = None
        self._fire_at: float | None = None
        self._in_flight: bool = False
        self._pending: bool = False

        # Background asyncio task handle (prod only)
        self._bg_task: asyncio.Task[None] | None = None

    # ── Public interface ───────────────────────────────────────────────────────

    def notify_bump(self, new_version: int) -> None:
        """
        Called immediately after vault_state.data_version is incremented (ADR-0014 §2).

        Schedules a debounced recompute: if no run is in-flight, set/reset the deadline.
        If a run IS in-flight, set the pending flag (one follow-up on completion, I7 §3).
        """
        now = self._clock()
        if self._in_flight:
            logger.debug(
                "GraphCache.notify_bump: run in-flight, setting pending (v=%d)", new_version
            )
            self._pending = True
        else:
            # Set/reset the debounce deadline (collapse bursts — AC-F16db-2)
            self._fire_at = now + self._debounce
            logger.debug(
                "GraphCache.notify_bump: debounce scheduled fire_at=now+%.1fs (v=%d)",
                self._debounce,
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
            try:
                logger.debug("GraphCache.tick: firing recompute vault_id=%r", self._vault_id)
                snapshot = await self._engine.recompute(self._vault_id)
                # Read current data_version from the snapshot or keep existing
                # We use the snapshot's node/edge count as a proxy; the caller
                # (get_graph) passes the live data_version to get_graph().
                # The cache stores the snapshot and the caller updates the marker
                # via get_graph(). But for background tick, we need the version.
                # We read it from the DB after recompute.
                new_version = await self._read_data_version()
                self._snapshot = snapshot
                self._marker = new_version
                logger.info(
                    "GraphCache.tick: recompute done marker=%d nodes=%d edges=%d",
                    new_version,
                    len(snapshot.nodes),
                    len(snapshot.edges),
                )
            except Exception:
                logger.exception("GraphCache.tick: recompute failed")
            finally:
                self._in_flight = False

            # One follow-up if bumped during the run (AC-F16db-3, I7)
            if self._pending:
                self._pending = False
                self._fire_at = self._clock() + self._debounce
                logger.debug("GraphCache.tick: pending flag was set — scheduling one follow-up")

    async def get_graph(self, current_version: int) -> tuple[GraphSnapshot, bool]:
        """
        Return the graph snapshot and a cache-hit boolean (ADR-0014 §5).

        HIT  (marker == current_version):
          Returns (_snapshot, True). No FA2. Sets X-Graph-Cache: hit.

        MISS (marker is None OR marker != current_version):
          Runs ONE inline synchronous recompute (shared in-flight guard — no concurrent FA2),
          updates _snapshot + _marker, returns (snapshot, False).
          Sets X-Graph-Cache: miss.

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

        # Run one inline recompute
        if not self._in_flight:
            self._in_flight = True
            try:
                logger.debug(
                    "GraphCache.get_graph: miss — running inline recompute vault_id=%r",
                    self._vault_id,
                )
                snapshot = await self._engine.recompute(self._vault_id)
                self._snapshot = snapshot
                self._marker = current_version
                self._fire_at = None  # cancel any pending debounce — we just recomputed
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
                self._fire_at = self._clock() + self._debounce

        assert self._snapshot is not None  # mypy
        return self._snapshot, False

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

        Invalidates the marker then delegates to get_graph(), which runs exactly one inline
        recompute under the shared in-flight guard (never two concurrent FA2 runs — I2).
        Returns the fresh snapshot.
        """
        logger.info("GraphCache.force_recompute: invalidating marker + recomputing FA2")
        self._marker = None
        snapshot, _ = await self.get_graph(current_version)
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
