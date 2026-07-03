# Synapse — Product Backlog
> Maintained by: product-manager
> Last updated: 2026-07-03 (Sprint 12 / v1.2 scope locked — SPRINT-v1.2-SCOPE.md; Sprint 11 / v1.1 scope locked — SPRINT-v1.1-SCOPE.md; Sprint 10 / v1.0 scope locked — SPRINT-v1.0-SCOPE.md; Sprint 9 / v0.9.0 shipped; Sprint 8 / v0.8.0 shipped; Sprint 7 / v0.7.0 shipped)
> Source of truth for feature IDs: CLAUDE.md §4
> Sprint roadmap: CLAUDE.md §8

---

## Legend

| Status | Meaning |
|--------|---------|
| in-progress | Active in current sprint |
| backlog | Scoped but not yet started |
| blocked | Dependency unresolved |
| done | Exit criteria verified by PM |
| done-pending-live-demo | Automated gates green; remaining condition is live-infra or human verification (no code change needed) |

---

## Sprint 12 — v1.2 — M12 "Home & Insights"

Goal: glanceable home dashboard (vault KPIs, AI spend, per-domain breakdowns); domain
vocabulary machinery (controlled vocabulary stored in app_config, auto-tag on ingest,
one-time bounded backfill); server release channel (GHCR image publish + StatusResponse
version field + frontend mismatch notice + optional Watchtower auto-update block);
ingest polling dedup carry-over fix.

**Sprint status: SCOPE LOCKED 2026-07-03**
Scope file: docs/sprints/SPRINT-v1.2-SCOPE.md
Branch: sprint/v1.2 (cut from main after v1.1.0 tag)
Prerequisite: EC-M11-1..EC-M11-HCP met by Emanuele.

### R12-1 — Home dashboard

| Field | Value |
|-------|-------|
| Feature ID | F18 (new), F1, F4, F16 |
| Sprint | v1.2 |
| Status | backlog |
| Effort | L |
| Acceptance criteria | AC-R12-1-1 through AC-R12-1-10 (SPRINT-v1.2-SCOPE.md §3) |

New endpoints: GET /stats/overview (pages_by_type, total_links, communities_count,
review_queue_depth, lint_findings_open, monthly_ai_spend_usd, recent_activity,
data_version), GET /stats/sections (per-domain breakdown). Frontend HomeDashboard.tsx
as default landing section with NavRail Home icon. Plain SVG sparkline (no charting
library). Domain section card click dispatches filter navigation.

### R12-2 — Domain vocabulary + auto-tag

| Field | Value |
|-------|-------|
| Feature ID | F18, F17 (I6 — classification via InferenceProvider), K6 (pages.tags domain/ convention) |
| Sprint | v1.2 |
| Status | blocked — ADR-0054 required before backend code |
| Effort | L |
| Acceptance criteria | AC-R12-2-0 through AC-R12-2-8 (SPRINT-v1.2-SCOPE.md §3) |

Vocabulary stored as domain_vocabulary key in app_config (ADR-0053 mechanism; new
allowed key added to ALLOWED_CONFIG_KEYS). Ingest step classifies each new page into
0..N domains via InferenceProvider (one bounded call, max_iter=1, I7). Writes
"domain/<term>" into pages.tags. POST /ops/backfill-domains one-time bounded backfill
(max_pages cap, token_budget_usd, total_cost_usd logged). Empty vocabulary = feature
dormant (zero provider calls). Settings > Advanced vocabulary editor in frontend.

### R12-3 — Server release channel + optional auto-update

| Field | Value |
|-------|-------|
| Feature ID | F15, F16 |
| Sprint | v1.2 |
| Status | backlog |
| Effort | M |
| Acceptance criteria | AC-R12-3-1 through AC-R12-3-8 (SPRINT-v1.2-SCOPE.md §3) |

CI publishes ghcr.io image on vX.Y.Z tag (both vX.Y.Z and latest tags). Dockerfile
gains APP_VERSION build arg. StatusResponse.backend_version field added. Frontend
version mismatch banner (dismissible per session). docker-compose.yml gains image
variant and optional Watchtower service block (Compose profile "autoupdate", off by
default; backend-only label scoping). DEPLOY.md "Updating Synapse" section with manual
and automatic (Watchtower/TrueNAS/Diun) options plus data-service caveat.

### R12-4 — Ingest polling dedup (BUG-2 carry-over)

| Field | Value |
|-------|-------|
| Feature ID | F1, F16 |
| Sprint | v1.2 |
| Status | backlog (confirm done from v1.1 before starting) |
| Effort | S |
| Acceptance criteria | AC-R12-4-1, AC-R12-4-2 (SPRINT-v1.2-SCOPE.md §3) — or confirmed green from v1.1 |

Polling hook or useEffect in IngestView.tsx returns a cleanup that clears
interval/timeout on unmount. Vitest asserts at most 1 active interval after
mount/unmount/remount cycle.

### F18 — Feature registration

| Field | Value |
|-------|-------|
| Feature ID | F18 (NEW — registered sprint v1.2) |
| Sprint | v1.2 (first sprint) |
| Status | in-progress |

F18: Home dashboard + per-section domain insights — a landing screen surfacing vault
KPIs (pages_by_type, total_links, communities_count, AI spend), community topology, and
domain-vocabulary breakdowns. CLAUDE.md §4 must be updated by tech-writer before the
first F18 backend commit.

---

## Sprint 11 — v1.1 — M11 "Convert & Configure"

Goal: UI-driven Marker PDF conversion with auto-ingest; runtime settings surface for
8 migrated config keys (no .env editing for daily use); logo deduplication; 3 bounded bugfixes.

**Sprint status: IN PROGRESS — scope locked 2026-07-03**
Scope file: docs/sprints/SPRINT-v1.1-SCOPE.md
Branch: sprint/v1.1

### R11-1 — Marker conversion from UI

| Field | Value |
|-------|-------|
| Feature ID | F12, F16 |
| Sprint | v1.1 |
| Status | backlog |
| Effort | L |
| Acceptance criteria | AC-R11-1-1 through AC-R11-1-8 (SPRINT-v1.1-SCOPE.md §3) |

### R11-2 — Settings redesign (runtime config migration, 8 settings)

| Field | Value |
|-------|-------|
| Feature ID | F16, F17 (adjacent) |
| Sprint | v1.1 |
| Status | blocked — ADR-0053 required before backend code |
| Effort | L |
| Acceptance criteria | AC-R11-2-0 through AC-R11-2-10 (SPRINT-v1.1-SCOPE.md §3) |

Settings migrated to UI: PDF_EXTRACTOR (S1), MARKER_SERVICE_URL (S2),
MARKER_TIMEOUT_SECONDS (S3), COST_ALERT_THRESHOLD_USD (S4), EMBEDDINGS_ENABLED (S5),
EMBEDDING_FORMAT (S6), OVERVIEW_LANGUAGE (S7), WIKILINK_ENRICH_ENABLED (S8).

### R11-3 — Logo deduplication

| Field | Value |
|-------|-------|
| Feature ID | F1 |
| Sprint | v1.1 |
| Status | backlog |
| Effort | S |
| Acceptance criteria | AC-R11-3-1 through AC-R11-3-4 (SPRINT-v1.1-SCOPE.md §3) |

### R11-4 — Bugfixes (bounded sweep)

| Field | Value |
|-------|-------|
| Feature ID | F1, F3 (I3), F4/F5 (I4) |
| Sprint | v1.1 |
| Status | backlog |
| Effort | S × 3 |
| Acceptance criteria | AC-R11-4-BUG1, AC-R11-4-BUG2, AC-R11-4-BUG3 (SPRINT-v1.1-SCOPE.md §3) |

Bugs: renderMarkdown null guard (BUG-1), ingest polling dedup (BUG-2),
virtualizer zero-height recovery (BUG-3).

---

## Sprint 1 — v0.1 — M1 "Data flows end-to-end"

Goal: walking skeleton — file dropped into vault/raw/sources/ triggers incremental
index → Postgres metadata row + Qdrant vector created → REST API returns the page.
No AI inference, no graph layout, no UI.

**Sprint status: DONE — M1 CLOSED**
Closed by: docs/sprints/v0.1-m1-closure.md (live demo on MacBook Air M2 with real Postgres
16 + Qdrant + bge-m3 via Ollama — all EC items confirmed including EC-15 human checkpoint).
Velocity: ON SCOPE. All in-scope feature IDs delivered. No underrun. No overrun.
One permitted early extra: docs/DEPLOY.md (D6b draft, DRAFT-tagged, harmless).
PM sign-off: docs/sprints/v0.1-pm-signoff.md | 2026-06-28

---

### K1 — 3-layer vault structure

| Field | Value |
|-------|-------|
| Feature ID | K1 |
| Sprint | v0.1 |
| Status | done |
| Priority | P0 — prerequisite for all other work |

**Scope (v0.1 subset):**
Create the directory skeleton: vault/raw/sources/, vault/raw/assets/,
vault/wiki/ (with index.md, log.md), vault/schema.md, vault/purpose.md stub.
raw/ is immutable at runtime (watcher only reads it). wiki/ is writable by the
service. schema.md defines required frontmatter fields for v0.1: type, title, sources[].

**Acceptance criteria:**
- AC-K1-1: vault/raw/sources/ and vault/raw/assets/ directories exist and are gitignored for content.
- AC-K1-2: vault/wiki/index.md and vault/wiki/log.md exist with correct YAML frontmatter (type, title).
- AC-K1-3: vault/schema.md exists and documents required frontmatter fields: type, title, sources[].
- AC-K1-4: vault/purpose.md exists as a stub (fields present, values editable by human).
- AC-K1-5: vault/raw/ is read-only by convention; watcher NEVER writes to it (enforced by code path, tested).

---

### K4 — log.md append-only history

| Field | Value |
|-------|-------|
| Feature ID | K4 |
| Sprint | v0.1 |
| Status | done |
| Priority | P0 — required to satisfy I1 incremental index |

**Scope (v0.1 subset):**
log.md must be appended to on every successful ingest event (file path + timestamp +
action = INDEXED). This is the parse source for incremental refresh decisions. Full
parseable history format locked here; only the INDEXED event type is emitted in v0.1.

**Acceptance criteria:**
- AC-K4-1: vault/wiki/log.md is append-only; the watcher appends one line per indexed file in format: `YYYY-MM-DDTHH:MM:SSZ | INDEXED | <relative_path>`.
- AC-K4-2: log.md is NEVER truncated or rewritten by the service (validated by test that checks file length only grows).
- AC-K4-3: Duplicate ingests of an unchanged file (same mtime) do NOT append a new log entry (I1 incremental).

---

### K6 — YAML frontmatter schema

| Field | Value |
|-------|-------|
| Feature ID | K6 |
| Sprint | v0.1 |
| Status | done |
| Priority | P0 — pivot for Postgres metadata storage |

**Scope (v0.1 subset):**
The watcher must parse YAML frontmatter from .md files dropped into vault/raw/sources/.
Required fields in v0.1: type (string), title (string), sources (list of strings).
Parsing must be tolerant: missing fields logged as warnings, file still indexed with
nulls. No LLM involved in v0.1 — frontmatter is human-supplied in source files.

**Acceptance criteria:**
- AC-K6-1: YAML frontmatter parser correctly extracts type, title, sources[] from a valid .md file.
- AC-K6-2: A file with missing frontmatter fields is still indexed; missing fields stored as NULL in Postgres; warning logged.
- AC-K6-3: A file with completely absent frontmatter block (no --- delimiters) is handled without exception; all fields NULL.
- AC-K6-4: Parsed fields are persisted to the Postgres pages table (see models.py) with correct types.

---

### K7 — Obsidian compatibility baseline

| Field | Value |
|-------|-------|
| Feature ID | K7 |
| Sprint | v0.1 |
| Status | done-pending-live-demo |
| Priority | P1 — invariant I5 must be honoured from day 1 |

**Scope (v0.1 subset):**
vault/wiki/ must be a valid Obsidian vault from the first commit. This means:
vault/wiki/.obsidian/app.json generated by the service on startup (minimal valid config).
All wiki/*.md files written by the service must have valid YAML frontmatter.
No wikilink parsing required in v0.1 (K5 is deferred), but no broken syntax introduced.

**Acceptance criteria:**
- AC-K7-1: vault/wiki/.obsidian/app.json is created on service startup if absent; contains minimal valid Obsidian config (JSON with "legacyEditor": false at minimum).
- AC-K7-2: vault/wiki/index.md and vault/wiki/log.md have valid YAML frontmatter (passes python-frontmatter parse without error).
- AC-K7-3: Opening vault/wiki/ in Obsidian does not show errors (manual verification step; noted in exit criteria).

---

### F16 (partial) — dataVersion

| Field | Value |
|-------|-------|
| Feature ID | F16 (partial — dataVersion only) |
| Sprint | v0.1 |
| Status | done |
| Priority | P1 — required by I2 architecture even though graph is not live yet |

**Scope (v0.1 subset — dataVersion only):**
The Postgres vault_state table must store a dataVersion integer. The watcher bumps
dataVersion on every successful ingest. This is the trigger signal for future graph
recompute (I2). The full F16 feature (i18n, settings, GFM, multi-provider chat,
timeout) is deferred to v0.4/v0.5.

**Acceptance criteria:**
- AC-F16dv-1: Postgres vault_state table exists with columns: id, vault_id, data_version (integer), updated_at.
- AC-F16dv-2: data_version is incremented by 1 on every successful file ingest.
- AC-F16dv-3: GET /status endpoint returns current data_version value.
- AC-F16dv-4: data_version is never decremented or reset by normal operations.

---

### Watcher (backbone, supports I1)

| Field | Value |
|-------|-------|
| Feature ID | K1 + K4 + K6 cross-cutting implementation |
| Sprint | v0.1 |
| Status | done |
| Priority | P0 |

**Scope:**
backend/app/watcher.py using watchdog library. Watches vault/raw/sources/ for
CREATE and MODIFY events. On event: (1) check mtime/hash against Postgres — skip if
unchanged (I1); (2) parse frontmatter (K6); (3) upsert Postgres pages row; (4) upsert
Qdrant vector via bge-m3 (I9 reuse); (5) append log.md (K4); (6) bump dataVersion.
DELETE events: mark page as deleted in Postgres, remove Qdrant vector. NEVER full-rescan.

**Acceptance criteria:**
- AC-WATCH-1: Dropping a new .md file into vault/raw/sources/ results in a Postgres row within 5 seconds (integration test with timing assertion).
- AC-WATCH-2: Dropping the same file again with identical content does NOT create a duplicate Postgres row or Qdrant vector (I1).
- AC-WATCH-3: Modifying a file's content updates the existing Postgres row (upsert) and replaces the Qdrant vector (no orphan).
- AC-WATCH-4: Deleting a file marks the Postgres row deleted_at = now() and removes the Qdrant point.
- AC-WATCH-5: Watcher startup does NOT trigger a full directory rescan (I1); only watchdog events drive processing.
- AC-WATCH-6: bge-m3 is called via the already-running Ollama or bge-m3 HTTP endpoint — no new embedding service introduced (I9).

---

### Postgres + SQLAlchemy models (backbone, supports D2)

| Field | Value |
|-------|-------|
| Feature ID | K6 + F16(partial) cross-cutting implementation |
| Sprint | v0.1 |
| Status | done-pending-live-demo |
| Priority | P0 |

**Scope:**
backend/app/models.py — SQLAlchemy 2 models (source of truth for ER diagram D2).
Tables in v0.1: pages, vault_state. Alembic migrations for both. pages columns:
id (uuid pk), vault_id, file_path, title, type, sources (JSONB), content_hash,
qdrant_point_id (uuid), deleted_at (nullable), created_at, updated_at.

**Acceptance criteria:**
- AC-PG-1: `docker compose up` brings up Postgres 16; Alembic migrations run cleanly on fresh DB.
- AC-PG-2: models.py is the single source of truth for the schema; `make er` generates docs/er/schema.mmd from it (D2 gate).
- AC-PG-3: All columns listed in the scope above are present and typed correctly (verified by mypy + pytest schema test).
- AC-PG-4: No raw SQL strings in application code; all queries via SQLAlchemy 2 ORM or core expressions.

---

### REST API — read endpoints (backbone, supports D4)

| Field | Value |
|-------|-------|
| Feature ID | K2 (Ingest operation entry point) |
| Sprint | v0.1 |
| Status | done |
| Priority | P0 |

**Scope:**
backend/app/main.py FastAPI service. v0.1 endpoints only:
  GET  /status           — health + data_version
  GET  /pages            — list pages (id, title, type, file_path, updated_at); pagination (limit/offset)
  GET  /pages/{id}       — single page detail (all metadata fields)
  POST /ingest/trigger   — manually trigger ingest for a specific file path (for testing; watcher is primary)

No chat endpoints, no graph endpoints, no search endpoints in v0.1.
OpenAPI JSON auto-generated by FastAPI at /docs and /openapi.json (feeds D4).

**Acceptance criteria:**
- AC-REST-1: GET /status returns HTTP 200 with JSON including data_version and service uptime.
- AC-REST-2: GET /pages returns paginated list; after dropping a file via watcher, the new page appears in the response.
- AC-REST-3: GET /pages/{id} returns full metadata for a known page; returns HTTP 404 for unknown id.
- AC-REST-4: POST /ingest/trigger accepts {"file_path": "..."} and returns HTTP 202; triggers ingest pipeline synchronously in v0.1 (async in later sprints).
- AC-REST-5: /openapi.json is valid OpenAPI 3.1; saved to docs/api/openapi.json as D4 artifact (CI step).
- AC-REST-6: All endpoints return correct HTTP status codes and JSON error bodies (not 500 on bad input).

---

### Qdrant integration (backbone, supports I9)

| Field | Value |
|-------|-------|
| Feature ID | K2 + I9 cross-cutting implementation |
| Sprint | v0.1 |
| Status | done-pending-live-demo |
| Priority | P0 |

**Scope:**
backend/app/ Qdrant client wrapper. Collection: synapse_pages. Point schema: id = page uuid,
vector = bge-m3 768-dim embedding of page content, payload = {file_path, title, type}.
Upsert on ingest, delete on file removal. No search in v0.1 (F5 is deferred).
Reuses the already-running Qdrant + bge-m3 instances (I9).

**Acceptance criteria:**
- AC-QD-1: synapse_pages collection is created on service startup if absent; correct vector dimension (bge-m3 = 1024 dims — verify against running instance).
- AC-QD-2: After a file ingest, a Qdrant point exists for the page with correct payload fields.
- AC-QD-3: After a file deletion, the Qdrant point is removed (verified by direct Qdrant API call in test).
- AC-QD-4: bge-m3 embedding is fetched from the already-running service (URL from env var EMBEDDING_URL); no local model loaded (I9).

---

### Docker Compose + dev tooling (backbone)

| Field | Value |
|-------|-------|
| Feature ID | F15 (partial — Docker Compose only) |
| Sprint | v0.1 |
| Status | done-pending-live-demo |
| Priority | P0 |

**Scope:**
docker-compose.yml with services: synapse-backend (FastAPI + watcher), postgres16.
Qdrant and bge-m3 are external (already running on TrueNAS) — referenced via env vars.
Makefile targets: `make up`, `make test`, `make er` (generates D2), `make openapi` (saves D4).
ruff + black + mypy configured. pytest with at least 2 integration tests (watcher + REST).

**Acceptance criteria:**
- AC-DC-1: `docker compose up` on a clean machine (with Postgres service only; Qdrant/bge-m3 pointed at TrueNAS) starts synapse-backend cleanly.
- AC-DC-2: `make test` runs pytest suite and exits 0 with all tests green.
- AC-DC-3: `make er` generates docs/er/schema.mmd from SQLAlchemy models without manual edits.
- AC-DC-4: `make openapi` saves /openapi.json output to docs/api/openapi.json.
- AC-DC-5: No secrets in docker-compose.yml or any committed file; all sensitive values via .env (gitignored).

---

### D1 — C4 Architecture Diagram

| Field | Value |
|-------|-------|
| Feature ID | D1 (docs artifact) |
| Sprint | v0.1 |
| Status | done |
| Priority | P1 — required for M1 docs gate (I8) |

**Scope:**
Mermaid C4 diagrams in docs/architecture/: context.mmd (system context — Synapse +
external users/services), container.mmd (FastAPI, Postgres, Qdrant, bge-m3, vault FS).
Component-level diagram deferred to v0.2 when InferenceProvider layer exists.

**Acceptance criteria:**
- AC-D1-1: docs/architecture/context.mmd is valid Mermaid C4Context diagram; renders without error.
- AC-D1-2: docs/architecture/container.mmd is valid Mermaid C4Container diagram showing all v0.1 containers.
- AC-D1-3: Both diagrams are reviewed and approved by solution-architect before M1 sign-off.

---

### D2 — ER Diagram

| Field | Value |
|-------|-------|
| Feature ID | D2 (docs artifact) |
| Sprint | v0.1 |
| Status | done |
| Priority | P1 — required for M1 docs gate (I8) |

**Scope:**
docs/er/schema.mmd generated by `make er` from SQLAlchemy models. Must reflect
exact v0.1 schema (pages + vault_state tables). Updated whenever models.py changes.

**Acceptance criteria:**
- AC-D2-1: docs/er/schema.mmd is generated by `make er` (not hand-written).
- AC-D2-2: ER diagram matches live Postgres schema exactly (CI check: run migrations + compare).
- AC-D2-3: Reviewed and approved by solution-architect before M1 sign-off.

---

### D4 — OpenAPI Reference

| Field | Value |
|-------|-------|
| Feature ID | D4 (docs artifact) |
| Sprint | v0.1 |
| Status | done |
| Priority | P1 — required for M1 docs gate (I8) |

**Scope:**
docs/api/openapi.json auto-generated from FastAPI. Saved by `make openapi`.
MCP tool schemas deferred to v0.2 (FastMCP server is not in scope for v0.1).

**Acceptance criteria:**
- AC-D4-1: docs/api/openapi.json is present and valid OpenAPI 3.1.
- AC-D4-2: File is generated by `make openapi`, not hand-written.
- AC-D4-3: All 4 v0.1 endpoints (GET /status, GET /pages, GET /pages/{id}, POST /ingest/trigger) are documented with correct request/response schemas.

---

## Sprint 2 — v0.2 — M2 "Agentic loop closed, 3 providers"

**Sprint status: DONE-PENDING-HUMAN-CHECKPOINT**
Scope locked: 2026-06-28 by product-manager. Scope log: docs/sprints/v0.2-scope.md
PM sign-off: docs/sprints/v0.2-pm-signoff.md | 2026-06-28
Prerequisite: M1 CLOSED — docs/sprints/v0.1-m1-closure.md confirmed.
Branch: sprint/v0.2
Invariants in force with heightened priority: I6 (pluggable inference — defining invariant
this sprint), I7 (bounded loops — first real loops introduced). All 9 invariants apply.
Velocity: ON SCOPE. All 11 in-scope feature IDs delivered. 1 harmless early extra
(DELETE /provider/config/{id}). 2 additional ADRs (0010/0011) beyond required 3 —
legitimate sprint decisions, not overrun. BUG-v0.2-1 found and fixed in-sprint. mmdc CI
check deferred (was "best-effort" per scope lock §11). v0.3 boundary (F4 graph) intact.
Human checkpoint: 3 live-run conditions remain (listed in docs/sprints/v0.2-pm-signoff.md
§6). Sprint 3 blocked until EC-M2-17 satisfied by Emanuele.
NB follow-ups: NB-1, NB-2, NB-4 carried to v0.3 hardening; NB-5 is a sprint-3 pre-start
blocker (OLLAMA_URL missing from docker-compose.yml). NB-3 was a pre-merge v0.2 action.

---

### F17 — InferenceProvider ABC + 3 backends + capability routing

| Field | Value |
|-------|-------|
| Feature ID | F17 |
| Sprint | v0.2 |
| Status | done — AC-F17-1..8 all GREEN; I6 APPROVED by architect; chat() stub confirmed; no hardcoded provider/model ID |
| Priority | P0 — defining feature of sprint; all other items depend on it |
| Owner | ai-agent-engineer (leads); backend-engineer (support) |

**Scope:**
`backend/app/ingest/provider/base.py` — `InferenceProvider` ABC with methods:
`analyze(source_text, vault_context) → Analysis`, `generate(analysis, retrieval_context)
→ list[WikiPage]`, `chat(messages, retrieval_context) → stream` (STUBBED, see §5 of scope
lock), `capabilities() → {mode, supports_tools, supports_agentic_loop, max_context, name}`.

`ollama.py` — OllamaProvider: Ollama /api/chat with format=json; tool-calling only if
model supports it; `supports_agentic_loop=False`.

`api.py` — ApiProvider: Anthropic Messages API (tool-use + JSON Schema) + OpenAI-compatible
endpoint (configurable base_url from provider_config); `supports_agentic_loop=False`.

`cli.py` — CliAgentProvider: claude-agent-sdk; filesystem tools scoped to vault;
in-process MCP tools (search, write_page); permission_mode='acceptEdits';
`supports_agentic_loop=True`.

`provider_config` Postgres table: id, scope (global/vault/operation), vault_id (nullable),
provider_type (local/api/cli), model_id, base_url (nullable), max_iter, token_budget,
created_at, updated_at. Resolution order: operation > vault > global.

REST endpoints: `GET /provider/config`, `POST /provider/config`.

**Acceptance criteria:**
- AC-F17-1: InferenceProvider ABC defines all 4 methods with correct signatures; mypy strict passes.
- AC-F17-2: All 3 concrete providers implement the ABC; chat() raises NotImplementedError; unit test confirms each provider's chat() raises NotImplementedError.
- AC-F17-3: `capabilities()` returns correct dict for each provider; `OllamaProvider.supports_agentic_loop == False`; `CliAgentProvider.supports_agentic_loop == True`.
- AC-F17-4: Static analysis test asserts zero direct Ollama/Anthropic/SDK imports outside `backend/app/ingest/provider/`; no model IDs, API keys, or endpoint URLs hardcoded anywhere outside provider modules.
- AC-F17-5: `provider_config` table exists after Alembic migration; all columns present with correct types.
- AC-F17-6: Config resolution order (operation > vault > global) verified by unit test with 3 conflicting rows.
- AC-F17-7: `GET /provider/config` returns HTTP 200 with current config; `POST /provider/config` accepts and persists new config; both endpoints in OpenAPI.
- AC-F17-8: No model ID is hardcoded in any file — always read from `provider_config`. Current model IDs from CLAUDE.md §12 are seeded as defaults.

---

### K2 (ingest op) — Orchestrated ingest loop with capability routing

| Field | Value |
|-------|-------|
| Feature ID | K2 (ingest operation — full implementation) |
| Sprint | v0.2 |
| Status | done-pending-live-smoke — AC-K2-1..3 MOCK-GREEN (live run required for EC-M2-5/17); AC-K2-4..8 GREEN |
| Priority | P0 — spine of M2; wires F17 + F3 |
| Owner | backend-engineer; ai-agent-engineer for routing branch |

**Scope:**
`backend/app/ingest/orchestrator.py` — expand the v0.1 seam (ADR-0003) into the full
routing loop:
1. Resolve provider from `provider_config` (operation > vault > global scope).
2. Call `capabilities()`.
3. If `supports_agentic_loop == True` (CliAgentProvider): delegate full ingest to the CLI
   provider. Provider links pages using MCP tools in its own agent loop.
4. Otherwise (Local / API): run orchestrated ingest loop:
   a. `analyze(source_text, vault_context)` — two-step CoT, step 1.
   b. `generate(analysis, retrieval_context)` — step 2; returns list[WikiPage].
   c. Validate: check each WikiPage has required frontmatter (type, title, sources[]).
   d. If invalid: augment prompt with validation errors; retry. Max iterations: `max_iter`
      (default 3). If still invalid after max_iter: log converged=False, surface error.
   e. Write valid pages to vault/wiki/ (reuse persist_metadata + upsert_vector primitives).
   f. Parse wikilinks (K5), update links table.
   g. Update index.md (K3).
   h. Log: provider_name, iterations_used, total_tokens, total_cost_usd, converged.
5. Provider fallback: if primary provider fails/times out → try fallback once → surface error.
   Bounded to 1 retry (I7).

**Acceptance criteria:**
- AC-K2-1: Ingest of a fixture .md file with OllamaProvider results in at least 1 page written to vault/wiki/ with valid YAML frontmatter (type, title, sources[] all present and non-empty).
- AC-K2-2: Ingest with ApiProvider (Anthropic) produces same result; sources[] populated from original file's frontmatter + LLM-identified sources.
- AC-K2-3: Ingest with CliAgentProvider executes delegated path; orchestrated loop body does NOT execute; pages written to vault/wiki/ via MCP write_page tool.
- AC-K2-4: The routing branch reads `capabilities().supports_agentic_loop` — does NOT inspect provider class name or type string (confirmed by code review + test that swaps a custom provider with supports_agentic_loop=True into the config).
- AC-K2-5 (I7): Loop stops at max_iter=3 on a mock provider that always returns invalid pages; log entry has converged=False; no extra provider calls beyond cap.
- AC-K2-6 (I7): Every ingest run produces a log entry with keys: provider_name, max_iter_used, total_tokens, total_cost_usd, converged.
- AC-K2-7: Provider fallback (primary timeout → fallback once) tested; second failure surfaces as HTTP 500 with error body; no infinite retry.
- AC-K2-8: vault/wiki/overview.md is created (or updated) after first ingest into a new vault.

---

### F3 — Two-step CoT ingest, source traceability, auto overview.md, language-aware

| Field | Value |
|-------|-------|
| Feature ID | F3 |
| Sprint | v0.2 |
| Status | done-pending-live-smoke — AC-F3-1..3 + F3-5..7 GREEN; AC-F3-4 (live language detection) LIVE-DEFERRED to EC-M2-5 TrueNAS run |
| Priority | P0 — what the loop produces |
| Owner | ai-agent-engineer (prompt engineering); backend-engineer (integration) |

**Scope:**
The two-step CoT ingest:
- Step 1: `analyze(source_text, vault_context)` — identify: topics, entities, concepts,
  relationships, language of source, suggested wiki page types, cross-references to existing
  pages. Returns structured `Analysis` object.
- Step 2: `generate(analysis, retrieval_context)` — produce `list[WikiPage]` where each
  WikiPage has: title, type (entity/concept/source/synthesis/comparison), content (Markdown
  with [[wikilinks]]), YAML frontmatter with sources[] tracing back to the source file.
- Source traceability: `sources[]` in each generated page frontmatter MUST include the
  originating source file path. LLM may add additional cited sources from the retrieval
  context.
- Auto overview.md: after first ingest, generate vault/wiki/overview.md summarizing the
  vault's content in 1–3 paragraphs. Updated (not replaced) on subsequent ingests.
- Language-aware: detect source language in `analyze()`; generate wiki pages in same language.
  Language stored in page frontmatter as `lang: <ISO-639-1>`.

**Acceptance criteria:**
- AC-F3-1: analyze() returns a structured Analysis object (Pydantic model) with: topics (list[str]), entities (list[str]), language (ISO-639-1 code), suggested_pages (list[dict] with title + type).
- AC-F3-2: generate() returns list[WikiPage] where each page has title, type, content, and frontmatter with type + title + sources[] + lang fields.
- AC-F3-3: sources[] in each generated page's frontmatter contains the originating source file path (relative to vault/raw/).
- AC-F3-4: Language detection works for at least EN and IT (the two vault languages per CLAUDE.md §1); generated pages are in the detected language.
- AC-F3-5: vault/wiki/overview.md is created after first ingest; it is a valid Obsidian-compatible Markdown file with YAML frontmatter.
- AC-F3-6: Overview.md is updated (appended/regenerated) on each subsequent ingest; it does NOT grow unboundedly (bounded to a max token length in the generation prompt).
- AC-F3-7: All generated pages pass the validation step in the orchestrated loop without requiring retry on a well-formed source document (happy path converges in 1 iteration).

---

### K5 — [[wikilink]] parser + links table

| Field | Value |
|-------|-------|
| Feature ID | K5 |
| Sprint | v0.2 |
| Status | done — AC-K5-1..7 all GREEN; links table 5-column schema verified; dangling warn-not-error confirmed |
| Priority | P1 — required before graph (F4, v0.3) can compute edges |
| Owner | backend-engineer |

**Scope:**
`backend/app/ingest/parser/wikilinks.py` — dedicated parser for [[Target]] and
[[Target|alias]] syntax. Extracts (target_title, alias_or_None) tuples from Markdown content.

Postgres `links` table (Alembic migration): id (uuid), source_page_id (uuid FK → pages.id),
target_title (text), alias (text nullable), dangling (bool — True if target page does not
exist in pages table at parse time), created_at.

After each page write, the wikilink parser runs on the page content and all extracted links
are upserted into the links table. Dangling links (no matching pages row) are stored with
dangling=True and logged as warnings. They are NOT errors.

**Acceptance criteria:**
- AC-K5-1: Parser correctly extracts [[Target Page]] → (target_title="Target Page", alias=None).
- AC-K5-2: Parser correctly extracts [[Target Page|display text]] → (target_title="Target Page", alias="display text").
- AC-K5-3: Parser handles multiple wikilinks per page, zero wikilinks, and nested brackets gracefully (no exception on any input).
- AC-K5-4: After ingest, all wikilinks in generated pages have corresponding rows in the links table.
- AC-K5-5: Dangling wikilinks (target does not exist in pages table) are stored with dangling=True; a WARNING is logged; no exception raised.
- AC-K5-6: `links` table schema matches ER diagram (D2); Alembic migration runs cleanly.
- AC-K5-7: Unit tests: valid link, aliased link, multiple links, zero links, invalid bracket syntax (no crash).

---

### K3 — index.md catalogue auto-maintained

| Field | Value |
|-------|-------|
| Feature ID | K3 |
| Sprint | v0.2 |
| Status | done — AC-K3-1..6 all GREEN; idempotent; I1-safe (DB query, not filesystem rescan); I5-compliant frontmatter |
| Priority | P1 — required output of ingest loop; LLM navigation entry-point |
| Owner | backend-engineer |

**Scope:**
After every successful wiki page write, `vault/wiki/index.md` is updated: a new entry is
added (or existing entry updated) with the page's title as a [[wikilink]] and its type.
index.md structure: YAML frontmatter (type: index, title: "Wiki Index") + a Markdown
sections table organized by page type. If index.md is missing, it is recreated.

**Acceptance criteria:**
- AC-K3-1: After ingest of a fixture document, vault/wiki/index.md contains a [[wikilink]] entry for each generated wiki page.
- AC-K3-2: index.md retains valid YAML frontmatter (type: index, title: "Wiki Index") at all times (I5).
- AC-K3-3: Entries in index.md are organized by page type (entity, concept, source, synthesis, comparison).
- AC-K3-4: If index.md is absent when the loop runs, it is recreated without error.
- AC-K3-5: index.md is never truncated — only appended to or regenerated in full if structure drift is detected (regeneration preferred over concatenation).
- AC-K3-6: Unit test: ingest fixture → assert index.md contains wikilink [[<title>]] for the new page.

---

### MCP server — FastMCP standalone server

| Field | Value |
|-------|-------|
| Feature ID | MCP server (backbone for CliAgentProvider; required M2 deliverable per CLAUDE.md §8) |
| Sprint | v0.2 |
| Status | done-pending-live-smoke — AC-MCP-1..7 GREEN; AC-MCP-8 (live CLI↔MCP wiring) LIVE-DEFERRED to EC-M2-5 TrueNAS run |
| Priority | P0 — CliAgentProvider cannot delegate without it |
| Owner | ai-agent-engineer (FastMCP integration); backend-engineer (tool implementations) |

**Scope:**
`backend/app/mcp/server.py` — FastMCP standalone server. Tools:
- `search_wiki(query: str) → list[PageRef]` — vector search (Qdrant bge-m3) + keyword filter over vault/wiki/ pages.
- `write_page(title: str, content: str, frontmatter: dict) → PageRef` — writes a page to vault/wiki/, validates frontmatter (K6 schema), upserts Postgres + Qdrant, updates index.md (K3), parses wikilinks (K5).
- `get_page(title: str) → Page` — returns full page content + frontmatter from vault/wiki/.
- `list_pages(type: str | None = None) → list[PageRef]` — lists all pages (optionally filtered by type) from Postgres.

Server starts in stdio mode (for CliAgentProvider subprocess) and HTTP mode (for external clients). `make mcp` target starts it.

Tool schemas exported to `docs/api/mcp-tools.json`.

**Acceptance criteria:**
- AC-MCP-1: FastMCP server starts without error via `make mcp`; exposes all 4 tools in its tool registry.
- AC-MCP-2: `search_wiki("test query")` returns a list of PageRef objects with id, title, type, relevance_score.
- AC-MCP-3: `write_page(...)` writes a valid Markdown file to vault/wiki/, creates a Postgres row, creates a Qdrant vector, updates index.md. Fails with descriptive error if required frontmatter fields are missing.
- AC-MCP-4: `get_page("Title")` returns the page content and frontmatter; returns a not-found error for unknown titles.
- AC-MCP-5: `list_pages()` returns all non-deleted pages; `list_pages(type="entity")` filters correctly.
- AC-MCP-6: Integration test calls each tool via FastMCP test client; all assertions on DB + filesystem state pass.
- AC-MCP-7: `docs/api/mcp-tools.json` is present and documents all 4 tools with input/output schemas.
- AC-MCP-8: CliAgentProvider subprocess communicates with MCP server via stdio successfully during the smoke matrix (EC-M2-5).

---

### D3 — Sequence diagrams (ingest routing + ingest loop)

| Field | Value |
|-------|-------|
| Feature ID | D3 (docs artifact — first sprint it is required) |
| Sprint | v0.2 |
| Status | done — AC-D3-1..2 GREEN; AC-D3-4 MANUAL (architect + tech-writer approved); AC-D3-3 (mmdc CI) deferred to v0.3 devops as "best-effort" per scope lock §11 |
| Priority | P1 — required by I8 docs-as-DoD for M2 |
| Owner | tech-writer |

**Scope:**
`docs/sequences/ingest-routing.mmd` — Mermaid sequenceDiagram showing the full routing flow
from watcher event through orchestrator to both branches (orchestrated loop path and CLI
delegation path) through to pages written and index.md updated.

`docs/sequences/ingest-loop.mmd` — Mermaid sequenceDiagram showing the detailed orchestrated
loop: analyze → generate → validate → [converged? yes: write pages; no: augment context +
retry, with max_iter annotated] → log total_cost_usd.

Both diagrams: CI mmdc render check must pass.

**Acceptance criteria:**
- AC-D3-1: `docs/sequences/ingest-routing.mmd` is a valid Mermaid sequenceDiagram; shows both routing branches; reviewed and approved by tech-writer.
- AC-D3-2: `docs/sequences/ingest-loop.mmd` is a valid Mermaid sequenceDiagram; shows all loop steps including the max_iter bound annotation and cost log; reviewed and approved by tech-writer.
- AC-D3-3: Both diagrams pass the CI mmdc render check (no parse errors).
- AC-D3-4: Diagrams are consistent with the actual implementation (reviewed by architect and tech-writer jointly).

---

### D7 — Architecture Decision Records (first formally required sprint)

| Field | Value |
|-------|-------|
| Feature ID | D7 (docs artifact — required from v0.2 per CLAUDE.md §9) |
| Sprint | v0.2 |
| Status | done — ADR-0007..0011 all present and Accepted; AC-D7-1..4 GREEN; AC-D7-5 MANUAL (architect + tech-writer approved); ADR README indexes all 11 |
| Priority | P1 — required by I8 docs-as-DoD for M2 |
| Owner | solution-architect (authors); tech-writer (consistency review) |

**Scope:**
Minimum required ADRs:
- `docs/adr/ADR-0007-inference-provider-abc.md`: InferenceProvider ABC design — why ABC (not duck typing), why 3 backends, capability-aware routing approach, why chat() is stubbed not deferred.
- `docs/adr/ADR-0008-provider-config-schema.md`: provider_config table scope model — why Postgres not a config file, why global/vault/operation hierarchy, resolution order.
- `docs/adr/ADR-0009-bounded-loop-defaults.md`: max_iter=3 and token_budget defaults — rationale, how total_cost_usd is computed per provider type, anomaly threshold ($1.00/run), CLI cost = $0.00 convention.

Additional ADRs as needed for: FastMCP choice over raw MCP SDK, links table schema (K5), wikilink parser approach.

**Acceptance criteria:**
- AC-D7-1: ADR-0007 present, covers InferenceProvider design decisions, reviewed and approved by solution-architect.
- AC-D7-2: ADR-0008 present, covers provider_config schema, reviewed and approved by solution-architect.
- AC-D7-3: ADR-0009 present, covers bounded-loop defaults and cost logging, reviewed and approved by solution-architect.
- AC-D7-4: All ADRs follow the established format (Status, Date, Sprint, Decider, Invariants, Context, Decision, Consequences).
- AC-D7-5: ADRs are internally consistent with the D3 sequence diagrams and the actual code implementation.

---

### D1/D2/D4 updates (continuous per I8)

| Field | Value |
|-------|-------|
| Feature ID | D1, D2, D4 (docs artifacts — continuous) |
| Sprint | v0.2 |
| Status | done — AC-D1u-1..2, AC-D2u-1, AC-D4u-1..2 all GREEN; zero drift on ER and OpenAPI; v0.1 carry-forward (202 schema) resolved; component.mmd new; mcp-tools.json new; NB-3 (openapi info.version string) is pre-merge fix |
| Priority | P1 — required before M2 docs gate |
| Owner | tech-writer (D1 narrative); backend-engineer (D2 via `make er`); backend-engineer (D4 via `make openapi`) |

**Scope:**
D1: Add `docs/architecture/component.mmd` — Mermaid C4Component diagram showing the
InferenceProvider layer (3 backends), orchestrator routing, MCP server, provider_config,
links table. Update context.mmd and container.mmd to reflect new architectural elements.

D2: Regenerate `docs/er/schema.mmd` via `make er` after adding provider_config and links
tables to models.py. Must match live Postgres schema.

D4: Regenerate `docs/api/openapi.json` via `make openapi` after adding provider config
endpoints. Fix the 202 response schema carry-forward from v0.1. Add
`docs/api/mcp-tools.json` via FastMCP schema export.

**Acceptance criteria:**
- AC-D1u-1: docs/architecture/component.mmd is a valid Mermaid C4Component diagram; reviewed by architect and tech-writer.
- AC-D1u-2: context.mmd and container.mmd updated to include InferenceProvider and MCP server; no stale elements.
- AC-D2u-1: docs/er/schema.mmd regenerated by `make er`; includes provider_config and links tables with all columns; matches live Postgres schema.
- AC-D4u-1: docs/api/openapi.json updated; POST /ingest/trigger has correct 202 schema; GET/POST /provider/config documented.
- AC-D4u-2: docs/api/mcp-tools.json present; all 4 MCP tools documented with input/output schemas.

---

## Sprint 3 — v0.3 — M3 "Graph live, no main-thread freeze"

**Sprint status: DONE-PENDING-HUMAN-CHECKPOINT**
Scope locked: 2026-06-28 by product-manager. Scope log: docs/sprints/v0.3-scope.md
PM sign-off: docs/sprints/v0.3-pm-signoff.md | 2026-06-28
Branch: sprint/v0.3
Invariants with heightened priority: I2 (headline — APPROVED by architect, no client layout,
two-layer enforcement), I4 (WebGL sigma.js canvas — APPROVED, ~6 DOM elements).
All 9 invariants apply.
Velocity: ON SCOPE. All in-scope feature IDs delivered. Zero out-of-scope items built.
Zero regressions. 25 new backend tests (366 total), 71 frontend vitest. ADR-0012..0015 added.
Performance gates: G1 PROVEN-NOW (T-INC-GRAPH-001..004). G2 static PROVEN-NOW (T-NCL-001..022);
G2 runtime DEFERRED-TO-LIVE. G3 N/A (v0.4). G4 DEFERRED-TO-LIVE (harness + seeder written).
Human checkpoint: 5 conditions in docs/sprints/v0.3-pm-signoff.md §6. Sprint 4 blocked until
EC-M3-17 satisfied by Emanuele (Playwright run + sigma viewer confirmed in browser).
NB follow-ups: NB-1/2/4/5 all DONE (see below). NB-6 (mmdc CI) carried to v0.4. NB-7/8
are cosmetic diagram notes, optional tech-writer polish for v0.4.

---

### F4 — Knowledge graph: 4-signal weighting + FA2 server-side layout + sigma.js viewer

| Field | Value |
|-------|-------|
| Feature ID | F4 |
| Sprint | v0.3 |
| Status | done-pending-live-perf — AC-F4-1..5/8/9 GREEN; AC-F4-6 (G2 Playwright longtask) and AC-F4-7 (G4 fps/DOM Playwright) DEFERRED-TO-LIVE; EC-M3-1/2/3/4/8 MET; EC-M3-5/6/7 MET-PENDING-LIVE |
| Priority | P0 — defining feature of sprint; I2 is the headline invariant |
| Owner | backend-engineer (engine + cache + API); frontend-engineer (sigma viewer) |

**Scope:**
`backend/app/graph/engine.py` — GraphEngine: reads pages + links tables from Postgres;
builds igraph Graph object; computes 4-signal edge weights:
  - direct-link: ×3 (a [[wikilink]] from page A to page B contributes weight 3)
  - source-overlap: ×4 (pages sharing a source[] entry contribute weight 4)
  - Adamic-Adar: ×1.5 (applied to the shared-neighbour similarity score)
  - type-affinity: ×1 (same page type contributes weight 1)
Runs FA2 layout via igraph (python-igraph, R9). Writes resulting x, y coordinates to
Postgres pages table (columns: x float, y float). Writes edges with computed weights to
a new `edges` Postgres table.

`backend/app/graph/cache.py` — GraphCache: monitors vault_state.data_version; on
version bump, enqueues a debounced FA2 recompute (debounce window: configurable, default
5 seconds). At most 1 queued recompute job at a time. Subsequent bumps during an in-flight
recompute collapse into 1 follow-up run. Logs recompute duration + node/edge count.

`GET /graph` REST endpoint — returns JSON:
  `{nodes: [{id, title, type, x, y}], edges: [{source, target, weight}], data_version: int, cached: bool}`
  Sets `X-Graph-Cache: hit` header when serving from cache (no re-layout triggered).

Frontend sigma viewer — React 19 + Vite + TypeScript single-page app. Fetches `GET /graph`
on load. Calls `sigma.js graph.addNode(id, {x, y, label, type})` and
`graph.addEdge(src, tgt, {weight})` with precomputed coords. NEVER calls any layout
function. Node click: fetches `GET /pages/{id}` and displays title + type in a tooltip
or side drawer (read-only). No chat, no editor, no 3-panel shell.

**Acceptance criteria:**
- AC-F4-1: All 4 edge-weight signals are computed and applied in engine.py. Unit test: fixture graph of 5 nodes with known links, source overlap, and types asserts expected weights on each edge type.
- AC-F4-2: FA2 layout runs in engine.py via igraph (python-igraph). Output: each page has x, y float values written to Postgres. No layout code in any frontend file.
- AC-F4-3: `GET /graph` returns nodes, edges, data_version, and cached fields. Typing verified by OpenAPI response schema (D4 updated). HTTP 200 on a seeded graph.
- AC-F4-4: Second `GET /graph` call with same data_version returns `cached: true` and `X-Graph-Cache: hit`; no second FA2 invocation (verified via backend log assertion in test).
- AC-F4-5: Sigma.js viewer renders the graph in a WebGL canvas. DOM node count in graph container <20 regardless of graph size (G4 assertion). No graphology/sigma layout function called in JS bundle (G2 static check).
- AC-F4-6: Playwright G2 test passes: no JS long task >50ms on main thread during graph render.
- AC-F4-7: Playwright G4 test passes: 200-node / 500-edge fixture graph renders at ≥60fps.
- AC-F4-8: Node click in viewer shows page title (read-only). No edit UI, no chat, no provider selector.
- AC-F4-9: G1 preserved: ingest one new file → only that file's node coords added/updated; no full pages table rewrite (test_incremental_graph_update).

---

### F16 (partial — debounce wiring) — dataVersion-triggered debounced FA2 recompute

| Field | Value |
|-------|-------|
| Feature ID | F16 (partial) |
| Sprint | v0.3 |
| Status | done — AC-F16db-1..4 all GREEN; T-GCACHE-001..013 (13 tests); debounce collapse, in-flight guard, marker stamp all verified; EC-M3-3 MET |
| Priority | P0 — required to satisfy I2 cached-and-debounced invariant |
| Owner | backend-engineer |

**Scope:**
The vault_state.data_version column (from v0.1) is already bumped on every successful
ingest. In v0.3, GraphCache subscribes to (or polls) this value. When data_version
changes, GraphCache schedules a debounced FA2 recompute. The debounce window collapses
rapid bursts of ingests into a single recompute. This is the v0.3 activation of the
F16 dataVersion mechanism introduced in v0.1. No other F16 sub-features (i18n, settings,
GFM, multi-provider timeout) are in scope this sprint — all v0.4.

**Acceptance criteria:**
- AC-F16db-1: GraphCache detects data_version change (via polling or event) and triggers FA2 recompute after debounce window.
- AC-F16db-2: Multiple rapid data_version bumps within the debounce window result in exactly 1 FA2 recompute run (not N runs). Verified by unit test with mocked clock.
- AC-F16db-3: An in-progress FA2 run is NOT interrupted by a new data_version bump; the new bump is queued and runs after current completes.
- AC-F16db-4: `GET /status` returns current data_version (existing AC from v0.1); no regression.

---

### Frontend thin viewer — sigma.js standalone page

| Field | Value |
|-------|-------|
| Feature ID | F4 (frontend component) |
| Sprint | v0.3 |
| Status | done-pending-live-perf — AC-FE-1 (viewer load) DEFERRED-TO-LIVE; AC-FE-2/3/4/5 GREEN (T-NCL-001..022, T-GSTORE-001..016, T-GTRANS-001..019); no client layout confirmed; I3 pre-compliance confirmed |
| Priority | P1 — required to produce D5 (Playwright screenshots) and satisfy M3 milestone |
| Owner | frontend-engineer |

**Scope:**
`frontend/` — React 19 + Vite + TypeScript project. Minimal scaffolding only:
- Single route: `/graph` (or `/` for v0.3, no routing library required).
- Zustand store with selectors + shallow equality for graph data state (I3 pre-compliance).
- sigma.js (WebGL) installed; graphology as the underlying graph model.
- NO: CodeMirror, 3-panel layout, chat components, provider selector, TanStack Virtual
  (unless a node list >50 items is added, in which case it is mandatory).
- `make dev` starts the Vite dev server. `make build` produces a static bundle served by
  FastAPI's static file mount.
- ESLint + prettier + TypeScript strict configured. vitest for unit tests.

**Acceptance criteria:**
- AC-FE-1: `make dev` starts the viewer; `GET /` or `GET /graph` loads the sigma canvas without console errors.
- AC-FE-2: No client-side layout function imported or called. Bundle analysis (vitest or rollup-plugin-visualizer) confirms no graphology-layout-forceatlas2 or similar package in bundle.
- AC-FE-3: Zustand store uses selectors + shallow equality for graph state (I3 pre-compliance). No whole-store subscription pattern.
- AC-FE-4: TypeScript strict passes. ESLint + prettier clean.
- AC-FE-5: vitest unit test coverage for the graph data transformation (API response → graphology graph object).

---

### D3 — Sequence diagram: graph recompute

| Field | Value |
|-------|-------|
| Feature ID | D3 (docs artifact — update) |
| Sprint | v0.3 |
| Status | done — AC-D3v3-1 GREEN (T-DOCS-030..034); AC-D3v3-3 MANUAL (architect + tech-writer approved); AC-D3v3-2 (mmdc CI) DEFERRED — GAP-v0.3-6 carried to NB-6 for v0.4 devops |
| Priority | P1 — required by I8 for M3 sign-off |
| Owner | tech-writer |

**Scope:**
`docs/sequences/graph-recompute.mmd` — new Mermaid sequenceDiagram. Must show:
- Watcher ingest → data_version bump → debounce timer fires (or collapses burst)
- GraphEngine.recompute() → igraph FA2 → Postgres coords written
- `GET /graph` cache-miss path: FA2 triggered → coords fetched
- `GET /graph` cache-hit path: coords returned directly, no FA2
- Annotate: FA2 runs ONLY server-side; client receives precomputed coords only.
Also resolve the mmdc CI render check deferred from v0.2 (best-effort carry-forward).

**Acceptance criteria:**
- AC-D3v3-1: `docs/sequences/graph-recompute.mmd` is a valid Mermaid sequenceDiagram; both cache-hit and cache-miss paths shown; debounce collapse annotated.
- AC-D3v3-2: Diagram passes mmdc CI render check (resolving v0.2 carry-forward AC-D3-3).
- AC-D3v3-3: Diagram reviewed and approved by architect and tech-writer; consistent with engine.py + cache.py implementation.

---

### D5 — UI screenshots via Playwright (first occurrence)

| Field | Value |
|-------|-------|
| Feature ID | D5 (docs artifact — first required sprint) |
| Sprint | v0.3 |
| Status | done-pending-live-perf — AC-D5-4 GREEN (docs/screens/ dir exists); AC-D5-1/2/3 DEFERRED-TO-LIVE (harness at frontend/e2e/graph-perf.spec.ts; 0 PNGs committed; run command in docs/sprints/v0.3-pm-signoff.md §6) |
| Priority | P1 — required by I8 / CLAUDE.md §9 from v0.3 onward |
| Owner | qa-test-engineer |

**Scope:**
Playwright E2E test (`frontend/tests/`) captures PNG screenshots:
1. `docs/screens/graph-viewer-initial.png` — sigma viewer on load (before graph renders or
   at initial network request).
2. `docs/screens/graph-viewer-rendered.png` — sigma canvas with nodes and edges visible.
3. `docs/screens/graph-viewer-node-click.png` — after clicking a node; tooltip or drawer
   visible with page title. (Optional if tooltip is not implemented in v0.3.)
`make screenshots` target runs the Playwright capture.

**Acceptance criteria:**
- AC-D5-1: `docs/screens/graph-viewer-initial.png` captured by Playwright; non-empty file; committed.
- AC-D5-2: `docs/screens/graph-viewer-rendered.png` shows sigma canvas with at least 1 visible node; non-empty file; committed.
- AC-D5-3: Playwright screenshot test is part of `make test` or a separate `make screenshots` target; runs headless in CI.
- AC-D5-4: G2 and G4 Playwright performance assertions run in the same E2E suite as the screenshot capture.

---

### D1/D2/D4 updates (continuous)

| Field | Value |
|-------|-------|
| Feature ID | D1, D2, D4 (docs artifacts — continuous) |
| Sprint | v0.3 |
| Status | done — AC-D1v3-1 GREEN (T-DOCS-035..037) + MANUAL (architect + tech-writer approved); AC-D2v3-1 GREEN (T-DOCS-038..041, make er zero drift, 6 tables); AC-D4v3-1 GREEN (T-DOCS-042..045, make openapi zero drift, GET /graph typed) |
| Priority | P1 — required before M3 docs gate |
| Owner | tech-writer (D1 narrative); backend-engineer (D2 via `make er`; D4 via `make openapi`) |

**Scope:**
D1: update `docs/architecture/component.mmd` — add GraphEngine (graph/engine.py),
GraphCache (graph/cache.py), and the `GET /graph` endpoint to the component diagram.
D2: regenerate `docs/er/schema.mmd` via `make er` after adding pages.x/y columns and
the edges table to models.py. Must match live Postgres schema.
D4: regenerate `docs/api/openapi.json` via `make openapi` after adding `GET /graph`.

**Acceptance criteria:**
- AC-D1v3-1: component.mmd updated to include GraphEngine, GraphCache, and sigma viewer as components. Reviewed by architect and tech-writer.
- AC-D2v3-1: schema.mmd regenerated via `make er`; includes pages.x/y float columns and edges table with id, source_page_id, target_page_id, weight columns. Matches live Postgres schema.
- AC-D4v3-1: openapi.json updated; `GET /graph` endpoint documented with typed response schema (nodes, edges, data_version, cached). No other endpoints added without PM approval.

---

## Tracked tech-debt — NB follow-ups from M2

These items are tracked as formal backlog entries with feature ID references. They are
hardening tasks carried from M2, not new features.

### NB-1 — Widen fallback exception clause (httpx.HTTPStatusError)

| Field | Value |
|-------|-------|
| Tracking ID | NB-1 |
| Sprint | v0.3 (hardening) |
| Status | done — AC-NB1-1..2 GREEN (T-BL-*); orchestrator.py updated; I7 bounded-fallback preserved |
| Priority | P1 — prevents silent fallback bypass on HTTP 5xx from inference providers |
| Owner | backend-engineer |
| Source | v0.2-pm-signoff.md §5; v0.2-architect-review §2 |

**Item:** In `backend/app/ingest/orchestrator.py`, the provider fallback `except` clause
currently catches only `TimeoutError` and `ConnectionError`. An HTTP 503 (or other 5xx)
from the Ollama/Anthropic endpoint arrives as `httpx.HTTPStatusError`, which currently
bypasses the fallback and surfaces directly as `IngestError` without engaging the single
fallback retry. Add `httpx.HTTPStatusError` to the except clause per ADR-0009 §4.

**Acceptance criteria:**
- AC-NB1-1: `httpx.HTTPStatusError` (HTTP 503) from the primary provider triggers the fallback exactly once; a second failure surfaces as IngestError (per I7 bounded-fallback rule).
- AC-NB1-2: Unit test: mock primary provider raises `httpx.HTTPStatusError(503)` → fallback fires → fallback also raises → IngestError surfaced. No infinite retry.

---

### NB-2 — Scope T-CQ-009 I6 guard to import lines only

| Field | Value |
|-------|-------|
| Tracking ID | NB-2 |
| Sprint | v0.3 (hardening) |
| Status | done — AC-NB2-1..2 GREEN (T-CQ-009); guard now scopes to import lines only; negative test case added |
| Priority | P2 — zero functional impact; prevents false negatives accumulating as codebase grows |
| Owner | backend-engineer |
| Source | v0.2-pm-signoff.md §5 |

**Item:** T-CQ-009 (I6 static guard) currently does a whole-file substring search for
"InferenceProvider". This forced avoidance of that word in a main.py docstring. Fix: scope
the guard to import statement lines only — e.g., check that `^from` or `^import` lines in
files outside `backend/app/ingest/provider/` do not import InferenceProvider or its
subclasses. This is a test code change only; no production code changes required.

**Acceptance criteria:**
- AC-NB2-1: T-CQ-009 updated to check only `^from` / `^import` lines for forbidden imports outside provider/. The word "InferenceProvider" may appear in docstrings, comments, and log strings without triggering the guard.
- AC-NB2-2: The updated test still catches a genuine forbidden import (added as a negative test case in a temp fixture).

---

### NB-4 — CLI cost logging: use SDK-reported total_cost_usd when API key is present

| Field | Value |
|-------|-------|
| Tracking ID | NB-4 |
| Sprint | v0.3 (hardening) |
| Status | done — AC-NB4-1..3 GREEN (T-CLICOST-001..003); SDK cost used when available; $0 convention preserved for build-time path |
| Priority | P2 — I7 cost accuracy for runtime API-key use of CliAgentProvider |
| Owner | ai-agent-engineer |
| Source | v0.2-pm-signoff.md §5 (NB-4 added by PM during Sprint 3 kickoff; not in original §5 list) |

**Item:** CliAgentProvider currently records `total_cost_usd = 0.00` by convention (build-time
agent credits, ADR-0009). When the provider is invoked at runtime with a real Anthropic API
key (user's `provider_config` row has valid key), the claude-agent-sdk may return
cost/usage metadata. If SDK metadata is available in the `ClaudeCodeOutput`, use the
reported cost instead of the $0 convention. If metadata is absent (build-time / no key),
emit the WARNING and keep $0. No change to ADR-0009's stated convention for build-time use.

**Acceptance criteria:**
- AC-NB4-1: When SDK output metadata includes a cost field with value > 0, that value is written to `ingest_runs.total_cost_usd`.
- AC-NB4-2: When SDK output metadata is absent or cost = 0 (build-time path), `total_cost_usd = 0.00` is written and a WARNING is logged (existing behaviour preserved).
- AC-NB4-3: Unit test: mock SDK output with cost metadata → assert ingest_runs row has correct non-zero cost.

---

### NB-5 — OLLAMA_URL missing from docker-compose.yml backend service

| Field | Value |
|-------|-------|
| Tracking ID | NB-5 |
| Sprint | v0.3 (pre-start blocker) |
| Status | done — AC-NB5-1..2 GREEN (static CI checks); AC-NB5-3 LIVE (correct; live infra test deferred to TrueNAS run). OLLAMA_URL in docker-compose.yml and .env.example. |
| Priority | P0 — blocks live Local provider via `make up`; blocks v0.3 graph ingest testing |
| Owner | devops-engineer |
| Source | v0.2-pm-signoff.md §5 (NB-5 added by PM during Sprint 3 kickoff) |

**Item:** `OLLAMA_URL` is not passed to the `synapse-backend` container in
`docker-compose.yml` or `.env.example`. When a developer runs `make up`, the backend
container cannot reach the Ollama service, making the Local provider (OllamaProvider)
unusable in the Docker environment. This blocks live graph-ingest testing in v0.3 which
depends on the Local provider to populate the links table with real wikilinks.

Fix: add `OLLAMA_URL` to the `environment` section of the `synapse-backend` service in
`docker-compose.yml` (reading from env var or .env), and add `OLLAMA_URL=` to
`.env.example` with a comment noting the default (`http://localhost:11434`).

**Acceptance criteria:**
- AC-NB5-1: `docker-compose.yml` backend service environment block includes `OLLAMA_URL: ${OLLAMA_URL}`.
- AC-NB5-2: `.env.example` contains `OLLAMA_URL=http://localhost:11434` with a comment.
- AC-NB5-3: `make up` + ingest trigger with Local provider config successfully calls Ollama (integration test or manual smoke on dev machine).

---

### NB-6 — mmdc CI render check (carried from v0.2 + v0.3)

| Field | Value |
|-------|-------|
| Tracking ID | NB-6 |
| Sprint | v0.4 (devops-engineer) |
| Status | backlog |
| Priority | P2 — T-DOCS-MANUAL-003 sentinel passes unconditionally; Mermaid files content-validated but not render-validated in CI |
| Owner | devops-engineer |
| Source | GAP-v0.2-3 / GAP-v0.3-6; carried from v0.2 (AC-D3-3 deferred) through v0.3 |

**Item:** Install mmdc (Mermaid CLI) in the CI environment and add a render step that validates
`docs/sequences/ingest-routing.mmd`, `docs/sequences/ingest-loop.mmd`, and
`docs/sequences/graph-recompute.mmd` render without parse errors. Currently T-DOCS-MANUAL-003
passes unconditionally as a sentinel. devops-engineer must wire mmdc into CI before M4 sign-off.

**Acceptance criteria:**
- AC-NB6-1: mmdc is installed in CI (package.json devDependency or CI action step).
- AC-NB6-2: CI step runs `mmdc -i docs/sequences/*.mmd -o /tmp/mmd-render/` and exits 0.
- AC-NB6-3: T-DOCS-MANUAL-003 is replaced with a real mmdc assertion (or the sentinel is updated to assert mmdc exit code = 0).

---

### NB-7 — Cosmetic: graph-recompute.mmd hit-path read source (optional polish)

| Field | Value |
|-------|-------|
| Tracking ID | NB-7 |
| Sprint | v0.4 (tech-writer, optional) |
| Status | backlog |
| Priority | P3 — cosmetic; non-blocking per architect review §7 note D-1 |
| Owner | tech-writer |
| Source | v0.3-architect-review.md §7 note D-1 |

**Item:** Line 49 of `docs/sequences/graph-recompute.mmd` shows the cache-hit path as
`GC->>PG: SELECT pages.x/y + edges`. The actual implementation serves a cache HIT from the
in-process `_snapshot` (not a Postgres re-query). The load-bearing I2 claim is correct; the
source of the read (in-memory vs. PG) is a simplification. Optional one-line diagram update
to show `GC->>Client: (from in-process snapshot)` on the HIT path.

**Acceptance criteria:**
- AC-NB7-1: graph-recompute.mmd updated to show HIT path served from in-process snapshot; reviewed by architect.

---

### NB-8 — Cosmetic: component.mmd store filename label (optional polish)

| Field | Value |
|-------|-------|
| Tracking ID | NB-8 |
| Sprint | v0.4 (tech-writer, optional) |
| Status | backlog |
| Priority | P3 — cosmetic; non-blocking per architect review §7 note D-2 |
| Owner | tech-writer |
| Source | v0.3-architect-review.md §7 note D-2 |

**Item:** `docs/architecture/component.mmd` labels the graph Zustand store component as
`store/graph.ts`. The real file is `store/graphStore.ts`. One-word label drift.

**Acceptance criteria:**
- AC-NB8-1: component.mmd updated to label store component `store/graphStore.ts`; reviewed by tech-writer.

---

## Sprint 4 — v0.4 — M4 "Usable & fluid"

**Sprint status: DONE — M4 CLOSED**
**EC-M4-HCP: CLEARED 2026-06-29 — docs/sprints/v0.4-m4-closure.md**
**v0.4.0 released — https://github.com/Emanuele-Chiummo/llm-wiki-synapse/releases/tag/v0.4.0**
**M4-EXT: DONE — F1-UPLOAD and F1-SCHED shipped (commit f7c7865 / 59765f3)**
**M4-HARD: DONE — MET-WITH-FOLLOWUPS — All 4 gate sign-offs received; PM verdict 2026-06-29**
**Sprint 5 gate: OPEN — EC-M4-HCP cleared; Sprint 5 scope locked in docs/sprints/v0.5-scope.md**
**M4-HARD scope-lock document: docs/sprints/v0.4-hard-scope.md**
Scope locked: 2026-06-28 by product-manager. Scope log: docs/sprints/v0.4-pm-scope.md
PM sign-off: docs/sprints/v0.4-pm-signoff.md | 2026-06-28
Scope amended: 2026-06-28 — F1-NAV (Left Navigation Rail) and F1-INGEST-VIEW (Ingest
Activity View) added to Phase 2 at stakeholder Emanuele's explicit request. Visual direction:
nashsu/llm_wiki-inspired. BE-INGEST-RUNS backend endpoint added as explicit work item.
M4-EXT scope added: 2026-06-28 — F1-UPLOAD (document upload from UI) and F1-SCHED
(scheduled folder import in Settings) added at stakeholder Emanuele's explicit request;
both added before EC-M4-HCP closes. AC details: docs/sprints/v0.4-pm-scope.md §8.
Branch: sprint/v0.4
Invariants with heightened priority: I3 (no per-token heavy work — headline for G3),
I4 (CodeMirror 6, TanStack Virtual, no WYSIWYG). All 9 invariants apply.
Velocity: ON SCOPE. All 25 original locked work items delivered or formally deferred. G3
mandatory streaming perf gate GREEN (no live-run waiver needed). 4-phase delivery plan
executed within 2-week envelope. M4-EXT adds 2 items; sprint remains within M4 envelope
as EC-M4-HCP has not yet closed.
Documentation gap: docs/USER.md absent (AC-D6-1 NOT MET); DEPLOY.md not promoted to v0.4
draft (AC-D6-2 partial). D6 is a conditional requirement before EC-M4-HCP can be closed.
docs/DEPLOY.md must additionally document Docker volume mounts for F1-SCHED (AC-S-2/S-3).
Human checkpoint: EC-M4-HCP — Emanuele must confirm 8 browser conditions (6 original +
EC-M4-HCP-U + EC-M4-HCP-S). See docs/sprints/v0.4-pm-scope.md §8. Sprint 5 BLOCKED
until all 8 conditions confirmed.
Carried nits: NB-7, NB-8, NB-9 (CI branch filter), architect P1/P2/P3 nits, G4 live-run,
G2 runtime live-run, chat-think-block D5 screenshot — all non-blocking, moved to M5.

---

### Baseline M4 features

| Feature ID | Description | Status | Notes |
|------------|-------------|--------|-------|
| F1 | 3-panel shell (tree / chat / preview), resizable | done | AC-F1-1..7 GREEN; I3/I4 verified; Phase 1 delivered |
| F1-NAV | Left Navigation Rail: persistent app navigation with Pages / Graph / Ingest / Settings sections; Chat active in Phase 3 | done | AC-F1-NAV-1..8 GREEN; nash-style icon rail; i18n labels |
| F1-INGEST-VIEW | Ingest Activity View: read-only list of recent ingest runs (status, pages_created, cost USD 4dp, timestamps, errors) + Run Ingest trigger button. NOT F9. | done | AC-F1-IV-1..8 GREEN; migration 0006; polling while running |
| BE-INGEST-RUNS | Backend: GET /ingest/runs endpoint — paginated, vault_id filter, started_at DESC; D4 updated | done | AC-BE-IR-1..5 GREEN; openapi.json regenerated |
| F6 | Multi-conversation persistent chat, cited-refs stub, regenerate; save-to-wiki button disabled (M5) | done-with-deferral | AC-F6-1/2/4/6 GREEN; AC-F6-3 citations empty (F5 M5); AC-F6-5 save-to-wiki disabled M5 |
| F7 | Reasoning `<think>` display, streaming, collapsed by default | done | AC-F7-1..4 GREEN; streaming-safe split; stored in full |
| F8 | LaTeX to Unicode (parse at stream END only) | done | AC-F8-1..4 GREEN; I3 verified; fires once on done event |
| F14 | Configurable context window 4K–1M; 60/20/5/15 budget | done | AC-F14-1..5 GREEN; persisted in provider_config |
| F17 (UI) | Provider Selector UI; wired to backend provider_config | done | AC-F17-UI-1..6 GREEN; reads/writes /provider/config; I6 no hardcoded IDs |
| F16 (rest) | i18n IT/EN, settings persistence, .obsidian auto-gen, GFM, multi-provider chat timeout | done | AC-F16 all GREEN; en/it parity; localStorage persist; obsidian vault_id change handled |
| G3 | Streaming perf gate — MANDATORY — GREEN | done | T-E2E-G3-001 PASS; 0 long tasks; parse-once; selector discipline verified |
| D5 (update) | UI screenshots refreshed: 10 PNGs committed | done | docs/screens/: graph-obsidian, shell-3panel, ingest-section, settings-section, provider-selector-open, navrail-graph-active, chat-streaming, chat-conversation + 2 node-selected variants |
| D6 | USER.md + DEPLOY.md drafts | NOT MET — CONDITIONAL | docs/USER.md ABSENT (AC-D6-1 gap); DEPLOY.md not promoted to v0.4 draft (AC-D6-2 partial); must be delivered before EC-M4-HCP closes |
| NB-6 | mmdc CI render check (devops-engineer) | done | ci.yml wired: mmdc installed + loop over all .mmd; T-DOCS-MANUAL-003 replaced |
| NB-7 | graph-recompute.mmd hit-path cosmetic fix (tech-writer, optional P3) | backlog-M5 | Non-blocking; carried to M5 tech-writer polish |
| NB-8 | component.mmd store filename label cosmetic fix (tech-writer, optional P3) | backlog-M5 | Non-blocking; carried to M5 tech-writer polish |

---

### M4-GUX — GraphUX formalization (work done at v0.3→v0.4 transition; gated and closed)

Work executed pragmatically before formal sprint kickoff. All sub-items gated through
Phase 0. ADR: docs/adr/0016-obsidian-graph-rendering.md — Accepted + Reviewed.

| Sub-ID | Description | Status | Notes |
|--------|-------------|--------|-------|
| M4-GUX-1 | Engine structural-only edges: direct-link + shared-source as generators; AA and same-type demoted to weight modulators | done | AC-GUX-1/2 GREEN; TRACEABILITY P3–P5 corrected to "absent" |
| M4-GUX-2 | Per-edge `kind` field ("link"/"source") in edges table + GraphEdgeResponse | done | AC-GUX-3 GREEN; migration 0004; backward-compatible |
| M4-GUX-3 | Node size = 1.0 + 1.0·sqrt(structural_degree); degree redefined to structural_degree | done | AC-GUX-1 GREEN; ADR-0016 §2 |
| M4-GUX-4 | Migrations 0004 (edges.kind) + 0005 (pages.pinned); D2 + D4 regenerated | done | make er + make openapi zero drift confirmed |
| M4-GUX-5 | Server-side near-circular layout envelope (_compress_to_disc, ~1.04 aspect ratio) | done | I2 intact; server-side only |
| M4-GUX-6 | Single-node DRAG with persistence: PATCH /pages/{id}/position; pages.pinned; no relayout, no data_version bump | done | AC-GUX-4/5 GREEN; I2-compatible; T-NCL-001..022 still pass |
| M4-GUX-7 | Frontend Obsidian-style viewer: CVD-safe palette, node size ∝ connections, hover-dim, accessible labels, LOD, prefers-reduced-motion, aria-live | done | AC-GUX-6/7/8/9 GREEN; Playwright a11y no critical violations |
| M4-GUX-8 | seed_demo_vault.py: 140-node scale-free realistic demo dataset | done | D5 baseline captured; graph PNGs committed |

---

### F1-NAV — Left Navigation Rail

| Field | Value |
|-------|-------|
| Feature ID | F1-NAV (sub-item of F1) |
| Sprint | v0.4 |
| Status | done — AC-F1-NAV-1..8 GREEN |
| Priority | P0 for Phase 2 |
| Owner | frontend-engineer |
| Source | Stakeholder request 2026-06-28 (Emanuele); visual direction: nashsu/llm_wiki |

**Scope:**
Restructure the shell's left region into a persistent vertical navigation rail. The rail
has 4 active section items — Pages (file tree), Graph (sigma viewer), Ingest (ingest activity),
Settings (provider/settings panel) — and 1 reserved item: Chat (active in Phase 3). Clicking
a section switches the main content area without a page reload. The activity panel (vault name,
active provider, last ingest timestamp, data_version) remains visible at all times. Section
labels are i18n translation keys.

**Acceptance criteria:** docs/sprints/v0.4-pm-scope.md §2 AC-F1-NAV-1..8

---

### F1-INGEST-VIEW — Ingest Activity View

| Field | Value |
|-------|-------|
| Feature ID | F1-INGEST-VIEW (sub-item of F1) |
| Sprint | v0.4 |
| Status | done — AC-F1-IV-1..8 GREEN |
| Priority | P0 for Phase 2 |
| Owner | frontend-engineer (view); backend-engineer (BE-INGEST-RUNS) |
| Source | Stakeholder request 2026-06-28 (Emanuele); visual direction: nashsu/llm_wiki |

**Scope:**
A read-only view showing recent ingest runs from the `ingest_runs` table, accessible via
the Ingest section of the navigation rail. Each row shows: status badge (color-coded),
provider type, pages created, total_cost_usd (4 decimal places, per I7), relative started_at
timestamp, and truncated error message (if any). A "Run Ingest" button triggers POST
/ingest/trigger and auto-refreshes the list. While any run is in "running" status the list
polls (default every 5s). NOT F9: no approve/reject/skip actions. F9 is M5.

**Acceptance criteria:** docs/sprints/v0.4-pm-scope.md §2 AC-F1-IV-1..8

---

### BE-INGEST-RUNS — GET /ingest/runs backend endpoint

| Field | Value |
|-------|-------|
| Feature ID | BE-INGEST-RUNS (backend work item; prerequisite for F1-INGEST-VIEW) |
| Sprint | v0.4 |
| Status | done — AC-BE-IR-1..5 GREEN |
| Priority | P0 for Phase 2 |
| Owner | backend-engineer |
| Source | Derived from F1-INGEST-VIEW stakeholder request 2026-06-28 |

**Scope:**
New FastAPI endpoint: GET /ingest/runs. Returns a paginated list of rows from the
`ingest_runs` table ordered by started_at DESC. Response fields per item: id (uuid),
vault_id (uuid), status (enum: running/completed/failed/converged_false), provider_type
(string), pages_created (int), iterations_used (int), total_cost_usd (decimal),
started_at (timestamptz), completed_at (timestamptz nullable), error_message (text nullable).
Query params: limit (int, default 20, max 100), offset (int, default 0), vault_id (uuid,
optional). Migration 0006 applied. openapi.json regenerated (D4 zero-drift confirmed).

**Acceptance criteria:** docs/sprints/v0.4-pm-scope.md §2 AC-BE-IR-1..5

---

---

### M4-EXT items

| Feature ID | Description | Status | Notes |
|------------|-------------|--------|-------|
| F1-UPLOAD | Document upload from UI: drag-and-drop / file-picker in Ingest section; POST /ingest/upload (multipart); saves to vault/raw/sources/<sanitized-name>; triggers ingest; .txt/.md only | in-progress | AC-U-1..11 defined in docs/sprints/v0.4-pm-scope.md §8; F12 boundary explicit: no PDF/DOCX/etc in v0.4 |
| F1-SCHED | Scheduled folder import in Settings: enabled toggle, source folder (mounted container path only), frequency; bounded scanner (I7); GET/PUT /import-schedule + POST /import-schedule/run-now; last-run status display | in-progress | AC-S-1..12 defined in docs/sprints/v0.4-pm-scope.md §8; I1/I5/I7 invariants in force; container path constraint explicit |

---

### F1-UPLOAD — Document upload from the UI

| Field | Value |
|-------|-------|
| Feature ID | F1-UPLOAD (M4-EXT sub-item of F1) |
| Sprint | v0.4 (M4-EXT extension — before EC-M4-HCP closes) |
| Status | in-progress |
| Priority | P1 — requested by stakeholder; extends Ingest section |
| Owner | frontend-engineer (upload UI); backend-engineer (POST /ingest/upload) |
| Source | Stakeholder request 2026-06-28 (Emanuele); M4-EXT |

**Scope:**
A drag-and-drop zone and/or file-picker in the Ingest section. Accepted file types:
`.txt` and `.md` ONLY. The frontend calls POST /ingest/upload (multipart/form-data).
The backend sanitizes the filename (path-traversal safe), writes the file to
vault/raw/sources/, and triggers ingest via the existing pipeline. The ingest run
appears in the runs list (F1-INGEST-VIEW). The endpoint enforces file-type (HTTP 415
for anything other than text/plain or text/markdown) and size limits (HTTP 413 over
configurable threshold, default 10 MB).

**F12 boundary — explicit:** Only .txt and .md files are accepted in v0.4.
Multi-format (PDF, DOCX, PPTX, XLSX, images, AV) is F12/M5 and must NOT be added here.
No pypdf, unstructured, python-docx, python-pptx, openpyxl, or AV library dependency
may be introduced as part of this feature.

**Key invariants:** I1 (incremental — duplicate file upload does not create duplicate
records); I5 (vault integrity — file written to vault/raw/sources/ only, not wiki/).

**Acceptance criteria:** docs/sprints/v0.4-pm-scope.md §8 AC-U-1..11

---

### F1-SCHED — Scheduled folder import (in Settings)

| Field | Value |
|-------|-------|
| Feature ID | F1-SCHED (M4-EXT sub-item of F16-rest/Settings) |
| Sprint | v0.4 (M4-EXT extension — before EC-M4-HCP closes) |
| Status | in-progress |
| Priority | P1 — requested by stakeholder; extends Settings section |
| Owner | frontend-engineer (Settings sub-section); backend-engineer (endpoints + scheduler + migration) |
| Source | Stakeholder request 2026-06-28 (Emanuele); M4-EXT |

**Scope:**
An "Automatic import" sub-section in the Settings panel. The user configures: enabled
toggle, source folder path, frequency (Hourly / Daily / Weekly / Manual). The backend
persists this in a new `import_schedules` Postgres table (Alembic migration required).
The scheduler periodically scans source_dir and copies new files to vault/raw/sources/,
then the watcher indexes them (I1). A "Run now" button triggers POST /import-schedule/run-now
(HTTP 202 or 409 if already running). The Settings UI shows last_run_at and last_status.

**Critical container path constraint:** The source folder must be a Docker-volume-mounted
path visible from inside the container. The backend validates this at save time (PUT
/import-schedule checks path exists + is readable). The UI shows a permanent helper note
explaining this. docs/DEPLOY.md must document how to configure the volume mount. This
constraint is an infrastructure reality, not a deferral.

**Key invariants:**
- I7 (bounded scheduler): max_files_per_run (default 50) + scan_timeout_seconds (default
  300 s) caps per scan run; no overlapping scans; existing ingest pipeline logs cost per run.
- I1 (incremental): only new files (not already indexed) are imported per scan.
- I5 (vault integrity): files copied to vault/raw/sources/ only; wiki/ written by ingest pipeline only.

**Backend endpoints:**
- GET /import-schedule — returns current schedule for active vault
- PUT /import-schedule — creates or replaces schedule; validates container path
- POST /import-schedule/run-now — triggers immediate scan (HTTP 202 or 409 if busy)

All 3 endpoints in openapi.json (D4 zero-drift gate). New table in schema.mmd (D2 zero-drift gate).

**Acceptance criteria:** docs/sprints/v0.4-pm-scope.md §8 AC-S-1..12

---

**Acceptance criteria for all M4 items:** docs/sprints/v0.4-pm-scope.md §2
**M4-EXT acceptance criteria:** docs/sprints/v0.4-pm-scope.md §8
**Phase plan:** docs/sprints/v0.4-pm-scope.md §4
**DoD gate checklist (original 11 gates + 2 M4-EXT EC-M4-HCP conditions):** docs/sprints/v0.4-pm-scope.md §5 + §8

---

### M4-HARD — Post-human-testing hardening increment (scope-locked 2026-06-29)

Opened after EC-M4-HCP human testing revealed usability problems. This is NOT a new
milestone. It is a hardening pass on branch sprint/v0.4 before EC-M4-HCP can be
confirmed and before Sprint 5 begins. Scope-lock document: docs/sprints/v0.4-hard-scope.md.
All 4 sign-offs still required before PM closes M4-HARD and unblocks Sprint 5.

| Feature ID | Description | Status | Priority |
|------------|-------------|--------|----------|
| F1-HARD-SETTINGS | Settings panel redesigned into 9-section left-nav (General / LLM Models / Embeddings / Source Watch / API+MCP / Output / Interface / Maintenance / About) | done | P0 |
| F1-HARD-COLLAPSE | Left and right panels gain collapse/expand chevron buttons (react-resizable-panels usePanelRef) | done | P0 |
| F1-HARD-PROVIDER-EDIT | Settings > LLM Models is now editable: add + delete providers via POST/DELETE /provider/config | done | P0 |
| F1-HARD-MCP-STUB | API+MCP settings section added with "coming in M5" placeholder | done | P1 |
| F1-HARD-NAV-ORDER | Nav rail order fixed: logo → Chat → Wiki → Sources → Search → Graph → Lint → Review → Deep Search → Settings; default section is Chat | done | P0 |
| F1-HARD-EMBED-STUB | Vector Embeddings settings section added as M5 placeholder | done | P1 |
| F1-HARD-CONV-HISTORY | Conversation history length control in Output settings section (2/4/6/8/10/20 messages) | done | P1 |
| F1-HARD-NAV-LABELS | Nav rail items MUST display text labels beside icons — not icon-only. Rail width expands to accommodate. | done | P0 — AC-HARD-LBL-1..8 GREEN |
| F1-HARD-M5-PLACEHOLDER | M5 nav items (Search, Lint, Review, Deep Search): remove from nav rail for M4. Add them back in M5 when logic exists. | done | P0 — AC-HARD-M5P-1..7 GREEN |

**Gate sign-offs (all 4 received):**
- QA (qa-test-engineer): PASS-WITH-NOTES — 371/371 vitest green; 4 defects self-fixed; 3 gaps escalated and closed by FE pass-2
- Architect (solution-architect): APPROVE-WITH-CONDITIONS — ADR-0021 Accepted; C1/C2/C3 all closed by FE pass-2
- Tech-Writer (tech-writer): PASS-WITH-PENDING — 5/6 D5 screenshots; USER.md updated; ADR-0021 indexed; 1 screenshot (CF-HARD-1) non-blocking follow-up
- PM (product-manager): MET-WITH-FOLLOWUPS — 2026-06-29 — see docs/sprints/v0.4-hard-scope.md §6-PM

**Carry-forward items:**
- CF-HARD-1: Recapture docs/screens/shell-collapsed-panel.png (root cause fixed; make screenshots needed) — M5 pre-start
- CF-HARD-6: Zustand persist rehydration guard for removed section names — M5 conditional (only if persist middleware added)

**M4-HARD acceptance criteria:** docs/sprints/v0.4-hard-scope.md §2
**Anti-scope-creep:** docs/sprints/v0.4-hard-scope.md §4
**M4-HARD exit criteria + PM verdict:** docs/sprints/v0.4-hard-scope.md §5 + §6-PM

---

## Sprint 5 — v0.5 — M5 "Feature parity core"

**Sprint status: M5 MET-PENDING-EC-M5-HCP — All 5 phases DONE; full PM exit-criteria declaration: docs/sprints/v0.5-m5-exit-criteria.md (2026-06-29); Sprint 6 BLOCKED until EC-M5-HCP confirmed**
**Phase 1 DONE (PM sign-off 2026-06-29); Phase 2 (F10) DONE (PM sign-off 2026-06-29); Phase 3 (F9 + F12) DONE (PM sign-off 2026-06-29); Phase 4 (F13) DONE (PM sign-off 2026-06-29); Phase 5 (F1-MCP-UI) DONE (PM sign-off 2026-06-29)**
**Scope locked: 2026-06-29 by product-manager — docs/sprints/v0.5-scope.md**
**Scope amended: 2026-06-29 — Amendment A1: F1-MCP-UI added (stakeholder request: Emanuele Chiummo) — docs/sprints/v0.5-scope.md §3 Amendment A1**
**Phase 1 PM sign-off: docs/sprints/v0.5-pm-phase1-signoff.md (verdict: DONE-WITH-FOLLOWUPS)**
**Phase 2 PM sign-off: docs/sprints/v0.5-pm-phase2-signoff.md (verdict: DONE-WITH-FOLLOWUPS)**
**Phase 3 PM sign-off: docs/sprints/v0.5-pm-phase3-signoff.md (verdict: DONE-WITH-FOLLOWUPS)**
**Phase 4 PM sign-off: docs/sprints/v0.5-pm-phase4-signoff.md (verdict: DONE-WITH-FOLLOWUPS)**
**Branch: sprint/v0.5 (cut from sprint/v0.4 after EC-M4-HCP cleared)**
**Prerequisite: M4 CLOSED — docs/sprints/v0.4-m4-closure.md (2026-06-29)**
**Invariants with heightened priority: I7 (bounded loops — headline, F10 deep research),
  I9 (SearXNG reuse — never Tavily; Qdrant + bge-m3 for embeddings). All 9 invariants apply.**

---

### F5 — 4-phase RAG retrieval

| Field | Value |
|-------|-------|
| Feature ID | F5 |
| Sprint | v0.5 |
| Status | done |
| Priority | P0 — Phase 1; dependency root for F6 citations, F17 chat, F10→F9 chain |
| Owner | backend-engineer (retrieval.py, GET /search); ai-agent-engineer (CliAgentProvider.chat() wiring) |

**Scope:**
`backend/app/rag/retrieval.py` — 4-phase retrieval pipeline:
(1) tokenized keyword search via Qdrant (bge-m3); (2) graph-expansion via Postgres `edges`
table (depth 1–2, no FA2 recompute); (3) token-budget allocation (20% of configured window
per F14); (4) context assembly with `[n]` citation markers. Returns `RetrievalContext`
Pydantic model with passages + page refs. `GET /search` REST endpoint.

**Key invariants:** I1 (no rescan), I2 (no graph recompute triggered), I3 (no retrieval
per-token during chat), I9 (reuse Qdrant + bge-m3 only).

**Acceptance criteria:** docs/sprints/v0.5-scope.md §4 AC-F5-1..8

---

### F6 (carry-forward) — AC-F6-3 citations + AC-F6-5 save-to-wiki

| Field | Value |
|-------|-------|
| Feature ID | F6 (AC-F6-3 + AC-F6-5 only — all other ACs done in M4) |
| Sprint | v0.5 |
| Status | done |
| Priority | P0 — directly user-visible chat improvement |
| Owner | frontend-engineer (citation rendering); backend-engineer (POST /ingest wire) |

**Scope:**
AC-F6-3: `[n]` citation markers in assistant messages, populated by F5 retrieval context.
Stored in messages table (JSONB citations field). Rendered as clickable superscripts.
AC-F6-5: save-to-wiki button enabled and wired to POST /ingest; inline result shown on 202.

**Acceptance criteria:** docs/sprints/v0.5-scope.md §4 AC-F6-3, AC-F6-5

---

### F17 (carry-forward) — CliAgentProvider.chat()

| Field | Value |
|-------|-------|
| Feature ID | F17 (CliAgentProvider.chat() only — all other ACs done in M2/M4) |
| Sprint | v0.5 |
| Status | done |
| Priority | P1 — removes NotImplementedError for CLI backend chat |
| Owner | ai-agent-engineer |

**Scope:**
Implement `CliAgentProvider.chat(messages, retrieval_context)` — delegates to
claude-agent-sdk with retrieval context injected. Bounded by max_iter + token_budget (I7).
Streaming interface consistent with OllamaProvider and ApiProvider. total_cost_usd logged.

**Acceptance criteria:** docs/sprints/v0.5-scope.md §4 AC-F17-CHAT-1..3

---

### F9 — Async HITL review queue

| Field | Value |
|-------|-------|
| Feature ID | F9 |
| Sprint | v0.5 |
| Status | done — AC-F9-1..11 all GREEN; AC-F10-5 (F9→F10 wiring) GREEN; I7 bounded generator PROVEN; hook safety PROVEN; chat() 2-arg signature CONFIRMED; PM sign-off 2026-06-29 |
| Priority | P0 — Phase 3; K8 principle; F9 is the async curation queue |
| Owner | backend-engineer (review.py, REST endpoints); frontend-engineer (Review nav section) |
| Unblocked | F10 DONE (Phase 2 PM sign-off 2026-06-29); POST /research/start live; AC-F10-5 (F9→F10 wiring) now workable in Phase 3 |

**Scope:**
`backend/app/ops/review.py` — async queue backed by `review_items` Postgres table. Enqueue
on every ingest (item_type=new_page). Actions: Create (approve), Deep-Research (delegates
to F10 POST /research/start), Skip. Pre-generated queries (1–3 per item, bounded 1
InferenceProvider call). Review nav section activated in UI (previously "coming in M5"
placeholder). TanStack Virtual for list >50 items (I4).

**F9 boundary (explicit):** This is NOT the Ingest Activity View (F1-INGEST-VIEW, done
in M4). F9 is a separate Review nav section. The Sources section (F1-INGEST-VIEW) is not
modified. Any engineer adding F9 actions to the Sources section will be blocked.

**Key invariants:** I6 (pre-generated query calls through InferenceProvider), I7 (bounded
1 call/item for query generation).

**Acceptance criteria:** docs/sprints/v0.5-scope.md §4 AC-F9-1..8

---

### F10 — Deep Research loop

| Field | Value |
|-------|-------|
| Feature ID | F10 |
| Sprint | v0.5 |
| Status | done |
| Priority | P0 — Phase 2; headline I7 feature; unblocks F9 Deep-Research action |
| Owner | backend-engineer (deep_research.py, REST endpoints); frontend-engineer (Deep Search nav section) |
| PM sign-off | DONE-WITH-FOLLOWUPS — docs/sprints/v0.5-pm-phase2-signoff.md (2026-06-29) |
| Follow-ups | FU-P2-1: D5 Deep Search screenshots (pending-live; capture at EC-M5-HCP-3); FU-P2-2: USER.md no-provider note (tech-writer, Phase 4/5 docs gate); FU-P2-3: $1 anomaly WARNING test gap (GAP-v0.5-P2-1; non-blocking) |

**Scope:**
`backend/app/ops/deep_research.py` — bounded multi-query SearXNG loop:
query generation (InferenceProvider) → SearXNG search (I9; never Tavily); concurrency=3
(asyncio semaphore) → fetch+parse → assess sufficiency (InferenceProvider) → refine queries
(bounded by max_iter, default 3) → synthesize (InferenceProvider) → auto-ingest (via
existing orchestrated pipeline). New Postgres tables: `deep_research_runs`,
`deep_research_sources`. REST: POST /research/start, GET /research/runs,
GET /research/runs/{id}. "Deep Search" nav section activated.

**Key invariants:** I7 (max_iter + token_budget + concurrency≤3 ALL enforced; total_cost_usd
logged), I9 (SearXNG only), I6 (all InferenceProvider calls via ABC).

**Acceptance criteria:** docs/sprints/v0.5-scope.md §4 AC-F10-1..8

---

### F12 — Multi-format ingest

| Field | Value |
|-------|-------|
| Feature ID | F12 |
| Sprint | v0.5 |
| Status | done — AC-F12-1..7 all GREEN; companion flow (I1/I5) PROVEN; format-lib isolation PROVEN; images/AV placeholder is conscious gap (out-of-M5 scope per ADR-0025 §4.3); PM sign-off 2026-06-29 |
| Priority | P0 — Phase 3; removes .txt/.md constraint introduced by F1-UPLOAD (M4) |
| Owner | backend-engineer (ingest/extract.py; pyproject.toml deps; upload endpoint extension) |

**Scope:**
`backend/app/ingest/extract.py` — dispatch function `extract_text(file_path) -> str` with
per-format extractors: PDF (pypdf), DOCX (python-docx), PPTX (python-pptx), XLSX
(openpyxl), images (unstructured or placeholder), AV (placeholder). Extracted text fed
into existing `analyze()`/`generate()` pipeline — no new LLM calls in extraction.
Extend POST /ingest/upload to accept PDF/DOCX/PPTX/XLSX/image MIME types. Binary files
written to vault/raw/sources/; extracted companion .md also in vault/raw/sources/. wiki/
only written by ingest pipeline (I5, K1 3-layer rule).

**Key invariants:** I1 (same file twice = no duplicate), I5 (raw/ layer preserved), I6
(no direct LLM calls in extractor).

**Acceptance criteria:** docs/sprints/v0.5-scope.md §4 AC-F12-1..7

---

### F13 — Cascade deletion

| Field | Value |
|-------|-------|
| Feature ID | F13 |
| Sprint | v0.5 |
| Status | done-with-followups — AC-F13-1..7 GREEN; AC-D3-CD-1 GREEN; DEFECT-F13-001 (I1) + DEFECT-F13-002 (I5) both FIXED; PM sign-off 2026-06-29; FU-P4-1..6 logged |
| Priority | P0 — Phase 4; highest vault-integrity risk; placed last when vault is stable |
| Owner | backend-engineer (cascade_delete.py, DELETE /pages/{id}); frontend-engineer (delete modal) |

**Scope:**
`backend/app/ops/cascade_delete.py` — `cascade_delete(page_id)`: soft-delete Postgres row
+ Qdrant vector removal + index.md entry removal + targeted wikilink cleanup in wiki/ files
(3-method matching: exact title, slug, fulltext scan). Shared-entity warning (non-blocking).
data_version bump triggers existing debounced recompute. DELETE /pages/{id} REST endpoint.
Frontend delete action with confirmation modal showing shared-entity warnings.

**Key invariants:** I1 (targeted file edits only — no full rescan, no mass wiki rewrite),
I5 (vault remains valid Obsidian vault after deletion).

**Acceptance criteria:** docs/sprints/v0.5-scope.md §4 AC-F13-1..7

---

### D3 (update) — Sequence diagrams: deep-research + cascade-delete

| Field | Value |
|-------|-------|
| Feature ID | D3 (docs artifact — update) |
| Sprint | v0.5 |
| Status | done (deep-research.mmd DONE Phase 2; cascade-delete.mmd DONE Phase 4 — AC-D3-CD-1 GREEN; mmdc CI DEFERRED GAP-v0.5-3 carry-forward) |
| Priority | P1 — required by I8 for M5 sign-off |
| Owner | tech-writer (diagrams); architect (review) |

**Scope:**
`docs/sequences/deep-research.mmd` — new Mermaid sequenceDiagram for F10 loop (see
AC-D3-DR-1 for required content including max_iter and concurrency annotations).
`docs/sequences/cascade-delete.mmd` — new Mermaid sequenceDiagram for F13 (see
AC-D3-CD-1 for required content). Both must pass mmdc CI render check (NB-6 step already
wired from M4).

**Acceptance criteria:** docs/sprints/v0.5-scope.md §4 AC-D3-DR-1, AC-D3-CD-1, AC-D3-CI-1, AC-D3-REV-1

---

### D5 (update) — Screenshots refreshed for M5 UI surfaces

| Field | Value |
|-------|-------|
| Feature ID | D5 (docs artifact — update) |
| Sprint | v0.5 |
| Status | backlog |
| Priority | P1 — required by I8 for M5 sign-off |
| Owner | qa-test-engineer (Playwright capture); tech-writer (review) |

**Scope:**
New PNGs: `docs/screens/review-queue.png`, `docs/screens/deep-search-trigger.png`,
`docs/screens/upload-multiformat.png`, `docs/screens/cascade-delete-modal.png`.
CF-HARD-1 recapture: `docs/screens/shell-collapsed-panel.png`.
`make screenshots` target exits 0.

**Acceptance criteria:** docs/sprints/v0.5-scope.md §4 AC-D5-M5-1..3

---

### F1-MCP-UI — MCP Configuration UI [Amendment A1, 2026-06-29]

| Field | Value |
|-------|-------|
| Feature ID | F1-MCP-UI (sub-item of F1 + F17; Settings > API + MCP panel) |
| Sprint | v0.5 |
| Status | done-with-followups — AC-F1-MCP-UI-1..8 + -10 GREEN; AC-F1-MCP-UI-9 PENDING-LIVE (screenshot, folds into EC-M5-HCP-7); PM sign-off 2026-06-29; EC-M5-22 CONDITIONAL (fully MET when docs/screens/settings-api-mcp.png committed) |
| Priority | P1 — Phase 5; stakeholder request; closes M4-HARD promise |
| Owner | backend-engineer (GET /mcp/info); frontend-engineer (SectionApiMcp replacement) |
| Source | Stakeholder request 2026-06-29 (Emanuele Chiummo); Amendment A1 |

**Scope:**
Replace the `ComingSoonBadge` stub in `SectionApiMcp` (SettingsPanel.tsx) with a real
panel that fetches `GET /mcp/info` and displays: (1) connection details — transport type,
entry-point command (`python -m app.mcp.server`), copy-to-clipboard Claude Desktop JSON
config snippet; (2) tools list — all tools from the real FastMCP registry (currently
search_wiki, write_page, get_page, list_pages), each with name and truncated description.

The backend adds a new read-only `GET /mcp/info` endpoint that derives its data from the
live `app.mcp.server.mcp` FastMCP instance — not hardcoded strings. The endpoint requires
no DB query and no live MCP transport call. It reflects the tool registry at import time.

This feature SURFACES the existing MCP server built in M2. It does NOT add new MCP tools,
does not invoke tools from the UI, does not add configuration-write capability. I9
(do not reinvent) is the primary invariant guard.

**Key invariants:** I9 (no reinvention — surface existing server only), I6 (no hardcoded
server name, command, or tool list in handler or JSX).

**Phase placement:** Phase 5 (new phase after F13/Phase 4). Can be deferred to M6 by PM
decision at Phase 4 sign-off only; deferral is not the default.

**Gate chain:** functional-analyst → architect → engineer → QA → tech-writer → PM (same
chain as all other work items per CLAUDE.md §8 DoD).

**Acceptance criteria:** docs/sprints/v0.5-scope.md §4 AC-F1-MCP-UI-1..10

**EC gate:** EC-M5-22 (§8); EC-M5-HCP-7 (§9)

---

### M4 carry-forward nits (disposal required by Phase 1 exit)

| Tracking ID | Item | Status | Owner |
|-------------|------|--------|-------|
| CF-HARD-1 | Recapture shell-collapsed-panel.png | backlog | qa-test-engineer |
| NB-7 | graph-recompute.mmd hit-path cosmetic | backlog | tech-writer |
| NB-8 | component.mmd store label (store/graphStore.ts) | backlog | tech-writer |
| NB-9 | CI branch filter — add sprint/v0.3, sprint/v0.4 | backlog | devops-engineer |
| AC-HARD-SET-5 | Settings left-nav arrow-key handler | backlog | frontend-engineer |
| GAP-HARD-1 / GAP-HARD-5 | TRACEABILITY.md stale PENDING rows | backlog | functional-analyst |

These items do NOT hold M5 sign-off but must be disposed of (done or formally deferred
with PM approval) before Phase 1 exits.

---

**Acceptance criteria for all M5 items:** docs/sprints/v0.5-scope.md §4
**Phase plan (4 phases with independent gate chains):** docs/sprints/v0.5-scope.md §7
**Exit criteria (EC-M5-1..22):** docs/sprints/v0.5-scope.md §8
**Human checkpoint (EC-M5-HCP, 7 conditions):** docs/sprints/v0.5-scope.md §9

---

### Phase 2 Gap Register (v0.5)

| Gap ID | Feature | Issue | Resolution |
|--------|---------|-------|-----------|
| GAP-v0.5-P2-1 | F10 (AC-F10-2 / I7) | No dedicated unit test asserts that `logger.warning(...)` fires when `total_cost_usd > 1.00`. Constant `COST_ANOMALY_THRESHOLD_USD = 1.00` and code path are present and code-reviewed. | Non-blocking. The anomaly is an observability feature, not a safety bound — it cannot cause runaway execution. A `caplog` fixture test may be added in Phase 3 QA cycle if capacity permits. This gap does not hold Phase 3 or M5 sign-off. |
| FU-P2-1 | F10 / D5 | D5 Deep Search screenshots (`deep-search-running.png`, `deep-search-complete.png`) pending live stack. | Fold into EC-M5-HCP-3 capture: run `make screenshots` at the same time as Emanuele's EC-M5-HCP-3 browser verification. Owner: qa-test-engineer. |
| FU-P2-2 | F10 / D6a | No-provider degraded path (zero LLM calls → low-quality stub) not yet documented in USER.md. | Add to USER.md F10 / Deep Search section: "Configure a provider before running Deep Research; an unconfigured vault produces a raw-fetch stub." Owner: tech-writer, Phase 4/5 docs gate. |

---

### Phase 3 Gap Register (v0.5)

| Gap ID | Feature | Issue | Resolution |
|--------|---------|-------|-----------|
| FU-P3-1 | F9 + F12 / D5 | `docs/screens/review-queue.png` + `docs/screens/upload-multiformat.png` pending live stack. | Fold into EC-M5-HCP browser session: run `make screenshots` when Emanuele verifies HCP-4 (review queue) and HCP-5 (PDF upload). Owner: qa-test-engineer. |
| FU-P3-2 | F9 / D6a | CLI delegated ingest path does not enqueue review items (ADR-0025 §7, Risk 1). Conscious design gap. | Add note to USER.md F9 / Review Queue section. Owner: tech-writer, Phase 4/5 docs gate. Post-M5 / v0.6 consideration for full CLI path enqueue. |
| FU-P3-3 | F12 / extract.py | images/AV extraction is a placeholder one-liner. Out-of-M5 scope per ADR-0025 §4.3. | No action before M5 close. Future ADR required if OCR/AV extraction is added. |

---

### Phase 4 Gap Register (v0.5)

| Gap ID | Feature | Issue | Resolution |
|--------|---------|-------|-----------|
| FU-P4-1 | F13 / D5 | `docs/screens/cascade-delete-preview.png` + `docs/screens/cascade-delete-confirm.png` pending live stack. | Fold into EC-M5-HCP-6 browser session: run `make screenshots` when Emanuele verifies EC-M5-HCP-6 (delete a page, confirm tree and index.md update). Owner: qa-test-engineer. |
| FU-P4-2 | F13 / cascade_delete.py | Advisory test gap: no dedicated test asserts that when (a) and (b) return empty, method (c) does fire (the `test_method_c_fires_when_links_table_empty` case). Happy-path skip proven by T-CD-026; fallback path exercised indirectly. | Non-blocking. Add as post-M5 or Phase 5 QA cycle if capacity permits. |
| FU-P4-3 | F13 / ADR-0026 | Architect C1: `cascade_delete` opens separate `get_session()` per step (5 transactions). A crash mid-run leaves a partially-applied state (transiently inconsistent but idempotent on retry). ADR §8 Consequences note missing this non-atomic, idempotent-on-retry semantics. | Document Consequences note in ADR-0026 OR wrap DB mutations in single session. Non-blocking for M5 ship. Owner: tech-writer (ADR amendment) or backend-engineer. Pre-M6. |
| FU-P4-4 | F13 / cascade_delete.py | Architect C2: `files_written == len(wikilinks_to_rewrite)` equality contract in AC-F13-4a. Code `continue`s without incrementing when body is unchanged or write fails. Pre-plan `_count_body_occurrences > 0` guard should make `continue` dead on the happy path — confirm or relax AC assertion to `<=`. | Confirm with QA and backend-engineer. Align code or AC so assertion is deterministic. Non-blocking for M5 ship. Pre-M6. |
| FU-P4-5 | F13 / cascade_delete.py | Architect C4: method (b) reads full `links` table when `already_found` is empty (`or_(True)` branch); method (c) enumerates all live wiki pages. Advisory perf concern on large vaults. Bounded by `CASCADE_FULLTEXT_MAX_FILES`. | Post-M5 perf follow-up. Consider scoping (b) or pushing slug comparison to SQL. Non-blocking for M5 ship. |
| FU-P4-6 | F10/F9 / D6a | FU-P2-2: USER.md no-provider Deep Research note; FU-P3-2: USER.md F9 CLI path enqueue note. Both pending Phase 5 docs gate. | Owner: tech-writer. Capture at Phase 5 (F1-MCP-UI) docs gate. |

---

## Sprint 6 — v0.6 — M6 "Shippable"

**Sprint status: CODE-COMPLETE — PENDING EC-M6-HCP (human checkpoint required before v1.0.0 tag)**
Branch: `sprint/v0.6`. Status tracker: `docs/sprints/v0.6-m6-status.md`. Scope: `docs/sprints/v0.6-scope.md`.
Baseline at cut: backend 926 pytest / frontend 621 vitest. Now: **backend 968 / frontend 711**, all green.

| Feature ID | Description | Status | Key commits |
|------------|-------------|--------|-------------|
| **K2** (lint backend) | `ops/lint.py` bounded HITL loop + 6 `/lint/*` endpoints + migration 0014 + ADR-0037 + `lint-fix.mmd` | **done** — pytest 946 green; I6/I7/I1 static tests | `745600f` |
| **K2** (lint UI) | `LintView` + `lintStore` + `lintClient` + nav slot + i18n EN/IT | **done** — vitest 680 green; tsc+eslint clean | `ac90d35` |
| **F11** (web clipper) | MV3 `extension/` (Readability+Turndown) + hardened `POST /clip` + ADR-0038 + `web-clip.mmd` | **done** — pytest 968 green; 22 security tests (401/403/413/safe-join) | `7c91354` |
| **F15** (CI gate) | Tests run in CI; `sprint/**` trigger; pinned ruff/black; mmdc check | **done** — ruff/black clean tree-wide; pins 0.15.20/24.1.0 | `8bbd2a9` |
| **F15** (PWA) | Web manifest + offline app-shell service worker (API NetworkOnly) | **done** — vite build emits sw.js; vitest 711 green | `cc365f6` |
| **F15** (Tauri v2) | `src-tauri/` scaffold + tag-only multi-OS CI build + ADR-0039 | **done** — cargo resolves; config valid; native build CI-only | `f68b2ad` |
| **F2** (purpose.md context) | `vault/purpose.md` injected into ingest prompts (`orchestrator.py:961`) and chat context (`context.py:86`) | **done** (pre-existing, verified) — `test_chat::test_includes_purpose_and_overview` green | (pre-existing) |
| **D1–D7** (docs gate) | All docs artifacts complete and consistent | **PASS-PENDING-D5/HCP** — D5 screenshots deferred to live-stack HCP session | this gate |
| **MkDocs** | Optional docs site | **not started** — explicitly optional per scope §2; no code impact |  |

---

## Sprint 7 — v0.7 — M7 "Core completeness & daily UX"

**Sprint status: DONE — v0.7.0 shipped (GitHub release confirmed by Emanuele)**
Branch: `sprint/v0.7`. Scope: `docs/sprints/SPRINT-v0.7-SCOPE.md`.

| Feature ID | Item ID | Description | Status |
|------------|---------|-------------|--------|
| F1, K1 | R7-1 | Scenario templates (5 vault presets) | done |
| F1 | R7-2 | New page from UI | done |
| F6 | R7-3 | Rename conversations + conversation search/filter | done |
| F1 | R7-4 | Unsaved-changes indicator + navigation guard | done |
| F9, F10 | R7-5 | Review search_queries JSONB populated at proposal time | done |
| F3, F12 | R7-6 | Recursive folder import + folderContext hint | done |
| F3, F12 | R7-7 | ServiceNow scheduler (local-folder watch) | done |
| F5 | R7-8 | Retrieval scope: citations from wiki/ only | done |
| F7 | R7-9 | ThinkBlock streaming preview (rolling fade) | done |
| F17, F3, F10 | R7-10 | Multi-provider routing verifications | done |
| F1 | R7-11 | Bulk ops on sources | done |
| F1 | R7-12 | Cancel-all confirmation dialog | done |
| docs | R7-13 | Refresh SYNAPSE-VS-LLMWIKI-PARITY.md | done |
| F15 | AUTO-UPDATE | Desktop auto-update (pulled forward from R10-4) | done |

---

## Sprint 8 — v0.8 — M8 "Content power"

**Sprint status: IN PROGRESS**
Branch: `sprint/v0.8` (cut from sprint/v0.7 after v0.7.0 tag).
Scope: `docs/sprints/SPRINT-v0.8-SCOPE.md`.
Sequencing constraint: R8-1 → R8-2 → R8-3 (extract.py chain); R8-4 → R8-5 backend (main.py chain).

---

### R8-1 — Marker as first-class PDF extractor (pluggable seam + pypdf fallback)

| Field | Value |
|-------|-------|
| Feature ID | F12, F3 |
| Sprint | v0.8 |
| Status | backlog |
| Owner | backend-engineer + devops-engineer |
| Priority | P0 — gates R8-2 and R8-3 |

**Acceptance criteria:** AC-R8-1-1 through AC-R8-1-6 (see SPRINT-v0.8-SCOPE.md §R8-1).
Summary: pluggable `PDF_EXTRACTOR` env var; `POST /convert` microservice in
`tools/marker-converter/service.py`; pypdf-always fallback; ADR-0050; DEPLOY.md updated.

---

### R8-2 — Vision captions for images

| Field | Value |
|-------|-------|
| Feature ID | F12, F17 |
| Sprint | v0.8 |
| Status | blocked — depends on R8-1 merged |
| Owner | ai-agent-engineer + backend-engineer |
| Priority | P0 |

**Acceptance criteria:** AC-R8-2-1 through AC-R8-2-6 (see SPRINT-v0.8-SCOPE.md §R8-2).
Summary: `supports_vision` capability flag; `image_captions` DB table (Alembic migration);
SHA256 cache; `VISION_MAX_IMAGES_PER_RUN` cap; ER diagram regenerated.

---

### R8-3 — Audio/video transcription (local Whisper, opt-in)

| Field | Value |
|-------|-------|
| Feature ID | F12, F3 |
| Sprint | v0.8 |
| Status | blocked — depends on R8-2 merged |
| Owner | backend-engineer + devops-engineer |
| Priority | P1 — opt-in; placeholder fallback exists if deferred |

**Acceptance criteria:** AC-R8-3-1 through AC-R8-3-6 (see SPRINT-v0.8-SCOPE.md §R8-3).
Summary: `AV_TRANSCRIPTION` opt-in flag; `POST /transcribe` microservice in
`tools/whisper-service/`; mlx-whisper on MPS; bounded by duration + file count; DEPLOY.md updated.

---

### R8-4 — Vault export / backup

| Field | Value |
|-------|-------|
| Feature ID | F15, K1 |
| Sprint | v0.8 |
| Status | backlog |
| Owner | backend-engineer |
| Priority | P0 — gates R8-5 backend (main.py sequencing) |

**Acceptance criteria:** AC-R8-4-1 through AC-R8-4-5 (see SPRINT-v0.8-SCOPE.md §R8-4).
Summary: `GET /export` streaming ZIP (500 MB cap, 429 on concurrent); `GET /export/data.json`
full DB dump; export.py router module; USER.md Backup & Restore section.

---

### R8-5 — Search filters and sort (type facet + date sort)

| Field | Value |
|-------|-------|
| Feature ID | F5, F1 |
| Sprint | v0.8 |
| Status | blocked — backend depends on R8-4 merged (main.py); frontend can start in parallel |
| Owner | backend-engineer (GET /search params) + frontend-engineer (facet UI) |
| Priority | P0 |

**Acceptance criteria:** AC-R8-5-1 through AC-R8-5-5 (see SPRINT-v0.8-SCOPE.md §R8-5).
Summary: `type` and `sort` query params on `GET /search`; Qdrant metadata filter + date sort;
facet sidebar with TanStack Virtual; i18n EN/IT; openapi.json regenerated.

---

### R8-6 — Citation click-through audit (wire onCitationClick everywhere)

| Field | Value |
|-------|-------|
| Feature ID | F5, F1 |
| Sprint | v0.8 |
| Status | backlog |
| Owner | frontend-engineer |
| Priority | P0 |

**Acceptance criteria:** AC-R8-6-1 through AC-R8-6-4 (see SPRINT-v0.8-SCOPE.md §R8-6).
Summary: grep audit of all MarkdownView/CitationBadge uses; onCitationClick wired in all
contexts; vitest for each context; no new `any` escapes.

---

### R8-7 — Chrome clipper packaging and CI artifact

| Field | Value |
|-------|-------|
| Feature ID | F11 |
| Sprint | v0.8 |
| Status | backlog |
| Owner | devops-engineer (CI zip) + frontend-engineer (extension audit + doc) |
| Priority | P0 — CI artifact required for EC-M8-9 |

**Acceptance criteria:** AC-R8-7-1 through AC-R8-7-4 (see SPRINT-v0.8-SCOPE.md §R8-7).
Summary: host_permissions audit + fix; icon file verification; CI job produces versioned
`synapse-clipper-{version}.zip` artifact; USER.md Chrome Clipper section; manifest version
bumped to 0.8.0. Chrome Web Store publication is explicitly OUT OF SCOPE.

---

---

## Sprint 9 — v0.9 — M9 "Trust & observability"

**Sprint status: IN PROGRESS**
Branch: `sprint/v0.9` (cut from sprint/v0.8 after v0.8.0 tag).
Scope: `docs/sprints/SPRINT-v0.9-SCOPE.md`.
Prerequisite: EC-M8-1..EC-M8-HCP confirmed by Emanuele.
Sequencing constraints:
- W0 IN FLIGHT → UXB-2 (ReviewQueueView.tsx) → R9-3 frontend → R9-4 frontend
- R9-1 → R9-2 (main.py chain)
- R9-3 → R9-4 (review.py + orchestrator.py chain)
- R9-5, R9-6 can proceed in parallel with R9-1/R9-2

---

### W0 — UX quick wins (IN FLIGHT)

| Field | Value |
|-------|-------|
| Feature ID | F1, F16 |
| Sprint | v0.9 |
| Status | in-progress (IN FLIGHT at scope lock) |
| Owner | frontend-engineer |
| Priority | P0 — gates UXB-2 |

**Acceptance criteria:** AC-W0-1 through AC-W0-5 (see SPRINT-v0.9-SCOPE.md §W0).
Summary: 10 audit quick wins (UXA-03/05/06/07/14/16/17/18/21/23) committed in one PR;
color-mix white regex zero hits; `activity.moreFailedTasks` i18n key; D5 screenshot
for ingest-zero-pages.

---

### UXB-1 — Conversation auto-titles + list preview snippet

| Field | Value |
|-------|-------|
| Feature ID | F6, F16 |
| Sprint | v0.9 |
| Status | backlog |
| Owner | backend-engineer (title endpoint) + frontend-engineer (ConversationList preview) |
| Priority | P0 |

**Acceptance criteria:** AC-UXB1-1 through AC-UXB1-6 (see SPRINT-v0.9-SCOPE.md §UXB-1).
Summary: `POST /conversations/{id}/generate-title` (max 60 tokens, timestamp fallback);
frontend calls after stream end; ConversationList preview line 80 chars, 9px dim;
Vitest snapshots; openapi.json updated.

---

### UXB-2 — Button / input design-system consolidation

| Field | Value |
|-------|-------|
| Feature ID | F1, F16 |
| Sprint | v0.9 |
| Status | blocked — depends on W0 merged |
| Owner | frontend-engineer |
| Priority | P0 — gates R9-3 and R9-4 frontend (ReviewQueueView.tsx) |

**Acceptance criteria:** AC-UXB2-1 through AC-UXB2-7 (see SPRINT-v0.9-SCOPE.md §UXB-2).
Summary: `components.css` created; `.syn-button--ghost` / `.syn-meta-row` / `.syn-card-row`
/ `.syn-role-label` classes; `--syn-bg-card` token added; @keyframes moved to theme.css;
`color-mix.*white` zero hits tree-wide; Playwright visual regression snapshots to docs/screens/.

---

### R9-1 — Cost dashboard

| Field | Value |
|-------|-------|
| Feature ID | F17, F16 |
| Sprint | v0.9 |
| Status | backlog |
| Owner | backend-engineer (aggregation endpoint) + frontend-engineer (Settings section) |
| Priority | P0 — gates R9-2 (main.py sequencing) |

**Acceptance criteria:** AC-R9-1-1 through AC-R9-1-6 (see SPRINT-v0.9-SCOPE.md §R9-1).
Summary: `GET /costs/summary` with by_provider/by_operation/by_day/monthly_total/threshold_alert;
`COST_ALERT_THRESHOLD_USD` env var; Settings "Cost & Usage" section; i18n EN/IT; openapi.json updated.

---

### R9-2 — Metrics / health endpoint

| Field | Value |
|-------|-------|
| Feature ID | F16 |
| Sprint | v0.9 |
| Status | blocked — depends on R9-1 merged |
| Owner | backend-engineer (endpoint) + frontend-engineer (Header indicator) |
| Priority | P0 |

**Acceptance criteria:** AC-R9-2-1 through AC-R9-2-5 (see SPRINT-v0.9-SCOPE.md §R9-2).
Summary: `GET /health/detailed` (watcher/scheduler/queue/db/qdrant liveness + last 5 errors);
Header chip amber/red indicators; 30s polling via Zustand interval; `HEALTH_POLL_MS` env var;
openapi.json updated.

---

### R9-3 — purpose.md suggestions (scope drift detection)

| Field | Value |
|-------|-------|
| Feature ID | F2, F9 |
| Sprint | v0.9 |
| Status | blocked — depends on UXB-2 (ReviewQueueView.tsx) merged |
| Owner | ai-agent-engineer (drift detection + provider call) + backend-engineer (ReviewItem type, API, review.py) |
| Priority | P0 — gates R9-4 (review.py chain) |

**Acceptance criteria:** AC-R9-3-1 through AC-R9-3-5 (see SPRINT-v0.9-SCOPE.md §R9-3).
Summary: `purpose-suggestion` ReviewItemType; `generate_purpose_suggestion()` in review.py
(bounded: max_tokens=300, no retry, cost logged); post-ingest orchestrator hook; ReviewQueue
card with "Apply to purpose.md" action; sequences/ addendum.

---

### R9-4 — schema.md co-evolution (schema-suggestion ReviewItem)

| Field | Value |
|-------|-------|
| Feature ID | F16, K6, F9 |
| Sprint | v0.9 |
| Status | blocked — depends on R9-3 merged |
| Owner | ai-agent-engineer + backend-engineer |
| Priority | P1 — DE-SCOPE PRIORITY 1 (first to cut if sprint runs over) |

**Acceptance criteria:** AC-R9-4-1 through AC-R9-4-6 (see SPRINT-v0.9-SCOPE.md §R9-4).
Summary: `schema-suggestion` ReviewItemType; `generate_schema_suggestion()` in review.py
(bounded: max_tokens=400, no retry); orchestrator hook after purpose-check; ReviewQueue card
with "Apply to schema.md" action; sequences/ addendum.
De-scope note: if cut, no placeholder remains in orchestrator.py; clean exit; deferred to v1.0.

---

### R9-5 — Graph drill-down (community panel + edge tooltip + cohesion score)

| Field | Value |
|-------|-------|
| Feature ID | F4, F1 |
| Sprint | v0.9 |
| Status | backlog |
| Owner | frontend-engineer (community panel + edge tooltip UI) + backend-engineer (community + edge endpoints) |
| Priority | P0 |

**Acceptance criteria:** AC-R9-5-1 through AC-R9-5-6 (see SPRINT-v0.9-SCOPE.md §R9-5).
Summary: `GET /graph/community/{id}` (members + cohesion_score + cohesion_warning);
`GET /graph/edge/{src}/{dst}` (4-signal breakdown); community side panel with amber
low-cohesion banner; edge weight tooltip with 150ms debounce; `GRAPH_COHESION_WARN`
env var; D5 screenshots graph-community-panel.png + graph-edge-tooltip.png.

---

### R9-6 — Playwright E2E suite (happy-path + D5 screenshot refresh)

| Field | Value |
|-------|-------|
| Feature ID | F15, F1 |
| Sprint | v0.9 |
| Status | backlog |
| Owner | qa-test-engineer |
| Priority | P0 |

**Acceptance criteria:** AC-R9-6-1 through AC-R9-6-6 (see SPRINT-v0.9-SCOPE.md §R9-6).
Summary: 7 happy-path E2E specs (Connect/Ingest/Search/Chat/Review/Graph/Settings) using
`SYNAPSE_FRONTEND_URL`; all pass in CI; D5 screenshots auto-committed; auto-title
regression test (UXB-1 coverage); playwright-report/ gitignored.

---

---

## Sprint 10 — v1.0 — M10 "Distribution & multi-user"

**Sprint status: IN PROGRESS — scope locked 2026-07-03**
Scope lock: docs/sprints/SPRINT-v1.0-SCOPE.md
Branch: sprint/v1.0 (cut after v0.9.0 tag)
Prerequisite: M9 exit criteria met (EC-M9-1..EC-M9-HCP confirmed by Emanuele).
Duration: 3–4 weeks.
QA gate rule: QA-test-engineer runs ci.yml exact commands verbatim (documented in
SPRINT-v1.0-SCOPE.md §0 Rule 2) — no proxy commands.

---

### ADR-0052 — Auth token model (prerequisite gate)

| Field | Value |
|-------|-------|
| Feature ID | F16, F15 |
| Sprint | v1.0 |
| Status | backlog — MUST be accepted before any R10-1 code is written |
| Owner | ai-agent-engineer (authors ADR); solution-architect (accepts) |
| Priority | P0 — hard blocker for R10-1 and R10-2 |

**Scope:** `docs/adr/ADR-0052-auth-token-model.md` — documents: why shared token not
OIDC, why OIDC is deferred to post-1.0, single-vault scope for 1.0, WebSocket auth
approach, HTTPS responsibility model, excluded endpoints (`/health`, `/status`,
`/health/detailed`), token rotation procedure.

**Acceptance criteria:** AC-R10-1-0 (see SPRINT-v1.0-SCOPE.md §R10-1). ADR committed and
in Accepted status before any backend auth code is written. Solution-architect sign-off
is the gate.

---

### R10-1 — Authentication middleware (shared Bearer token)

| Field | Value |
|-------|-------|
| Feature ID | F16, F15 |
| Sprint | v1.0 |
| Status | blocked — depends on ADR-0052 accepted |
| Owner | backend-engineer (implementation); ai-agent-engineer (ADR lead) |
| Priority | P0 — gates R10-2 |

**Scope:** `SYNAPSE_AUTH_TOKEN` env var; FastAPI middleware or global `Depends(verify_token)`;
all routes enforced except `GET /health`, `GET /status`, `GET /health/detailed`; WebSocket
upgrade path protected; 401 body standardised; `backend/app/auth.py` extracted module;
`BearerAuth` security scheme in OpenAPI spec; `docs/DEPLOY.md` Security section.

**Acceptance criteria:** AC-R10-1-1 through AC-R10-1-6 (see SPRINT-v1.0-SCOPE.md §R10-1).
Summary: backward-compat (empty = auth disabled); 3 excluded endpoints verified; WebSocket
auth path chosen and tested; mypy strict on auth.py; openapi.json updated with security scheme.

---

### R10-2 — Auth UX (ConnectScreen token field + Settings rotation)

| Field | Value |
|-------|-------|
| Feature ID | F1, F16, F15 |
| Sprint | v1.0 |
| Status | blocked — depends on R10-1 merged |
| Owner | frontend-engineer |
| Priority | P0 |

**Scope:** Single API client module injects `Authorization: Bearer` on every request;
per-server token stored in `localStorage`; 401 response clears token + shows ConnectScreen
with token field (password type + show/hide toggle); Settings > Security section for token
rotation (client-side only); EN/IT i18n keys; D5 screenshot `connect-screen-auth.png`;
coverage of health polling (R9-2) and WebSocket upgrade path.

**Acceptance criteria:** AC-R10-2-1 through AC-R10-2-8 (see SPRINT-v1.0-SCOPE.md §R10-2).

---

### R10-3 — Code signing guide in DEPLOY.md

| Field | Value |
|-------|-------|
| Feature ID | F15, D6b |
| Sprint | v1.0 |
| Status | backlog |
| Owner | tech-writer + devops-engineer |
| Priority | P1 — docs-as-DoD; no CI implementation required |

**Scope:** `docs/DEPLOY.md` "Code Signing" section covering macOS (Apple Developer, required
GitHub Actions secrets, tauri.conf.json, notarization via xcrun notarytool) and Windows (EV
cert, required secrets, tauri.conf.json). Also documents current unsigned-build workarounds
(macOS xattr, Windows SmartScreen bypass) with security model explanation.

**Acceptance criteria:** AC-R10-3-1 through AC-R10-3-2 (see SPRINT-v1.0-SCOPE.md §R10-3).
No code changes. Tech-writer + devops-engineer review and approval required.

---

### R10-4 — Desktop auto-update verification

| Field | Value |
|-------|-------|
| Feature ID | F15 |
| Sprint | v1.0 |
| Status | done — shipped v0.8.1; verification step only at v1.0.0 release |
| Owner | Emanuele (human verification) |
| Priority | P1 — release checklist item |

**Scope:** Verify v1.0.0 update chain: tag v1.0.0 → build → running v0.9.0 receives
update prompt → updates successfully. Manual verification by Emanuele.

**Acceptance criteria:** AC-R10-4-verify (see SPRINT-v1.0-SCOPE.md §R10-4). Result noted in
release checklist. No code work.

---

### R10-5 — Mobile/PWA polish

| Field | Value |
|-------|-------|
| Feature ID | F15, F1 |
| Sprint | v1.0 |
| Status | backlog |
| Owner | frontend-engineer |
| Priority | P1 |

**Scope (tightly bounded — 3 mechanical changes only):** (1) `@media (max-width: 767px)`
CSS block: nav rail collapses (bottom tab bar or hamburger drawer) + panels stack vertically;
(2) touch targets ≥44×44px for 5 critical interactive elements at 375px viewport (Playwright
verified); (3) sigma graph canvas `touch-action: none` + pinch-zoom sanity check (Playwright
or manual). D5 screenshot `graph-mobile.png` at 375px. USER.md "Mobile / PWA" section.

**Acceptance criteria:** AC-R10-5-1 through AC-R10-5-5 (see SPRINT-v1.0-SCOPE.md §R10-5).

---

### R10-6 — MkDocs Material docs site

| Field | Value |
|-------|-------|
| Feature ID | D1, D6, D7 (docs published), F15 |
| Sprint | v1.0 |
| Status | backlog |
| Owner | tech-writer (content + nav); devops-engineer (CI job + Makefile) |
| Priority | P1 — I8 culmination |

**Scope:** `mkdocs.yml` at repo root (Material theme); nav covering USER.md, DEPLOY.md,
architecture diagrams, ADR index, API reference; `make docs-serve` + `make docs-build`
targets; `docs-site` CI job in ci.yml running `mkdocs build --strict` on every push
(no auto-deploy — Pages enablement requires owner action, documented); `docs/adr/index.md`
listing all ADRs; Mermaid rendering via pymdownx.superfences; USER.md and DEPLOY.md
completed to v1.0 accuracy.

**Acceptance criteria:** AC-R10-6-1 through AC-R10-6-6 (see SPRINT-v1.0-SCOPE.md §R10-6).

---

### QA-v0.9-leftovers — E2E test fixes carried from v0.9

| Field | Value |
|-------|-------|
| Feature ID | F15 (QA) |
| Sprint | v1.0 |
| Status | backlog — Wave 1 start (no dependencies) |
| Owner | qa-test-engineer |
| Priority | P0 — must fix before QA full pass |

**Item LO-1 (E2E Cost testid/locator gap):** Add `data-testid="settings-cost-section"` and
`data-testid="cost-monthly-total"` to Settings Cost section component; update Playwright spec
to use `getByTestId()`; spec must pass against running backend with fixture cost run.

**Item LO-2 (EdgeDetail `computed_at` field):** Align `GET /graph/edge/{src_id}/{dst_id}`
Pydantic response model with OpenAPI schema (either add `computed_at: datetime | None`
populated from `edges.updated_at`, or remove from both response model and schema);
regenerate `docs/api/openapi.json`; pytest asserts response matches schema via
`jsonschema.validate`.

**Acceptance criteria:** AC-QA-LO-1 and AC-QA-LO-2 (see SPRINT-v1.0-SCOPE.md §QA-v0.9-leftovers).

---

### UX-v1.0 — P2/P3 quick items (3 items, PM-selected)

| Field | Value |
|-------|-------|
| Feature ID | F1, F16 |
| Sprint | v1.0 |
| Status | backlog — after R10-1 merged |
| Owner | frontend-engineer |
| Priority | P2 — de-scope as group if sprint runs over (de-scope priority 1) |

**Selected items (PM judgment — cap at 3):**
- UX-v1.0-A (UXA-08): Role labels in MessageList.tsx — 9px, `var(--syn-text-dim)`, no
  uppercase; left-border stripe per turn.
- UX-v1.0-B (UXA-15): ProviderSelector.tsx ARIA fix — `role="dialog" aria-modal="true"`;
  remove incorrect `role="listbox"` from inner list.
- UX-v1.0-C (UXA-18): ItemTypeBadge `item_type` normalisation — replace underscores with
  hyphens before `t()` lookup.

**Acceptance criteria:** AC-UX-A, AC-UX-B, AC-UX-C (see SPRINT-v1.0-SCOPE.md §UX-v1.0).
De-scope trigger: if sprint falls behind after Wave 2, drop all three as a unit — no
selective partial de-scope.

---

## Out of scope — never (invariant violations)

These items must NEVER be implemented as described; any proposal requires
escalation to solution-architect:

| What | Why blocked |
|------|-------------|
| Full-rescan on startup | Violates I1 |
| Force-directed layout on UI main thread | Violates I2 |
| Per-token markdown/LaTeX parse in chat | Violates I3 |
| ProseMirror / WYSIWYG editor | Violates I4 |
| Hardcoded inference provider | Violates I6 |
| Unbounded ingest/research loops | Violates I7 |
| Skipping D-artifacts at sprint end | Violates I8 |
| Introducing Tavily or alternative search | Violates I9 |
| Reinventing embedding service (run bge-m3 locally) | Violates I9 |
