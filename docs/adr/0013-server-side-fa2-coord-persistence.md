# ADR-0013 — Server-side FA2 layout, coordinate persistence, and determinism seed (I2)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.3
- Decider: solution-architect
- Invariants: **I2** (layout server-side + cached, NEVER on UI main thread), I1 (incremental
  row updates), I9 (igraph R9), I7 (single bounded pass)
- Related: CLAUDE.md §3 I2, §4 F4, v0.3-scope §4/§6, ADR-0005 (data_version), ADR-0012, ADR-0014
- Resolves: AQ-v0.3-2 (FA2 determinism seed), AQ-v0.3-4 (incremental semantics),
  AQ-v0.3-6 (coordinate storage location)

## Context

I2 is the headline invariant of M3: ForceAtlas2 must run **only** server-side, coordinates
stored in Postgres, and the client renders precomputed coords in WebGL — never a force layout
on the browser main thread. Three pinned decisions are required before engineering:
seed determinism (AQ-2), what "incremental" means when FA2 is a global algorithm (AQ-4), and
where x/y live (AQ-6).

## Decision

### 1. FA2 runs ONLY in `backend/app/graph/engine.py` via python-igraph (I2, I9)

The layout is computed by `igraph.Graph.layout_fruchterman_reingold` /
`layout_drl` — **architect note:** the canonical "ForceAtlas2-family" force-directed layout in
python-igraph is exposed as `layout_drl` (Distributed Recursive Layout) and the
Fruchterman-Reingold family; both are force-directed and satisfy I2's "FA2 runs server-side"
intent. The engineer selects the igraph force-directed layout that accepts a `seed`/initial
coordinate parameter and weighting. **The library choice is locked to python-igraph (R9, I9).**
No layout code may exist in any frontend file (enforced by AC-FE-2 / AC-F4-2b static bundle
grep, a P0 block — see ADR-0014).

The weighted edge list from ADR-0012 is passed to the layout as **edge weights**, so strongly
related pages are pulled closer.

### 2. Determinism: fixed seed (AQ-v0.3-2 → fixed)

The engine seeds the force layout with a **fixed integer `FA2_SEED = 42`** (module constant,
overridable via the `GRAPH_LAYOUT_SEED` env var for experimentation, default 42). Given
identical graph topology + weights, two recompute runs produce **identical** coordinates.

Rationale: reproducible coords enable byte-stable-ish regression tests, make screenshot
diffs (D5) stable, and make a cache hit provably equal to the prior miss. Layout variety is
not a v0.3 goal. igraph seeding is done by setting the RNG via `igraph.set_random_number_generator`
/ passing `seed=` to the layout call as the library supports; the engineer wires whichever the
chosen layout exposes and asserts determinism in `test_graph_engine.py`.

### 3. Coordinate storage: columns on `pages` (AQ-v0.3-6 → columns)

x and y are stored as **two nullable float columns on the existing `pages` table**:
`pages.x DOUBLE PRECISION NULL`, `pages.y DOUBLE PRECISION NULL`. Not a separate
`graph_coords` table.

Rationale:
- BACKLOG.md §F4 and AC-D2v3-1 explicitly specify `pages.x, pages.y` columns — confirmed, no change.
- The graph node IS a page; 1:1 cardinality makes a join table pure overhead.
- Coordinates are written in the **same UPDATE** that already touches the page row, keeping
  the recompute a set of column-level upserts (I1-friendly, no full-table rewrite).
- NULL x/y is a valid "not yet laid out" state (a brand-new page between ingest and the next
  debounced recompute); `GET /graph` filters or includes per ADR-0014.

A monotonic `vault_state.data_version` (ADR-0005) already exists as the layout-version signal,
so no per-row `layout_version` column is needed; the cache (ADR-0014) tracks which
`data_version` the persisted coords correspond to.

### 4. Incremental semantics (AQ-v0.3-4 → clarified, NOT frozen coords)

FA2 is a **global** layout: every recompute repositions **all** nodes. "Incremental" (I1 / G1)
at the graph level means precisely two things, and NOT coordinate-freezing:

1. **Ingesting one file mutates only that file's DB rows.** One new `pages` row is inserted
   (or one upserted); the page's `links` are re-persisted. No other `pages` row is deleted or
   recreated; ids/title/type/sources of unrelated pages are untouched (AC-F4-9).
2. **Recompute is triggered ONCE per debounce window** on a `data_version` bump (ADR-0014),
   not once per file change. It is off the ingest hot path.

The coordinate **values** of existing pages MAY change after a recompute — this is correct and
expected because FA2 is global. AC-F4-9 therefore asserts **row-level** incrementality (exactly
+1 row, unrelated rows unchanged), **not** coordinate stability. This is documented in
`test_incremental_graph_update.py` to prevent a false-failure on coord drift.

### 5. Recompute is a single bounded pass (I7)

`recompute()` is one pass: build weights (ADR-0012) → build igraph graph → run seeded FA2 →
write coords + edges in one transaction. It is not a loop. Bounding of *how often* it runs is
the cache's job (ADR-0014, max one queued follow-up). The engine logs node count, edge count,
and wall-clock duration per run (I7 observability).

## Algorithm (engine.recompute, summary)

1. `SELECT id, page_type, sources FROM pages WHERE deleted_at IS NULL` → node set + attributes.
2. `SELECT source_page_id, target_page_id FROM links WHERE dangling = false AND target_page_id IS NOT NULL` → resolved directed edges.
3. Build undirected unweighted igraph for AA; compute `similarity_inverse_log_weighted` (AA matrix, sparse-restricted to neighbour-sharing pairs).
4. Compute the four-signal weight per candidate pair (ADR-0012); keep weight > 0.
5. Build the weighted igraph for layout; run seeded FA2 → coords per node.
6. In ONE transaction: `UPDATE pages SET x=?, y=? WHERE id=?` for each node (batch);
   replace `edges` rows (delete-then-insert for this vault, or upsert) with the weight list.
7. Stamp the cache (ADR-0014) with the `data_version` these coords correspond to. Log metrics.

**Complexity:** nodes N, resolved links L, candidate pairs P (pairs sharing a source or a
neighbour — far fewer than N²). Weight build O(L + P). FA2 O(iters · (N + E)). Persistence
O(N + E). For target vaults (<500 nodes) this is sub-2s, supporting synchronous `GET /graph`
(ADR-0014).

It reads only Postgres tables — **never walks `vault/`** (I1). The vault filesystem is not
touched by the graph engine at all (also satisfies I5: no `.md`/frontmatter writes).

## Consequences

- (+) I2 satisfied at the source: the only force-layout code in the repo is in `engine.py`.
- (+) Fixed seed → deterministic coords → stable tests and stable D5 screenshots.
- (+) Columns on `pages` keep coord writes inside the existing row upsert (I1, no join table).
- (+) Clear, documented incremental semantics prevent a false AC-F4-9 failure on coord drift.
- (−) Global relayout means coords are not stable across ingests (nodes "jump" between
  versions). Accepted for v0.3; an incremental/anchored layout is a possible v0.5+ refinement,
  out of scope and explicitly NOT required by I1.
- (−) `pages` gains two nullable columns that are NULL until first layout. Handled by ADR-0014
  (first `GET /graph` triggers a synchronous miss compute) and an `is null` filter on read.
