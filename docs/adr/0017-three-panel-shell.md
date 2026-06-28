# ADR-0017 — Three-panel shell: layout, resizing, shared selection model (F1)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.4 (M4 "Usable and fluid"), Phase 1
- Decider: solution-architect
- Invariants: **I3** (Zustand selectors + shallow equality; no whole-store subscription),
  **I4** (CodeMirror 6 reserved for the future editor; NO WYSIWYG/ProseMirror; all long
  lists virtualized with TanStack Virtual; graph container DOM stays < 20),
  **I2** (graph layout stays server-side — GraphViewer is wrapped, not modified),
  **I5** (Obsidian compat — preview is read-only render, never mutates `wiki/`)
- Related: CLAUDE.md §3 (I2/I3/I4), §4b F1, docs/sprints/v0.4-pm-scope.md §2 (AC-F1-1..7,
  §4 Phase 1), ADR-0015 (no client layout — GraphViewer contract preserved), ADR-0016
  (Obsidian-style rendering — the embedded viewer is unchanged)
- Gates: this is a NEW module. No Phase-1 shell code is written before this ADR is approved.

---

## Context

v0.4 Phase 1 introduces the first real web UI: the **3-panel shell (F1)**. Today the frontend
is a single-route app — `App.tsx` renders only `<GraphViewer/>` full-screen. There is no
router, no CodeMirror, no i18n, and `@tanstack/virtual-core` is present but the React adapter
(`@tanstack/react-virtual`) is not.

F1 names the three panels **"tree / chat / preview"**, but the design must reconcile that
target with three hard facts about the current state:

1. **Chat (F6) does not exist yet** — it is Phase 3. Phase 1 must leave a clean slot, not a
   half-built chat.
2. **The graph is the primary, already-built view.** GraphViewer (ADR-0015/0016) is a
   substantial, invariant-critical component. AC-F1-4 requires it to be **embedded, not
   removed**, with its no-client-layout bundle assertion (T-NCL-001..022) still passing.
3. **There is no page-content API and demo nodes have no files.** `GET /pages/{id}` returns
   metadata only (`id, file_path, title, type, sources[], content_hash, timestamps`); the
   `Page` model (`backend/app/models.py`) has **no markdown/content column**. The 140-node
   demo dataset (ADR-0016 §M4-GUX-8) is DB-only rows with `file_path = "demo/…"` and **no
   backing vault file**. `vault/wiki/` exists but contains no generated pages. So a "render
   the document body" preview has nothing to render for the dataset we ship Phase 1 against.

The shell must therefore: host the graph as a first-class center view; expose a virtualized
navigation tree; provide a metadata/relationship preview that works **with no content API**;
and wire a single shared selection so node ↔ tree ↔ preview stay in sync — all without
violating I2/I3/I4. It must also reserve clean seams for chat (Phase 3), the provider
selector and activity panel (Phase 2), and the eventual CodeMirror editor.

---

## Decision

### 1. Layout — left Tree, center tabbed Main (Graph now, Chat later), right Preview/Inspector

The Phase-1 shell is a horizontal 3-panel group inside the existing app column (`<header>`
on top, panel group fills the rest):

```
┌───────────────────────────────────────────────────────────────────────────┐
│ Header  [Synapse]   ……… provider-selector SLOT (Phase 2) ……… [vault]       │
├──────────────┬──────────────────────────────────────┬─────────────────────┤
│  LEFT        │  CENTER  (tabbed)                     │  RIGHT              │
│  NavTree     │  ┌────────────────────────────────┐   │  PreviewPanel       │
│  (F1 tree)   │  │ [ Graph ] [ Chat ▸ Phase 3 ]   │   │  (inspector of the  │
│              │  ├────────────────────────────────┤   │   selected page)    │
│  pages       │  │                                │   │                     │
│  grouped by  │  │   GraphPanel → <GraphViewer/>  │   │  title / type /     │
│  type,       │  │   (ADR-0015/0016, untouched)   │   │  sources / links /  │
│  virtualized │  │                                │   │  neighbors          │
│              │  │   Chat tab = disabled stub     │   │                     │
│              │  └────────────────────────────────┘   │                     │
├──────────────┴──────────────────────────────────────┴─────────────────────┤
│ ActivityBar  vault · provider · last-ingest · data_version (F1 activity)   │
└───────────────────────────────────────────────────────────────────────────┘
```

- **LEFT = navigation tree (`NavTree`).** The F1 "tree" panel, fully built in Phase 1.
- **CENTER = tabbed main view (`MainTabs`).** Phase 1 ships exactly one live tab, **Graph**,
  which hosts the existing GraphViewer. A second tab **Chat** is rendered as a *disabled
  stub* (visible, not clickable, labelled "Phase 3"), so the tab strip is the seam Phase 3
  fills with zero shell rework. We choose **tabbed, not split**: the graph wants the full
  center area to be readable, and the eventual chat is an alternative primary activity, not a
  simultaneous one. This keeps Phase-1 DOM minimal and matches the "tree / chat / preview"
  target — the graph simply occupies the chat slot's container until chat exists, then
  becomes a peer tab.
- **RIGHT = preview / inspector (`PreviewPanel`).** The F1 "preview" panel. Phase 1 shows a
  **read-only metadata + relationship inspector** of the selected page (see §5). This is the
  same panel that will host the GFM-rendered wiki page (Phase 3) and, later, the CodeMirror
  editor — but Phase 1 is strictly read-only and is NOT an editor.
- **Activity panel (`ActivityBar`).** A thin bottom bar satisfying AC-F1-5: current vault,
  active provider name, last-ingest timestamp, `data_version`. In Phase 1 the provider name
  is a placeholder ("—") fed by Phase 2's provider store; vault and `data_version` come from
  `GET /status` and the graph store.

Rationale for hosting the graph in the **center** rather than the right panel: AC-F1-4 allows
"right panel or as a tab", but the graph is a wide canvas that benefits from the largest
region and reads naturally as the *main activity*, with the right panel acting as the
*detail/inspector* of whatever is selected in the main view. This is the conventional
3-pane IDE/Obsidian topology (navigator | workspace | inspector) and it makes the Phase-3
chat tab a drop-in peer of the graph.

### 2. Resizing — `react-resizable-panels` (add dependency)

We add **`react-resizable-panels`** (Brian Vaughn / bvaughn) as the panel-group primitive,
rather than a hand-rolled flex+drag implementation.

Justification (the trade-off is explicit):

- **Accessibility is the deciding factor.** AC-F1-7 and our I-level a11y bar require
  keyboard-resizable panels. `react-resizable-panels` ships `PanelResizeHandle` with correct
  `role="separator"`, `aria-valuenow/min/max`, `aria-orientation`, and arrow-key resize out
  of the box. Re-implementing correct separator semantics + keyboard handling by hand is
  exactly the kind of "clever and fragile" we avoid.
- **No per-frame JS layout (AC-F1-1).** The library resizes via CSS `flex-grow` percentages
  on pointer move; it does not run a JS layout loop every frame. This satisfies "no layout
  calculated in JS on every frame" and keeps reflow well under the 16ms AC-F1-7 budget.
- **Bundle cost is acceptable.** ~5 kB gzipped, zero runtime deps, actively maintained,
  React-19 compatible. This is a smaller risk surface than a bespoke drag implementation that
  we would have to test for a11y, touch, RTL, and min/max clamping ourselves.
- **Persistence hook.** Its `onLayout` callback gives us panel sizes to persist (Phase 2
  localStorage, AC-F16-settings-1) without extra plumbing.

Constraint stated: the panel group MUST use `direction="horizontal"` percentage sizing and
MUST NOT introduce any rAF/JS layout loop. The GraphViewer's own canvas resizes via sigma's
internal `ResizeObserver`; we must ensure the Graph panel container has a stable, non-zero
height so sigma sizes correctly (the panel content area is `height: 100%`, `min-height: 0`).

### 3. Tree — `NavTree`, data from `GET /pages`, grouped by type, virtualized

Add **`@tanstack/react-virtual`** (the React adapter; `virtual-core` alone is insufficient).

- **Data source:** `GET /pages` (paginated; `limit` up to 500). For Phase 1 the tree fetches
  the page list for the active vault. The endpoint returns metadata only — exactly what the
  tree needs (no body required).
- **Grouping:** pages are grouped by `type` into the canonical buckets
  `concept · entity · source · synthesis · comparison`, plus an `other` bucket for `null`/
  unknown types (mirrors the GraphViewer legend and CVD palette). Groups render as collapsible
  headers; items render under their group.
- **Virtualization (I4 / AC-F1-2):** the tree is rendered as a **single flattened, virtualized
  list** of rows (group-header rows + page rows) via `useVirtualizer`. We flatten rather than
  nest so one virtualizer covers the whole tree and AC-F1-2 ("no non-virtualized list
  rendering > 50 DOM nodes at once") holds regardless of page count. Collapsing a group simply
  drops its child rows from the flattened array.
- **Item model:**

  ```ts
  type TreeRow =
    | { kind: "group"; type: PageType | "other"; count: number; collapsed: boolean }
    | { kind: "page"; id: string; title: string; type: PageType | "other" };
  ```

  where `id` is the page UUID (identical to the graph node id — this is what makes
  tree ↔ graph selection a single shared key).
- **Selection behavior:** clicking a `page` row sets the shared `selectedNodeId` (§4).
  Selecting in the tree does **not** pan the graph automatically in Phase 1 (camera-follow is
  a Phase 3+ nicety); it updates the preview and highlights the row. The selected row gets
  `aria-selected="true"`; the list container is a `role="tree"` with `role="treeitem"` rows
  and `role="group"` headers (see §8).

### 4. Selection model — extend `graphStore` with a `uiSlice`; typed selectors only (I3)

We **extend the existing `graphStore`** rather than introducing a separate store, but we add
the selection/UI fields as a clearly delimited **UI slice** with its own selectors. Rationale:
`selectedNodeId` already lives in `graphStore` and is already wired into GraphViewer's
click/aria-live logic. Splitting selection into a second store would force GraphViewer to
subscribe to two stores and would risk cross-store sync bugs for the exact piece of state
(`selectedNodeId`) that must stay singular. One store, one selection key, one source of truth.

The new UI state (additive to `GraphState`):

```ts
// ── UI slice (added to graphStore) ──
type CenterTab = "graph" | "chat"; // "chat" is a disabled stub in Phase 1

interface UiState {
  // selectedNodeId ALREADY EXISTS — it remains the single shared selection key.
  selectedSource: "graph" | "tree" | null; // who set the selection (for subtle UX, optional)
  activeTab: CenterTab;
  treeCollapsed: Record<string, boolean>; // group type -> collapsed
}

interface UiActions {
  // setSelectedNodeId ALREADY EXISTS — tree and graph BOTH call it.
  selectPage: (id: string | null, source: "graph" | "tree") => void; // sets id + source
  setActiveTab: (tab: CenterTab) => void;
  toggleGroup: (type: string) => void;
}
```

Wiring (the crux of "node ↔ tree ↔ preview"):

- **Single key:** `selectedNodeId` is the one shared selection. Graph node click already calls
  `setSelectedNodeId(node)`. The tree row click calls `selectPage(id, "tree")`, which sets the
  same key. The PreviewPanel subscribes to `selectedNodeId` and re-derives its content. No new
  cross-component event bus — the store is the bus.
- **Graph already reacts** to `selectedNodeId` via its existing `useEffect` (aria-live +
  refresh). Setting it from the tree therefore announces the selection to screen readers and
  re-applies sigma reducers for free; no GraphViewer change is required for selection-in.
  (Optional Phase-1+ enhancement, out of scope here: camera-follow when `selectedSource ===
  "tree"`.)
- **I3 compliance:** every consumer subscribes through a **typed selector** with shallow
  equality where the selection returns an object. New selectors:
  `selectActiveTab`, `selectTreeCollapsed` (shallow), `selectSelectPage`, `selectSetActiveTab`,
  `selectToggleGroup`. No component calls `useGraphStore()` without a selector. The existing
  `selectSelectedNodeId` is reused by both NavTree (for the highlighted row) and PreviewPanel.
- **No whole-store subscription, no per-token churn:** the tree subscribes only to
  `selectedNodeId` + `treeCollapsed`; the preview only to `selectedNodeId` (+ derives from the
  already-loaded `nodes`/`edges` arrays it reads via `selectNodes`/`selectEdges`). Selecting a
  node re-renders the tree's highlighted row and the preview — nothing else.

### 5. Preview content source — Phase 1 = metadata + relationships (option a); endpoint contract reserved (option b)

**Decision: Phase 1 ships option (a).** The PreviewPanel renders a rich, read-only
**inspector** built entirely from data already on the client:

- From `GET /pages/{id}` (already wired as `fetchPageDetail`): `title`, `type`, and — once we
  extend the client type to carry them (the API already returns them) — `sources[]` and
  `file_path`.
- From the in-memory graph (`selectNodes`/`selectEdges`): the selected node's **neighbors**
  (incident edges → neighbor titles + edge `kind` link/source), in/out degree, and the node
  `type` color.
- A clear, non-error **"No document body — demo node"** note when there is no content to show,
  so the empty state is intentional, not a bug.

This is the only correct Phase-1 choice because (i) there is no content column or content API,
and (ii) the 140-node demo dataset has no backing files — options (b) and (c) would render an
empty body for every node we actually ship against. The metadata+relationship inspector is
genuinely useful (it mirrors what the graph tooltip shows, expanded) and exercises the full
selection-sync wiring end to end.

**Fast-follow (reserved, NOT Phase-1 work): option (b) content endpoint.** So real
(non-demo) pages can render their body in Phase 3's GFM preview, we reserve this contract for a
backend engineer to implement as a Phase-2/3 fast-follow:

```
GET /pages/{id}/content
  200 → { id: str, file_path: str, markdown: str, source: "vault" }
  204 / 200 with markdown:"" → page exists but has no backing file (demo node)
  404 → unknown or soft-deleted page
```

Semantics: reads `vault/wiki/{…}.md` (or the resolved page file) for real pages; returns an
empty/204 body for demo nodes (`file_path` starting `demo/`). It MUST NOT read arbitrary paths
— the path is resolved from `pages.file_path` joined to the vault root, never taken from the
request (path-traversal guard). This endpoint, the GFM renderer, and the CodeMirror editor are
explicitly **out of Phase-1 scope** — Phase 1 preview is metadata/relationship render only.
We do **not** pursue option (c) (synthetic markdown in the seed) because it pollutes the demo
fixture and still doesn't represent real ingest output; option (b) on real pages is the right
long-term path.

### 6. Component tree

All under `frontend/src/components/`, except the new store slice (extends existing store) and
the new tree client. One-line responsibility each:

| Component (file) | Responsibility |
|---|---|
| `AppShell.tsx` | Top-level layout: `<Header>` + `<PanelGroup>` (left/center/right) + `<ActivityBar>`. Replaces the body of `App.tsx`. |
| `Header.tsx` | Branding + a `providerSelectorSlot` placeholder div (Phase 2 fills it) + vault label. |
| `panels/PanelGroup.tsx` | Thin wrapper over `react-resizable-panels` `<PanelGroup direction="horizontal">` with the 3 `<Panel>`s + 2 `<PanelResizeHandle>`s; min sizes + `onLayout` hook. |
| `nav/NavTree.tsx` | Left panel: fetches `GET /pages`, builds grouped flattened `TreeRow[]`, virtualizes via `useVirtualizer`, handles select + group collapse. |
| `nav/useNavTreeData.ts` | Hook: fetch + group + flatten pages into `TreeRow[]`; exposes loading/error. |
| `center/MainTabs.tsx` | Tab strip (Graph \| Chat-stub) + active-tab content host; reads/sets `activeTab` from the UI slice. |
| `center/GraphPanel.tsx` | Thin wrapper that mounts the **existing** `<GraphViewer/>` unchanged inside the center panel (ensures `height:100%; min-height:0`). |
| `preview/PreviewPanel.tsx` | Right panel: subscribes to `selectedNodeId`; renders metadata + relationship inspector (§5); empty-state note for demo nodes. |
| `activity/ActivityBar.tsx` | Bottom bar: vault, provider (placeholder Phase 1), last-ingest, `data_version` (AC-F1-5). |
| `common/ScenarioTemplates.tsx` | Renders ≥2 scenario template buttons (AC-F1-6); Phase 1 they pre-fill a (disabled) chat-input placeholder / are wired into the chat store in Phase 3. Lives in the chat-stub region. |

Supporting (non-component) additions:

| File | Responsibility |
|---|---|
| `store/graphStore.ts` (extend) | Add the UI slice fields + actions + selectors from §4. No breaking change to existing selectors. |
| `api/pagesClient.ts` (new) | `fetchPages(vaultId, {limit, offset})` → `PageListResponse`; typed. (Keeps graph client focused.) |
| `api/types.ts` (extend) | Add `PageListItem`/`PageListResponse` types; extend `PageDetail` to include `sources?: string[]` and `file_path?: string` (already returned by the API). |

`App.tsx` shrinks to: render `<AppShell/>`. The existing `<header>` styling migrates into
`Header.tsx`.

### 7. Invariant confirmation

- **I3 (selectors + shallow equality):** every new subscription uses a typed selector;
  object-returning selectors use `useShallow`. The shared selection is a single scalar key
  (`selectedNodeId`) — Object.is equality, no spurious re-renders. No component subscribes to
  the whole store. The tree's per-row render is driven by the virtualizer, not by store churn.
- **I4 (CodeMirror reserved; virtualization; bounded graph DOM):** Phase-1 preview is a
  **read-only render, NOT an editor** — no CodeMirror, no `contentEditable`, no ProseMirror
  anywhere (AC-F1-3). CodeMirror 6 remains reserved for the future editor and is not added in
  Phase 1. The NavTree is virtualized via TanStack Virtual (AC-F1-2). The GraphViewer is
  **wrapped, not modified**: its single-WebGL-canvas, <20-DOM-node container (AC-F1-4) is
  untouched, so T-NCL-001..022 and the bundle grep remain valid. Budget note: the shell adds
  panels/header/activity DOM *outside* the graph container; the **graph container's** internal
  DOM count is unchanged and stays < 20.
- **I2 (graph stays server-side):** GraphPanel imports and mounts the existing GraphViewer
  verbatim. No layout code is added anywhere in the shell. The tree and preview read
  precomputed `nodes`/`edges` from the store; they never compute positions. The no-client-layout
  guard is preserved by construction.
- **I5 (Obsidian compat):** the preview never writes to `wiki/`; Phase 1 is read-only. No
  `.obsidian` or vault file is touched by the shell.

### 8. Accessibility

- **Three landmark regions:** left panel `role="navigation"` aria-label "Page navigator";
  center `role="main"`; right panel `role="complementary"` aria-label "Page inspector". The
  activity bar is `role="status"` (or `contentinfo`) aria-label "Vault activity".
- **Resizable via keyboard:** `react-resizable-panels` `PanelResizeHandle` renders
  `role="separator"` with `aria-orientation="vertical"`, `aria-valuenow/min/max`, and arrow-key
  resize. Each handle has an `aria-label` ("Resize navigator panel" / "Resize inspector panel").
- **Focus order:** Header → NavTree (tree) → first resize handle → MainTabs (tab list, roving
  tabindex; Chat tab is `aria-disabled`) → Graph content → second resize handle → PreviewPanel
  → ActivityBar. Tab strip uses `role="tablist"`/`role="tab"`/`role="tabpanel"` with
  arrow-key navigation and `aria-selected`.
- **Tree semantics:** container `role="tree"`, group headers `role="treeitem"` +
  `aria-expanded` (or `role="group"`), page rows `role="treeitem"` + `aria-selected`. Selected
  row reflects the shared `selectedNodeId`. Selection changes are already announced by the
  GraphViewer's existing `aria-live` region (reused via the shared key — §4), so tree-driven
  selection is announced to screen readers without duplicating the live region.
- Dark-palette contrast continues the GitHub-dark tokens already in use (#0d1117 / #161b22 /
  #e6edf3, ≈16:1 AAA for body text).

---

## Consequences

**Positive**

- The graph (the only built, invariant-critical view) is embedded unchanged; ADR-0015/0016
  contracts and tests (T-NCL-001..022) hold with zero risk.
- One shared selection key makes node ↔ tree ↔ preview sync trivial and keeps I3 clean.
- Tabbed center + disabled chat stub is the exact seam Phase 3 fills with no shell rework;
  the provider-selector slot and activity-bar placeholders are the seams Phase 2 fills.
- The metadata/relationship preview works against the demo dataset with no new backend work,
  while the reserved `GET /pages/{id}/content` contract gives Phase 3 a clean path to render
  real page bodies.
- Accessibility (keyboard-resizable panels, landmark roles, tree semantics) is satisfied by a
  well-maintained primitive rather than fragile hand-rolled code.

**Negative / trade-offs (stated explicitly)**

- **Two new dependencies:** `react-resizable-panels` (~5 kB gz) and `@tanstack/react-virtual`.
  Accepted: a11y correctness and I4 virtualization outweigh the bundle cost; both are small,
  maintained, React-19 compatible, zero-runtime-dep.
- **Demo nodes show no body in Phase 1.** Mitigated by an explicit "No document body — demo
  node" empty state and the reserved content endpoint for real pages. This is a deliberate
  Phase-1 boundary, not a defect.
- **Center hosts the graph, not chat, in Phase 1.** The graph temporarily occupies the chat
  tab's container. When chat lands (Phase 3) it becomes a peer tab; the "tree/chat/preview"
  target is reached without re-architecting the shell.
- **The store grows a UI slice.** Accepted over a second store because the shared selection
  must remain singular; the slice is clearly delimited and selector-gated.

**Follow-ups (NOT Phase-1 scope — tracked for later phases)**

- Phase 2: provider-selector fills the header slot; activity-bar provider name becomes live;
  panel sizes + tree-collapse persisted to localStorage (AC-F16-settings-1).
- Phase 3: chat tab activated; `GET /pages/{id}/content` + GFM render + CodeMirror editor in
  the preview/editor panel; optional camera-follow on tree selection.
- D1 component.mmd updated (tech-writer) to add AppShell/NavTree/MainTabs/PreviewPanel/
  ActivityBar and the GraphPanel→GraphViewer wrapping.

---

## Implementation spec (hand to frontend-engineer)

**Dependencies to add** (`frontend/package.json`):
- `react-resizable-panels` (latest, React-19 compatible) — panel group + keyboard-resizable handles.
- `@tanstack/react-virtual` (^3, matching the present `@tanstack/virtual-core`) — tree virtualization.

**Build order:**
1. Extend `store/graphStore.ts` with the UI slice (§4): fields `selectedSource`, `activeTab`,
   `treeCollapsed`; actions `selectPage`, `setActiveTab`, `toggleGroup`; selectors
   `selectActiveTab`, `selectTreeCollapsed` (shallow), `selectSelectPage`, `selectSetActiveTab`,
   `selectToggleGroup`. Do NOT remove or change existing `selectedNodeId`/`setSelectedNodeId`.
2. Add `api/pagesClient.ts` (`fetchPages`) and extend `api/types.ts` (`PageListItem`,
   `PageListResponse`; widen `PageDetail` with optional `sources`, `file_path`).
3. Build `panels/PanelGroup.tsx` (horizontal, 3 panels, min sizes ~[15%,40%,20%],
   `onLayout` no-op for now).
4. Build `nav/useNavTreeData.ts` + `nav/NavTree.tsx` (fetch → group by type → flatten →
   `useVirtualizer`; row click → `selectPage(id,"tree")`; group header → `toggleGroup`).
5. Build `center/MainTabs.tsx` (tablist: Graph active, Chat `aria-disabled` stub hosting
   `ScenarioTemplates`) + `center/GraphPanel.tsx` (mount existing `<GraphViewer/>`,
   `height:100%; min-height:0`).
6. Build `preview/PreviewPanel.tsx` (subscribe `selectedNodeId`; fetch detail via
   `fetchPageDetail`; derive neighbors from `selectNodes`/`selectEdges`; empty-state note).
7. Build `activity/ActivityBar.tsx` (vault + `data_version` from store/`GET /status`; provider
   placeholder) and `Header.tsx` (branding + provider-selector slot).
8. Build `AppShell.tsx`; reduce `App.tsx` to `<AppShell/>`.
9. Wire landmark roles + handle aria-labels + tree roles per §8.

**Acceptance mapping:** AC-F1-1 (3 resizable panels, no per-frame JS layout) → §1/§2;
AC-F1-2 (tree virtualized, ≤50 DOM rows) → §3; AC-F1-3 (no WYSIWYG, no CodeMirror in preview)
→ §5/§7; AC-F1-4 (graph embedded, T-NCL still green) → §6/§7; AC-F1-5 (activity panel) →
ActivityBar; AC-F1-6 (≥2 scenario templates) → ScenarioTemplates; AC-F1-7 (resize ≤16ms
reflow) → §2 (CSS percentage resize, no JS layout loop).

**Do NOT:** add CodeMirror/ProseMirror/contentEditable; modify GraphViewer internals; add any
layout/force code; subscribe to the whole store; render a non-virtualized list > 50 rows;
read page bodies from a content API (it does not exist in Phase 1).
