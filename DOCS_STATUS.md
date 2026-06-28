# DOCS_STATUS — Sprint v0.2 (M2) Documentation Gate

> Tech-writer sign-off for EC-M2-16 (v0.2-scope §7 sign-off register).
> Generated: 2026-06-28
> Author: tech-writer agent
> Supersedes: DOCS_STATUS.md (v0.1 gate)

This file is the artifact the milestone gate reads. ALL UP-TO-DATE means the docs gate
passes and sprint/v0.2 may merge. NOT UP-TO-DATE means the gate blocks until resolved.

---

## 1. Per-artifact status table

| Artifact | Required v0.2? | Status | Drift result | Notes |
|----------|----------------|--------|--------------|-------|
| **D1 — C4 context** (`docs/architecture/context.mmd`) | YES (updated) | UP-TO-DATE | No drift. Line 1 = `C4Context`. Heading comment added this gate. | Shows Synapse + InferenceProvider relationship to Ollama and Anthropic API. Scope-accurate: no UI (v0.4), no SearXNG (v0.5), no graph (v0.3). |
| **D1 — C4 container** (`docs/architecture/container.mmd`) | YES (updated) | UP-TO-DATE | No drift. Line 1 = `C4Container`. Heading comment added this gate. | Provider layer container + MCP server container added. All 5 DB tables listed. 202 IngestTriggerResponse noted on /ingest/trigger. |
| **D1 — C4 component** (`docs/architecture/component.mmd`) | YES (new) | UP-TO-DATE | No drift. Line 1 = `C4Component`. Heading comment added this gate. | New in v0.2. Shows all 3 provider backends, ConfigResolver, WikiPage validator, K5 wikilink parser, K3 index writer, MCP server with 4 tools, Postgres component. |
| **D2 — ER diagram** (`docs/er/schema.mmd`) | YES (updated) | UP-TO-DATE | ZERO DRIFT. Regenerated via `python backend/scripts/generate_er.py`; `git diff` shows no change to committed file. Sanity assertions passed. | All 5 tables present: PAGES, VAULT_STATE, PROVIDER_CONFIG, INGEST_RUNS, LINKS. No api_key column in PROVIDER_CONFIG. INGEST_RUNS has total_cost_usd, converged, cost_anomaly. LINKS has source_page_id, target_title, target_page_id, dangling. |
| **D3 — ingest-routing.mmd** (`docs/sequences/ingest-routing.mmd`) | YES (new) | UP-TO-DATE | No drift. Line 1 = `sequenceDiagram`. Heading comment added this gate. | Both branches shown: `supports_agentic_loop == True` (CLI delegated) and `== False` (orchestrated). Routing branch reads only `supports_agentic_loop` — never isinstance/type. I7 cost log + anomaly check shown. |
| **D3 — ingest-loop.mmd** (`docs/sequences/ingest-loop.mmd`) | YES (new) | UP-TO-DATE | No drift. Line 1 = `sequenceDiagram`. Heading comment added this gate. | analyze() called ONCE (AQ-v0.2-1). generate()/validate() loop with max_iter + token_budget dual bounds annotated (I7). whole-batch retry with augmented context. converged path and non-converged path both shown. ingest_runs write at end. |
| **D4 — openapi.json** (`docs/api/openapi.json`) | YES (updated) | UP-TO-DATE | ZERO DRIFT. Regenerated via `python backend/scripts/generate_openapi.py`; `git diff` shows no change. | /provider/config GET + POST present. /provider/config/{id} DELETE present. POST /ingest/trigger 202 response is now a typed `$ref` to IngestTriggerResponse (task_id in schema). v0.1 carry-forward item RESOLVED. |
| **D4 — mcp-tools.json** (`docs/api/mcp-tools.json`) | YES (new) | UP-TO-DATE | No drift. | All 4 tools documented with full input/output schemas: search_wiki, write_page, get_page, list_pages. Transport: stdio (v0.2, ADR-0010). Not-RAG note explicit. write_page invariants (I1, I5) documented. |
| **D5 — UI screenshots** (`docs/screens/`) | NO | N/A-THIS-SPRINT | — | No UI in v0.2. First required sprint: v0.3. |
| **D6a — USER.md** | NO | N/A-THIS-SPRINT | — | First required sprint: v0.4 (draft). |
| **D6b — DEPLOY.md** (`docs/DEPLOY.md`) | NO | DRAFT/EARLY | Not required this sprint. | Remains DRAFT from v0.1. Not gated this sprint. |
| **D7 — ADR-0007** (`docs/adr/0007-inference-provider-abc.md`) | YES (new) | UP-TO-DATE | File present and correct. | InferenceProvider ABC rationale, capability-aware routing, analyze-once retry strategy, validator contract (AQ-v0.2-1, AQ-v0.2-7). Status: Accepted. |
| **D7 — ADR-0008** (`docs/adr/0008-provider-config-schema.md`) | YES (new) | UP-TO-DATE | File present and correct. | provider_config + ingest_runs + links schema, secrets-via-env, scope model with operation column (AQ-v0.2-2, AQ-v0.2-5). Status: Accepted. |
| **D7 — ADR-0009** (`docs/adr/0009-bounded-loop-defaults.md`) | YES (new) | UP-TO-DATE | File present and correct. | max_iter=3, token_budget 60k/100k, Usage normalization per backend, CLI=$0.00 convention, $1 anomaly inline (AQ-v0.2-4, AQ-v0.2-8). Status: Accepted. |
| **D7 — ADR-0010** (`docs/adr/0010-mcp-transport-and-write-path.md`) | YES (additional) | UP-TO-DATE | File present and correct. | stdio transport v0.2 (HTTP deferred v0.4), write_page shares persist primitives (I1, I5, I9) (AQ-v0.2-6). Status: Accepted. |
| **D7 — ADR-0011** (`docs/adr/0011-ingest-contract-schemas.md`) | YES (additional) | UP-TO-DATE | File present and correct. | Locks Analysis/WikiPage/Message/ProviderCapabilities schemas in schemas.py (AQ-v0.2-3). Status: Accepted. |
| **D7 — ADR README index** (`docs/adr/README.md`) | YES | UP-TO-DATE | All 11 ADRs (0001–0011) indexed. | One-line summaries for all 5 v0.2 ADRs. Sprint column present. |

---

## 2. D2 ER completeness detail (I8 gate)

Regenerated from `backend/app/models.py` on 2026-06-28. `git diff --exit-code docs/er/schema.mmd` → ZERO DRIFT.

**5-table confirmation:**

| Table | ER name | Present | Key v0.2 columns confirmed |
|-------|---------|---------|---------------------------|
| `pages` | PAGES | YES | id (PK), vault_id, file_path, title, type, sources (jsonb), content_hash, source_mtime_ns, qdrant_point_id, deleted_at, created_at, updated_at |
| `vault_state` | VAULT_STATE | YES | id (PK), vault_id, data_version, updated_at |
| `provider_config` | PROVIDER_CONFIG | YES | id (PK), scope, operation, vault_id, provider_type, model_id, base_url, max_iter, token_budget, is_fallback, created_at, updated_at. **No api_key column** (§12 / ADR-0008 §3). |
| `ingest_runs` | INGEST_RUNS | YES | id (PK), vault_id, page_id (FK), provider_name, provider_type, model_id, route, max_iter_used, total_tokens, total_cost_usd (decimal), converged, cost_anomaly, started_at, finished_at |
| `links` | LINKS | YES | id (PK), source_page_id (FK), target_title, target_page_id (FK, nullable), alias, dangling, created_at |

**I8 verdict: ER matches live SQLAlchemy schema. Zero drift.**

---

## 3. D4 OpenAPI completeness detail

Generated from `backend/app/main.py` via FastAPI's `.openapi()`. `git diff --exit-code docs/api/openapi.json` → ZERO DRIFT.

| Endpoint | Method | Expected status | Schema | Present |
|----------|--------|-----------------|--------|---------|
| `/status` | GET | 200 | `StatusResponse` | YES |
| `/pages` | GET | 200 | `PageListResponse` | YES |
| `/pages/{page_id}` | GET | 200 | `PageResponse` | YES |
| `/ingest/trigger` | POST | 202 | `IngestTriggerResponse` (`$ref` — typed, task_id in schema) | YES — v0.1 carry-forward RESOLVED |
| `/provider/config` | GET | 200 | `ProviderConfigListResponse` | YES |
| `/provider/config` | POST | 201 | `ProviderConfigResponse` | YES |
| `/provider/config/{config_id}` | DELETE | 204 | — | YES (bonus beyond scope requirement) |

`ProviderConfigCreate` and `ProviderConfigResponse` schemas: no `api_key` field present in either.

**Minor cosmetic note (non-blocking):** `info.version` in openapi.json is "0.1.0" and `info.description` still says "walking skeleton (M1)". These are set in `backend/app/main.py` (backend code; tech-writer cannot edit). The routes and schemas are v0.2-accurate. Backend-engineer should update the version string and description for the final v0.2 commit — tracked as a non-blocking polish item.

---

## 4. D3 sequence diagram validity detail

### ingest-routing.mmd

- Line 1: `sequenceDiagram` — PASS
- Both branches rendered: `alt supports_agentic_loop == True (CLI — DELEGATED)` / `else supports_agentic_loop == False (Local/API — ORCHESTRATED)` — PASS
- Routing branch key: reads `ProviderCapabilities{supports_agentic_loop, mode, ...}` output; no isinstance/type/provider_type-string check in routing branch — PASS (I6 compliant)
- I7 annotation: ingest_runs INSERT with cost log + anomaly check at end — PASS
- ConfigResolver resolution order (operation > vault > global) shown — PASS
- CLI path: MCP tools (search_wiki, get_page, write_page) shown; pages_written count returned — PASS

### ingest-loop.mmd

- Line 1: `sequenceDiagram` — PASS
- analyze() called before loop, labeled "ONCE per run (AQ-v0.2-1)" — PASS (I7 and AQ-v0.2-1 correct)
- Loop annotation: `iteration = 1..max_iter (stop when converged OR iteration==max_iter OR tokens>=budget)` — PASS (dual-bound per I7)
- Pre-call token_budget check shown inside loop — PASS
- validate(pages) step with whole-batch retry on failure — PASS (ADR-0007 §5)
- converged=True path: writes pages, persists links (K5), updates Postgres — PASS
- converged=False path: augments retrieval_context, iterates — PASS
- ingest_runs INSERT at end with cost/anomaly — PASS

---

## 5. D1 C4 diagram validity detail

### context.mmd

- Line 1: `C4Context` (no leading HTML comment) — PASS
- Shows Synapse system, Emanuele person, Ollama + Anthropic API as external systems, Qdrant, bge-m3, Vault filesystem — PASS
- v0.2 additions: Synapse description updated to mention capability-aware ingest loop (I6) and bounded I7 — PASS
- No v0.3+ systems drawn (graph, UI, SearXNG) — PASS

### container.mmd

- Line 1: `C4Container` — PASS
- InferenceProvider layer container added with all 3 backends noted — PASS
- MCP server container added (FastMCP, stdio) — PASS
- Postgres description lists all 5 tables by name — PASS
- No api_key mentioned — PASS

### component.mmd

- Line 1: `C4Component` — PASS (new file in v0.2)
- All provider components (OllamaProvider, ApiProvider, CliAgentProvider) shown within InferenceProvider ABC — PASS
- ConfigResolver, WikiPage validator, K5 wikilink parser, K3 index/overview writer shown — PASS
- MCP server with 4 tools shown; write_page annotated with shared primitives (I1, I5) — PASS
- Routing arrows correctly from orch → prov (ABC) → three backends; never isinstance/class reference — PASS

---

## 6. D7 ADR completeness detail

All 5 v0.2-required ADRs and 2 additional ADRs are present. v0.1 ADRs (0001–0006) remain Accepted.

| ADR | Required by | Status | Key resolved questions |
|-----|------------|--------|----------------------|
| 0007 | EC-M2-11 | Accepted 2026-06-28 | AQ-v0.2-1 (analyze-once); AQ-v0.2-7 (validator contract); ABC vs Protocol; routing via capabilities() |
| 0008 | EC-M2-11 | Accepted 2026-06-28 | AQ-v0.2-2 (ingest_runs table); AQ-v0.2-5 (operation column); secrets-via-env; links table in same migration |
| 0009 | EC-M2-11 | Accepted 2026-06-28 | AQ-v0.2-4 (Usage normalization); AQ-v0.2-8 (anomaly WARNING site); max_iter=3; token_budget defaults |
| 0010 | additional | Accepted 2026-06-28 | AQ-v0.2-6 (stdio transport); write_page reuses seam primitives (I1, I5, I9) |
| 0011 | additional | Accepted 2026-06-28 | AQ-v0.2-3 (Pydantic schemas locked in schemas.py) |

ADR README index: all 11 ADRs listed with status, date, sprint, and one-line summary.

---

## 7. Cross-consistency sweep

| Check | Result |
|-------|--------|
| No `api_key` column in ER, OpenAPI schema, C4, or ADRs | PASS — explicitly excluded in ADR-0008 §3, OpenAPI descriptions reference "no api_key field", ER has no such column |
| Routing in sequence diagram uses `supports_agentic_loop` only (not isinstance/type/provider_type string) | PASS — ingest-routing.mmd alt branch reads `supports_agentic_loop == True/False` only |
| ER PROVIDER_CONFIG matches ADR-0008 §2 column table | PASS — all columns match (id, scope, operation, vault_id, provider_type, model_id, base_url, max_iter, token_budget, is_fallback, created_at, updated_at) |
| ER INGEST_RUNS has total_cost_usd, converged, cost_anomaly (I7) | PASS — all three present with correct types (decimal, boolean, boolean) |
| ER LINKS has source_page_id, target_title, target_page_id (nullable), dangling (K5) | PASS — all columns present; target_page_id is FK nullable for v0.3 graph resolution |
| OpenAPI typed 202 response for /ingest/trigger (v0.1 carry-forward) | PASS — resolved: `$ref: IngestTriggerResponse`; task_id present in schema as nullable |
| ingest-loop.mmd: analyze called once; generate retried (AQ-v0.2-1) | PASS — analyze before loop; generate inside loop |
| C4 component diagram shows provider layer (I6 surface) | PASS — component.mmd is new in v0.2 and shows the full provider layer |
| MCP tools in mcp-tools.json match MCP server contracts in v0.2-architecture §6 | PASS — all 4 tools present with matching input/output schemas; transport annotation (stdio) correct |
| ADR-0010: write_page described as reusing persist primitives (not direct FS write) | PASS — mcp-tools.json description and ADR-0010 §2 are consistent |
| ADR-0009 CLI=$0.00 convention: no cost claimed for CliAgentProvider | PASS — mcp-tools.json does not assert billing; ingest_runs total_cost_usd documented as 0.0000 for local/cli |
| C4 context/container/component all start with C4 keyword on line 1 (no leading HTML comment) | PASS — verified by `head -1` of all three files |
| Sequence diagrams start with `sequenceDiagram` on line 1 | PASS — verified by grep |
| CLAUDE.md §12: no model id / API key outside provider/ — reflected in docs | PASS — ER has no api_key; OpenAPI ProviderConfigCreate accepts no api_key; ADRs document env-only constraint |
| D5/D6 not required v0.2; correctly N/A | PASS |
| openapi.json info.version = "0.1.0" and description = "walking skeleton (M1)" | MINOR DRIFT — cosmetic; set in backend/app/main.py (backend code). Routes and schemas are v0.2-accurate. Non-blocking; backend-engineer action required. |

**One cosmetic inconsistency identified:** `openapi.json` `info.version` and `info.description` are stale from v0.1 (set in main.py, which tech-writer cannot edit). This is a cosmetic annotation issue; it does not affect endpoint correctness or schema accuracy. Classified as non-blocking with a tracked backend-engineer action.

No structural contradictions found between CLAUDE.md, ER, OpenAPI, C4, sequence diagrams, and ADRs.

---

## 8. Heading comment compliance

Per tech-writer rules, every diagram file must carry a version/date heading comment.

| File | Comment form | Value | Added this gate |
|------|-------------|-------|-----------------|
| `docs/er/schema.mmd` | `<!-- ... -->` (line 1, generated by script) | `<!-- Generated: v0.2 sprint 2 | 2026-06-28 -->` | via generate_er.py (script emits it) |
| `docs/architecture/context.mmd` | `%% <!-- ... -->` (line 2, after C4Context keyword) | `%% <!-- Generated: v0.2 sprint 2 | 2026-06-28 -->` | YES |
| `docs/architecture/container.mmd` | `%% <!-- ... -->` (line 2) | `%% <!-- Generated: v0.2 sprint 2 | 2026-06-28 -->` | YES |
| `docs/architecture/component.mmd` | `%% <!-- ... -->` (line 2) | `%% <!-- Generated: v0.2 sprint 2 | 2026-06-28 -->` | YES |
| `docs/sequences/ingest-routing.mmd` | `%% <!-- ... -->` (line 2, after sequenceDiagram keyword) | `%% <!-- Generated: v0.2 sprint 2 | 2026-06-28 -->` | YES |
| `docs/sequences/ingest-loop.mmd` | `%% <!-- ... -->` (line 2) | `%% <!-- Generated: v0.2 sprint 2 | 2026-06-28 -->` | YES |
| `docs/api/openapi.json` | N/A (JSON; no comment syntax) | — | — |
| `docs/api/mcp-tools.json` | N/A (JSON; no comment syntax) | `_generated` metadata key present in JSON | — |

Note: Mermaid `%%` is the native Mermaid comment prefix. Using `%% <!-- ... -->` inside a `.mmd` file keeps the comment invisible to the Mermaid renderer while still being readable in the raw file source on GitHub and Obsidian (both render `.mmd` files through the Mermaid engine). The C4 keyword must remain on line 1 for the C4 plugin to activate; the comment is placed on line 2.

---

## 9. v0.1 carry-forward items — resolution in v0.2

| Item | v0.1 tracking | Resolution in v0.2 |
|------|--------------|---------------------|
| OpenAPI 202 schema typed for `/ingest/trigger` | OPEN in v0.1 gate §5 | RESOLVED: `$ref: IngestTriggerResponse` present; task_id in schema (nullable UUID). |
| CI mmdc render check | OPEN in v0.1 gate §5 | Devops gate item; tracked per EC-M2-10 ("CI: mmdc render check"). Not a tech-writer gate item. |
| provider_config table | N/A in v0.1 (v0.2 delivery) | DELIVERED: PROVIDER_CONFIG in ER, OpenAPI endpoints present, ADR-0008 authored. |

---

## 10. DOCS GATE VERDICT

**DOCS GATE: UP-TO-DATE**

All D-artifacts required for sprint v0.2 (D1 updated/new, D2 updated, D3 new, D4 updated/new, D7 new) are present, internally consistent, and pass the drift check. D5/D6 are correctly N/A this sprint.

**Summary of gate results:**

| Gate item | Result |
|-----------|--------|
| D1 C4 context (updated) — line 1 = C4Context, v0.2 accurate | PASS |
| D1 C4 container (updated) — line 1 = C4Container, provider + MCP shown | PASS |
| D1 C4 component (new) — line 1 = C4Component, full provider layer | PASS |
| D2 ER — zero drift, 5 tables, no api_key, all I7 columns | PASS |
| D3 ingest-routing — line 1 = sequenceDiagram, both branches, I6/I7 compliant | PASS |
| D3 ingest-loop — line 1 = sequenceDiagram, analyze-once, dual-bound loop | PASS |
| D4 openapi.json — zero drift, /provider/config GET/POST/DELETE, typed 202 | PASS |
| D4 mcp-tools.json — 4 tools, full input/output schemas, transport noted | PASS |
| D7 ADR-0007 through 0011 — all present, Accepted, indexed in README | PASS |
| Cross-consistency — no structural contradictions across ER/OpenAPI/C4/ADRs/CLAUDE.md | PASS |
| Heading comments — all 6 diagram files now carry version/date annotation | PASS |

**Non-blocking items (do not hold the docs gate):**

1. `openapi.json` `info.version` = "0.1.0" and `info.description` = "walking skeleton (M1)" — cosmetic stale strings set in `backend/app/main.py`. Backend-engineer action before final v0.2 merge.
2. CI mmdc render check — devops gate item (EC-M2-10 second half). Wiring in CI pipeline; not a tech-writer deliverable.
3. D5/D6 remain N/A until v0.3/v0.4 respectively.

The docs gate passes. Tech-writer signs off on EC-M2-16.

**Signed: tech-writer | 2026-06-28**
