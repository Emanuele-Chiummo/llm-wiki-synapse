# ADR-0016 — Obsidian-style graph: structural edges, real-connection sizing, type-as-modulator (F4)

- Status: Accepted
- Reviewed & approved (M4 Phase 0) — solution-architect, 2026-06-28: structural-edge gate, kind,
  sqrt sizing, server-side deterministic `_compress_to_disc`, single-node drag + PATCH, and
  pinned-coord preservation all verified I1/I2/I7/I8-compliant. Single-node drag is direct
  manipulation (no client layout). APPROVED-WITH-NITS (nits are follow-ups, non-blocking).
- Date: 2026-06-28
- Sprint: v0.3 → v0.4 transition
- Decider: solution-architect
- Invariants: **I2** (layout stays server-side + cached, NEVER on UI main thread), I1 (computed
  from Postgres tables, no vault rescan), I7 (single bounded pass), I9 (igraph R9), I8 (D-artifacts)
- Related: CLAUDE.md §3 I2/I1/I7, §4 F4, ADR-0012 (edge-weight formula), ADR-0013 (FA2/coords),
  ADR-0014 (GraphCache + GET /graph contract), ADR-0015 (no client layout)
- Supersedes: ADR-0012 §3 (edge **inclusion** rule) — see §6. Edge-weight *formula* (ADR-0012 §1/§2)
  is RETAINED and reused unchanged as a *modulation* term.
- Resolves: empirical "4-clique hairball" defect (verified 2026-06-28 on the 200-node synthetic
  fixture: types `i%4` → 4 balanced groups of 50 → 4 complete cliques = 4900 edges, every node
  degree 49, every node identical size).

## Context

The 4-signal weight (ADR-0012) is `3·direct + 4·source-overlap + 1.5·Adamic-Adar + 1·same-type`,
and ADR-0012 §3 persists an edge **iff `weight > 0`**. Because `same_type` alone contributes
`1.0`, the inclusion rule **materializes a standalone edge for every same-type pair**
(engine.py §3(d), lines 212–222, combined with the `if w > 0` gate at line 257).

Empirically verified today:

- **Synthetic fixture (200 nodes, type = `i % 4`):** 4 balanced groups of 50 → **4 complete
  cliques = 4900 edges**, every node degree exactly 49, every node identical size. The viewer is
  a dense hairball of 4 colored blobs — not a knowledge graph.
- **Real data projection:** N same-type notes → `C(N,2)` type-only edges. 500 `concept` notes →
  ~125k edges purely from type-affinity. This neither scales (FA2 input blows up, contradicting
  the <2s synchronous-miss assumption of ADR-0014 §5) nor *looks* like a knowledge graph (type
  becomes a clique generator, drowning the real link structure).
- **Node size** is `1 + ln(1+degree)` over the **weighted** (4-signal) degree (engine.py line
  299), then `×5` on the client (graphTransform.ts). When every node sits in a same-type clique,
  every degree is ~`group_size − 1` and the log compression flattens all sizes to near-identical.
  Size therefore reflects "how many notes share my type", **not** "how many real connections I
  have" — the exact opposite of the user goal *"più collegamenti → pallino più grande"*.

ADR-0012 §Consequences already flagged this ("same-type adds +1 to *every* same-type pair, which
can create a dense block of weak edges … Revisit if graph density hurts FA2 runtime"). That
revisit is now due, and it is also a **visual-quality** defect, not only a runtime one.

**User goal (authoritative):** an Obsidian-style graph where (a) node size ∝ number of *real*
connections to a note, (b) color by type, (c) not a hairball, (d) readable. Layout stays
**server-side** (I2 — non-negotiable).

The root cause is a category error: **type-affinity is a *similarity* signal, not a *structural
relation*.** Obsidian's graph draws an edge only for an actual link; node color groups by folder/
tag; node size grows with link count. We adopt that mental model while keeping our richer
4-signal weight as the thing that *modulates* (a) which structural edges are emphasized and
(b) how the force layout pulls — never as an edge *generator*.

## Decision

### 1. Edges are STRUCTURAL; similarity signals MODULATE, never materialize (answers Q1)

Split the two roles the 4-signal score was conflating:

- **Structural relation** — the only thing that *creates* an edge. An edge `(A,B)` exists iff
  the pair has a **structural** tie:
  - **Direct link** (`direct_link_count(A,B) > 0`) — always structural (a real `[[wikilink]]`).
  - **Shared source** (`shared_source_count(A,B) > 0`) — structural by **provenance**: two pages
    distilled from the same `raw/` document are genuinely related, not merely similar. KEEP as an
    edge generator.
- **Similarity modulation** — adjusts the **weight** of edges that *already exist* structurally,
  and never creates a standalone edge:
  - **Adamic-Adar** — RECLASSIFIED to modulator. AA(A,B) > 0 means A and B share a *resolved-link*
    neighbour; this is transitive *similarity*, not a first-class relation. It strengthens an
    existing direct/shared-source edge but no longer materializes an edge on its own.
  - **Same-type affinity** — MODULATOR ONLY (this is the defect fix). `same_type` adds weight to
    an edge that already exists; it **never** creates an edge by itself. This eliminates the
    cliques.

**Confirmed and lightly revised vs. the brief's recommended target.** The brief proposed
"structural edges = direct links + (optionally) shared-source/AA". Decision: structural =
**direct link OR shared-source**; **AA and same-type are modulators only**. Rationale for keeping
shared-source structural but demoting AA: shared-source is a *direct provenance fact* about a pair
(low fan-out — a source maps to a handful of pages), whereas AA is a *derived transitive* score
whose pair-set is the union of all 2-paths and tends to fill in dense local neighbourhoods (it
re-introduces hairball pressure, milder than type but real). Keep the structural edge set tight
and explainable; let AA and type only *weight* it.

**New edge inclusion rule (replaces ADR-0012 §3):**

```
edge (A,B) EXISTS  iff  direct_link_count(A,B) > 0  OR  shared_source_count(A,B) > 0
weight(A,B)        =  3.0·direct_link_count(A,B)            # ADR-0012 formula, UNCHANGED
                   +  4.0·shared_source_count(A,B)
                   +  1.5·adamic_adar(A,B)                  # now only added to existing edges
                   +  1.0·same_type(A,B)                    # now only added to existing edges
```

The **weight arithmetic of ADR-0012 §1/§2 is retained byte-for-byte.** Only the *gate* changes:
from "persist iff weight > 0" to "persist iff a structural tie exists". For any pair that already
had a structural tie, the stored `weight` is **identical** to before (the AA and type terms still
add in). The only pairs removed are those whose *only* nonzero terms were AA and/or type — i.e.
exactly the clique edges. This is minimal and surgical.

### 2. Node SIZE is driven by STRUCTURAL DEGREE = "number of real connections" (answers Q2)

Three candidates were on the table: full 4-signal weighted degree, direct-link degree, weighted
degree. Decision: **size is a monotonic function of `structural_degree`** = the number of
**distinct incident structural edges** (the new edge set from §1), i.e. the count of distinct
neighbours reachable by a direct link or a shared source.

- NOT weighted degree (sum of weights): a single high-weight edge (e.g. 3 mutual links + shared
  source) would inflate size without more *connections* — contradicts *"più collegamenti"*.
- NOT direct-link-only degree: shared-source ties are real connections the user cares about
  (same source document) and are now structural edges; excluding them would under-size genuine
  hubs and contradict the edge set we draw.
- = distinct structural neighbour count: this is exactly the visible degree in the rendered graph
  ("how many lines come out of this dot"), which is what an Obsidian user reads as connectedness.

**Size formula (revised, less aggressive compression):** keep a sub-linear curve so one mega-hub
doesn't dwarf everything, but make the low end *visibly* differentiate 0/1/2/3 connections:

```
size = BASE + GROWTH · sqrt(structural_degree)      # server-side, in engine.py
       BASE = 1.0, GROWTH = 2.5  (tunable constants; shipped value GROWTH=2.5)
```

`sqrt` instead of `ln(1+·)`: `ln1p` over-compresses at the low end where most real vaults live
(ln1p(1)=0.69, ln1p(3)=1.39 — barely distinguishable after ×5); `sqrt` gives 0→1.0, 1→2.0,
3→2.73, 9→4.0 — a clear, readable progression that still tapers for hubs. `degree` in the
response becomes `structural_degree` (see §4). The client `×5` scale (graphTransform.ts) is
retained as a pure display multiplier; isolated nodes (degree 0) render at `BASE×5` and remain
clickable.

### 3. LAYOUT (FR) weighting uses the full modulated weight; clusters form from real ties (answers Q3)

The force layout (igraph FR, ADR-0013 — stays server-side, I2) is fed the **§1 edge set** with
the **§1 modulated weights**:

- Because type no longer creates edges, FR is no longer dragged into 4 type-cliques. Clusters now
  emerge from **link topology + shared provenance** — i.e. genuine knowledge structure.
- The similarity terms still **modulate** the spring strength: two structurally-linked pages that
  are *also* same-type and share neighbours (high AA) sit closer than a bare single-link pair.
  This is the desired effect — type/AA refine *positioning within* the real structure, instead of
  fabricating the structure.
- Determinism (fixed seed=42, ADR-0013 §2) and the single bounded pass (I7) are unchanged.

Net: same FR call, same I2/I7 posture; only the input edge set/weights change. Disconnected
nodes (no structural tie) are positioned by FR as isolated points (igraph scatters unconnected
vertices); acceptable and Obsidian-like (orphan notes float at the periphery).

### 4. /graph exposes a per-edge `kind` and a structural `degree` so the client can style/filter (answers Q4)

The client needs to (a) size nodes by real connections, (b) optionally style/filter edges by what
created them, without re-deriving anything. Add two **additive, backward-compatible** fields:

**Per-edge `kind`** (string enum) — *what structural relation created the edge*:

| `kind` value | Meaning |
|--------------|---------|
| `"link"`     | `direct_link_count > 0` (a real wikilink exists; may also have source/AA/type weight) |
| `"source"`   | `direct_link_count == 0` AND `shared_source_count > 0` (provenance-only edge) |

(Precedence: a pair with both a link and a shared source is `"link"` — the stronger structural
fact. There is no `"type"` or `"aa"` kind because those never create edges anymore.) The existing
per-edge `signals` JSONB (already persisted in the `edges` table, engine.py line 325) is the
authoritative breakdown (`{direct, source, aa, type}`) and MAY be surfaced later if the client
wants a tooltip; for v0.3/v0.4 the single `kind` discriminator is sufficient and cheaper on the
wire. We do **not** add the full per-edge signal breakdown to the response now (YAGNI; `signals`
stays server-side in the table, available for a future ADR if the UI needs it).

**Per-node `degree`** — REDEFINED to `structural_degree` (count of distinct incident structural
edges), the value that drives `size` (§2). This is a *semantic* change to an existing OPTIONAL
field, not a new field: clients already treat `degree` as "number of connections" (graphTransform.ts
`nodeSize` fallback), so the new meaning is what they already assumed.

**Response-schema delta (exact):**

```jsonc
// GraphEdgeResponse  — ADD one field
{
  "source": "uuid-string",
  "target": "uuid-string",
  "weight": 11.0,
  "kind": "link"            // NEW: "link" | "source"  (enum, required going forward, default "link")
}

// GraphNodeResponse  — UNCHANGED shape; "degree" semantics now = structural_degree, "size" = §2 curve
{
  "id": "uuid-string", "title": "string", "type": "string|null",
  "x": 0.0, "y": 0.0,
  "size": 2.0,             // now BASE + GROWTH·sqrt(structural_degree)
  "degree": 3             // now structural_degree (distinct structural neighbours)
}
```

`kind` is additive (existing clients ignore unknown fields; the field is non-breaking). `weight`,
`x`, `y`, `data_version`, `cached`, and the `X-Graph-Cache` header are all unchanged, so ADR-0014
§6 and the AC-F4-3 core-field assertions still hold.

### 5. Exact, minimal, backward-compatible change list (for backend-engineer — NO code written here)

**`backend/app/graph/engine.py`:**

1. **Candidate-pair generation (lines ~185–222):** keep blocks (a) direct-link and (b)
   shared-source as the **structural candidate set**. KEEP blocks (c) AA-pair enumeration and (d)
   same-type enumeration **only** as the iteration set over which the AA/type *weight terms* are
   computed — but do **not** let (c)/(d) alone admit an edge. Concretely: compute the structural
   set `S = (a) ∪ (b)`; compute weights for pairs in `S` using the full 4-term formula
   (direct, source, AA, type all still summed). Blocks (c)/(d) are no longer needed to *enumerate
   edges*; AA and same_type are evaluated for the pairs already in `S`. Net effect: drop (c) and
   (d) from `candidate_pairs`; AA/type survive as weight terms on structural pairs only.
2. **Inclusion gate (line ~257, `if w > 0`):** replace with the structural gate — persist iff
   `direct > 0 OR shared > 0`. (Within `S` this is always true by construction, so in practice
   the loop simply emits every pair in `S`; keep an explicit `if direct > 0 or shared > 0:` guard
   for clarity and to document the contract.) The `weight` value and the `signals` dict are
   computed exactly as today (no arithmetic change).
3. **Per-edge `kind` (in the `weighted_edges` / `edge_db_rows` assembly, ~lines 256–327):** derive
   `kind = "link" if direct > 0 else "source"`. Add `kind` to `EdgeSnapshot` (new field, default
   `"link"`) and include it in the `edge_db_rows` so it is available to the response. Persisting
   `kind` in the `edges` table is OPTIONAL (it is recomputable from `signals.direct`); recommended
   to add a nullable `kind` text column for a pure-read hit, but acceptable to derive it on read
   from `signals` to avoid a migration — backend-engineer picks the cheaper path and notes it.
4. **`structural_degree` + `size` (lines ~291–310):** compute degree from the **structural edge
   set** (the `weighted_edges` list, which is now structural-only) — i.e. count distinct incident
   edges per node from that list — instead of `g_weighted.degree()` over a graph that used to
   include clique edges. (After change 1/2 the weighted graph IS the structural graph, so
   `g_weighted.degree()` already yields `structural_degree`; just rename the variable and the
   `NodeSnapshot.degree` semantics in the docstring.) Replace the size formula at line 299:
   `size = 1.0 + 2.5 * math.sqrt(deg)` (BASE=1.0, GROWTH=2.5 constants), keeping `max(1.0, …)` floor.
5. **Docstrings/comments referencing ADR-0012 §3 inclusion** (header block lines ~15–20, inline
   ~174–222): update to cite ADR-0016 for the inclusion rule; keep the ADR-0012 citation for the
   weight *arithmetic*.

**`backend/app/main.py` (/graph Pydantic models, lines ~610–660):**

6. **`GraphEdgeResponse`:** add `kind: str = Field(default="link", description="Structural edge
   kind: link | source")`. Update the model example and the `GraphResponse` example to include
   `"kind": "link"`.
7. **`GraphNodeResponse`:** no field change; update the `degree` field description to
   "structural degree (distinct incident structural edges; drives size)" and the `size`
   description to "BASE + GROWTH·sqrt(structural_degree)".
8. **Response assembly (lines ~728–730):** pass `kind=e.kind` into `GraphEdgeResponse`.

**Frontend (out of scope to change here, but flagged for frontend-engineer in v0.4):**
`graphTransform.ts` may keep using `node.size` verbatim (server now sends the right curve);
optionally read `edge.kind` to style provenance edges differently (e.g. dashed for `"source"`).
No client-layout is introduced (ADR-0015 untouched).

**What does NOT change (explicitly):** ADR-0013 (FA2 server-side, seed, coords in `pages.x/y`),
ADR-0014 (debounce, cache marker, synchronous 200, `cached` + `X-Graph-Cache`), ADR-0015 (zero
client layout), the `edges` table shape (except optional `kind` column), the recompute being a
single bounded pass (I7). `make er` / D2 only changes if the optional `kind` column is added.

### 6. Invariant + ADR-0012 reconciliation (answers Q6)

- **I2** — Layout stays 100% server-side via igraph FR; only the *input* edge set/weights change.
  No client layout introduced. **Compliant.**
- **I1** — Engine still reads only `pages` + `links` from Postgres; no vault walk. **Compliant.**
- **I7** — Still one bounded pass; in fact the edge count drops by orders of magnitude (no
  cliques), shrinking FA2 input and *strengthening* the <2s synchronous-miss bound (ADR-0014 §5).
  **Compliant.**
- **I8** — This ADR + the README index row are the D7 update; D4 (OpenAPI) auto-regenerates from
  the new `kind` field; D2 (ER) only moves if the optional `kind` column is added (`make er`).
  **Compliant** (D-artifacts enumerated).
- **I9** — Still igraph for AA + FR; no reinvention. **Compliant.**

**ADR-0012 semantics change — explicit note (required by Q6):** ADR-0012 §3 (edge **inclusion**
rule: "persist iff weight > 0") is **SUPERSEDED** by ADR-0016 §1 (persist iff a structural tie
exists). ADR-0012 §1/§2 (the additive 4-signal **weight formula** and per-term definitions) and
§4 (FA2-seed-independent determinism) are **RETAINED unchanged** — the four terms still combine
additively into the stored `weight`; they now apply only to structurally-tied pairs. ADR-0012's
worked-fixture §5 row "P3–P5 (type-only) → weight 1, stored" is the one numeric case that flips:
under ADR-0016, P3–P5 has no link and no shared source → **no edge** (the type-only +1 no longer
materializes an edge). QA's AC-F4-1 lower-bound assertions for P1–P2 (≥11), P1–P4 (≥8), P2–P4 (≥5)
are **unaffected** (those pairs are structural via link/source and keep identical weights). The
functional-analyst must update the type-only fixture expectation from "weight == 1.0, present" to
"absent" — a one-line correction, flagged here.

## Consequences

- (+) Kills the hairball: the 200-node fixture goes from 4900 clique edges to only its real
  link/source edges; node degree and size now vary with actual connectedness (user goal a/c/d met).
- (+) Node size finally means *"più collegamenti → pallino più grande"* — `sqrt(structural_degree)`
  visibly differentiates 0/1/2/3-connection notes (Q2 goal met).
- (+) Type still drives **color** (frontend `TYPE_COLORS`, unchanged) — grouping survives visually
  without fabricating edges (user goal b met).
- (+) FA2 input shrinks by orders of magnitude on type-heavy vaults → faster recompute, safer
  synchronous-miss bound (reinforces ADR-0014, I7).
- (+) `kind` lets the client style provenance vs. link edges and lets QA assert "no type-only
  edges exist" directly.
- (+) Minimal/surgical: weight arithmetic untouched; one gate flipped, one size curve swapped,
  one additive field. Backward-compatible on the wire (additive `kind`; `degree` semantics align
  with what clients already assumed).
- (−) AA-only and type-only "soft similarity" edges are no longer drawn. Accepted: they were the
  noise. If a future "show similarity halo" feature is wanted, it is a *separate, opt-in* overlay
  computed from `signals` — a new ADR, not a default edge.
- (−) Orphan notes (no link, no shared source) render as isolated dots. This is correct and
  Obsidian-like; it also surfaces under-connected notes, which is useful curation signal (K8).
- (−) One QA fixture expectation (ADR-0012 §5 P3–P5) flips from "present, weight 1" to "absent";
  functional-analyst updates it (flagged §6). All lower-bound weight assertions are unchanged.
- (−) Coordinates will differ from the pre-change layout (fewer edges → different FR result).
  Expected and acceptable per ADR-0013 §4 (coords are not stable across recomputes); D5
  screenshots regenerate.

---

## Amendment — 2026-07-09 (llm_wiki 0.6.0 parity, sprint v1.3.13)

- Status: Accepted (amends ADR-0016 §1 and §2; all other sections remain in force)
- Decider: backend-engineer (solution-architect notified; I2/I1/I7/I8 compliance verified below)
- Related: ADR-0045 amendment (same sprint), ADR-0013 §3/§4/§5 (coord storage — unchanged)

### A.1 EDGE CREATION — WIKILINK-ONLY (reverses §1's shared-source structural status)

ADR-0016 §1 defined two structural edge generators: **direct wikilink** AND **shared
source** (`shared_source_count > 0`). Analysis of nashsu/llm_wiki 0.6.0 (`wiki-graph.ts`
lines 219-231) shows that llm_wiki creates edges **exclusively for resolved `[[wikilinks]]`**
— provenance proximity is NOT treated as a structural relation in the reference
implementation.

**Revised edge rule (supersedes ADR-0016 §1 second bullet):**

```
edge (A,B) EXISTS  iff  direct_link_count(A,B) > 0   (wikilink-only)

weight(A,B)        =  3.0·direct_link_count(A,B)      # unchanged arithmetic
                   +  4.0·shared_source_count(A,B)    # WEIGHT contribution preserved
                   +  1.5·adamic_adar(A,B)
                   +  1.0·type_affinity(A,B)
```

Key clarification: **shared-source count still contributes +4.0 per shared source to the
weight of a wikilink edge.** Two pages that both link to each other AND share a source
document receive a higher spring strength in FA2, pulling them closer. Shared-source
influence is preserved as a *weight modulator* — it is no longer an *edge creator*.

Rationale:
- **parity with llm_wiki 0.6.0** (R1 / the reference implementation): the goal is for the
  Synapse graph to produce the same edge topology as llm_wiki given the same vault.
- **source cliques problem**: `shared_source_count > 0` generates a clique over all pages
  derived from the same source document. A source with 30 extracted pages yields C(30,2) =
  435 source-only edges — identical in hairball pressure to the original type-clique defect
  that ADR-0016 was written to fix. Removing it as an edge generator eliminates this class
  of cliques.
- **explainability**: a user can point at an edge and say "there is a `[[wikilink]]` here".
  Provenance-only edges lack that grounding.

**`kind` field update:** `kind="source"` is retired. All edges are now `kind="link"`. The
field remains on `EdgeSnapshot` and `GraphEdgeResponse` for backward wire compatibility;
the value is always `"link"`. Clients that styled dashed edges for `kind="source"` can
treat the absence of source-kind edges as the graph being cleaner (intentional regression).

### A.2 NODE INCLUSION — exclude `type == "query"` nodes

`engine.py` now filters out pages whose `page_type == "query"` before building the node
index. This mirrors llm_wiki `wiki-graph.ts:204-209`:

```python
_HIDDEN_TYPES: frozenset[str] = frozenset({"query"})
# applied after node_index load, before directed_links and candidate_pairs
node_index = {k: v for k, v in node_index.items()
              if v.get("page_type") not in _HIDDEN_TYPES}
```

Rationale: query pages are transient research artifacts (saved chat answers); the entities
and concepts extracted from them are ingested as proper wiki pages. Including query nodes
would scatter them across the graph as low-connectivity leaves, adding noise without
knowledge-structure signal.

Wikilinks from/to query nodes are also dropped (they reference a node that no longer
participates in the graph). This is correct: the useful signal from a query page is already
captured in the ingested entities it spawned.

### A.3 NODE SIZE — normalized against max degree (supersedes ADR-0016 §2)

ADR-0016 §2 defined `size = 1.0 + 2.5 * sqrt(structural_degree)`. llm_wiki
`graph-view.tsx:232-237` normalizes against the maximum-degree node:

```
size = BASE(8) + sqrt(degree / max_degree) * (MAX(28) - BASE(8))
```

The Synapse server-side implementation mirrors this:

```python
_BASE_SIZE = 8.0
_MAX_SIZE  = 28.0
max_degree_val = max(structural_degrees)  # 0 if no edges

# per-node:
if max_degree_val == 0:
    node_size = _BASE_SIZE                                    # isolated: minimum size
else:
    ratio = deg / max_degree_val
    node_size = _BASE_SIZE + math.sqrt(ratio) * (_MAX_SIZE - _BASE_SIZE)
    # → 8.0 + sqrt(deg / max_deg) * 20.0
```

Properties:
- Maximum-degree node always gets `size = 28.0` (fully-saturated).
- Isolated node (deg=0, or only node in graph) gets `size = 8.0`.
- Normalized `sqrt` gives readable graduation: at 25% of max → 14.0; at 50% → 22.2.
- `structural_degree` used (count of distinct incident wikilink edges, same as §1/A.1).

The client-side `×5` display multiplier (graphTransform.ts) is REMOVED as a consequence:
server now sends absolute sigma sizes (8–28 px range), not a sub-1 value to multiply.
Frontend-engineer must drop the multiplier in the same sprint to avoid ×5 inflation.

### A.4 Invariant compliance

| Invariant | Status |
|-----------|--------|
| **I2** | Layout (FA2) still runs only server-side via igraph/fa2_modified; coords stored in Postgres; no client layout introduced. |
| **I1** | Engine reads only `pages` + `links` from Postgres; no vault rescan. |
| **I7** | Single bounded FA2 pass; edge count is now lower (fewer candidate pairs → faster). |
| **I8** | This amendment is the D7 update; D4 (OpenAPI) unchanged (`kind` field retained, value always "link"); D2 (ER) unchanged (no new columns). |

### A.5 Test suite impact

- `TestWikilinkEdgeCount`: expects 3 edges (P1-P2, P1-P4, P3-P4); P2-P4 (shared-source only) absent.
- `TestLlmWikiParityEdgeRule`: 3 new tests — no source-kind edges, query nodes excluded, shared-source weight still contributes on wikilink edges.
- `TestClampRemovedFromEnginePath`: verifies extreme pinned coords survive unmodified (no clamping).
- Suite result: **2164 passed, 4 skipped** (net +4 from new parity tests).
