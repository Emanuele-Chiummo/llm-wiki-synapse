# ADR-0012 — 4-signal graph edge-weight formula (F4)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.3
- Decider: solution-architect
- Invariants: I1 (computed from Postgres tables, no vault rescan), I9 (igraph R9)
- Related: CLAUDE.md §4 F4, v0.3-scope §6 (EC-M3-1), v0.3-stories §1, ADR-0008 §5 (links)
- Resolves: AQ-v0.3-1 (combining formula), AQ-v0.3-5 (edges table persistence)

## Context

F4 specifies four relevance signals with multipliers — direct-link ×3, source-overlap ×4,
Adamic-Adar ×1.5, type-affinity ×1 — but does not define how the four combine into a single
edge weight, nor the base unit each multiplier scales. The functional-analyst (AQ-v0.3-1, P0)
blocks AC-F4-1 fixture values until this is fixed exactly, because QA must compute expected
weights by hand. AQ-v0.3-5 (P0) couples here: the resulting weighted edge list must be
**persisted** so `GET /graph` is a pure read on a cache hit (I2 intent).

## Decision

### 1. Combining model: ADDITIVE over an undirected page-pair

For an unordered pair of live pages `(A, B)`, the edge weight is the **sum** of the four
independent signal contributions. No signal is multiplicative across signals; no cap is
applied. Additive is auditable (each term inspectable in `signals` JSONB), monotonic
(adding evidence never lowers weight), and lets QA isolate one signal at a time.

```
weight(A, B) =  3.0 · direct_link_count(A, B)
             +  4.0 · shared_source_count(A, B)
             +  1.5 · adamic_adar(A, B)
             +  1.0 · same_type(A, B)
```

### 2. Each term defined exactly (so QA can compute by hand)

The graph is the set of **live** pages (`deleted_at IS NULL`). Edges are computed over the
**undirected** projection; the direct-link term carries the only directional information by
counting both orientations.

| Term | Symbol | Base unit | Exact definition |
|------|--------|-----------|------------------|
| Direct link | `direct_link_count(A,B)` | count (0,1,2) | Number of **resolved, non-dangling** rows in `links` whose `{source_page_id, target_page_id}` equals `{A,B}` in either direction. `A→B` contributes 1, `B→A` contributes 1; a bidirectional pair = 2. `dangling=True` rows are excluded (no resolved endpoint). Multiplier ×3. |
| Source overlap | `shared_source_count(A,B)` | count (≥0) | `len(set(A.sources) ∩ set(B.sources))` over the `pages.sources` JSONB arrays. NULL sources → empty set. Multiplier ×4. |
| Adamic-Adar | `adamic_adar(A,B)` | float (≥0) | The igraph-computed Adamic-Adar index on the **undirected, unweighted** adjacency built from the resolved-link edge set (same edge set as the direct-link term, collapsed to undirected, deduplicated). `AA(A,B) = Σ_{c ∈ N(A)∩N(B)} 1/ln(deg(c))`. Computed via `igraph.Graph.similarity_inverse_log_weighted()`. Multiplier ×1.5. |
| Type affinity | `same_type(A,B)` | indicator (0/1) | `1` if `A.page_type == B.page_type` AND both are non-NULL; else `0`. Two NULL types do NOT match (NULL is "unknown", not a type). Multiplier ×1. |

### 3. Edge inclusion rule

An edge `(A,B)` is **persisted to the `edges` table iff `weight(A,B) > 0`**. Pairs whose four
signals all evaluate to zero are not stored (keeps the table sparse; matches AC-F4-1(d):
P3–P5 must NOT appear). The Adamic-Adar term alone can make a non-linked, non-shared-source,
different-type pair have weight > 0 if the two pages share a resolved-link neighbour — this is
intended (transitive relevance) and such an edge IS persisted.

### 4. Determinism of the weight (independent of FA2 seed)

Weights are a pure deterministic function of the `pages` and `links` rows. They do not depend
on the FA2 seed (ADR-0013). The same DB state always yields the same `edges` rows and weights,
so QA fixtures are byte-stable.

### 5. Worked fixture (matches v0.3-stories §1 expected values)

Using the 5-node fixture (P1 Alpha/entity/[doc_a], P2 Beta/entity/[doc_a],
P3 Gamma/concept/[doc_b], P4 Delta/entity/[doc_a,doc_b], P5 Epsilon/concept/[doc_c]) with
resolved links P1→P2, P2→P1, P3→P4, P4→P1:

| Edge | direct ×3 | source ×4 | AA ×1.5 | type ×1 | base (excl. AA) |
|------|-----------|-----------|---------|---------|-----------------|
| P1–P2 | 2·3 = 6 | 1·4 = 4 | ≥0 | 1 | **11** + AA |
| P1–P4 | 1·3 = 3 (P4→P1) | 1·4 = 4 (doc_a) | ≥0 | 1 | **8** + AA |
| P2–P4 | 0 | 1·4 = 4 (doc_a) | ≥0 | 1 | **5** + AA |
| P3–P5 | 0 | 0 | 0 | 0 (concept vs concept? — see note) | **0** → not stored |

Note on P3–P5: both are `concept`, so type-affinity would be 1, giving weight 1 (stored).
v0.3-stories §1 states P3–P5 expected weight 0 / not present. **Architect resolution:** the
stories fixture is authoritative for the *intent* (an unrelated pair), but per this formula
two same-type pages DO get the +1 type term. To keep the fixture self-consistent, QA must
either (a) set P5 to a different type than P3 in the test fixture, OR (b) assert P3–P5 weight
== 1.0 (type-only). The formula is the source of truth; the QA fixture is adjusted, not the
formula. This is flagged to functional-analyst as a one-line fixture correction (see
v0.3-architecture §AQ-1 note). Lower-bound assertions for P1–P2 (≥11), P1–P4 (≥8), P2–P4 (≥5)
are unaffected.

## Consequences

- (+) Each signal independently testable (AC-F4-1(e)) — zero out three terms, assert the fourth.
- (+) Weights are deterministic and FA2-seed-independent → stable regression fixtures.
- (+) Sparse `edges` table (only weight>0) keeps `GET /graph` payload and FA2 input small.
- (+) Adamic-Adar reuses igraph natively (I9) — no hand-rolled graph math.
- (−) Same-type adds +1 to *every* same-type pair, which can create a dense block of weak
  edges in a vault dominated by one type. Accepted for v0.3: the +1 is the weakest signal and
  FA2 weighting (ADR-0013) keeps these from dominating layout. Revisit if graph density hurts
  FA2 runtime at >2k nodes (out of v0.3 scope).
- (−) AA is O(pairs) in the worst case; bounded in practice because it is only non-zero for
  pairs sharing a neighbour. Complexity analysed in v0.3-architecture §GraphEngine.
