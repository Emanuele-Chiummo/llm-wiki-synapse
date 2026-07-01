# Synapse vs. nashsu/llm_wiki — Parity Matrix

> **Purpose:** exhaustive behavioral mirror audit. Drives gap-closure implementation.
> **Sources:** audit docs at `docs/reference/llm_wiki-audit/01-AUDIT-FUNZIONALE.md` (code-anchored
> to llm_wiki v0.5.4 @ `c03c6be`); Synapse codebase at `sprint/v0.6`.
> **Date:** 2026-07-01. **Author:** functional-analyst agent.
>
> Verdict legend:
>   ✅ parity — behavior matches or exceeds user-facing expectations.
>   🟡 partial — core exists but a sub-behavior or edge case is absent.
>   ❌ missing — feature / sub-behavior not yet implemented.
>   ⭐ Synapse-better — Synapse deliberately uses a superior internal implementation.
>     The user-facing PROCESS should mirror llm_wiki; the internal is intentionally different.
>   ⛔ do-not-mirror — an llm_wiki defect we explicitly do NOT want to replicate.

---

## 0. Reading guide

Every row covers one atomic user-facing behavior pulled from the audit.
- "llm_wiki ref" = `file:line` in the audit document (audit = `01-AUDIT-FUNZIONALE.md`,
  `02-CODE-UI-REVIEW.md`).
- "Synapse code ref" = the authoritative Synapse source file + line range where the behavior lives,
  or the explicit absence.
- Priority: **P0** = blocks behavioral mirror promise / sprint gate; **P1** = significant gap,
  ship within sprint; **P2** = polish / nice-to-have.

---

## 1. F1 — Three-column layout + icon sidebar + resizable + activity panel

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| 3-panel shell (sidebar + center + preview) | `app-layout.tsx:86-143` — left SidebarPanel + ActivityPanel, center ContentArea, right ResearchPanel | `frontend/src/components/AppShell.tsx:8-15` — NavRail + SectionRouter (sections: pages→PanelGroup with NavTree/Center/PreviewPanel, graph, ingest, settings) | ✅ | None — panels present; layout maps cleanly. llm_wiki has a ResearchPanel column; Synapse collapses research into a section behind the NavRail (design choice). | — | — |
| Resizable panels with min/max clamp | `app-layout.tsx:53-62` — `Math.max(150, Math.min(400,…))` on left; right clamped to 0.5 rect | `frontend/src/components/panels/PanelGroup.tsx` — exists | 🟡 | Verify PanelGroup exposes per-axis min/max clamps matching user-perceived "feel" of llm_wiki. If absent, drag can reach zero width (UX regression). | P1 | [FE] |
| Icon sidebar with nav items | `icon-sidebar.tsx:20-28` — Chat/Wiki/Sources/Search/Graph/Lint/Review/DeepResearch/Settings | `frontend/src/components/nav/NavRail.tsx:28-30` — Chat · Wiki · Sources · Graph / Lint · Review · DeepSearch · Settings | ✅ | Nav items present and ordered. Search removed per architectural ruling (lexical via Query). | — | — |
| Activity panel — real-time task status | `activity-panel.tsx` — polls queue every 1s, live progress bar, auto-expand on running task, retry/cancel/pause per task | `frontend/src/components/activity/ActivityBar.tsx` — exists (28px bottom bar) | 🟡 | ActivityBar is a 28px bottom strip, not the llm_wiki side-panel with per-task status text + progress bar + retry/cancel/pause. Gap: (a) no per-task retry/cancel from the activity surface, (b) no auto-expand on ingest start. Implementation detail, but user-facing affordance is markedly narrower. | P1 | [FE] |
| Scenario templates (5 presets with schema + purpose + extraDirs) | `templates.ts:640-646` — Research / Reading / PersonalGrowth / Business / General, each with full schema+purpose text | `frontend/src/components/common/ScenarioTemplates.tsx:16-43` — 2 quick-action buttons (explore-high-degree, explore-concept) | ❌ | Synapse has only 2 graph-exploration buttons, not 5 vault-bootstrap templates with schema+purpose text. Missing: Research/Reading/Business/General presets that pre-populate purpose.md and schema.md at vault creation. | P1 | [FE]/[BE] |
| Tauri v2 / PWA desktop packaging | `src-tauri/` — Rust Tauri v2 app; close behavior user-settable; CI matrix 4 OS/arch | CLAUDE.md §8 sprint 6 goal: "PWA + Tauri v2"; `frontend/src/main.tsx` — Vite SPA today | 🟡 | PWA entry-point and Tauri v2 wrapping are sprint-6 goals (F15), not yet shipped. Not a behavioral gap for server use. Track separately under F15. | P2 | [FE] |

**Invariants touched:** I2, I3, I4.

---

## 2. F2 — `purpose.md` (injected as context + LLM suggestions)

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| purpose.md read into ingest context | `ingest.ts:691-694` — reads `${pp}/purpose.md`; injected in both analysis `:968` and generation `:1000` | `backend/app/ingest/orchestrator.py:1155-1166` — `_load_vault_context()` reads `vault_root/purpose.md` and `vault_root/schema.md`; passed as `vault_context` to `run_orchestrated_loop` and as `system_prompt` to `delegate_ingest` | ✅ | Both providers receive purpose+schema. llm_wiki had a path divergence bug (`wiki/purpose.md` vs root) — Synapse is unified on `vault_root/purpose.md`. | — | — |
| purpose.md read into chat / query context | `chat-agent.ts:1005`, `:1654` — injected into every chat turn | `backend/app/chat/stream.py:run_chat_stream` — calls `build_chat_context()` which prepends purpose + overview to retrieval context | ✅ | Purpose is in chat context via `backend/app/chat/context.py`. | — | — |
| LLM suggests updates to purpose.md | Audit: **ASSENTE** — vaporware in llm_wiki (01-AUDIT §F2: "nessun codice") | Synapse: also absent. No endpoint, no prompt, no review item type for purpose-suggestion. | ❌ | Neither codebase implements this. Synapse can CLOSE the parity gap by implementing it properly: at ingest end, the orchestrated branch can emit a `purpose-suggestion` ReviewItem type if the analysis reveals scope drift. This is also an improvement over llm_wiki. | P2 | [AI]/[BE] |
| purpose.md path consistency | llm_wiki bug: `startIngest` reads `wiki/purpose.md` (wrong), `autoIngest` reads root (01-AUDIT §F2 B-3) | Synapse: `vault_root/purpose.md` everywhere (orchestrator.py:1155-1166) | ⭐ | Synapse already fixed this bug. Do NOT mirror the llm_wiki bug. | — | — |

**Invariants touched:** I5, I6.

---

## 3. F3 — Two-step Chain-of-Thought ingest (Analysis → Generation)

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| Two distinct LLM calls (analyze → generate) | `ingest.ts:953-1039` — Step 1 analysis, Step 2 generation, temperature 0.1, reasoning off | `backend/app/ingest/loop.py` — `run_orchestrated_loop`: `provider.analyze()` then `provider.generate()` bounded by max_iter; `backend/app/ingest/provider/base.py` — ABC defines both methods | ✅ | Two-step CoT present for orchestrated (Local/API) providers. CLI provider delegates the entire agent loop, which intrinsically does CoT. | — | — |
| Source traceability — origin path in `sources[]` | `ingest.ts:2002,2048` — prompt imposes filename; `canonicalizeSourcesField` forces it | `backend/app/ingest/orchestrator.py:879-888` — `write_wiki_page` appends `origin_source` to sources[] if absent; `backend/app/ingest/loop.py:validate_pages:55` — "origin path ∈ sources[]" is a validation hard rule; invalid batch triggers retry | ✅ | Synapse enforces traceability via the shared validator (loop.py) and the write seam (orchestrator.py). | — | — |
| `overview.md` auto-generation after ingest | `ingest.ts:2024` — prompt item 6 regenerates overview.md (FULL overwrite each ingest) | `backend/app/ingest/orchestrator.py:1067-1083` — `_update_overview()` APPENDS a one-line entry per source; does NOT full-overwrite | ⭐ | Synapse deliberately chose append-only overview. Avoids the llm_wiki "overview drift" bug (audit B-7). The user-facing result (a growing catalogue) is better than a full rewrite that could lose prior topics. DO NOT mirror full-overwrite. | — | — |
| Language-aware generation (language directive) | `buildLanguageDirective` injects "MANDATORY OUTPUT LANGUAGE: X" in both steps; `contentMatchesTargetLanguage` soft-checks at write | `backend/app/ingest/schemas.py` — `WikiFrontmatter.lang` field; provider prompts include lang directive from the analysis | 🟡 | Lang field exists in schemas. Verify `ApiProvider` and `OllamaProvider` prompt templates include an explicit "OUTPUT LANGUAGE" directive matching the source detection. No `contentMatchesTargetLanguage`-equivalent output filter confirmed in Synapse provider code. | P1 | [AI] |
| Fallback source-summary page if provider produces no pages | `ingest.ts:1219-1244` — stubs source-summary with analysis.slice(0,3000) if no FILE blocks and not aborted | `backend/app/ingest/orchestrator.py:1046-1064` — `_ensure_source_summary()` synthesizes a minimal source page when `pages` is empty (post-loop) | ✅ | Parity: both guarantee at least one page per source. | — | — |

### F3-bis — SHA256 / hash-based incremental cache

| Feature / sub-behavior | llm_wiki behavior | Synapse current state | Verdict | Gap | Priority | Owner |
|---|---|---|---|---|---|---|
| SHA256 hash gate — skip unchanged files | `ingest-cache.ts` — SHA256 via `crypto.subtle`; cache keyed by filename; anti-ghost check; skips ingest if hash unchanged AND prior output files exist | `backend/app/ingest/orchestrator.py:139-146` — mtime-then-SHA256 hash gate (ADR-0001): mtime fast-path → if mtime changed, recompute SHA256 → if hash unchanged, touch mtime only and SKIP | ⭐ | Synapse has an equivalent and arguably more robust gate (mtime-then-hash avoids the "same basename in different dirs" collision flaw noted in the audit). The anti-ghost equivalent is the `deleted_at` soft-delete + Qdrant hard-delete (ADR-0005). | — | — |

### F3-ter — Persistent ingest queue with crash-recovery and retry

| Feature / sub-behavior | llm_wiki behavior | Synapse current state | Verdict | Gap | Priority | Owner |
|---|---|---|---|---|---|---|
| Serialized persistent ingest queue | `ingest-queue.ts:658-821` — serial, persisted to `.llm-wiki/ingest-queue.json`, crash-recovery restores pending tasks | Synapse ingest is triggered via `POST /ingest/trigger` (sync) or the watchdog watcher (async via asyncio event loop). No persistent queue/JSON on disk. | 🟡 | Synapse has no persistent queue file. A crash mid-ingest loses the in-flight task (watcher will re-fire on next write event). For the **server** use-case this is acceptable (watcher retriggers on restart), but there is no "pending tasks panel" or "resume after crash" UX. Gap: no activity panel showing "queued / running / failed" with per-task status + retry. This is the same gap as F1 activity panel above. | P1 | [BE]/[FE] |
| Retry with MAX_RETRIES=3 | `ingest-queue.ts:610,805-814` — 3 retries | `backend/app/ingest/loop.py` — `max_iter` (default 3) covers ingest loop retries; provider fallback is bounded to exactly once (orchestrator.py:610-633) | ✅ | Max iterations equivalent exists. | — | — |
| Retry WITHOUT backoff (defect) | Bug: immediate requeue on failure (01-CODE-UI §B-5) | `backend/app/ingest/orchestrator.py:605-633` — fallback is synchronous; no exponential backoff between loop iterations | ⛔ | llm_wiki bug — do NOT mirror. Synapse should implement exponential backoff on provider retries (P1, separate from parity). For now the loop already stops after max_iter so the churn is bounded. | — | — |
| Cancel in-flight ingest with cascade-delete of partial files | `cancelTask` + `AbortController` → cascade-delete partial files | No cancel endpoint in Synapse v0.6. | 🟡 | Cancel is a UX feature, not a correctness issue. P2. | P2 | [BE]/[FE] |

### F3-quater — Folder import

| Feature / sub-behavior | llm_wiki behavior | Synapse current state | Verdict | Gap | Priority | Owner |
|---|---|---|---|---|---|---|
| Recursive folder import preserving structure | `source-lifecycle.ts:200-257` — `flattenFiles`, copies to `raw/sources/<folder>/`, `folderContext` hint in analysis prompt | `backend/app/import_scheduler.py` — scheduled folder import: copies files from `source_dir` into `raw_sources/` (bounded scan); `backend/app/upload.py` — multipart upload endpoint (single file) | 🟡 | Synapse has scheduled single-directory import and per-file upload. Missing: (a) recursive folder import preserving subdirectory structure, (b) `folderContext` hint (joined path segments) injected into the analysis prompt. | P1 | [BE]/[AI] |

### F3-quinquies — Source folder auto-watch + debounce

| Feature / sub-behavior | llm_wiki behavior | Synapse current state | Verdict | Gap | Priority | Owner |
|---|---|---|---|---|---|---|
| FS watcher on raw/sources/ with debounce | `file_sync.rs` — `notify::RecommendedWatcher` recursive, debounce 700ms | `backend/app/watcher.py:_DEBOUNCE_SECONDS=1.5` — watchdog-based, debounced (env `WATCH_DEBOUNCE_SECONDS`), per-path coalescing | ⭐ | Server-side Python watchdog equivalent, 1.5s debounce (tunable). Handles burst events. No dual-watch race (the Rust double-watch bug is absent by construction). | — | — |
| Watch only active project (non-active silently dropped) | llm_wiki bug: changes to non-active projects are `console.error`-logged (01-AUDIT §F3-quinquies) | Synapse: single vault per service instance (VAULT_ID env); no multi-project context; all events on the watched path are processed | ⭐ | No multi-project confusion. Not applicable by design. | — | — |

### F3-sexies — Scheduled import

| Feature / sub-behavior | llm_wiki behavior | Synapse current state | Verdict | Gap | Priority | Owner |
|---|---|---|---|---|---|---|
| Periodic scheduled import (configurable interval) | `scheduled-import.ts` — `setInterval` 1–1440 min, MD5-diff, cap 100MB/file | `backend/app/import_scheduler.py` — asyncio background task, frequency enum (15m/1h/6h/daily), hash-compare, `IMPORT_SCAN_MAX_FILES` + `IMPORT_SCAN_MAX_SECONDS` bounds | ✅ | Equivalent. Synapse avoids the llm_wiki `scanning` global (per-vault by construction). | — | — |

---

## 4. F4 — Knowledge Graph, 4-signal relevance, sigma.js / FA2

> **⚠️ CORRECTION (2026-07-01, code-verified against llm_wiki `src/lib/wiki-graph.ts`):** the
> earlier claim that "llm_wiki includes any edge with weight > 0" is **WRONG**. llm_wiki's
> rendered graph is built from **wikilinks only** (one edge per resolved `[[wikilink]]`;
> relevance is applied only as a *weight* to those edges). Synapse (wikilink ∪ shared-source,
> ADR-0016) is actually a **denser superset** — so the perceived "abysmal difference" from
> llm_wiki was **NOT** edge density. Root causes were: (1) layout algorithm — **fixed by
> switching FR → server-side ForceAtlas2, ADR-0045** (2026-07-01); (2) thin edges / no
> opacity-by-weight — **fixed** (edge size 0.5→4px + deepened weight ramp); (3) missing
> zoom/fit controls — **fixed** (zoom-in/out/fit buttons). The remaining real edge-logic gap
> is the typeAffinity cross-type matrix (row below, still P1).

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| 4-signal edge-weight formula (exact weights) | `graph-relevance.ts:30-43` — `directLink×3.0 + sourceOverlap×4.0 + adamicAdar×1.5 + typeAffinity×1.0` | `backend/app/graph/engine.py:21-26` — identical formula: `3.0·direct + 4.0·source_overlap + 1.5·adamic_adar + 1.0·same_type` | ✅ | Exact weight parity confirmed. | — | — |
| typeAffinity signal semantics (cross-type bonus) | Audit: `TYPE_AFFINITY` matrix awards CROSS-type pairs (entity↔concept=1.2), not same-type (01-AUDIT §F4 inaccuracy 1) | `backend/app/graph/engine.py` — `_TYPE_AFFINITY` matrix + `_type_affinity()` helper (symmetric, case-insensitive, None/unknown→0.5); 4th weight term is now `1.0·type_affinity` | ✅ | Done (G-P1-7, 2026-07-01). Exact llm_wiki matrix ported from `src/lib/graph-relevance.ts`: entity↔concept=1.2, concept↔synthesis=1.2, entity↔entity=0.8, source↔source/query↔query=0.5, default 0.5. Rewards cross-type, penalizes same-type — modulates ForceAtlas2 clustering (does NOT create edges; ADR-0016 preserved). Replaces the old binary `same_type`. Tests: `TestTypeAffinity`. | — | [BE] |
| Edge INCLUSION rule (structural only) | llm_wiki: `calculateRelevance` computes relevance for all pairs; graph includes any edge with weight > 0 | `backend/app/graph/engine.py:15-19` — ADR-0016: edge EXISTS iff `direct_link_count > 0 OR shared_source_count > 0`; AA and same-type are MODULATORS only; prevents type-cliques | ⭐ | Synapse's edge-inclusion rule is strictly better: it avoids hairball graphs where pure type-affinity creates phantom edges. Preserve. | — | — |
| Louvain community detection | `wiki-graph.ts:53` — `louvain(g,{resolution:1})`; community palette 12 colors; community renumbered by size | Not yet implemented. `backend/app/graph/engine.py` does not call Louvain; edges table has no community column | ❌ | Louvain community detection is entirely absent. Users cannot see community coloring. Gap: add `python-igraph` `community_multilevel()` or `community_leiden()` call in `GraphEngine.recompute()`; persist community_id per node; expose in GET /graph nodes; render in GraphViewer via community palette. | P0 | [BE]/[FE] |
| Community cohesion score + warning marker (<0.15) | `wiki-graph.ts:78-88` — `intraEdges/possibleEdges`; warning if <0.15 | Absent. No cohesion metric computed. | ❌ | P1 after Louvain lands. | P1 | [BE] |
| Graph insights — surprising connections | `graph-insights.ts:31-102` — composite score, threshold ≥3, excludes index/log/overview nodes | `frontend/src/components/graph/graphInsights.ts` — cross-community edges, `weight>=3`, top 8, meta nodes (index/log/overview) excluded | ✅ | Done (G-P1-5). | — | [FE] |
| Graph insights — knowledge gaps (isolated / sparse community / bridge nodes) | `graph-insights.ts:141-179` — isolated (deg≤1), sparse (cohesion<0.15 & ≥3 nodes), bridge (≥3 communities) | `graphInsights.ts` — isolated (deg≤1), sparse (cohesion<0.15 & size≥3), bridge (≥3 distinct neighbor communities, own+unassigned excluded) | ✅ | Done (G-P1-5). | — | [FE] |
| Graph insights dismissable + click-to-highlight | `graph-view.tsx:800-805` — dismissable; `:1516` — click highlights node in graph | `GraphInsightsPanel.tsx` — per-item dismiss (local Set); row click → `setSelectedNodeId(primaryNodeId)` (GraphViewer highlights) | ✅ | Done (G-P1-5). | — | [FE] |
| Deep Research button from knowledge gap | `graph-view.tsx:1577-1588` — gap insight has "Deep Research" button that reads overview+purpose | `GraphInsightsPanel.tsx` — gap rows show a Deep Research button → `setActiveSection("deep-search")` | 🟡 | Button present + navigates (G-P1-5). Topic-seeding of the deep-research input deferred: `researchStore` has no prefill action. Small follow-up (P2) — add a seed action + wire `item.topic`. | P2 | [FE] |
| sigma.js WebGL rendering with type coloring | `graph-view.tsx:203-208` — color by type/community; size √ | `frontend/src/components/GraphViewer.tsx:1-35` — sigma.js WebGL; type color palette; node size from degree | ✅ | Core rendering present. | — | — |
| Hover neighbor-highlight | `graph-view.tsx:557-562`, `:480-527` | `frontend/src/components/GraphViewer.tsx` — hover handling present | ✅ | | — | — |
| Zoom in/out/fit | `graph-view.tsx:584-623` | `frontend/src/components/GraphViewer.tsx` — zoom controls | ✅ | | — | — |
| Position cache / anti-jump on re-layout | `graph-view.tsx:301-302`, `:322-338` — module-level positionCache | `backend/app/models.py:207-213` — `pages.x / pages.y` columns (FA2 layout coords, ADR-0013); `pages.pinned` flag (Feature A); coords persisted across recomputes | ⭐ | Synapse persists positions SERVER-SIDE in Postgres. No client-side positionCache needed. Pinned nodes preserved across recomputes (Feature A). Strictly better than llm_wiki's ephemeral positionCache. | — | — |
| Edge label (relevance score on hover) | `renderEdgeLabels:false` — llm_wiki README claims this exists but **it is absent** (01-AUDIT §F4 inaccuracy 3) | Absent in Synapse too. | ⛔ | llm_wiki's own audit says this is NOT implemented (renderEdgeLabels=false). Do not add it as a gap — it is an llm_wiki unimplemented claim, not a behavioral reference. If desired, treat as a Synapse enhancement (P2). | P2 | [FE] |
| Edge color coding (strength by alpha) | llm_wiki: monochrome slate-500 + alpha variation (README "green=strong" is wrong) | `frontend/src/api/graphTransform.ts` — edge size `0.5 + normW·3.5` (0.5–4px) + weight-proportional color ramp (link gray `#dde0e4→#7c8598`, source blue-gray `#d8dff0→#7d90bf`); baked RGB since sigma v3 ignores edge alpha on white | ✅ | Done (2026-07-01). Thicker + darker = stronger edge, matching llm_wiki's opacity-by-weight cue. | — | [FE] |
| FA2 layout server-side, never main-thread | llm_wiki defect: FA2 blocks main thread for graphs <220 nodes (01-CODE-UI §P-2) | `backend/app/graph/engine.py` — **ForceAtlas2 server-side** via `fa2_modified` (gravity 1, strongGravity, scalingRatio 2–3, Barnes-Hut, iterations 140→28; determinism via circle-init + numpy seed; disc-compression removed) — ADR-0045; `frontend/src/components/GraphViewer.tsx` — FORBIDDEN to import any layout algo | ⭐ | Best of both: Synapse now runs the SAME ForceAtlas2 algorithm as llm_wiki (organic clustered look) but **server-side + cached** (I2), so it never blocks the UI main thread at any scale — llm_wiki's P-2 defect stays eliminated. Switched FR→FA2 on 2026-07-01. | — | — |
| Full graph rebuild on every dataVersion (defect) | llm_wiki: rebuilds ALL .md files on each dataVersion (01-CODE-UI §P-1) | `backend/app/graph/cache.py:39-60` — debounce-based; `backend/app/graph/engine.py` — reads only pages+links tables (no vault filesystem walk, I1); debounced recompute via `GraphCache.notify_bump()` | ⭐ | Synapse avoids the full-rescan defect. GraphEngine reads Postgres, not the filesystem. Debounced to avoid burst rebuilds. | — | — |

**Invariants touched:** I1, I2, I4.

---

## 5. F5 — Knowledge Graph: Query retrieval pipeline (4-phase)

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| Phase 1 — vector / tokenized search | `search.rs:488-520` — Rust tokenizer; RRF with LanceDB; bonus: FILENAME_EXACT=200, PHRASE_IN_TITLE=50 | `backend/app/rag/retrieval.py:1-45` — Phase 1: bge-m3 dense vector (Qdrant); lexical fallback when `EMBEDDINGS_ENABLED=false` (ILIKE, ADR-0030) | ✅ | Synapse uses better vector search (bge-m3 vs LanceDB). Lexical fallback available. | — | — |
| Phase 2 — graph expansion (1-hop, NOT 2-hop as README claims) | `graph-relevance.ts:289-308` — strictly 1-hop (audit confirms "2-hop+decay" in README is FALSE) | `backend/app/rag/retrieval.py:19-22` — BFS over edges table, `expansion_depth ≤ 2` (HARD cap), ordered by weight DESC | ⭐ | Synapse correctly implements BFS with depth ≤ 2. The audit says llm_wiki is 1-hop. Synapse already does more. Preserve. | — | — |
| Phase 3 — token budget (actual split) | `context-budget.ts:54-59` — real split: PAGE=50%, INDEX=5%, RESPONSE_RESERVE=15%, ~30% headroom (NOT 60/20/5/15 as README claims) | `backend/app/rag/retrieval.py:69` — `_RETRIEVAL_BUDGET_FRACTION = 0.20` (the "retrieved" slice of 60/20/5/15); `backend/app/chat/context.py` — full budget split | 🟡 | Synapse's budget fractions (20% retrieval slice from the 60/20/5/15 split) differs from llm_wiki's real split (50% pages, not 60%). Neither matches their own README. The audit warns: use CODE values, not README. The functional parity question is: does Synapse allocate a reasonable budget to retrieved context? 20% of context_window is reasonable; the exact numbers are an implementation policy. No user-visible behavioral gap, but document the split clearly (D6). | P2 | — |
| Phase 4 — context assembly + [n] citations | `chat-agent.ts:1479-1517` — `<context id="n">` blocks, inline `[n]` markers, trailer `<!-- cited: 1,3 -->` | `backend/app/rag/retrieval.py:assembly` — numbered citation map; `backend/app/chat/stream.py:17-20` — `citations` list in `done` event; `frontend/src/components/chat/MarkdownView.tsx:14-16` — `decorateCitations` wraps `[n]` in `<sup>` | ✅ | Full citation pipeline present: retrieve → assemble → stream → decorate in UI. | — | — |
| Search restricted to wiki/ only (NOT raw/sources/) | `search.rs:151-152` — searches only `wiki/`; README claim "AND raw/sources/" is FALSE (01-AUDIT §F5) | `backend/app/rag/retrieval.py` — Qdrant search over pages (which includes both wiki and raw/sources entries embedded) | 🟡 | Synapse embeds ALL ingested pages (wiki/ and raw/sources/) in the same Qdrant collection. llm_wiki intentionally restricts to wiki/. This may surface raw source content in citations. Evaluate whether filtering to wiki/ pages only is desirable for citation quality. | P1 | [BE] |
| Agentic / LLM-router query (tool-calling loop) | `chat-agent.ts` — LLM router decides which tools to call (wiki_search, graph_search, external_search…); not a linear 4-phase pipeline | `backend/app/rag/retrieval.py` + `backend/app/chat/stream.py` — DETERMINISTIC 4-phase pipeline (no LLM router in retrieval itself); provider.chat() gets the assembled context | ⭐ | Synapse's deterministic retrieval + single chat() call is more cost-predictable and avoids tool-calling variability. The user-facing result (cited chat answer) is equivalent. For CLI (CliAgentProvider), the CLI agent can itself use the MCP search_wiki tool for more agentic behavior. This is intentionally NOT mirrored. | — | — |

---

## 6. F6 — Multi-conversation chat + persistency + Regenerate + Save-to-Wiki

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| Multi-conversation list (create/rename/delete/active) | `chat-store.ts:108-145`; persistent `.llm-wiki/chats/{id}.json`; auto-title from first 50 chars | `backend/app/models.py:706-776` — `conversations` table; `frontend/src/store/chatStore.ts` — ConversationSummary list; `frontend/src/components/chat/ConversationList.tsx` | ✅ | Multi-conversation persistency to Postgres. | — | — |
| Persistent cited-references per message | `chat-store.ts:40`,`:202-220` — refs persisted in message data; collapsible refs panel | `backend/app/models.py:819-825` — `citations` JSONB column on `messages`; `frontend/src/components/chat/MarkdownView.tsx:14-16` — citation decorateCitations | ✅ | Citations persisted per message. | — | — |
| History depth control (default 10 messages) | `chat-store.ts:103` — `maxHistoryMessages` default 10; `.slice(-N)` in chat-panel | `frontend/src/components/chat/ChatSection.tsx:61-70` — `buildMessagePayload(allMessages, historyLength)`; `frontend/src/store/settingsStore.ts` — `CONV_HISTORY_OPTIONS` | ✅ | Configurable history depth present. | — | — |
| Regenerate (remove last assistant + re-send last user, including images) | `chat-panel.tsx:331-356` — removes last-assistant + last-user, re-sends with images; `setTimeout(50)` hack | `frontend/src/components/chat/ChatSection.tsx` — Regenerate wired to re-POST with `regenerate:true` (AC-F6-4); `backend/app/chat/stream.py` — handles `regenerate` flag | ✅ | Regenerate present. Synapse avoids the 50ms setTimeout hack. | — | — |
| Save-to-Wiki (query answer → wiki/queries/) | `chat-message.tsx:236-309` — writes `wiki/queries/`, updates index.md/log.md, bumps dataVersion, then autoIngest | `backend/app/chat/stream.py` / REST endpoint — no explicit "Save to Wiki" endpoint visible in main.py docstring | ❌ | "Save to Wiki" is listed in CLAUDE.md §4b F6 but no corresponding `POST /chat/messages/{id}/save-to-wiki` endpoint appears in `main.py:lines 1-65` docstring or in the existing endpoints list. The feature is planned (F6) but absent in the current API surface. Gap: implement endpoint that writes the assistant message to `wiki/queries/<slug>.md` with `origin: query` frontmatter, calls `ingest_file`, updates index.md. | P0 | [BE]/[FE] |

**Invariants touched:** I1, I3, I5, I7.

---

## 7. F7 — Thinking / reasoning `<think>` display

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| `<think>…</think>` split on streaming | `separateThinking` — regex, handles multiple blocks + unclosed tag in stream | `backend/app/chat/think.py` — `ThinkScanner` 2-state machine, partial-tag safe; split server-side to NDJSON events `{type:"token"}` / `{type:"think"}` | ⭐ | Synapse splits on the SERVER. No client-side tag parsing (I3). More robust than regex. | — | — |
| Collapsed by default | `ThinkingBlock:1083-1107` — collapsed-by-default in UI | `frontend/src/components/chat/ThinkBlock.tsx:23` — `useState(false)` (collapsed default); AC-F7-1 | ✅ | Collapsed by default confirmed. | — | — |
| Streaming "roll" / live preview while inside think block | `StreamingThinkingBlock:1055-1080` — 5-line roll with opacity fade | `frontend/src/components/chat/ThinkBlock.tsx:79-87` — shows "Thinking…" label when `streaming && !open`; no 5-line rolling preview | 🟡 | Synapse shows "Thinking…" indicator during streaming but does NOT render a scrolling live preview of the think text. The roll/fade UX is absent. Gap: in ThinkBlock, when `streaming=true`, render the last N lines of `content` with fade, or at minimum an animated "..." indicator. | P1 | [FE] |
| Multi-provider reasoning field routing (DeepSeek/Kimi/Qwen/Anthropic/Gemini) | `reasoning-detector.ts:50-87` — detects `reasoning_content`, `reasoning`, `thinking_delta`, `thought` fields per-provider | `backend/app/ingest/provider/api.py` — ApiProvider streaming; `backend/app/ingest/provider/cli.py` — CliAgentProvider | 🟡 | Verify ApiProvider/OllamaProvider streaming handlers correctly extract all vendor-specific reasoning fields and emit them as `think` events. The ThinkScanner assumes `<think>` tag syntax; providers that use separate JSON fields (DeepSeek reasoning_content) may not emit think events at all. | P1 | [AI] |
| Retry with reasoning off on reasoning-only error | `chat-panel.tsx:286-296` — single retry with `reasoning:{mode:"off"}` | Not confirmed in Synapse. | 🟡 | No reasoning-off fallback visible in `backend/app/chat/stream.py`. If a provider returns only a reasoning block (no visible text), the current path may produce an empty assistant message. | P1 | [BE] |
| Raw message stored un-mutated (think tags preserved) | `chat-store.ts:40` — content stored RAW (AC-F7-2) | `backend/app/models.py:813-817` — `content` stored "RAW un-mutated, incl. literal <think>…</think>" (AC-F7-2 documented in model) | ✅ | | — | — |

**Invariants touched:** I3, I7.

---

## 8. F8 — LaTeX → Unicode

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| LaTeX → Unicode conversion (100+ symbols) | `latex-to-unicode.ts` — 168 entries (~145 distinct glyphs); applied outside math blocks | `frontend/src/components/chat/latexToUnicode.ts` — ~145 glyphs (Greek, operators, arrows, relations) + super/subscript ranges; wired into `renderMarkdown.ts` pipeline | ✅ | Already implemented (roadmap was stale). Inline `$…$` / `\(…\)` converted to Unicode; applied to both chat (MarkdownView) and wiki (NoteView). | — | [FE] |
| KaTeX rendering for display math | `chat-message.tsx:5-7` — imports `remarkMath`+`rehypeKatex`+CSS; applied to ReactMarkdown | `frontend/src/components/chat/renderMarkdown.ts:extractDisplayMath/injectDisplayMath` — `$$…$$` and `\[…\]` extracted before Unicode pass (raw LaTeX preserved), rendered via `katex.renderToString(displayMode, throwOnError:false, trust:false)`, injected after DOMPurify; `katex/dist/katex.min.css` imported | ✅ | Done (G-P1-2, 2026-07-01). ADR-0019 §2.6 amended: display math now KaTeX-rendered (was fenced `\`\`\`math\`); inline math stays Unicode-only. Fallback to fenced code block on KaTeX failure — never dropped (AC-F8-3 preserved). Tests: `renderMarkdownMath.test.ts`. | — | [FE] |
| Auto-wrap bare LaTeX environments with $$ | `chat-message.tsx:1123-1126` — wraps `\begin{…}\end{…}` bare with `$$` | Absent — bare `\begin{env}…\end{env}` not auto-wrapped; KaTeX handles environments only inside `$$…$$` / `\[…\]`. | 🟡 | Minor: an LLM emitting a bare `\begin{aligned}…\end{aligned}` without `$$` fences won't render. Low incidence; wrap-detection is a small follow-up. | P2 | [FE] |

**Invariants touched:** I3.

---

## 9. F9 — Review System (async HITL, Create / Deep Research / Skip)

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| Review items generated at ingest time | `ingest.ts:1840` — `---REVIEW:…---END REVIEW---` blocks parsed and pushed to store | `backend/app/ops/review.py:propose_reviews` — orchestrated route calls `propose_reviews()` post-write; `backend/app/ingest/orchestrator.py:436-466` — fire-and-forget hook after each ingest | ✅ | Review proposals generated post-ingest (proposal model, ADR-0034). | — | — |
| 5 proposal types (missing-page/suggestion/contradiction/duplicate/confirm) | `review-store.ts:11` — 5 types enum | `backend/app/ops/review.py:59-63` — `_VALID_ITEM_TYPES = {"missing-page","suggestion","contradiction","duplicate","confirm"}` | ✅ | Type set matches exactly. | — | — |
| Pre-generated search queries at ingest time | `ingest.ts:2178` — `SEARCH:` line in prompt; `searchQueries` array persisted per review item | `backend/app/models.py` — `ReviewItem` — no `search_queries` column in the schema documentation (models.py:1404-1462) | ❌ | Synapse's ReviewItem schema does NOT include a `search_queries` array. llm_wiki generates pre-computed queries to power the "Deep Research from review" action. Gap: add `search_queries JSONB` to ReviewItem; populate during `propose_reviews` LLM call; pass to `run_deep_research` when the user triggers Deep Research from a review item. | P1 | [BE]/[AI] |
| Sweep auto-resolve (Pass-1 rule-based + Pass-2 LLM, bounded) | `sweep-reviews.ts:362-396` — Pass-1 rule-based; Pass-2 LLM `JUDGE_BATCH_SIZE=40/MAX_JUDGE_BATCHES=5/MAX_PAGES_IN_PROMPT=300`; race guards | `backend/app/ops/review.py:sweep_reviews` — Pass-1 + Pass-2 bounded LLM sweep; triggered fire-and-forget in orchestrator | ✅ | Bounded 2-pass sweep present. | — | — |
| FNV-1a content-derived idempotency key (dedup re-ingest) | `review-store.ts:49-58` — FNV-1a based `id` for stable resolved state | `backend/app/ops/review.py:83-100` — `_fnv1a_16hex()` — same algorithm; `enqueue_review` upserts on `(vault_id, content_key)` for dedup (ADR-0044 §3) | ✅ | Exact algorithm match. | — | — |
| Create action — lazy on-demand page generation | `review-view.tsx:476-548` — Create triggers `queueCreate`; later: generate + write page | `backend/app/ops/review.py:create_page_from_review` — lazy generation via `_run_generation()` (bounded `run_orchestrated_loop`); `POST /review/queue/{id}/create` endpoint in main.py:41 | ✅ | Lazy on-demand create present. | — | — |
| Review item dismissible (distinct from Skip) | `review-store.ts` — ADR-0044: `dismissed` status distinct from `skipped` | `backend/app/ops/review.py:63` — `_VALID_STATUSES` includes `dismissed`; `backend/app/main.py` — no dismiss endpoint visible in docstring | 🟡 | Backend status enum includes "dismissed" but no `POST /review/queue/{id}/dismiss` endpoint visible in main.py docstring. Verify: if the endpoint exists in the route handlers below line 65, it is fine. If absent, add the dismiss endpoint. | P1 | [BE] |
| Review queue UI panel with Create/Deep Research/Skip | `review-view.tsx` — full UI panel | `frontend/src/components/review/ReviewQueueView.tsx` — exists | ✅ | Review UI panel present. | — | — |

**Invariants touched:** I6, I7.

---

## 10. F10 — Deep Research loop

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| SearXNG as web search backend | llm_wiki supports 6 providers (Tavily, SerpApi, SearXNG, Brave, Firecrawl, Ollama) | `backend/app/ops/searxng.py` — SearXNG ONLY (I9: "ALL web search goes through ops/searxng.py"); `deep_research.py:15` — SEARXNG_URL enforced | ⭐ | Synapse's SearXNG-only approach is intentional (I9, R8). The multi-provider complexity of llm_wiki is deliberately avoided. Preserve. | — | — |
| Concurrency = 3 for parallel research tasks | `research-store.ts:33` — `maxConcurrent:3` | `backend/app/ops/deep_research.py:3,7-9` — `concurrency=3 HARDCODED module constant`; shared with `ops/searxng.py`; architect ADR required to change | ✅ | Exact parity. | — | — |
| Bounded loop (max_iter + token_budget) | `research-store.ts` — `MAX_RESEARCH_SOURCES=20`; no explicit token_budget / cost cap per loop (01-AUDIT §F10 bug) | `backend/app/ops/deep_research.py:1-21` — `for iteration in range(1, max_iter+1)`, `token_budget` checked at top of each round; bounds FROZEN at INSERT; $1 cost anomaly threshold | ⭐ | Synapse has both max_iter AND token_budget bounds (I7). llm_wiki only has MAX_RESEARCH_SOURCES=20 without a token budget — this is the audit's defect. Synapse is strictly better. | — | — |
| Multi-query per topic, URL dedup | `deep-research.ts:118-125` — multiple queries per topic, dedup by URL | `backend/app/ops/deep_research.py` — multiple queries via `searxng_search_many()`, URL-level dedup | ✅ | | — | — |
| Topic LLM-optimization reading overview+purpose | `optimize-research-topic.ts:14`,`:27-30` — LLM call to rephrase topic using overview+purpose context | `backend/app/ops/deep_research.py` — query_gen phase; vault context loaded via `_load_vault_context()` equivalent | 🟡 | Verify query generation in `deep_research.py` includes an explicit "optimize topic" pre-step that reads overview+purpose to produce better search queries. If the query generation prompt does not include overview content, the research will miss vault-specific context. | P1 | [AI] |
| Synthesis saved to wiki/queries/ with origin:deep-research | `deep-research.ts:305-324` — synthesis to `wiki/queries/` with `origin:deep-research` frontmatter | `backend/app/ops/deep_research.py:18-19` — "synthesis is raw source material → written to vault/raw/sources/ → ingest_file()" per I1/I5 docstring | 🟡 | Synapse writes synthesis to `raw/sources/` and lets ingest handle classification (design choice per ADR-0024/I1/I5). llm_wiki writes directly to `wiki/queries/`. The user-facing result SHOULD be the same (ingested synthesis lands in wiki/queries/ after the orchestrated loop), but the path is different. Verify that the synthesis output from a deep research run is actually tagged `type: synthesis` or `type: query` by the ingest provider prompt, and that it lands in the right subdir. | P1 | [BE]/[AI] |
| Per-token streaming progress | `deep-research.ts:265-269` — `<think>` collapsible + per-token streaming in research panel | `frontend/src/components/research/DeepSearchView.tsx` — exists | 🟡 | Verify research panel shows per-token streaming progress. If only polling the run status, UX is noticeably worse than llm_wiki's live token stream. | P1 | [FE] |
| Auto-ingest after synthesis + visible failure | `deep-research.ts:339-343` — `autoIngest` fire-and-forget, `.catch(console.error)` only (01-AUDIT B-2: invisible failure) | `backend/app/ops/deep_research.py` — synthesis triggers `ingest_file()` which returns `IngestResult`; run row updated with `synthesis_page_id` | ⭐ | Synapse surfaces the ingest result in the run row (`synthesis_page_id`). The llm_wiki fire-and-forget bug is NOT present. | — | — |
| "Full content extraction" (llm_wiki claim is FALSE) | Audit: snippet-only; no Readability page fetch in deep research path | `backend/app/ops/deep_research.py:_fetch_max_chars()` — fetched content capped at `DEEP_RESEARCH_FETCH_MAX_CHARS` (configurable, default 20k chars); actual HTTP fetch per URL | 🟡 | Synapse fetches actual page content (up to 20k chars) per URL — this is BETTER than llm_wiki's snippet-only approach. Verify the HTTP fetch step in `deep_research.py` uses a Readability-like extraction (html2text / trafilatura / plain HTML strip) rather than raw HTML bytes. If raw HTML, the model receives noisy content. | P1 | [BE] |

**Invariants touched:** I7, I9.

---

## 11. F11 — Browser Extension (Web Clipper)

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| Chrome MV3 extension with Readability + Turndown | `popup.js:94-107` — Readability + Turndown in-page | CLAUDE.md §4b F11 — "Chrome MV3 web clipper (Readability+Turndown → local API → auto-ingest)"; `backend/app/main.py:49,52` — `/clip/config` + `POST /clip` endpoints | 🟡 | Backend clip endpoint exists (`POST /clip` in main.py:52, ADR-0040). Chrome extension itself is not yet confirmed as shipped — no `extension/` directory found in the Synapse repo. Verify extension files exist; if not, this is a partial gap. | P1 | [FE]/[BE] |
| Authenticated clip server with token (NOT unauthenticated) | llm_wiki bug: **completely unauthenticated** clip server `:19827` (01-AUDIT §F11 — Critical S-1/S-2/S-3) | `backend/app/models.py:346-380` — `vault_state.clip_access_token` + `clip_enabled_db` + `clip_allowed_origins_db` (ADR-0040); `backend/app/main.py:49` — `PUT /clip/config` + `GET /clip/config` | ⭐ | Synapse ALREADY implements authentication for the clip endpoint (token-based, origin allowlist, ADR-0040). The most critical llm_wiki security defect is pre-empted. | — | — |
| Body cap on clip POST | llm_wiki bug: unlimited body (01-CODE-UI §S-5) | FastAPI middleware / request size limit — verify body cap is set on `POST /clip` | 🟡 | Confirm Synapse's `POST /clip` enforces a body size cap (equivalent to llm_wiki's `MAX_BODY_BYTES`). If relying solely on FastAPI defaults (no explicit cap), add a `max_body_size` middleware or `Content-Length` check. | P1 | [BE] |
| Origin allowlist (reject drive-by writes) | llm_wiki defect: Origin used only for response headers, not enforcement (01-AUDIT §S-3) | `backend/app/models.py:371-379` — `clip_allowed_origins_db`; ADR-0040 — origin allowlist enforced | ⭐ | Origin allowlist enforced in Synapse by design. | — | — |
| path traversal protection on clip write | llm_wiki defect: `project_path` from body used in `Path::new()` without safe_join (01-AUDIT §S-2) | `backend/app/upload.py:safe_source_name` + `resolve_under_sources` — sanitization + containment-check | ⭐ | Synapse uses `safe_source_name` + `resolve_under_sources` — equivalent to llm_wiki's `safe_join` which it was missing. | — | — |
| Project picker (list projects) | `popup.js:55-69` — `GET /projects` returns project list | No `/projects` endpoint in Synapse (single vault per instance). Extension project selector would need to be simplified or removed. | 🟡 | Synapse is single-vault per deployment (VAULT_ID env). The extension need not offer a project picker; it can auto-target the active vault. Needs explicit UX decision and extension code adjustment. | P1 | [FE] |
| Auto-ingest clip immediately (polling watcher) | `clip-watcher.ts:39-43` — enqueueIngest after clip lands; polling 3s | `backend/app/watcher.py` — watchdog detects new file in raw/sources/ and triggers ingest automatically | ✅ | Synapse's watchdog ingest path is equivalent and more robust (event-driven vs polling). | — | — |

**Invariants touched:** I1, I5.

---

## 12. F12 — Multi-format document ingestion

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| PDF text extraction | `pdfium-render 0.9` Rust FFI (01-AUDIT §F12) | `backend/app/ingest/extract.py:PDF` — `pypdf` (Python); dispatched on `.pdf` extension | ✅ | Both extract PDF text. Synapse uses pypdf (pure Python); llm_wiki uses pdfium (faster, better layout). Acceptable for M5 scope. | — | — |
| DOCX extraction | `docx-rs 0.4.20` — headings/bold/italic/lists/tables | `backend/app/ingest/extract.py:DOCX` — `python-docx` | ✅ | | — | — |
| PPTX extraction | ZIP+XML slide-by-slide (`fs.rs:786-845`) | `backend/app/ingest/extract.py:PPTX` — `python-pptx` | ✅ | | — | — |
| XLSX extraction (multi-sheet, GFM tables) | `calamine 0.34` — multi-sheet, cell-type | `backend/app/ingest/extract.py:XLSX` — `openpyxl` | ✅ | | — | — |
| Image ingestion with vision caption | `extract_images.rs:204/345/758` — extracts images from PDF (min 100px, max 500/doc); `vision-caption.ts` — vision LLM with SHA256 cache | `backend/app/ingest/extract.py:64-67` — `PLACEHOLDER_EXTENSIONS` for images; `backend/app/upload.py:_PLACEHOLDER_EXTENSIONS` — placeholder text only | ❌ | Synapse v0.5 produces a placeholder for image files — no vision captioning, no image extraction from PDFs. llm_wiki has a full vision pipeline. Gap: add vision caption step via an InferenceProvider chat() call (I6) for image files; cache captions by file SHA256. | P2 | [AI]/[BE] |
| MinerU PDF extraction with fallback to pdfium | `ingest.ts:678-685` — tries MinerU, falls back to pdfium; abort not swallowed | Absent — Synapse uses pypdf only, no MinerU. | ❌ | MinerU for high-quality PDF extraction is a llm_wiki differentiator. Not yet planned for Synapse. P2 enhancement opportunity. | P2 | [BE] |
| File caching (mtime-based, avoid re-extraction) | `fs.rs:147-172` — mtime-based cache for format extraction | `backend/app/ingest/orchestrator.py:129-146` — mtime-then-hash gate (ADR-0001) gates the ENTIRE ingest including extraction | ✅ | Extraction is gated by the mtime-then-hash check at the ingest level. No separate extraction-only cache needed. | — | — |

---

## 13. F13 — Cascade deletion (3-method matching + shared-entity preservation)

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| 3-method source-to-page matching | (1) filename in frontmatter sources; (2) source-summary page name; (3) full-text YAML scan | `backend/app/ops/cascade_delete.py:51-55` — uses links back-reference index (method 1); bounded full-text fallback `CASCADE_FULLTEXT_MAX_FILES` (method 3); source-summary detection via path convention | ✅ | Equivalent 3-method approach, backed by links table (faster than llm_wiki's Rust FS scan). | — | — |
| Shared-entity preservation (sources[] rewrite) | `source-delete-decision.ts:33` — survivors>0 → keep + rewrite sources[]; =0 → delete | `backend/app/ops/cascade_delete.py:27-28` — "Never delete a shared page whose sources[] retains another entry." (Do-NOT #6); sources[] rewrite path | ✅ | Shared-entity preservation by design. | — | — |
| index.md cleanup (structural, not substring) | `wiki-cleanup.ts:cleanIndexListing:98` — structural parse, not substring replace | `backend/app/ops/cascade_delete.py` — cleanup referenced via invariant I5; python-frontmatter round-trip | ✅ | | — | — |
| Dead wikilink replacement (→ plain text) | `wiki-cleanup.ts:stripDeletedWikilinks:123` — `[[deleted|alias]]` → `alias` | `backend/app/ops/cascade_delete.py:56,CASCADE_DEAD_LINK_STYLE="plain"` — dead links replaced with plain text; regex anchored | ✅ | Behavioral parity. | — | — |
| Cascade-delete dry-run plan (preview) | No explicit dry-run in llm_wiki; `collect_related_pages` is the implicit plan | `backend/app/ops/cascade_delete.py:plan_cascade_delete` + `POST /pages/{id}/cascade-delete/preview` — mandatory dry-run endpoint (Do-NOT #7) | ⭐ | Synapse adds a dry-run preview endpoint absent from llm_wiki. Strictly better. | — | — |
| Embeddings cleanup (Qdrant point removed) | `removePageEmbedding` in cleanup | `backend/app/ops/cascade_delete.py` — calls `qdrant_client.delete_point` | ✅ | | — | — |
| raw/sources/ file deletion | `wiki-page-delete.ts:93-106` — also deletes `wiki/media/<slug>/` | `backend/app/ops/cascade_delete.py:9` — "Never leave the raw/sources/ file on disk" (Do-NOT #9); enforced | ✅ | | — | — |

**Invariants touched:** I1, I2, I5, I7.

---

## 14. F14 — Configurable context window (4K–1M)

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| Discrete context window selector | `context-size-selector.tsx:1-12` — 4K/8K/…/1M | `frontend/src/store/settingsStore.ts:CONTEXT_WINDOW_OPTIONS` — context window options; `frontend/src/components/settings/SettingsPanel.tsx:26-36` — F14 in General section | ✅ | Discrete presets present. | — | — |
| Budget split display (label vs allocator mismatch) | llm_wiki bug: label shows 60% but allocator gives 55% (01-AUDIT §F14) | `frontend/src/store/settingsStore.ts:computeBudgetSplit` — budget split computed from actual fractions | ✅ | Synapse computes the budget split programmatically from the actual fractions (no hardcoded label mismatch). | — | — |

---

## 15. F15 — Cross-platform (normalizePath, unicode-safe, CI)

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| Path normalization (single consistent impl) | `path-utils.ts:5` — one `normalizePath()` referenced in 51 files; Rust has 3 divergent impls (01-CODE-UI §Q-Rust-1) | `backend/app/ingest/orchestrator.py:1342-1352` — `_relative_path()` uses `path.resolve().relative_to(vault_root)` (single impl); `backend/app/ops/cascade_delete.py:_slugify` | ✅ | Single Python path utility. No divergent impls. | — | — |
| Unicode-safe slug / filename handling | `wiki-filename.ts:44` — `Array.from(slug).slice(0,50).join("")` | `backend/app/ingest/orchestrator.py:1169-1175` — `_slugify()` via regex on lowercased title; no char-boundary issue in Python | ✅ | Python string slice is unicode-safe by default. | — | — |
| Multi-OS CI | llm_wiki CI: 4 OS/arch matrix (but doesn't run tests! — 01-CODE-UI §Q-1) | `sprint/v0.6` — pytest + vitest configured; no multi-OS matrix confirmed in CI config | 🟡 | Synapse needs a CI matrix running tests on macOS/Linux (TrueNAS/Docker target). Not blocking for server deployment (always Linux), but important for correctness gate. Tests should run in CI (the llm_wiki defect we should NOT mirror). | P1 | [DevOps] |
| PWA + Tauri v2 packaging | llm_wiki: Tauri v2 Rust app; CI bundles dmg/deb/AppImage/msi | Synapse sprint-6 goal; not yet shipped | 🟡 | F15 target, tracked separately. | P2 | [FE] |

---

## 16. F16 — i18n / settings persistence / multi-provider / timeout / dataVersion

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| i18n (EN + IT) | llm_wiki: EN+ZH+JA+KO; `i18n-parity.test.ts` validates 673-key parity | `frontend/src/` — react-i18next; EN+IT (`en.json`, `it.json`) per CLAUDE.md; `frontend/src/store/settingsStore.ts` — language selector | 🟡 | EN+IT present. Need key-count parity test (equivalent to `i18n-parity.test.ts`). Without it, IT translation may drift. | P1 | [FE] |
| Settings persistence across sessions | llm_wiki: Tauri Store plugin (`project-store.ts:1,13`) | `frontend/src/store/settingsStore.ts` — Zustand store; persistence method (localStorage / Tauri Store) should be confirmed | 🟡 | Verify settings persist across browser refresh (localStorage write or indexedDB). If in-memory only, they reset on refresh. | P1 | [FE] |
| `.obsidian/` auto-generated | `project.rs:196-233` — Rust generates `app.json`/`appearance.json`/`core-plugins.json` | `backend/app/vault.py:40-46` — `_OBSIDIAN_APP_JSON` generated at bootstrap (AC-K7-1) | ✅ | `.obsidian/app.json` auto-generated on startup. | — | — |
| GFM rendering | `remark-gfm` in 4 renderers | `frontend/src/components/chat/MarkdownView.tsx` — uses remark-gfm | ✅ | | — | — |
| Multi-provider chat (selectable per operation) | llm_wiki: 7 providers (OpenAI/Anthropic/Google/Ollama/Azure/MiniMax/Custom) + CLI transport | `backend/app/models.py:453-555` — `provider_config` table; `frontend/src/components/provider/ProviderSelector.tsx` — UI selector; 3 backends (Local/API/CLI) as per F17 | ✅ | Synapse's 3 backend abstraction (Local/API/CLI) maps cleanly. The API backend can point to any OpenAI-compatible endpoint. | — | — |
| Timeout (configurable, per provider) | llm_wiki: 30 min hardcoded (`llm-client.ts:96`) | `backend/app/chat/stream.py:57` — `DEFAULT_CHAT_TIMEOUT_SECONDS = 60.0`; per-`provider_config` timeout | ⭐ | Synapse: default 60s, configurable per provider_config row. Avoids llm_wiki's 30min hardcoded timeout (a resource risk). | — | — |
| dataVersion — monotonic counter for graph/tree refresh | `wiki-store.ts:385/439/567` — `dataVersion` consumed by graph/tree/chat | `backend/app/models.py:296-302` — `vault_state.data_version` bumped per successful ingest; `backend/app/ingest/orchestrator.py:1305-1332` — `bump_version()`; `frontend/src/store/graphStore.ts` — consumed by GraphViewer | ✅ | | — | — |

---

## 17. F17 — Inference Provider (pluggable, 3 backends)

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| InferenceProvider ABC with analyze/generate/chat/capabilities() | Not in llm_wiki (only Anthropic/OpenAI provider list) | `backend/app/ingest/provider/base.py` — `InferenceProvider` ABC; `analyze()`, `generate()`, `chat()`, `capabilities()` | ⭐ | Synapse's pluggable provider is the defining F17 feature, beyond llm_wiki. | — | — |
| Local/Ollama backend | llm_wiki: Ollama as one of its 7 providers | `backend/app/ingest/provider/ollama.py` — `OllamaProvider` | ✅ | | — | — |
| API/Anthropic + OpenAI-compatible backend | llm_wiki: Anthropic + OpenAI-compat | `backend/app/ingest/provider/api.py` — `ApiProvider` (Anthropic Messages API + OpenAI-compat `base_url`) | ✅ | | — | — |
| CLI/claude-agent-sdk backend | Not in llm_wiki | `backend/app/ingest/provider/cli.py` — `CliAgentProvider`; `delegate_ingest()` via claude-agent-sdk | ⭐ | Synapse-only feature. | — | — |
| Capability-aware routing (no isinstance branching) | N/A | `backend/app/ingest/orchestrator.py:355` — routes on `caps.supports_agentic_loop` ONLY; no isinstance/type/class-name branching | ✅ | I6 enforced. | — | — |
| Provider config stored in DB, not code | N/A | `backend/app/models.py:453-555` — `provider_config` table; model_id NEVER in code | ✅ | | — | — |
| Fallback provider (bounded to one attempt) | N/A | `backend/app/ingest/orchestrator.py:610-633` — single fallback attempt, `is_fallback=True` row | ✅ | I7 enforced. | — | — |
| UI provider selector | llm_wiki: settings UI with provider list | `frontend/src/components/provider/ProviderSelector.tsx` — exists; `frontend/src/components/settings/SettingsPanel.tsx` — LLM Models section | ✅ | | — | — |
| CLI OAuth token UI-settable (DB-stored, injected to subprocess) | Not in llm_wiki | `backend/app/models.py:382-399` — `vault_state.cli_oauth_token`; ADR-0043; `backend/app/cli_auth.py` | ⭐ | Sprint-6 feature, beyond llm_wiki. | — | — |

---

## 18. Knowledge / Files / Sources tabs

| Feature / sub-behavior | llm_wiki behavior (audit ref) | Synapse current state (code ref) | Verdict | Gap (exact, actionable) | Priority | Owner |
|---|---|---|---|---|---|---|
| Wiki/Sources navigation tree | `icon-sidebar.tsx` — Wiki tab + Sources tab showing file tree | `frontend/src/components/nav/NavTree.tsx` — file tree; NavRail sections: Wiki, Sources | ✅ | Navigation tabs present. | — | — |
| TanStack Virtual for large lists | Not in llm_wiki (no virtualization mentioned) | `frontend/src/components/chat/MessageList.tsx` / NavTree — check for TanStack Virtual import | 🟡 | CLAUDE.md §3 I4: "All long lists (tree, message history) are virtualized (TanStack Virtual)". Verify MessageList and NavTree use TanStack Virtual. If not, long message histories / large vaults will cause render lag. | P1 | [FE] |
| CodeMirror 6 editor (not WYSIWYG) | llm_wiki uses Milkdown (WYSIWYG/ProseMirror) | `frontend/src/components/wiki/CodeMirrorEditor.tsx` — CodeMirror 6 | ⭐ | Synapse uses CodeMirror 6 (I4 invariant) — no WYSIWYG, no ProseMirror. This avoids the llm_wiki bottleneck of WYSIWYG on long documents. Strictly better. | — | — |
| Onboarding / empty-states with provider gate | llm_wiki UX gap: `!hasUsableLlm` is a silent check, no blokcing UI gate (01-CODE-UI §UX-1) | `frontend/src/components/common/EmptyState.tsx` — EmptyState component exists | 🟡 | Verify IngestView and ChatSection show an explicit `EmptyState` with "Configure a provider to begin" CTA when no provider is configured. If the check is silent (like llm_wiki's bug), add the gate. | P0 | [FE] |

---

## 19. Karpathy K1–K8 core behaviors

| K-feature | llm_wiki behavior | Synapse current state | Verdict | Gap | Priority | Owner |
|---|---|---|---|---|---|---|
| K1 — 3-layer vault (raw/wiki/schema.md) | raw/ (immutable) → wiki/ (LLM-generated) → schema.md (rules) | `backend/app/vault.py:52-60` — bootstrap creates all 3 layers; I1 enforces immutability of raw/ | ✅ | | — | — |
| K2 — 3 operations (Ingest/Query/Lint) | `lib/lint.ts` — lint present; ingest + chat | Ingest: watcher + REST; Query: `/search` + `/chat/stream`; Lint: `backend/app/ops/lint.py` + `POST /lint/scan` | ✅ | All 3 present. | — | — |
| K3 — index.md catalogue | Updated at every ingest (content catalog) AND **fed to the LLM** so it links to existing pages | `backend/app/wiki/index.py:update_index` (write) + `orchestrator.py:_load_existing_pages_catalogue()` (2026-07-01) injects existing page titles grouped by type into the ingest context (analyze + delegate), token-bounded (I7), instructing exact-title `[[wikilinks]]` | ✅ | Fixed the cross-ingest fragmentation (was: catalogue updated but NOT fed back → LLM invented titles → 56% dangling links → isolated islands). Now the LLM reuses existing entities → connected web like llm_wiki. | — | [AI] |
| K4 — log.md append-only | Append-only log; parseable `## [date]` format | `backend/app/ingest/orchestrator.py:1284-1302` — `append_log()`: timestamp \| INDEXED \| path; never truncated (AC-K4-2) | ✅ | | — | — |
| K5 — `[[wikilink]]` syntax with dedicated parser | `wiki-graph.ts` / `graph-relevance.ts` — regex parser | `backend/app/wiki/links.py:parse_wikilinks` + `persist_links` (tolerant resolution 2026-07-01: exact→case-insensitive→slug, first-hit); `reresolve_dangling_links()` + `POST /links/reresolve` backfill; `frontend/src/components/wiki/NoteView.tsx` — wikilink render | ✅ | Resolution now catches near-miss titles (case/slug) → fewer dangling. Backfill endpoint re-resolves existing dangling links against current titles + bumps the graph, reconnecting the vault without re-ingest. | — | — |
| K6 — YAML frontmatter (type/title/sources[]/tags[]) | Frontmatter pivot for graph + cascade-delete | `backend/app/models.py:156-184` — `page_type`, `sources`, `tags` columns (JSONB); `backend/app/ingest/orchestrator.py:1389-1417` — tolerant parser | ✅ | `tags[]` added in migration 0018 (llm_wiki parity per model comment). | — | — |
| K7 — Obsidian compatibility | `wiki/` valid Obsidian vault; `.obsidian/` auto-generated | `backend/app/vault.py:40-46` — `.obsidian/app.json` auto-generated (AC-K7-1/2); I5 enforces valid frontmatter | ✅ | | — | — |
| K8 — Human curates, LLM maintains | Review queue (F9) + human-gated lint (K2 ADR-0037) | F9 review + lint human gate (Do-NOT: never auto-apply without human action) | ✅ | | — | — |
| schema.md co-evolution (LLM suggests schema updates) | llm_wiki: schema.md static after creation (01-AUDIT §Karpathy cross-check) | Synapse: same. schema.md is a static template set at vault bootstrap | ❌ | Neither codebase implements schema co-evolution. This is a genuine Karpathy principle gap. Low priority as it requires significant product design. | P2 | [AI]/[BE] |

---

## 20. Prioritized Gap-Closure Backlog

### Phase 0 — Correctness blockers (ship before any next sprint review)

| ID | Gap | Owner | Effort |
|---|---|---|---|
| G-P0-1 | **Save-to-Wiki endpoint missing.** `POST /chat/messages/{id}/save-to-wiki` — writes assistant message to `wiki/queries/<slug>.md`, calls `ingest_file`. F6 user story incomplete without it. | [BE]/[FE] | M |
| G-P0-2 | **Louvain community detection absent.** Add `community_multilevel()` in `GraphEngine.recompute()`; expose `community_id` per node in GET /graph; render community palette in GraphViewer. Core F4 sub-feature. | [BE]/[FE] | L |
| G-P0-3 | **Provider gate empty-state.** Verify `IngestView` and `ChatSection` block with explicit "Configure a provider" CTA when no `provider_config` row exists. If silent, implement (mirrors llm_wiki UX best practice, not its bug). | [FE] | S |

### Phase 1 — Significant UX/behavior gaps (current sprint P1)

| ID | Gap | Owner | Effort |
|---|---|---|---|
| ~~G-P1-1~~ | ✅ **DONE (was already implemented).** `latexToUnicode.ts` (~145 glyphs) wired into `renderMarkdown`. Roadmap entry was stale. | [FE] | — |
| ~~G-P1-2~~ | ✅ **DONE (2026-07-01).** KaTeX display-math rendering via `extractDisplayMath`/`injectDisplayMath` in `renderMarkdown.ts`; ADR-0019 amended; `renderMarkdownMath.test.ts`. Bare-environment auto-wrap deferred to P2. | [FE] | — |
| ~~G-P1-3~~ | ✅ **DONE (2026-07-01, ADR-0046).** Live ingest activity queue: running-row at ingest start (migration 0021 + `source_path`/`retry_count`), `queue_manager.py` (cooperative cancel at loop boundary + cascade-delete of partials I1/I7, pause/resume via watcher gating, cancel-suppression), 5 endpoints (`GET /ingest/queue`, cancel/retry/pause/resume), orphan-running sweep on startup. `ActivityBar`→expandable panel (progress bar, per-task rows, retry/cancel, pause/resume, cancel-all, auto-expand). Preview-verified (live queue, pause, panel). NOTE: exceeds llm_wiki (server-side queue vs client JSON). Follow-up: per-task phase/progress/ETA. | [FE]/[BE] | — |
| G-P1-4 | **Scenario templates (5 vault presets).** Add Research/Reading/Personal Growth/Business/General templates that pre-populate purpose.md + schema.md at vault creation. | [FE]/[BE] | M |
| ~~G-P1-5~~ | ✅ **DONE (2026-07-01).** Frontend-only (computed from existing GET /graph payload — no BE change, I2-safe). `frontend/src/components/graph/graphInsights.ts` (pure logic) + `GraphInsightsPanel.tsx` overlay in GraphPanel. Surprising connections (cross-community, weight≥3, top 8), knowledge gaps (isolated deg≤1, sparse cohesion<0.15 & size≥3, bridge ≥3 neighbor communities); meta nodes excluded; dismissable; click-to-highlight (setSelectedNodeId); Deep Research button on gaps → navigate to deep-search (researchStore has no seed action → navigate-only). i18n IT/EN. Tests: `graphInsights.test.ts`, `GraphInsightsPanel.test.tsx`. Verified in preview (all 4 kinds render, dismiss + navigation work). | [FE] | — |
| G-P1-6 | **pre-generated search_queries in ReviewItem.** Add `search_queries JSONB` column; populate from propose_reviews LLM call; pass to deep_research when action triggered. | [BE]/[AI] | M |
| ~~G-P1-7~~ | ✅ **DONE (2026-07-01).** `_TYPE_AFFINITY` matrix + `_type_affinity()` in engine.py (exact llm_wiki values, symmetric, None/unknown→0.5); 4th weight term now `1.0·type_affinity`. Modulator only (ADR-0016 edge-inclusion preserved). Tests: `TestTypeAffinity`. | [BE] | — |
| G-P1-8 | **Language-aware output check.** Confirm ApiProvider/OllamaProvider prompt templates inject explicit "MANDATORY OUTPUT LANGUAGE" directive; add soft filter for CJK/Latin mismatch. | [AI] | S |
| G-P1-9 | **Folder import with folderContext hint.** Add recursive folder import endpoint; inject `folderContext` (joined path segments) into the analysis prompt. | [BE]/[AI] | M |
| G-P1-10 | **Retrieval scope — wiki/ only for citations.** Evaluate filtering GET /search + citation assembly to wiki/ pages only (excludes raw/sources/ from cited context, matching llm_wiki behavior). | [BE] | S |
| G-P1-11 | **ThinkBlock streaming roll preview.** During active `streaming=true`, render last N lines of think content with fade (or animated "..." indicator). | [FE] | S |
| G-P1-12 | **Multi-provider reasoning field routing.** Confirm ApiProvider streaming extracts vendor-specific reasoning fields (DeepSeek `reasoning_content`, Qwen `reasoning`) and routes them to `think` events. | [AI] | S |
| G-P1-13 | **Deep-research synthesis to wiki/queries/ verify.** Confirm provider prompt for synthesis tags result as `type: synthesis` / `type: query`; verify file lands in right wiki subdir after ingest_file. | [BE]/[AI] | S |
| ~~G-P1-14~~ | ✅ **DONE.** `clip_max_body_bytes` (2 MB, `CLIP_MAX_BODY_BYTES` env) — Content-Length + accumulated body → 413 (`main.py`, ADR-0038). | [BE] | — |
| ~~G-P1-15~~ | ✅ **DONE.** `frontend/src/tests/i18n-key-parity.test.ts` asserts EN/IT key-set parity. | [FE] | — |
| ~~G-P1-16~~ | ✅ **DONE.** `@tanstack/react-virtual` in MessageList, NavTree, ConversationList (I4). | [FE] | — |
| ~~G-P1-17~~ | ✅ **DONE.** `settingsStore` persists lang + settings to localStorage (`synapse.lang`, `synapse.settings`). | [FE] | — |
| ~~G-P1-18~~ | ✅ **DONE.** `.github/workflows/ci.yml`: ruff/black/mypy, pytest (stage 4), vitest + `tsc --noEmit` + eslint (stage 4b). | [DevOps] | — |

### Phase 2 — Polish and enhancement opportunities (future sprints)

| ID | Gap or opportunity | Owner | Effort |
|---|---|---|---|
| G-P2-1 | **Vision caption for images.** Add vision-LLM caption step for `.png/.jpg` files via `provider.chat()` (I6); cache by SHA256 of file bytes. Parity with llm_wiki vision pipeline. | [AI]/[BE] | L |
| G-P2-2 | **purpose.md suggestion via ReviewItem.** At ingest end, emit a `purpose-suggestion` ReviewItem when analysis reveals scope drift. Closes the llm_wiki vaporware gap (F2) better than llm_wiki did. | [AI]/[BE] | M |
| G-P2-3 | **Cancel in-flight ingest.** `DELETE /ingest/{run_id}` — aborts the running ingest_file coroutine; does NOT delete already-written pages. | [BE]/[FE] | M |
| G-P2-4 | **schema.md co-evolution.** Allow LLM to propose schema.md edits (K8 / Karpathy principle). Requires a `schema-suggestion` ReviewItem type. | [AI]/[BE] | L |
| G-P2-5 | **MinerU PDF extraction.** Add optional MinerU extractor with pypdf fallback (mirrors llm_wiki). | [BE] | L |
| G-P2-6 | **PWA + Tauri v2 packaging.** Sprint-6 target (F15). | [FE] | XL |
| G-P2-7 | **Edge relevance tooltip on hover.** Show weight/signal breakdown on edge hover in GraphViewer. Both llm_wiki and Synapse lack this — pure enhancement. | [FE] | S |
| G-P2-8 | **Community cohesion score + warning marker.** After Louvain (G-P0-2), add cohesion score per community; warn in legend if <0.15 (mirrors llm_wiki graph-insights). | [BE]/[FE] | S |

---

## 21. Already at parity or better — do not redo this work

| Feature | Why it is solved |
|---|---|
| SHA256/mtime-then-hash incremental gate | ADR-0001: faster and more correct than llm_wiki's basename-keyed cache. |
| Graph layout server-side (I2 hard rule) | Never blocks main thread. Eliminates llm_wiki P-2 defect entirely. |
| Incremental graph reads (Postgres, no vault walk) | I1: GraphEngine reads pages+links tables; zero filesystem scan on recompute. |
| Bounded loops everywhere (I7) | ingest loop, deep-research, lint, sweep-reviews, fallback: all have explicit max_iter + token_budget enforced at loop level, not just source count. |
| Clip server authentication (ADR-0040) | The llm_wiki Critical S-1/S-2/S-3/S-4/S-5 defects are pre-empted: token auth, origin allowlist, body cap, path safe_join — all present. |
| purpose.md path consistency | Unified on `vault_root/purpose.md`; llm_wiki B-3 bug absent. |
| overview.md append (no drift) | Append-only (not full-overwrite). llm_wiki B-7 bug not present. |
| Shared-entity preservation in cascade delete | ADR-0026 Do-NOT #6 + sources[] rewrite path. 87 test cases in llm_wiki audit; Synapse enforces by invariant. |
| Cascade delete dry-run plan | `plan_cascade_delete` is a mandatory dry-run; llm_wiki has no equivalent. |
| Per-run cost accounting (I7 ledger) | `ingest_runs` + `deep_research_runs` + per-message token columns; $1 anomaly warning. llm_wiki has no cost tracking. |
| Provider capability-aware routing (no isinstance) | I6 hard rule; only `supports_agentic_loop` is read. |
| CodeMirror 6 editor (I4) | No WYSIWYG; no ProseMirror bottleneck. |
| Server-side think-tag splitting (I3) | ThinkScanner on backend; zero client-side per-token parsing. |
| Auto-generated `.obsidian/` (I5/K7) | vault.py bootstrap; Obsidian compatibility on every startup. |
| dataVersion debounce + GraphCache | Single background loop; burst of bumps → one recompute. |
| F13 raw/sources/ file deletion | Do-NOT #9 enforced; no orphan source files. |
| Single `write_wiki_page` seam (ADR-0010) | MCP write_page and orchestrated loop share exactly one write path (I1/I5). |
| FNV-1a dedup on ReviewItems (ADR-0044) | Exact algorithm from llm_wiki; upsert on content_key prevents duplicate proposals on re-ingest. |

---

*End of parity matrix.*
