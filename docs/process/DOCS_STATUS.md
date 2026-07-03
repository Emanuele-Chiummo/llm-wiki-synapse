# DOCS_STATUS — Sprint v0.6 / M6 Documentation Gate

> Tech-writer sign-off. Phases appended chronologically; most recent phase at top.

---

## v0.7.0 Pre-Release Docs Gate — VERDICT: PASS-PENDING-D5

> Gate run: 2026-07-03
> Branch: `sprint/v0.6`
> Scope: v0.7.0 release — parity closure (G-P0-1/2/3, G-P1-4/6/8/9/10/11/12/13),
>   desktop auto-update (ADR-0049), scenario templates, recursive import, ServiceNow connector.
> Key commits: `aab417c` (provider gate + Search view), `0afa34e` (Save-to-Wiki + Louvain),
>   `edb35c6` (Save-to-Wiki backend + Louvain engine), `9751461` (Lucide icons),
>   `99eeb3d` (Overview note mirror).

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/` (context/container/component) | UP-TO-DATE (no change) | ADR-0049 adds no new container, port, or external service. Auto-update is a desktop-client transport only; Synapse topology unchanged. C4 diagrams current from v0.6/M6. |
| D2 | `docs/er/schema.mmd` | PENDING-REGEN | `ReviewItem.search_queries` column added (G-P1-6). Requires `make er` after the migration lands. All other v0.7 changes (G-P1-4 scenarios, G-P1-8 language patch, G-P1-9 recursive import, G-P1-10 retrieval scope, G-P1-11/12/13) are migration-free or provider-side. Blocked on backend-engineer running `make er` post migration. |
| D3 | `docs/sequences/` | UP-TO-DATE (carry-forward) | No new sequence flows introduced by v0.7 items in this gate. G-P1-9 recursive import adds a folderContext hint inside the existing ingest-loop.mmd ingest sequence (no new swimlane needed). G-P1-10 retrieval change is a filter in the existing query-4phase.mmd Phase 1 step. No new diagrams required this gate. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (verified) | All three new v0.7 endpoints confirmed present: `POST /pages` (PRESENT), `PATCH /conversations/{conversation_id}` (PRESENT), `GET /scenarios` (PRESENT). `/chat/save-to-wiki` (G-P0-1) confirmed present. Total: 64 paths. API version field shows `0.6.0` — version string bump to `0.7.0` should be done by devops-engineer via `make openapi` after the release tag. Non-blocking for code gate. |
| D5 | `docs/screens/` | PENDING-LIVE (HCP carry-forward) | v0.7 UI changes (new-page button, bulk select, scenarios panel, conversation rename/filter, unsaved-changes guard, auto-update banner) require Playwright screenshot refresh. These fold into the next live-stack verification session (`make screenshots`). Non-blocking for code gate; blocks v1.0.0 tag. |
| D6a | `docs/USER.md` | UPDATED (this gate) | Header updated to v0.7. Added sections: (1) New-page button in wiki tree (left panel under Wiki section); (2) Unsaved-changes guard in editor (Stay / Discard dialog); (3) Conversation rename (double-click inline) + filter (search box in conversation list); (4) Bulk select in Sources (Select button, checkboxes, Delete selected + cascade-delete confirm dialog); (5) Scenarios in Settings — full sub-section with 5-preset table, Apply flow, and caveats; updated settings table from 9 to 10 sections; (6) Desktop auto-update — startup check, Update now / Later (Aggiorna ora / Più tardi), progress, key-loss caveat link to DEPLOY.md. |
| D6b | `docs/DEPLOY.md` | UPDATED (this gate) | Header updated to v0.7. (1) `IMPORT_SCAN_RECURSIVE` env var row added to §2.1 table (default false, G-P1-9). §6.4 scan limits text updated: non-recursive default + `IMPORT_SCAN_RECURSIVE=true` opt-in + folderContext explanation. (2) §7.7 "CI desktop release" replaced by "Release procedure (v* unified tag, ADR-0049)": three-way version bump table (tauri.conf.json / Cargo.toml / package.json), tag + push steps, CI matrix with `latest.json` + `.sig` artifacts, acceptance gate checklist, key-loss caveat, `workflow_dispatch` note. Old `desktop-v*` trigger language removed. (3) New §13 "ServiceNow doc connector": one-shot conversion, watch-daemon mode, integration notes, link to `tools/marker-converter/README.md`. Old §13 renumbered to §14 References. ADR reference updated through ADR-0049. |
| D7 | `docs/adr/README.md` | DRIFT — needs ADR-0049 row | ADR-0049 (`0049-desktop-auto-update-github-releases.md`) exists at `docs/adr/` but the README index table has not been updated to include it. Backend/devops-engineer should add the row; tech-writer to verify at next gate. |
| D7 | `docs/adr/0049-desktop-auto-update-github-releases.md` | UP-TO-DATE | File present and complete (authored by solution-architect, 2026-07-03). Accepted status. ADR content is the source of truth for DEPLOY.md §7.7 and USER.md desktop auto-update section. |
| R (parity) | `docs/reference/SYNAPSE-VS-LLMWIKI-PARITY.md` | UPDATED (this gate) | v0.7 closure note added to header. 13 items closed: G-P0-1/2/3 + G-P1-4/6/8/9/10/11/12/13. Per-row verdicts updated from ❌/🟡 to ✅ for all 13. P2 backlog unchanged. All P0+P1 items now closed. |

### Endpoint verification (D4)

| Endpoint | Method | Present in openapi.json | Notes |
|----------|--------|------------------------|-------|
| `/pages` | POST | YES | New wiki page creation |
| `/conversations/{conversation_id}` | PATCH | YES | Conversation rename |
| `/scenarios` | GET | YES | List scenario presets |
| `/chat/save-to-wiki` | POST | YES | G-P0-1 Save-to-Wiki |

Total paths in openapi.json: 64. All four checked endpoints present.

### Invariant compliance check (v0.7 gate)

| Invariant | Status |
|-----------|--------|
| **I1** (incremental index only) | HOLDS — new-page button writes through `write_wiki_page` (one page, one bump); recursive import copies files without re-ingesting unchanged ones (hash gate). |
| **I6** (pluggable inference) | HOLDS — language directive patch touches provider prompt templates only; no provider is hardcoded. G-P1-10 retrieval scope is a filter in `retrieval.py`, not provider-bound. |
| **I7** (bounded loops) | HOLDS — recursive import bounded by `IMPORT_SCAN_MAX_FILES` + `IMPORT_SCAN_MAX_SECONDS` per tick; scenario Apply is a single file write, no loop. |
| **I8** (docs-as-DoD) | HOLDS pending D2 regen (make er after search_queries migration) and D7 ADR-0049 README row. D4 endpoints verified; D6a/D6b updated; parity matrix closed. |
| **I9** (do not reinvent) | HOLDS — ADR-0049 uses Tauri first-party `tauri-plugin-updater` over GitHub Releases; no bespoke update server. ServiceNow connector uses Marker (existing external) + standard Synapse ingest pipeline. |

### Outstanding items (carry-forward, non-blocking for code gate)

1. **D2 — ER regen**: `make er` after the `search_queries JSONB` migration is applied (backend-engineer).
2. **D4 — API version**: `openapi.json` version field shows `0.6.0`; should be bumped to `0.7.0` via `make openapi` post-tag (devops-engineer).
3. **D5 — Screenshots**: v0.7 UI views PENDING-LIVE (Playwright capture session).
4. **D7 — ADR README**: ADR-0049 row missing from `docs/adr/README.md` index (backend/devops-engineer to add; tech-writer to verify).

### DOCS GATE VERDICT — v0.7.0 Pre-Release

**PASS-PENDING-D5**

D6a, D6b, and parity matrix (R) are fully updated. D4 endpoints verified (64 paths,
all 4 checked present). D1 and most D3 are unchanged and current. D2 and D7/ADR-0049
row have known carry-forward gaps documented above; neither blocks the code gate.

Parity rows closed this gate: G-P0-1, G-P0-2, G-P0-3, G-P1-4, G-P1-6, G-P1-8,
G-P1-9, G-P1-10, G-P1-11, G-P1-12, G-P1-13 — 13 items total. All P0+P1 backlog
items are now closed.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-07-03 | v0.7.0 pre-release docs gate**

---

## M6 / v0.6 Docs Gate — VERDICT: PASS-PENDING-D5/HCP

> Gate run: 2026-06-30
> Branch: `sprint/v0.6`
> Scope: M6 feature set — K2 Lint, F11 Web Clipper, F15 CI/PWA/Tauri, F2 purpose.md verification.
> Backend: 968 pytest green. Frontend: 711 vitest green. Linters: ruff+black+tsc+eslint clean tree-wide.
> Key commits: `745600f` (K2 lint backend), `ac90d35` (K2 lint UI), `7c91354` (F11 clipper),
>   `8bbd2a9` (F15 CI), `cc365f6` (F15 PWA), `f68b2ad` (F15 Tauri).

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/` (context/container/component) | UP-TO-DATE (no change needed) | No new container, port, or external service introduced by M6. Lint and clipper are components inside the existing FastAPI boundary. Tauri wraps the existing frontend build — no new C4 box. C4 topology unchanged; D1 files current from v0.5. |
| D2 | `docs/er/schema.mmd` | UP-TO-DATE — ZERO DRIFT | Migration 0014 (`lint_runs` + `lint_findings` tables) was regenerated by the K2 lint agent via `make er`. ER header updated to `v0.6-K2-lint`. 14 tables. `make er` produces zero diff against committed file. I8 holds. |
| D3 | `docs/sequences/lint-fix.mmd` | UP-TO-DATE | New sequence diagram for K2 lint loop added in commit `745600f` (ADR-0037). Covers: `POST /lint/scan` → bounded loop (max\_iter + token\_budget) → deterministic pass (orphan/structural, no LLM) + semantic pass (missing-xref/contradiction/stale, bounded provider call, timeout→degrade) → `lint_findings` rows written → human Apply/Acknowledge/Dismiss actions. Header: `v0.6 ADR-0037 \| 2026-06-30`. |
| D3 | `docs/sequences/web-clip.mmd` | UP-TO-DATE | New sequence diagram for F11 clipper added in commit `7c91354` (ADR-0038). Covers: Chrome extension → `POST /clip` (token auth → origin check → body cap → safe-join path) → write to `vault/raw/sources/` → watcher ingest pipeline. Header: `v0.6 ADR-0038 \| 2026-06-30`. |
| D3 | All other sequence diagrams | UP-TO-DATE (carry-forward, unchanged) | `ingest-loop.mmd`, `ingest-routing.mmd`, `graph-recompute.mmd`, `deep-research.mmd`, `cascade-delete.mmd`, `review-create-sweep.mmd`, `wikilink-enrichment.mmd` — all unchanged and current from their respective gate runs. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE | Regenerated for K2 (`/lint/scan`, `/lint/runs`, `/lint/runs/{id}`, `/lint/runs/{id}/findings`, `/lint/findings/{id}/apply`, `/lint/findings/{id}/acknowledge`) and F11 (`POST /clip`). All endpoints present with typed request/response schemas. `make openapi` produces zero diff against committed file. |
| D5 | `docs/screens/` | PENDING-LIVE (HCP) | M6 views requiring Playwright capture: Lint section (Run Lint + findings list), web-clipper popup flow, PWA install prompt, Tauri desktop window. These fold into EC-M6-HCP §5 item 4 — captured during Emanuele's live-stack verification session (`make screenshots`). Pre-existing M5 PENDING-LIVE screenshots also remain in this category. Non-blocking for code-gate; blocks the v1.0.0 tag. |
| D6a | `docs/USER.md` | UPDATED (this gate) | Version header updated to v0.6. Nav rail table updated to include Review, Deep Research, and Lint. Lint section added: Run Lint action, four finding categories (orphan-page, contradiction, stale-claim, missing-xref), Apply vs Acknowledge distinction (flag-only categories per ADR-0037), empty state. Web Clipper section added: install from `extension/`, configure base URL + token in Options, clip a page → auto-ingest flow, security notes. FU-P3-2 note added to the Review section: CLI delegated-ingest path does not enqueue review items (ADR-0025 §7, conscious design gap). M5/M6 feature tables updated. |
| D6b | `docs/DEPLOY.md` | UPDATED (this gate) | Eight new env var rows added to §2.1 table: `LINT_MAX_ITER` (3), `LINT_TOKEN_BUDGET` (20000), `LINT_MAX_FINDINGS` (50), `LINT_TIMEOUT_SECONDS` (30.0), `CLIP_ENABLED` (false), `CLIP_TOKEN` (none, SECRET), `CLIP_ALLOWED_ORIGINS` (empty), `CLIP_MAX_BODY_BYTES` (2097152). Migration 0014 (`lint_runs` + `lint_findings`) documented in §3.2 startup sequence. Header and ADR reference updated. |
| D7 | `docs/adr/README.md` | UP-TO-DATE | ADR-0037 (K2 Lint), ADR-0038 (F11 Web Clipper), ADR-0039 (F15 Tauri) all present and indexed (added in their respective feature commits). Index complete through ADR-0039. |
| D7 | `docs/adr/0037-lint-fix-loop.md` | UP-TO-DATE | File present and complete (commit `745600f`). Accepted status. |
| D7 | `docs/adr/0038-web-clipper-secure-ingress.md` | UP-TO-DATE | File present and complete (commit `7c91354`). Accepted status. |
| D7 | `docs/adr/0039-tauri-v2-desktop-shell.md` | UP-TO-DATE | File present and complete (commit `f68b2ad`). Accepted status. |

### FU-P4-3 — ADR-0026 atomicity note

ADR-0026 (`docs/adr/0026-cascade-delete.md`) amended this gate with a Consequences note documenting that `cascade_delete` opens a separate DB session per step (five sequential `get_session()` calls, not one atomic transaction). A crash between steps leaves a transiently inconsistent but idempotent-on-retry state (see §8 Do-NOT list: the page is soft-deleted first, so a retry finds a 404 and exits cleanly). This is a known and documented consequence, not a correctness defect. The single-session wrap alternative was evaluated (FU-P4-3) and deferred — the idempotent-on-retry behavior is sufficient for M6 and the atomicity trade-off is now explicit in the ADR. No code change made (doc note only, per task constraint).

### NB-7 / NB-8 cosmetic label status

- **NB-7 (graph-recompute.mmd hit-path label):** The current `docs/sequences/graph-recompute.mmd` already shows the cache-HIT path as `GC-->>CL: 200 {nodes,edges,data_version,cached:true}` with an explicit note "NO FA2, NO DB — pure read of the in-process snapshot". No `GC->>PG` line exists on the hit path. The label described in NB-7 (BACKLOG.md line 998) is **not present** in the committed file — the fix was applied in a prior sprint pass. **SKIPPED (already correct).**
- **NB-8 (component.mmd store filename):** `docs/architecture/component.mmd` line 81 already reads `"store/graphStore.ts"` — the `store/graph.ts` label described in NB-8 (BACKLOG.md line 1020) is **not present** in the committed file. **SKIPPED (already correct).**

### Invariant compliance check (M6)

| Invariant | Status |
|-----------|--------|
| **I1** (incremental index only) | HOLDS — lint Apply writes through `write_wiki_page` (one page, one data_version bump); clip writes to `raw/sources/` and lets the watcher ingest (no direct ingest call). |
| **I6** (pluggable inference) | HOLDS — lint semantic pass routes via `resolve_provider_config`; no hardcoded backend. Confirmed by AC-K2-2 static test. |
| **I7** (bounded loops) | HOLDS — lint loop bounded by `LINT_MAX_ITER` + `LINT_TOKEN_BUDGET` + `LINT_TIMEOUT_SECONDS` + `LINT_MAX_FINDINGS`. Cost logged to `lint_runs.total_cost_usd`. |
| **I8** (docs-as-DoD) | HOLDS — ER regenerated (14 tables, zero drift), OpenAPI regenerated (`/lint/*` + `/clip` present), D3 diagrams added, USER.md + DEPLOY.md updated, ADR index complete. |
| **I9** (do not reinvent) | HOLDS — clipper uses Readability + Turndown (standard libraries); no custom HTML parser. Watcher ingest pipeline reused. SearXNG only (no Tavily). |

### DOCS GATE VERDICT — M6 / v0.6

**PASS-PENDING-D5/HCP**

All D-artifacts are UP-TO-DATE, N/A-unchanged, or PENDING-LIVE with documented rationale.

Items confirmed clean (no drift):
- D2: 14 tables, zero drift after migration 0014 (ER regenerated by K2 agent)
- D4: `openapi.json` current; `/lint/*` (6 paths) + `/clip` present; `make openapi` zero diff
- D3: `lint-fix.mmd` and `web-clip.mmd` added; all prior diagrams unchanged
- D6a: USER.md updated — Lint section, Web Clipper section, FU-P3-2 CLI-path note, nav rail, version
- D6b: DEPLOY.md updated — 8 new env vars, migration 0014, version and ADR reference
- D7: ADR-0037/0038/0039 indexed; ADR-0026 FU-P4-3 Consequences note added

Carry-forward pending items (non-blocking for code-gate; block v1.0.0 tag):
- D5: M5 + M6 screenshots PENDING-LIVE (EC-M6-HCP §5 item 4)
- EC-M6-HCP: live-stack smoke, PWA/Tauri install, 3-provider matrix, gate-chain sign-offs

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-30 | M6 / v0.6 docs gate**

---

## v0.5 Hardening — ADR-0035 + ADR-0036 + Bug Fixes + Wiki Notes UI — DOCS GATE: PASS

> Gate run: 2026-06-30
> Scope: Four changes landed this hardening increment:
> (1) ADR-0035: GET/PUT /pages/{id}/content — wiki page read/edit with optimistic lock, inline re-index (I1), atomic write (I5).
> (2) ADR-0036: wikilink-enrichment post-pass — bounded provider call returning {mention, target_title} substitutions applied deterministically into page bodies (F4 direct link x3 signal restored).
> (3) Bug fixes: OllamaProvider options.num_ctx; ingest_runs pages_created/status/error_message persist; chat preamble bare [n] citation markers.
> (4) Frontend: Wiki section center panel replaced from graph viewer to NoteView (CodeMirror 6 reader/editor); Graph section unchanged.
> Schema changes: NONE (ADR-0035 and ADR-0036 are both migration-free). ER unchanged.

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/` | N/A-UNCHANGED | No new container or component. NoteView is inside the existing Wiki/Pages section. GET/PUT /pages/{id}/content is an endpoint on the existing FastAPI service. No C4 topology change. |
| D2 | `docs/er/schema.mmd` | UP-TO-DATE (no-change) | ADR-0035 migration-free (no `content` column; reuses `content_hash`). ADR-0036 migration-free (no new columns). Bug fixes reused existing columns (`pages_created`, `status`, `error_message`) that already existed in the schema (Alembic 0006, ADR-0018). ER unchanged; last header: `v0.5-ADR-0033`. I8 holds. |
| D3 | `docs/sequences/wikilink-enrichment.mmd` | NEW — created this gate | New sequence diagram for ADR-0036 post-pass: anti-spam gate → resolve provider (I6) → ONE bounded provider.chat call (I7) → deterministic validate+apply single-mention (I5) → reindex per modified page (I1) → single data_version bump → GraphCache debounce (I2). Header: `v0.5 ADR-0036 | 2026-06-30`. |
| D3 | `docs/sequences/ingest-loop.mmd` | UP-TO-DATE (no change needed) | Already updated at ADR-0034 gate to show propose_reviews + sweep. ADR-0036 enrichment step detail lives in the new dedicated diagram. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (description corrected) | GET/PUT /pages/{id}/content endpoints confirmed present. `info.description` was stale ("M5 Phase 1") — corrected to "M5 hardening (v0.5)" with explicit ADR-0035 callout. |
| D5 | `docs/screens/` | PENDING-LIVE (carry-forward) | NoteView is new UI; no Playwright spec yet for `wiki-note-read.png` / `wiki-note-edit.png`. Non-blocking; add to M5 screenshot session. |
| D6a | `docs/USER.md` | UPDATED | Header updated. Wiki section center panel description corrected (NoteView, not graph). New "Reading and editing wiki notes" section added with read/edit/save flow, optimistic-lock behavior, and constraints. |
| D6b | `docs/DEPLOY.md` | DRIFT-MINOR | WIKILINK_ENRICH_* env vars (ENABLED, MAX_CANDIDATES, MAX_SUBS, TIMEOUT_SECONDS) not yet in §2.1 env table. To be added in next deploy-docs pass. Non-blocking. |
| D7 | `docs/adr/README.md` | UPDATED | ADR-0034 row moved to correct numerical position (was incorrectly after 0036; now before 0035). Header updated to reflect ADR-0034/0035/0036. |
| D7 | `docs/adr/0035-page-content-read-edit-endpoints.md` | UP-TO-DATE | File present and complete. |
| D7 | `docs/adr/0036-wikilink-enrichment-post-pass.md` | UP-TO-DATE | File present and complete. |

### Docstring corrections — backend/app/ops/review.py

Three private functions had "TODO[ai-agent-engineer]" / "STUB SEAMS" wording even though all three are fully implemented. Module docstring was also stale. Corrections (doc-only, zero logic touched):

| Symbol | Old wording | New wording |
|--------|------------|------------|
| Module docstring | `STUB SEAMS (to be filled by ai-agent-engineer)` | `AI SEAMS (implemented — ADR-0034 §11.2)` |
| `_llm_propose_reviews` | `TODO[ai-agent-engineer] ADR-0034 §4.3 — single bounded provider call.` | `Single bounded provider call (ADR-0034 §4.3, implemented).` |
| `_llm_sweep_judge` | `TODO[ai-agent-engineer] ADR-0034 §6.3 — conservative bounded LLM pass, default-to-keep.` | `Conservative bounded LLM pass, default-to-keep (ADR-0034 §6.3, implemented).` |
| `_run_generation` | `TODO[ai-agent-engineer] ADR-0034 §5 — bounded run_orchestrated_loop on-demand.` | `Bounded run_orchestrated_loop on-demand for lazy Create (ADR-0034 §5, implemented).` |

### Context budget alignment (Task E)

Verified `frontend/src/store/settingsStore.ts` `computeBudgetSplit()`:
- history: 60%, retrieved: 20%, system: 5%, generation: 15%

CLAUDE.md §4b F14 and docs/USER.md Settings > General both say "60/20/5/15". **Exact match — no correction needed.**

Also confirmed `backend/app/rag/retrieval.py` uses `_RETRIEVAL_BUDGET_FRACTION = 0.20` (the 20% retrieved slice) and `_CHARS_PER_TOKEN = 4` (char/4 approximation). The audit's "~50/5/15 in CHARACTERS" claim refers to the upstream reference (nashsu/llm_wiki), not Synapse. Synapse's split is percentages, not hardcoded character counts.

### F5 4-phase retrieval alignment (Task E)

Verified `backend/app/rag/retrieval.py` implements all four phases as documented:
1. Dense bge-m3 top-k (or lexical ILIKE fallback per ADR-0030)
2. BFS graph-expansion over `edges` table, depth <= 2 (hard cap)
3. Token budget = 20% of context window via char/4
4. Server-side assembly with [n] markers; overflow = drop lowest-ranked

**Docs are accurate. No correction needed.**

### ER verdict (Task G)

ADR-0035: migration-free (no new columns). ADR-0036: migration-free (config.py only). Bug fixes: reused existing columns from Alembic 0006.

**ER unchanged. `docs/er/schema.mmd` current. `make er` not required.**

### DOCS GATE VERDICT — v0.5 Hardening

**PASS** (two non-blocking carry-forward items: DEPLOY.md WIKILINK_ENRICH_* env vars; NoteView screenshots)

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-30 | v0.5 hardening docs gate**

---

## ADR-0034 — F9 Review Queue Redesign (Proposal Model + Lazy Create + Sweep) — DOCS GATE: PASS

> Gate run: 2026-06-30
> Scope: ADR-0034 redesigns the F9 review queue (proposal model, lazy on-demand Create, auto-resolution sweep). Schema change = Alembic **0013** (`review_items` gains six new columns, drops `pre_generated_query`, new `source_page_id`/`created_page_id` FKs to `pages`, `deep_research_run_id` FK to `deep_research_runs`). New REST surface: `/review/queue/{id}/create` alias + `POST /review/queue/sweep`. New env vars in `config.py` (`REVIEW_PROPOSE_*` + `REVIEW_SWEEP_*`). New D3 sequences. USER.md + DEPLOY.md updated. ADR-0025 F9 parts superseded. Screenshots confirmed by frontend-engineer.

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/` (context/container/component) | N/A-UNCHANGED | ADR-0034 §11.3 explicit: no new container or component. `ops/review.py` stays a component inside the FastAPI service. No C4 topology change. |
| D2 | `docs/er/schema.mmd` | UP-TO-DATE (backend-engineer regenerated via `make er`) | `REVIEW_ITEMS` entity carries the three `pages` FKs (`page_id`, `source_page_id`, `created_page_id`) and the `deep_research_runs` FK (`deep_research_run_id`). New columns `proposed_title`, `proposed_page_type`, `proposed_dir`, `rationale`, `resolution` present. `pre_generated_query` absent (dropped in 0013). I8 holds — ER matches live schema post-migration 0013. See verification §D2 below. |
| D3 | `docs/sequences/ingest-loop.mmd` | UPDATED | Added `propose_reviews` (anti-spam gate → rule-based → ≤1 bounded LLM call → INSERT review_items) and fire-and-forget `sweep_reviews` (Pass-1 rule + Pass-2 conservative LLM) as post-generation steps. Header updated to `v0.5 ADR-0034 \| 2026-06-30`. |
| D3 | `docs/sequences/review-create-sweep.mmd` | NEW | New sequence diagram covering the Create action (POST /review/queue/{id}/create → skeleton derivation → bounded run_orchestrated_loop → write_wiki_page → data_version bump → status=created → fire-and-forget sweep), plus Skip, Deep Research, and the manual sweep endpoint. Header: `v0.5 ADR-0034 \| 2026-06-30`. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (backend-engineer regenerated via `make openapi`) | `/review/queue/{item_id}/create` (preferred alias, 201) and `/review/queue/sweep` (200 `SweepResponse`) are present. `ReviewItemResponse` schema carries all ADR-0034 §7.1 fields. See verification §D4 below. |
| D5 | `docs/screens/review-queue-proposal-cards.png` | CONFIRMED PRESENT | Frontend-engineer committed `review-queue-proposal-cards.png` and `review-queue-adr0034.png`. Both files exist in `docs/screens/`. Embedded in USER.md. |
| D5 | `docs/screens/review-queue-adr0034.png` | CONFIRMED PRESENT | See above. |
| D6a | `docs/USER.md` | UPDATED | Added full **Review section** documenting: 5 proposal types (missing-page, suggestion, contradiction, duplicate, confirm), the 3 closed actions (Create / Deep Research / Skip), the Create-is-lazy explanation with note that Approve is renamed to Create (breaking semantics change), the auto-resolution sweep (rule then LLM). Updated nav rail description, Sources section stale note, and M5 feature table row. Header updated to `v0.5 ADR-0034 \| 2026-06-30`. |
| D6b | `docs/DEPLOY.md` | UPDATED | Migration 0013 documented in §3.2 (additive columns, `pre_generated_query` drop, legacy row left-shift, new sweep index). Eleven new env var rows added to §2.1 table: `REVIEW_PROPOSE_MIN_CHARS`, `REVIEW_PROPOSE_MIN_PAGES`, `REVIEW_PROPOSE_MAX_ITEMS`, `REVIEW_PROPOSE_TOKEN_BUDGET`, `REVIEW_PROPOSE_TIMEOUT_SECONDS`, `REVIEW_SWEEP_MAX_ITEMS`, `REVIEW_SWEEP_LLM_ENABLED`, `REVIEW_SWEEP_LLM_MAX_ITEMS`, `REVIEW_SWEEP_LLM_TOKEN_BUDGET`, `REVIEW_SWEEP_TIMEOUT_SECONDS` (all with defaults from config.py). ADR reference updated to ADR-0034. Header updated. |
| D7 | `docs/adr/README.md` | UPDATED | ADR-0034 row added (Accepted, 2026-06-30, full summary). ADR-0025 row amended: status shows "F9 parts superseded by ADR-0034". Header updated to ADR-0034. |
| D7 | `docs/adr/0034-review-queue-proposal-model.md` | UP-TO-DATE (pre-existing — authored by solution-architect) | File present, complete, Accepted status. Formatted by tech-writer. |

### §D2 — ER diagram verification (post-migration 0013)

File: `docs/er/schema.mmd`

`REVIEW_ITEMS` entity inspected. Fields present and matching ADR-0034 §3.1:

| Column | FK target | Present in ER | Notes |
|--------|-----------|---------------|-------|
| `page_id` | `pages.id` | YES | Review target (contradiction/duplicate conflicting page) |
| `source_page_id` | `pages.id` | YES | Provenance: which ingest produced this proposal |
| `created_page_id` | `pages.id` | YES | Page produced by the Create action |
| `deep_research_run_id` | `deep_research_runs.id` | YES | Set by Deep-Research action |
| `proposed_title` | — | YES | Drives rule-based sweep title match |
| `proposed_page_type` | — | YES | Lazy skeleton PageType inference |
| `proposed_dir` | — | YES | Display-only; recomputed at Create time |
| `rationale` | — | YES | Replaces `pre_generated_query` |
| `resolution` | — | YES | How the item closed (audit) |
| `pre_generated_query` | — | ABSENT (dropped in 0013) | Correct: dropped per ADR-0034 §3.1 |
| `item_type` | — | YES | New five-value convention (missing-page etc.) |
| `status` | — | YES | Extended lifecycle (pending/created/skipped/deep_researched/auto_resolved) |

Three-FK-to-pages check: `page_id`, `source_page_id`, `created_page_id` all present as FK references to `pages.id`. **I8 holds.**

### §D4 — OpenAPI verification (post-make openapi)

File: `docs/api/openapi.json`

| Path | Method | Present | Notes |
|------|--------|---------|-------|
| `/review/queue` | GET | YES | ADR-0034 §7 projection description present |
| `/review/queue/{item_id}/approve` | POST | YES | Now documented as Create semantics (201 response) |
| `/review/queue/{item_id}/create` | POST | YES | Explicit alias; operationId `create_review_item_review_queue__item_id__create_post` |
| `/review/queue/{item_id}/skip` | POST | YES | |
| `/review/queue/{item_id}/deep-research` | POST | YES | Topic derivation from proposed_title/rationale documented |
| `/review/queue/sweep` | POST | YES | `SweepResponse` schema with `rule_resolved`, `llm_resolved`, `kept` |

`ReviewItemResponse` schema confirmed to carry ADR-0034 §7.1 fields: `proposed_title`, `proposed_page_type`, `proposed_dir`, `rationale`, `source_page_id`, `created_page_id`, `resolution`. **D4 up-to-date.**

### §D5 — Screenshot verification

| Screenshot | File | Status |
|-----------|------|--------|
| `review-queue-proposal-cards.png` | `docs/screens/review-queue-proposal-cards.png` | CONFIRMED PRESENT |
| `review-queue-adr0034.png` | `docs/screens/review-queue-adr0034.png` | CONFIRMED PRESENT |

Both screenshots embedded in `docs/USER.md` Review section. **I8 D5 check: PASS.**

### Invariant compliance check

| Invariant | Status |
|-----------|--------|
| **K8** (human curates, LLM maintains) | RESTORED — LLM now proposes; human decides Create/Deep-Research/Skip. Old Approve no-op eliminated. |
| **I1** (incremental index only) | HOLDS — Create writes through `write_wiki_page` (same seam); sweep uses bounded indexed reads, never vault re-scan. |
| **I6** (pluggable inference) | HOLDS — all three new LLM call sites route via `resolve_provider_config("ingest")`; no hardcoded backend. 409 on missing provider. |
| **I7** (loops are bounded) | HOLDS — proposal: anti-spam gate + ≤1 call + `REVIEW_PROPOSE_MAX_ITEMS` cap + timeout; Create: bounded `run_orchestrated_loop`; sweep Pass-2: ≤1 batched call + cap + default-to-keep. |
| **I8** (docs-as-DoD) | HOLDS — ER regenerated (3 FKs to pages + deep_research_runs FK confirmed); OpenAPI regenerated (`/create` alias + `/sweep` present); D3 updated + new diagram; USER.md + DEPLOY.md updated. |
| **I2/I4/I5** | UNTOUCHED — sweep never calls FA2; review list virtualized >50 (I4); Create writes valid frontmatter through existing seam (I5). |

### DOCS GATE VERDICT — ADR-0034

**PASS**

All D-artifacts are UP-TO-DATE for the ADR-0034 F9 redesign:
- D2: `review_items` ER reflects migration 0013 (three FKs to `pages`, `pre_generated_query` absent, all new columns present). I8 holds.
- D3: `ingest-loop.mmd` updated with propose/sweep steps; `review-create-sweep.mmd` new sequence covering Create / Skip / Deep Research / manual sweep.
- D4: `openapi.json` includes `/create` alias + `/sweep` endpoint + updated `ReviewItemResponse` schema.
- D5: Two ADR-0034 screenshots confirmed present in `docs/screens/`.
- D6a: USER.md has a complete Review section with 5 proposal types, 3 actions, auto-sweep, Create-vs-Approve note, screenshots embedded.
- D6b: DEPLOY.md documents migration 0013 and all 10 new `REVIEW_PROPOSE_*` / `REVIEW_SWEEP_*` env vars with defaults.
- D7: ADR-0034 row added to README; ADR-0025 row marked "F9 parts superseded by ADR-0034".

Carry-forward pending items (unchanged, non-blocking):
- D5: 9 prior M5 captures still PENDING-LIVE (live-stack Playwright session).
- AC-D3-CI-1 (mmdc CI render step): GAP-v0.5-3 carry-forward to M6.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-30 | ADR-0034 F9 review queue redesign docs gate**

---

## M5 UI Polish Follow-up — Graph, empty states, timeout UX — DOCS NOTE

> Gate run: 2026-06-30
> Scope: frontend-only completion of the UI polish plan: graph toolbar/search/filter/retry, guided empty states for Sources/Review/Deep Search/Graph, request timeout helper for frontend API calls, responsive section stacking, and backend connection state hardening.
> Owner: **Codex acting as frontend-engineer** for the modified frontend surfaces, tests, and this docs note.

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1/D2/D3/D4/D7 | architecture / ER / sequences / API / ADR | N/A-UNCHANGED | No backend schema, MCP surface, provider-routing contract, or public API shape changed. Timeout behavior is frontend-only. Graph toolbar is render-only and does not introduce client layout. |
| D5 | `docs/screens/` | NEEDS-REFRESH | Existing UI screenshots that include Graph, Sources/Ingest, Review, Deep Search, Header/ActivityBar, and mobile layouts are visually stale after this follow-up. Refresh owner: **Codex/frontend-engineer** with QA/Browser/Playwright capture once the live stack is stable. |
| D6a | `docs/USER.md` | N/A-UNCHANGED | Core user workflows are unchanged; empty states and CTAs guide existing flows. |

**Invariant check:** I2 held (no client-side layout; graph filters/search affect render state only); I3 held (typed selectors retained; no broad store subscriptions added); I4 held (graph overlays and search results are bounded; virtualised lists unchanged); I6/I7 held (provider routing and cost display semantics unchanged).

**Verification target:** `npm run build`, `npm run test`, and Browser desktop/mobile visual checks for Chat, Graph, Sources/Ingest, Review, Deep Search, and Settings.

---

## M5 UI Polish — Frontend workspace shell — DOCS NOTE

> Gate run: 2026-06-30
> Scope: frontend-only visual/UX hardening of the existing shell: shared theme tokens, header branding, labeled NavRail readability, Chat empty state quick prompts, narrow-viewport Chat behavior, and active-section store coverage.
> Owner: **Codex acting as frontend-engineer** for the modified UI surface and related tests.

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1/D2/D3/D4/D7 | architecture / ER / sequences / API / ADR | N/A-UNCHANGED | No topology, schema, API, MCP, provider-routing, or architectural contract change. No ADR required; existing ADR-0017/0018/0021 constraints still apply. |
| D5 | `docs/screens/` | NEEDS-REFRESH | Existing screenshots that include Header/NavRail/Chat empty state are visually stale after this UI polish. Refresh owner: **Codex/frontend-engineer**, with QA/Playwright capture before the next docs gate. |
| D6a | `docs/USER.md` | N/A-UNCHANGED | No user workflow changed; prompt buttons prefill the existing chat input only. |

**Invariant check:** I2 held (GraphViewer layout untouched); I3 held (no broad store subscription; prompt draft is local Chat state); I4 held (MessageList virtualization unchanged; narrow viewport CSS only); I6/I7 held (provider and budgets untouched).

**Verification:** `npm run build` PASS; targeted Vitest PASS (`activeSection-store`, `i18n-key-parity`, `NavRail`); Browser desktop + 390px viewport visual checks PASS.

---

## M5 Post-Phase — ADR-0033 (UI-settable MCP token + allow-without-token) — DOCS GATE: PASS

> Gate run: 2026-06-30
> Scope: ADR-0033 adds TWO columns to `vault_state` (Alembic **0012** — `mcp_access_token_hash` TEXT nullable + `mcp_allow_without_token` BOOLEAN NOT NULL DEFAULT false) plus a new `PUT /mcp/auth` endpoint and new `GET /mcp/info` fields (`token_source`, `allow_without_token`). Architect confirmed: no C4/topology change (D1 unchanged).

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D2 | `docs/er/schema.mmd` | REGENERATED | `make er` re-run via `.venv/bin/python scripts/generate_er.py`. `VAULT_STATE` entity now carries `mcp_access_token_hash` (string) + `mcp_allow_without_token` (boolean) alongside the ADR-0032 `remote_mcp_enabled`. 12 tables; sanity check passed. **I8 (ER matches live schema) holds** — both migration 0012 columns reflected. Header updated to `v0.5-ADR-0033`. |
| D4 | `docs/api/openapi.json` | REGENERATED | `make openapi` re-run via `.venv/bin/python scripts/generate_openapi.py`. Includes `PUT /mcp/auth` with `McpAuthRequest` / `McpAuthStateResponse` schemas; `McpInfoResponse` now has `token_source` + `allow_without_token`. 19-endpoint sanity check passed. **No-token-leak check PASS**: no plaintext token, hash, or salt field exposed in any response schema. `generated_token` (one-time reveal slot) present with correct "shown once" description. |
| D4 | `backend/scripts/generate_openapi.py` | UPDATED | Sanity check extended: `/mcp/auth` added to required-path list (19 paths, was 18); `token_source` + `allow_without_token` asserted in `McpInfoResponse` + `McpAuthStateResponse`; explicit no-leak check for suspicious field names. Script now fails CI if any of these are absent or a secret field appears. |
| D4 | `backend/scripts/generate_er.py` | UPDATED | Header string updated to `v0.5-ADR-0033` to reflect Alembic 0012 in generated file metadata. |
| D7 | `docs/adr/README.md` | UP-TO-DATE | ADR-0033 row added (Accepted, 2026-06-30); header date updated to 2026-06-30. ADR-0032 row amended with "superseded in part" pointer to ADR-0033. |
| D7 | `docs/adr/0029-remote-mcp-over-http.md` | AMENDED | One-line "superseded in part by ADR-0033" note added to the Status line (§2.2 mount-only-when-token-set condition is now allow-aware). |
| D7 | `docs/adr/0032-remote-mcp-runtime-toggle.md` | AMENDED | One-line "superseded in part by ADR-0033" note added to the Status line (§2.4 token-floor clamp is now allow-aware). |
| D6b | `docs/DEPLOY.md` | UPDATED | Header updated to `v0.5-ADR-0033 \| 2026-06-30`. §5 section title/intro updated to reference ADR-0033; §5.1 Prerequisites updated (UI-generated token path documented; env bootstrap explained as fallback). New **§5.9** added: UI token management (`PUT /mcp/auth` generate/rotate/clear lifecycle; `MCP_AUTH_TOKEN` bootstrap fallback; `allow_without_token` private-source behavior; Cloudflare tunnel always requires token; `MCP_TRUSTED_PROXIES` XFF trust). §3.2 migration list updated (migrations 0011 + 0012 described). §2.1 env var table: `MCP_TRUSTED_PROXIES` row added. §12 ADR range updated to ADR-0033. |
| D1/D3 | architecture / sequences | N/A-UNCHANGED | No new container/process/port; no new flow. The new endpoint is inside the existing FastAPI boundary; the gating logic extends the existing `_BearerAuthMiddleware`. |

**No-token-leak verification:**

| Check | Result |
|-------|--------|
| `grep -c '"mcp_access_token_hash"' docs/api/openapi.json` | 0 — hash column name not exposed in spec |
| `McpAuthStateResponse` properties contain no field named `plaintext_token`, `raw_token`, `token_value`, `hash_value`, `salt_value` | PASS — confirmed by automated script check |
| `McpInfoResponse` properties: same check | PASS |
| `generated_token` field description contains "ONCE" | PASS — one-time reveal semantics documented |

**I8 invariant check:** `docs/er/schema.mmd` `VAULT_STATE` entity matches `backend/app/models.py` `VaultState` class — `mcp_access_token_hash` + `mcp_allow_without_token` + `remote_mcp_enabled` all present. Migration file `0012_vault_state_mcp_access_token_and_allow.py` confirmed in `backend/alembic/versions/`. I8 holds.

**Verdict: PASS.** D2 regenerated (vault_state +2 cols), D4 regenerated (PUT /mcp/auth + /mcp/info fields, no token leak), D1/D3 unchanged, D7 ADR index + cross-refs updated, D6b DEPLOY updated (§5.9 new, MCP_TRUSTED_PROXIES added). I8 holds.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-30 | ADR-0033 docs gate**

---

## M5 Post-Phase — ADR-0032 (remote-MCP runtime toggle + URL) — DOCS GATE: PASS

> Gate run: 2026-06-29 (orchestrator-completed after the tech-writer agent was interrupted).
> Scope: ADR-0032 adds the ONE schema change of this batch — a `remote_mcp_enabled` boolean on `vault_state` (Alembic **0011**) — plus `PUT /mcp/remote` and three new `GET /mcp/info` fields (`token_configured`, `remote_enabled`, `mount_path`). Architect confirmed: no C4/topology change (D1 unchanged).

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D2 | `docs/er/schema.mmd` | REGENERATED | `make er` re-run; `vault_state` now carries `remote_mcp_enabled`. 12 tables, sanity check passed. **I8 (ER matches live schema) holds** — migration 0011 applied to the model. |
| D4 | `docs/api/openapi.json` | REGENERATED | `make openapi` re-run; includes `PUT /mcp/remote` (`McpRemoteStateResponse`) and the 3 new `McpInfoResponse` fields. Valid JSON; 18-endpoint sanity check passed. |
| D7 | `docs/adr/README.md` | UP-TO-DATE | ADR-0032 row added (Accepted); ADR-0029/0030/0031 flipped Proposed→Accepted (owner decisions landed 2026-06-29). |
| D6b | `docs/DEPLOY.md` | UP-TO-DATE | Remote MCP section already documents the env floor; runtime toggle is the Settings UI counterpart (clamped off when no token). |
| D1/D3 | architecture / sequences | N/A-UNCHANGED | No new container/process/port; no new flow. The toggle is a request-gate on the existing ADR-0029 mount. |

**Verdict: PASS.** Verified live: ER + OpenAPI regenerated and grep-confirmed; backend gate transition (404→401) and UI toggle+URL confirmed in Claude preview.

---

## M5 Post-Phase — ADR-0028/0029/0030/0031 (Features A/B/C + bugfix) — DOCS GATE: PASS

> Gate run: 2026-06-29
> Scope: Four ADRs landed after M5 Phase 5 sign-off: ADR-0028 (relative API base / proxy split — bugfix), ADR-0029 (remote MCP over HTTP), ADR-0030 (embeddings toggle + lexical degrade), ADR-0031 (OpenAI-compatible embeddings adapter).
> Architect confirmed: NO schema/migration change (D2/ER unchanged); NO C4/topology change (D1 unchanged — the MCP HTTP surface mounts inside the existing FastAPI container, no new process or port).
> Scope of this gate run: D4 (openapi.json regenerated; mcp-tools.json updated), D7 ADR index (ADR-0028..0031 entries added), D6a USER.md (stale M5 feature table + sources section text), D6b DEPLOY.md (new env vars, stale migration/format/multiformat text), generate_openapi.py sanity checks.

### Per-artifact status (M5 Post-Phase)

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/` (context/container/component) | N/A-UNCHANGED | Architect confirmed: no topology change. `/mcp/server` mounts inside the existing FastAPI container (same process, same port). No new C4 box or arrow. |
| D2 | `docs/er/schema.mmd` | UP-TO-DATE (no-change) | Architect confirmed: no schema/migration change. ADR-0028/0029/0030/0031 are pure config + in-seam adapters. Last migration remains 0010 (review_items). 12 tables. I8 invariant holds. |
| D3 | `docs/sequences/` | N/A-UNCHANGED | No new sequence diagrams required. ADR-0028 is transport plumbing; ADR-0029 is a mount point; ADR-0030/0031 are embedding-seam branches. Existing diagrams accurate. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (regenerated) | Regenerated via `make openapi` (backend/.venv/bin/python backend/scripts/generate_openapi.py). Enhanced sanity checks confirm: `embeddings_enabled` in EmbeddingConfigResponse (ADR-0030); `http_enabled` + `remote_write_enabled` in McpInfoResponse (ADR-0029). Valid JSON; 28 paths; version 0.5.0. |
| D4 | `docs/api/mcp-tools.json` | UP-TO-DATE (amended) | `_transport` note updated: stdio + HTTP (ADR-0029; was "HTTP deferred to v0.4"). `search_wiki` description updated: now routes through `retrieve()` with lexical degrade when `EMBEDDINGS_ENABLED=false` (ADR-0030 §2.6; was "vector lookup only, F5 deferred"). `_schema_version` bumped to v0.5. |
| D5 | `docs/screens/` | PENDING-LIVE (carry-forward) | No new UI views added by ADR-0028/0029/0030/0031. Pending M5 screenshots carry forward unchanged from Phase 5 gate. |
| D6a | `docs/USER.md` | UP-TO-DATE (amended) | Version header updated v0.4 → v0.5. Settings table: Embeddings row and API + MCP row updated (no longer "coming in M5"; now describe shipped functionality). Sources section: accepted formats updated (v0.5 multi-format list; was "v0.4 Markdown/text only"). Source Watch section: format list updated. "What is coming" table rewritten as "What shipped in M5 / What is coming in M6" — all M5 features moved to shipped, M6 remainder listed. |
| D6b | `docs/DEPLOY.md` | UP-TO-DATE (amended) | Version header updated v0.4 → v0.5. §2.1 env var table: 7 new rows added (`EMBEDDINGS_ENABLED`, `EMBEDDING_FORMAT`, `EMBEDDING_API_KEY`, `MCP_AUTH_TOKEN`, `MCP_REMOTE_WRITE_ENABLED`, `BACKEND_PROXY_TARGET`; ADR-0028/0029/0030/0031 cited). §3.2 startup text: migrations updated 0001–0008 → 0001–0010 (added 0009 deep_research, 0010 review_items descriptions). §6.4 import format list: updated from "v0.4 text/MD only" to "v0.5 + F12 binary list". §11.2 EMBEDDING_DIM: added EMBEDDING_FORMAT note for OpenAI-compat endpoints. §11.5 CLI backend: updated from "not yet implemented (M5)" to current v0.5 state. §11.7 415 error: updated accepted format list. §12 References: ADR range updated "through ADR-0020" → "through ADR-0031". |
| D7 | `docs/adr/README.md` | UP-TO-DATE (amended) | Header updated to include "Features A/B/C post-phase". ADR-0028, ADR-0029, ADR-0030, ADR-0031 rows added to index table with one-line summaries. |
| D7 | `docs/adr/0028-*.md` through `0031-*.md` | UP-TO-DATE | Files confirmed present (pre-existing from solution-architect). No formatting changes required; content is complete. |
| generate_openapi.py | `backend/scripts/generate_openapi.py` | UP-TO-DATE (enhanced) | Sanity checks extended: `embeddings_enabled`, `http_enabled`, `remote_write_enabled` now asserted as present in the component schemas. `/mcp/info` and `/config/embedding` added to the required-paths list. Total required paths: 18 (was 16). |

### Three-field verification (ADR-0029 / ADR-0030 confirmation)

| Field | Schema | Line in openapi.json | Confirmed |
|-------|--------|----------------------|-----------|
| `embeddings_enabled` | `EmbeddingConfigResponse` | 1837 | YES — bool, description references `EMBEDDINGS_ENABLED` env var |
| `http_enabled` | `McpInfoResponse` | 2522 | YES — bool, description references `MCP_AUTH_TOKEN` (ADR-0029 §2.2) |
| `remote_write_enabled` | `McpInfoResponse` | 2527 | YES — bool, description references `MCP_REMOTE_WRITE_ENABLED` (ADR-0029 §2.3) |

All three fields confirmed in the regenerated spec. Sanity check script asserts them and exits non-zero on absence.

### I8 invariant check (ER matches schema)

ADR-0028/0029/0030/0031 make no DB schema change (confirmed by architect). No Alembic migration file for any of these ADRs. `backend/app/models.py` unchanged by this batch. The ER diagram (`docs/er/schema.mmd`, 12 tables) continues to match the live schema. I8 holds.

### DOCS GATE VERDICT — M5 Post-Phase

**PASS**

All D-artifacts are UP-TO-DATE, N/A-unchanged, or carry-forward PENDING-LIVE with valid rationale.

Items confirmed clean (no drift):
- D2: 12 tables, zero drift — no schema change (architect confirmed)
- D1: no topology change — HTTP MCP mounts inside existing FastAPI container (architect confirmed)
- D4 openapi.json: regenerated; three ADR-0029/0030 fields confirmed present; valid JSON; 18 required paths pass sanity checks
- D4 mcp-tools.json: transport note and search_wiki description updated to reflect ADR-0029 HTTP surface and ADR-0030 lexical degrade routing
- D6a USER.md: M5 feature table rewritten; sources format list updated; Settings section descriptions updated
- D6b DEPLOY.md: 7 new env vars documented; migration count corrected; format lists updated; stale version headers fixed
- D7 ADR README: ADR-0028/0029/0030/0031 indexed with summaries

Carry-forward pending items (unchanged from Phase 5 gate, non-blocking):
- D5: 9 M5 screenshots PENDING-LIVE (Playwright specs exist; requires live stack)
- AC-D3-CI-1 (mmdc CI step): GAP-v0.5-3 carry-forward

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-29 | M5 post-phase gate (ADR-0028/0029/0030/0031)**

---

## M5 — Milestone Docs Roll-up (all 5 phases) — CONSOLIDATED VERDICT: PASS-WITH-PENDING

> Roll-up run: 2026-06-29
> Scope: Full sprint v0.5 (M5). Five phases: Phase 1 (F5/F6/F17-chat), Phase 2 (F10), Phase 3 (F9/F12), Phase 4 (F13), Phase 5 (F1-MCP-UI). This is the LAST phase; M5 is feature-complete.
> Outstanding items: D5 live-capture screenshots (all phases) + mmdc CI step (GAP-v0.5-3).
> All code, test, and documentation artifacts are current. No functional gaps remain.

### D2 — ER Diagram (docs/er/schema.mmd)

**Status: UP-TO-DATE (zero drift). Final table count: 12.**

Tables present:

| # | Table | Introduced | ADR |
|---|-------|-----------|-----|
| 1 | PAGES | v0.1 | ADR-0002, ADR-0005 |
| 2 | VAULT_STATE | v0.1 | ADR-0005 |
| 3 | PROVIDER_CONFIG | v0.2 | ADR-0008 |
| 4 | INGEST_RUNS | v0.2 | ADR-0008, ADR-0009 |
| 5 | LINKS | v0.2 | ADR-0008 (K5) |
| 6 | EDGES | v0.3 | ADR-0012, ADR-0016 |
| 7 | CONVERSATIONS | v0.4 | ADR-0019 |
| 8 | MESSAGES | v0.4 | ADR-0019 |
| 9 | IMPORT_SCHEDULES | v0.4 | ADR-0020 |
| 10 | DEEP_RESEARCH_RUNS | v0.5 Phase 2 | ADR-0024 (Alembic 0009) |
| 11 | DEEP_RESEARCH_SOURCES | v0.5 Phase 2 | ADR-0024 (Alembic 0009) |
| 12 | REVIEW_ITEMS | v0.5 Phase 3 | ADR-0025 (Alembic 0010) |

Zero drift vs `backend/app/models.py` confirmed at Phase 3 gate (QA Phase 3 §9) and Phase 4 gate (QA Phase 4 §4.6). Phase 5 (F1-MCP-UI) introduced no DB table and no migration (ADR-0027 §4 Do-NOT #6). D2 is current for the full M5 sprint.

Last ER header: `<!-- Generated: v0.5-F9/F10 | 2026-06-29 — ADR-0025: review_items; ADR-0024: deep_research_runs/sources; ADR-0016: edges.kind; Feature A: pages.pinned -->`

### D3 — Sequence Diagrams (docs/sequences/)

**Status: UP-TO-DATE for content. mmdc CI render DEFERRED (GAP-v0.5-3).**

M5 added two new sequence diagrams:

| File | Feature | Gate | Status |
|------|---------|------|--------|
| `docs/sequences/deep-research.mmd` | F10 Deep Research | Phase 2 gate | UP-TO-DATE — 8/8 T-DOCS-051..058 assertions PASS; SearXNG/max_iter/concurrency/ingest_file/total_cost_usd annotated |
| `docs/sequences/cascade-delete.mmd` | F13 Cascade Delete | Phase 4 gate (amended) | UP-TO-DATE — 3-method/soft-delete/data_version/wikilink/frontmatter-safe elements confirmed; DEFECT-F13-002 fix annotated |

Pre-existing diagrams from v0.2/v0.3 (ingest-loop.mmd, ingest-routing.mmd, graph-recompute.mmd) are unchanged and UP-TO-DATE for their respective features.

GAP-v0.5-3: mmdc CI render step carries forward from v0.3. Both M5 `.mmd` files are valid Mermaid syntax (content-checked by `test_docs.py`). devops-engineer to wire `mmdc` before M6 sign-off. Non-blocking for M5.

### D4 — API Reference (docs/api/openapi.json)

**Status: UP-TO-DATE (zero drift). All M5 endpoints present.**

M5 endpoint inventory by phase:

| Phase | Endpoint(s) | Status |
|-------|------------|--------|
| Phase 1 | `GET /search` | PRESENT — RetrievalResponse; zero drift (QA Phase 1) |
| Phase 1 | `POST /ingest/from-text` | PRESENT — save-to-wiki path; zero drift (QA Phase 1) |
| Phase 2 | `POST /research/start` | PRESENT — 202 + run_id; zero drift (QA Phase 2) |
| Phase 2 | `GET /research/runs` | PRESENT — paginated; zero drift (QA Phase 2) |
| Phase 2 | `GET /research/runs/{run_id}` | PRESENT — detail + synthesis_text; zero drift (QA Phase 2) |
| Phase 3 | `GET /review/queue` | PRESENT — paginated, capped; zero drift (QA Phase 3) |
| Phase 3 | `POST /review/queue/{item_id}/approve` | PRESENT — status write; zero drift (QA Phase 3) |
| Phase 3 | `POST /review/queue/{item_id}/skip` | PRESENT — status write; zero drift (QA Phase 3) |
| Phase 3 | `POST /review/queue/{item_id}/deep-research` | PRESENT — 202/503; zero drift (QA Phase 3) |
| Phase 4 | `POST /pages/{page_id}/cascade-delete/preview` | PRESENT — CascadePreviewResponse (10 fields); zero drift (QA Phase 4 §4.6) |
| Phase 4 | `DELETE /pages/{page_id}` | PRESENT — CascadeDeleteResponse (4 fields); zero drift (QA Phase 4 §4.6) |
| Phase 5 | `GET /mcp/info` | PRESENT — McpInfoResponse (5 fields) + McpToolInfo (3 fields); zero drift (QA Phase 5 §5) |
| pre-M5 | `GET /config/embedding` | PRESENT — confirmed at Phase 5 as the mirror for GET /mcp/info pattern |

Total paths in openapi.json: confirmed growing phase-by-phase; all M5 paths present as of Phase 5 gate.

### D5 — Screenshots (docs/screens/)

**Status: PENDING-LIVE for all M5 captures. Non-blocking; Playwright specs exist.**

Existing committed screenshots (pre-M5, from M4/M3): shell-3panel.png, shell-3panel-selected.png, shell-collapsed-panel.png, graph-obsidian.png, graph-obsidian-node-selected.png, navrail-graph-active.png, provider-selector-open.png, settings-section.png, settings-llm-models.png, settings-import-schedule.png, ingest-section.png, ingest-upload.png, chat-conversation.png, chat-streaming.png.

M5 screenshots PENDING-LIVE (all require live stack with Playwright):

| Screenshot | Phase | Playwright Spec | Notes |
|-----------|-------|----------------|-------|
| `docs/screens/chat-citations.png` | Phase 1 | frontend/e2e/m5-screenshots.spec.ts | Chat with [n] citation superscripts visible |
| `docs/screens/save-to-wiki.png` | Phase 1 | frontend/e2e/m5-screenshots.spec.ts | Save-to-wiki panel inline result |
| `docs/screens/deep-search-running.png` | Phase 2 | frontend/e2e/deep-search.spec.ts | Run in progress |
| `docs/screens/deep-search-complete.png` | Phase 2 | frontend/e2e/deep-search.spec.ts | Synthesis expanded |
| `docs/screens/review-queue.png` | Phase 3 | frontend/e2e/m5-screenshots.spec.ts | Review queue with pending items |
| `docs/screens/upload-multiformat.png` | Phase 3 | frontend/e2e/m5-screenshots.spec.ts | Binary upload with companion creation |
| `docs/screens/cascade-delete-preview.png` | Phase 4 | frontend/e2e/m5-screenshots.spec.ts | Delete preview modal (step 1) |
| `docs/screens/cascade-delete-confirm.png` | Phase 4 | frontend/e2e/m5-screenshots.spec.ts | Delete confirm modal (step 2) |
| `docs/screens/settings-api-mcp.png` | Phase 5 | frontend/e2e/shell-m5-phase5-mcp-ui.spec.ts | API + MCP settings panel |

All M5 PENDING-LIVE screenshots fold into EC-M5-HCP / EC-M5-HCP-7: captured in a single `make screenshots` session on the live stack. No individual phase is blocked; this is a documentation completeness item.

### D7 — Architecture Decision Records (docs/adr/)

**Status: UP-TO-DATE. All M5 ADRs present and indexed.**

M5 ADRs:

| ADR | Feature | Phase | Status |
|-----|---------|-------|--------|
| 0022 | F5 4-phase retrieval + citations | Phase 1 | PRESENT — file exists; indexed in README |
| 0023 | *(reserved — skipped)* | — | ABSENT BY DESIGN — number reserved then not promoted; README now documents this explicitly. Number will not be reused. |
| 0024 | F10 Deep Research | Phase 2 | PRESENT — file exists; indexed in README |
| 0025 | F9 Review Queue + F12 Multi-format | Phase 3 | PRESENT — file exists; indexed in README (amended this phase: §3.1 enum-by-convention) |
| 0026 | F13 Cascade Delete | Phase 4 | PRESENT — file exists; indexed in README (amended this phase: §4.3 raw-split wikilink rewrite) |
| 0027 | F1-MCP-UI | Phase 5 | PRESENT — file exists; indexed in README; no amendment required (implementation matched design) |

ADR README header corrected to Sprint v0.5 (was stale v0.4) this gate run. ADR-0022 row moved to correct numerical position in the index table (was out-of-order at the bottom; now follows 0021, before 0023 skipped note). No content change to any ADR file — documentation-accuracy corrections to the index only.

### TRACEABILITY — M5 full-sprint AC status

**Status: UP-TO-DATE. All M5 phase ACs GREEN except PENDING-LIVE D5 screenshot rows.**

| Phase | ACs | GREEN | PENDING-LIVE | DEFERRED | Notes |
|-------|-----|-------|-------------|---------|-------|
| Phase 1 (F5/F6/F17-chat) | AC-F5-1..8, AC-F6-3/5, AC-F17-CHAT-1..3, AC-D3-... | All GREEN | — | — | Phase 1 gate 2026-06-29 |
| Phase 2 (F10) | AC-F10-1..4, AC-F10-6..8, AC-D3-DR-1 | All GREEN | AC-F10-8 D5 | — | Phase 2 gate 2026-06-29 |
| Phase 3 (F9/F12) | AC-F9-1..11, AC-F12-1..7, AC-F10-5 | All GREEN | — | — | Phase 3 gate 2026-06-29 |
| Phase 4 (F13) | AC-F13-1..7, AC-D3-CD-1 | All GREEN | AC-D5-M5-1..3 | AC-D3-CI-1 (mmdc) | Phase 4 gate 2026-06-29 |
| Phase 5 (F1-MCP-UI) | AC-F1-MCP-UI-1..10, AC-D7-0027-1 | 10 GREEN | AC-F1-MCP-UI-9 (screenshot) | — | Phase 5 gate 2026-06-29 |

EC-M5-22 status: CONDITIONAL (GREEN when `docs/screens/settings-api-mcp.png` is committed after live-stack capture).

All non-screenshot M5 ACs are GREEN. The mmdc CI deferral (GAP-v0.5-3, AC-D3-CI-1) is a devops carry-forward to M6 — the `.mmd` files are valid and content-checked.

### M5 Milestone Docs Consolidated Verdict

**PASS-WITH-PENDING**

Pending items (non-blocking — all require the live stack):
1. D5 screenshots: 9 M5 captures PENDING-LIVE (see table above). Fold into a single `make screenshots` session. Playwright specs all exist and committed.
2. AC-D3-CI-1 (mmdc CI render step): GAP-v0.5-3 carry-forward from v0.3. devops-engineer to wire before M6 sign-off.

All other D-artifacts are UP-TO-DATE:
- D2: 12 tables, zero drift vs models.py (confirmed through Phase 5; no Phase 5 migration)
- D3: deep-research.mmd + cascade-delete.mmd both present, valid Mermaid, architect-reviewed
- D4: all M5 endpoints present (GET /search + /ingest/from-text + /research/* + /review/queue* + /pages/{id}/cascade-delete/preview + DELETE /pages/{id} + GET /mcp/info); zero drift confirmed at each phase gate
- D7: ADRs 0022/0024/0025/0026/0027 all present and indexed; ADR-0023 skipped/reserved (documented in README); no ADR has unresolved conditions
- TRACEABILITY: all M5 phase ACs GREEN except PENDING-LIVE D5 rows

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-29 | M5 milestone docs consolidation**

---

## M5 Phase 5 — F1-MCP-UI (MCP Configuration UI) — DOCS GATE: PASS-WITH-PENDING

> Gate run: 2026-06-29
> Scope: ADR-0027 (read-only GET /mcp/info endpoint + SectionApiMcp settings panel). No Alembic migration (D2 unchanged). One new endpoint (GET /mcp/info). D4 updated. D5 spec created (PNG pending live stack). D7 ADR-0027 added.
> QA verdict: PASS-WITH-NOTES (688 backend / 557 frontend — v0.5-qa-phase5.md). Single open item (NOTE-1): D5 screenshot not yet captured.
> Architect verdict: APPROVE (unconditional — v0.5-architect-review-phase5.md). No conditions; no ADR amendment required.

### Per-artifact status (M5 Phase 5)

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/component.mmd` | N/A-unchanged | Phase 5 adds no new container or top-level component. `GET /mcp/info` is an introspection endpoint on the existing FastAPI service boundary; `SectionApiMcp` is a panel within the existing Settings section. No topology change. ADR-0027 §3 explicitly documents this as a display-only addition with no new server. |
| D2 | `docs/er/schema.mmd` | UP-TO-DATE (zero drift, no change) | No Alembic migration in F1-MCP-UI (ADR-0027 §4 Do-NOT #6: "No DB table or migration"). Confirmed by QA Phase 5 §5: "D2 is unchanged (no schema migration for this feature)." Last migration remains 0010 (review_items, Phase 3). 12 tables confirmed. See §M5P5-D2. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (zero drift) | GET /mcp/info present with operationId `get_mcp_info_mcp_info_get`; McpInfoResponse (5 fields) and McpToolInfo (3 fields) schemas present. Confirmed by QA Phase 5 §5 and architect review. See §M5P5-D4. |
| D5 | `docs/screens/settings-api-mcp.png` | PENDING-LIVE | Playwright spec `frontend/e2e/shell-m5-phase5-mcp-ui.spec.ts` created and committed. PNG capture requires live backend+frontend stack. Non-blocking per established precedent (consistent with all prior phase D5 deferrals). See §M5P5-D5. |
| D7 | `docs/adr/0027-mcp-info-ui.md` | UP-TO-DATE (no amendment) | File present. Architect review confirmed: "implementation matched the design; no amendment required." ADR-0027 indexed in docs/adr/README.md (added this gate run, including 0023 skipped note and 0022 reordering). See §M5P5-D7. |
| TRACEABILITY | Phase-5 ACs | UP-TO-DATE | AC-F1-MCP-UI-1..8 and AC-F1-MCP-UI-10 flipped PENDING → GREEN with test IDs from QA Phase 5 report §2. AC-F1-MCP-UI-9 PENDING-LIVE. AC-D7-0027-1 GREEN. EC-M5-17 updated (GET /mcp/info added). EC-M5-18 updated (688/557 final baseline). EC-M5-22 added as CONDITIONAL. EC-M5-HCP updated to include Phase 5 screenshot. See §M5P5-TRACE. |

### §M5P5-D2 — ER diagram (no-change confirmation)

File: `docs/er/schema.mmd`

ADR-0027 §4 is explicit: "No DB table or migration." The feature is pure in-process FastMCP introspection — handler body is only `await _mcp_server.list_tools()` plus field reads (architect review §2, Do-NOT #2). No new SQLAlchemy model. No Alembic migration file in `backend/alembic/versions` for this phase (confirmed by QA Phase 5 §4).

No `make er` regeneration needed or performed. The committed file from Phase 3 gate remains current.

**Result: UP-TO-DATE (no-change confirmed). D2 unchanged by Phase 5.**

### §M5P5-D4 — OpenAPI zero-drift verification

File: `docs/api/openapi.json`

QA Phase 5 §5 confirmed zero drift. Verified by grep:

| Path | Method | Present | Schema | Notes |
|------|--------|---------|--------|-------|
| `/mcp/info` | GET | YES | `McpInfoResponse` | operationId `get_mcp_info_mcp_info_get`; description references ADR-0027 §2.1 |

Schemas confirmed in `components/schemas`:
- `McpInfoResponse`: 5 fields: `server_name`, `transport`, `entry_point_command`, `tool_count`, `tools` (array of McpToolInfo). Description: "Response model for GET /mcp/info (ADR-0027 §2.1)."
- `McpToolInfo`: 3 fields: `name`, `description`, `input_schema`. Description: "Schema for a single tool entry in GET /mcp/info (ADR-0027 §2.1)."

Confirmed by architect review: "D4 (OpenAPI): `GET /mcp/info` present in `docs/api/openapi.json` (3 occurrences). Zero-drift gate satisfied for the route."

No regeneration needed; the committed file is current.

**Result: UP-TO-DATE (zero drift confirmed by QA Phase 5 §5 and architect review). D4 current.**

### §M5P5-D5 — Screenshots status (PENDING-LIVE, not blocking)

One screenshot scoped to Phase 5 requires a running Synapse stack:

- `docs/screens/settings-api-mcp.png` — Settings > API + MCP panel: Connection sub-section with transport/entry_point_command rows and copy-to-clipboard button visible; Tools sub-section with ≥4 tool rows showing name, truncated description, and param count.

`SectionApiMcp` in `SettingsPanel.tsx` is code-complete and unit-tested (SettingsPanel.test.tsx §11 — loading state, error state, snippet content, tool names, copy button, param counts all PASS per QA Phase 5 §2). Playwright spec: `frontend/e2e/shell-m5-phase5-mcp-ui.spec.ts` (created by QA). Not blocking this gate. Consistent with all prior phase precedents.

### §M5P5-D7 — ADR-0027 and README verification

File: `docs/adr/0027-mcp-info-ui.md` and `docs/adr/README.md`

ADR-0027: file present, non-empty, Accepted status, 2026-06-29 date, Sprint v0.5. Architect review confirmed no amendment required: "The implementation is a faithful, minimal realization of ADR-0027. Every Do-NOT holds." No architecture drift detected.

README corrections made this gate run:
1. Header "Last updated" corrected from "Sprint v0.4 (M4-HARD)" to "Sprint v0.5 (M5 Phase 5)".
2. ADR-0022 row moved from out-of-order position (was at table bottom) to correct numerical position (after 0021, before 0024).
3. ADR-0023 skipped/reserved note added: "reserved — skipped; no decision was promoted to ADR status; number will not be reused."
4. ADR-0027 row added.

**Result: UP-TO-DATE. ADR-0027 present and indexed; README corrected; 0023 status documented.**

### §M5P5-TRACE — TRACEABILITY.md Phase-5 rows

All Phase-5 F1-MCP-UI ACs: 9 rows GREEN, 1 row PENDING-LIVE, 1 row GREEN (D7). Test IDs sourced from `docs/sprints/v0.5-qa-phase5.md` §2 (AC coverage table).

| AC | Test file(s) | Key test ID(s) | Status |
|----|--------------|----------------|--------|
| AC-F1-MCP-UI-1 | backend/tests/test_mcp_info.py | `test_mcp_info_returns_200`, `test_mcp_info_response_shape`, `test_mcp_info_tool_count_ge_4` | GREEN |
| AC-F1-MCP-UI-2 | backend/tests/test_mcp_info.py | `test_mcp_info_server_name_is_synapse`, `test_mcp_info_entry_point_command_from_settings`, `test_mcp_info_transport_from_settings`, `test_mcp_info_tools_match_live_registry`, `test_mcp_info_tool_descriptions_match_live_registry`, `test_mcp_info_input_schema_matches_live_registry` | GREEN |
| AC-F1-MCP-UI-3 | frontend/src/tests/SettingsPanel.test.tsx §11 | loading state, error state, no comingSoon key | GREEN |
| AC-F1-MCP-UI-4 | frontend/src/tests/SettingsPanel.test.tsx §11; frontend/src/tests/mcpClient.test.ts | snippet content, server_name key, tokenised command+args (6 snippet tokenisation tests) | GREEN |
| AC-F1-MCP-UI-5 | frontend/src/tests/SettingsPanel.test.tsx §11 | 4 tool names rendered, param counts via data-param-count | GREEN |
| AC-F1-MCP-UI-6 | frontend/src/tests/SettingsPanel.test.tsx §11 (grep) | Python key-parity check; no apiMcp.comingSoon key | GREEN |
| AC-F1-MCP-UI-7 | Code inspection (main.py:1626-1652; SettingsPanel.tsx) | handler calls only `await _mcp_server.list_tools()`; frontend has no MCP call path | GREEN |
| AC-F1-MCP-UI-8 | backend/tests/test_docs.py (grep) | `/mcp/info`, `McpInfoResponse`, `McpToolInfo` confirmed in openapi.json | GREEN |
| AC-F1-MCP-UI-9 | frontend/e2e/shell-m5-phase5-mcp-ui.spec.ts | Playwright spec created; `docs/screens/settings-api-mcp.png` PNG pending live stack — QA Phase 5 NOTE-1 | PENDING-LIVE |
| AC-F1-MCP-UI-10 | backend/tests/test_mcp_info.py (14 tests); frontend/src/tests/SettingsPanel.test.tsx §11 | All 14 backend tests PASS; copy button + 4 tool rows confirmed | GREEN |
| AC-D7-0027-1 | backend/tests/test_docs.py; docs/adr/README.md | ADR-0027 file exists; indexed; no amendment required | GREEN |

Total rows updated this gate run: **11** (AC-F1-MCP-UI-1..10: 9 GREEN + 1 PENDING-LIVE; AC-D7-0027-1: 1 GREEN).
EC summary rows updated: EC-M5-17 (D4 GET /mcp/info added), EC-M5-18 (688/557 baseline), EC-M5-22 (new row — CONDITIONAL), EC-M5-HCP (updated to include Phase 5 screenshot in consolidated live-capture session).

### §M5P5-CROSS — Cross-consistency check (ADR-0027 ↔ code ↔ openapi.json ↔ ER ↔ TRACEABILITY)

| Check | Result |
|-------|--------|
| ADR-0027 §2.1 GET /mcp/info response schema ↔ McpInfoResponse in openapi.json (5 fields) + McpToolInfo (3 fields) | PASS — all fields present; descriptions reference ADR-0027 §2.1 |
| ADR-0027 Do-NOT #1 (no hardcoded tool list) ↔ handler body (`await _mcp_server.list_tools()`) ↔ T-tests (`test_mcp_info_tools_match_live_registry`) | PASS — dynamic check confirmed; no string literal tool data |
| ADR-0027 Do-NOT #6 (no DB table or migration) ↔ models.py (no new class) ↔ alembic/versions (no Phase 5 file) ↔ schema.mmd (12 tables, unchanged) | PASS — zero-migration confirmed; D2 unchanged |
| ADR-0027 §2.2 async introspection ↔ main.py:1626-1652 (`await _mcp_server.list_tools()`) ↔ architect review (no asyncio.run, no thread bridge) | PASS — pure await inside async def; confirmed by architect review §2 |
| TRACEABILITY Phase-5 test IDs ↔ QA Phase 5 report §2 (AC coverage table) | PASS — test IDs sourced verbatim from authoritative QA Phase 5 report |
| D5 screenshots PENDING-LIVE — consistent with all prior phase precedents | PASS |
| ADR README: 0022 reordered, 0023 skipped note, header corrected | PASS — no content change to any ADR file; index-only corrections |
| ADR-0027 file ↔ architect review verdict (implementation matched design; no amendment) | PASS — ADR unchanged; architect APPROVE unconditional |

**No contradictions found across ADR-0027 / openapi.json / schema.mmd / TRACEABILITY / QA Phase 5 report.**

### DOCS GATE VERDICT — M5 Phase 5

| Artifact | Status | Detail |
|----------|--------|--------|
| D1 `docs/architecture/component.mmd` | N/A-UNCHANGED | No topology change; `GET /mcp/info` introspects the existing MCP object within the FastAPI boundary; SectionApiMcp is within the existing Settings section |
| D2 `docs/er/schema.mmd` | UP-TO-DATE (no-change) | No Phase 5 migration; 12 tables confirmed; zero drift (QA Phase 5 §5) |
| D4 `docs/api/openapi.json` | UP-TO-DATE (zero drift) | GET /mcp/info with McpInfoResponse (5 fields) + McpToolInfo (3 fields); operationId + descriptions confirmed; zero drift (QA Phase 5 §5 + architect review) |
| D5 `docs/screens/settings-api-mcp.png` | PENDING-LIVE | Playwright spec committed; PNG capture requires live stack; non-blocking |
| D7 `docs/adr/0027-mcp-info-ui.md` | UP-TO-DATE (no amendment) | Present; indexed; implementation matched design; no amendment required (architect review) |
| D7 `docs/adr/README.md` | UP-TO-DATE (corrected) | Sprint header corrected to v0.5; 0022 row moved to correct position; 0023 skipped note added; 0027 row added |
| TRACEABILITY Phase-5 ACs | UP-TO-DATE | 11 rows updated: AC-F1-MCP-UI-1..8 + -10 → GREEN (9 rows); AC-F1-MCP-UI-9 → PENDING-LIVE (1 row); AC-D7-0027-1 → GREEN (1 row). EC-M5-17/18/22/HCP summary rows updated. |

**DOCS GATE: PASS-WITH-PENDING**

All required D-artifacts for M5 Phase 5 are UP-TO-DATE, N/A-unchanged, or carry-forward DEFERRED with valid rationale.

Pending items (non-blocking):
- D5: `settings-api-mcp.png` requires a live stack. Playwright spec `frontend/e2e/shell-m5-phase5-mcp-ui.spec.ts` committed. Fold into EC-M5-HCP consolidated `make screenshots` session.
- AC-D3-CI-1 (mmdc CI step): GAP-v0.5-3 carry-forward from v0.3. devops-engineer to wire before M6.

Drift found and fixed in this run:
- D7 README: stale sprint header (v0.4 → v0.5) corrected; ADR-0022 row reordered to numerical position; ADR-0023 skipped/reserved note added; ADR-0027 row added.
- TRACEABILITY: 11 Phase-5 AC rows added/updated; 4 EC summary rows updated.

Zero-drift items (no content change required):
- D2: No Phase 5 migration; confirmed unchanged.
- D4: GET /mcp/info already present with full schemas; confirmed by QA Phase 5 and architect.
- D1: No topology change.
- D7 ADR-0027 file: implementation matched design; no amendment required.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-29 | M5 Phase 5 gate + M5 milestone docs consolidation (last phase)**

---

## M5 Phase 4 — Cascade Deletion (F13) — DOCS GATE: PASS-WITH-PENDING

> Gate run: 2026-06-29
> Scope: ADR-0026 (single-pass cascade delete: 3-method match, preserve-shared, frontmatter-safe dead-wikilink rewrite, soft-delete + Qdrant delete, index.md update, data_version +1 once). No Alembic migration (D2 unchanged). Two new endpoints (POST /pages/{id}/cascade-delete/preview + DELETE /pages/{id}). D4 updated.
> QA verdict: PASS (674 backend / 526 frontend — re-verified 2026-06-29, v0.5-qa-phase4.md §10). Both P1 defects (DEFECT-F13-001 I1 violation; DEFECT-F13-002 I5 violation) FIXED and CLOSED.
> Architect verdict: APPROVE-WITH-CONDITIONS; gate-touching condition C3 (I1 defect DEFECT-F13-001) FIXED per §10 re-verification. ADR accuracy condition resolved by this gate run (§4.3 amendment).

### Per-artifact status (M5 Phase 4)

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/component.mmd` | N/A-unchanged | Phase 4 adds no new container or top-level component. `ops/cascade_delete.py` is an internal module within the existing FastAPI service boundary. The existing REST component already covers the two new routes. No topology change warranting a regen. ADR-0026 §9 handoff did not predict a D1 change. |
| D2 | `docs/er/schema.mmd` | UP-TO-DATE (zero drift) | No Alembic migration in F13 (ADR-0026 §5.1: "No new column, no new table, no Alembic migration"). Last migration was 0010 (review_items, Phase 3). D2 confirmed by QA Phase 4 §4.6: "PASS. Last Alembic migration: 0010_review_items.py". Schema unchanged. See §M5P4-D2. |
| D3 | `docs/sequences/cascade-delete.mmd` | UP-TO-DATE (amended) | File present (authored at Phase 4 start). Content verified: sequenceDiagram keyword, 3-method match, soft-delete/deleted_at, data_version bump (exact once), frontmatter-safe wikilink rewrite, wikilink notation, shared-entity warnings. DEFECT-F13-002 fix annotated in diagram. See §M5P4-D3. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (zero drift) | POST /pages/{id}/cascade-delete/preview + DELETE /pages/{id} both present with CascadePreviewResponse + CascadeDeleteResponse schemas and accurate descriptions. Confirmed by QA Phase 4 §4.6: "PASS". See §M5P4-D4. |
| D5 | `docs/screens/cascade-delete-preview.png` | PENDING-LIVE | Cascade-delete preview modal requires live stack (page must exist with wikilink references). Non-blocking per established precedent. See §M5P4-D5. |
| D5 | `docs/screens/cascade-delete-confirm.png` | PENDING-LIVE | Cascade-delete confirmation step (step 2 with shared-entity warnings visible). Non-blocking. See §M5P4-D5. |
| D7 | `docs/adr/0026-cascade-delete.md` | UP-TO-DATE (amended) | §4.3 amended: replaced false "byte-for-byte via `frontmatter.dumps`" claim with accurate description of `_rewrite_body_preserving_frontmatter` (raw `---` split) for dead-wikilink rewrites and `frontmatter.dumps(sort_keys=False)` for `_prune_sources`. Amendment note records DEFECT-F13-002. §4.4, Do-NOT #5, and §9 gate item #4 updated to match. See §M5P4-D7. |
| TRACEABILITY | Phase-4 ACs | UP-TO-DATE | AC-F13-1..7, AC-D3-CD-1 flipped PENDING → GREEN with test IDs from QA Phase 4 report. AC-D3-CI-1 flipped PENDING → DEFERRED (GAP-v0.5-3 carry-forward). EC-M5-12..15, EC-M5-17, EC-M5-18 summary rows updated. See §M5P4-TRACE. |

### §M5P4-D2 — ER diagram (no-change confirmation)

File: `docs/er/schema.mmd`

ADR-0026 §5.1 is explicit: "No new column, no new table, no Alembic migration." F13 reuses `pages.deleted_at` (ADR-0005), `links.dangling`/`links.target_page_id`, the two `edges` endpoint indexes, `pages.sources` (JSONB), and `vault_state`. All of these columns are already in the 12-table ER (confirmed at Phase 3 gate). QA Phase 4 §4.6 confirmed: "Last Alembic migration: `0010_review_items.py` (F9). No F13 migration. PASS." and "`docs/er/schema.mmd` contains `deleted_at` (pre-existing). No new columns. PASS."

No `make er` regeneration needed or performed. The committed file from Phase 3 gate remains current.

**Result: UP-TO-DATE (no-change confirmed). D2 unchanged by F13.**

### §M5P4-D3 — Cascade-delete sequence diagram verification

File: `docs/sequences/cascade-delete.mmd`

Cross-check against the implemented phases (ADR-0026 §5) and the QA Phase 4 report:

| Required element | Present | Evidence |
|-----------------|---------|---------|
| `sequenceDiagram` keyword | YES | Line 2 |
| 3-method reference match (a/b/c) | YES | `3-METHOD MATCH (logged per reference): (a)/(b)/(c)` note block; `SELECT links WHERE target_page_id=:id OR target_title=:T` |
| `soft-delete` / `deleted_at` | YES | `UPDATE pages SET deleted_at=now()` |
| `data_version` bump exactly once | YES | `bump_version() (+1 — exactly once, regardless of files touched)` |
| Wikilink notation `[[T]]` | YES | `rewrite [[T]]→T in BODY only` (original and amended step) |
| Frontmatter-safe annotation (byte-for-byte, raw split) | YES | `raw ---split → rewrite [[T]]→T in BODY only → frontmatter block byte-for-byte (I5, DEFECT-F13-002 fix)` — updated this gate run |
| `sources[]` prune with `sort_keys=False` | YES | `frontmatter.dumps(sort_keys=False)` — updated this gate run |
| Shared-entity warnings (advisory, never block) | YES | `WARN, never block — deletion proceeds (AC-F13-3)` |
| `update_index()` once | YES | `update_index() once → index.md` |
| `raw/sources/X` file deleted (AQ-v0.5-5) | YES | `delete raw/sources/X file (watcher sees DELETE, not CREATE on restart)` |
| I1 annotation (targeted writes, no rescan) | YES | `(ONLY these — AC-F13-4a/b)` |
| I2 annotation (debounced, no inline FA2) | YES | `NO inline FA2 (I2)` via Cache participant |
| I7 annotation (single pass, no loop) | YES | `%% ── APPLY (single pass — not a loop, I7)` |
| DRY-RUN / preview path shown | YES | `%% ── DRY-RUN / PREVIEW` section |
| Generation header comment | YES | Added this gate run |

The diagram correctly annotates AC-F13-6a (modal shows warnings BEFORE any action) and AC-F13-5c (404 on double-delete).

**D3 verdict: UP-TO-DATE. Amended this gate run to annotate DEFECT-F13-002 fix in the dead-wikilink rewrite and `_prune_sources` loop steps.**

### §M5P4-D4 — OpenAPI zero-drift verification

File: `docs/api/openapi.json`

QA Phase 4 §4.6 confirmed: "Both endpoints present with full descriptions. PASS."

| Path | Method | Present | Schema | Notes |
|------|--------|---------|--------|-------|
| `/pages/{page_id}/cascade-delete/preview` | POST | YES | `CascadePreviewResponse` | Read-only DRY-RUN; 200/404; description references ADR-0026 §6 and AC-F13-5/6 |
| `/pages/{page_id}` | DELETE | YES | `CascadeDeleteResponse` | Single-pass apply; 200/404; description references all 7 steps, I1/I2/I5, mandatory preview |

Schemas confirmed in `components/schemas`:
- `CascadePreviewResponse`: 10 fields matching `CascadePlan` dataclass (ADR-0026 §2): `target_page_id`, `target_title` (nullable), `target_file_path`, `will_delete`, `will_preserve_with_pruned_source`, `wikilinks_to_rewrite`, `index_entry_will_be_removed`, `raw_source_to_delete` (nullable), `shared_entity_warnings`, `match_methods_used`.
- `CascadeDeleteResponse`: 4 fields matching `CascadeResult` (ADR-0026 §2): `deleted_page_id`, `wikilinks_cleaned`, `index_entry_removed`, `shared_entity_warnings`.

No regeneration needed; QA Phase 4 confirmed the committed file is current.

**Result: UP-TO-DATE (zero drift confirmed by QA Phase 4 §4.6). D4 current.**

### §M5P4-D5 — Screenshots status (PENDING-LIVE, not blocking)

Two screenshots scoped to Phase 4 require a running Synapse stack:

- `docs/screens/cascade-delete-preview.png` — Delete preview modal (step 1): shows will_delete list, wikilinks_to_rewrite count, shared_entity_warnings (if any), Cancel and Continue buttons.
- `docs/screens/cascade-delete-confirm.png` — Delete confirmation modal (step 2): warnings repeated, Back and Delete buttons visible; confirms the two-step flow (AC-F13-6a).

`CascadeDeleteModal.tsx` is code-complete and unit-tested (CascadeDeleteModal.test.tsx — all 10 modal interaction tests PASS per QA Phase 4 §4.5). E2E Playwright spec target: `frontend/e2e/m5-screenshots.spec.ts`. Not blocking this gate. Consistent with all prior phase precedents.

### §M5P4-D7 — ADR-0026 §4.3 amendment detail

File: `docs/adr/0026-cascade-delete.md`

**Root defect (DEFECT-F13-002):** The original §4.3 claimed "The frontmatter block is re-emitted byte-for-byte via `frontmatter.dumps`." This was false: PyYAML's default `Dumper` sorts mapping keys alphabetically and changes list-item indentation on every round-trip. QA T-CD-024b proved the drift: `title, type, sources, tags, created_at` was reordered to `created_at, sources, tags, title, type` and `  - item` became `- item`.

**Fix implemented (backend-engineer, confirmed by QA Phase 4 §10):**
1. New helper `_rewrite_body_preserving_frontmatter(raw, target_title)` (lines 181-200 in `cascade_delete.py`): splits on `---\n` (maxsplit=2), keeps the frontmatter block as a raw string with no parse or round-trip, applies `_rewrite_body` to the body segment only. Returns `None` if body is unchanged (no unnecessary write).
2. Step 4 of `cascade_delete` changed from `frontmatter.loads/dumps` to `_rewrite_body_preserving_frontmatter`. Frontmatter block is now byte-identical after a dead-wikilink rewrite.
3. `_prune_sources` (line 836): `frontmatter.dumps` now passes `sort_keys=False`. Key order is preserved; only the `sources` value changes (the pruned entry is removed). Byte-identity is impossible here by design, and not claimed.

**Amendments made to ADR-0026 this gate run:**
1. §4.3: blockquote amendment note added at section top. Body rewritten to describe `_rewrite_body_preserving_frontmatter` accurately. Over-claiming removed. Distinction between the two paths (byte-identical rewrite vs. key-order-preserving prune) made explicit.
2. §4.4 heading: "same frontmatter-safe path" → "key-order-preserving frontmatter edit". Text updated to reference `sort_keys=False` and note the `sources` value change is by design.
3. Do-NOT #5: updated to describe both paths (raw split for rewrite; `sort_keys=False` for prune).
4. §9 architect gate item #4: updated to reference `_rewrite_body_preserving_frontmatter` and tests T-CD-024b (byte-identical gate) and T-CD-025 (prune-on-disk gate).

No decision changed — this is a documentation-accuracy amendment. **Condition CLEARED.**

### §M5P4-TRACE — TRACEABILITY.md Phase-4 rows

All Phase-4 F13 ACs flipped from PENDING to GREEN. Test IDs sourced from `docs/sprints/v0.5-qa-phase4.md` §2 (AC coverage map) and §10 (re-verification record after both P1 defects were closed).

| AC | Test file(s) | Key test ID(s) | Status |
|----|--------------|----------------|--------|
| AC-F13-1 | test_cascade_delete.py | T-CD-009, T-CD-010, T-CD-007/013 (rewrite), T-CD-015 (index), T-CD-011 (data_version), T-CD-014 (raw file), T-CD-024 (frontmatter integrity), T-CD-025 (prune on disk) | GREEN |
| AC-F13-2 | test_cascade_delete.py | T-CD-002 (method a), T-CD-005 (multi-source preserve), T-CD-026 (method c not called — DEFECT-F13-001 fix) | GREEN |
| AC-F13-3 | test_cascade_delete.py | T-CD-016 (shared-entity advisory; deletion proceeds) | GREEN |
| AC-F13-4 | test_cascade_delete.py | T-CD-011 (bump once), T-CD-012 (no FA2), T-CD-013/021 (files_written == rewrites), T-CD-026 (method c skip gate), T-CD-024b (byte-identical frontmatter gate — DEFECT-F13-002 fix) | GREEN |
| AC-F13-5 | test_cascade_delete.py, test_docs.py | T-CD-017/018/019/019b/020; POST preview + DELETE in openapi.json confirmed | GREEN |
| AC-F13-6 | CascadeDeleteModal.test.tsx | Loading/error/warnings-before-confirm/step-2-warnings/cancel/ESC/backdrop/confirm/back/DELETE-error | GREEN |
| AC-F13-7 | test_cascade_delete.py | T-CD-021 (3-rewrite scenario), T-CD-016 (shared-overlap), T-CD-019b (double-delete) | GREEN |
| AC-D3-CD-1 | cascade-delete.mmd content | Present; valid sequenceDiagram; all required elements confirmed; DEFECT-F13-002 annotation added | GREEN |
| AC-D3-CI-1 | CI: mmdc step | GAP-v0.5-3 carry-forward — mmdc not yet wired in CI | DEFERRED |

Total rows flipped: **9** (AC-F13-1..7: 7 GREEN; AC-D3-CD-1: 1 GREEN; AC-D3-CI-1: DEFERRED).
EC summary rows updated: EC-M5-12, EC-M5-13, EC-M5-14, EC-M5-15, EC-M5-17, EC-M5-18.
AC-D5-M5-1..3 updated from bare PENDING to PENDING-LIVE (scope clarified, consistent with precedent).

### §M5P4-CROSS — Cross-consistency check (ADR-0026 ↔ code ↔ openapi.json ↔ ER ↔ TRACEABILITY ↔ D3)

| Check | Result |
|-------|--------|
| ADR-0026 §4.3 amendment ↔ `_rewrite_body_preserving_frontmatter` in `cascade_delete.py` (raw `---` split, no PyYAML round-trip) | PASS — ADR amended to match as-built code; T-CD-024b byte-identical gate PASS confirms |
| ADR-0026 §4.4 `sort_keys=False` ↔ `_prune_sources` in `cascade_delete.py` (line 836) | PASS — ADR amended; T-CD-025 prune-on-disk PASS confirms correct key order preserved |
| ADR-0026 §3.1 "method (c) skipped when (a)∪(b) covers candidates" ↔ `_build_wikilink_rewrites` guard `if not all_found_ids:` ↔ T-CD-026 PASS | PASS — DEFECT-F13-001 fix confirmed by re-verification; guard is in production code |
| ADR-0026 §6.1 REST surface ↔ openapi.json (POST /pages/{id}/cascade-delete/preview + DELETE /pages/{id}) ↔ QA Phase 4 §4.6 | PASS — both endpoints present with correct schemas and descriptions |
| ADR-0026 §5.1 "no migration" ↔ schema.mmd (12 tables, last migration 0010) ↔ QA Phase 4 §4.6 "No F13 migration" | PASS — D2 unchanged; zero-drift confirmed |
| cascade-delete.mmd step annotations ↔ ADR-0026 §5 (7-step applied order) ↔ `cascade_delete.py` implementation | PASS — diagram updated this gate run to annotate DEFECT-F13-002 fix; all 7 steps present |
| cascade-delete.mmd I1/I2/I5/I7 annotations ↔ ADR-0026 §1 invariants owned | PASS — I1 (targeted, no rescan, one bump), I2 (debounced, no inline FA2), I5 (frontmatter-safe after fix), I7 (single pass) all annotated |
| TRACEABILITY Phase-4 test IDs ↔ QA Phase 4 report §2/§10 | PASS — test IDs sourced verbatim from authoritative QA Phase 4 report |
| D5 screenshots PENDING-LIVE — consistent with all prior phase precedents | PASS |
| ADR-0026 Do-NOT #5 and §9 gate item #4 ↔ as-built code (raw split + sort_keys=False) | PASS — both updated to describe the current production approach |
| No D1 topology change: `ops/cascade_delete.py` internal to FastAPI service boundary | PASS — confirmed by ADR-0026 §2 and architect review |

**No contradictions found across ADR-0026 / openapi.json / schema.mmd / cascade-delete.mmd / TRACEABILITY / QA Phase 4 report.**

### DOCS GATE VERDICT — M5 Phase 4

| Artifact | Status | Detail |
|----------|--------|--------|
| D1 `docs/architecture/component.mmd` | N/A-UNCHANGED | No topology change; `ops/cascade_delete.py` internal to existing FastAPI service boundary |
| D2 `docs/er/schema.mmd` | UP-TO-DATE (no-change) | No F13 migration; `deleted_at` pre-existing; 12 tables confirmed; zero drift (QA Phase 4 §4.6) |
| D3 `docs/sequences/cascade-delete.mmd` | UP-TO-DATE (amended) | Present; valid sequenceDiagram; 3-method/soft-delete/data_version/wikilink/frontmatter-safe elements confirmed; DEFECT-F13-002 fix annotated; generation header added |
| D4 `docs/api/openapi.json` | UP-TO-DATE (zero drift) | POST /pages/{id}/cascade-delete/preview + DELETE /pages/{id} with CascadePreviewResponse + CascadeDeleteResponse schemas; zero drift confirmed (QA Phase 4 §4.6) |
| D5 `docs/screens/cascade-delete-preview.png` | PENDING-LIVE | Playwright capture on live stack; CascadeDeleteModal code-complete and unit-tested; non-blocking |
| D5 `docs/screens/cascade-delete-confirm.png` | PENDING-LIVE | Playwright capture on live stack; non-blocking |
| D7 `docs/adr/0026-cascade-delete.md` | UP-TO-DATE (amended) | §4.3 rewritten: raw-split wikilink rewrite (byte-identical frontmatter); §4.4 updated: `sort_keys=False`; Do-NOT #5 + §9 gate #4 updated; DEFECT-F13-002 amendment note present |
| TRACEABILITY Phase-4 ACs | UP-TO-DATE | 9 rows updated: AC-F13-1..7 → GREEN (7 rows); AC-D3-CD-1 → GREEN (1 row); AC-D3-CI-1 → DEFERRED (1 row). EC-M5-12..15, 17, 18 summary rows updated. |

**DOCS GATE: PASS-WITH-PENDING**

All required D-artifacts for M5 Phase 4 are UP-TO-DATE, N/A-unchanged, or DEFERRED with valid rationale. Both P1 defects are closed. The ADR-0026 §4.3 accuracy amendment is complete.

Pending items (non-blocking):
- D5: `cascade-delete-preview.png` and `cascade-delete-confirm.png` require a live stack. QA/Playwright responsibility. Consistent with all prior phase precedents.
- AC-D3-CI-1 (mmdc CI step): GAP-v0.5-3 carry-forward from v0.3. devops-engineer to wire before M6 sign-off.

Drift found and fixed in this run:
- D7 ADR-0026 §4.3: `frontmatter.dumps` byte-for-byte claim replaced with accurate raw-split description; DEFECT-F13-002 amendment note added. §4.4, Do-NOT #5, §9 gate item #4 updated.
- D3 cascade-delete.mmd: dead-wikilink loop step and preserve-branch step updated to annotate DEFECT-F13-002 fix; generation header comment added.
- TRACEABILITY: 9 Phase-4 AC rows updated; 6 EC summary rows updated; AC-D5-M5-1..3 clarified to PENDING-LIVE.

Zero-drift items (no content change required):
- D2: No F13 migration; confirmed unchanged by QA Phase 4.
- D4: Both cascade-delete endpoints already present with full schemas; confirmed by QA Phase 4.
- D1: No topology change.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-29 | M5 Phase 4 gate (both P1 defects closed; ADR-0026 §4.3 accuracy amendment)**

---

## M5 Phase 3 — Review Queue (F9) + Multi-format Ingest (F12) — DOCS GATE: PASS-WITH-PENDING

> Gate run: 2026-06-29
> Scope: ADR-0025 (HITL review queue + multi-format ingest). F9: `review_items` table (Alembic 0010),
>   4 `/review/queue*` endpoints, fire-and-forget post-ingest hook, bounded query-gen (1 call/item).
>   F12: `ingest/extract.py` dispatch (pypdf/python-docx/python-pptx/openpyxl), companion `.extracted.md`
>   on upload, watcher ingests companion only. D2 updated (12 tables). D4 updated (/review/* + upload).
> QA verdict: PASS (639 backend / 483 frontend, 2026-06-29 — v0.5-qa-phase3.md).
> Architect verdict: APPROVE-WITH-CONDITIONS; architect condition (DB CHECK constraints) resolved by
>   this gate run via §3.1 amendment to ADR-0025 (enum-by-convention, house standard).

### Per-artifact status (M5 Phase 3)

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/component.mmd` | N/A-unchanged | Phase 3 adds no new container or top-level component. `ops/review.py` and `ingest/extract.py` are internal modules within the existing FastAPI service boundary. No topology change warranting a regen. ADR-0025 §8 handoff explicitly predicted no D1 change. |
| D2 | `docs/er/schema.mmd` | UP-TO-DATE (zero drift) | Regenerated; 12 tables confirmed including REVIEW_ITEMS (10 columns, both FKs). Header: `v0.5-F9/F10 | 2026-06-29`. See §M5P3-D2. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (zero drift) | Regenerated; 4 `/review/queue*` endpoints present; `/ingest/upload` description references F12 binary/placeholder flows; 3 review schemas confirmed. See §M5P3-D4. |
| D5 | `docs/screens/review-queue.png` | PENDING-LIVE | Review Queue view requires live stack. Non-blocking per established precedent. See §M5P3-D5. |
| D5 | `docs/screens/upload-multiformat.png` | PENDING-LIVE | Multi-format upload view requires live stack. Non-blocking. See §M5P3-D5. |
| D7 | `docs/adr/0025-review-queue-and-multiformat.md` | UP-TO-DATE (amended) | Architect condition (DB CHECK constraints absent) resolved: §3.1 amended to document enum-by-convention as house standard, consistent with `ingest_runs.status` and `deep_research_runs.status`. See §M5P3-D7. |
| TRACEABILITY | Phase-3 ACs | UP-TO-DATE | AC-F9-1..11, AC-F12-1..7, AC-F10-5 flipped PENDING → GREEN with concrete test IDs from QA Phase 3 report. See §M5P3-TRACE. |

### §M5P3-D2 — ER diagram zero-drift verification

File: `docs/er/schema.mmd`

Header: `<!-- Generated: v0.5-F9/F10 | 2026-06-29 — ADR-0025: review_items; ADR-0024: deep_research_runs/sources; ADR-0016: edges.kind; Feature A: pages.pinned -->`

QA Phase 3 §9 confirmed zero drift (D2 verdict: "ZERO DRIFT"). Cross-checked against `backend/app/models.py` `ReviewItem` class and the architect review migration/schema conformance table.

| Table | Columns present | ADR reference | Match |
|-------|----------------|---------------|-------|
| REVIEW_ITEMS | 10 columns: `id`, `vault_id` (String — no FK, AQ-v0.5-6), `page_id` (FK → PAGES), `item_type`, `status`, `pre_generated_query`, `deep_research_run_id` (FK → DEEP_RESEARCH_RUNS), `created_at`, `reviewed_at`, `reviewed_by` | ADR-0025 §3.1 | PASS |

Total: 12 tables (CONVERSATIONS, IMPORT_SCHEDULES, PAGES, PROVIDER_CONFIG, VAULT_STATE, INGEST_RUNS, LINKS, MESSAGES, EDGES, DEEP_RESEARCH_RUNS, DEEP_RESEARCH_SOURCES, REVIEW_ITEMS). Count increase from 11 (Phase 2) to 12 (Phase 3) is exactly +REVIEW_ITEMS.

Cross-check: `models.py` `ReviewItem` — `vault_id: Mapped[str] = mapped_column(String, …)` (no FK), `page_id` nullable FK → `pages.id`, `deep_research_run_id` nullable FK → `deep_research_runs.id`. Matches ER entity exactly.

`ingest_runs.status` and `deep_research_runs.status` confirmed as plain `Text` columns in `models.py` (lines 481 and 1019 respectively) — no CHECK constraints. House convention confirmed for the ADR amendment.

**Result: zero drift. D2 is current.**

### §M5P3-D4 — OpenAPI zero-drift verification

File: `docs/api/openapi.json`

QA Phase 3 §9 confirmed zero drift (D4 verdict: "ZERO DRIFT"). Verified programmatically: 26 total paths; review paths present:

| Path | Method | Present | Notes |
|------|--------|---------|-------|
| `/review/queue` | GET | YES | `?vault_id&limit&offset`; limit capped at 200 (I7); references ADR-0025 §3.5 |
| `/review/queue/{item_id}/approve` | POST | YES | Status-write only; no re-ingest (AC-F9-6) |
| `/review/queue/{item_id}/skip` | POST | YES | Status-write only |
| `/review/queue/{item_id}/deep-research` | POST | YES | 202; 503 on SEARXNG_URL unset; references F10 |
| `/ingest/upload` | POST | YES (updated) | Description references F12 binary/placeholder flows and companion `.extracted.md` |

Review schemas confirmed: `ReviewDeepResearchResponse`, `ReviewItemResponse`, `ReviewQueueResponse`.

**Result: zero drift. D4 is current.**

### §M5P3-D5 — Screenshots status (PENDING-LIVE, not blocking)

Two screenshots scoped to Phase 3 require a running Synapse stack:

- `docs/screens/review-queue.png` — Review Queue section: virtualized list with pending items, page title, item type, pre-generated query, Approve/Skip/Deep-Research action buttons.
- `docs/screens/upload-multiformat.png` — Sources/Ingest section: UploadZone accepting binary formats (PDF/DOCX shown), companion `.extracted.md` creation noted.

`ReviewQueueView.tsx` and `reviewStore.ts` are code-complete and unit-tested (RTL: `ReviewQueueView.test.tsx`, PASS). E2E Playwright spec target: `frontend/e2e/m5-screenshots.spec.ts`. Not blocking this gate. Consistent with all prior phase precedents.

### §M5P3-D7 — ADR-0025 amendment detail (architect condition)

File: `docs/adr/0025-review-queue-and-multiformat.md`

**Architect condition (Phase 3 review, item 1):** DB-level CHECK constraints on `review_items.item_type` and `review_items.status` were specified in the original ADR §3.1 but absent from migration 0010. Architect offered two resolution paths: (a) add CHECK constraints, or (b) amend ADR to document enum-by-convention.

**Resolution: (b) — ADR-0025 §3.1 amended.**

Basis for choosing (b): `ingest_runs.status` (`models.py:481`) and `deep_research_runs.status` (`models.py:1019`) are both plain `Text` columns with Literal-typed handlers and no DB CHECK — identical pattern to `review_items`. This is the established Synapse house convention. No follow-up migration needed.

**Amendments made:**
1. §3.1 table rows for `item_type` and `status`: text changed from "CHECK constraint" to "Enforced at app level by handler `Literal` types — no DB CHECK constraint (see §3.1 amendment note)."
2. §3.1 blockquote amendment note added: cites the house convention (`ingest_runs.status` / `deep_research_runs.status`), states no migration is required, records condition resolution.
3. §8 Conditions section: resolution paragraph appended confirming the condition is cleared.

No decision was changed — this is a documentation-accuracy amendment. **Condition CLEARED.**

### §M5P3-TRACE — TRACEABILITY.md Phase-3 rows

All Phase-3 ACs flipped from PENDING to GREEN. Test IDs sourced from `docs/sprints/v0.5-qa-phase3.md` (coverage tables §1 and §2).

| AC | Test file(s) | Key test ID(s) | Status |
|----|--------------|----------------|--------|
| AC-F9-1 | test_models_schema.py | T-RV-001 (`test_enqueues_pending_row`, `test_enqueues_without_query`, `test_enqueue_is_not_singleton`) | GREEN |
| AC-F9-2 | test_ingest_review_queue.py | T-RV-006 (`test_hook_exception_does_not_raise`) | GREEN |
| AC-F9-3 | test_review_api.py, test_docs.py, reviewClient.test.ts | T-RV-007..011, T-RV-013..014, T-RV-016; 4 review endpoints in openapi.json | GREEN |
| AC-F9-4 | test_ingest_review_queue.py | T-RV-002..005, T-RV-015 | GREEN |
| AC-F9-5 | ReviewQueueView.test.tsx, reviewStore.test.ts | TanStack Virtual `useVirtualizer`; pagination tests | GREEN |
| AC-F9-6 | test_review_api.py, reviewStore.test.ts | T-RV-009 (`mock_ingest.call_count == 0`); `test_does_not_call_ingest_endpoint` | GREEN |
| AC-F9-7 | ReviewQueueView section routing | Static: `/review/*` not on `/ingest/*`; separate section confirmed | GREEN |
| AC-F9-8 | test_review_integration.py | `test_approve_sets_status` + `test_skip_sets_status` + `test_deep_research_stores_run_id_on_item` | GREEN |
| AC-F9-9 | test_review.py (I6 test) | `TestI6NoIsinstanceBranching::test_no_isinstance_branching_in_review` | GREEN |
| AC-F9-10 | test_review_api.py | T-RV-008 (`test_get_queue_limit_capped_at_200` → 422 on limit=201) | GREEN |
| AC-F9-11 | test_review_api.py, reviewStore.test.ts, reviewClient.test.ts | T-RV-012; 503 distinct handling; 503 client test | GREEN |
| AC-F12-1 | test_extract.py | T-EXT-001..006, T-EXT-012..014 | GREEN |
| AC-F12-2 | test_upload.py | `test_pdf_allowed_f12`, `test_docx_allowed_f12`, T-UPLOAD-005, T-EXT-007 | GREEN |
| AC-F12-3 | test_upload.py | T-UPLOAD-007 (`overwritten=True` on re-upload) | GREEN |
| AC-F12-4 | test_upload_and_schedule.py | T-UPLOAD-F12-1 (`test_upload_binary_creates_companion_and_preserves_original` — added this gate) | GREEN |
| AC-F12-5 | test_code_quality.py | T-EXT-009 (pyproject.toml static guard + deps in .venv) | GREEN |
| AC-F12-6 | test_extract.py | T-EXT-001..004, T-EXT-007 | GREEN |
| AC-F12-7 | test_code_quality.py | `TestStaticGuard::test_no_format_lib_imports_outside_extract` + `test_no_unstructured_added` (T-EXT-009) | GREEN |
| AC-F10-5 | test_review_integration.py | T-RV-013 (`test_deep_research_returns_202_with_run_id`), T-RV-014 (`test_deep_research_stores_run_id_on_item`), T-RV-012 (503 orphan-free path) | GREEN |

Total rows flipped: **19** (AC-F9-1..11: 11 rows; AC-F12-1..7: 7 rows; AC-F10-5: 1 row).

### §M5P3-CROSS — Cross-consistency check (ADR-0025 ↔ code ↔ openapi.json ↔ ER ↔ TRACEABILITY)

| Check | Result |
|-------|--------|
| ADR-0025 §3.1 `review_items` columns ↔ `schema.mmd` REVIEW_ITEMS entity (10 columns, 2 FKs) | PASS — all 10 columns present; `vault_id` String no-FK (AQ-v0.5-6); `page_id` FK → PAGES; `deep_research_run_id` FK → DEEP_RESEARCH_RUNS |
| ADR-0025 §3.1 amendment: enum-by-convention ↔ `models.py` `Text` columns (no CHECK) ↔ `ingest_runs.status` + `deep_research_runs.status` (same pattern) | PASS — house convention confirmed; ADR amended |
| ADR-0025 §3.2 exactly-one-call bound (I7) ↔ T-RV-002 `call_count == 1` instrumented test ↔ `review.py:_single_chat_call` | PASS — proven by call_count instrumentation (QA Phase 3 §3) |
| ADR-0025 §3.3 fire-and-forget hook ↔ T-RV-006 hook-exception isolation ↔ `orchestrator.py:377-387` try/except | PASS — two-level defense confirmed by architect review §3 + QA Phase 3 §4 |
| ADR-0025 §3.5 REST surface ↔ openapi.json `/review/queue*` (4 paths) ↔ TRACEABILITY AC-F9-3 | PASS — all 4 paths present with correct methods and 202/503 documentation |
| ADR-0025 §4.2 `_ALLOWED_EXTENSIONS` unchanged ↔ `upload.py` ↔ T-UPLOAD-F12-1 assertion | PASS — binary exts in separate set; watcher unchanged; companion-only ingested |
| ADR-0025 §4.4 companion frontmatter (type/title/sources) ↔ T-UPLOAD-F12-1 YAML assertions | PASS — test checks `---`, `type:`, `title:`, `sources:` in companion body |
| ADR-0025 §4.6 only 4 pure-Python libs (no unstructured) ↔ T-EXT-009 static guard ↔ pyproject.toml | PASS — static guard PASS; no `unstructured` |
| D2 12-table count: Phase 2 had 11; Phase 3 adds REVIEW_ITEMS → 12 | PASS |
| D4 openapi.json: 26 paths total; 4 new `/review/*` + updated `/ingest/upload` description | PASS — confirmed programmatically |
| TRACEABILITY Phase-3 test IDs ↔ QA Phase 3 report §1/§2 | PASS — test IDs sourced verbatim from authoritative QA Phase 3 report |
| AC-F10-5 now GREEN: Phase 3 scope fulfilled — `ops/review.py::deep_research()` wired to F10 | PASS — run_id threading safe (C1 pattern confirmed by architect review §2); 202 + DB persistence verified |
| D5 screenshots PENDING-LIVE — consistent with all prior phase precedents | PASS |
| No new C4 topology change: `ops/review.py` and `ingest/extract.py` internal to FastAPI service | PASS — architect review confirmed "No new container/component topology" |

**No contradictions found across ADR-0025 / openapi.json / schema.mmd / TRACEABILITY / QA Phase 3 report.**

### DOCS GATE VERDICT — M5 Phase 3

| Artifact | Status | Detail |
|----------|--------|--------|
| D1 `docs/architecture/component.mmd` | N/A-UNCHANGED | No topology change; `ops/review.py` and `ingest/extract.py` internal to existing FastAPI service boundary |
| D2 `docs/er/schema.mmd` | UP-TO-DATE (zero drift) | 12 tables; REVIEW_ITEMS with all 10 columns + 2 FKs confirmed; zero diff vs committed file |
| D4 `docs/api/openapi.json` | UP-TO-DATE (zero drift) | 4 `/review/queue*` endpoints + 3 review schemas + updated `/ingest/upload` description; zero diff vs committed file |
| D5 `docs/screens/review-queue.png` | PENDING-LIVE | Playwright capture on live stack; ReviewQueueView code-complete + unit-tested; non-blocking |
| D5 `docs/screens/upload-multiformat.png` | PENDING-LIVE | Playwright capture on live stack; non-blocking |
| D7 `docs/adr/0025-review-queue-and-multiformat.md` | UP-TO-DATE (amended) | §3.1 amended: DB CHECK → enum-by-convention, house standard; architect condition cleared |
| TRACEABILITY Phase-3 ACs | UP-TO-DATE | 19 rows flipped PENDING → GREEN (AC-F9-1..11, AC-F12-1..7, AC-F10-5) |

**DOCS GATE: PASS-WITH-PENDING**

All required D-artifacts for M5 Phase 3 are UP-TO-DATE or N/A-unchanged. The architect condition is cleared.

Pending items (non-blocking):
- D5: `review-queue.png` and `upload-multiformat.png` require a live stack. QA/Playwright responsibility. Consistent with all prior phase precedents.

Drift found and fixed in this run:
- D7 ADR-0025 §3.1: CHECK constraint text corrected to enum-by-convention; amendment note + condition resolution added.
- TRACEABILITY: 19 Phase-3 AC rows updated from PENDING to GREEN with authoritative test IDs from `docs/sprints/v0.5-qa-phase3.md`.

Zero-drift items (no content change required):
- D2: `schema.mmd` already contained REVIEW_ITEMS; confirmed zero diff (QA Phase 3 §9).
- D4: `openapi.json` already contained all 4 `/review/queue*` routes and updated upload description; confirmed zero diff (QA Phase 3 §9).
- D1: No topology change.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-29 | M5 Phase 3 gate (architect condition cleared)**

---

## M5 Phase 2 — Deep Research Loop (F10) — DOCS GATE: PASS-WITH-PENDING

> Gate run: 2026-06-29
> Scope: ADR-0024 (bounded SearXNG loop, fire-and-poll REST, synthesis via ingest_file seam).
>   New Alembic migration: 0009 (deep_research_runs + deep_research_sources). D2 updated.
>   New endpoints: POST /research/start, GET /research/runs, GET /research/runs/{id}. D4 updated.
>   New sequence diagram: docs/sequences/deep-research.mmd. D3 updated.
> QA verdict: PASS (576 backend / 442 frontend, 2026-06-29 — v0.5-qa-phase2.md).
> Architect verdict: APPROVE-WITH-CONDITIONS; C1 (run_id threading bug) fixed; C2 (test added); C3 (D2/D3/D4 regen) is this gate run.

### Per-artifact status (M5 Phase 2)

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/component.mmd` | N/A-unchanged | Phase 2 adds no new container or top-level component. `ops/deep_research.py` and `ops/searxng.py` are internal modules within the existing backend service boundary. The existing REST component already covers new routes on the same service. No topology change warranting a regen. |
| D2 | `docs/er/schema.mmd` | UP-TO-DATE (zero drift) | Regenerated via `backend/.venv/bin/python backend/scripts/generate_openapi.py`. Zero diff vs committed file. 11 tables confirmed including DEEP_RESEARCH_RUNS (13 ADR-0024 §7.1 columns) and DEEP_RESEARCH_SOURCES (7 ADR-0024 §7.2 columns). See §M5P2-D2. |
| D3 | `docs/sequences/deep-research.mmd` | UP-TO-DATE | File present. All 8 T-DOCS-051..058 assertions PASS (sequenceDiagram keyword, SearXNG, max_iter, concurrency, total_cost_usd, ingest_file, InferenceProvider, 202 fire-and-poll). See §M5P2-D3. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (zero drift) | Regenerated via `backend/.venv/bin/python backend/scripts/generate_openapi.py`. Zero diff vs committed file. POST /research/start, GET /research/runs, GET /research/runs/{id} confirmed present. All 6 research schemas confirmed (ResearchStartRequest, ResearchStartResponse, ResearchRunSummary, ResearchRunDetail, ResearchRunListResponse, ResearchSourceSummary). See §M5P2-D4. |
| D5 | Deep Search view PNG (`docs/screens/deep-search-*.png`) | PENDING-LIVE | Requires live stack with SearXNG reachable. Non-blocking per established precedent (same handling as all prior Playwright captures). See §M5P2-D5. |
| D7 | `docs/adr/0024-deep-research.md` + README row | UP-TO-DATE | ADR-0024 present and indexed (Accepted, 2026-06-29, v0.5). See §M5P2-D7. |
| TRACEABILITY | Phase-2 ACs | UP-TO-DATE | AC-F10-1..4, AC-F10-6..8, AC-D3-DR-1 flipped PENDING → GREEN with concrete test IDs from QA Phase 2 report. AC-F10-5 remains PENDING (Phase 3 scope — review queue integration). See §M5P2-TRACE. |

### §M5P2-D2 — ER diagram zero-drift verification

File: `docs/er/schema.mmd`

Ran `backend/.venv/bin/python backend/scripts/generate_er.py`. Output:
```
Generated docs/er/schema.mmd
Sanity check passed: all 11 tables present (PAGES, VAULT_STATE, PROVIDER_CONFIG, INGEST_RUNS,
LINKS, EDGES, CONVERSATIONS, MESSAGES, IMPORT_SCHEDULES, DEEP_RESEARCH_RUNS, DEEP_RESEARCH_SOURCES)
```
`git diff docs/er/schema.mmd` produced zero output — the committed file is identical to the
freshly generated output. The backend engineer had already committed the current version
(header: `Generated: v0.5-F10 | 2026-06-29 — ADR-0024: deep_research_runs/sources; ADR-0016: edges.kind; Feature A: pages.pinned`).

Key Phase-2 tables confirmed present in schema.mmd:

| Table | Columns present | ADR reference | Match |
|-------|----------------|---------------|-------|
| DEEP_RESEARCH_RUNS | 13 columns incl. max_iter, token_budget, converged, synthesis_text, synthesis_page_id (FK) | ADR-0024 §7.1 | PASS |
| DEEP_RESEARCH_SOURCES | 7 columns incl. run_id (FK CASCADE), fetched_content_md, relevance_score, iteration | ADR-0024 §7.2 | PASS |

Total: 11 tables (CONVERSATIONS, IMPORT_SCHEDULES, PAGES, PROVIDER_CONFIG, VAULT_STATE, INGEST_RUNS,
LINKS, MESSAGES, EDGES, DEEP_RESEARCH_RUNS, DEEP_RESEARCH_SOURCES). Count verified by the
generate_er.py sanity check assertions.

**Result: zero drift. D2 is current.**

### §M5P2-D3 — Deep Research sequence diagram verification

File: `docs/sequences/deep-research.mmd`

All 8 T-DOCS-051..058 assertions (from QA Phase 2 test_docs.py) manually verified:

| Test ID | Assertion | Result |
|---------|-----------|--------|
| T-DOCS-051 | `sequenceDiagram` keyword present | PASS |
| T-DOCS-052 | Fire-and-poll 202 pattern present | PASS — `202 {run_id}` line present |
| T-DOCS-053 | SearXNG participant present | PASS — `participant SearXNG as SearXNG (I9)` |
| T-DOCS-054 | max_iter annotation present | PASS — `[HARD CAP — I7, ADR-0024 §3.2]` |
| T-DOCS-055 | concurrency annotation present | PASS — `semaphore=3` in Step 2 |
| T-DOCS-056 | InferenceProvider participant present | PASS — `participant Provider as InferenceProvider (I6)` |
| T-DOCS-057 | ingest_file seam call present | PASS — `DR->>Ingest: ingest_file(path)` |
| T-DOCS-058 | total_cost_usd in terminal write | PASS — `UPDATE deep_research_runs SET status=…, total_cost_usd` |

Diagram also shows: fire-and-poll 202 → `asyncio.create_task` → client polls via
`GET /research/runs/{run_id}`; sufficiency check before refine; `budget_exhausted` break;
`_finalize_run_row` in `finally` (status never stuck `running` — I7, ADR-0024 §3.2);
synthesis routed through `ingest_file` not direct wiki write (I1/I5, AQ-v0.5-3).

**D3 verdict: UP-TO-DATE. All 8 T-DOCS assertions pass.**

### §M5P2-D4 — OpenAPI zero-drift verification

File: `docs/api/openapi.json`

Ran `backend/.venv/bin/python backend/scripts/generate_openapi.py`. Output:
```
Generated docs/api/openapi.json
Sanity check passed: all 10 required endpoints present
(including /search, /ingest/from-text, /ingest/upload, /import-schedule)
```
`git diff docs/api/openapi.json` produced zero output — the committed file is identical to the
freshly generated output.

Key Phase-2 paths confirmed present in openapi.json:

| Path | Method | Present | Notes |
|------|--------|---------|-------|
| `/research/start` | POST | YES | 202 response; SEARXNG_URL guard; I7 bounds in description |
| `/research/runs` | GET | YES | Paginated; vault_id filter |
| `/research/runs/{run_id}` | GET | YES | 200/404; synthesis_text null-until-done noted |

Research schemas confirmed present: `ResearchStartRequest`, `ResearchStartResponse`,
`ResearchRunSummary`, `ResearchRunDetail`, `ResearchRunListResponse`, `ResearchSourceSummary`.

The generate_openapi.py sanity check asserts only the 10 pre-F10 paths; the research endpoints
are present in addition to those 10. Path listing verified programmatically (all 21 paths
checked; `/research/start`, `/research/runs`, `/research/runs/{run_id}` all confirmed).

**Result: zero drift. D4 is current.**

### §M5P2-D5 — Screenshots status (PENDING-LIVE, not blocking)

Deep Search view PNG requires a running Synapse stack with SearXNG reachable and at least one
deep research run completed to a terminal state. The frontend component `DeepSearchView.tsx`
is code-complete and unit-tested (DeepSearchView.test.tsx, 8 test groups, PASS). E2E Playwright
spec target: `frontend/e2e/deep-search.spec.ts`.

Target screenshot file names:
- `docs/screens/deep-search-running.png` — view with a run in progress (topic input + run list)
- `docs/screens/deep-search-complete.png` — view with a completed run (synthesis_text expanded)

These require a live stack (`make screenshots` or manual Playwright run). Not blocking this gate.
Consistent with precedent established at M4-EXT, M4 Phase 1, M3, and M5 Phase 1 gates.

### §M5P2-D7 — ADR-0024 verification

File: `docs/adr/0024-deep-research.md` and `docs/adr/README.md`

| Field | Value | Correct |
|-------|-------|---------|
| ADR number | 0024 | YES |
| Title | "Deep Research: bounded multi-query SearXNG loop + ingest-seam synthesis (F10, M5 Phase 2)" | YES |
| Status | Accepted | YES |
| Date | 2026-06-29 | YES |
| Sprint | v0.5 | YES |
| Link | `0024-deep-research.md` | YES — file exists |

ADR-0024 documents: structural I7 loop cap (`for range`), SearXNG-only constraint (I9), assess
before refine (Do-NOT #8), synthesis via `ingest_file` (I1/I5), bounds frozen at run start
(AQ-v0.5-4), status never stuck `running` (`finally` block), cost ledger + $1 anomaly (ADR-0009),
and the two new Postgres tables (Alembic migration 0009). Full D2/D3/D4 cross-references present.

Note: ADR-0023 is absent from the index (no ADR-0023 file exists). This is not a gap — the
architect skipped that number; 0024 follows 0022 in this sprint's authoring sequence.

### §M5P2-TRACE — TRACEABILITY.md Phase-2 rows

AC rows flipped from PENDING to GREEN. Test IDs sourced from `docs/sprints/v0.5-qa-phase2.md`
(QA coverage table §2 and §9).

| AC | Test file(s) | Key test ID(s) | Status |
|----|--------------|----------------|--------|
| AC-F10-1 | test_deep_research.py | T-DR-001 (`test_all_six_steps_execute`) | GREEN |
| AC-F10-2 | test_deep_research.py | T-DR-002..004, T-DR-007, T-DR-010, T-DR-012 (I7 bounds suite) | GREEN |
| AC-F10-3 | test_code_quality.py, test_research_api.py | T-DR-013 (`test_no_forbidden_search_imports`); T-RA-002 (`test_503_when_searxng_url_unset`) | GREEN |
| AC-F10-4 | test_research_api.py, test_docs.py | T-RA-001, T-RA-007..010, T-RA-011..013; all 3 endpoints in openapi.json (§7 QA report) | GREEN |
| AC-F10-5 | (Phase 3 scope) | — | PENDING — review queue → deep-research action not yet implemented; explicitly deferred per QA Phase 2 report §2 note |
| AC-F10-6 | test_models_schema.py | T-PG-031..031p (17 tests, deep_research_runs); T-PG-032..032i (9 tests, deep_research_sources) | GREEN |
| AC-F10-7 | test_deep_research.py | T-DR-005..006, T-DR-008..009, T-DR-011 | GREEN |
| AC-F10-8 | DeepSearchView.test.tsx (8 groups), frontend/e2e/deep-search.spec.ts | Frontend unit PASS; E2E PENDING-LIVE | GREEN (unit); D5 PENDING-LIVE |
| AC-D3-DR-1 | test_docs.py (T-DOCS-051..058) | All 8 assertions PASS (file + keyword + SearXNG + max_iter + concurrency + cost + ingest_file + InferenceProvider) | GREEN |

Total rows flipped: **9** (AC-F10-1..4, AC-F10-6..8: 8 rows; AC-D3-DR-1: 1 row).
AC-F10-5 remains PENDING (Phase 3 scope).

### §M5P2-CROSS — Cross-consistency check (ADR-0024 ↔ code ↔ openapi.json ↔ ER ↔ TRACEABILITY)

| Check | Result |
|-------|--------|
| ADR-0024 §7.1 deep_research_runs columns ↔ schema.mmd DEEP_RESEARCH_RUNS entity | PASS — all 13 columns present; FK synthesis_page_id to pages.id present |
| ADR-0024 §7.2 deep_research_sources columns ↔ schema.mmd DEEP_RESEARCH_SOURCES entity | PASS — all 7 columns present; run_id FK with ON DELETE CASCADE noted |
| ADR-0024 §3.2 `for range(1, max_iter+1)` structural cap ↔ deep-research.mmd `[HARD CAP — I7]` annotation | PASS — diagram explicitly annotates I7 structural loop cap |
| ADR-0024 §4 SearXNG-only constraint (I9) ↔ T-DR-013 import ban + T-RA-002 503 test | PASS — code guard + API guard; both tested |
| ADR-0024 REST contract (202 + fire-and-poll) ↔ openapi.json `/research/start` (POST, 202) | PASS — openapi.json response code 202; fire-and-poll pattern in D3 |
| ADR-0024 AQ-v0.5-3 synthesis via `ingest_file` ↔ deep-research.mmd Step 6 + T-DR-009 | PASS — diagram shows `ingest_file(path)`, test asserts it is called with raw/sources path |
| ADR-0024 AQ-v0.5-4 bounds frozen ↔ deep-research.mmd `bounds frozen` note at INSERT step | PASS — diagram shows `INSERT deep_research_runs (status=running, bounds frozen, AQ-v0.5-4)` |
| openapi.json research schemas ↔ QA Phase 2 §7 schema list | PASS — all 6 schemas present (ResearchStartRequest/Response, ResearchRunSummary/Detail, ResearchRunListResponse, ResearchSourceSummary) |
| TRACEABILITY Phase-2 test IDs ↔ QA Phase 2 report §2/§9 | PASS — test IDs sourced verbatim from authoritative QA report |
| C1 fix (run_id threading) — no schema/endpoint shape change | PASS — zero diff on D2 regen and D4 regen confirms C1 fix was internal-only |
| AC-F10-5 Phase 3 scope — correctly kept PENDING | PASS — review queue integration not yet implemented; AC explicitly notes Phase 3 deferral |
| D5 screenshots PENDING-LIVE — consistent with precedent | PASS — same handling as M4-EXT, M4 Phase 1, M3, M5 Phase 1 |

**No contradictions found across ADR-0024 / openapi.json / schema.mmd / deep-research.mmd / TRACEABILITY / QA Phase 2 report.**

### DOCS GATE VERDICT — M5 Phase 2

| Artifact | Status | Detail |
|----------|--------|--------|
| D1 `docs/architecture/component.mmd` | N/A-UNCHANGED | No topology change; ops/deep_research.py and ops/searxng.py are internal modules within existing backend service boundary |
| D2 `docs/er/schema.mmd` | UP-TO-DATE (zero drift) | Regenerated; 11 tables; DEEP_RESEARCH_RUNS + DEEP_RESEARCH_SOURCES confirmed; zero diff vs committed file |
| D3 `docs/sequences/deep-research.mmd` | UP-TO-DATE | All 8 T-DOCS-051..058 assertions PASS; diagram covers all 6 pipeline steps + bounded loop + fire-and-poll + I7/I9 annotations |
| D4 `docs/api/openapi.json` | UP-TO-DATE (zero drift) | Regenerated; 3 research endpoints + 6 research schemas confirmed; zero diff vs committed file |
| D5 `docs/screens/deep-search-*.png` | PENDING-LIVE | E2E Playwright capture on live stack with SearXNG; non-blocking per established precedent |
| D7 `docs/adr/0024-deep-research.md` | UP-TO-DATE | ADR present and indexed (Accepted, 2026-06-29, v0.5); covers all I7/I9/I1/I5/I6 invariants |
| TRACEABILITY Phase-2 ACs | UP-TO-DATE | 9 rows flipped PENDING → GREEN; AC-F10-5 correctly kept PENDING (Phase 3 scope) |

**DOCS GATE: PASS-WITH-PENDING**

All required D-artifacts for M5 Phase 2 are UP-TO-DATE or N/A-unchanged. The architect
condition C3 (D2/D3/D4 regen + TRACEABILITY Phase-2 flip) is hereby cleared.

Pending items (non-blocking):
- D5: Deep Search view screenshots require a live stack with SearXNG reachable.
  QA/Playwright responsibility. Consistent with all prior phase precedents.
- AC-F10-5: Review queue → deep-research action is Phase 3 scope; PENDING is correct.

Zero-drift items (no content change required):
- D2: `schema.mmd` regen produced zero diff (backend engineer had committed the current version
  after Alembic migration 0009 was authored).
- D4: `openapi.json` regen produced zero diff (backend engineer had committed the current version
  with all three research endpoints).
- D1: No topology change in Phase 2; existing backend service boundary covers new modules.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-29 | M5 Phase 2 gate (C3 cleared)**

---

## M5 Phase 1 — Retrieval Foundation (F5 + F6-citations + F17-chat) — DOCS GATE: PASS-WITH-PENDING

> Gate run: 2026-06-29
> Scope: ADR-0022 (4-phase RAG retrieval, [n] citations, save-to-wiki, CliAgentProvider.chat()).
>   Backend-only phase. No new Alembic migration (D2 unchanged). No frontend schema changes.
>   New endpoint: GET /search. Endpoint wired: POST /ingest/from-text (pre-existing, now exercised).
>   CliAgentProvider.chat() stub replaced with full delegated streaming implementation.
> QA verdict: PASS-WITH-NOTES (514 backend / 396 frontend, 2026-06-29).
> Architect verdict: APPROVE-WITH-CONDITIONS (C1 ADR accuracy fix, C2 already done as pyproject bump).

### Per-artifact status (M5 Phase 1)

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/component.mmd` | N/A-unchanged | Phase 1 adds no new container or top-level component. `rag/retrieval.py` is an internal module within the existing backend service boundary, already represented. `GET /search` is a new route on the existing REST component. No topology change warranting a regen; C1 condition does not require D1 update. |
| D2 | `docs/er/schema.mmd` | N/A-unchanged | No Alembic migration in Phase 1. `messages.citations` column already present (reserved by ADR-0019, migration 0007). `slug` is derived in code, NOT a DB column (ADR-0022 §2.6). Last verified at M4-EXT gate (migration 0008). Zero drift. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (zero drift) | Regenerated via `python backend/scripts/generate_openapi.py` — zero diff vs committed file. GET /search and POST /ingest/from-text confirmed present. See §M5P1-D4. |
| D5 | Citation renders + save-to-wiki button | PENDING-LIVE | Phase-1 UI changes ([n] citations as `<sup>`, save-to-wiki button states) need fresh Playwright PNGs. Requires live stack. Not blocking. See §M5P1-D5. |
| D7 | `docs/adr/0022-retrieval-and-citations.md` | UP-TO-DATE (amended) | C1 condition: `.search()` → `.query_points()` code references corrected in §2.2 and §3 (AQ-v0.5-1). See §M5P1-D7. |
| TRACEABILITY | Phase-1 ACs | UP-TO-DATE | AC-F5-1..8, AC-F6-3, AC-F6-5, AC-F17-CHAT-1..3 flipped PENDING → GREEN with concrete test IDs from QA report. See §M5P1-TRACE. |

### §M5P1-D4 — OpenAPI zero-drift verification

File: `docs/api/openapi.json`

Ran `cd backend && .venv/bin/python scripts/generate_openapi.py`. Output:
```
Generated docs/api/openapi.json
Sanity check passed: all 10 required endpoints present
(including /search, /ingest/from-text, /ingest/upload, /import-schedule)
```
`git diff docs/api/openapi.json` produced zero output — the committed file is identical to the
freshly generated output. No regen needed; the backend agent had already committed the current
version.

Key Phase-1 paths confirmed present in openapi.json:

| Path | Method | Present |
|------|--------|---------|
| `/search` | GET | YES |
| `/ingest/from-text` | POST | YES |

**Result: zero drift. D4 is current.**

### §M5P1-D5 — Screenshots (PENDING-LIVE, not blocking)

Phase-1 UI changes that warrant new or updated screenshots:
- Chat messages with `[n]` citation superscripts rendered (AC-F6-3).
- Save-to-wiki button — idle / saving / saved / error states (AC-F6-5).
- Optionally: a GET /search result view showing citations with scores.

These require a running Synapse stack with at least one indexed document and a live chat session.
QA/Playwright responsibility. Tracked here; not blocking this gate per established precedent
(same handling as M4-EXT screenshots and Phase 1 shell screenshots).

Files to capture (target names):
- `docs/screens/chat-citations.png` — chat message with [n] superscripts.
- `docs/screens/save-to-wiki-active.png` — save-to-wiki button in saving/saved state.

### §M5P1-D7 — ADR-0022 amendment detail (C1)

File: `docs/adr/0022-retrieval-and-citations.md`

**C1 condition:** §2.2 and §3 (AQ-v0.5-1) referenced `qdrant.search(synapse_pages, ...)` which
does not exist in qdrant-client ≥ 1.18. The implemented code uses
`client.query_points(collection_name=…, query=vector, limit=k, with_payload=True)` reading
`response.points`.

**Amendment applied (§2.2, Phase 1 step 1):**

Old text:
> `qdrant.search(synapse_pages, vector, limit=k, with_payload=True)`. Point ids are `pages.id`.

New text:
> `client.query_points(collection_name="synapse_pages", query=vector, limit=k, with_payload=True)`,
> reading `response.points`. Note: `qdrant_client.QdrantClient.search()` was removed in
> qdrant-client 1.18; `query_points()` is the current dense top-k API (semantically identical).

**Amendment applied (§3, AQ-v0.5-1):**

Old text:
> The keyword phase = bge-m3 dense top-k.

New text:
> The keyword phase = bge-m3 dense top-k via `client.query_points()` (qdrant-client ≥ 1.12;
> `.search()` was removed in 1.18).

No decision was changed — this is a code-reference accuracy fix only. The behaviour described
(cosine top-k on the `synapse_pages` collection, same `ScoredPoint` shape) is identical.
The pyproject.toml floor `qdrant-client>=1.12` was already bumped by the backend agent (C2
of the architect conditions); this note in the ADR is consistent with that version floor.

### §M5P1-TRACE — TRACEABILITY.md Phase-1 rows

All Phase-1 ACs flipped from PENDING to GREEN. Test IDs sourced from
`docs/sprints/v0.5-qa-phase1.md` (QA coverage table §4).

| AC | Test file(s) | Test ID(s) | Status |
|----|--------------|------------|--------|
| AC-F5-1 | test_retrieval.py | test_ac_f5_1_four_phases_in_order, test_ac_f5_1_vector_seed_ranks_before_expansion | GREEN |
| AC-F5-2 | test_retrieval.py | test_ac_f5_2_pageref_fields_and_markers, test_ac_f5_2_title_falls_back_to_file_stem | GREEN |
| AC-F5-3 | test_code_quality.py | test_retrieval_does_not_import_sentence_transformers, test_retrieval_does_not_create_new_qdrant_collection, test_retrieval_uses_existing_embedding_wrapper, test_no_new_embedding_service_in_retrieval_imports | GREEN |
| AC-F5-4 | test_retrieval.py | test_ac_f5_4_budget_drops_lowest_ranked, test_ac_f5_7d_overflow_drops_until_satisfied | GREEN |
| AC-F5-5 | test_retrieval.py, test_api.py | test_ac_f5_5_data_version_unchanged, TestGetSearch::test_search_does_not_bump_data_version, TestGetSearch::test_search_data_version_in_response | GREEN |
| AC-F5-6 | test_api.py, test_docs.py | TestGetSearch::test_search_returns_200, TestGetSearch::test_search_response_has_required_fields, TestGetSearch::test_search_query_reflected_in_response, TestGetSearch::test_openapi_has_search_path | GREEN |
| AC-F5-7 | test_retrieval.py, test_api.py | test_ac_f5_7a_zero_hit_empty_context, test_ac_f5_7b_single_hit, test_ac_f5_7c_multi_page_expansion, test_ac_f5_7c_expansion_depth_hard_capped_at_2, test_ac_f5_7c_resolved_links_expansion, test_ac_f5_7d_overflow_drops_until_satisfied, TestGetSearch::test_search_0_hit_returns_empty_results | GREEN |
| AC-F5-8 | test_chat_endpoint.py | test_ac_f5_8_all_providers_receive_retrieval_context[local/api/cli], test_ac_f5_8_done_event_carries_citations_for_all_providers[local/api/cli] | GREEN |
| AC-F6-3 | test_chat.py, ChatMessage.test.tsx | TestChatCitations::test_citations_stored_in_assistant_message, TestChatCitations::test_done_event_has_citations_field, TestChatCitations::test_done_event_still_has_all_existing_fields, TestChatCitations::test_no_citations_when_retrieve_returns_empty, ChatMessage.test.tsx::decorateCitations (8 cases) | GREEN |
| AC-F6-5 | ChatMessage.test.tsx, test_api.py | saveToWiki client (4 cases), save-to-wiki button state machine (6 cases), TestIngestFromText::test_from_text_returns_202, TestIngestFromText::test_from_text_response_shape, TestIngestFromText::test_from_text_writes_to_raw_sources | GREEN |
| AC-F17-CHAT-1 | test_cli_chat.py | test_chat_streams_text_deltas_and_injects_context, test_chat_bounded_by_chat_agent_max_turns_env, test_chat_default_max_turns_is_eight, test_chat_invalid_max_turns_falls_back_to_default | GREEN |
| AC-F17-CHAT-2 | test_cli_chat.py, test_schemas.py | test_chat_returns_async_iterator_of_strings, test_chat_returns_async_iterator_for_local_and_api, test_chat_cli_no_longer_notimplemented_clean_config_error_without_key | GREEN |
| AC-F17-CHAT-3 | test_cli_chat.py | test_chat_records_real_sdk_cost_when_present, test_chat_falls_back_to_zero_cost_with_warning, test_chat_no_cost_metadata_does_not_raise | GREEN |

Total rows flipped: **13** (AC-F5-1..8: 8 rows; AC-F6-3, AC-F6-5: 2 rows; AC-F17-CHAT-1..3: 3 rows).

### §M5P1-CROSS — Cross-consistency check

| Check | Result |
|-------|--------|
| ADR-0022 §2.2 `query_points()` call matches `backend/app/rag/retrieval.py` implementation | PASS — amendment aligns ADR to code |
| ADR-0022 §3 AQ-v0.5-1 qdrant-client version note matches `backend/pyproject.toml` floor `>=1.12` | PASS |
| openapi.json `GET /search` path present and matches ADR-0022 §2.5 response schema (`query`, `context`, `results`, `data_version`, `approx_tokens`, `token_budget`) | PASS |
| openapi.json `POST /ingest/from-text` present (ADR-0022 §2.7 save-to-wiki seam) | PASS |
| TRACEABILITY Phase-1 ACs GREEN with test IDs that match QA report `docs/sprints/v0.5-qa-phase1.md` §4 | PASS — sourced directly from QA report |
| D2 (no migration): `messages.citations` column already present from migration 0007 (ADR-0019); `slug` derived in code; no new columns | PASS — consistent with ADR-0022 §2.6 |
| D5 screenshots PENDING-LIVE: consistent with precedent set at M4-EXT, M4 Phase 1, M3 gates | PASS |
| No schema drift introduced by Phase 1 (I8) | PASS — `make er` not needed; confirmed by no models.py change in git status |

**No contradictions found across ADR-0022 / openapi.json / TRACEABILITY / QA report.**

### DOCS GATE VERDICT — M5 Phase 1

| Artifact | Status | Detail |
|----------|--------|--------|
| D1 `docs/architecture/component.mmd` | N/A-UNCHANGED | No topology change in Phase 1; existing backend service boundary covers new `rag/retrieval.py` module |
| D2 `docs/er/schema.mmd` | N/A-UNCHANGED | No migration; messages.citations already present; slug is derived; last verified M4-EXT |
| D4 `docs/api/openapi.json` | UP-TO-DATE | Zero drift on regeneration; GET /search and POST /ingest/from-text present |
| D5 `docs/screens/` (citation + save-to-wiki views) | PENDING-LIVE | Playwright capture on live stack; not blocking |
| D7 `docs/adr/0022-retrieval-and-citations.md` | UP-TO-DATE | C1 amendment: `.search()` → `.query_points()` with 1.18-removal note in §2.2 and §3 |
| TRACEABILITY Phase-1 ACs | UP-TO-DATE | 13 rows flipped PENDING → GREEN with concrete test IDs from QA report |

**DOCS GATE: PASS-WITH-PENDING**

All required D-artifacts for M5 Phase 1 are UP-TO-DATE or N/A-unchanged.

Pending items (non-blocking):
- D5: `chat-citations.png` and `save-to-wiki-active.png` screenshots require a live stack.
  These are QA/Playwright responsibility. Consistent with prior phase precedents.

Drift found and fixed in this run:
- D7 ADR-0022: `.search()` code references corrected to `.query_points()` in §2.2 and §3
  (C1 architect condition). No decision changed — accuracy fix only.
- TRACEABILITY: 13 Phase-1 AC rows updated from PENDING to GREEN with authoritative test IDs.

Zero-drift items:
- D4: `openapi.json` regeneration produced zero diff (backend agent had already committed the
  current version with GET /search and POST /ingest/from-text).
- D1, D2: no topology or schema changes in Phase 1.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-29 | M5 Phase 1 gate**

---

## M4-HARD — Labeled NavRail + Provider CRUD + Settings Rebuild — DOCS GATE: PASS-WITH-PENDING

> Gate run: 2026-06-29
> Scope: ADR-0021. Frontend-only increment. 9 feature IDs:
>   F1-HARD-SETTINGS (9-section Settings), F1-HARD-COLLAPSE (panel collapse),
>   F1-HARD-PROVIDER-EDIT (LLM Models CRUD), F1-HARD-MCP-STUB, F1-HARD-NAV-ORDER,
>   F1-HARD-EMBED-STUB, F1-HARD-CONV-HISTORY, F1-HARD-NAV-LABELS (72px labeled rail),
>   F1-HARD-M5-PLACEHOLDER (M5 items removed from rail).
> No backend schema changes. No new API endpoints. No Alembic migration. D2 and D4 are
>   N/A-unchanged for this increment.

### Per-artifact status (M4-HARD)

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/component.mmd` | N/A-unchanged | Frontend-only increment. No topology change: no new backend component, no new REST route, no new container. The nav-rail width change, Settings sub-nav, and provider list are internal to the existing `settingspanel` / `navrail` / `providerclient` components already in the diagram. No regen required. |
| D2 | `docs/er/schema.mmd` | N/A-unchanged | No Alembic migration. No models.py change. Last verified at M4-EXT gate (migration 0008 / import_schedules). |
| D4 | `docs/api/openapi.json` | N/A-unchanged | No new routes. `POST /provider/config` and `DELETE /provider/config/{id}` already existed and are already in openapi.json (confirmed present from M4 Phase 2 gate). No regen required. |
| D5 | `docs/screens/navrail-graph-active.png` | UP-TO-DATE | Recaptured 2026-06-29 via Playwright. Shows 72px labeled rail (Chat/Wiki/Sources/Graph labels below icons; Graph highlighted). |
| D5 | `docs/screens/settings-section.png` | UP-TO-DATE | Recaptured 2026-06-29 via Playwright. Shows 9-section left-nav (General selected, LLM Models/Embeddings/Source Watch/API+MCP/Output/Interface/Maintenance/About visible). |
| D5 | `docs/screens/shell-3panel.png` | UP-TO-DATE | Recaptured 2026-06-29 via Playwright. Shows labeled rail with Wiki active, 3-panel layout (page tree + graph + empty inspector). |
| D5 | `docs/screens/shell-3panel-selected.png` | UP-TO-DATE | Recaptured 2026-06-29 via Playwright. Shows labeled rail with Wiki active, 3-panel layout with node selected. |
| D5 | `docs/screens/settings-llm-models.png` | UP-TO-DATE (new) | NEW screenshot 2026-06-29. Shows LLM Models section: provider rows (API/Local with Delete buttons) and "+ Add provider" button. |
| D5 | `docs/screens/shell-collapsed-panel.png` | PENDING-MANUAL-CAPTURE | See §M4-HARD-D5-note. |
| D6a | `docs/USER.md` | UP-TO-DATE | Updated this gate run. See §M4-HARD-D6a. |
| D6b | `docs/DEPLOY.md` | N/A-unchanged | No new operator configuration, no new env vars, no new volume mounts introduced by this frontend-only increment. DEPLOY.md remains current from M4-EXT gate. |
| D7 | `docs/adr/0021-labeled-navrail-and-provider-crud.md` | UP-TO-DATE | Authored by solution-architect (2026-06-29). Indexed in `docs/adr/README.md` (row 0021, Accepted). See §M4-HARD-D7. |

### §M4-HARD-D5-note — Screenshot status detail

The following screenshots were recaptured from the live app (http://localhost:5199) via
Playwright on 2026-06-29 and all show the M4-HARD UI (labeled 72px rail, 9-section Settings,
provider CRUD list):

| File | Captured | Content verified |
|------|----------|-----------------|
| `navrail-graph-active.png` | YES (2026-06-29 10:07) | Labeled rail: Chat/Wiki/Sources/Graph icons with text labels; Graph section active |
| `settings-section.png` | YES (2026-06-29 10:07) | 9-section left-nav: General selected; all 9 items visible |
| `shell-3panel.png` | YES (2026-06-29 10:07) | Labeled rail (Wiki active), 3-panel layout with page tree, graph, empty inspector |
| `shell-3panel-selected.png` | YES (2026-06-29 10:07) | Labeled rail (Wiki active), 3-panel layout with node selected in tree |
| `settings-llm-models.png` | YES (2026-06-29 10:07) | LLM Models section: provider rows with Delete buttons, + Add provider button |

PENDING-MANUAL-CAPTURE — `shell-collapsed-panel.png`:

The Playwright capture spec attempted to collapse the left panel via `[data-testid='collapse-left']`
and `[aria-label*='collapse']` selectors. Neither matched the rendered collapse chevron in the
live app. The spec screenshot fell back to the default Chat section (showing the labeled rail
and chat UI — which is informative but does not demonstrate the collapse feature).

Root cause: the collapse trigger in `AppShell.tsx` likely uses an icon button without a
`data-testid` or standardized aria-label that the generic selector could match. The existing
`shell-3panel.png` does show the collapse chevron button (`›`) on the right edge of the right
panel (visible in the screenshot), providing documentary evidence that the control exists.

Resolution: QA-engineer should add `data-testid="collapse-left-btn"` and
`data-testid="collapse-right-btn"` to the chevron buttons in `AppShell.tsx`, then recapture via
the phase-1 spec. This is a minor test-infrastructure gap, not a functional defect. The collapse
feature itself (F1-HARD-COLLAPSE) is tested by the vitest AC-HARD-COL-1..5 suite (371/371 green).

The `shell-collapsed-panel.png` file at `docs/screens/shell-collapsed-panel.png` shows the Chat
section with the labeled rail, which is useful context but is not the intended collapsed-panel view.
It is kept in the repository as a partial capture. A fully accurate replacement should be captured
when the testid is added.

### §M4-HARD-D6a — USER.md updates

Changes made this gate run to `docs/USER.md`:

- **Header comment** updated to `v0.4 M4-HARD | 2026-06-29`.
- **Core journey** rewritten: step 1 now says "you land on the Chat section by default";
  step 4 references "Wiki tree" not "Pages tree"; step 6 documents provider CRUD.
- **Interface / Navigation rail section** rewritten entirely:
  - Rail described as ~72px with persistent text labels (not icon-only).
  - Item table updated: Pages→Wiki, Ingest→Sources; "Chat" now listed first as default;
    M5 items (Search/Lint/Review/Deep Search) noted as not present in M4 rail.
- **Pages section renamed to Wiki section** throughout.
  - Added: left and right panels can be collapsed via the chevron button on their inner
    edge.
- **Graph section**: added note that it shows only the graph canvas (no tree/inspector);
  refers user to Wiki section for the combined view.
- **Ingest section renamed to Sources section** throughout.
  - "Top of the Ingest section" → "Top of the Sources section".
- **Settings section** fully rewritten:
  - Old: single flat form (context window, language, provider list, reset).
  - New: two-column layout (9-section sub-nav + content pane), with sub-sections:
    General (context window), LLM Models (editable provider list with Add/Delete),
    Output (conversation history length + language), Source Watch (scheduled import),
    plus placeholder sections (Embeddings, API+MCP, Interface, Maintenance, About).
  - New `settings-llm-models.png` screenshot embedded.
  - `settings-section.png` caption updated to "General section".
  - "Automatic import card in Settings" references updated to "Settings > Source Watch".
- **Ingesting your first document**: "Ingest section" references updated to "Sources section";
  "Automatic import card in Settings" updated to "Settings > Source Watch".
- **"After ingest" paragraph**: "Ingest section" → "Sources section"; added "or Wiki section"
  alongside Graph.
- **What is coming in M5 and M6**: added Search/Lint/Review nav items (functional logic M5),
  Vector embeddings config UI (M5), MCP server config UI (M5).

### §M4-HARD-D7 — ADR index verification (ADR-0021)

File: `docs/adr/README.md`

| Field | Value | Correct |
|-------|-------|---------|
| ADR number | 0021 | YES |
| Title | "Labeled NavRail standard + provider config CRUD contract (M4-HARD)" | YES |
| Status | Accepted | YES |
| Date | 2026-06-29 | YES |
| Sprint | v0.4 | YES |
| Link | `0021-labeled-navrail-and-provider-crud.md` | YES — file exists |

Header reads: `Last updated: 2026-06-29 · Sprint v0.4 (M4-HARD)` — correct.
Total ADRs in index: 21 (0001–0021, note: 0019 and 0020 are in reverse order in the table
relative to their authoring sequence, which is correct as authored). All Accepted. Zero gaps.

### DOCS GATE VERDICT — M4-HARD

| Artifact | Status | Drift found | Detail |
|----------|--------|-------------|--------|
| D1 `docs/architecture/component.mmd` | N/A-UNCHANGED | N/A | Frontend-only increment; no topology change; no regen required |
| D2 `docs/er/schema.mmd` | N/A-UNCHANGED | N/A | No migration; last verified M4-EXT (migration 0008) |
| D4 `docs/api/openapi.json` | N/A-UNCHANGED | N/A | No new routes; POST/DELETE /provider/config already present from M4 Phase 2 |
| D5 `docs/screens/navrail-graph-active.png` | UP-TO-DATE | YES — recaptured | Old screenshot showed icon-only 48px rail; new shows labeled 72px rail with Graph active |
| D5 `docs/screens/settings-section.png` | UP-TO-DATE | YES — recaptured | Old screenshot showed flat Settings form; new shows 9-section sub-nav with General selected |
| D5 `docs/screens/shell-3panel.png` | UP-TO-DATE | YES — recaptured | Old screenshot shows pre-M4-HARD UI; new shows labeled rail with Wiki active |
| D5 `docs/screens/shell-3panel-selected.png` | UP-TO-DATE | YES — recaptured | Old screenshot shows pre-M4-HARD UI; new shows labeled rail with selected node |
| D5 `docs/screens/settings-llm-models.png` | UP-TO-DATE (new) | N/A — new file | New screenshot showing editable provider list (F1-HARD-PROVIDER-EDIT) |
| D5 `docs/screens/shell-collapsed-panel.png` | PENDING-MANUAL-CAPTURE | N/A | Collapse chevron selector not found by Playwright; existing shell-3panel.png shows the control; full recapture pending testid addition (see §M4-HARD-D5-note) |
| D6a `docs/USER.md` | UP-TO-DATE | YES — fixed this run | Nav rail section, Settings section, section names (Wiki/Sources), provider CRUD, conversation history, panel collapse documented |
| D6b `docs/DEPLOY.md` | N/A-UNCHANGED | N/A | No new operator config; remains current from M4-EXT |
| D7 ADR-0021 row in `docs/adr/README.md` | UP-TO-DATE | NONE | Row present (architect authored); 21 ADRs, header updated to M4-HARD |

**DOCS GATE: PASS-WITH-PENDING**

All required D-artifacts for this frontend-only increment are UP-TO-DATE or N/A-unchanged.

Pending item (non-blocking):
- `shell-collapsed-panel.png`: Playwright selector did not find the collapse chevron button.
  The feature is functionally verified by vitest (371/371 green). The existing shell-3panel.png
  shows the chevron button visually. A full recapture requires adding `data-testid="collapse-left-btn"`
  / `data-testid="collapse-right-btn"` to `AppShell.tsx` and re-running the phase-1 spec. This is
  a QA-infrastructure gap; it does not block the docs gate.

Drift found and fixed in this run:
- D5: Four screenshots (navrail-graph-active, settings-section, shell-3panel, shell-3panel-selected)
  replaced with M4-HARD captures showing the labeled 72px rail and 9-section Settings.
- D5: One new screenshot (settings-llm-models.png) added.
- D6a: USER.md updated for labeled rail, Chat default, 9-section Settings, provider CRUD,
  panel collapse, conversation history control, and section renames (Wiki/Sources).

Zero-drift items (no content change required):
- D1 / D2 / D4 / D6b: frontend-only increment; no backend, schema, or OpenAPI changes.
- D7: ADR-0021 row was already in the index (architect authored it before this gate run).

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-29 | M4-HARD gate**

---

## M4-EXT — Feature U (upload) + Feature S (scheduled import) — DOCS GATE: PASS

> Gate run: 2026-06-28
> Scope: ADR-0020 (upload + scheduled import). Backend: migration 0008 (import_schedules),
>   POST /ingest/upload, GET/PUT /import-schedule, POST /import-schedule/run-now,
>   upload.py sanitizer, import_scheduler.py asyncio task, docker-compose import mount.
>   Frontend: UploadZone (Ingest section), ImportScheduleCard (Settings section).

### Per-artifact status

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D1 | `docs/architecture/component.mmd` | UP-TO-DATE | Drift found and fixed this gate run. See §M4-EXT-D1. |
| D2 | `docs/er/schema.mmd` | UP-TO-DATE (zero drift) | IMPORT_SCHEDULES table already present; header comment updated. See §M4-EXT-D2. |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (zero drift) | /ingest/upload (202), /import-schedule (GET/PUT), /import-schedule/run-now all present. See §M4-EXT-D4. |
| D5 | `docs/screens/ingest-upload.png` | PENDING QA | Not yet captured; task for QA/Playwright after frontend ships. |
| D5 | `docs/screens/settings-import-schedule.png` | PENDING QA | Not yet captured; task for QA/Playwright after frontend ships. |
| D6a | `docs/USER.md` | UP-TO-DATE | Upload and scheduled import sections added this gate run. See §M4-EXT-D6a. |
| D6b | `docs/DEPLOY.md` | UP-TO-DATE | §5 (import mount) added; env var table extended; §10.6/10.7 troubleshooting added. See §M4-EXT-D6b. |
| D7 | `docs/adr/README.md` (ADR-0020 row) | UP-TO-DATE | ADR-0020 row present (authored by solution-architect); header updated to M4-EXT. See §M4-EXT-D7. |

### §M4-EXT-D1 — component.mmd drift found and fixed

**Drift before this run:** The Phase 3 diagram (title: "Synapse v0.4 Phase 3") did not
include any M4-EXT components or routes.

**Missing items added:**
- Header comment: v0.4 M4-EXT block documenting Feature U and Feature S changes.
- Title updated to "Synapse v0.4 M4-EXT (Feature U: upload + Feature S: scheduled import — ADR-0020)".
- REST component description: added `POST /ingest/upload (202 — ADR-0020 §2)`,
  `GET/PUT /import-schedule (ADR-0020 §4.6)`, `POST /import-schedule/run-now (ADR-0020 §4.6)`.
- Postgres component description: added `import_schedules (migration 0008: enabled/source_dir/
  frequency enum/last_run_at/last_status/last_imported_count/last_error — ADR-0020 §4.1, I7)`.

**New backend components added (in `api` Container_Boundary):**
| Component | File | Key invariant |
|---|---|---|
| `uploadsanitizer` | `upload.py` | basename-only, extension allow-list (.md/.txt/.markdown → 415), containment check (422); pure function, unit-testable (ADR-0020 §2.2) |
| `importscheduler` | `import_scheduler.py` | Single asyncio lifespan task (NOT APScheduler — I9); copy→watcher path (I1/I9); MAX_FILES+MAX_SECONDS (I7); single in-flight guard (I7) |

**New frontend components added (in `fe` Container_Boundary):**
| Component | File | Key invariant |
|---|---|---|
| `uploadzone` | `ingest/UploadZone.tsx` | In Ingest section; FormData POST; client-side guard UX-only, backend authoritative; no CodeMirror (I4) |
| `importschedulecard` | `settings/ImportScheduleCard.tsx` | In Settings section; container-path text input (no host picker); dir_ok:false warning; NOT in graphStore (I3) |

**New relations added:**
- `rest → uploadsanitizer` (POST /ingest/upload validation + write flow)
- `uploadsanitizer → vaultfs` (write to raw/sources/; watcher observes)
- `rest → importscheduler` (POST /import-schedule/run-now; GET/PUT helpers)
- `importscheduler → pg` (read/write import_schedules, migration 0008)
- `importscheduler → vaultfs` (os.scandir → copy; never calls ingest_file directly)
- comment noting: scheduler writes to vaultfs; watcher observes the write and calls ingest_file() via the normal pipeline (I1/I9 — no direct relation node, handled by existing watcher→orch path)
- `ingestview → uploadzone`, `uploadzone → ingestclient`, `uploadzone → ingeststore`, `uploadzone → toast`
- `settingspanel → importschedulecard`, `importschedulecard → rest`, `importschedulecard → toast`

**I7 annotations:** ImportScheduler and ImportScheduleCard descriptions explicitly reference
MAX_FILES + MAX_SECONDS caps and single in-flight guard.
**I9 annotations:** ImportScheduler description states "NOT APScheduler" and "never calls
ingest_file directly — copy→watcher path."
**I3 annotations:** ImportScheduleCard explicitly notes "NOT in graphStore (I3)".
**I1 annotations:** ImportScheduler description states "Hash-compare before copy (I1)".

### §M4-EXT-D2 — ER diagram zero-drift verification

File: `docs/er/schema.mmd`

The backend engineer regenerated `schema.mmd` via `make er` when committing migration 0008
and `models.py` `ImportSchedule` class. The `IMPORT_SCHEDULES` entity was already present in
the committed file before this gate run.

**Cross-check: `IMPORT_SCHEDULES` vs `models.py` `ImportSchedule` vs migration 0008:**

| Column | ER present | models.py | migration 0008 | Match |
|--------|-----------|-----------|----------------|-------|
| `id` UUID PK | YES | UUID PK gen_random_uuid() | YES | YES |
| `vault_id` String NOT NULL UNIQUE | YES | String NOT NULL | UNIQUE constraint | YES |
| `enabled` boolean NOT NULL DEFAULT false | YES | Boolean NOT NULL server_default false | YES | YES |
| `source_dir` string NULL | YES | Text nullable | YES | YES |
| `frequency` string NOT NULL DEFAULT '1h' | YES | Text NOT NULL default '1h' | YES | YES |
| `last_run_at` timestamptz NULL | YES | TIMESTAMP(timezone=True) nullable | YES | YES |
| `last_status` string NULL | YES | Text nullable | YES | YES |
| `last_imported_count` int NOT NULL DEFAULT 0 | YES | Integer NOT NULL default 0 | YES | YES |
| `last_error` string NULL | YES | Text nullable | YES | YES |
| `created_at` timestamptz NOT NULL | YES | TIMESTAMP(timezone=True) NOT NULL | YES | YES |
| `updated_at` timestamptz NOT NULL | YES | TIMESTAMP(timezone=True) NOT NULL | YES | YES |

All 11 columns match. UNIQUE constraint on `vault_id` matches `uq_import_schedules_vault_id`.

**Header comment updated** from `v0.3→v0.4 transition | 2026-06-28 — ADR-0016: edges.kind;
Feature A: pages.pinned` to `v0.4 M4-EXT | 2026-06-28 — ADR-0020: import_schedules
(migration 0008); ADR-0019: conversations+messages (migration 0007); ADR-0016: edges.kind;
Feature A: pages.pinned`.

**Result: zero drift vs models.py + migration 0008. D2 is current.**

### §M4-EXT-D4 — OpenAPI zero-drift verification

File: `docs/api/openapi.json`

The backend engineer regenerated `openapi.json` via `make openapi` when committing the M4-EXT
endpoints. Key items confirmed present:

| Check | Present | Notes |
|-------|---------|-------|
| `POST /ingest/upload` path | YES | operationId: `upload_ingest_ingest_upload_post` |
| `/ingest/upload` response code | YES | **202** (async watcher-driven — implementation diverges from ADR §2.1 which specified 201 synchronous; actual implementation uses 202 + queued status; openapi.json and live code are consistent with each other) |
| `/ingest/upload` 415 documented | YES | "Only .md/.txt/.markdown accepted in v0.4; multi-format (F12) planned for M5" |
| `/ingest/upload` 413 documented | YES | "File exceeds MAX_UPLOAD_BYTES" |
| `/ingest/upload` 422 documented | YES | "Filename is empty or unsafe after sanitization" |
| `UploadResponse` schema | YES | `file_path`, `status` ("queued"), `overwritten`; note: no `page_id` (async path) |
| `GET /import-schedule` path | YES | operationId: `get_import_schedule_import_schedule_get` |
| `PUT /import-schedule` path | YES | operationId: `put_import_schedule_import_schedule_put` |
| `POST /import-schedule/run-now` path | YES | operationId: `run_import_now_import_schedule_run_now_post` |
| `ImportScheduleResponse` schema | YES | All 8 response fields present |
| `ImportSchedulePutResponse` schema | YES | Extends `ImportScheduleResponse` with `dir_ok` + `dir_message` |
| API info description | YES | References M4-EXT, ADR-0020, Feature U and S |

**Note on 201 vs 202 divergence:** ADR-0020 §2.1 specified 201 with synchronous `ingest_file`
and `page_id` in the response. The implemented endpoint returns 202 (async, watcher-driven,
no `page_id`, `status="queued"`). The committed `openapi.json` and the live code are mutually
consistent at 202. This is a known implementation divergence from the ADR; the openapi.json
is the ground truth for the live API. The ADR note in §8 of this status file records it.

**Result: zero drift between committed openapi.json and live FastAPI app. D4 is current.**

### §M4-EXT-D5 — Screenshots status

| File | Status | Notes |
|------|--------|-------|
| `docs/screens/ingest-upload.png` | PENDING QA | Must show UploadZone in the Ingest section (drag-drop zone + accepted types label + M5 note). Playwright capture after frontend ships. |
| `docs/screens/settings-import-schedule.png` | PENDING QA | Must show ImportScheduleCard in the Settings section (enabled toggle, container-path input, frequency select, last-run status). Playwright capture after frontend ships. |

The two M4-EXT screenshots are a QA/Playwright responsibility. Their absence does not
block the docs gate (consistent precedent: D5 captures are never blocking at the backend-
first phase; QA captures after the frontend ships). They are explicitly tracked here.

### §M4-EXT-D6a — USER.md updates

Sections added this gate run:

- **Ingest section** expanded: added "Uploading a document" sub-section documenting the
  drag-drop zone, accepted formats (text/markdown only v0.4, F12/M5 note), 25 MB size
  limit, and overwrite semantics. Existing "Run history" content retained.
- **Settings section** expanded: added "Automatic import" sub-section documenting
  mounted-path constraint, how to configure source_dir (container path, not host path),
  frequency options, Run-now button, scan limits (200 files / 60 s), non-recursive note,
  and text-only v0.4 restriction.
- **Ingesting your first document** section rewritten: three options (drag-and-drop,
  direct file placement, scheduled import) with clear prose; M5 multi-format note retained.

Features explicitly NOT documented as present in v0.4: PDF/DOCX/etc. ingest (F12/M5),
recursive scanning (future opt-in), and per-file async ingest result from upload (202 means
the run appears in the list, not an immediate page_id).

### §M4-EXT-D6b — DEPLOY.md updates

Changes made this gate run:

- **Header comment** updated to `v0.4 M4-EXT`.
- **§2.1 env var table**: three new rows — `MAX_UPLOAD_BYTES`, `IMPORT_SCAN_MAX_FILES`,
  `IMPORT_SCAN_MAX_SECONDS` (all I7 caps; env-configurable; defaults documented).
- **§3.2 first-run text**: updated "migrations 0001–0007" to "0001–0008"; added sentence
  about migration 0008 creating `import_schedules`.
- **New §5 "Scheduled folder import (Feature S)"**: mounted-path constraint explained
  (container sees only mounted paths; no host filesystem browse); volume mount example
  (`./import:/import:ro`); configure-the-schedule steps; scan limits table.
- **§6–§11 renumbering**: old §5–§10 renumbered to §6–§11 to accommodate the new §5.
- **§10.6**: new troubleshooting entry for `last_status="dir_missing"` (missing mount).
- **§10.7**: new troubleshooting entry for 415 (binary file type not accepted in v0.4).
- **§11 References**: updated ADR range from "0001–0019" to "0001–0020".

### §M4-EXT-D7 — ADR index verification

File: `docs/adr/README.md`

ADR-0020 row was already present (authored by solution-architect). Header updated from
"Sprint v0.4 (M4 Phase 3)" to "Sprint v0.4 (M4-EXT)".

| Field | Value | Correct |
|-------|-------|---------|
| ADR number | 0020 | YES |
| Title | "Document upload + scheduled folder import (M4-EXT)" | YES |
| Status | Accepted | YES |
| Date | 2026-06-28 | YES |
| Sprint | v0.4 | YES |
| Link | `0020-upload-and-scheduled-import.md` | YES — file exists |

Total ADRs in index: 20 (0001–0020). All Accepted. Zero gaps.

### Known divergence from ADR-0020 (documented, not a gate failure)

ADR-0020 §2.1 specified `POST /ingest/upload` → **201** synchronous with `page_id`.
The implemented endpoint returns **202** (async watcher-driven, `status="queued"`, no
`page_id`). The committed `openapi.json` and live code are consistent with each other at
202. The `UploadResponse` schema omits `page_id` and sets `status: "queued"`. This is a
known implementation-diverges-from-ADR situation; the openapi.json is the ground truth.
If the solution-architect wishes to update ADR-0020 §2.1 to reflect the actual 202
contract, that is a follow-up action — it does not block the docs gate because the docs
accurately describe the implemented behaviour.

### DOCS GATE VERDICT — M4-EXT

| Artifact | Status | Drift found | Detail |
|----------|--------|-------------|--------|
| D1 `docs/architecture/component.mmd` | UP-TO-DATE | YES — fixed this run | UploadZone, ImportScheduleCard, ImportScheduler, uploadsanitizer components + routes + import_schedules table note + 12 new Rel() entries + 1 explanatory comment added |
| D2 `docs/er/schema.mmd` | UP-TO-DATE | NONE (header updated) | IMPORT_SCHEDULES entity already present from make er; all 11 columns verified vs models.py + migration 0008; zero drift |
| D4 `docs/api/openapi.json` | UP-TO-DATE | NONE | /ingest/upload (202), GET/PUT /import-schedule, POST /import-schedule/run-now all present; UploadResponse + ImportScheduleResponse + ImportSchedulePutResponse schemas confirmed; zero drift |
| D5 `docs/screens/ingest-upload.png` | PENDING QA | N/A | QA/Playwright capture required after frontend ships; not blocking |
| D5 `docs/screens/settings-import-schedule.png` | PENDING QA | N/A | QA/Playwright capture required after frontend ships; not blocking |
| D6a `docs/USER.md` | UP-TO-DATE | YES — fixed this run | Upload section + scheduled import section + rewritten ingesting guide added |
| D6b `docs/DEPLOY.md` | UP-TO-DATE | YES — fixed this run | §5 import mount + env vars + migration note + §10.6/10.7 troubleshooting added; section renumbered |
| D7 ADR-0020 row in `docs/adr/README.md` | UP-TO-DATE | NONE (header updated) | ADR-0020 row present; 20 ADRs, zero gaps; header updated to M4-EXT |

**DOCS GATE: PASS**

Drift found and fixed in this run:
- D1: `component.mmd` was at Phase 3 level (no M4-EXT components); updated with UploadZone,
  ImportScheduleCard, ImportScheduler, uploadsanitizer, new routes, import_schedules table note,
  and 14 new relations.
- D6a: `USER.md` had no upload or scheduled import content; sections added.
- D6b: `DEPLOY.md` had no import volume mount documentation; §5 added; env vars extended.

Zero-drift artifacts (no content change required):
- D2: `schema.mmd` IMPORT_SCHEDULES entity was already present (make er had been run).
- D4: `openapi.json` M4-EXT endpoints were already present (make openapi had been run).
- D7: ADR-0020 row was already in the index.

Pending (not blocking):
- D5: Two screenshots (`ingest-upload.png`, `settings-import-schedule.png`) awaiting QA
  Playwright capture after the M4-EXT frontend components ship.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-28 | M4-EXT gate**

---

## D6 Delivery — USER.md + DEPLOY.md v0.4 draft — DOCS GATE: PASS

> Gate run: 2026-06-28
> Prerequisite: M4 PM sign-off (v0.4-pm-signoff.md) records D6 as the one genuine gap
> blocking EC-M4-HCP-6. This section records the closure of that gap.

### Per-artifact status

| ID | Artifact | Status | Notes |
|----|----------|--------|-------|
| D6a | `docs/USER.md` | UP-TO-DATE | Created this gate run. See §D6a below. |
| D6b | `docs/DEPLOY.md` | UP-TO-DATE (v0.4 draft) | Promoted from v0.1 draft this gate run. See §D6b below. |

### §D6a — docs/USER.md

File created at `docs/USER.md`. Covers:
- What Synapse is (self-organizing wiki, Karpathy pattern).
- Core user journey (ingest → graph → inspect → chat → configure provider).
- Each nav section with embedded screenshots: Pages (3-panel + selected-node), Graph
  (sigma viewer, node sizing, hover, drag), Ingest (activity list, Run Ingest, status
  badges, cost to 4dp), Chat (streaming, conversation list, Regenerate, GFM/LaTeX,
  reasoning block), Settings (context window budget split, language toggle, provider
  table, Reset).
- Provider selector header (three types: Local/API/CLI; scope: Global/Vault).
- How to ingest a first document and open the wiki in Obsidian.
- Status bar description.
- What is coming in M5 and M6 (citations, review queue, deep research, multi-format,
  cascade delete, clipper, PWA/Tauri, lint, MkDocs) — clearly labeled as not present
  in v0.4.
- Screenshots referenced from `docs/screens/` (all 10 PNGs committed by QA in Phase 2
  and Phase 3).
- Features NOT documented as present: save-to-wiki (button disabled), [n] citations
  (M5), CliAgentProvider.chat() (M5), F9 review queue (M5).

AC-D6-1 MET. AC-D6-3 MET (no [TODO] placeholders). AC-D6-4 MET (tech-writer reviewed).

### §D6b — docs/DEPLOY.md (v0.4 draft)

File updated from v0.1-only content to v0.4 draft. Changes from the v0.1 draft:
- Header promotes to "v0.4 draft" status.
- Prerequisites table updated: SearXNG noted as optional (required for M5 Deep
  Research, not M4).
- Environment variable table: added `CORS_ALLOW_ORIGINS` (shipped in v0.4 — CORS
  middleware wired in M4); `QDRANT_COLLECTION` clarified.
- §4 (Configuring an inference provider) is new: covers inserting `provider_config`
  rows via psql for Local Ollama (e.g. `qwen2.5:3b`), API (Anthropic), and
  OpenAI-compatible endpoints; explains resolution precedence; documents the Alembic
  data migration seed behavior.
- §5 TrueNAS deployment: vault bind-mount path override documented.
- Troubleshooting: added §9.4 (no provider_config hard error) and §9.5
  (CliAgentProvider.chat NotImplementedError — switch to Local/API for chat).
- Make targets: added `make screenshots`.
- References: updated to include ADRs 0001–0019 and USER.md.

AC-D6-2 MET. AC-D6-3 MET (no [TODO] placeholders). AC-D6-4 MET (tech-writer reviewed).

### DOCS GATE VERDICT — D6 delivery

| Artifact | Status | Detail |
|----------|--------|--------|
| D6a `docs/USER.md` | UP-TO-DATE | Created; covers all shipped M4 features; screenshots embedded; M5/M6 roadmap clearly labeled |
| D6b `docs/DEPLOY.md` | UP-TO-DATE (v0.4 draft) | Promoted from v0.1; provider_config setup documented; CORS_ALLOW_ORIGINS added; troubleshooting extended |

**DOCS GATE: PASS**

EC-M4-HCP-6 condition ("docs/USER.md exists and covers the core user journey") is now
MET. The PM may record D6 as closed in the M4 sign-off document and proceed to the
human checkpoint.

Overall M4 D-artifact verdict: ALL UP-TO-DATE

| ID | Artifact | Status |
|----|----------|--------|
| D1 | `docs/architecture/component.mmd` | UP-TO-DATE (Phase 3 gate) |
| D2 | `docs/er/schema.mmd` | UP-TO-DATE (Phase 3 gate; migrations 0001–0007) |
| D3 | `docs/sequences/` (ingest-loop, ingest-routing, graph-recompute) | UP-TO-DATE (M3 + M4 phase gates) |
| D4 | `docs/api/openapi.json` | UP-TO-DATE (Phase 3 gate) |
| D5 | `docs/screens/` (10 PNGs) | UP-TO-DATE (Phase 2 + Phase 3 QA captures) |
| D6a | `docs/USER.md` | UP-TO-DATE (this gate run) |
| D6b | `docs/DEPLOY.md` | UP-TO-DATE v0.4 draft (this gate run) |
| D7 | `docs/adr/README.md` + ADR-0001..0019 | UP-TO-DATE (Phase 3 gate) |

**OVERALL VERDICT: ALL UP-TO-DATE**

Deferred (not gaps — PM-approved):
- chat-think-block D5 screenshot: no `<think>`-capable model available during capture run;
  will be captured when a reasoning model is configured. Tracked in M5.
- NB-7 graph-recompute.mmd cosmetic: optional P3 polish, carried to M5.
- NB-8 component.mmd store label: optional P3 polish, carried to M5.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-28 | M4 D6 delivery gate**

---

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
