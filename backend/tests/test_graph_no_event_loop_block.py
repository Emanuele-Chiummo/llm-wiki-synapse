"""
Regression test for defect B1: graph recompute must NOT block the asyncio event loop.

Before the fix: GraphEngine.recompute() invoked igraph/FA2/Louvain synchronously
inside the coroutine, blocking the event loop for the full layout duration (seconds
on a 1-2.5k-page vault).  All concurrent requests, chat streams, and watcher events
stalled until the CPU work finished.

After the fix: the CPU-bound portion (_compute_graph_sync) runs in a thread via
asyncio.to_thread(), so the event loop stays free throughout.

Test strategy (infra-free):
  - Monkeypatch _load_data and _persist_results so no DB is needed.
  - Monkeypatch _compute_graph_sync with a version that calls time.sleep() to
    simulate heavy CPU work while still returning a valid (empty) GraphSnapshot.
  - Run engine.recompute() and a concurrent ticker task using asyncio.gather().
  - Assert that the ticker accumulated ticks in the FIRST HALF of the recompute
    window — proving the event loop was not blocked during the sleep.

If the event loop were blocked the ticker's asyncio.sleep() calls would stall until
the time.sleep() in the worker thread finished.  All ticks would then be clustered
AFTER the recompute, and "early_ticks" would be 0, failing the assertion.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from app.graph import engine as engine_mod
from app.graph.engine import GraphEngine, GraphSnapshot

# ---------------------------------------------------------------------------
# Timing constants
# ---------------------------------------------------------------------------

# Simulated CPU-work duration.  Long enough to clearly separate "during" from
# "after" in the tick timeline; short enough that CI is not noticeably slowed.
_COMPUTE_SLEEP = 0.25  # 250 ms

# asyncio.sleep interval between ticker ticks.  Gives ~8 ticks during the
# compute window when the event loop is free.
_TICK_INTERVAL = 0.03  # 30 ms

# Total number of ticks the ticker will fire.  Must exceed the compute window.
_TICK_COUNT = 16  # 16 × 30 ms = 480 ms  > 250 ms

# Minimum ticks that must land in the FIRST HALF of the compute window.
# Free event loop: ~_COMPUTE_SLEEP*0.5 / _TICK_INTERVAL = ~4.
# Blocked event loop: 0.
# Threshold is intentionally conservative to absorb CI scheduling jitter.
_MIN_EARLY_TICKS = 2


# ---------------------------------------------------------------------------
# Minimal node data (at least 1 node so the code doesn't hit the "no pages"
# early-return and actually reaches _compute_graph_sync)
# ---------------------------------------------------------------------------

_NODES_DATA = [
    {
        "id": "aaaaaaaa-0000-0000-0000-000000000001",
        "title": "Alpha",
        "page_type": "entity",
        "sources": [],
        "pinned": False,
        "stored_x": None,
        "stored_y": None,
    },
    {
        "id": "aaaaaaaa-0000-0000-0000-000000000002",
        "title": "Beta",
        "page_type": "concept",
        "sources": [],
        "pinned": False,
        "stored_x": None,
        "stored_y": None,
    },
]


@pytest.mark.asyncio
async def test_recompute_does_not_block_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    CPU-bound graph compute runs in a thread; the asyncio event loop stays responsive.

    Guards invariant I2 (server-side layout, never on the UI thread) and defect B1
    (synchronous FA2/igraph blocked the event loop on large vaults).
    """

    # ── DB stubs (no Postgres needed) ──────────────────────────────────────
    async def _fake_load(
        self: GraphEngine,
        vault_id: str,
        session: object,
    ) -> tuple[list[dict], list[dict]]:
        return _NODES_DATA, []

    async def _fake_persist(
        self: GraphEngine,
        vault_id: str,
        coord_rows: list[dict],
        edge_rows: list[dict],
        session: object,
    ) -> None:
        pass

    # ── Slow compute stub ────────────────────────────────────────────────────
    # Replaces the real igraph/FA2 work.  time.sleep() inside asyncio.to_thread()
    # should NOT block the event loop — if it does, the ticker below will see no
    # ticks during the sleep, and the assertion will fail.
    def _slow_compute(
        nodes: list[dict],
        links: list[dict],
        vault_id: str,
        domain_vocab: list[str] | None = None,
    ) -> tuple[list[dict], list[dict], GraphSnapshot]:
        time.sleep(_COMPUTE_SLEEP)
        return [], [], GraphSnapshot()

    monkeypatch.setattr(GraphEngine, "_load_data", _fake_load)
    monkeypatch.setattr(GraphEngine, "_persist_results", _fake_persist)
    # _compute_graph_sync is a module-level name; recompute() resolves it via
    # globals() at call time, so the monkeypatch is picked up automatically.
    monkeypatch.setattr(engine_mod, "_compute_graph_sync", _slow_compute)

    # ── Concurrent ticker ────────────────────────────────────────────────────
    loop = asyncio.get_running_loop()
    tick_times: list[float] = []

    async def _ticker() -> None:
        for _ in range(_TICK_COUNT):
            tick_times.append(loop.time())
            await asyncio.sleep(_TICK_INTERVAL)

    # Run recompute + ticker concurrently — asyncio.gather keeps both alive.
    t_start = loop.time()
    await asyncio.gather(
        GraphEngine().recompute("test-vault"),
        _ticker(),
    )
    t_end = loop.time()

    # ── Sanity: the slow compute was actually invoked ─────────────────────
    assert t_end - t_start >= _COMPUTE_SLEEP * 0.7, (
        f"Recompute finished in {t_end - t_start:.3f}s, expected >= "
        f"{_COMPUTE_SLEEP * 0.7:.3f}s. "
        "_slow_compute may not have been called (fast-return path taken)."
    )

    # ── Key assertion: ticks must occur DURING the recompute ─────────────
    # "Early" ticks = ticks that landed before the midpoint of the compute window.
    # Blocked event loop  → early_ticks = 0 (all ticks pile up after the sleep).
    # Free event loop     → early_ticks ≈ _COMPUTE_SLEEP*0.5 / _TICK_INTERVAL ≈ 4.
    midpoint = t_start + _COMPUTE_SLEEP * 0.5
    early_ticks = sum(1 for t in tick_times if t_start < t < midpoint)

    tick_offsets = [round(t - t_start, 3) for t in tick_times]
    assert early_ticks >= _MIN_EARLY_TICKS, (
        f"Event loop was blocked during recompute: only {early_ticks} tick(s) "
        f"before the {_COMPUTE_SLEEP * 0.5:.3f}s midpoint (need >= {_MIN_EARLY_TICKS}). "
        f"Tick offsets from start (s): {tick_offsets}. "
        "CPU graph work must run inside asyncio.to_thread(_compute_graph_sync, ...) "
        "so the event loop stays free [B1, I2]."
    )


@pytest.mark.asyncio
async def test_empty_vault_fast_path_does_not_call_compute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    With zero pages _compute_graph_sync is NOT called (early-return path).

    Guards the no-pages branch that skips CPU work entirely and returns an empty
    GraphSnapshot directly.  This is a correctness check, not a concurrency check.
    """
    called: list[bool] = []

    async def _fake_load(
        self: GraphEngine,
        vault_id: str,
        session: object,
    ) -> tuple[list[dict], list[dict]]:
        return [], []

    async def _fake_persist(
        self: GraphEngine,
        vault_id: str,
        coord_rows: list[dict],
        edge_rows: list[dict],
        session: object,
    ) -> None:
        pass

    def _sentinel_compute(
        nodes: list[dict],
        links: list[dict],
        vault_id: str,
    ) -> tuple[list[dict], list[dict], GraphSnapshot]:
        called.append(True)
        return [], [], GraphSnapshot()

    monkeypatch.setattr(GraphEngine, "_load_data", _fake_load)
    monkeypatch.setattr(GraphEngine, "_persist_results", _fake_persist)
    monkeypatch.setattr(engine_mod, "_compute_graph_sync", _sentinel_compute)

    snapshot = await GraphEngine().recompute("empty-vault")

    assert not called, "_compute_graph_sync must NOT be called when there are no pages"
    assert snapshot.nodes == [], "Empty vault must yield an empty node list"
    assert snapshot.edges == [], "Empty vault must yield an empty edge list"
