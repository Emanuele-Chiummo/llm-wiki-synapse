# ADR-0014 — GraphCache debounce, dataVersion trigger, and GET /graph contract (I2)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.3
- Decider: solution-architect
- Invariants: **I2** (cached + debounced), I7 (bounded queue — max one queued job), I1, I8 (D4)
- Related: CLAUDE.md §3 I2 / §7 (bounded loops), ADR-0005 (data_version), ADR-0013, ADR-0012
- Resolves: AQ-v0.3-3 (sync 200 vs async 202), AQ-v0.3-7 (recompute trigger/detection)

## Context

I2 requires the layout to be **cached** and recompute to be **debounced on `data_version`**.
Without a cache, every `GET /graph` would re-run FA2 — the exact bottleneck F4 fixes. Two
decisions are pinned: how the cache detects a `data_version` bump and debounces it (AQ-7), and
whether `GET /graph` is synchronous 200 or async 202 (AQ-3). The mechanism MUST be testable
without real `sleep()` (the QA mocks inject a clock — v0.3-stories §3).

## Decision

### 1. Cache state: persisted coords + a `layout_data_version` marker

The "cache" is the **persisted coordinates in `pages.x/y` plus the `edges` table** (ADR-0013),
tagged with the `data_version` they were computed from. The marker is held by `GraphCache`
(in-process) and is also derivable: coords are "fresh" iff
`marker_data_version == vault_state.data_version`. The marker is initialised on startup by
reading the current `data_version` (coords are treated as stale-unknown until first compute).

A cache **hit** = `vault_state.data_version == marker` (coords correspond to current data).
A cache **miss** = marker is unset (first ever call) OR `data_version` advanced past the marker
without a completed recompute.

### 2. Trigger / detection: in-process debounce on data_version bump (AQ-v0.3-7 → option a/c hybrid)

**Decision: in-process debounced async task keyed off the `data_version` bump.** The watcher /
ingest path already runs in the FastAPI process and already bumps `vault_state.data_version`
(ADR-0005). `GraphCache.notify_bump()` is called right after a successful ingest bump (in-process
event — option c), with a **polling fallback** (option a) reconciling the marker against
`vault_state` on a configurable interval for robustness against a missed in-process signal and
for multi-writer safety. **No Postgres LISTEN/NOTIFY** (rejected: adds coupling/complexity,
v0.3-stories §3 recommendation).

Debounce: on a bump, schedule a recompute to fire after `GRAPH_DEBOUNCE_SECONDS` (default
**5s**, configurable). A further bump **within** the window resets the timer (collapses bursts
into one run — AC-F16db-2).

### 3. Bounded queue (I7): at most one in-flight + at most one queued (AQ AC-F16db-3)

- If a recompute is **in-flight** and a new bump arrives, do NOT interrupt it and do NOT spawn
  a parallel run. Set a single `pending` flag.
- When the in-flight run completes, if `pending` is set (and data moved past the just-computed
  marker), schedule **exactly one** follow-up debounce. N bumps during one in-flight run
  collapse to **one** follow-up — total runs for "1 bump + N-during-flight" = 2, never N
  (AC-F16db-3). The queue depth is structurally capped at 1.

### 4. Testable debounce — injected clock + trigger, no real sleep

`GraphCache` takes an injectable **clock** (`now()` callable) and the debounce is driven by an
**advanceable timer** rather than `asyncio.sleep` against wall time. The test:
1. injects a fake clock and a mock `GraphEngine.recompute`,
2. calls `notify_bump()` (1× or N×),
3. advances the fake clock past the window,
4. drives the scheduler tick,
5. asserts `recompute` call count (1 for AC-F16db-1/2; 2 for the in-flight-then-deferred
   AC-F16db-3).

No `time.sleep` / no real `asyncio.sleep` against wall time appears in the tests. Concretely:
the debounce stores a `fire_at = clock.now() + window` deadline; a `tick()` method (called by
the test, and by a lightweight asyncio loop in production) fires the recompute when
`clock.now() >= fire_at` and no run is in-flight. This makes the debounce a pure function of
injected time.

### 5. GET /graph: SYNCHRONOUS 200 (AQ-v0.3-3 → sync)

`GET /graph` returns **HTTP 200 synchronously**, never 202. Two paths:

- **Cache hit** (`data_version == marker`, coords present): read `pages` + `edges`, return the
  payload directly. Set `cached: true` and header `X-Graph-Cache: hit`. **No FA2.**
- **Cache miss** (no coords yet / stale, e.g. first ever call): run **one synchronous**
  `GraphEngine.recompute()` inline, then return the fresh payload with `cached: false` and
  `X-Graph-Cache: miss`. The marker is updated to the current `data_version`.

Rationale: igraph FA2 on the target vault size (<500 nodes) completes in <2s (AQ-3 analysis);
synchronous keeps the client and tests trivial (assert full payload, no polling). The
debounced background recompute (§2) means the *common* case is always a hit; the synchronous
miss path is the cold-start / just-after-bump safety net. This satisfies G2: the **second**
open (same data_version) is a pure-read hit with no FA2, hence no main-thread work and no
server recompute.

A debounced background recompute that completes between two GETs simply makes the next GET a
hit. The synchronous miss path and the background debounce never run FA2 concurrently for the
same vault (§3 in-flight guard is shared).

### 6. GET /graph response contract (locks AC-F4-3 / AC-F4-4 / AC-D4v3-1)

```json
{
  "nodes": [
    {"id": "uuid-string", "title": "string", "type": "string|null",
     "x": 0.0, "y": 0.0, "size": 1.0, "degree": 3}
  ],
  "edges": [
    {"source": "uuid-string", "target": "uuid-string", "weight": 11.0}
  ],
  "data_version": 7,
  "cached": true
}
```

- `nodes`: one object per **live** page that has non-NULL x/y. Fields:
  - `id` (string UUID), `title` (string, may be null→empty per K6 tolerance),
    `type` (string|null), `x`/`y` (float, from `pages.x/y`).
  - `size` (float, OPTIONAL, derived = a monotonic function of `degree`; default 1.0) — a
    rendering hint so high-degree hubs draw larger. Computed server-side from the edge list;
    NOT persisted.
  - `degree` (int, OPTIONAL) — number of incident edges in the returned edge set. Convenience
    for the client; derived, not persisted.
- `edges`: one object per `edges` row for live endpoints. `source`/`target` are page-id
  strings; `weight` is the ADR-0012 float. Undirected — emitted once per pair.
- `data_version` (int): the `vault_state.data_version` the returned coords correspond to.
- `cached` (bool): `true` on a hit (no FA2 this request), `false` on a miss (FA2 ran inline).
- **Header** `X-Graph-Cache: hit|miss` mirrors `cached` for the G2 Playwright assertion.

`size` and `degree` are OPTIONAL in the schema so a minimal client may ignore them; QA AC-F4-3
asserts the required core fields (id/title/type/x/y, source/target/weight, data_version, cached).

## Consequences

- (+) I2 fully satisfied: layout cached, recompute debounced on data_version, second open is a
  pure read hit with `X-Graph-Cache: hit` and zero FA2 (G2 provable).
- (+) I7: queue structurally bounded to one in-flight + one pending; collapse verified by
  AC-F16db-2/3.
- (+) Injected clock/tick makes the debounce deterministically testable with no sleeps.
- (+) Synchronous 200 keeps the client and the AC-F4-3/4 tests simple; no 202/polling.
- (+) `X-Graph-Cache` header + `cached` field give QA two independent cache-hit signals.
- (−) A cold-start `GET /graph` (no coords yet) pays the FA2 cost inline (<2s for target sizes).
  Accepted; mitigated by the background debounce warming the cache after ingests, and bounded
  by the same in-flight guard so a miss-compute and a background compute never race.
- (−) In-process debounce state is lost on restart; reconstructed by reading `data_version` at
  startup and lazily on the next `GET /graph` miss. Acceptable — coords persist in Postgres, so
  a restart never loses computed layout, only the in-memory timer.
