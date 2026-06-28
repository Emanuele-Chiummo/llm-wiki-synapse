# DOCS_STATUS — Sprint v0.1 (M1) Documentation Gate

> Tech-writer sign-off for EC-14 (v0.1-scope §7 sign-off register).
> Generated: 2026-06-28
> Author: tech-writer agent

This file is the artifact the milestone gate reads. ALL UP-TO-DATE means the docs gate
passes and sprint/v0.1 may merge. DRIFT means the gate blocks until the listed items
are resolved.

---

## 1. Per-artifact status table

| Artifact | Required v0.1? | Status | Drift check | Notes |
|----------|----------------|--------|-------------|-------|
| **D1 — C4 context** (`docs/architecture/context.mmd`) | YES | UP-TO-DATE | No drift. Syntax verified by inspection (mmdc not installed locally; CI render check is a devops gate per EC-9). | Correctly shows Synapse, Emanuele, Qdrant, bge-m3, Vault filesystem. No v0.2+ components (InferenceProvider, MCP, SearXNG absent). Heading comment added 2026-06-28. |
| **D1 — C4 container** (`docs/architecture/container.mmd`) | YES | UP-TO-DATE | No drift. Syntax verified by inspection. | Containers: FastAPI service, Watcher, Ingest seam, Postgres 16. External: Qdrant, bge-m3, Vault filesystem. `provider_config` correctly absent. Heading comment added 2026-06-28. |
| **D2 — ER diagram** (`docs/er/schema.mmd`) | YES | UP-TO-DATE | ZERO DRIFT confirmed. Regenerated via `python3.13 backend/scripts/generate_er.py`; `git status` shows no change to committed file. | All 12 `pages` columns present including `source_mtime_ns` (required by ADR-0001 fast path) and `deleted_at` (soft-delete, ADR-0005). Partial unique index documented in architecture note. All 4 `vault_state` columns present. `provider_config` correctly absent (ADR-0003). Heading comment now emitted by script. |
| **D3 — Sequence diagrams** (`docs/sequences/`) | NO | N/A-THIS-SPRINT | — | First required sprint: v0.2 (ingest loop) / v0.3 (graph). |
| **D4 — OpenAPI** (`docs/api/openapi.json`) | YES | UP-TO-DATE | ZERO DRIFT confirmed. Regenerated via `python3.13 backend/scripts/generate_openapi.py`; `git status` shows no change to committed file. | All 4 endpoints present: `GET /status`, `GET /pages`, `GET /pages/{id}`, `POST /ingest/trigger`. OpenAPI version 3.1.0. 202 response present for ingest trigger. See minor note below. |
| **D4 — MCP reference** (`docs/api/mcp-tools.md`) | NO | N/A-THIS-SPRINT | — | MCP server is a v0.2 deliverable (v0.1-scope §3). No MCP doc needed this sprint. |
| **D5 — UI screenshots** (`docs/screens/`) | NO | N/A-THIS-SPRINT | — | No UI in v0.1. First required sprint: v0.3. |
| **D6a — USER.md** | NO | N/A-THIS-SPRINT | — | First required sprint: v0.4 (draft). |
| **D6b — DEPLOY.md** (`docs/DEPLOY.md`) | NO | DRAFT/EARLY | Not required this sprint (first required v0.4). File exists as a devops early draft. | Marked DRAFT in file header. Contains v0.1-accurate infra (TrueNAS services, docker compose). Does not imply D6 is "done". |
| **D7 — ADR index** (`docs/adr/README.md`) | EARLY | EARLY-COMPLETE | Created 2026-06-28. All 6 ADRs indexed. | D7 formally required from v0.2 per CLAUDE.md §9; ADR files 0001–0006 committed early by architect (permitted per v0.1-architecture §3 / I8 note). Index table created this sprint to satisfy task instructions. |

---

## 2. D1 C4 validity detail

**context.mmd** — valid Mermaid C4Context syntax:
- `Person`, `System`, `System_Ext` macros used correctly.
- All relationships are directional and described.
- Scope is accurate to v0.1: no LLM, no MCP, no UI, no SearXNG.
- `UpdateLayoutConfig` hint present.

**container.mmd** — valid Mermaid C4Container syntax:
- `System_Boundary` wraps the four Docker Compose containers: `api`, `watcher`, `seam`, `pg`.
- External systems (Qdrant, bge-m3, vaultfs) outside boundary.
- All relationships traceable to architecture note §2.
- No v0.2+ artefacts (InferenceProvider ABC, MCP server, provider_config, SearXNG) drawn as present.
- Seam explicitly labels the v0.2 F17 extension point in its description.

Render check: `mmdc` is not installed on the local dev machine. Per EC-9 and architecture note §4, the mmdc render check (AC-D1-1/2) must run in CI. Flagged to devops for the docs CI stage. Syntax correctness verified by reading the Mermaid C4 specification.

---

## 3. D2 ER completeness detail

Columns verified against `backend/app/models.py` (the source of truth per I8):

**Table `pages`** (12 columns):

| Column | Type in model | Type in ER | Present |
|--------|--------------|------------|---------|
| `id` | UUID PK | uuid PK | YES |
| `vault_id` | String NOT NULL | string | YES |
| `file_path` | Text NOT NULL | string | YES |
| `title` | Text nullable | string | YES |
| `type` (mapped as `page_type`) | Text nullable | string | YES |
| `sources` | JSONB nullable | jsonb | YES |
| `content_hash` | String(64) NOT NULL | string | YES |
| `source_mtime_ns` | BigInteger nullable | int | YES |
| `qdrant_point_id` | UUID nullable | uuid | YES |
| `deleted_at` | TIMESTAMPTZ nullable | timestamptz | YES |
| `created_at` | TIMESTAMPTZ NOT NULL | timestamptz | YES |
| `updated_at` | TIMESTAMPTZ NOT NULL | timestamptz | YES |

Partial unique index `uix_pages_vault_file_path_live` on `(vault_id, file_path) WHERE deleted_at IS NULL`: present in model `__table_args__`; not rendered in erDiagram syntax (erDiagram has no index notation). This is an erDiagram format limitation, not a drift. Architecture note §2.1 documents the index.

**Table `vault_state`** (4 columns):

| Column | Type in model | Type in ER | Present |
|--------|--------------|------------|---------|
| `id` | UUID PK | uuid PK | YES |
| `vault_id` | String NOT NULL (UNIQUE) | string | YES |
| `data_version` | Integer NOT NULL default 0 | int | YES |
| `updated_at` | TIMESTAMPTZ NOT NULL | timestamptz | YES |

`UniqueConstraint("vault_id")` present in model; not rendered in erDiagram (format limitation).

`provider_config`: correctly ABSENT from ER (ADR-0003; enters at v0.2).

---

## 4. D4 OpenAPI completeness detail

| Endpoint | Method | Status code | Response schema | Present |
|----------|--------|-------------|-----------------|---------|
| `/status` | GET | 200 | `StatusResponse` (vault_id, data_version, started_at, uptime_seconds) | YES |
| `/pages` | GET | 200 | `PageListResponse` (items, total, limit, offset) | YES |
| `/pages/{page_id}` | GET | 200 / 404 / 422 | `PageResponse` (all 10 serialisable page fields) | YES |
| `/ingest/trigger` | POST | 202 / 422 | Open schema with example; 422 on bad body | YES |

Minor note: the 202 response for `POST /ingest/trigger` uses an open schema (no `$ref`) rather than referencing `IngestTriggerResponse` directly. The response example in the OpenAPI shows `{status, page_id}` without `task_id`. The `IngestTriggerResponse` Pydantic model in `main.py` does include `task_id: None`, but FastAPI's `response_class=JSONResponse` path bypasses the model schema for the 202. This is a FastAPI OpenAPI generation artifact, not a code/contract error: the actual runtime response includes `task_id` per ADR-0006. Future action: add `response_model=IngestTriggerResponse` with `status_code=202` to the route decorator so the 202 schema is formally typed in the OpenAPI. Tracked as a v0.2 clean-up item (non-blocking for this gate).

---

## 5. Cross-consistency sweep

| Check | Result |
|-------|--------|
| `provider_config` absent from ER, C4, ADRs in v0.1 | PASS — correctly deferred to v0.2 per ADR-0003 |
| C4 container diagram matches architecture note components | PASS — FastAPI, Watcher, Seam, Postgres 16 match §2 exactly |
| OpenAPI endpoints match architecture note §2.5 | PASS — all 4 endpoints present with correct methods and status codes |
| ER tables match `models.py` tables | PASS — zero drift confirmed by script regeneration |
| No doc references SearXNG as a v0.1 component | PASS — SearXNG mentioned only in DEPLOY.md prerequisites (external service, not started by Synapse) |
| ADR-0001 mtime-then-hash: `source_mtime_ns` + `content_hash` in ER | PASS — both columns present in PAGES table |
| ADR-0002 point id == page id: `qdrant_point_id` in ER | PASS — column present |
| ADR-0005 soft-delete: `deleted_at` in ER | PASS — column present |
| ADR-0005 vault_state: `data_version` in ER | PASS — column present |
| ADR-0006 ingest trigger: 202 in OpenAPI | PASS — 202 response present |
| C4 context: no InferenceProvider, no MCP, no UI clients in v0.1 | PASS — clients section correctly absent; v0.1 context shows only Emanuele + 3 external systems |
| DEPLOY.md does not imply D6 is "done" | PASS — file is marked "Initial draft" in its own header; classified DRAFT/EARLY in this gate |
| QA report (`docs/sprints/v0.1-qa-report.md`) | MISSING — file does not exist. This is a QA deliverable, not a tech-writer deliverable. Flagged to orchestrator: EC-12 (QA green) cannot be confirmed without it. This does NOT block the docs gate (D-artifacts only), but DOES block the overall M1 gate until QA supplies it. |

No contradictions found between CLAUDE.md repo layout, the ER, the OpenAPI, the C4 diagrams, and the ADRs.

---

## 6. Heading comment compliance

Per tech-writer rules, every diagram file must carry `<!-- Generated: v0.x sprint N | YYYY-MM-DD -->`.

| File | Comment added | Value |
|------|--------------|-------|
| `docs/architecture/context.mmd` | 2026-06-28 | `<!-- Generated: v0.1 sprint 1 | 2026-06-28 -->` |
| `docs/architecture/container.mmd` | 2026-06-28 | `<!-- Generated: v0.1 sprint 1 | 2026-06-28 -->` |
| `docs/er/schema.mmd` | 2026-06-28 (via generate_er.py) | `<!-- Generated: v0.1 sprint 1 | 2026-06-28 -->` |

`docs/api/openapi.json` is JSON and does not support HTML comments. No heading comment required or possible.

---

## 7. DOCS GATE VERDICT

**DOCS GATE: UP-TO-DATE**

All D-artifacts required for sprint v0.1 (D1, D2, D4) are present, internally consistent,
and pass the drift check. D3/D5/D6/D7 are N/A or EARLY this sprint per scope lock.

The docs gate passes. The tech-writer signs off on EC-14.

**Open items (non-blocking for the docs gate; tracked for v0.2):**

1. CI mmdc render check (AC-D1-1/2): `mmdc` must be wired in the docs CI stage by devops.
   Not a docs-gate blocker per architecture note §4/EC-9.
2. OpenAPI 202 schema for `POST /ingest/trigger` is an open schema rather than a typed
   `$ref`. Add `response_model=IngestTriggerResponse, status_code=202` in v0.2 route decorator.
3. `docs/sprints/v0.1-qa-report.md` missing — QA deliverable. Blocks EC-12 (QA gate) but
   not EC-14 (docs gate).

**Signed: tech-writer | 2026-06-28**
