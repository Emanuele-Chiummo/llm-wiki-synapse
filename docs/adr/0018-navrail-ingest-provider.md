# ADR-0018 — NavRail IA, Ingest Activity View, Provider Selector, Settings, i18n (M4 Phase 2)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.4 (M4 "Usable and fluid"), Phase 2
- Decider: solution-architect
- Invariants: **I3** (Zustand selectors + shallow equality; no whole-store subscription;
  no per-token / per-frame heavy work), **I4** (CodeMirror reserved for the future editor —
  NO WYSIWYG/ProseMirror/Milkdown; all long lists virtualized with TanStack Virtual; graph
  container DOM bounded), **I6** (pluggable provider — no provider/model ID hardcoded in any
  component; values come from `GET /provider/config`), **I7** (bounded loops; `total_cost_usd`
  visible to the user at 4dp), **I2** (graph layout stays server-side — GraphViewer untouched),
  **I5** (Obsidian compat — `.obsidian` hardening is backend-only; UI never writes `wiki/`)
- Related: ADR-0017 (3-panel shell this extends), ADR-0008 (provider_config + ingest_runs
  schema), ADR-0009 (bounded-loop cost ledger), ADR-0006 (POST /ingest/trigger contract),
  CLAUDE.md §3 (I2/I3/I4/I6/I7), §4b (F1, F14, F16, F17), §5 (F17 provider detail),
  docs/sprints/v0.4-pm-scope.md §1b/§2 (F1-NAV, F1-INGEST-VIEW, BE-INGEST-RUNS, F17-UI,
  F14, F16-rest; AC-F1-NAV-1..8, AC-F1-IV-1..8, AC-BE-IR-1..5, AC-F17-UI-1..6, AC-F14-1..5,
  AC-F16-i18n/settings-*), §3 (F9 boundary — OUT of scope)
- Gates: this is a NEW set of modules + a NEW backend endpoint. No Phase-2 code is written
  before this ADR is approved.

---

## Context

ADR-0017 delivered the Phase-1 shell: a single horizontal 3-panel group (NavTree | MainTabs
hosting GraphViewer | PreviewPanel) under a Header, over an ActivityBar. The center is tabbed
(`graph` live, `chat` a disabled stub) and selection flows through one shared `selectedNodeId`
key in the `graphStore` UI slice.

Phase 2 adds, at stakeholder request, a navigation rail and an operations surface, plus the
provider/settings/i18n work the sprint already required:

1. **F1-NAV** — a top-level mode switcher. Today the left region is *only* a page tree; the
   shell has no way to switch between distinct activities (Pages, Graph, Ingest, Settings, and
   eventually Chat). The tabbed center handles Graph↔Chat but is the wrong place for Ingest and
   Settings, which are not "center documents" — they are whole-app modes.
2. **F1-INGEST-VIEW** — a read-only history of `ingest_runs` (status, provider, pages created,
   `total_cost_usd`, timestamps, errors) plus a "Run Ingest" trigger. This surfaces the I7 cost
   ledger to the user before chat exists. It is **not** the F9 review queue (no Create / Skip /
   Deep-Research actions — that is M5; see §0).
3. **F17-UI** — the Provider Selector that fills the Header slot ADR-0017 reserved; reads/writes
   `GET|POST /provider/config`.
4. **F14 + F16-rest** — a Settings section: context-window size, language (IT/EN), persistence.
5. **F16-i18n** — react-i18next with `en.json` + `it.json`; every new UI string is a key.

**Visual direction** (grounded in the six nashsu/llm_wiki screenshots): a thin far-left
**icon rail** (~48px, monochrome line/outline icons, badge for pending counts, active item =
soft tint) as the top-level mode switcher; the left tree carries a Knowledge/Files-style toggle;
Ingest reads as a persistent file-by-file **activity queue** of cards (status, manifest); chrome
is monochrome, color is reserved for graph data; cards use a solid-dark primary button with
ghost secondaries. **We adapt all of this to Synapse's existing GitHub-dark palette
(`#0d1117` / `#161b22` / `#e6edf3`, borders `#21262d`) — we do NOT switch to llm_wiki's light
theme.** No Milkdown/ProseMirror anywhere (I4); CodeMirror remains reserved for the future editor.

**Three hard facts constrain the build:**

- **The `ingest_runs` table is missing 3 of the 9 contract fields.** `models.py` `IngestRun` has
  `id, vault_id, page_id, provider_name, provider_type, model_id, route, max_iter_used,
  total_tokens, total_cost_usd, converged, cost_anomaly, started_at, finished_at`. The PM
  contract (§1b / AC-BE-IR-1) requires `status`, `pages_created`, `error_message` — none exist —
  and renames `max_iter_used → iterations_used`, `finished_at → completed_at`. This forces a
  migration decision (§7).
- **`POST /ingest/trigger` takes a `file_path` body, not a `vault_id`** (ADR-0006; `main.py`
  `IngestTriggerRequest{file_path}`). AC-F1-IV-3 says "trigger with the current vault_id". The
  endpoint as built ingests one file; it does not "ingest the vault". The Run-Ingest UX must be
  designed around the real contract (§3), not an imagined whole-vault trigger.
- **`GET /provider/config` returns raw rows, not a resolved/active selection.** It lists all
  `provider_config` rows (`{items, total}`); there is no "which provider is active for this
  vault" endpoint. The selector must derive the active row client-side from precedence
  (operation+vault > vault > global) — or post a new row to change it (§4).

The shell must therefore: add a persistent left icon rail that swaps the whole 3-panel content
per section; render Ingest and Settings as section views (not center tabs); fill the Header
provider slot and the ActivityBar provider placeholder with live data; and do all of this on the
existing single `graphStore` with selector discipline — without touching the graph (I2), without
per-token/per-frame work (I3), with every long list virtualized (I4), with no hardcoded provider
(I6), and with cost shown at 4dp (I7).

---

## §0. The F9 boundary (restated as a hard gate)

The Ingest Activity View is **read-only history + a single trigger button**. It MUST NOT contain
any of: an approve / reject / **Skip** / **Create Page** / **Deep-Research** action; a
pre-generated-query list; a queue-management surface; per-page review cards with accept/decline.
Those are **F9 (M5)**. Any PR adding them to this view in M4 is rejected on review. The cards in
the llm_wiki "Review" / "Deep Research" screenshots (the ones with `Deep Research | Create Page |
Skip` buttons) are the **F9 design we are explicitly NOT building now** — we borrow only their
*card layout language* (status, title, metadata, one primary action), not their *actions*.

---

## Decision

### 1. Information architecture — persistent left icon rail; section swaps the whole panel region

We add a **`NavRail`**: a persistent, ~48px vertical icon rail pinned to the far-left edge of the
shell, *outside* and *to the left of* the existing 3-panel group. It is the top-level mode
switcher (the llm_wiki far-left rail). It is always visible and never collapses across section
switches (AC-F1-NAV-3, AC-F1-NAV-6).

```
┌───────────────────────────────────────────────────────────────────────────────┐
│ Header  [⚡ Synapse v0.4]  ………………  [ ProviderSelector ▾ ]   [vault]            │
├────┬──────────────────────────────────────────────────────────────────────────┤
│ N  │  SECTION CONTENT (one of: Pages · Graph · Ingest · Settings)              │
│ a  │                                                                          │
│ v  │   Pages    → PanelGroup: NavTree | GraphPanel | PreviewPanel  (as today)  │
│ R  │   Graph    → GraphPanel maximized (NavTree+Preview collapsed/hidden)      │
│ a  │   Ingest   → IngestView (center) | IngestRunDetail (right)                │
│ i  │   Settings → SettingsPanel (center, single column)                       │
│ l  │                                                                          │
│ ▸Pa│   Chat slot = present, disabled, "Phase 3" (NOT a panel)                  │
│  Gr│                                                                          │
│  In│                                                                          │
│  Se│                                                                          │
├────┴──────────────────────────────────────────────────────────────────────────┤
│ ActivityBar  vault · provider (live, F17) · last-ingest · data_version        │
└───────────────────────────────────────────────────────────────────────────────┘
```

**Rail items** (top group; Settings pinned to the bottom of the rail, IDE-convention):

| Order | Section id | Icon (lucide-style outline) | Badge | State |
|---|---|---|---|---|
| 1 | `pages` | `files` / list-tree | — | live |
| 2 | `graph` | `share-2` / network | — | live |
| 3 | `ingest` | `download` / inbox | **count of `running` runs** (soft tint, hidden when 0) | live |
| (sep) | `chat` | `message-square` | "Phase 3" tag | **disabled** (AC-F1-NAV-1) — does NOT open a panel |
| bottom | `settings` | `settings` (gear) | — | live |

**Section → panel mapping** (the crux — exactly how a rail click reshapes the 3 panels):

| Section | Left | Center | Right |
|---|---|---|---|
| **Pages** | `NavTree` (virtualized tree, ADR-0017 §3) | `GraphPanel` (the unchanged GraphViewer) | `PreviewPanel` (metadata+relationship inspector, ADR-0017 §5) |
| **Graph** | hidden | `GraphPanel` maximized (full width) | hidden |
| **Ingest** | hidden | `IngestView` (run list + Run-Ingest) | `IngestRunDetail` (selected-run artifact manifest) |
| **Settings** | hidden | `SettingsPanel` (single-column form) | hidden |

Implementation of the swap: the **`SectionRouter`** component (mounted where `AppShell` mounts
`PanelGroup` today) reads `activeSection` from the store and renders the matching layout. **Pages**
renders the existing `<PanelGroup/>` verbatim (zero rework — ADR-0017 holds). **Graph** renders a
single full-bleed `<GraphPanel/>`. **Ingest** and **Settings** render their own 2-pane / 1-pane
layouts. This is a *router by store key*, not React Router — there is no URL route, no page
reload, no navigation event (AC-F1-NAV-2, AC-F1-NAV-7). MainTabs' Graph↔Chat tab strip is
**removed from the Pages center** and superseded by the rail: Graph is now a rail section, and the
Chat slot moves to the rail (disabled). The center under Pages hosts `GraphPanel` directly. This
keeps exactly one place that says "graph here" and makes Phase 3 register a `chat` rail section
rather than re-enable a tab.

> Decision rationale — rail vs. keeping center tabs: the four destinations are **app modes**
> (a tree workspace, a full-canvas graph, an operations log, a settings form), not co-equal
> documents in one workspace. A mode switcher (rail) is the correct control; center tabs would
> wrongly imply Ingest/Settings are "documents" peer to the graph. This matches the llm_wiki
> far-left rail and the conventional IDE topology (activity-bar | workspace).

The **ActivityBar stays mounted by `AppShell` below the section region**, so it is visible in
every section (AC-F1-NAV-6). The **Header (with ProviderSelector) likewise stays mounted above**
the section region in every section.

### 2. State — extend `graphStore` with `activeSection`; typed selectors + shallow equality (I3)

We extend the **existing `graphStore`** (no second store; consistent with ADR-0017 §4 — one store,
selector-gated). Add `activeSection` to the UI slice. We do **not** remove `activeTab`; it becomes
vestigial-for-Phase-2 (the Pages center no longer shows a tab strip) but is retained so Phase 3 can
decide whether chat is a rail section or a Pages-center tab without a store migration. The
`treeCollapsed` / `selectPage` / `selectedNodeId` keys are untouched.

```ts
// ── added to graphStore UI slice ──
export type Section = "pages" | "graph" | "ingest" | "settings"; // "chat" reserved (Phase 3)

interface UiStateAdditions {
  activeSection: Section;            // default "pages"
}
interface UiActionsAdditions {
  setActiveSection: (section: Section) => void;
}

// initial: activeSection: "pages"

// action
setActiveSection: (activeSection) => set({ activeSection }),

// selectors (scalar → Object.is; no shallow needed)
export const selectActiveSection = (s: GraphStore): Section => s.activeSection;
export const selectSetActiveSection = (s: GraphStore): GraphActions["setActiveSection"] =>
  s.setActiveSection;
```

I3 discipline (AC-F1-NAV-8): `activeSection` is a **scalar**; NavRail subscribes to it and to
`setActiveSection` only; a section change re-renders the rail's active highlight and swaps the
section region — it does **not** invalidate `selectNodes`/`selectEdges`/`selectStatus` (those
selectors return the same references, so their consumers do not re-compute). NavRail never calls
`useGraphStore()` without a selector. The ingest-running badge count is derived inside the Ingest
data hook (§3), **not** stored in `graphStore` — it must not couple the rail's render to ingest
polling. The rail reads the badge via a small `useIngestRunningCount()` hook that subscribes to
the ingest store/hook, keeping ingest churn out of the graph store entirely.

Provider, settings, and ingest state do **not** go in `graphStore`. They get their own small,
purpose-scoped Zustand stores (or hooks) so graph rendering never re-runs on a provider change:

- `store/providerStore.ts` — active provider config + the raw list (§4).
- `store/settingsStore.ts` — context window, language, persistence (§5).
- `store/ingestStore.ts` (or a `useIngestRuns` hook with local state) — runs list, polling,
  selected run (§3).

Each is selector-gated with `useShallow` for object/array returns (I3). Keeping them separate from
`graphStore` is the I3-correct choice: a settings or provider change must not re-render the graph.

### 3. Ingest Activity View — read-only run list (virtualized) + a real "Run Ingest" trigger

`IngestView` (center, Ingest section) renders the run history; `IngestRunDetail` (right) renders the
selected run's manifest. Maps AC-F1-IV-1..8.

**List (AC-F1-IV-1,2):** rows from `GET /ingest/runs` (§7 contract). Each row is a **card** in the
llm_wiki activity-queue language, adapted to dark theme:

- **status badge** — color-coded: `running` (soft-blue `#1f6feb` pulsing), `completed`
  (green `#3fb950`), `failed` (red `#f85149`), `converged_false` (amber `#d29922`, label
  "did not converge"). Badge label is an i18n key, not a literal (AC-F1-NAV-5 spirit / §6).
- **provider** — `provider_type` (Local / API / CLI mapped via i18n) + `model_id` if surfaced.
- **pages created** — `pages_created` (int).
- **cost** — `total_cost_usd` formatted to **exactly 4 decimal places** (`$0.0000`) to honour I7
  cost visibility (AC-F1-IV-1). `$0.0000` for local/cli is correct and shown, not hidden.
- **time** — `started_at` as a relative human string ("2 minutes ago") via a tiny pure formatter
  (no date lib needed; `Intl.RelativeTimeFormat`, locale-aware for i18n). Absolute timestamp in
  `title`/tooltip.
- **error** — `error_message` truncated to 80 chars with an expand control when present
  (AC-F1-IV-1).

**Virtualization (I4 / AC-F1-IV-2):** the list is rendered with **TanStack Virtual**
(`@tanstack/react-virtual`, already a dependency) — at most ~40 row DOM nodes mounted regardless of
history length. We use virtualization with infinite paging: the hook holds an accumulating
`runs[]`, fetches the next `limit=20` batch on scroll-near-end (offset paging, §7), and the
virtualizer windows the accumulated array. (Equivalent "Load more" button is acceptable, but
virtualized scroll satisfies I4 directly and matches the persistent-queue feel.)

**Run Ingest button (AC-F1-IV-3) — designed around the REAL endpoint.** `POST /ingest/trigger`
takes `{file_path}` (one file under `vault/raw/sources/`), **not** a vault id. There is no
"ingest the whole vault" endpoint in M4 scope. Therefore Run-Ingest is a **two-step control**:

1. The button opens a small inline form / popover: a **file path input** (relative to
   `vault/raw/sources/`) with a short helper string. (A file *picker* requires a backend
   directory-listing endpoint that does not exist and is out of M4 scope — so a path input is the
   correct, honest control. If a `GET /raw/sources` listing lands as a fast-follow, the input
   becomes a dropdown with no UI rework.)
2. Submit → `POST /ingest/trigger {file_path}`. On HTTP 202: transient **success toast**
   ("Ingest started: <file>") and the list **auto-refreshes** (re-fetch `GET /ingest/runs`,
   offset 0). On non-2xx: **error toast** with `detail` from the body (AC-F1-IV-3).

> This is a deliberate, explicit deviation from AC-F1-IV-3's literal "with the current vault_id":
> the backend trigger is per-file by contract (ADR-0006). Flagged to PM/orchestrator — see
> Consequences. It does NOT change the F9 boundary (still read-only history + a trigger).

**Polling (AC-F1-IV-4):** the ingest hook polls `GET /ingest/runs` (offset 0, limit 20) on a
default **5s** interval **only while at least one visible run has `status === "running"`**; polling
stops when no running rows remain (AC-F1-IV-4). The interval is a constant (configurable later via
settings). Running rows show a pulsing badge; `prefers-reduced-motion` disables the pulse animation
(reuse the GraphUX media-query pattern). Polling uses a single `setTimeout` chain with
`AbortController` cleanup (no `setInterval` leak; no unbounded loop — I7-aligned).

**Read-only (AC-F1-IV-5):** no row has any action other than *select* (to show its manifest in
`IngestRunDetail`) and *expand error*. No approve/reject/skip/deep-research — §0 gate.

**Artifact manifest (AC-F1-IV — optional expansion).** `IngestRunDetail` (right pane) shows the
selected run's available fields as a manifest: `route` (orchestrated | delegated),
`iterations_used` / `max_iter`, `total_tokens`, `converged`, `cost_anomaly` (flag if cost > $1.00,
I7/ADR-0009), `model_id`, `page_id` (link to the created page in the Pages section if resolvable).
We do **not** invent a per-file artifact list the backend cannot supply; the manifest renders only
columns the contract returns. If `page_id` is present, "View page" sets `activeSection="pages"` +
`selectPage(page_id, "tree")` — reusing the shared selection key (ADR-0017).

### 4. Provider Selector (F17-UI) — Header slot; reads/writes `provider_config`; capability-aware display

`ProviderSelector` fills the **Header provider slot** ADR-0017 reserved (Header.tsx
`app-header__provider-slot`). It is a **dropdown**, not a config editor (we deliberately do not
overbuild — CRUD of provider rows stays in the Settings section / backend, §5). Maps
AC-F17-UI-1..6.

**Reads — `GET /provider/config`.** The endpoint returns **raw rows** `{items, total}` (no
"active" concept). `providerStore` fetches the list once on mount and derives the **active row**
client-side using the documented precedence (operation+vault > vault > global, ADR-0008 §2) for
the **current vault + the chat/ingest operation context**. For the Phase-2 selector we resolve at
**vault scope** (the dropdown changes the vault-level provider; per-operation override is a Phase-3
nicety). The dropdown lists the **distinct configured providers** the user can switch to: one entry
per `(provider_type, model_id)` candidate from the rows, labelled capability-aware:

| `provider_type` | Display label (i18n) | Sub-text | Capability hint |
|---|---|---|---|
| `local` | "Local (Ollama)" | `model_id` | orchestrated loop |
| `api` | "API (Anthropic / OpenAI-compatible)" | `model_id` (+ `base_url` host if set) | orchestrated loop, native tools |
| `cli` | "CLI (claude-agent-sdk)" | `model_id` | delegated agentic loop |

**No provider type or model id is hardcoded** in the component (AC-F17-UI-6 / I6): every label's
*type→display-name* mapping is an i18n key, but the **set of available providers and their
model_ids comes entirely from the `GET /provider/config` response**. The component renders whatever
the backend returns; it never assumes a backend exists.

**Writes — `POST /provider/config`.** Selecting a provider posts a `provider_config` row with
`scope` set by a small **scope sub-toggle** (Vault | Global, default Vault per AC-F17-UI-2),
`vault_id` = current vault when scope=vault, and the chosen `provider_type` + `model_id` (+
`base_url` carried over from the source row). On 201 the store refreshes the list and re-derives the
active row; the change persists across refresh because it lives in Postgres (AC-F17-UI-2). No page
reload; the next ingest/chat reads the new resolved config server-side (AC-F17-UI-5).

> Note: POST always creates a row (no upsert endpoint exists). To avoid unbounded row growth we
> resolve "active = most-recent matching row" on read; a backend upsert/PUT is a fast-follow, not
> M4-blocking. Flagged in Consequences.

**ActivityBar feed (AC-F17-UI-3 / AC-F1-5):** the ActivityBar provider placeholder ("Provider: –")
is replaced by the active provider's display name from `providerStore` (selector-gated). The
Header dropdown and the ActivityBar read the same store key, so they stay in sync without prop
drilling.

### 5. Settings (F14 + F16-rest) — single-column section; localStorage persistence

`SettingsPanel` (center, Settings section) is a single-column form, grouped into cards (dark
GitHub palette). Maps AC-F14-1..5 (UI parts) and AC-F16-i18n/settings.

**Context window (F14, AC-F14-1).** A select with the bounded set
`4K · 8K · 16K · 32K(default) · 64K · 128K · 256K · 512K · 1M`. The chosen value is the
**F14 budget basis**; the panel shows the derived 60/20/5/15 split (history / retrieved / system /
generation) as read-only computed text so the user sees the allocation (AC-F14-3 is a server
concern; the UI displays the split, the server enforces truncation AC-F14-4). **Persistence of the
selected window is twofold:** (a) immediately to `localStorage` (AC-F16-settings-1, client UX), and
(b) to the backend `provider_config.context_window_tokens` column **when that column exists**
(AC-F14-2). That column does **not exist yet** (§7 audit) — its migration is a backend item; until
it lands, the UI persists to localStorage and posts the value on the provider config write as a
no-op-safe extra field guarded by feature-detection. The UI does not block on the backend column.

**Language (F16-i18n, AC-F16-i18n-1).** A IT | EN toggle bound to `i18n.changeLanguage(...)` and
persisted to `localStorage` (`synapse.lang`). Initial language: persisted value if present, else
`navigator.language` (`it`/`it-IT` → Italian, else English) — AC-F16-i18n-1.

**Provider config (F17).** A read-only list of the `provider_config` rows (from `providerStore`)
with the active row marked, plus the same Vault|Global scope toggle the Header dropdown uses. Full
row CRUD beyond "select active" is out of M4-UI scope (backend CRUD already exists).

**Reset (AC-F16-settings-2).** A "Reset settings" action clears the Synapse localStorage keys
(`synapse.lang`, `synapse-panel-layout-v2`, `synapse.settings`, …) and resets the stores to
defaults **without a hard reload** (re-initialize stores in place).

**Persistence mechanism (AC-F16-settings-1).** `settingsStore` is a Zustand store with a thin
`localStorage` read on init and a `subscribe` that writes the persisted slice
(`{ language, contextWindowTokens }`) on change. Panel widths already persist via
`react-resizable-panels` `onLayoutChanged` → `synapse-panel-layout-v2` (ADR-0017 / PanelGroup);
the active provider persists server-side. So the four "user settings" of AC-F16-settings-1 (active
provider, language, panel widths, context window) are each persisted by the appropriate mechanism.

### 6. i18n — react-i18next; `locales/en.json` + `locales/it.json`; all new strings are keys

Add **`i18next`** + **`react-i18next`** + **`i18next-browser-languagedetector`**. Initialize in a
new `frontend/src/i18n/index.ts` imported once from `main.tsx`. Detection order:
`localStorage(synapse.lang) → navigator` (AC-F16-i18n-1); fallback `en`.

Locale files live at `frontend/src/i18n/locales/en.json` and `it.json`, namespaced by area so keys
are discoverable and the "every en key has an it key" vitest test (AC-F16-i18n-2) is trivial:

```jsonc
{
  "nav":      { "pages": "...", "graph": "...", "ingest": "...", "settings": "...", "chat": "...", "chatComingSoon": "..." },
  "ingest":   { "title": "...", "runIngest": "...", "filePathLabel": "...", "filePathHelp": "...",
                "status": { "running": "...", "completed": "...", "failed": "...", "convergedFalse": "..." },
                "pagesCreated": "...", "cost": "...", "startedAt": "...", "error": "...",
                "expandError": "...", "manifest": "...", "viewPage": "...", "empty": "...",
                "toastStarted": "...", "toastError": "..." },
  "provider": { "label": "...", "scope": { "vault": "...", "global": "..." },
                "type": { "local": "...", "api": "...", "cli": "..." } },
  "settings": { "title": "...", "contextWindow": "...", "budgetSplit": "...", "language": "...",
                "providerSection": "...", "reset": "...", "resetConfirm": "..." },
  "common":   { "loading": "...", "retry": "...", "vault": "...", "dataVersion": "...", "close": "..." }
}
```

Rule (AC-F1-NAV-5, AC-F16-i18n-2): **no display string is hardcoded** in any new component — all
go through `t("...")`. NavRail labels/tooltips, all Ingest strings, all Settings strings, and
provider type display names are keys. vitest asserts key parity en↔it. Existing Phase-1 components
(Header, ActivityBar, MainTabs, PreviewPanel, NavTree) are migrated opportunistically for the
strings the screenshots touch; not a blocking rewrite.

### 7. Backend — `GET /ingest/runs` contract + the REQUIRED `ingest_runs` migration

**`GET /ingest/runs` (BE-INGEST-RUNS, AC-BE-IR-1..5).** New FastAPI route + Pydantic models;
appears in regenerated `openapi.json` (D4 zero-drift).

```
GET /ingest/runs?limit=20&offset=0&vault_id=<uuid?>
  200 → {
    items: [
      {
        id: uuid,
        vault_id: uuid,
        status: "running" | "completed" | "failed" | "converged_false",
        provider_type: str,            // "local" | "api" | "cli"
        pages_created: int,
        iterations_used: int,
        total_cost_usd: number,        // serialized at 4dp; UI also formats to 4dp
        started_at: datetime,          // ISO-8601 tz-aware
        completed_at: datetime | null,
        error_message: str | null
      }, ...
    ],
    total: int, limit: int, offset: int
  }
  422 → invalid limit/offset (limit 1..100 default 20; offset ≥0 default 0)
```

- Order: `started_at DESC` (AC-BE-IR-3).
- Params: `limit` int default 20 max 100; `offset` int default 0; `vault_id` uuid optional filter
  (AC-BE-IR-2).
- Response model is a Pydantic `IngestRunResponse` + `IngestRunListResponse{items,total,limit,offset}`
  (AC-BE-IR-1).

**Schema audit — `ingest_runs` is MISSING 3 contract fields → migration `0006` REQUIRED.**

| Contract field | In `models.py` today? | Resolution |
|---|---|---|
| `id`, `vault_id`, `provider_type`, `total_cost_usd`, `started_at` | yes | map directly |
| `iterations_used` | named `max_iter_used` | **expose as `iterations_used`** in the response (alias in Pydantic) — no column rename needed |
| `completed_at` | named `finished_at` | **expose as `completed_at`** in the response (alias) — no column rename needed |
| **`status`** | **MISSING** | **ADD column** `status TEXT NOT NULL DEFAULT 'completed'`. Derivation for existing rows / writers: `running` (in-flight), `completed` (`converged=true`), `converged_false` (`converged=false` and no error), `failed` (error). Cleanest: add an explicit `status` column the orchestrator sets; migration backfills from `converged`/`cost_anomaly` (`converged=true → completed`, else `converged_false`; rows with the new `error_message` set → `failed`). |
| **`pages_created`** | **MISSING** | **ADD column** `pages_created INTEGER NOT NULL DEFAULT 0`. Orchestrator sets it to the number of `WikiPage`s persisted in the run. Migration backfills `0` for historical rows. |
| **`error_message`** | **MISSING** | **ADD column** `error_message TEXT NULL`. Orchestrator sets it on a failed run. |

> Decision: **add three columns** (`status`, `pages_created`, `error_message`) and **alias the two
> renamed fields in the response** (`max_iter_used → iterations_used`, `finished_at → completed_at`).
> Aliasing avoids a column rename that would break the existing orchestrator/audit writers and the
> ER history; the three genuinely-new fields are added because the read endpoint cannot honestly
> synthesize `status`/`pages_created`/`error_message` from existing columns alone (`converged` is
> not the same as `status`, and there is no page-count or error column at all). This is a backend
> item for **backend-engineer**: Alembic `0006_ingest_runs_view_fields.py` (additive, nullable /
> defaulted — backward-compatible), update `IngestRun` model, set the new fields where the
> orchestrator writes runs, regenerate D2 (`make er`) and D4 (`make openapi`). Zero-drift gates apply.

**Out-of-this-ADR backend columns it depends on but does not own:** `provider_config
.context_window_tokens` (AC-F14-2) and `provider_config.timeout_seconds` (AC-F16-timeout-1) are
separate F14 / F16-timeout backend items (their own migration). This ADR's UI feature-detects
`context_window_tokens` and falls back to localStorage until it lands (§5); it does not block on it.

### 8. Invariant confirmation

- **I2 (graph server-side):** GraphViewer/GraphPanel are reused **verbatim**; the rail only mounts
  the same `<GraphPanel/>` in two layouts (Pages center, Graph maximized). No layout/force code is
  added anywhere; the no-client-layout grep (T-NCL-001..022) is unaffected. The Graph section's
  graph container DOM count is unchanged (< 20).
- **I3 (selectors + shallow equality; no per-token/per-frame work):** `activeSection` is a scalar
  key; the rail subscribes to it + its setter only. Section switch swaps the section subtree and
  does not re-compute `selectNodes`/`selectEdges`/`selectStatus`. Provider/settings/ingest live in
  separate stores so their churn never re-renders the graph. Ingest polling appends to a list in
  the ingest store; the rail's badge reads a derived count via a dedicated hook (no graph-store
  coupling). All object/array selectors use `useShallow`. No component subscribes to a whole store.
- **I4 (CodeMirror reserved; virtualization; bounded graph DOM):** **No Milkdown/ProseMirror/
  contentEditable / CodeMirror is added** — Settings and Ingest are plain forms/lists (read-only
  render). The ingest run list is **TanStack-Virtual-virtualized** (≤40 row DOM nodes, AC-F1-IV-2).
  The NavTree (Pages section) remains virtualized (ADR-0017). The graph container DOM stays bounded
  and unchanged.
- **I6 (pluggable provider):** the selector renders providers **only** from `GET /provider/config`;
  no `provider_type`/`model_id` literal appears in any component (type→display-name is an i18n
  label, not a routing value). Switching posts a `provider_config` row; routing stays server-side
  and capability-driven.
- **I7 (bounded loops; cost visible):** `total_cost_usd` is shown on **every** run row at exactly
  4dp (including `$0.0000`); `cost_anomaly` (> $1.00) is surfaced in the run manifest. Ingest
  polling is a bounded `setTimeout` chain that stops when no run is `running` (no runaway loop).
- **I5 (Obsidian compat):** the UI never writes to `wiki/`; `.obsidian` hardening (AC-F16-obsidian-1)
  is a backend-only item and not touched here.

---

## Consequences

**Positive**

- The rail gives the shell its permanent skeleton: Phase 3 registers a `chat` section (flip the
  disabled flag + add a layout case) with no shell rework. Ingest/Settings drop in as section views.
- The Pages section reuses ADR-0017's `<PanelGroup/>` unchanged — zero regression risk to the
  shipped 3-panel shell, the graph, or T-NCL.
- Cost (I7) becomes user-visible before chat exists; the stakeholder can verify the bounded loop is
  working from the Ingest view alone.
- Provider switching is wired before Phase-3 chat, so chat can be tested against real provider
  selection immediately (the PM's stated Phase-2-before-Phase-3 rationale).
- Separate provider/settings/ingest stores keep the graph render path isolated from all of this
  new state (clean I3).
- Visual language matches the llm_wiki research brief (icon rail, activity-queue cards, primary/
  ghost buttons) while staying on the established dark palette — no theme fork.

**Negative / trade-offs (stated explicitly)**

- **`ingest_runs` needs migration `0006` (3 new columns).** Unavoidable: the read contract requires
  `status`, `pages_created`, `error_message` that the table does not have. Mitigated by making the
  migration additive/backward-compatible and aliasing (not renaming) the two differently-named
  fields. **Escalation flag to PM/orchestrator:** AC-BE-IR-1 lists these fields as if they exist;
  they do not — the AC is satisfiable only after `0006` lands.
- **"Run Ingest" is per-file, not per-vault.** `POST /ingest/trigger` takes `{file_path}` by
  contract (ADR-0006). AC-F1-IV-3 says "with the current vault_id". **Escalation flag:** the
  honest UI is a file-path input, not a one-click vault ingest. A whole-vault trigger or a
  `GET /raw/sources` listing endpoint would need its own scope grant; both are out of M4. The F9
  boundary is untouched.
- **Three new deps** (`i18next`, `react-i18next`, `i18next-browser-languagedetector`, all small,
  maintained, React-19 compatible). Accepted — F16-i18n requires a real i18n runtime and AC-F16-i18n-2
  requires externalized locale files.
- **`POST /provider/config` only creates rows** (no upsert), so changing the active provider
  appends rows. Mitigated by resolving "active = most-recent matching" on read. A backend PUT/upsert
  is a fast-follow. Flagged, non-blocking.
- **`context_window_tokens` / `timeout_seconds` columns do not exist yet.** The UI feature-detects
  and persists the window to localStorage until the backend column lands (separate F14 / F16-timeout
  items). Flagged, non-blocking for the UI.
- **`activeTab` becomes vestigial in Phase 2.** Retained (not removed) to avoid a store migration
  and to keep Phase 3's chat-placement decision open. Accepted as a small dead field.

**Follow-ups (NOT this ADR's scope — tracked)**

- Phase 3: register the `chat` rail section (flip disabled, add a section case); per-operation
  provider override; `GET /pages/{id}/content` + GFM render (ADR-0017 reserved).
- Backend fast-follows: `GET /raw/sources` listing (turns the Run-Ingest path input into a picker);
  `PUT /provider/config` upsert; `provider_config.context_window_tokens` + `.timeout_seconds`
  migrations (F14 / F16-timeout).
- D1 `component.mmd`: add NavRail, SectionRouter, IngestView, IngestRunList, IngestRunDetail,
  ProviderSelector, SettingsPanel and the providerStore/settingsStore/ingestStore (tech-writer).
- D3: a short ingest-trigger sequence stub (UI → POST /ingest/trigger → GET /ingest/runs poll)
  may be added by tech-writer; not gating.

---

## Implementation spec (hand to frontend-engineer + backend-engineer)

### Dependencies to add (`frontend/package.json`)
- `i18next`, `react-i18next`, `i18next-browser-languagedetector` (i18n runtime + detection).
- (`@tanstack/react-virtual` and `react-resizable-panels` already present — reuse.)
- No new icon dependency required; use inline lucide-style outline SVGs (or add `lucide-react` if
  the team prefers — small, tree-shakeable; either is acceptable, no new invariant impact).

### New / changed frontend files (all under `frontend/src/`)

| File | Responsibility |
|---|---|
| `components/nav/NavRail.tsx` | **NEW.** ~48px persistent left rail; items Pages/Graph/Ingest(+badge)/Settings + disabled Chat; reads `activeSection`, calls `setActiveSection`; i18n labels; active = soft-tint; reduced-motion-safe. |
| `components/SectionRouter.tsx` | **NEW.** Reads `activeSection`; renders Pages → `<PanelGroup/>`, Graph → full-bleed `<GraphPanel/>`, Ingest → `IngestView`+`IngestRunDetail`, Settings → `SettingsPanel`. |
| `components/AppShell.tsx` | **EDIT.** Layout becomes `Header` / row(`NavRail` + `SectionRouter`) / `ActivityBar`. Replace the direct `<PanelGroup/>` mount with `<NavRail/>` + `<SectionRouter/>`. |
| `components/center/MainTabs.tsx` | **EDIT/RETIRE.** Remove the Graph/Chat tab strip from the Pages center (Graph is now a rail section; Chat is a rail item). Pages center renders `<GraphPanel/>` directly (via `PanelGroup`). Keep the file only if Phase 3 wants a Pages-center tab; otherwise delete. |
| `components/ingest/IngestView.tsx` | **NEW.** Center Ingest view: Run-Ingest control (file-path input → `POST /ingest/trigger`) + toast + `IngestRunList`. |
| `components/ingest/IngestRunList.tsx` | **NEW.** TanStack-Virtual list of run cards (status badge, provider, pages_created, cost 4dp, relative time, error-truncate+expand); select → set selected run; infinite paging. |
| `components/ingest/IngestRunDetail.tsx` | **NEW.** Right pane: selected-run manifest (route, iterations_used/max_iter, total_tokens, converged, cost_anomaly, model_id, View-page link). |
| `components/ingest/StatusBadge.tsx` | **NEW.** Small color-coded badge; pulse on `running`; reduced-motion-safe; i18n label. |
| `components/provider/ProviderSelector.tsx` | **NEW.** Header-slot dropdown; lists providers from `providerStore`; Vault|Global scope sub-toggle; select → `POST /provider/config`; capability-aware labels (i18n). |
| `components/settings/SettingsPanel.tsx` | **NEW.** Context-window select (+ 60/20/5/15 display), language IT|EN toggle, provider-config read-only list, Reset. |
| `components/common/Toast.tsx` | **NEW.** Minimal transient toast (success/error) for Run-Ingest + provider change. |
| `components/Header.tsx` | **EDIT.** Replace the placeholder provider slot with `<ProviderSelector/>`. |
| `components/activity/ActivityBar.tsx` | **EDIT.** Replace "Provider: –" with the active provider display name from `providerStore` (selector-gated); add last-ingest from latest run if available. |
| `store/graphStore.ts` | **EDIT.** Add `activeSection` + `setActiveSection` + `selectActiveSection`/`selectSetActiveSection` (§2). Do NOT remove existing keys. |
| `store/providerStore.ts` | **NEW.** `{ list, active, scope, fetch(), setActive(provider_type, model_id, base_url, scope) }`; selector-gated. |
| `store/settingsStore.ts` | **NEW.** `{ language, contextWindowTokens, set*, reset() }`; localStorage init + subscribe-persist. |
| `store/ingestStore.ts` (or `components/ingest/useIngestRuns.ts`) | **NEW.** runs[], total, loading, selectedRunId, fetch/page, polling (5s while any `running`), `runningCount` derived; `useIngestRunningCount()` for the rail badge. |
| `api/ingestClient.ts` | **NEW.** `fetchIngestRuns({limit,offset,vaultId})` → `IngestRunListResponse`; `triggerIngest({file_path})` → 202 (reuse/extend pagesClient error handling). |
| `api/providerClient.ts` | **NEW.** `fetchProviderConfigs({scope?,vaultId?})` → list; `createProviderConfig(body)` → 201. |
| `api/types.ts` | **EDIT.** Add `IngestRunItem`, `IngestRunListResponse`, `ProviderConfigItem`, `ProviderConfigListResponse`, `Section`. |
| `i18n/index.ts` | **NEW.** i18next init + languagedetector (localStorage→navigator, fallback en). |
| `i18n/locales/en.json`, `i18n/locales/it.json` | **NEW.** Namespaced keys per §6; en↔it parity. |
| `main.tsx` | **EDIT.** `import "./i18n";` once before render. |

### New / changed backend files
| File | Responsibility |
|---|---|
| `backend/app/main.py` | **EDIT.** Add `GET /ingest/runs` route + `IngestRunResponse` / `IngestRunListResponse` Pydantic models (fields per §7; `iterations_used` aliases `max_iter_used`, `completed_at` aliases `finished_at`). |
| `backend/app/models.py` | **EDIT.** `IngestRun`: add `status`, `pages_created`, `error_message`. |
| `backend/alembic/versions/0006_ingest_runs_view_fields.py` | **NEW.** Additive migration: `status TEXT NOT NULL DEFAULT 'completed'`, `pages_created INTEGER NOT NULL DEFAULT 0`, `error_message TEXT NULL`; backfill `status` from `converged` for historical rows. |
| `backend/app/ingest/orchestrator.py` | **EDIT.** Set `status`/`pages_created`/`error_message` when writing an `IngestRun` row (so live runs populate the new fields). |
| `docs/er/schema.mmd`, `docs/api/openapi.json` | **REGEN.** `make er` + `make openapi` (D2/D4 zero-drift). |

### Build order
1. **Backend first** (unblocks the UI): models.py + Alembic `0006` + orchestrator write + `GET
   /ingest/runs` route + Pydantic models; `make er` + `make openapi`; pytest (AC-BE-IR-1..5).
2. `store/graphStore.ts` `activeSection` extension (§2) + vitest (AC-F1-NAV-8).
3. `i18n/index.ts` + locale files + `main.tsx` import; vitest key-parity (AC-F16-i18n-2).
4. `NavRail` + `SectionRouter` + `AppShell` rewire + MainTabs retire; vitest + Playwright
   (AC-F1-NAV-1..7).
5. `api/ingestClient.ts` + `ingestStore`/`useIngestRuns` + `StatusBadge` + `IngestRunList`
   (virtualized) + `IngestRunDetail` + `IngestView` + `Toast`; vitest (AC-F1-IV-7) + Playwright
   (AC-F1-IV-8).
6. `api/providerClient.ts` + `providerStore` + `ProviderSelector` (Header) + ActivityBar feed;
   vitest (AC-F17-UI-5).
7. `settingsStore` + `SettingsPanel` (context window + language + reset); vitest
   (AC-F16-settings-1..2, AC-F14-1).
8. Playwright D5 captures: `docs/screens/shell-provider-selector.png`,
   `docs/screens/shell-ingest-view.png`.

### Acceptance mapping
- AC-F1-NAV-1..8 → §1 (rail, section→panel, persistence, i18n labels) + §2 (scalar `activeSection`,
  no unrelated re-compute).
- AC-F1-IV-1..8 → §3 (virtualized run cards, 4dp cost, relative time, error truncate, Run-Ingest
  toast+refresh, running-only polling, read-only, GET /ingest/runs).
- AC-BE-IR-1..5 → §7 (endpoint + migration + DESC order + 422 validation).
- AC-F17-UI-1..6 → §4 (Header dropdown, GET/POST provider/config, scope toggle, capability labels,
  no reload, no hardcoded ids, ActivityBar feed).
- AC-F14-1..3 (UI) → §5 (context-window select + 60/20/5/15 display; enforcement is server-side).
- AC-F16-i18n-1..2 → §6; AC-F16-settings-1..2 → §5; AC-F16-obsidian-1 / AC-F16-timeout-1 / AC-F16-gfm-1
  are backend/Phase-3 items, not this ADR.

### Do NOT
- Add Milkdown / ProseMirror / contentEditable / CodeMirror anywhere (I4 — Settings/Ingest are
  plain forms/lists; CodeMirror stays reserved for the future editor).
- Add any F9 action (approve / reject / Skip / Create Page / Deep-Research / query list) to the
  Ingest view (§0 gate — that is M5).
- Hardcode any `provider_type` or `model_id` in a component (I6 — read from `GET /provider/config`).
- Modify GraphViewer internals or add any layout/force code (I2 — reuse `<GraphPanel/>` verbatim).
- Render a non-virtualized run list > 40 DOM rows (I4); subscribe to a whole store (I3); put
  ingest/provider/settings state in `graphStore` (keep them in separate stores so the graph never
  re-renders on their change).
- Hide `total_cost_usd` or show it at < 4dp (I7).
