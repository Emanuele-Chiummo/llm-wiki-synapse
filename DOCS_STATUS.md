# DOCS_STATUS — Sprint 3 / v0.3 / M3 Documentation Gate

> Tech-writer sign-off for EC-M3-15 (v0.3-scope §6 / §8 sign-off register).
> Generated: 2026-06-28
> Author: tech-writer (claude-sonnet-4-6)
> Supersedes: DOCS_STATUS.md (v0.2 gate)
> Sprint branch: sprint/v0.3
> Scope reference: docs/sprints/v0.3-scope.md §8
> I8 gate: CLAUDE.md §3 invariant I8 (docs-as-DoD; ER matches live schema)

This file is the artifact the milestone gate reads. ALL UP-TO-DATE (with DEFERRED-TO-LIVE for
D5 capture explicitly tracked) means the docs gate passes. NOT UP-TO-DATE with untracked gaps
blocks the gate.

---

## 1. Per-artifact status table

| ID | Artifact | Required v0.3? | Status | Drift result | Notes |
|----|----------|----------------|--------|--------------|-------|
| D1 | `docs/architecture/component.mmd` | YES (updated) | UP-TO-DATE | ZERO DRIFT | GraphEngine (graph/engine.py), GraphCache (graph/cache.py), sigma viewer, coord/edge persistence all present. I2 annotation in header `%%` comments. Line 1 = `C4Component` (no leading HTML comment). No v0.4 features depicted. Module labels match real files. Git working tree clean. |
| D2 | `docs/er/schema.mmd` | YES (updated) | UP-TO-DATE | ZERO DRIFT | `make er` re-run via backend/.venv python3.13. Regenerated output byte-identical to committed file (git diff empty; working tree clean per `git status`). 6 tables confirmed: PAGES, VAULT_STATE, PROVIDER_CONFIG, INGEST_RUNS, LINKS, EDGES. `pages.x` / `pages.y` (DOUBLE PRECISION, nullable, ADR-0013) present. EDGES table with source_page_id, target_page_id, weight, signals confirmed (ADR-0012). |
| D3 | `docs/sequences/graph-recompute.mmd` | YES (new) | UP-TO-DATE | ZERO DRIFT | Line 1 = `sequenceDiagram`. Heading comment in `%%` (line 2). Debounce loop, cache-miss path (inline recompute, `X-Graph-Cache: miss`) and cache-hit path (pure Postgres read, `X-Graph-Cache: hit`) all present. FA2 server-side via igraph explicit. Client addNode with server coords and no client layout noted (ADR-0015). Wording polished. mmdc render deferred (no mmdc in sandbox — T-DOCS-MANUAL-003 sentinel). |
| D4 | `docs/api/openapi.json` | YES (updated) | UP-TO-DATE | ZERO DRIFT | `make openapi` re-run via backend/.venv python3.13. Regenerated output byte-identical to committed file (git diff empty; working tree clean). `GET /graph` present. GraphResponse schema complete: nodes (id/title/type/x/y), edges (source/target/weight), data_version (int), cached (bool). `X-Graph-Cache: hit\|miss` documented in 200 response headers with ADR-0014 reference. info.version = "0.3.0". |
| D5 | `docs/screens/` (Playwright PNGs) | YES — capture DEFERRED-TO-LIVE | DEFERRED-TO-LIVE | N/A — no fabrication | Harness exists (see §2). 0 PNGs committed. Acceptable: requires live browser + running stack. Run command documented in §2. EC-M3-11 remains open until Playwright runs against live stack as part of EC-M3-17 (Emanuele live confirmation). |
| D6a | `docs/USER.md` | NO (v0.4) | N/A | N/A | Not required at M3. Deferred to v0.4 per v0.3-scope §3 and §9. |
| D6b | `docs/DEPLOY.md` | NO (v0.4) | N/A | N/A | Not required at M3. Deferred to v0.4. DEPLOY.md remains DRAFT-tagged from prior sprint. |
| D7 | `docs/adr/` (ADRs 0012–0015 + README) | YES (new) | UP-TO-DATE | ZERO DRIFT | ADR-0012 (4-signal formula), ADR-0013 (FA2 + coord persistence, I2), ADR-0014 (GraphCache + GET /graph), ADR-0015 (no client-side layout sigma contract) all present, non-empty, reference I2/FA2/igraph. ADR README index updated: all 15 ADRs listed (0001–0015), correct sprint/status/date/summary. Consistent format throughout. |

---

## 2. D5 deferred-capture status (DEFERRED-TO-LIVE)

The Playwright harness is written, verified present, and does not need to be re-run in this
sandbox. Files confirmed:

- `frontend/e2e/graph-perf.spec.ts` — E2E spec capturing G2/G4 metrics and D5 PNGs
- `frontend/playwright.config.ts` — Config: testDir `./e2e`; baseURL `SYNAPSE_FRONTEND_URL`
  (default `http://localhost:5173`); headless Chromium; trace on first retry

The spec writes two screenshots to `docs/screens/`:
- `docs/screens/graph-viewer-initial.png` — graph rendered, no node selected
- `docs/screens/graph-viewer-node-selected.png` — after node click, tooltip/drawer visible

To populate D5 (satisfies EC-M3-11, T-E2E-D5-001..003, G2 runtime T-E2E-G2-001/002,
G4 T-E2E-G4-001/002):

```bash
# 1. Seed 200-node/500-edge fixture
cd backend && python scripts/seed_graph_fixture.py --nodes 200 --edges 500 --db-url $DATABASE_URL

# 2. Start backend
uvicorn app.main:app --port 8000

# 3. Start frontend
cd frontend && npm run dev

# 4. Run Playwright (captures PNGs into docs/screens/ and asserts G2/G4)
cd frontend && npx playwright test e2e/graph-perf.spec.ts --config playwright.config.ts
```

After the run, commit the two PNGs in `docs/screens/`. This closes EC-M3-11.

---

## 3. make er / make openapi drift check (I8 gate)

| Script | Python used | Exit code | Drift vs committed file | Sanity output |
|--------|------------|-----------|------------------------|---------------|
| `make er` (`backend/scripts/generate_er.py`) | `backend/.venv/bin/python3` (3.13) | 0 | **ZERO** — `git status` clean | "all 6 tables present (PAGES, VAULT_STATE, PROVIDER_CONFIG, INGEST_RUNS, LINKS, EDGES)" |
| `make openapi` (`backend/scripts/generate_openapi.py`) | `backend/.venv/bin/python3` (3.13) | 0 | **ZERO** — `git status` clean | "all 5 required endpoints present (including GET /graph)" |

Runtime note: the system Python at `/usr/bin/python3` is 3.9 and is incompatible with the
codebase (requires Python 3.11+ per CLAUDE.md §12; `X | Y` union type syntax and
`datetime.UTC` require 3.10+/3.11+). Both scripts must be run via `backend/.venv/bin/python3`
(3.13). This is a devops note, not a drift issue — both scripts succeeded with the venv and
produced zero drift.

---

## 4. D2 ER — 6-table confirmation (I8 gate)

| Table | ER name | Present | Key v0.3 columns confirmed |
|-------|---------|---------|---------------------------|
| `pages` | PAGES | YES | x (double, nullable, ADR-0013), y (double, nullable, ADR-0013). All prior columns retained. |
| `vault_state` | VAULT_STATE | YES | data_version (int, monotonic); updated_at. |
| `provider_config` | PROVIDER_CONFIG | YES | Unchanged from v0.2. No api_key column. |
| `ingest_runs` | INGEST_RUNS | YES | Unchanged from v0.2. total_cost_usd, converged, cost_anomaly present. |
| `links` | LINKS | YES | Unchanged from v0.2. source_page_id, target_title, target_page_id (nullable FK), dangling. |
| `edges` | EDGES | YES | id (PK), vault_id, source_page_id (FK), target_page_id (FK), weight (double), signals (jsonb), created_at. |

**I8 verdict: ER matches live SQLAlchemy schema. Zero drift. 6 tables confirmed.**

---

## 5. D4 OpenAPI — GET /graph confirmation

| Check | Result |
|-------|--------|
| `GET /graph` path present | YES (path `/graph`, method `get`) |
| `GraphResponse` `$ref` on 200 response | YES |
| `nodes` array of `GraphNodeResponse` | YES — required fields: id, title, type, x, y |
| `edges` array of `GraphEdgeResponse` | YES — required fields: source, target, weight |
| `data_version` (integer, required) | YES |
| `cached` (boolean, required) | YES |
| `X-Graph-Cache: hit\|miss` in response headers | YES — documented in 200 headers object with description "hit\|miss — mirrors the cached field (ADR-0014 §5)" |
| Synchronous 200 only (no 202) for `/graph` | YES — no 202 response defined for this path (AQ-v0.3-3) |
| info.version | "0.3.0" (updated from v0.2 "0.1.0" — resolved) |
| info.description | References F4, I2, FA2, igraph, ADR-0014. Accurate. |

---

## 6. D1 component diagram — detailed validity check

File: `docs/architecture/component.mmd`

| Check | Result |
|-------|--------|
| Line 1 = `C4Component` (no leading HTML comment) | PASS |
| Heading comment in `%%` on line 2 | PASS — `%% <!-- Generated: v0.3 sprint 3 \| 2026-06-28 -->` |
| `GraphEngine` component, labelled `graph/engine.py` | PASS |
| `GraphCache` component, labelled `graph/cache.py` | PASS |
| `Coord/edge persistence` component, labelled `in engine.py` | PASS |
| `Graph viewer` (sigma viewer) in Frontend boundary | PASS — `React 19 + Vite + sigma.js`, reads precomputed coords |
| `Graph Zustand store` in Frontend boundary | PASS — selectors + shallow equality (I3) |
| Postgres component: `pages (+x,y v0.3)` and `edges (v0.3)` noted | PASS |
| I2 annotation in header comments | PASS |
| ADR references (0012/0013/0014/0015) in component descriptions | PASS |
| notify_bump() rel from orch to gcache | PASS |
| recompute() rel from gcache to gengine (debounced, I7) | PASS |
| GET /graph rel from rest to gcache (hit/miss) | PASS |
| viewer fetches GET /graph rel | PASS |
| No v0.4 features shown (no 3-panel, no chat, no provider selector UI, no CodeMirror) | PASS |
| Module labels match real backend file paths | PASS |

Style note: the heading comment on line 2 embeds `<!-- -->` inside `%%` (i.e., `%% <!-- ... -->`). The outer `%%` makes it a Mermaid comment so the renderer never sees the HTML. Safe for GitHub and Obsidian. Not a blocking issue.

---

## 7. D3 sequence diagram — detailed validity check

File: `docs/sequences/graph-recompute.mmd`

| Check | Result |
|-------|--------|
| Line 1 = `sequenceDiagram` | PASS |
| Heading comment in `%%` (line 2) | PASS |
| `title` line present | PASS |
| `autonumber` enabled | PASS |
| Participants: Watcher/Ingest, vault_state.data_version, GraphCache, GraphEngine, Postgres, Client | PASS |
| Ingest bump: W→DV (+1 on successful upsert, ADR-0005) | PASS |
| Ingest notify: W→GC (notify_bump(), in-process trigger) | PASS |
| Debounce loop: N bumps within window reset fire_at (burst collapse) | PASS |
| Recompute fires ONCE after debounce window (max 1 in-flight + 1 pending, I7) | PASS |
| GE reads pages + links from Postgres (no vault walk, I1) | PASS |
| 4-signal weight note (direct×3 + source×4 + AdamicAdar×1.5 + type×1, ADR-0012) | PASS |
| Seeded FA2 via igraph noted | PASS |
| GE→PG: ONE txn — UPDATE pages.x/y per node (column upsert, I1) + replace edges rows | PASS |
| Cache marker stamp after recompute; one-follow-up rule (I7) | PASS |
| GET /graph cache-miss path: inline recompute → `cached:false` + `X-Graph-Cache: miss` | PASS |
| GET /graph cache-hit path: pure Postgres read → `cached:true` + `X-Graph-Cache: hit` | PASS |
| Client addNode with server coords, NO client layout (ADR-0015) | PASS |
| I2 invariant annotation at Note over GE | PASS |
| mmdc render check | DEFERRED (no mmdc in sandbox; T-DOCS-MANUAL-003 sentinel in QA report) |

---

## 8. D7 ADR completeness detail

| ADR | Sprint | Status | Key decisions locked |
|-----|--------|--------|---------------------|
| 0012 | v0.3 | Accepted 2026-06-28 | 4-signal additive weight formula; EDGES table persistence; edge inclusion rule (weight > 0); worked fixture |
| 0013 | v0.3 | Accepted 2026-06-28 | FA2 only in engine.py via python-igraph; fixed seed=42; pages.x/y columns; incremental semantics (row-level, not coord-stable); single bounded pass (I7) |
| 0014 | v0.3 | Accepted 2026-06-28 | In-process debounce on data_version bump (5s, injectable clock); bounded queue (1 in-flight + 1 pending, I7); synchronous GET /graph 200; X-Graph-Cache header; GET /graph response contract |
| 0015 | v0.3 | Accepted 2026-06-28 | Zero client-side layout (P0 block; static bundle grep + architect review); sigma renders precomputed coords in ONE WebGL canvas; Zustand selectors + shallow equality (I3 pre-compliance); G2/G4 by construction |

ADR README index: all 15 ADRs (0001–0015) listed with correct title, status, date, sprint, summary. No gaps in the index.

---

## 9. Cross-consistency sweep

| Check | Result |
|-------|--------|
| No doc claims client-side FA2 or layout anywhere | PASS — ADR-0015, component.mmd, graph-recompute.mmd, openapi.json description all state FA2 is server-side only (I2). No contradiction. |
| GET /graph contract consistent across openapi.json / ADR-0014 §6 / architecture doc §6 / sequence diagram | PASS — nodes/edges/data_version/cached schema identical; X-Graph-Cache header identical. |
| pages.x/y in ER matches models.py `Page.x`, `Page.y` (Double, nullable) and ADR-0013 §3 | PASS |
| EDGES table in ER matches models.py `Edge` class (source_page_id, target_page_id, weight, signals, vault_id, created_at) and ADR-0012 | PASS |
| 4-signal formula consistent across ADR-0012 / architecture doc §2 / component.mmd GraphEngine description / sequence diagram Note | PASS — direct×3 + source×4 + AA×1.5 + type×1 everywhere |
| 6 tables in ER consistent with models.py (Page, VaultState, ProviderConfig, IngestRun, Link, Edge) | PASS |
| Debounce default 5s in sequence diagram matches ADR-0014 §2 and architecture doc §5 | PASS |
| Synchronous 200 for /graph (no 202) in sequence diagram matches openapi.json and ADR-0014 §5 / AQ-v0.3-3 | PASS |
| component.mmd Postgres description mentions `pages (+x,y v0.3)` and `edges (v0.3)` consistent with ER 6-table count | PASS |
| ADR README index: 15 ADRs listed; 0012–0015 all Accepted, Sprint v0.3, date 2026-06-28 | PASS |
| CLAUDE.md §4 F4 multipliers (direct×3, source-overlap×4, Adamic-Adar×1.5, type-affinity×1) match ADR-0012 formula | PASS |
| I9 compliance: python-igraph (R9) in ADR-0013 and component.mmd; sigma.js (R10) in ADR-0015 and component.mmd; Playwright (R16) in D5 harness; no vis.js/cytoscape/d3-force/networkx referenced anywhere | PASS |
| I2 is consistently enforced: no doc, no ADR, no diagram ever implies or permits a client-side layout | PASS |
| Playwright harness + playwright.config.ts exist at expected paths | PASS (verified by file system check) |
| docs/screens/ directory exists | PASS (T-DOCS-046 sentinel green per QA report) |

**No contradictions found.** All D-artifacts are mutually consistent and consistent with CLAUDE.md.

---

## 10. DOCS GATE VERDICT

| Item | Verdict |
|------|---------|
| D1 component.mmd (v0.3 update) | UP-TO-DATE |
| D2 schema.mmd (v0.3 update — 6 tables, pages.x/y + EDGES) | UP-TO-DATE — ZERO DRIFT |
| D3 graph-recompute.mmd (new) | UP-TO-DATE |
| D4 openapi.json (v0.3 update — GET /graph + X-Graph-Cache) | UP-TO-DATE — ZERO DRIFT |
| D5 screenshots | DEFERRED-TO-LIVE (harness present and verified; 0 PNGs; run command in §2) |
| D6a/D6b USER.md / DEPLOY.md | N/A (v0.4) |
| D7 ADRs 0012–0015 + README | UP-TO-DATE |
| Cross-consistency sweep | PASS — no contradictions |
| ER drift (I8 gate) | ZERO |
| OpenAPI drift | ZERO |

**DOCS GATE: UP-TO-DATE**

D5 screenshot capture is DEFERRED-TO-LIVE. This is explicitly tracked in §2, matches the QA
report deferral (T-E2E-D5-001..003 DEFERRED-TO-LIVE in v0.3-qa-report.md §5), and is the
established precedent for live-infra tests in this project. It does NOT block the docs gate;
it holds only EC-M3-11 (D5 screenshots committed) which will close when Emanuele runs the
Playwright suite against the live stack as part of EC-M3-17 (human checkpoint).

All other required v0.3 D-artifacts are UP-TO-DATE with zero drift.

**Signed: tech-writer (claude-sonnet-4-6) | 2026-06-28 | EC-M3-15**
