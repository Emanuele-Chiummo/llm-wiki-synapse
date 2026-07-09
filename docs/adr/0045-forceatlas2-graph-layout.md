# ADR-0045 — ForceAtlas2 graph layout via fa2_modified (F4, I2)

- Status: Accepted
- Date: 2026-07-01
- Sprint: v0.6
- Decider: solution-architect
- Invariants: **I2** (layout server-side + cached, NEVER on UI main thread), I7 (bounded pass),
  I9 (igraph R9 retained for Adamic-Adar and circle-init)
- Related: ADR-0013 (superseded §1/§2 for layout algorithm; §3/§4/§5 still in force),
  ADR-0012 (edge-weight formula unchanged), ADR-0014 (cache/debounce unchanged),
  ADR-0016 (edge inclusion rule unchanged), CLAUDE.md §4 F4

## Context

The original layout engine (ADR-0013 §1) used igraph's `layout_fruchterman_reingold`
combined with a polar disc-compression post-pass (`_compress_to_disc`, Feature B) that
capped all node radii at R_TARGET=10 via a concave exponent mapping. In practice this
produced a near-uniform disc — all nodes at roughly the same distance from the centre
regardless of community structure — which is the opposite of the organic clustered look
that nashsu/llm_wiki achieves with ForceAtlas2.

Two compounding problems:

1. **Wrong algorithm**: Fruchterman-Reingold does not mirror the llm_wiki visual. FA2
   uses gravity + attraction to produce natural clustering where connected groups sit
   together and isolated nodes drift outward — exactly the llm_wiki look.
2. **Disc compression fights FA2**: the polar post-pass was designed to contain FR's
   diffuse outliers. Applied to FA2 output it erases the clustering by collapsing all
   radii to a uniform disc. Even if FR were replaced by FA2, keeping this pass would
   negate the visual improvement.

The `fa2_modified` package (v0.4+) is already installed in the backend container and
exposes a `forceatlas2_igraph_layout()` method that accepts an igraph Graph directly,
making the integration lightweight.

## Decision

### 1. Replace FR with ForceAtlas2 via fa2_modified

`GraphEngine.recompute()` now calls `_forceatlas2_layout(g_weighted, edge_weights, n)` —
a private helper that wraps `fa2_modified.ForceAtlas2` — instead of
`igraph.Graph.layout_fruchterman_reingold`.

Settings mirror nashsu/llm_wiki:

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| `gravity` | `1.0` | Standard FA2 gravity; pulls disconnected nodes inward |
| `strongGravityMode` | `True` | Prevents isolated nodes from flying to infinity |
| `scalingRatio` | `2.0` (n≤400), `3.0` (n>400) | More spread for larger graphs to avoid crowding |
| `barnesHutOptimize` | `n > 50` | Barnes-Hut approximation enabled above 50 nodes for speed |
| `verbose` | `False` | No stdout noise in production |

Iteration taper by node count (mirrors llm_wiki `layoutIterations`; bounded per I7):

| Range | Iterations constant | Value |
|-------|---------------------|-------|
| n ≤ 100 | `FA2_ITERS_SMALL` | 140 |
| 100 < n ≤ 400 | `FA2_ITERS_MEDIUM` | 100 |
| 400 < n ≤ 1000 | `FA2_ITERS_LARGE` | 60 |
| 1000 < n ≤ 2500 | `FA2_ITERS_XLARGE` | 40 |
| n > 2500 | `FA2_ITERS_HUGE` | 28 |

### 2. Determinism: circle-init + numpy seed (supersedes ADR-0013 §1/§2 for layout)

ADR-0013 §2 achieved determinism by seeding igraph's internal RNG via
`igraph.set_random_number_generator(_SeedableRNG(FA2_SEED))`. FA2 from `fa2_modified`
initializes node positions randomly when `pos=None`, which would bypass that seeding.

The new determinism strategy (two layers):

1. **Deterministic initial positions**: `igraph.Graph.layout_circle()` places nodes on
   a unit circle — a pure mathematical function with no RNG. The result is passed as
   `pos=` to `forceatlas2_igraph_layout()`, so FA2 never randomizes the starting state.
2. **numpy.random.seed(FA2_SEED)** called immediately before each FA2 invocation as
   belt-and-suspenders: `fa2_modified` may draw from numpy's global RNG internally; the
   seed call ensures any such draws are reproducible.

`FA2_SEED = 42` (same constant, overridable via `GRAPH_LAYOUT_SEED` env var — ADR-0013 §2
env contract preserved).

The `_SeedableRNG` / `igraph.set_random_number_generator` seeding is **kept** — it is
still required by Louvain's `community_multilevel` call later in the same recompute.

### 3. Remove disc-compression post-pass (Feature B)

`_compress_to_disc()` is **no longer called** by `GraphEngine.recompute()`. The raw FA2
output is used directly: `coords = list(raw_coords)`.

The function itself is **retained in engine.py** because existing unit tests in
`TestFeatureBDiscEnvelope` import and test it in isolation as a standalone utility; those
tests remain valid. The function's docstring is updated to mark it as no longer called by
the engine.

### 4. New dependency

`fa2_modified>=0.4` added to `backend/pyproject.toml` `[project] dependencies`. It was
already pip-installed in the production container; the pyproject entry makes it explicit
for CI and reproducible builds. `fa2_modified` transitively requires `scipy` and `tqdm`.

### 5. Runaway-outlier clamp (post-pinning) — added later

Removing the disc-compression post-pass (§3) left FA2's occasional **runaway outliers**
untamed: on large graphs (few iterations) or with loosely-connected nodes, a handful of
nodes land at extreme coordinates (observed: |x|,|y| up to ~2 million while ~95% of nodes
sit within radius ~90 of the center). Sigma's fit-to-view then zooms out to include the
outliers, collapsing the dense core to a dot — reported by the user as *"the graph is all
collapsed in the center"*.

Fix: `_clamp_outliers(coords)` runs as the **last** step of the coordinate pipeline, AFTER
pinned-node restore (§Feature A). Algorithm (deterministic, O(n log n)):

1. Center on the **median** (x, y) — robust to the outliers we are taming (the centroid
   would be dragged toward them).
2. `radius_i = dist(node_i, median_center)`.
3. `r_ref` = the **p90** radius (the edge of the dense core — robust to a minority of
   runaways; a higher percentile like p98 would itself land on an outlier once >2% of
   nodes run away, making the cap useless). `cap = 3.0 * r_ref`.
4. Nodes with `radius > cap` are rescaled radially onto the cap (angle preserved); all
   other nodes are returned **unchanged** — so the organic core spread (the whole reason
   §3 removed disc-compression) and legitimate in-view pins are preserved exactly.

Running it **after** pinning (not before) makes it also tame runaway *pinned* coords — e.g.
a mobile tap with slight touch-jitter that registers as a drag and pins a node at its
current (runaway) position (the observed root cause). Because the clamped coords are what
get persisted, runaway stored coords self-heal on the next recompute. A frontend
`DRAG_THRESHOLD_PX` (5px) is the complementary prevention: sub-threshold pointer movement
no longer counts as a drag, so a tap can no longer pin.

Force path: `POST /graph/recompute` (`GraphCache.force_recompute`) lets the user re-run the
layout on demand (the "Regenerate graph" button) — it reconnects dangling cross-ingest
wikilinks then invalidates the cache marker so FA2 re-runs even when `data_version` is
unchanged, applying this clamp. Still one inline FA2 run under the in-flight guard (I2).

## Consequences

- (+) Graph layout now matches the organic clustered look of nashsu/llm_wiki (R1/R2).
- (+) Disc-compression removal unblocks natural community clustering from Louvain coloring
  (G-P0-2): related pages sit visually close; isolated nodes sit at the periphery.
- (+) I2 still fully holds: layout runs only in `engine.py`, server-side, output stored
  in `pages.x/y` (ADR-0013 §3 unchanged).
- (+) Determinism invariant (ADR-0013 §2 intent) is preserved by a stronger mechanism:
  circle-init eliminates the random-init problem entirely; numpy seed is belt-and-suspenders.
- (+) Iteration taper keeps single-pass wall-clock bounded for large vaults (I7).
- (−) New runtime dependencies: `fa2_modified`, `scipy`, `tqdm`. All are standard
  scientific-Python packages; `scipy` was likely already present as a transitive dep.
- (−) ADR-0013 §1/§2 notes for the layout algorithm are superseded. ADR-0013 §3/§4/§5
  (coordinate storage, incremental semantics, single-bounded-pass contract) remain in force.
- (−) Existing tests that asserted FR-specific behavior (class name `TestFRDeterminism`,
  disc-envelope radius bound `r <= 10`) are updated: `TestFRDeterminism` renamed
  `TestFA2Determinism`; `TestFeatureBDiscEnvelope` now documents it tests the standalone
  function only; new `TestFA2LayoutHelper` class added for `_forceatlas2_layout` unit tests
  including a determinism assertion.

---

## Amendment — 2026-07-09 (llm_wiki 0.6.0 parity, sprint v1.3.13)

- Status: Accepted (amends ADR-0045 §1 parameter table, §1 iteration taper, and §5)
- Decider: backend-engineer (solution-architect notified; I2/I7 compliance verified below)
- Related: ADR-0016 amendment (same sprint — edge set narrows to wikilink-only)

### B.1 Revised FA2 parameters (supersedes §1 parameter table)

The reference implementation (nashsu/llm_wiki `graph-layout-worker.ts`, graphology-layout-
forceatlas2 v0.5.x, `inferSettings(graph)`) uses:

```
outboundAttractionDistribution = false   ← THE biggest visual shape lever
edgeWeightInfluence             = 1.0
gravity                         = 1.0
scalingRatio                    = n>400 ? 3 : 2
strongGravityMode               = true
barnesHutOptimize               = n>50
barnesHutTheta                  = 0.5
adjustSizes                     = false
slowDown                        = 1 + ln(n)   (graphology's inferSettings formula)
```

fa2_modified (the Python package used server-side) does not expose `slowDown` or
`linLogMode` parameters. The closest equivalent to graphology's `slowDown` is
`jitterTolerance`, which controls the convergence speed/stability threshold in
`adjustSpeedAndApplyForces`. The mapping used:

```python
jitter_tolerance = 1.0 + math.log(max(n, 2))   # mirrors 1 + ln(n) for n>=2
```

fa2_modified asserts `linLogMode == False` internally; no explicit parameter needed.

**Complete FA2 constructor call in engine.py** (`_forceatlas2_layout`):

```python
jitter_tolerance = 1.0 + math.log(max(n, 2))
fa = ForceAtlas2(
    outboundAttractionDistribution=False,   # WAS True — the key shape change
    edgeWeightInfluence=1.0,
    gravity=1.0,
    scalingRatio=3.0 if n > 400 else 2.0,
    strongGravityMode=True,
    barnesHutOptimize=(n > 50),
    barnesHutTheta=0.5,                     # NEW (was fa2_modified default 1.2)
    adjustSizes=False,                      # explicit; was fa2_modified default
    jitterTolerance=jitter_tolerance,       # mirrors slowDown=1+ln(n)
    verbose=False,
)
```

**Key parameter rationale:**

| Parameter | Old value | New value | Effect |
|-----------|-----------|-----------|--------|
| `outboundAttractionDistribution` | `True` | `False` | Hubs attracted inward (False) vs. pushed outward (True). False gives the natural llm_wiki cluster shape where high-degree nodes anchor communities. |
| `barnesHutTheta` | (default 1.2) | `0.5` | Tighter approximation accuracy. 1.2 is fast but introduces visible error on medium-sized graphs; 0.5 matches llm_wiki's precision. |
| `adjustSizes` | (default False) | `False` | Explicit; ensures no node-repulsion-by-size adjustment that could conflict with server-side size values. |
| `jitterTolerance` | (default 1.0) | `1+ln(n)` | Mirrors graphology's `slowDown`. Larger tolerance = more aggressive convergence damping on large graphs. |

### B.2 Revised iteration taper (supersedes §1 iteration table)

llm_wiki `graph-layout-worker.ts` uses `layoutIterations` thresholds at 250/600/1200/2500
nodes. The previous taper (ADR-0045 §1) used 100/400/1000/2500. Updated to match:

| Range | Constant | Iterations |
|-------|----------|------------|
| n ≤ 250 | `FA2_ITERS_SMALL` | 140 |
| 250 < n ≤ 600 | `FA2_ITERS_MEDIUM` | 90 |
| 600 < n ≤ 1200 | `FA2_ITERS_LARGE` | 65 |
| 1200 < n ≤ 2500 | `FA2_ITERS_XLARGE` | 40 |
| n > 2500 | `FA2_ITERS_HUGE` | 28 |

All values remain bounded (I7). The change increases iterations for small-to-medium
vaults (250→90 vs. 100→100 at medium range), improving convergence quality without
changing the asymptotic worst case.

### B.3 Outlier clamp REMOVED from engine path (supersedes §5)

ADR-0045 §5 added `_clamp_outliers(coords)` as the last step of the coordinate pipeline,
after pinned-node restore. This clamp was a corrective measure for runaway FA2 nodes
caused by `outboundAttractionDistribution=True` pushing low-connectivity nodes to extreme
positions.

With `outboundAttractionDistribution=False` (B.1 above) the runaway-outlier failure mode
disappears: attraction no longer distributes along hub spokes, so isolated nodes are held
in place by `strongGravityMode=True` rather than being launched outward. Matching llm_wiki
also requires no equivalent post-pass (it has none).

**Change:** `_clamp_outliers` is NOT called by `_compute_graph_sync`. The post-FA2
coordinate pipeline is:

```python
coords = _forceatlas2_layout(g_weighted, edge_weights, n)   # FA2 output
# pinned-node restore (unchanged)
for pid, (px, py) in pinned_lookup.items():
    coords[id_to_idx[pid]] = [px, py]
# NO _clamp_outliers call
```

The `_clamp_outliers` function is **retained in engine.py** for backward-compatible
standalone testing (`TestFeatureBDiscEnvelope` tests reference it directly). Its docstring
is updated to note it is no longer called by the engine.

### B.4 Node size formula (cross-reference ADR-0016 Amendment §A.3)

The node size formula changed concurrently with the FA2 params as part of the same llm_wiki
parity pass. See ADR-0016 Amendment §A.3 for the full specification:

```
size = 8.0 + sqrt(degree / max_degree) * 20.0
```

This is a server-side value; FA2 layout is unaffected by it (size is applied after
coordinate computation). No FA2 parameter changes for node size.

### B.5 Invariant compliance

| Invariant | Status |
|-----------|--------|
| **I2** | FA2 still runs in `asyncio.to_thread` inside `_compute_graph_sync`, server-side; coords written to Postgres. No client layout introduced. |
| **I7** | Iteration counts B.2 are bounded. Removing `_clamp_outliers` reduces O(n log n) post-pass; net effect: slightly faster recompute. |

### B.6 Test suite impact

Tests updated in `backend/tests/test_graph_engine.py`:
- `TestClampRemovedFromEnginePath.test_extreme_pinned_coords_not_clamped`: pins a node at
  (1_000_000, 1_000_000) and asserts coords survive unmodified post-FA2 (|x − 1_000_000| < 1.0).
- `TestFA2Determinism` and `TestFA2LayoutHelper`: no changes needed (determinism mechanism unchanged).
- `TestFeatureBDiscEnvelope`: unchanged (tests standalone `_clamp_outliers`; engine no longer calls it).
- Suite result: **2164 passed, 4 skipped** after all parity changes.
