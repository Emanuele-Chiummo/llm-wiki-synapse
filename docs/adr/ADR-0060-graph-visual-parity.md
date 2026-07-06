# ADR-0060 — Graph visual parity: render-only edge culling, hub labels, node density down-scale (F4, I2)

- Status: Accepted
- Date: 2026-07-06
- Sprint: v1.3+ (B3-LOOK batch)
- Decider: solution-architect
- Invariants: **I2** (layout + coords stay server-side and cached; NEVER run a layout on the
  client), **I3** (thresholds/topK computed once, not per-frame), I4 (WebGL sigma renderer
  unchanged), I8 (this ADR + index row are the D7 update)
- Related: ADR-0045 (server-side FA2 layout + determinism — UNTOUCHED; GL4 would amend it),
  ADR-0016 (structural edge-inclusion = direct ∪ shared-source — UNTOUCHED; GL5 would revisit),
  ADR-0012 (4-signal edge-weight formula — UNTOUCHED), ADR-0015 (no client-side layout),
  ADR-0014 (GraphCache + GET /graph contract), CLAUDE.md §3 I2/I3, §4 F4,
  docs/reference/UI-ALIGNMENT-PLAN-2026-07.md §B3-LOOK (GL1–GL6)

## Context

Synapse's knowledge graph rendered as a uniform "ball" of dots — the opposite of the legible
clustered look nashsu/llm_wiki achieves. The **root cause is code-verified to be the render
layer, not the data layer**:

- The sigma client drew **all edges** (~2400 on the reference vault) at uniform faint opacity,
  so the eye reads a solid haze instead of structure. There was **no edge culling**.
- **No node density down-scaling**: every node rendered at (near) full display size regardless
  of graph size, so a large vault crowds into an undifferentiated mass.
- **Few at-rest labels**: labels only appeared on hover/zoom, so no anchoring hub names gave
  the layout legibility even when clusters existed.

Critically, the *data* is already correct and already server-side:

- Coordinates come from server-side ForceAtlas2 with cached, deterministic output
  (ADR-0045) — the clusters exist in the coords; the client was simply drowning them.
- The edge set is already tight and structural (direct link ∪ shared-source; similarity
  signals only *modulate* weight, ADR-0016) — there is no hairball to fix in the model.

Therefore the fix is a set of **render-only levers in the sigma client**. This ADR formalizes
the three that ship in this batch and records the disposition of the three that do not.

## Decision

**In this batch (accepted, RENDER-ONLY):**

### GL1 — Cull weak edges, reveal on hover (dominant fix)

The sigma reducer hides edges below a weight threshold from the *at-rest* render, and reveals
the hidden incident edges of a node only while that node (or its neighbourhood) is hovered.

- This is a **visibility filter only**. Culled edges **remain in the graph model** (still
  loaded from `GET /graph`, still in sigma's graphology instance); they are hidden, not
  removed, and come back on hover.
- **No edge weight is changed** and the 4-signal formula (ADR-0012) and structural
  inclusion rule (ADR-0016) are untouched — culling reads the existing `weight`, it does not
  recompute it. What is drawn ≠ what exists; the graph model is unchanged.
- This is the dominant contributor to parity: removing the faint-edge haze is what lets the
  server-computed clusters read as clusters.

### GL2 — Force hub labels + lower the label threshold

Nodes above a structural-degree / size rank get their label **forced on at rest** (not only on
hover), and the general label-render threshold is lowered so more anchor names show. Labels are
a pure display concern; node identity, size, and position are unchanged.

### GL3 — Node density down-scale

Node *display size* is scaled down as a function of graph size so a large vault no longer
crowds into a ball. This changes the **display radius the client draws**, not the server `size`
field semantics (ADR-0016 §2 `BASE + GROWTH·sqrt(structural_degree)` stays the source of the
*relative* ordering) and never the node's `(x, y)` position.

All three (GL1/GL2/GL3) are levers on **edge visibility**, **label forcing**, and **node
display size** — never node positions, and the client never runs a layout.

**Deferred (spike — would amend ADR-0045):**

### GL4 — Layout seed init: circle → seeded-random-in-disc

ADR-0045 §2 initializes FA2 from `igraph.Graph.layout_circle()` — a pure, RNG-free function —
which both (a) gives the spherical outer envelope and (b) *is* the determinism mechanism that
makes the server-side coordinate cache valid (deterministic init → deterministic coords →
cache is safe to persist and reuse, ADR-0014). Switching to a seeded-random-in-disc initial
placement is plausibly better clustering but is **out of scope for this render-only batch**: it
is a **server-side layout change**, not a client render tweak. Recorded as a **deferred spike**
that, if pursued, must **amend ADR-0045** and MUST keep the init deterministic (numpy-seeded via
`FA2_SEED` / `GRAPH_LAYOUT_SEED`) so I2's coordinate cache remains valid. NOT in this batch.

**Declined:**

### GL5 — Redefine the drawn edge-set as wikilink ∪ shared-source "for looks"

DECLINED. This is exactly the current structural edge set (ADR-0016 §1), and ADR-0016 chose it
**deliberately** — it kills the type/AA hairball, drives real-connection node sizing, and feeds
better clusters into FA2, all of which also improve retrieval and curation signal. We do not
degrade a deliberate, retrieval-improving internal for a purely visual reason, and there is
nothing to change here anyway. If a future need arises to draw a *different* subset than the
model stores, it is a **view-toggle overlay** (a separate opt-in ADR), never a change to the
stored edge set or the inclusion rule.

### GL6 — Swap the ForceAtlas2 implementation library

DECLINED. High migration/regression risk (new determinism surface, re-tuning of the ADR-0045
parameter table, coordinate-cache invalidation across the whole vault) for marginal, unproven
visual gain. The parity gap is a render problem (GL1–GL3), not a layout-quality problem.
`fa2_modified` (ADR-0045) stays.

## Invariants owned

- **I2 (headline) — render-only, no client layout.** Server-side FA2 layout and the cached
  `pages.x/y` coordinates (ADR-0045, ADR-0013 §3) are **UNTOUCHED**. GL1/GL2/GL3 change only
  edge *visibility*, label *forcing*, and node *display size* in the sigma reducer. The client
  runs **no** force layout and **mutates no coordinate**. Compliant.
- **I3 — thresholds/topK computed once.** The cull threshold, the hub-label rank/topK set, and
  the density scale factor are derived **once per graph load / `dataVersion` change**, not
  per-frame or per-token. The sigma reducer does an O(1) lookup per element; no heavy work runs
  on the render/main thread each frame. Compliant.
- **I4** — CodeMirror/editor untouched; sigma WebGL renderer and virtualisation untouched.
  Compliant.
- **I8** — This ADR + its index row are the D7 update. No schema change → no `make er`/D2 move;
  `GET /graph` contract (ADR-0014 §6) and the response schema (ADR-0016 §4) are unchanged, so D4
  is unaffected. D5 graph screenshots regenerate to show the parity.

## Do NOT (guard rail for the implementing frontend agent)

- Do **NOT** run a force layout (or any layout) on the client — coords are server-authoritative.
- Do **NOT** mutate node `(x, y)` coordinates — GL3 scales *display size*, never position.
- Do **NOT** remove culled edges from the graph model — **hide** them (visibility filter) and
  reveal on hover; they must still be present for hover-reveal and unchanged for `GET /graph`.
- Do **NOT** change edge weights or the 4-signal formula (ADR-0012) or the structural inclusion
  rule (ADR-0016) — GL1 *reads* `weight`, it does not recompute or re-gate it.
- Do **NOT** compute thresholds/topK per-frame — derive once per load / `dataVersion` bump (I3).

## Consequences

- (+) The server-computed clusters finally read as clusters: culling the faint-edge haze (GL1)
  is the dominant fix; hub labels (GL2) and density down-scale (GL3) add legibility.
- (+) Zero backend/schema change, zero coordinate-cache invalidation, zero risk to determinism —
  the whole batch lives in the sigma reducer. Fully I2/I3-safe by construction.
- (+) Hover-reveal keeps the full edge information available on demand; nothing is lost, only
  de-emphasized at rest.
- (−) GL4's potentially-better clustering is deferred; the current circle-init envelope stays.
  Acceptable — the parity gap is render, not layout.
- (−) "Weak" edges are invisible at rest, so a user scanning without hovering under-counts an
  edge's true connectivity. Accepted: it is the same trade-off Obsidian makes, and hover +
  node size (structural degree, ADR-0016 §2) still convey connectedness.
</content>
</invoke>
