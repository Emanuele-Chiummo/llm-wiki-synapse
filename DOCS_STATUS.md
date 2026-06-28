# DOCS_STATUS — Sprint v0.4 / M4 Documentation Gate

> Tech-writer sign-off. Phases appended chronologically; most recent phase at top.

## Phase 3 (Chat — F6/F7/F8 + G3) — DOCS GATE: PASS

- **D1** `docs/architecture/component.mmd` — UPDATED: chat backend module (context/think/stream) + `/chat/stream` + `/conversations*` routes; frontend ChatSection/ConversationList/MessageList/StreamingMessage/MarkdownView/ThinkBlock/MessageInput + chatStore/chatClient/useChatStream/latexToUnicode (I3/I4/G3 annotated); OllamaProvider/ApiProvider `chat()` implemented; NavRail Chat enabled.
- **D2** `docs/er/schema.mmd` — zero drift: migration 0007 `conversations` + `messages` (per-message input/output tokens + total_cost_usd, I7) match models.py.
- **D4** `docs/api/openapi.json` — zero drift: `/chat/stream`, `/conversations`, `/conversations/{id}`, `/conversations/{id}/messages` present.
- **D5** `docs/screens/` — chat-streaming.png + chat-conversation.png committed (chat-think-block.png deferred — qwen2.5:3b emits no `<think>`).
- **ADR-0019** indexed in `docs/adr/README.md` (Accepted).
- **Deferred to M5**: F5 4-phase retrieval + `[n]` citations, save-to-wiki (button disabled "coming in M5"), CliAgentProvider.chat().


> Generated: 2026-06-28
> Author: tech-writer (claude-sonnet-4-6)
> Sprint branch: sprint/v0.3 (v0.4 Phase 1 + Phase 2 work)
> I8 gate: CLAUDE.md §3 invariant I8 (docs-as-DoD; ER matches live schema; OpenAPI matches
>   live FastAPI)

---

## Phase 2 section — M4 (NavRail + Ingest + Provider + Settings + i18n, ADR-0018)

> Gate run: 2026-06-28
> Phase scope: F1-NAV (NavRail/SectionRouter), F1-INGEST-VIEW (IngestView/IngestRunList/IngestRunDetail),
>   F17-UI (ProviderSelector), F14+F16 (SettingsPanel, i18n/react-i18next),
>   providerStore + settingsStore + ingestStore, Toast;
>   backend: migration 0006 (ingest_runs.status/pages_created/error_message), GET /ingest/runs.
>   ADR-0018 Accepted.

### Per-artifact status

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/component.mmd` | UP-TO-DATE | Updated this gate run. See §Phase-2-D1. |
| D2 | `docs/er/schema.mmd` | UP-TO-DATE (verified) | Migration 0006 columns confirmed present; zero drift. See §Phase-2-D2. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (verified) | GET /ingest/runs + IngestRunResponse/IngestRunListResponse confirmed; live diff = empty. See §Phase-2-D4. |
| D5 | `docs/screens/ingest-section.png` | UP-TO-DATE | Committed by QA (2026-06-28 21:12). See §Phase-2-D5. |
| D5 | `docs/screens/navrail-graph-active.png` | UP-TO-DATE | Committed by QA (2026-06-28 21:13). See §Phase-2-D5. |
| D5 | `docs/screens/provider-selector-open.png` | UP-TO-DATE | Committed by QA (2026-06-28 21:12). See §Phase-2-D5. |
| D5 | `docs/screens/settings-section.png` | UP-TO-DATE | Committed by QA (2026-06-28 21:12). See §Phase-2-D5. |
| D7 | `docs/adr/README.md` (ADR-0018 row) | UP-TO-DATE | ADR-0018 row present (architect added it). See §Phase-2-D7. |

### §Phase-2-D1 — D1 component diagram updated

File: `docs/architecture/component.mmd`

**Drift before this run:** the Phase 1 diagram did not include NavRail, SectionRouter,
IngestView, IngestRunList, IngestRunDetail, ProviderSelector, SettingsPanel, Toast,
the i18n module, providerStore, settingsStore, ingestStore, ingestClient, or
providerClient. The REST component description did not mention `GET /ingest/runs`. The
Postgres component description did not mention migration 0006. The ActivityBar description
still showed the Phase-1 placeholder '—' for the provider label.

**Fix applied:** complete Phase 2 update to `component.mmd`. Specific changes:

- Header comment version note appended: "v0.4 Phase 2 (ADR-0018): NavRail / SectionRouter / IngestView + IngestRunList + IngestRunDetail / ProviderSelector / SettingsPanel / Toast / i18n module / providerStore + settingsStore + ingestStore — migration 0006."
- Title updated: "Synapse v0.4 Phase 2 (M4 — F1 shell + F17-UI + F14/F16)".
- Frontend boundary label updated to include ADR-0017 and ADR-0018.
- REST component description: added `GET /ingest/runs (ADR-0018 §7)`.
- IngestOrchestrator: added "Sets status/pages_created/error_message on ingest_runs rows (migration 0006)."
- Postgres: added `+status/pages_created/error_message migration 0006` to ingest_runs note.
- AppShell: updated to describe Phase 2 layout (NavRail + SectionRouter row; ToastHost).
- Header: updated to show ProviderSelector wired in Phase 2.
- ActivityBar: updated to show reads selectActiveProvider from providerStore (Phase 2 filled).

New components added (all under `frontend/src/`):

| Component ID | File | Key invariant |
|---|---|---|
| `navrail` | nav/NavRail.tsx | ~48px icon rail; activeSection from graphStore; badge from ingestStore (I3) |
| `sectionrouter` | SectionRouter.tsx | Reads activeSection (scalar); keyed switch to 4 section layouts (I3) |
| `ingestview` | ingest/IngestView.tsx | Center of Ingest section; POST /ingest/trigger; polling (I4/I7) |
| `ingestrunlist` | ingest/IngestRunList.tsx | TanStack Virtual ≤40 DOM rows; cost at 4dp (I4/I7) |
| `ingestrundetail` | ingest/IngestRunDetail.tsx | Right pane; full run manifest incl. cost_anomaly (I7) |
| `providerselector` | provider/ProviderSelector.tsx | Header slot; GET+POST /provider/config; zero hardcoded IDs (I6) |
| `settingspanel` | settings/SettingsPanel.tsx | Context window (F14) + IT/EN (F16); reset |
| `toast` | common/Toast.tsx | Singleton; mounted once in AppShell; showToast() from anywhere |
| `i18nmod` | i18n/index.ts + locales/*.json | react-i18next; key parity test enforced |
| `providerstore` | store/providerStore.ts | SEPARATE from graphStore (I3) |
| `settingsstore` | store/settingsStore.ts | SEPARATE from graphStore (I3); localStorage |
| `ingeststore` | store/ingestStore.ts | SEPARATE from graphStore (I3); 5s polling chain |
| `ingestclient` | api/ingestClient.ts | GET /ingest/runs + POST /ingest/trigger |
| `providerclient` | api/providerClient.ts | GET + POST /provider/config |

New relations added: 22 `Rel()` entries for Phase 2 wiring including:
- NavRail → graphStore (activeSection/setActiveSection) and → ingestStore (badge)
- Header → ProviderSelector (F17 slot wired)
- SectionRouter → all 4 section views keyed by activeSection
- IngestView → ingestStore → ingestClient → REST (GET /ingest/runs migration 0006)
- SettingsPanel → settingsStore + providerStore
- ActivityBar → providerStore (selectActiveProvider, Phase 2 filled)
- i18nmod → NavRail, IngestView, ProviderSelector, SettingsPanel

**I3 separation confirmed in diagram:** providerStore, settingsStore, and ingestStore are
explicitly described as "SEPARATE from graphStore" in their component descriptions, ensuring
the diagram documents that provider/settings/ingest changes cannot cause the graph to re-render.

**I6 confirmed in diagram:** ProviderSelector description states "INVARIANT I6: zero hardcoded
provider_type/model_id literals; all values from GET /provider/config." i18nmod description
notes t() for capability labels with "no hardcoded provider names — I6."

**GraphPanel unchanged:** the `graphpanel` component description retains T-NCL-001..022 intact
notation. The `viewer` component is unchanged from v0.3 per I2.

### §Phase-2-D2 — ER diagram zero-drift verification

File: `docs/er/schema.mmd`

**Pre-verification state:** the ER diagram header already reads
`<!-- Generated: v0.4 M4 Phase 2 | 2026-06-28 — ADR-0018 §7: ingest_runs view fields (status/pages_created/error_message) -->`,
indicating the backend engineer regenerated it when committing migration 0006 and models.py changes.

**Verification method:** cross-checked every column in `docs/er/schema.mmd` INGEST_RUNS
against `backend/app/models.py` `IngestRun` class and `backend/alembic/versions/0006_ingest_runs_view_fields.py`.

| Column | Present in ER | Type in ER | models.py type | migration 0006 adds it | Accurate |
|--------|---------------|-----------|----------------|------------------------|---------|
| `status` | YES | `string` | `Text` NOT NULL default 'completed' | YES | YES |
| `pages_created` | YES | `int` | `Integer` NOT NULL default 0 | YES | YES |
| `error_message` | YES | `string` | `Text` nullable | YES | YES |
| `max_iter_used` | YES (aliased) | `int` | `Integer` | pre-existing | YES — alias comment present |
| `finished_at` | YES (aliased) | `timestamptz` | `TIMESTAMP(timezone=True)` | pre-existing | YES — alias comment present |

Migration 0006 file (`0006_ingest_runs_view_fields.py`) confirmed: adds `status`, `pages_created`,
`error_message` with correct types/defaults and a backfill UPDATE for historical rows.

**Result: zero drift. D2 is current with models.py and migration 0006. No regen required.**

### §Phase-2-D4 — OpenAPI zero-drift verification

File: `docs/api/openapi.json`

**Verification method:** ran `curl http://localhost:8000/openapi.json` and diffed the
JSON-normalised output against the committed file (sort_keys=True). The diff was empty.

**Key fields confirmed present in committed openapi.json:**

| Check | Present | Detail |
|-------|---------|--------|
| `GET /ingest/runs` path | YES | operationId: `list_ingest_runs_ingest_runs_get` |
| `GET /ingest/runs` description | YES | References I7 cost ledger, AC-BE-IR-1..5, ADR-0018 §7; documents limit/offset/vault_id params; column aliases |
| `IngestRunListResponse` schema | YES | `items: [IngestRunResponse]`, `total: int`, description references ADR-0018 §7 |
| `IngestRunResponse` schema | YES | Fields: `id, vault_id, status, pages_created, error_message, iterations_used (alias), completed_at (alias), started_at, total_cost_usd, provider_type` |
| `status` field in IngestRunResponse | YES | |
| `pages_created` field in IngestRunResponse | YES | |
| `error_message` field in IngestRunResponse | YES | |
| `iterations_used` field (alias for max_iter_used) | YES | |
| `completed_at` field (alias for finished_at) | YES | |
| Committed == live API (full diff) | ZERO DIFF | Exact match on all paths + schemas |

**Result: zero drift. D4 is current with the live FastAPI app. No regen required.**

### §Phase-2-D5 — Screenshots verification

`docs/screens/` current contents (as of 2026-06-28 21:12–21:13):

| File | Committed | Captures |
|------|-----------|---------|
| `ingest-section.png` | YES (21:12) | Ingest section: IngestView + IngestRunDetail pane |
| `navrail-graph-active.png` | YES (21:13) | NavRail visible + Graph section active (full-bleed GraphPanel) |
| `provider-selector-open.png` | YES (21:12) | ProviderSelector dropdown expanded in Header |
| `settings-section.png` | YES (21:12) | Settings section: SettingsPanel (context window + language + providers) |
| `shell-3panel.png` | YES (21:12) | 3-panel layout — Pages section active (carried from Phase 1) |
| `shell-3panel-selected.png` | YES (21:12) | 3-panel with node selected (carried from Phase 1) |
| `graph-obsidian.png` | YES (19:03) | Graph view (carried from Phase 0) |
| `graph-obsidian-node-selected.png` | YES (19:03) | Graph with node selected (carried from Phase 0) |

Note on filenames: the QA engineer used `ingest-section.png` and `settings-section.png`
(rather than `shell-navrail-ingest.png` / `shell-settings.png`). The filenames are
descriptive and unambiguous; no rename needed.

All 4 Phase 2 views are captured. All 4 Phase 1 views are captured. All Phase 0 views remain valid.

**D5 is fully current for Phase 2.**

### §Phase-2-D7 — ADR index verification

File: `docs/adr/README.md`

ADR-0018 row is present (added by solution-architect before Phase 2 coding began).

| Field | Value | Correct |
|-------|-------|---------|
| ADR number | 0018 | YES |
| Title | "NavRail IA, Ingest Activity View, Provider Selector, Settings, i18n (M4 Phase 2)" | YES |
| Status | Accepted | YES |
| Date | 2026-06-28 | YES |
| Sprint | v0.4 | YES |
| Link | `0018-navrail-ingest-provider.md` | YES — file exists at `docs/adr/0018-navrail-ingest-provider.md` |

Index header reads: `Last updated: 2026-06-28 · Sprint v0.4 (M4 Phase 2)` — correct.
Total ADRs in index: 18 (0001–0018). All Accepted. Zero gaps.

### DOCS GATE VERDICT — M4 Phase 2

| Artifact | Status | Drift found | Detail |
|----------|--------|-------------|--------|
| D1 `docs/architecture/component.mmd` | UP-TO-DATE | YES — fixed this run | All Phase 2 components (NavRail, SectionRouter, IngestView/List/Detail, ProviderSelector, SettingsPanel, Toast, i18n, providerStore, settingsStore, ingestStore, ingestClient, providerClient) and 22 new relations added; migration 0006 noted; I3/I6 separation explicit |
| D2 `docs/er/schema.mmd` | UP-TO-DATE | NONE | Backend engineer regenerated; status/pages_created/error_message confirmed present; cross-checked vs models.py and migration 0006; zero drift |
| D4 `docs/api/openapi.json` | UP-TO-DATE | NONE | GET /ingest/runs confirmed; IngestRunResponse/ListResponse confirmed; live diff = empty; backend regenerated on Phase 2 completion |
| D5 `docs/screens/` (Phase 2 captures) | UP-TO-DATE | NONE | 4 new screenshots committed by QA: ingest-section.png, navrail-graph-active.png, provider-selector-open.png, settings-section.png |
| D7 ADR-0018 row in `docs/adr/README.md` | UP-TO-DATE | NONE | Row present; 18 ADRs listed; header timestamps correct |

**DOCS GATE: PASS**

All required Phase 2 D-artifacts are UP-TO-DATE. The only drift found was in D1 (component
diagram had not yet been updated for Phase 2 components); this was fixed in this gate run.
D2, D4, D5, and D7 required no changes.

Drift found and fixed in this run:
- D1: `component.mmd` was at Phase 1 level; updated to reflect all Phase 2 components and relations (ADR-0018).

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-28 | M4 Phase 2**

---

---

## Phase 1 section — M4 (F1 three-panel shell, ADR-0017)

### Per-artifact status

| ID | Artifact | Required Phase 1? | Status | Notes |
|----|----------|-------------------|--------|-------|
| D1 | `docs/architecture/component.mmd` | YES | UP-TO-DATE | Updated this gate run. See §Phase-1-D1. |
| D5 | `docs/screens/shell-3panel.png` | YES | PENDING QA | QA agent captures via Playwright. Not yet committed. See §Phase-1-D5. |
| D5 | `docs/screens/shell-3panel-selected.png` | YES | PENDING QA | QA agent captures via Playwright. Not yet committed. See §Phase-1-D5. |
| D7 | `docs/adr/README.md` (ADR-0017 row) | YES | UP-TO-DATE | ADR-0017 row is present (architect added it). See §Phase-1-D7. |
| D2 | `docs/er/schema.mmd` | NO (no schema change) | CARRY-FORWARD | F1 is a pure-frontend shell. No new migration, no new models.py column. ER remains valid from Phase 0 gate. |
| D4 | `docs/api/openapi.json` | NO (no API change) | CARRY-FORWARD | F1 adds no new backend endpoints. `GET /pages` was already present. OpenAPI remains valid from Phase 0 gate. |

### §Phase-1-D1 — D1 component diagram updated

File: `docs/architecture/component.mmd`

**Drift before this run:** the committed component diagram reflected v0.3 (thin sigma viewer
only). The F1 shell components (AppShell, Header, PanelGroup, NavTree, MainTabs, GraphPanel,
PreviewPanel, ActivityBar, ScenarioTemplates, pagesClient, graphStore UI slice) and their
relations were absent.

**Fix applied:** updated the diagram in this gate run. Changes made:
- Header comment bumped to `v0.4 sprint 4 | 2026-06-28`.
- Title updated to "Synapse v0.4 Phase 1 (M4 — F1 shell)".
- Frontend boundary label updated to "Frontend — 3-panel shell (v0.4 Phase 1, F1, ADR-0017)".
- Added 11 new components inside the frontend boundary (see list below).
- Added 18 new `Rel()` entries for Phase 1 shell wiring.
- Existing GraphViewer and graphStore components retained; GraphViewer description updated to
  note it is UNCHANGED and that T-NCL-001..022 remain intact.

New components added (all under `frontend/src/`):

| Component ID | File | Key invariant noted |
|---|---|---|
| `appshell` | AppShell.tsx | Top-level layout; replaces App.tsx body |
| `header` | Header.tsx | providerSelectorSlot placeholder (Phase 2 seam) |
| `panelgroup` | panels/PanelGroup.tsx | react-resizable-panels; no rAF loop (AC-F1-7) |
| `navtree` | nav/NavTree.tsx + useNavTreeData.ts | TanStack Virtual; ≤50 DOM rows (I4, AC-F1-2) |
| `maintabs` | center/MainTabs.tsx | Chat tab aria-disabled Phase-3 seam |
| `graphpanel` | center/GraphPanel.tsx | Wraps GraphViewer UNCHANGED; T-NCL intact (I2) |
| `previewpanel` | preview/PreviewPanel.tsx | Read-only inspector; NOT an editor (I4, AC-F1-3) |
| `activitybar` | activity/ActivityBar.tsx | Phase-1 provider placeholder '—' (AC-F1-5) |
| `scenariotemplates` | common/ScenarioTemplates.tsx | ≥2 templates; chat-store wiring in Phase 3 (AC-F1-6) |
| `gstore` (updated) | store/graphStore.ts | UI slice added; selectedNodeId unchanged (I3) |
| `pagesclient` | api/pagesClient.ts | GET /pages metadata only; separate from graph client |

**GraphPanel→GraphViewer wrapping:** the diagram explicitly shows `graphpanel` as a thin wrapper
over the unchanged `viewer` component. The `Rel(graphpanel, viewer, "wraps unchanged GraphViewer
(I2, no layout code)")` entry makes the I2 contract visible at the diagram level.

**Shared selection key (I3):** `navtree`, `viewer`, and `previewpanel` all connect to `gstore`
via selectors (`selectPage`, `setSelectedNodeId`, `selectSelectedNodeId`). The single shared key
(`selectedNodeId`) is documented in the gstore description. No cross-store wiring.

**Zero drift vs ADR-0017 component table (§6):** every component in the ADR implementation
spec has a corresponding node in the updated diagram. Phase-3 seams (chat tab, content endpoint,
CodeMirror editor) are noted as stubs/reserved in the component descriptions.

**Confirmed: D2/D4 need no regen.** The F1 shell introduces no new Postgres columns and no new
API endpoints. `GET /pages` was already present in openapi.json with its current schema. The
ER diagram and openapi.json committed from Phase 0 remain authoritative.

### §Phase-1-D5 — Screenshots (QA agent responsibility)

Expected captures (Playwright, QA agent):

| File | View | Status |
|------|------|--------|
| `docs/screens/shell-3panel.png` | 3-panel shell, no selection, all panels visible | PENDING QA |
| `docs/screens/shell-3panel-selected.png` | 3-panel shell, node selected — NavTree row highlighted, PreviewPanel populated | PENDING QA |

Current state of `docs/screens/`: `graph-obsidian.png` and `graph-obsidian-node-selected.png`
are present (committed Jun 28 18:53/19:03, Phase 0 gate). `shell-3panel.png` and
`shell-3panel-selected.png` are NOT YET COMMITTED. This is expected: the QA agent runs
Playwright against the live stack after the frontend-engineer lands the shell code. Tech-writer
does not capture D5.

The two Phase 0 screens (`graph-obsidian.png`, `graph-obsidian-node-selected.png`) remain
valid references for the graph view (GraphViewer unchanged in Phase 1).

### §Phase-1-D7 — ADR index verification

File: `docs/adr/README.md`

ADR-0017 row was present at the time of this gate run (added by solution-architect).

| Field | Value | Correct |
|-------|-------|---------|
| ADR number | 0017 | YES |
| Title | "Three-panel shell: layout, resizing, shared selection model (F1)" | YES |
| Status | Accepted | YES |
| Date | 2026-06-28 | YES |
| Sprint | v0.4 | YES |
| Link | `0017-three-panel-shell.md` | YES — file exists |
| Summary | NavTree / tabbed main (GraphViewer wrapped, chat stub) / PreviewPanel; react-resizable-panels; single selectedNodeId key in graphStore UI slice; TanStack Virtual | YES — accurate |

Total ADRs in index: 17 (0001–0017). All Accepted. Zero gaps.

No update to the index was required. The header timestamp already reads
`Last updated: 2026-06-28 · Sprint v0.4 (M4 Phase 1)` — consistent with this phase.

### §Phase-1-D2D4-confirm — ER and OpenAPI carry-forward confirmation

**D2 carry-forward:** no new Alembic migration was added in Phase 1. `models.py` is unchanged.
The last migration is 0005 (`pages.pinned`). The ER diagram at `docs/er/schema.mmd` was
regenerated and verified at the Phase 0 gate and remains authoritative. No regen required.

**D4 carry-forward:** the Phase 1 shell uses `GET /pages` (already documented in openapi.json
with `PageListResponse`/`PageListItem` schemas as extended in `api/types.ts`) and
`GET /pages/{id}` (already documented). No new routes, no schema changes. The openapi.json
committed at Phase 0 gate remains authoritative. No regen required.

### DOCS GATE VERDICT — M4 Phase 1

| Artifact | Status | Detail |
|----------|--------|--------|
| D1 `docs/architecture/component.mmd` | UP-TO-DATE | Updated this gate run; all F1 shell components and relations present; ADR-0017 §6 component table fully reflected; GraphPanel→GraphViewer wrapping explicit; I2/I3/I4 invariant annotations present |
| D5 `docs/screens/shell-3panel.png` | PENDING QA | QA agent Playwright capture required; not yet committed |
| D5 `docs/screens/shell-3panel-selected.png` | PENDING QA | QA agent Playwright capture required; not yet committed |
| D7 ADR-0017 row in `docs/adr/README.md` | UP-TO-DATE | Row present (architect added it); 17 ADRs listed, zero gaps |
| D2 `docs/er/schema.mmd` | CARRY-FORWARD (no change) | No schema change in Phase 1; Phase 0 gate ER remains valid |
| D4 `docs/api/openapi.json` | CARRY-FORWARD (no change) | No new endpoints in Phase 1; Phase 0 gate OpenAPI remains valid |

**DOCS GATE: PASS**

All required Phase 1 D-artifacts are UP-TO-DATE. D5 (two shell screenshots) is a QA-agent
Playwright responsibility and is explicitly tracked as pending — consistent with established
precedent (Phase 0 gate §2, v0.3 gate). It does not block this gate.

No D2/D4 regen was required: F1 is a pure-frontend shell with no database schema changes and
no new API routes.

Drift found and fixed in this run:
- D1: `component.mmd` was at v0.3; updated to reflect all F1 shell components (ADR-0017 §6).

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-28 | M4 Phase 1**

---

## M4-GUX Phase 0 section (carried forward)

> Original Phase 0 gate signed 2026-06-28. All verdicts below remain valid.
> Phase scope: GraphUX work — ADR-0016 (structural edges, per-edge kind), Feature A (node
>   pinning: pages.pinned + PATCH /pages/{id}/position), sigma.js viewer UX updates

---

## 1. Per-artifact status table

| ID | Artifact | Required M4-GUX P0? | Status | Drift found | Action taken | Notes |
|----|----------|---------------------|--------|-------------|--------------|-------|
| D2 | `docs/er/schema.mmd` | YES | UP-TO-DATE | DRIFT FOUND — FIXED | Regenerated via `make er` | See §3 for detail. |
| D4 | `docs/api/openapi.json` | YES | UP-TO-DATE | DRIFT FOUND — FIXED | Regenerated via `make openapi` | See §4 for detail. |
| D7 | `docs/adr/README.md` (ADR-0016 index row) | YES | UP-TO-DATE | ZERO DRIFT (row already present) | Header timestamp updated | See §5 for detail. |
| D5 | `docs/screens/graph-obsidian.png` | REFERENCE ONLY | COMMITTED | N/A | Committed by QA agent (Jun 28 19:03) | `graph-obsidian.png` and `graph-obsidian-node-selected.png` both present in `docs/screens/`. |
| D1 | `docs/architecture/component.mmd` | NO (v0.4 update deferred) | RESOLVED IN PHASE 1 | — | Updated in Phase 1 gate (this file §Phase-1-D1) | M3 version carried forward through Phase 0. Updated for F1 shell in Phase 1 gate run. |
| D3 | `docs/sequences/` | NO (Phase 0 scope) | CARRY-FORWARD | — | — | graph-recompute.mmd from M3 remains valid. ADR-0016 edge-filter change is an engine-internal detail; sequence is unchanged. |
| D6a | `docs/USER.md` | NO (v0.4) | N/A | — | — | Not in Phase 0 scope. |
| D6b | `docs/DEPLOY.md` | NO (v0.4) | N/A | — | — | Not in Phase 0 scope. |

---

## 2. D5 screenshot reference — QA agent responsibility

The `docs/screens/graph-obsidian.png` screenshot is captured by the QA/test-engineer agent
running Playwright against the live stack. Tech-writer does NOT capture D5. This is the
established precedent (v0.3 DOCS_STATUS §2: "D5 capture DEFERRED-TO-LIVE").

Expected capture: `docs/screens/graph-obsidian.png` — graph viewer after ADR-0016 structural
edge filter, showing Obsidian-style topology (no hairball, nodes sized by structural degree,
edges styled by kind).

Status as of Phase 1 gate update: `docs/screens/graph-obsidian.png` and
`docs/screens/graph-obsidian-node-selected.png` are both committed (Jun 28 19:03). The
Phase 0 D5 capture is now complete. Phase 1 shell screenshots (`shell-3panel.png`,
`shell-3panel-selected.png`) are tracked as PENDING QA in the Phase 1 section above.

---

## 3. D2 ER diagram — drift found and fixed

### Drift description (pre-fix)

The committed `docs/er/schema.mmd` was generated at v0.3 / M3 and was missing two columns
added in migrations 0004 and 0005:

| Column | Table | Migration | Status before fix |
|--------|-------|-----------|-------------------|
| `edges.kind` | EDGES | 0004 (2026-06-28) | ABSENT from ER |
| `pages.pinned` | PAGES | 0005 (2026-06-28) | ABSENT from ER |

Additionally, the header comment read `<!-- Generated: v0.3 sprint 3 | 2026-06-28 -->`,
not reflecting the M4-GUX transition.

### Fix applied

Ran `/Users/emanuelechiummo/Desktop/LLM Wiki Project/.venv/bin/python backend/scripts/generate_er.py`
which introspects live SQLAlchemy models (`backend/app/models.py`) and regenerates
`docs/er/schema.mmd` from the authoritative source. Output confirmed by generator sanity check:
"all 6 tables present (PAGES, VAULT_STATE, PROVIDER_CONFIG, INGEST_RUNS, LINKS, EDGES)".

Header comment in generated file updated to:
`<!-- Generated: v0.3→v0.4 transition | 2026-06-28 — ADR-0016: edges.kind; Feature A: pages.pinned -->`

`backend/scripts/generate_er.py` line 69 updated to emit this header on future runs.

### Post-fix verification

| Table | Column | Present | Type | Comment accurate |
|-------|--------|---------|------|-----------------|
| PAGES | `pinned` | YES | boolean | "True when user manually positioned this node via PATCH /pages/{id}/position; preserved across FR recomputes (Feature A)." |
| EDGES | `kind` | YES | string | "Structural discriminator: link (wikilink) or source (provenance). ADR-0016 §4. NULL = link for pre-0004 rows." |

All 6 tables present. pages.x/y retained. Relationships (EDGES FK → PAGES) consistent with
models.py. **Zero drift vs models.py after fix.**

---

## 4. D4 OpenAPI — drift found and fixed

### Drift description (pre-fix)

The committed `docs/api/openapi.json` was generated at v0.3 / M3 and was missing the
M4-GUX additions:

| Missing element | Type | ADR/Feature reference |
|-----------------|------|----------------------|
| `PATCH /pages/{page_id}/position` path | New endpoint | Feature A — node pin/drag |
| `PatchPositionRequest` schema | New schema | Feature A |
| `PatchPositionResponse` schema (id, x, y, pinned) | New schema | Feature A |
| `GraphEdgeResponse.kind` field | New field | ADR-0016 §4 |
| `GraphEdgeResponse` description update | Doc update | ADR-0016 §4 |
| `GraphNodeResponse.size` description | Doc update | ADR-0016 §2 (sqrt formula) |
| `GraphNodeResponse.degree` description | Doc update | ADR-0016 §2/§4 (structural degree) |
| `GraphResponse` example `edges[0].kind` | Example update | ADR-0016 §4 |

### Fix applied

Ran `/Users/emanuelechiummo/Desktop/LLM Wiki Project/.venv/bin/python backend/scripts/generate_openapi.py`
which imports `backend/app/main.py` (FastAPI app) and regenerates `docs/api/openapi.json`.
Output confirmed by generator sanity check:
"all 5 required endpoints present (including GET /graph)".

Post-generation comparison against live API (`curl http://localhost:8000/openapi.json`) showed
exact schema match: identical paths, identical component schemas, identical `kind` field
definition in `GraphEdgeResponse`.

### Post-fix verification

| Check | Result |
|-------|--------|
| `PATCH /pages/{page_id}/position` path present | YES |
| `PatchPositionRequest` schema: required x, y | YES |
| `PatchPositionResponse` schema: id, x, y, pinned (all required) | YES |
| `GraphEdgeResponse.kind` field present | YES — type: string, default: "link", description references ADR-0016 §4 |
| `GraphEdgeResponse` description references ADR-0016 §4 | YES |
| `GraphNodeResponse.size` description: "BASE + GROWTH·sqrt(structural_degree)" | YES |
| `GraphNodeResponse.degree` description: "Structural degree…drives size (ADR-0016 §2/§4)" | YES |
| `GraphResponse` example edges include `"kind": "link"` | YES |
| Committed file == live API (`/openapi.json`): path set identical | YES — 8 paths, zero diff |
| Committed file == live API: schema set identical | YES — 15 schemas, zero diff |
| `info.version` | "0.3.0" (not yet bumped to 0.4.0; backend-engineer owns version bump) |

**Zero drift vs live FastAPI app after fix.**

---

## 5. D7 ADR index — ADR-0016 verification

File: `docs/adr/README.md`

### Pre-fix state

ADR-0016 row was already present in the index (authored by solution-architect). The header
line read `Last updated: 2026-06-28 · Sprint v0.3`, which did not reflect the M4-GUX transition.

### Fix applied

Updated header to: `Last updated: 2026-06-28 · Sprint v0.3→v0.4 (M4-GUX Phase 0)`

Updated narrative paragraph to include ADR-0016 description.

### ADR-0016 index row verification

| Field | Value | Correct |
|-------|-------|---------|
| ADR number | 0016 | YES |
| Title | "Obsidian-style graph: structural edges, real-connection sizing, type-as-modulator (F4)" | YES |
| Status | Accepted | YES |
| Date | 2026-06-28 | YES |
| Sprint | v0.3→v0.4 | YES |
| Link | `0016-obsidian-graph-rendering.md` | YES — file exists at `docs/adr/0016-obsidian-graph-rendering.md` |
| Summary | Structural-only edges, ADR-0012 superseded §3, sqrt sizing, per-edge kind | YES — accurate |

### ADR-0016 content verification (spot-check)

| Section | Present | Content accurate |
|---------|---------|-----------------|
| Context | YES | Describes hairball defect; same-type clique math; user goal |
| Decision §1 | YES | Structural edges = direct link OR shared source; AA/same-type = modulators |
| Decision §2 | YES | size = BASE + GROWTH·sqrt(structural_degree); BASE=1.0, GROWTH=1.0 |
| Decision §3 | YES | FR layout fed structural edge set with modulated weights |
| Decision §4 | YES | Per-edge `kind` ("link"|"source"); `degree` = structural_degree |
| Decision §5 | YES | Exact change list for backend-engineer (engine.py + main.py) |
| Decision §6 | YES | ADR-0012 reconciliation: §3 superseded, §1/§2 weight formula retained |
| Consequences | YES | Lists +/- outcomes including D5 screenshot regeneration note |

ADR-0016 file is consistent with models.py (edges.kind column added in migration 0004),
with openapi.json (GraphEdgeResponse.kind field), and with the ER diagram (edges.kind row).

**Total ADRs in index: 16 (0001–0016). All Accepted. Zero gaps.**

---

## 6. Cross-consistency sweep (M4-GUX Phase 0)

| Check | Result |
|-------|--------|
| `pages.pinned` in ER matches `models.py` `Page.pinned` (Boolean, NOT NULL, server_default false, migration 0005) | PASS |
| `edges.kind` in ER matches `models.py` `Edge.kind` (String, nullable, migration 0004) | PASS |
| `PATCH /pages/{page_id}/position` in openapi.json matches live backend (curl confirms 200 schema) | PASS |
| `GraphEdgeResponse.kind` in openapi.json matches ADR-0016 §4 ("link"\|"source" discriminator) | PASS |
| `GraphNodeResponse.size` description (sqrt curve) matches ADR-0016 §2 formula | PASS |
| `GraphNodeResponse.degree` description (structural degree) matches ADR-0016 §2/§4 | PASS |
| ADR-0016 edge inclusion rule (structural gate) consistent with ADR-0012 reconciliation note in ADR-0016 §6 | PASS |
| ADR-0012 §3 superseded status documented in ADR-0016 §6 and README summary | PASS |
| `docs/adr/0016-obsidian-graph-rendering.md` exists and is non-empty | PASS |
| ER header comment updated to reflect M4-GUX transition | PASS — "v0.3→v0.4 transition | 2026-06-28 — ADR-0016: edges.kind; Feature A: pages.pinned" |
| generate_er.py header string updated to match | PASS |
| D5 screen reference (graph-obsidian.png): QA agent responsibility, not tech-writer | PASS — noted in §2, not blocking gate |
| I2 invariant: no client-side layout in any diagram or doc | PASS — unchanged; ADR-0015 untouched |
| I8: ER matches live SQLAlchemy models after regeneration | PASS — zero drift |
| I8: openapi.json matches live FastAPI app after regeneration | PASS — zero drift |

**No contradictions found across ER / OpenAPI / ADR-0016 / models.py / migrations 0004–0005.**

---

## 7. Files modified by this gate run

| File | Action | Reason |
|------|--------|--------|
| `docs/er/schema.mmd` | Regenerated via `make er` + header updated | DRIFT: missing pages.pinned (migration 0005) and edges.kind (migration 0004) |
| `docs/api/openapi.json` | Regenerated via `make openapi` | DRIFT: missing PATCH /pages/{id}/position, PatchPositionRequest/Response schemas, GraphEdgeResponse.kind |
| `backend/scripts/generate_er.py` | Header string updated (line 69) | Header was "v0.3 sprint 3"; updated to "v0.3→v0.4 transition …" |
| `docs/adr/README.md` | Header timestamp + narrative paragraph updated | Header said "Sprint v0.3"; ADR-0016 row was present; narrative lacked ADR-0016 description |
| `DOCS_STATUS.md` | Full rewrite (this file) | Supersedes M3 gate; Phase 0 verdict |

---

## 8. DOCS GATE VERDICT — M4-GUX Phase 0

| Artifact | Status | Detail |
|----------|--------|--------|
| D2 `docs/er/schema.mmd` | UP-TO-DATE (drift fixed) | pages.pinned + edges.kind now present; header updated; zero drift vs models.py |
| D4 `docs/api/openapi.json` | UP-TO-DATE (drift fixed) | PATCH /pages/{id}/position + PatchPositionRequest/Response + GraphEdgeResponse.kind all present; zero drift vs live API |
| D7 ADR-0016 row in `docs/adr/README.md` | UP-TO-DATE | Row was present; index header updated to M4-GUX; 16 ADRs listed, zero gaps |
| D5 `docs/screens/graph-obsidian.png` | PENDING QA | QA agent captures separately; not blocking Phase 0 gate |

**DOCS GATE: PASS**

All required M4-GUX Phase 0 D-artifacts are UP-TO-DATE after drift correction. D5 is
a QA-agent responsibility (Playwright capture against live stack) and is explicitly tracked
as pending — it does not block this gate.

Drift found and fixed in this run:
- D2: `pages.pinned` and `edges.kind` were absent from the committed ER diagram.
- D4: `PATCH /pages/{page_id}/position` endpoint, `PatchPositionRequest/Response` schemas,
  and `GraphEdgeResponse.kind` field were absent from the committed openapi.json.

Both artifacts now match the live schema (models.py / migrations 0004–0005) and the live
FastAPI app respectively.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-28 | M4-GUX Phase 0**
