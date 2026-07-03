# Sprint v0.7 — PM Scope Lock

> Milestone: M7 — "Core completeness & daily UX"
> Author: product-manager
> Date: 2026-07-03
> Branch: sprint/v0.7 (cut from sprint/v0.6)
> Prerequisite: M6 exit criteria met (EC-M6-1..EC-M6-HCP confirmed by Emanuele).
> Source roadmap: docs/reference/ROADMAP-v0.7-v1.0.md §v0.7

---

## 1. Sprint Goal

Close every seam a daily user hits and make the wiki feel finished: 5 vault-bootstrap
templates, new-page from UI, conversation management, unsaved-changes guard, review
search queries, recursive folder import, ServiceNow scheduler (local-folder mode),
retrieval scope fix, ThinkBlock streaming preview, multi-provider routing verifications,
bulk source ops, cancel-all confirmation, parity doc refresh — plus the auto-update
feature (pulled forward from R10-4 by owner request, in flight).

---

## 2. Committed Scope

Exactly the following 14 items. Anything else is out of scope and requires explicit PM
re-approval before any token is spent on it.

---

### R7-1 — Scenario templates (5 vault presets)

| Field | Value |
|---|---|
| Feature ID | F1 (scenarios), K1 (vault bootstrap) |
| Owner | frontend-engineer (UI + preset payload) + backend-engineer (purpose.md / schema.md write endpoint) |
| Effort | M |

**Acceptance criteria:**
- AC-R7-1-1: Five named presets are available in the new-vault or settings flow: Research, Reading, PersonalGrowth, Business, General. Each has a distinct `purpose.md` body (goal, key questions, scope, thesis) and `schema.md` stub appropriate to the domain.
- AC-R7-1-2: Selecting a preset and confirming writes `vault/purpose.md` and `vault/schema.md` via the existing write path (no new ingest pipeline). A backend unit test asserts both files are written with non-empty, preset-specific content.
- AC-R7-1-3: The preset selector is accessible from the vault-creation flow AND from a "Reset vault template" action in Settings. Vitest asserts the component renders all 5 options.
- AC-R7-1-4: i18n strings present in EN and IT for all preset names and descriptions.

**Sequencing note:** shares `vault/purpose.md` write path with R7-6 (recursive import also touches vault context). Implement R7-1 backend endpoint first; R7-6 can reuse it.

---

### R7-2 — New page from UI

| Field | Value |
|---|---|
| Feature ID | F1 (UX gap #9) |
| Owner | frontend-engineer |
| Effort | S |

**Acceptance criteria:**
- AC-R7-2-1: A "+ New page" affordance is visible in the wiki tree panel header. Clicking it opens a dialog with fields: title (required), type (select from entity/concept/source/synthesis/comparison), directory (defaults to `wiki/`, dropdown of existing subdirs).
- AC-R7-2-2: Confirming the dialog calls the existing `POST /pages` or `write_page` endpoint with the supplied frontmatter; the new page appears in the tree without a full reload.
- AC-R7-2-3: Submitting with an empty title is blocked client-side with an inline validation message. Vitest asserts the validation path.
- AC-R7-2-4: i18n strings present in EN and IT.

**Sequencing note:** touches `frontend/src/components/panels/NavTree` and the page-write endpoint. No conflict with R7-3 (different components) but both land in the same WikiTree area — coordinate file ownership with frontend-engineer.

---

### R7-3 — Rename conversations + conversation search/filter

| Field | Value |
|---|---|
| Feature ID | F6 (UX gaps #1, #15) |
| Owner | frontend-engineer (UI) + backend-engineer (PATCH /conversations/{id} endpoint) |
| Effort | S |

**Acceptance criteria:**
- AC-R7-3-1: Each conversation in the chat sidebar has an inline rename action (double-click or edit icon). A `PATCH /conversations/{id}` endpoint accepts `{"title": "..."}` and persists the new name. Vitest asserts the optimistic UI update and revert-on-error path.
- AC-R7-3-2: A search/filter input at the top of the conversation sidebar filters the displayed list by title substring, client-side, with no backend round-trip for the filter itself.
- AC-R7-3-3: The filter input debounces at 200 ms and is virtualized if the conversation list exceeds 50 items (TanStack Virtual, I4 compliance).
- AC-R7-3-4: i18n strings present in EN and IT.

**Sequencing note:** `PATCH /conversations/{id}` is a new endpoint in `backend/app/chat/`; must be reflected in `docs/api/openapi.json` (D4 continuous gate).

---

### R7-4 — Unsaved-changes indicator + navigation guard

| Field | Value |
|---|---|
| Feature ID | F1 (UX gap #2) |
| Owner | frontend-engineer |
| Effort | S |

**Acceptance criteria:**
- AC-R7-4-1: The editor tab/header shows a visible unsaved indicator (e.g., a dot or asterisk) whenever the CodeMirror buffer differs from the last-saved content. The indicator disappears on save. Vitest asserts the dirty-state toggle.
- AC-R7-4-2: Attempting to navigate away from an unsaved editor (NavRail click, browser back, Tauri window close) shows a confirmation dialog: "Unsaved changes — leave anyway?" with Confirm / Cancel actions.
- AC-R7-4-3: The guard is implemented as a Zustand-derived selector on the editor dirty state; no prop-drilling through the component tree. TypeScript strict passes.
- AC-R7-4-4: Guard integrates with the Tauri `CloseRequested` event if the desktop shell is running (graceful no-op in browser/PWA mode).

**Sequencing note:** touches `frontend/src/store/` and the CodeMirror editor component. No backend changes.

---

### R7-5 — Review search_queries: JSONB populated at proposal time

| Field | Value |
|---|---|
| Feature ID | F9 (review queue), F10 (deep-research hand-off) |
| Owner | backend-engineer (JSONB column + proposal logic) + ai-agent-engineer (query generation prompt) |
| Effort | M |

**Acceptance criteria:**
- AC-R7-5-1: The `review_items` table has a `search_queries` JSONB column (Alembic migration). When a `deep-research` ReviewItem is proposed (by the ingest orchestrator or the review endpoint), the AI generates 3–5 targeted search queries from the item's topic and stores them in `search_queries`. A backend unit test asserts the column is non-null and contains a list of strings after proposal.
- AC-R7-5-2: When the user selects the "Deep Research" action on a ReviewItem, the stored `search_queries` are passed directly to `ops/deep_research.py` as the seed query list (no re-generation). A pytest integration test asserts the same list flows end-to-end.
- AC-R7-5-3: The query-generation prompt routes through `InferenceProvider` (I6 compliance). No hardcoded model or backend.
- AC-R7-5-4: ER diagram (`docs/er/schema.mmd`) regenerated via `make er` to reflect the new column; D4 openapi updated if a new response field is exposed.

**Sequencing note:** touches `backend/app/ops/review.py` and `backend/app/ops/orchestrator.py`. R7-6 also touches `orchestrator.py` — these two items must NOT be developed on the same file simultaneously; sequence R7-5 first, R7-6 second, or assign separate review.py vs orchestrator.py ownership.

---

### R7-6 — Recursive folder import + folderContext hint

| Field | Value |
|---|---|
| Feature ID | F3 (ingest), F12 (multi-format) |
| Owner | backend-engineer (folder walk + endpoint) + ai-agent-engineer (folderContext prompt injection) |
| Effort | M |

**Acceptance criteria:**
- AC-R7-6-1: A `POST /ingest/folder` endpoint accepts a path (relative to `vault/raw/`) and recursively enqueues all supported files (`.md`, `.pdf`, `.docx`, `.txt`, `.pptx`, `.xlsx`) for ingest. Only files not already indexed (hash gate, I1) are enqueued. Pytest asserts the correct file list is returned for a fixture directory tree.
- AC-R7-6-2: Each file ingested from a folder import receives a `folderContext` hint (the folder name or README if present) injected into the analysis prompt's `vault_context`. The hint is bounded to 500 tokens (I7). A unit test with a mock provider asserts `folderContext` is present in the assembled prompt.
- AC-R7-6-3: The folder walk is bounded: max 500 files per call; returns HTTP 400 with a clear error if the limit is exceeded. No unbounded filesystem traversal (I7 spirit).
- AC-R7-6-4: UI: a "Import folder" button in the Sources panel opens a path input. The import is enqueued to the existing activity queue with per-file progress entries.

**Sequencing note:** touches `backend/app/ingest/orchestrator.py` (same file as R7-5); must be sequenced after R7-5 is merged. Also touches `frontend/src/components/sections/IngestSection` — no conflict with R7-2/R7-3.

---

### R7-7 — ServiceNow scheduler (local-folder watch + experimental auto-download)

| Field | Value |
|---|---|
| Feature ID | F3 (ingest pipeline), F12 (multi-format / PDF) |
| Owner | backend-engineer |
| Effort | M |

**Scope clarification (PM flag — anti-scope-creep boundary):**
The committed scope is a **scheduled convert-and-ingest of a watched local folder**:
the Marker connector (`tools/marker-converter/`) already exists; this item wires it into
an import schedule. Auto-download from `docs.servicenow.com` is **experimental** and is
implemented only if `SERVICENOW_AUTO_DOWNLOAD=true` in env — it is explicitly not part of
the acceptance gate and must not block the item from being marked done.

**Acceptance criteria:**
- AC-R7-7-1: A configurable scheduler (interval, default 1 h) watches a designated local folder (env var `SERVICENOW_WATCH_DIR`, default `vault/raw/servicenow/`) for new or updated PDF files. On schedule tick, it runs the Marker converter on any unprocessed PDFs, drops the resulting Markdown into `vault/raw/sources/`, and the existing watcher picks them up for ingest. Pytest asserts the convert-drop-ingest flow with a fixture PDF (no real ServiceNow dependency).
- AC-R7-7-2: The scheduler is bounded: max 20 PDFs per tick; skips files already converted (SHA256 hash gate, I1); logs `total_files_converted`, `total_cost_usd=0.00` (Marker is local). A unit test asserts the max-per-tick cap.
- AC-R7-7-3: The scheduler is disabled by default (`SERVICENOW_SCHEDULE_ENABLED=false`); enabling it requires explicit env var. Scheduler lifecycle (start/stop) is exposed via `GET /scheduler/status` and `POST /scheduler/{action}` (start/stop/trigger). D4 openapi updated.
- AC-R7-7-4 (experimental, not gate-blocking): When `SERVICENOW_AUTO_DOWNLOAD=true`, the scheduler additionally attempts to download PDFs from a configurable URL list in `vault/raw/servicenow/download-list.json`. This path is marked `experimental` in code comments, logs a warning on startup, and is excluded from the pytest suite. It must NOT affect the green-gate for this item.

**Sequencing note:** no shared files with R7-5/R7-6. Can be built in parallel with R7-2/R7-3/R7-4.

---

### R7-8 — Retrieval scope: citations from wiki/ only

| Field | Value |
|---|---|
| Feature ID | F5 (4-phase retrieval) |
| Owner | backend-engineer |
| Effort | S |

**Acceptance criteria:**
- AC-R7-8-1: The `/search` assembly phase (4-phase retrieval in `backend/app/rag/retrieval.py`) filters Qdrant results to pages with `file_path` under `wiki/` only. Raw source documents (`raw/`) are excluded from the cited context assembled for chat. A unit test asserts that a `raw/` page present in Qdrant is not returned by the assembly phase.
- AC-R7-8-2: The decision is documented in a single-paragraph ADR note (appended to an existing ADR or a new ADR-0049) explaining why `raw/` exclusion is the correct behavior (raw docs are source material, not citable wiki knowledge).
- AC-R7-8-3: Existing retrieval tests remain green; no regression on citation count or citation format.
- AC-R7-8-4: `GET /search` response schema documents the `wiki_only` filter; D4 openapi updated.

**Sequencing note:** isolated to `backend/app/rag/retrieval.py`. No frontend changes. No conflict with other items.

---

### R7-9 — ThinkBlock streaming preview (rolling fade)

| Field | Value |
|---|---|
| Feature ID | F7 (reasoning `<think>` display) |
| Owner | frontend-engineer |
| Effort | S |

**Acceptance criteria:**
- AC-R7-9-1: During streaming, if the response contains an open `<think>` block (not yet closed), the last 3 visible lines of think content are rendered in a muted/faded style below the main response area. The block collapses to a single summary line when `</think>` arrives (existing behavior preserved).
- AC-R7-9-2: The rolling preview is parsed at stream END boundary only — no per-token DOM mutation (I3 compliance). A vitest asserts the ThinkBlock component does not re-render on individual token appends, only on chunk boundaries.
- AC-R7-9-3: The feature is gated behind the existing `VITE_SHOW_THINKING` env flag; when disabled, no think content is rendered at any point.
- AC-R7-9-4: TypeScript strict passes; no new `any` escapes.

**Sequencing note:** isolated to the chat message render path. No backend changes. No conflict with other items.

---

### R7-10 — Multi-provider routing verifications

| Field | Value |
|---|---|
| Feature ID | F17 (InferenceProvider), F3 (language directive), F10 (deep-research synthesis) |
| Owner | ai-agent-engineer |
| Effort | S |

**Acceptance criteria:**
- AC-R7-10-1: DeepSeek and Qwen models (served via the OpenAI-compatible `ApiProvider` backend) correctly receive and expose the `reasoning_content` / `<think>` field in chat streaming. A unit test with a fixture streaming response asserts the field is routed to the ThinkBlock component.
- AC-R7-10-2: Deep-research synthesis pages land in `vault/wiki/queries/` (not the root `wiki/`). A pytest integration test asserts the output file path.
- AC-R7-10-3: `ApiProvider` and `OllamaProvider` both inject a mandatory language directive ("OUTPUT LANGUAGE: {lang}") in the page-generation step of the orchestrated loop, matching the behavior already present in the CLI provider. A unit test asserts the directive is present in the assembled prompt for both providers.
- AC-R7-10-4: No new provider-specific code outside `backend/app/ingest/provider/` (I6 compliance). Static analysis test (already in suite) remains green.

**Sequencing note:** touches `backend/app/ingest/provider/api.py`, `ollama.py`, and `ops/deep_research.py`. The provider files are shared with F17 base work — no conflicts expected if this item is treated as targeted patches, not refactors.

---

### R7-11 — Bulk ops on sources

| Field | Value |
|---|---|
| Feature ID | F1 (UX gaps #4, #14) |
| Owner | frontend-engineer (multi-select UI + progress) + backend-engineer (bulk ingest/delete endpoint) |
| Effort | M |

**Acceptance criteria:**
- AC-R7-11-1: The Sources panel supports multi-select (checkbox per row + select-all). A "Ingest selected" and "Delete selected" action bar appears when one or more items are selected. Vitest asserts the selection state and action bar visibility.
- AC-R7-11-2: "Ingest selected" calls a `POST /ingest/bulk` endpoint with a list of file paths; each file is enqueued as a separate activity queue item with individual progress tracking visible in the activity bar.
- AC-R7-11-3: "Delete selected" calls a `DELETE /sources/bulk` endpoint (or equivalent); triggers cascade-delete for each item (F13 pipeline, already implemented). A pytest asserts the correct cascade behavior for a 2-file fixture.
- AC-R7-11-4: Upload progress per file is shown in the existing activity bar entries (percentage or spinner). No new UI surface; reuses ActivityBar. The list is virtualized for >50 items (TanStack Virtual, I4).

**Sequencing note:** `POST /ingest/bulk` touches the ingest orchestrator queue, same area as R7-6. Sequence R7-6 first (folder import endpoint adds the queue infrastructure); R7-11 can reuse it. Assign the ingest queue area to one engineer at a time.

---

### R7-12 — Cancel-all confirmation dialog

| Field | Value |
|---|---|
| Feature ID | F1 (UX gap #12) |
| Owner | frontend-engineer |
| Effort | S |

**Acceptance criteria:**
- AC-R7-12-1: The activity bar's "Cancel all" button (or equivalent bulk-cancel affordance) triggers a confirmation dialog: "Cancel all running tasks? This cannot be undone." with Confirm / Cancel. The cancel action is only sent to the backend after the user confirms.
- AC-R7-12-2: The dialog is a shared `ConfirmDialog` component (reuse or create); not an inline `window.confirm()` call. Vitest asserts the dialog renders and that the cancel API call is NOT made if the user clicks "Cancel" in the dialog.
- AC-R7-12-3: i18n strings present in EN and IT.

**Sequencing note:** isolated to ActivityBar and a shared dialog component. No backend changes. No conflict with other items.

---

### R7-13 — Refresh SYNAPSE-VS-LLMWIKI-PARITY.md

| Field | Value |
|---|---|
| Feature ID | docs artifact (K1–K8 / F1–F17 parity tracking) |
| Owner | tech-writer (with input from backend-engineer and ai-agent-engineer on closed gaps) |
| Effort | S |

**Acceptance criteria:**
- AC-R7-13-1: All P0 rows in `docs/reference/SYNAPSE-VS-LLMWIKI-PARITY.md` that were closed before or during v0.7 are marked `✅` with a code reference pointing to the implementing file + line range.
- AC-R7-13-2: All P1 gaps addressed in this sprint (R7-1 through R7-12) have their parity rows updated to reflect the new state. New rows are added for any net-new behaviors shipped.
- AC-R7-13-3: The document header "Date" is updated; no stale "audit corrections" notes remain unreferenced.
- AC-R7-13-4: tech-writer sign-off on consistency between the parity doc and the sprint ACs.

**Sequencing note:** must be the LAST item completed in the sprint, after all other R7 items are merged and their behaviors confirmed.

---

### AUTO-UPDATE — Desktop auto-update (pulled forward from R10-4)

| Field | Value |
|---|---|
| Feature ID | F15 (cross-platform / desktop) — references R10-4 in roadmap |
| Owner | frontend-engineer (Tauri plugin wiring + UI) + devops-engineer (GitHub release endpoint + CI) |
| Effort | M |
| Status | IN FLIGHT — owner-approved pull-forward; treat as in-scope |

**Context:** Originally R10-4 in the v1.0 release (requires code signing, R10-3). The owner
has explicitly pulled this forward into v0.7. The implementation must NOT block the release
gate if signing is unavailable on the build machine — see AC-AUTO-3.

**Acceptance criteria:**
- AC-AUTO-1: `tauri-plugin-updater` is wired into `src-tauri/`. On app start, if `SYNAPSE_UPDATE_ENDPOINT` is set, the plugin checks the configured GitHub Releases endpoint for a newer version. A vitest/Tauri unit test with a mocked updater response asserts the "update available" notification appears in the UI.
- AC-AUTO-2: The update check is non-blocking: the app starts and is fully usable within 3 seconds regardless of the update endpoint response time (timeout 5 s, silently ignored on failure). A test asserts startup time is unaffected when the update endpoint returns a timeout.
- AC-AUTO-3: The update flow is gated behind `SYNAPSE_AUTO_UPDATE=true` (default: false). When the env var is absent or false, no update check is performed and no UI is shown. If code-signing artifacts are unavailable in CI, the gate for this item is met by the env-var-off path being green; the signed-artifact path is best-effort and does not block the sprint exit.
- AC-AUTO-4: ADR-0049 (or appended section to ADR-0039) documents the update endpoint contract, the signing dependency, and the opt-in flag. D6a (USER.md) describes how users enable auto-update.

**Sequencing note:** Tauri `src-tauri/` files are shared with the v0.6 desktop shell (ADR-0039). No other v0.7 item touches `src-tauri/`. devops-engineer owns the GitHub release pipeline side; frontend-engineer owns the Tauri plugin and UI side. These can run in parallel once ADR-0049 is written.

---

## 3. Explicit sequencing order (same-file conflicts)

Items that share backend files must be sequenced strictly in this order to prevent
concurrent edits and merge conflicts:

1. **R7-5** (review.py / orchestrator.py) → merge
2. **R7-6** (orchestrator.py + ingest queue) → merge
3. **R7-11** (bulk ingest — reuses queue infrastructure from R7-6) → merge

Items that share the wiki-tree / NavTree frontend area:
1. **R7-2** (new-page dialog in NavTree) → merge
2. **R7-3** (conversation sidebar — different file but same PR window) → can be parallel

Items with no shared files (can be parallelized freely): R7-1, R7-4, R7-7, R7-8, R7-9,
R7-10, R7-12, AUTO-UPDATE.

R7-13 (parity doc refresh) is last, after all code items are merged.

---

## 4. Out of scope for v0.7

Everything not listed in §2 above is explicitly out of scope. The following items from the
roadmap are deferred to v0.8 or later and must NOT be built during this sprint:

| Deferred item | Target release | Reason |
|---|---|---|
| R8-1: Marker as first-class PDF extractor (promote to main pipeline) | v0.8 | Architectural change to ingest pipeline; needs ADR first |
| R8-2: Vision captions for images | v0.8 | Requires AI+BE coordination; separate sprint |
| R8-3: Audio/video transcription (Whisper) | v0.8 | Large effort; separate sprint |
| R8-4: Vault export/backup endpoint | v0.8 | No dependency in v0.7 |
| R8-5: Search filters & sort (facets) | v0.8 | UX gap, not daily-critical |
| R8-6: Citation click-through everywhere | v0.8 | Polish pass |
| R8-7: Chrome clipper store release | v0.8 | Extension packaging/publish flow |
| R9-1: Cost dashboard | v0.9 | Observability sprint |
| R9-2: Metrics/health endpoint | v0.9 | Observability sprint |
| R9-3: purpose.md suggestions (scope-drift ReviewItem) | v0.9 | AI+BE; needs v0.9 trust sprint |
| R9-4: schema.md co-evolution | v0.9 | L effort; separate sprint |
| R9-5: Graph drill-down (community panel, edge tooltip) | v0.9 | Graph polish sprint |
| R9-6: Playwright E2E happy-path suite | v0.9 | QA sprint |
| R10-1: Authentication layer | v1.0 | Structural; design ADR first |
| R10-2: Multi-vault UI | v1.0 | Depends on R10-1 |
| R10-3: Code signing + notarization | v1.0 | Requires Apple/Windows certs |
| R10-5: Mobile/PWA breakpoints + touch gestures | v1.0 | Polish; separate sprint |
| R10-6: MkDocs docs site | v1.0 | Optional; docs sprint |
| Any feature not assigned a Feature ID in CLAUDE.md §4 | never without new ID | Anti-scope-creep invariant |

**Never list (invariants I1–I9):** full-rescan, main-thread force layout, per-token DOM
mutation, WYSIWYG/ProseMirror, hardcoded provider or model ID, unbounded loops, skipping
D-artifacts, Tavily/alt-search, local embedding reimplementation. These are permanent
blocks regardless of sprint.

---

## 5. Exit criteria for v0.7 release (EC-M7)

All 4 sign-offs required before tagging `v0.7.0`: QA-test-engineer + Solution-architect +
Tech-writer + Product-manager.

| ID | Criterion |
|---|---|
| EC-M7-1 | All 14 committed items (R7-1..R7-13 + AUTO-UPDATE) have all ACs green in pytest + vitest. |
| EC-M7-2 | `ruff check` + `black --check` + mypy strict pass tree-wide (no new violations). ESLint + prettier clean. TypeScript strict passes. |
| EC-M7-3 | ER diagram zero-drift: `make er` output matches live schema (new `search_queries` column on `review_items` reflected). `docs/api/openapi.json` regenerated and current. |
| EC-M7-4 | `docs/reference/SYNAPSE-VS-LLMWIKI-PARITY.md` updated (R7-13 complete); tech-writer sign-off. |
| EC-M7-5 | `docs/sequences/` updated if any new bounded loop was introduced (R7-7 scheduler, R7-5 query generation). |
| EC-M7-6 | ADR written for: R7-8 retrieval scope decision (ADR-0049 or appended); AUTO-UPDATE plugin contract (ADR-0049 or ADR-0050). ADR README index updated. |
| EC-M7-7 | `vault/wiki/` remains a valid Obsidian vault after all v0.7 ops (I5/K7). Manual spot-check by owner. |
| EC-M7-8 | GitHub release `v0.7.0` created with: macOS `.dmg`, Windows `.msi`, Linux `.AppImage` desktop artifacts (signed if certificates available; unsigned artifacts acceptable if AUTO-UPDATE AC-AUTO-3 env-var-off path is green). |
| EC-M7-HCP | Human checkpoint: Emanuele confirms scenario templates, new-page flow, conversation rename, and ThinkBlock preview in a live browser session before tagging v0.7.0. |

---

## 6. Velocity note

v0.6 shipped ON SCOPE with some items pulled in ahead of plan (Tauri shell, brand
identity, dark mode, command palette, ingest queue cancel/pause/retry — all confirmed in
sprint-v0.6 commits). v0.7 carries 14 items at S/M effort each across a 2-week window.
The ServiceNow scheduler (R7-7) is the highest uncertainty item due to the Marker
integration surface — it is sequenced to run in parallel with lower-risk items and its
experimental download path is explicitly NOT gate-blocking. AUTO-UPDATE is in-flight and
should not create new critical-path risk provided ADR-0049 is written before implementation
starts.

If the sprint runs over, the PM de-scope order is: AUTO-UPDATE experimental signing path
(already gated) > R7-7 AC-R7-7-4 (already non-blocking) > R7-10 (verifications, S effort
but touches multiple providers) > R7-13 (can ship as WIP if all rows are updated, just not
signed off). R7-1 through R7-9 and R7-12 are committed and must not be cut.
