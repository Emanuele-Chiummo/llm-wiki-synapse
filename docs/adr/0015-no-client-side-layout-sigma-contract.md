# ADR-0015 — No client-side layout: sigma.js viewer contract (I2 / I4 / I3)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.3
- Decider: solution-architect
- Invariants: **I2** (client NEVER runs a force layout on the main thread), **I4** (WebGL,
  bounded DOM, virtualize long lists), I3 (Zustand selectors + shallow equality pre-compliance)
- Related: CLAUDE.md §3 I2/I3/I4, §4 F4, v0.3-scope §4/§5 (G2,G4), R10 sigma.js, R11 TanStack
  Virtual, ADR-0013, ADR-0014
- Resolves: design of the thin sigma viewer + the hard no-client-layout review rule

## Context

I2's client half: the browser renders **precomputed** coordinates from `GET /graph` in WebGL
and MUST NOT execute any force-directed / physics layout on the main thread. v0.3 introduces
the project's first frontend — a thin, read-only sigma.js viewer (not the F1 3-panel shell).
This ADR locks the client contract and the enforcement rule so any deviation is a P0 block.

## Decision

### 1. HARD REVIEW RULE — zero client-side layout (P0 block)

The frontend bundle MUST contain **no** layout/physics code. The following are FORBIDDEN in
`frontend/` source and in the built `dist/` bundle (AC-FE-2 / AC-F4-2b static grep):

- `graphology-layout-forceatlas2`, any `forceAtlas2*` import or call
- `sigma`'s any layout helper invoked as a layout (ForceAtlas2 supervisor/worker, `fa2Worker`)
- `d3-force`, `forceSimulation`, `@antv/layout`
- `random`/`circular` layout helpers used to **assign** node positions
- any `requestAnimationFrame` loop that **mutates node x/y** (a physics tick)

The viewer calls `graph.addNode(id, {x, y, label, type, size})` with the **server's** x/y and
`graph.addEdge(source, target, {weight})`. sigma renders from these fixed coordinates. The only
permitted rAF usage is sigma's own **render** loop (drawing, camera), which never recomputes
positions.

**Enforcement:** (a) a vitest/grep bundle-check test scans `dist/` for the forbidden strings
(AC-FE-2), and (b) the solution-architect manually reviews `package.json` + the viewer source
before the G2 Playwright test is written (v0.3-scope §10). Any PR introducing client layout —
even temporarily/behind a flag — is escalated to solution-architect and blocked (I2 §5).

### 2. Thin component boundary (single route, read-only)

| Concern | v0.3 decision |
|---------|---------------|
| Routing | Single route (`/` serves the viewer). No router library required. |
| Stack | React 19 + Vite + TypeScript (strict), sigma.js (WebGL) over graphology as the in-memory graph model (R10). |
| Data | One `fetch('/graph')` on mount → transform → graphology graph → sigma. No write path. |
| Interaction | Node click → `GET /pages/{id}` → show **title + type** in a read-only tooltip/drawer. No editor (no CodeMirror — I4), no chat, no provider selector (those are F1/F6/F17-UI in v0.4). |
| Lists | None required. **If** a node/search list is added and can exceed 50 items, it MUST use TanStack Virtual (R11, I4). For the v0.3 thin viewer with no such list, I4 is satisfied by default. |

### 3. Zustand store — selectors + shallow equality (I3 pre-compliance)

Even though there is no chat in v0.3, the graph store is built I3-correct now so v0.4 chat
inherits a compliant pattern:

- Components subscribe via **selector functions** (`useGraphStore(s => s.nodes)`), never the
  whole store (`useGraphStore()` with no selector is forbidden).
- Equality is **shallow** (`Object.is` / zustand `shallow`), so an unrelated slice change does
  not re-render subscribers (AC-FE-3).
- The graphology→sigma transform (AC-FE-5) is a **pure function** unit-tested in isolation
  (`graph-transform.test.ts`), keeping heavy work out of render.

### 4. How G2 (no main-thread long task >50ms) is met BY CONSTRUCTION

- No layout runs client-side → no physics loop → the only main-thread work is one fetch, one
  pure transform (O(N+E), small), and sigma's WebGL draw (GPU). On the **second** open
  (cache hit, ADR-0014) the server returns coords with no recompute, so the client work is
  identical and trivial.
- Playwright (AC-F4-6) traces long tasks during render and asserts none > 50ms; the static
  bundle check (AC-FE-2) proves no layout code can run.

### 5. How G4 (≥60fps, <20 DOM nodes) is met BY CONSTRUCTION

- sigma draws **all** nodes/edges in a **single `<canvas>`** (WebGL). DOM node count in the
  graph container is fixed (canvas + a few overlays), independent of graph size → `<20`
  (AC-F4-7(a), I4 bounded DOM).
- With positions precomputed, each frame is a pure GPU redraw (no CPU layout), so 200 nodes /
  500 edges sustains ≥60fps / ≤16ms mean frame time (AC-F4-7(b)).

## Consequences

- (+) I2 client-half guaranteed and statically provable; any regression is caught by the
  bundle grep and the architect bundle review before G2 is even written.
- (+) I4 satisfied by construction (single WebGL canvas, bounded DOM, no editor; virtualize
  rule pre-stated for any future list).
- (+) I3 pattern locked early so v0.4 chat does not retrofit selectors onto a leaky store.
- (+) Thin scope (single route, read-only) prevents F1/F6 scope creep (v0.3-scope §3).
- (−) The viewer cannot re-lay-out on the client even when desirable (e.g. user drags a node
  and wants neighbours to settle). Accepted: any reflow is a **server** recompute (debounced),
  consistent with I2. Client-side drag that merely moves one node's stored display position
  without running a physics solver is permitted in a later sprint but is OUT of v0.3 scope.
