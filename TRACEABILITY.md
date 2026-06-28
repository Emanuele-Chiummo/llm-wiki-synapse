# Synapse — Traceability Matrix
> Maintained by: functional-analyst (stub), qa-test-engineer (fills Test ID + Status columns)
> Last updated: 2026-06-28 (Sprint 2 / v0.2 — QA pass; Test IDs filled, statuses updated)
> Source of truth for feature IDs: CLAUDE.md §4
> User stories + ACs: docs/sprints/v0.1-stories.md, docs/sprints/v0.2-stories.md
> Sprint scope + Exit Criteria (EC-x): docs/sprints/v0.1-scope.md §5, docs/sprints/v0.2-scope.md §7
> Backlog ACs (PM-authored): BACKLOG.md §Sprint 1, §Sprint 2
>
> Column guide:
>   Feature ID    — K1–K8 / F1–F17 per CLAUDE.md §4 (or infra label for cross-cutting work)
>   User Story ID — US-<label> in docs/sprints/v0.1-stories.md
>   AC ID         — AC-<LABEL>-<N> as defined in BACKLOG.md and refined in v0.1-stories.md
>   EC            — M1 Exit Criterion from v0.1-scope.md §5 (EC-1 … EC-15)
>   D-artifacts   — D1–D7 as defined in CLAUDE.md §9
>   Invariants    — I1–I9 directly exercised by this AC
>   Planned test file — path relative to backend/tests/ or frontend/tests/ (forward reference)
>   Test ID       — filled by qa-test-engineer after tests are written
>   PR            — PR number that introduced the implementation (filled by engineer)
>   Status        — PENDING / GREEN / MANUAL / GAP (filled by QA)

---

## Sprint 1 — v0.1 Coverage

### K1 — 3-layer vault skeleton

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-K1-1 | US-K1 | EC-1 | — | I5, I8 | test_vault_structure.py | T-VAULT-001, T-VAULT-002 | — | GREEN |
| AC-K1-2 | US-K1 | EC-1 | — | I5 | test_vault_structure.py | T-VAULT-003, T-VAULT-004 | — | GREEN |
| AC-K1-3 | US-K1 | EC-1 | — | — | test_vault_structure.py | T-VAULT-005 | — | GREEN |
| AC-K1-4 | US-K1 | EC-1 | — | — | test_vault_structure.py | T-VAULT-006 | — | GREEN |
| AC-K1-5 | US-K1 | EC-2 | — | I1 | test_vault_structure.py | T-VAULT-011, T-VAULT-012, T-VAULT-013, T-INC-017 | — | GREEN |

---

### K4 — log.md append-only history

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-K4-1 | US-K4 | EC-3 | — | I1 | test_ingest_incremental.py | T-INC-011, T-INC-012 | — | GREEN |
| AC-K4-2 | US-K4 | EC-3 | — | I1 | test_ingest_incremental.py | T-INC-013 | — | GREEN |
| AC-K4-3 | US-K4 | EC-2, EC-3 | — | I1 | test_ingest_incremental.py | T-INC-005 | — | GREEN |

---

### K6 — YAML frontmatter schema

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-K6-1 | US-K6 | EC-4 | D2 | I1 | test_frontmatter.py | T-FM-001..T-FM-008 (25 tests) | — | GREEN |
| AC-K6-2 | US-K6 | EC-4 | D2 | I1 | test_frontmatter.py | T-FM-001..T-FM-008 (25 tests) | — | GREEN |
| AC-K6-3 | US-K6 | EC-4 | D2 | I1 | test_frontmatter.py | T-FM-001..T-FM-008 (25 tests) | — | GREEN |
| AC-K6-4 | US-K6 | EC-4 | D2 | I8 | test_models_schema.py | T-PG-001..T-PG-014 | — | GREEN |

---

### K7 — Obsidian compatibility baseline

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-K7-1 | US-K7 | EC-1 | — | I5 | test_vault_structure.py | T-VAULT-007, T-VAULT-008, T-VAULT-009 | — | GREEN |
| AC-K7-2 | US-K7 | EC-1 | — | I5 | test_vault_structure.py | T-VAULT-010 | — | GREEN |
| AC-K7-3 | US-K7 | EC-1 | — | I5 | — (MANUAL GATE — not automatable) | MANUAL | — | MANUAL |

Note: AC-K7-3 is a mandatory human verification step (Emanuele opens vault/wiki/ in Obsidian). No pytest test ID exists. QA must record this as MANUAL and confirm it in the sign-off register before M1 sign-off.

---

### F16 (partial) — dataVersion

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F16dv-1 | US-F16dv | EC-7 | D2 | I2 | test_models_schema.py | T-PG-015..T-PG-019 | — | GREEN |
| AC-F16dv-2 | US-F16dv | EC-7 | — | I2 | test_ingest_incremental.py | T-INC-014 | — | GREEN |
| AC-F16dv-3 | US-F16dv | EC-6, EC-7 | D4 | I2 | test_api.py | T-API-004 | — | GREEN |
| AC-F16dv-4 | US-F16dv | EC-7 | — | I2 | test_ingest_incremental.py | T-INC-006, T-INC-015 | — | GREEN |

---

### Watcher — watchdog incremental file detection

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-WATCH-1 | US-WATCH | EC-2 | — | I1 | test_ingest_incremental.py | T-INC-001, T-INC-002 | — | GREEN |
| AC-WATCH-2 | US-WATCH | EC-2 | — | I1 | test_ingest_incremental.py, test_watcher_hash.py | T-INC-003, T-INC-004, T-HASH-001..T-HASH-018 | — | GREEN |
| AC-WATCH-3 | US-WATCH | EC-2 | — | I1 | test_ingest_incremental.py | T-INC-007, T-INC-008 | — | GREEN |
| AC-WATCH-4 | US-WATCH | EC-2 | — | I1 | test_ingest_incremental.py | T-INC-009, T-INC-010 | — | GREEN |
| AC-WATCH-5 | US-WATCH | EC-2 | — | I1 | test_ingest_incremental.py | T-INC-016, T-INC-017 | — | GREEN |
| AC-WATCH-6 | US-WATCH | EC-5 | — | I9 | test_ingest_incremental.py | T-INC-002 | — | GREEN |

---

### Postgres + SQLAlchemy models

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-PG-1 | US-PG | EC-8 | D2 | I8 | test_models_schema.py | DEFERRED-needs-live-infra | — | DEFERRED |
| AC-PG-2 | US-PG | EC-8, EC-10 | D2 | I8 | test_models_schema.py | T-PG-020, T-PG-021, T-PG-022, T-PG-023 | — | GREEN |
| AC-PG-3 | US-PG | EC-4 | D2 | I8 | test_models_schema.py | T-PG-001..T-PG-014 | — | GREEN |
| AC-PG-4 | US-PG | EC-8 | — | — | test_models_schema.py, test_code_quality.py | T-PG-019, T-CQ-001..T-CQ-004 | — | GREEN |

---

### K2 (partial) — REST read endpoints

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-REST-1 | US-REST | EC-6 | D4 | I8 | test_api.py | T-API-001..T-API-004 | — | GREEN |
| AC-REST-2 | US-REST | EC-6 | D4 | — | test_api.py | T-API-005..T-API-007 | — | GREEN |
| AC-REST-3 | US-REST | EC-6 | D4 | — | test_api.py | T-API-008..T-API-010 | — | GREEN |
| AC-REST-4 | US-REST | EC-6 | D4 | — | test_api.py | T-API-011..T-API-014 | — | GREEN |
| AC-REST-5 | US-REST | EC-11 | D4 | I8 | test_api.py, test_docs.py | T-API-015..T-API-021, T-DOCS-013..T-DOCS-019 | — | GREEN |
| AC-REST-6 | US-REST | EC-6 | — | — | test_api.py | T-API-009 | — | GREEN |

---

### Qdrant integration

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-QD-1 | US-QD | EC-5 | — | I9 | test_ingest_incremental.py (FakeQdrantClient) | T-INC-001, T-INC-004, T-INC-010 | — | GREEN |
| AC-QD-2 | US-QD | EC-5 | — | I9 | test_qdrant.py (live) | DEFERRED-needs-live-infra | — | DEFERRED |
| AC-QD-3 | US-QD | EC-5 | — | I9 | test_qdrant.py (live) | DEFERRED-needs-live-infra | — | DEFERRED |
| AC-QD-4 | US-QD | EC-5 | — | I9 | test_ingest_incremental.py | T-INC-002 | — | GREEN |

---

### Docker Compose + dev tooling

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-DC-1 | US-DC | EC-8 | — | I8, I9 | test_docker.py (CI) | DEFERRED-needs-docker | — | DEFERRED |
| AC-DC-2 | US-DC | EC-8, EC-12 | — | I8 | CI: make test | 156/156 GREEN | — | GREEN |
| AC-DC-3 | US-DC | EC-8, EC-10 | D2 | I8 | CI: make er | scripts/generate_er.py passes | — | GREEN |
| AC-DC-4 | US-DC | EC-8, EC-11 | D4 | I8 | CI: make openapi | scripts/generate_openapi.py passes | — | GREEN |
| AC-DC-5 | US-DC | EC-8 | — | — | test_code_quality.py | T-CQ-001..T-CQ-010 | — | GREEN |

---

### D1 — C4 Architecture Diagrams

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D1-1 | US-D1 | EC-9 | D1 | I8 | test_docs.py | T-DOCS-001..T-DOCS-004 | — | GREEN |
| AC-D1-2 | US-D1 | EC-9 | D1 | I8 | test_docs.py | T-DOCS-005..T-DOCS-008 | — | GREEN |
| AC-D1-3 | US-D1 | EC-9, EC-13 | D1 | I8 | — (MANUAL GATE — architect sign-off) | MANUAL | — | MANUAL |

---

### D2 — ER Diagram

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D2-1 | US-D2 | EC-10 | D2 | I8 | test_docs.py, test_models_schema.py | T-DOCS-009..T-DOCS-012, T-PG-020..T-PG-024 | — | GREEN |
| AC-D2-2 | US-D2 | EC-10 | D2 | I8 | test_docs.py | T-DOCS-011, T-DOCS-012 | — | GREEN |
| AC-D2-3 | US-D2 | EC-10, EC-13 | D2 | I8 | — (MANUAL GATE — architect sign-off) | MANUAL | — | MANUAL |

---

### D4 — OpenAPI Reference

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D4-1 | US-D4 | EC-11 | D4 | I8 | test_docs.py | T-DOCS-014, T-DOCS-015 | — | GREEN |
| AC-D4-2 | US-D4 | EC-11 | D4 | I8 | test_docs.py | T-DOCS-013, T-DOCS-018 | — | GREEN |
| AC-D4-3 | US-D4 | EC-11 | D4 | I8 | test_docs.py | T-DOCS-016, T-DOCS-017, T-DOCS-019 | — | GREEN |

---

## M1 Exit Criteria coverage summary

| EC | Description (abbreviated) | Covering ACs | All ACs automated? |
|----|---------------------------|-------------|-------------------|
| EC-1 | Vault structure valid | AC-K1-1, AC-K1-2, AC-K1-3, AC-K1-4, AC-K7-1, AC-K7-2, AC-K7-3 | No — AC-K7-3 is MANUAL |
| EC-2 | Incremental ingest fires correctly | AC-WATCH-1 through AC-WATCH-5, AC-K1-5, AC-K4-3 | Yes |
| EC-3 | log.md append-only | AC-K4-1, AC-K4-2, AC-K4-3 | Yes |
| EC-4 | Frontmatter stored correctly | AC-K6-1, AC-K6-2, AC-K6-3, AC-K6-4 | Yes |
| EC-5 | Vectors from existing bge-m3 service | AC-QD-1, AC-QD-2, AC-QD-3, AC-QD-4, AC-WATCH-6 | Yes |
| EC-6 | All 4 REST endpoints operational | AC-REST-1 through AC-REST-6 | Yes |
| EC-7 | dataVersion increments | AC-F16dv-1, AC-F16dv-2, AC-F16dv-3, AC-F16dv-4 | Yes |
| EC-8 | Docker Compose + devtools work | AC-DC-1 through AC-DC-5, AC-PG-1 | Yes (CI) |
| EC-9 | D1 docs gate | AC-D1-1, AC-D1-2, AC-D1-3 | No — AC-D1-3 is MANUAL (architect) |
| EC-10 | D2 docs gate | AC-D2-1, AC-D2-2, AC-D2-3 | No — AC-D2-3 is MANUAL (architect) |
| EC-11 | D4 docs gate | AC-D4-1, AC-D4-2, AC-D4-3, AC-REST-5 | No — requires tech-writer sign-off (human gate) |
| EC-12 | QA gate — green suite | All automated ACs above | GREEN: 156/156 pytest pass; ruff+black+mypy clean |
| EC-13 | Architect gate — models.py / watcher.py / main.py | AC-D1-3, AC-D2-3 | MANUAL (architect) |
| EC-14 | Tech-writer gate — D1, D2, D4 consistent | D1 + D2 + D4 ACs | MANUAL (tech-writer) |
| EC-15 | Human checkpoint — demo approved | EC-2 live run | MANUAL (Emanuele) |

---

## Actual test file index (as of v0.1 QA pass — 2026-06-28)

| File | Layer | Tests | ACs covered | Status |
|------|-------|-------|-------------|--------|
| backend/tests/test_vault_structure.py | unit / filesystem | 15 | AC-K1-1..4, AC-K7-1..2, AC-K1-5 (static) | GREEN |
| backend/tests/test_frontmatter.py | unit | 25 | AC-K6-1, AC-K6-2, AC-K6-3 | GREEN |
| backend/tests/test_watcher_hash.py | unit | 18 | AC-WATCH-2 (mtime+hash gate) | GREEN |
| backend/tests/test_ingest_incremental.py | integration (SQLite+FakeQdrant) | 17 | AC-WATCH-1..6, AC-K4-1..3, AC-F16dv-2/4, I1/G1 | GREEN |
| backend/tests/test_api.py | integration (ASGI) | 21 | AC-REST-1..6, AC-F16dv-3, AC-D4-1..3 | GREEN |
| backend/tests/test_models_schema.py | static introspection | 28 | AC-K6-4, AC-F16dv-1, AC-PG-2..4, AC-D2-1..2 | GREEN |
| backend/tests/test_docs.py | CI artefact | 22 | AC-D1-1..2, AC-D2-1..2, AC-D4-1..3 | GREEN |
| backend/tests/test_code_quality.py | static / lint | 10 | AC-DC-5, I6 guard, I7 guard, I9 guard | GREEN |
| CI: make test | CI script | 156 total | EC-12 suite gate | GREEN |
| CI: make er | script | — | AC-DC-3, AC-D2-1 | GREEN |
| CI: make openapi | script | — | AC-DC-4, AC-D4-2 | GREEN |
| test_qdrant.py (DEFERRED) | live integration | — | AC-QD-2, AC-QD-3 (live Qdrant) | DEFERRED-needs-live-infra |
| test_docker.py (DEFERRED) | live integration | — | AC-DC-1 (docker compose) | DEFERRED-needs-docker |

---

## Gap register

| Gap ID | AC ID | Issue | Resolution |
|--------|-------|-------|-----------|
| GAP-1 | AC-K7-3 | Not automatable — requires human to open vault in Obsidian | Record as MANUAL in sign-off register; human must confirm before EC-1 is marked green |
| GAP-2 | AC-D1-3 | Architect sign-off is a manual human gate, not a pytest test | Record as MANUAL; architect must confirm in sign-off register |
| GAP-3 | AC-D2-3 | Architect sign-off is a manual human gate | Record as MANUAL; architect must confirm in sign-off register |
| GAP-4 | AC-DC-1 | Full docker compose integration test requires external services (Qdrant, bge-m3) to be reachable | Recommend: test_docker.py uses mocked external URLs by default; a separate CI integration stage with real services is labelled "integration" and skipped in unit-only runs |
| GAP-5 | AC-PG-1 | `alembic upgrade head` requires a live Postgres instance; not available in dev sandbox | devops-engineer runs this on TrueNAS before M1 live demo; confirmed DEFERRED-needs-live-infra |
| GAP-6 | AC-QD-2, AC-QD-3 | Live Qdrant API verify (retrieve, confirm payload) requires running Qdrant on TrueNAS | devops-engineer confirms on TrueNAS before M1 live demo; FakeQdrantClient covers functional contract |

---

## Ambiguities requiring architect resolution (before engineering begins)

| AQ ID | Blocks ACs | Question | Recommended resolution |
|-------|-----------|----------|----------------------|
| AQ-1 | AC-QD-1 | bge-m3 embedding dimension: 1024 or 768? BACKLOG says "verify against running instance." | Query running bge-m3; store dimension in EMBEDDING_DIM env var; do not hardcode |
| AQ-2 | AC-WATCH-2, AC-K4-3 | "Unchanged file" detection: mtime-only, hash-only, or mtime-then-hash? | Recommend mtime-then-hash; architect to confirm and document in watcher.py docstring |
| AQ-3 | AC-WATCH-5 | On startup with pre-existing files: silent ignore or log warning? | Architect to confirm; test must assert the chosen behaviour |
| AQ-4 | AC-F16dv-1, AC-F16dv-4 | vault_state row seeding: created on startup or on first ingest? One row or one per vault? | Confirm: seed on startup with data_version=0; one row, vault_id from env var VAULT_ID |
| AQ-5 | AC-K7-3 | Not an architectural ambiguity — recorded as GAP-1 (not automatable) | See GAP-1 |
| AQ-6 | AC-REST-4 | POST /ingest/trigger v0.1 response schema: what fields to return so v0.2 async extension is non-breaking? | Recommend: {"task_id": null, "status": "completed", "page_id": "<uuid>"}; architect to confirm |
| AQ-7 | AC-PG-4 | Alembic migration files necessarily contain SQL; grep test must exclude alembic/ directory | Confirm: alembic directory lives at backend/alembic/ (not inside backend/app/); test scope is backend/app/ only |

---

## Forward-reference columns (to be filled by QA and engineers)

- **Test ID**: assigned by qa-test-engineer after tests are written (format: T-<file_abbrev>-<NNN>, e.g. T-WATCH-001)
- **PR**: pull request number where the implementation landed (filled by backend-engineer)
- **Status**: updated by qa-test-engineer after each test run (GREEN / PENDING / FAIL / MANUAL)

All rows with Status = PENDING must reach GREEN or MANUAL before EC-12 (QA gate) can be signed off.

---

## Sprint 2 — v0.2 Coverage

> User stories + ACs defined in: docs/sprints/v0.2-stories.md
> Exit criteria: EC-M2-1 through EC-M2-17 (docs/sprints/v0.2-scope.md §7)
> AQ IDs (architect questions): AQ-v0.2-1 through AQ-v0.2-8 (docs/sprints/v0.2-stories.md §Ambiguities)
> Invariants with heightened priority: I6 (pluggable inference), I7 (bounded loops)
>
> Column guide (same as Sprint 1):
>   Feature ID  — K1–K8 / F1–F17 / MCP / D-artifact
>   User Story  — US-<label> in docs/sprints/v0.2-stories.md
>   AC ID       — AC-<LABEL>-<N> as defined in BACKLOG.md §Sprint 2
>   EC          — M2 Exit Criterion from v0.2-scope.md §7 (EC-M2-1 … EC-M2-17)
>   D-artifacts — D1–D7 touched by this AC
>   Invariants  — I1–I9 directly exercised
>   Planned test file — path relative to backend/tests/ (forward reference)
>   Test ID     — filled by qa-test-engineer after tests are written
>   PR          — PR number (filled by engineer)
>   Status      — PENDING / GREEN / MANUAL / GAP / LIVE (live-infra test, requires TrueNAS run)

---

### F17 — InferenceProvider ABC + 3 backends + provider_config

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F17-1 | US-F17 | EC-M2-1 | D1, D7 | I6 | test_provider_routing.py | T-ROUTE-001..T-ROUTE-015 | — | GREEN |
| AC-F17-2 | US-F17 | EC-M2-1 | — | I6 | test_schemas.py | T-SCH-001..T-SCH-020 | — | GREEN |
| AC-F17-3 | US-F17 | EC-M2-1, EC-M2-3 | — | I6 | test_provider_routing.py | T-ROUTE-001..T-ROUTE-015 | — | GREEN |
| AC-F17-4 | US-F17 | EC-M2-1 | — | I6 | test_code_quality.py | T-CQ-001..T-CQ-010 | — | GREEN |
| AC-F17-5 | US-F17 | EC-M2-2 | D2 | I6, I8 | test_models_schema.py | T-PG-020..T-PG-025c | — | GREEN |
| AC-F17-6 | US-F17 | EC-M2-2 | — | I6 | test_provider_config_resolution.py, test_provider_config_api.py | multiple | — | GREEN |
| AC-F17-7 | US-F17 | EC-M2-2 | D4 | I6, I8 | test_api.py, test_provider_config_api.py | T-API-*, T-PCFG-* | — | GREEN |
| AC-F17-8 | US-F17 | EC-M2-1 | — | I6 | test_code_quality.py | T-CQ-001..T-CQ-010 | — | GREEN |

Note: All F17 ACs now GREEN. AQ-v0.2-3 resolved by v0.2-architecture.md §2 locking schemas.
AQ-v0.2-5 resolved by adding operation TEXT nullable column to provider_config (ADR-0008).

---

### K2 (ingest op) — Orchestrated ingest loop with capability routing

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-K2-1 | US-K2-INGEST | EC-M2-5, EC-M2-6 | — | I1, I5, I6 | test_smoke_providers.py (CI mock: T-SMOKE-LOCAL-MOCK; live: @pytest.mark.live) | T-SMOKE-LOCAL-MOCK | — | MOCK-GREEN / LIVE-DEFERRED |
| AC-K2-2 | US-K2-INGEST | EC-M2-5, EC-M2-6 | — | I1, I5, I6 | test_smoke_providers.py (CI mock: T-SMOKE-API-MOCK; live: @pytest.mark.live) | T-SMOKE-API-MOCK | — | MOCK-GREEN / LIVE-DEFERRED |
| AC-K2-3 | US-K2-INGEST | EC-M2-5, EC-M2-6 | — | I1, I5, I6 | test_smoke_providers.py (CI mock: T-SMOKE-CLI-MOCK; live: @pytest.mark.live) | T-SMOKE-CLI-MOCK | — | MOCK-GREEN / LIVE-DEFERRED |
| AC-K2-4 | US-K2-INGEST | EC-M2-3 | — | I6 | test_provider_routing.py | T-ROUTE-001..015 | — | GREEN |
| AC-K2-5 | US-K2-INGEST | EC-M2-4 | — | I7 | test_bounded_loop.py | T-LOOP-001..020 | — | GREEN |
| AC-K2-6 | US-K2-INGEST | EC-M2-4 | — | I7 | test_bounded_loop.py | T-LOOP-001..020 | — | GREEN |
| AC-K2-7 | US-K2-INGEST | EC-M2-4 | — | I7 | test_bounded_loop.py | T-LOOP-001..020 | — | GREEN |
| AC-K2-8 | US-K2-INGEST | EC-M2-6 | — | I5 | test_smoke_providers.py (mock path) | T-SMOKE-*-MOCK | — | MOCK-GREEN |

Note: AC-K2-1, AC-K2-2, AC-K2-3 — CI mock tests GREEN (SYNAPSE_SMOKE_MOCK=1 path validates
routing/schema/cost/frontmatter wiring). Live tests (@pytest.mark.live) DEFERRED to TrueNAS
run (GAP-v0.2-1). Run: `pytest tests/test_smoke_providers.py -m live` on TrueNAS.
AQ-v0.2-1 resolved: analyze() called ONCE per run (ADR-0009). AQ-v0.2-2 resolved: Postgres
ingest_runs table (ADR-0009). AQ-v0.2-8 resolved: inline WARNING in orchestrator after cost row.

---

### F3 — Two-step CoT ingest, source traceability, language-aware

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F3-1 | US-F3 | EC-M2-6 | — | I6 | test_schemas.py | T-SCH-001..T-SCH-020 | — | GREEN |
| AC-F3-2 | US-F3 | EC-M2-6 | — | I5, I6 | test_schemas.py | T-SCH-001..T-SCH-020 | — | GREEN |
| AC-F3-3 | US-F3 | EC-M2-6 | — | I5 | test_smoke_providers.py (mock path) | T-SMOKE-*-MOCK | — | MOCK-GREEN |
| AC-F3-4 | US-F3 | EC-M2-6 | — | — | test_smoke_providers.py (live) | DEFERRED-TO-LIVE | — | LIVE-DEFERRED |
| AC-F3-5 | US-F3 | EC-M2-6 | — | I5 | test_smoke_providers.py (mock path) | T-SMOKE-*-MOCK | — | MOCK-GREEN |
| AC-F3-6 | US-F3 | EC-M2-6 | — | I5 | test_smoke_providers.py (mock path) | T-SMOKE-*-MOCK | — | MOCK-GREEN |
| AC-F3-7 | US-F3 | EC-M2-6 | — | I7 | test_bounded_loop.py | T-LOOP-001..020 | — | GREEN |

Note: AQ-v0.2-3 resolved — schemas locked in v0.2-architecture.md §2 and implemented in
backend/app/ingest/schemas.py. AC-F3-1..2 covered by test_schemas.py (schema validation
tests). AC-F3-4 (live language detection) LIVE-DEFERRED — validated during EC-M2-5 smoke
matrix on TrueNAS.

---

### K5 — Wikilink parser + links table

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-K5-1 | US-K5 | EC-M2-8 | — | — | test_wikilink_parser.py | T-WL-001..T-WL-020 | — | GREEN |
| AC-K5-2 | US-K5 | EC-M2-8 | — | — | test_wikilink_parser.py | T-WL-001..T-WL-020 | — | GREEN |
| AC-K5-3 | US-K5 | EC-M2-8 | — | — | test_wikilink_parser.py | T-WL-001..T-WL-020 | — | GREEN |
| AC-K5-4 | US-K5 | EC-M2-8 | D2 | I1 | test_wikilink_parser.py | T-WL-001..T-WL-020 | — | GREEN |
| AC-K5-5 | US-K5 | EC-M2-8 | — | I5 | test_wikilink_parser.py | T-WL-001..T-WL-020 | — | GREEN |
| AC-K5-6 | US-K5 | EC-M2-8 | D2 | I8 | test_models_schema.py | T-PG-030..T-PG-030d | — | GREEN |
| AC-K5-7 | US-K5 | EC-M2-8 | — | — | test_wikilink_parser.py | T-WL-001..T-WL-020 | — | GREEN |

Note: All K5 ACs GREEN. test_wikilink_parser.py covers pure parser unit tests. Links table
columns verified by test_models_schema.py (T-PG-030..T-PG-030d).

---

### K3 — index.md catalogue auto-maintained

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-K3-1 | US-K3 | EC-M2-9 | — | I5 | test_index_md.py | T-IDX-001..T-IDX-015 | — | GREEN |
| AC-K3-2 | US-K3 | EC-M2-9 | — | I5 | test_index_md.py | T-IDX-001..T-IDX-015 | — | GREEN |
| AC-K3-3 | US-K3 | EC-M2-9 | — | I5 | test_index_md.py | T-IDX-001..T-IDX-015 | — | GREEN |
| AC-K3-4 | US-K3 | EC-M2-9 | — | I5 | test_index_md.py | T-IDX-001..T-IDX-015 | — | GREEN |
| AC-K3-5 | US-K3 | EC-M2-9 | — | I1, I5 | test_index_md.py | T-IDX-001..T-IDX-015 | — | GREEN |
| AC-K3-6 | US-K3 | EC-M2-9 | — | — | test_index_md.py | T-IDX-001..T-IDX-015 | — | GREEN |

Note: All K3 ACs GREEN. test_index_md.py exercises the index update function directly
with pre-constructed WikiPage objects (mock provider — no live inference required).

---

### MCP server — FastMCP standalone server

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-MCP-1 | US-MCP | EC-M2-7 | D4 | I6 | test_mcp_tools.py | T-MCP-001..T-MCP-015 | — | GREEN |
| AC-MCP-2 | US-MCP | EC-M2-7 | — | I9 | test_mcp_tools.py | T-MCP-001..T-MCP-015 | — | GREEN |
| AC-MCP-3 | US-MCP | EC-M2-7 | — | I1, I5, I6 | test_mcp_tools.py | T-MCP-001..T-MCP-015 | — | GREEN |
| AC-MCP-4 | US-MCP | EC-M2-7 | — | — | test_mcp_tools.py | T-MCP-001..T-MCP-015 | — | GREEN |
| AC-MCP-5 | US-MCP | EC-M2-7 | — | — | test_mcp_tools.py | T-MCP-001..T-MCP-015 | — | GREEN |
| AC-MCP-6 | US-MCP | EC-M2-7 | — | I1, I6 | test_mcp_tools.py | T-MCP-001..T-MCP-015 | — | GREEN |
| AC-MCP-7 | US-MCP | EC-M2-7 | D4 | I8 | test_docs.py | T-DOCS-028..T-DOCS-028e | — | GREEN |
| AC-MCP-8 | US-MCP | EC-M2-5, EC-M2-7 | — | I6 | test_smoke_providers.py (live) | DEFERRED-TO-LIVE | — | LIVE-DEFERRED |

Note: AC-MCP-1..7 GREEN. AQ-v0.2-6 resolved: stdio transport (v0.2) per ADR-0010.
AC-MCP-7 covered by T-DOCS-028..028e (mcp-tools.json schema check in test_docs.py).
AC-MCP-8 is LIVE-DEFERRED — requires CLI provider + live MCP server on TrueNAS (GAP-v0.2-1).

---

### D3 — Sequence diagrams

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D3-1 | US-D3 | EC-M2-10 | D3 | I8 | test_docs.py | T-DOCS-026..T-DOCS-026c | — | GREEN |
| AC-D3-2 | US-D3 | EC-M2-10 | D3 | I8 | test_docs.py | T-DOCS-027..T-DOCS-027c | — | GREEN |
| AC-D3-3 | US-D3 | EC-M2-10 | D3 | I8 | CI: mmdc render step | DEFERRED (mmdc not in CI) | — | DEFERRED |
| AC-D3-4 | US-D3 | EC-M2-10, EC-M2-15, EC-M2-16 | D3 | I8 | — (MANUAL GATE — architect + tech-writer) | MANUAL | — | MANUAL |

Note: AC-D3-1 and AC-D3-2 GREEN — test_docs.py checks that files exist, contain
sequenceDiagram keyword, and mention both routes (AC-D3-1) or bounded loop (AC-D3-2).
AC-D3-3 deferred — mmdc not in CI; devops carry-forward. AC-D3-4 is MANUAL.

---

### D7 — Architecture Decision Records

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D7-1 | US-D7 | EC-M2-11 | D7 | I8 | test_docs.py | T-DOCS-029 | — | GREEN |
| AC-D7-2 | US-D7 | EC-M2-11 | D7 | I8 | test_docs.py | T-DOCS-029b | — | GREEN |
| AC-D7-3 | US-D7 | EC-M2-11 | D7 | I8 | test_docs.py | T-DOCS-029c | — | GREEN |
| AC-D7-4 | US-D7 | EC-M2-11 | D7 | I8 | test_docs.py | T-DOCS-029..T-DOCS-029c | — | GREEN |
| AC-D7-5 | US-D7 | EC-M2-11, EC-M2-15, EC-M2-16 | D7 | I8 | — (MANUAL GATE — architect + tech-writer) | MANUAL | — | MANUAL |

Note: AC-D7-1..4 GREEN — all 5 ADR files exist, non-empty (>100 chars), and collectively
reference I6 and I7 invariants. AC-D7-5 is a mandatory human gate; cannot be automated.

---

### D1/D2/D4 updates

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D1u-1 | US-D1u | EC-M2-12 | D1 | I8 | test_docs.py | T-DOCS-023..T-DOCS-025 | — | GREEN |
| AC-D1u-2 | US-D1u | EC-M2-12 | D1 | I8 | test_docs.py | T-DOCS-023..T-DOCS-025 | — | GREEN |
| AC-D2u-1 | US-D2u | EC-M2-12 | D2 | I8 | test_docs.py, test_models_schema.py | T-DOCS-020..T-DOCS-022, T-PG-020..T-PG-030 | — | GREEN |
| AC-D4u-1 | US-D4u | EC-M2-12 | D4 | I8 | test_docs.py | T-DOCS-016..T-DOCS-019 | — | GREEN |
| AC-D4u-2 | US-D4u | EC-M2-12 | D4 | I8 | test_docs.py | T-DOCS-028..T-DOCS-028e | — | GREEN |

Note: All D1/D2/D4 update ACs now GREEN. component.mmd exists with InferenceProvider
layer (T-DOCS-023..025). ER includes all 5 v0.2 tables (T-DOCS-020..022). mcp-tools.json
exists with all 4 tools and frontmatter schema (T-DOCS-028..028e).

---

## M2 Exit Criteria coverage summary

| EC | Description (abbreviated) | Covering ACs | Status |
|----|---------------------------|-------------|--------|
| EC-M2-1 | F17 ABC + 3 backends complete | AC-F17-1..8 | GREEN — all automated |
| EC-M2-2 | provider_config table + REST endpoints | AC-F17-5, AC-F17-6, AC-F17-7 | GREEN |
| EC-M2-3 | Capability routing correct branch | AC-K2-4, AC-F17-3 | GREEN |
| EC-M2-4 | I7 bounded loop stops at max_iter | AC-K2-5, AC-K2-6, AC-K2-7 | GREEN |
| EC-M2-5 | 3-provider smoke matrix (live) | AC-K2-1..3, AC-MCP-8 | MOCK-GREEN / LIVE-DEFERRED (GAP-v0.2-1) |
| EC-M2-6 | Generated pages schema-valid | AC-F3-1..7, AC-K2-1..3, AC-K2-8 | MOCK-GREEN / LIVE-DEFERRED |
| EC-M2-7 | MCP server tools exposed and callable | AC-MCP-1..7 | GREEN |
| EC-M2-8 | K5 wikilink parser + links table | AC-K5-1..7 | GREEN |
| EC-M2-9 | K3 index.md auto-updated | AC-K3-1..6 | GREEN |
| EC-M2-10 | D3 sequence diagrams present | AC-D3-1..4 | GREEN (content checks); AC-D3-3 mmdc DEFERRED; AC-D3-4 MANUAL |
| EC-M2-11 | D7 ADRs present | AC-D7-1..5 | GREEN (AC-D7-1..4); AC-D7-5 MANUAL |
| EC-M2-12 | D1/D2/D4 updated | AC-D1u-1..2, AC-D2u-1, AC-D4u-1..2 | GREEN |
| EC-M2-13 | I5 Obsidian still valid post-ingest | test_obsidian_check.py (automated); EC-M2-13 manual open | GREEN (automated); MANUAL open check DEFERRED-TO-LIVE |
| EC-M2-14 | QA gate — green suite | All automated ACs above | GREEN: 299/299 pytest pass; ruff+black clean |
| EC-M2-15 | Architect gate | AC-D3-4, AC-D7-5, AC-D1u-1 | MANUAL (architect sign-off required) |
| EC-M2-16 | Tech-writer gate | AC-D3-1..4, AC-D7-4..5, AC-D1u-1..2 | MANUAL (tech-writer sign-off required) |
| EC-M2-17 | Human checkpoint | EC-M2-5 live demo | MANUAL (Emanuele confirms on TrueNAS) |

---

## Actual test file index (v0.2 — as of QA pass 2026-06-28)

| File | Layer | Tests | Primary ACs | Status |
|------|-------|-------|-------------|--------|
| backend/tests/test_vault_structure.py | unit / filesystem | 15 | AC-K1-1..4, AC-K7-1..2 | GREEN (v0.1 + v0.2 unchanged) |
| backend/tests/test_frontmatter.py | unit | 25 | AC-K6-1..3 | GREEN (v0.1, unchanged) |
| backend/tests/test_watcher_hash.py | unit | 18 | AC-WATCH-2 | GREEN (v0.1, unchanged) |
| backend/tests/test_ingest_incremental.py | integration (SQLite+FakeQdrant) | 17 | AC-WATCH-1..6, AC-K4-1..3, I1/G1 | GREEN (v0.1, unchanged) |
| backend/tests/test_api.py | integration (ASGI) | 21 | AC-REST-1..6, AC-F17-7 | GREEN |
| backend/tests/test_models_schema.py | static introspection | 46 | AC-K6-4, AC-F17-5, AC-K5-6, AC-D2u-1 | GREEN (extended from 28→46) |
| backend/tests/test_docs.py | CI artefact | 42 | AC-D1-1..2, AC-D3-1..2, AC-D7-1..4, AC-D1u-1..2, AC-D2u-1, AC-D4u-1..2, AC-MCP-7 | GREEN (extended from 22→42) |
| backend/tests/test_code_quality.py | static / lint | 10 | AC-F17-4, AC-F17-8, AC-DC-5 | GREEN (v0.1 + extended) |
| backend/tests/test_schemas.py | unit | ~20 | AC-F3-1..2, AC-F17-2 | GREEN |
| backend/tests/test_provider_routing.py | unit | ~15 | AC-K2-4, AC-F17-1, AC-F17-3 | GREEN |
| backend/tests/test_provider_config_resolution.py | unit | multiple | AC-F17-6 | GREEN |
| backend/tests/test_provider_config_api.py | integration (ASGI) | multiple | AC-F17-7 | GREEN |
| backend/tests/test_bounded_loop.py | unit | ~20 | AC-K2-5..7, AC-F3-7 | GREEN |
| backend/tests/test_wikilink_parser.py | unit | ~20 | AC-K5-1..5, AC-K5-7 | GREEN |
| backend/tests/test_index_md.py | unit / integration | ~15 | AC-K3-1..6 | GREEN |
| backend/tests/test_mcp_tools.py | unit (FastMCP) | ~15 | AC-MCP-1..6 | GREEN |
| backend/tests/test_smoke_providers.py | smoke matrix | 6 (3 mock, 3 live-skip) | AC-K2-1..3, AC-MCP-8 | MOCK-GREEN / LIVE-DEFERRED |
| backend/tests/test_obsidian_check.py | unit / self-test | 15 | I5, K7, EC-M2-13 | GREEN |
| CI: make er | script | — | AC-DC-3, AC-D2u-1 | GREEN |
| CI: make openapi | script | — | AC-DC-4, AC-D4u-1 | GREEN |
| CI: mmdc render step | CI (DEFERRED) | — | AC-D3-3 | DEFERRED-needs-mmdc |
| test_qdrant.py (DEFERRED) | live integration | — | AC-QD-2, AC-QD-3 | DEFERRED-needs-live-infra |
| test_docker.py (DEFERRED) | live integration | — | AC-DC-1 | DEFERRED-needs-docker |

Total automated: **299 passed, 3 skipped** (live smoke tests correctly skip in CI without env)

---

## Gap register (v0.2)

| Gap ID | AC ID | Issue | Resolution |
|--------|-------|-------|-----------|
| GAP-v0.2-1 | AC-K2-1, AC-K2-2, AC-K2-3, AC-MCP-8 | Live-infra tests require Ollama on TrueNAS, Anthropic API key, and working claude-agent-sdk — not available in dev sandbox or CI unit-test run | CI mock tests GREEN (SYNAPSE_SMOKE_MOCK=1). Run `pytest tests/test_smoke_providers.py -m live` on TrueNAS before EC-M2-17. Harness at backend/scripts/smoke_providers.py; fixture at backend/tests/fixtures/sample-source.md |
| GAP-v0.2-2 | AC-D3-4, AC-D7-5, AC-D1u-1 (architect approval), AC-D1u-2 (tech-writer) | Architect and tech-writer review gates are not automatable as pytest tests | Record as MANUAL in sign-off register; binary gate before PM sign-off |
| GAP-v0.2-3 | AC-F3-4 | Language detection (EN/IT) requires live inference | Mock path returns preset language codes. Live detection validated in EC-M2-5 smoke matrix |
| GAP-v0.2-4 | AC-K2-6 | RESOLVED: AQ-v0.2-2 resolved — Postgres ingest_runs table chosen (ADR-0009) | GREEN. ingest_runs table model verified by test_models_schema.py T-PG-026..T-PG-029b |
| GAP-v0.2-5 | AC-F17-1, AC-F3-1, AC-F3-2 | RESOLVED: AQ-v0.2-3 resolved — schemas locked in v0.2-architecture.md §2 and implemented in backend/app/ingest/schemas.py | GREEN. Verified by test_schemas.py |
| GAP-v0.2-6 | AC-MCP-1..6 | RESOLVED: AQ-v0.2-6 resolved — stdio transport chosen (ADR-0010, v0.2); HTTP deferred to v0.4 | GREEN. MCP tests use in-process FastMCP client |
| GAP-v0.2-7 | — | BUG FOUND AND FIXED: orchestrator._delegate_ingest() returned bool (converged only), discarding pages_written from DelegatedIngestResult. IngestRunResult.pages_written was always 0 for the CLI (delegated) path. | FIXED: _delegate_ingest() now returns tuple[bool, int]. IngestRunResult.pages_written uses delegated_pages_written on the delegated path. Verified by T-SMOKE-CLI-MOCK |

---

## Ambiguities requiring architect resolution before engineering begins (v0.2)

(Full text of each AQ in docs/sprints/v0.2-stories.md §Ambiguities)

| AQ ID | Blocks ACs | Question (abbreviated) | Recommended resolution |
|-------|-----------|------------------------|----------------------|
| AQ-v0.2-1 | AC-K2-5 | analyze() called once or per retry iteration? | Single analyze recommended (cheaper); confirm in ADR-0007 |
| AQ-v0.2-2 | AC-K2-6 | Ingest run log: Python structlog or Postgres ingest_runs table? | Postgres table recommended for queryability; confirm in ADR-0009 |
| AQ-v0.2-3 | AC-F17-1, AC-F3-1, AC-F3-2 | Exact Pydantic schemas for Analysis, WikiPage, Message, ProviderCapabilities | ai-agent-engineer to define in backend/app/ingest/schemas.py before provider work begins |
| AQ-v0.2-4 | AC-K2-6 | Token usage measurement per backend (Anthropic / Ollama / CLI SDK) | Confirm SDK attribute names; CLI = $0.00 convention; document in ADR-0009 |
| AQ-v0.2-5 | AC-F17-5, AC-F17-6 | provider_config.scope = 'operation' — where is the operation name stored? | Add operation TEXT column (nullable); confirm Alembic migration schema |
| AQ-v0.2-6 | AC-MCP-1..8 | MCP server transport: stdio-only, HTTP-only, or both simultaneously? | Architect to decide; recommend stdio for v0.2 (CLI use case); HTTP in v0.4 |
| AQ-v0.2-7 | AC-K2-5, AC-F3-7 | WikiPage validator: exact "invalid" criteria; partial batch retry or full batch? | Define enum for type; non-empty sources required; full batch retry; document in ADR-0007 |
| AQ-v0.2-8 | AC-K2-6 | Cost anomaly WARNING: inline in orchestrator or separate monitoring hook? | Inline in orchestrator after cost log entry; confirm location in ADR-0009 |

---

## Sprint 3 — v0.3 Coverage

> User stories + ACs defined in: docs/sprints/v0.3-stories.md
> Exit criteria: EC-M3-1 through EC-M3-17 (docs/sprints/v0.3-scope.md §6)
> AQ IDs (architect questions): AQ-v0.3-1 through AQ-v0.3-7 (docs/sprints/v0.3-stories.md §Ambiguities)
> Invariants with heightened priority: I2 (server-side cached graph layout — HEADLINE), I4 (WebGL + DOM bound)
>
> Column guide (same as Sprint 1/2):
>   Feature ID  — K1–K8 / F1–F17 / NB / D-artifact
>   User Story  — US-<label> in docs/sprints/v0.3-stories.md
>   AC ID       — AC-<LABEL>-<N> as defined in BACKLOG.md §Sprint 3
>   EC          — M3 Exit Criterion from v0.3-scope.md §6 (EC-M3-1 … EC-M3-17)
>   D-artifacts — D1–D7 touched by this AC
>   Invariants  — I1–I9 directly exercised
>   Planned test file — path relative to backend/tests/ or frontend/tests/
>   Test ID     — filled by qa-test-engineer after tests are written
>   PR          — PR number (filled by engineer)
>   Status      — PENDING / GREEN / MANUAL / GAP / LIVE / BLOCKED-AQ

---

### NB-5 — OLLAMA_URL docker-compose fix (pre-start blocker)

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-NB5-1 | US-NB5 | (pre-start) | — | I9 | test_code_quality.py (YAML parse) | T-CQ-* | — | GREEN |
| AC-NB5-2 | US-NB5 | (pre-start) | — | I9 | test_docs.py (file assert) | T-DOCS-* | — | GREEN |
| AC-NB5-3 | US-NB5 | (pre-start) | — | I9 | test_smoke_providers.py (@pytest.mark.live) | T-SMOKE-* | — | LIVE |

Note: NB-5 is a sprint-3 pre-start blocker (P0). AC-NB5-3 is a live-infra test; NB5-1 and
NB5-2 are static file assertions automatable in CI. Owner: devops-engineer.

---

### NB-1 — Fallback except clause widened to httpx.HTTPStatusError

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-NB1-1 | US-NB1 | (hardening) | — | I7 | test_bounded_loop.py | T-BL-* | — | GREEN |
| AC-NB1-2 | US-NB1 | (hardening) | — | I7 | test_bounded_loop.py | T-BL-* | — | GREEN |

Note: NB-1 is a P1 hardening task. One-line fix in orchestrator.py. Owner: backend-engineer.

---

### NB-2 — T-CQ-009 I6 guard scoped to import lines only

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-NB2-1 | US-NB2 | (hardening) | — | I6 | test_code_quality.py | T-CQ-009 | — | GREEN |
| AC-NB2-2 | US-NB2 | (hardening) | — | I6 | test_code_quality.py | T-CQ-009 | — | GREEN |

Note: NB-2 is a P2 hardening task. Test code change only; no production code change.
Owner: backend-engineer.

---

### NB-4 — CLI cost logging uses SDK-reported cost when API key present

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-NB4-1 | US-NB4 | (hardening) | — | I7 | test_cli_cost.py | T-CLICOST-001 | — | GREEN |
| AC-NB4-2 | US-NB4 | (hardening) | — | I7 | test_cli_cost.py | T-CLICOST-002 | — | GREEN |
| AC-NB4-3 | US-NB4 | (hardening) | — | I7 | test_cli_cost.py | T-CLICOST-003 | — | GREEN |

Note: NB-4 is a P2 hardening task. Targeted change to cli.py only. Owner: ai-agent-engineer.

---

### F4 — GraphEngine: 4-signal edge weighting

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-1 | US-F4-ENGINE | EC-M3-1 | D2, D3 | I1, I9 | backend/tests/test_graph_engine.py | T-GENG-001..012 | — | GREEN |

Note: AC-F4-1 is BLOCKED pending AQ-v0.3-1 resolution (edge-weight combining formula).
QA cannot finalize fixture expected values until the formula is locked by the architect.
Owner: backend-engineer (engine.py); qa-test-engineer (fixture design).

---

### F4 — GraphEngine: FA2 server-side layout + no client layout

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-2 | US-F4-LAYOUT | EC-M3-2 | D2, D3 | I1, I2, I9 | backend/tests/test_graph_engine.py | T-GENG-003..006 | — | GREEN |
| AC-F4-2b | US-F4-LAYOUT | EC-M3-5 | — | I2 | frontend/src/tests/no-client-layout.test.ts (vitest) | T-NCL-001..022 | — | GREEN |
| AC-F4-2c | US-F4-LAYOUT | EC-M3-2 | — | I2, I9 | backend/tests/test_graph_engine.py | T-GENG-001 | — | GREEN |

Note: AC-F4-2b requires the Vite build to exist (run `make build` before test). AC-F4-2c is
a static import check on engine.py source. Seed for FA2: see AQ-v0.3-2.

---

### F4 / F16 — Graph Cache: dataVersion-debounced recompute

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F16db-1 | US-F4-CACHE | EC-M3-3 | D3 | I2, I7 | backend/tests/test_graph_cache.py | T-GCACHE-001..004 | — | GREEN |
| AC-F16db-2 | US-F4-CACHE | EC-M3-3 | D3 | I2, I7 | backend/tests/test_graph_cache.py | T-GCACHE-005..008 | — | GREEN |
| AC-F16db-3 | US-F4-CACHE | EC-M3-3 | D3 | I2, I7 | backend/tests/test_graph_cache.py | T-GCACHE-009..013 | — | GREEN |
| AC-F16db-4 | US-F4-CACHE | (regression) | D4 | I2 | backend/tests/test_api.py | T-API-* | — | GREEN |

Note: AC-F16db-1 and AC-F16db-2 are BLOCKED on AQ-v0.3-7 (polling vs. event detection
mechanism). Test mock strategy depends on whether the cache uses asyncio.sleep polling or
Postgres LISTEN/NOTIFY or in-process event. Owner: backend-engineer.

---

### F4 — GET /graph REST endpoint

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-3 | US-F4-API | EC-M3-4 | D4 | I2, I8 | backend/tests/test_graph_api.py | T-GRAPI-001..013 | — | GREEN |
| AC-F4-4 | US-F4-API | EC-M3-3 | D4 | I2 | backend/tests/test_graph_api.py | T-GRAPI-010..011 | — | GREEN |
| AC-D4v3-1 | US-DOCS-V3 | EC-M3-4, EC-M3-12 | D4 | I8 | backend/tests/test_docs.py | T-DOCS-042..045 | — | GREEN |

Note: AC-F4-3 and AC-F4-4 are BLOCKED on AQ-v0.3-3 (synchronous vs. async GET /graph
response). If synchronous: test asserts HTTP 200 with full payload. If async: test must
also handle HTTP 202. Owner: backend-engineer.
Also blocked on AQ-v0.3-5 (edges table in Postgres vs. in-memory computation).

---

### F4 — Incremental graph update (G1 proof)

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-9 | US-F4-G1 | EC-M3-8 | — | I1, I2 | backend/tests/test_incremental_graph_update.py | T-INC-GRAPH-001..004 | — | GREEN |

Note: AC-F4-9 interpretation confirmed by AQ-v0.3-4 (tests row-count incrementality,
not coord-value stability). Owner: qa-test-engineer (fixture + test), backend-engineer
(ensure watcher + ingest do not delete/recreate unaffected page rows).

---

### Frontend thin viewer — sigma.js WebGL viewer

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-FE-1 | US-FE-VIEWER | EC-M3-7 | D5 | I2, I4 | frontend/e2e/graph-perf.spec.ts (Playwright) | T-E2E-VIEWER-001 | — | DEFERRED-TO-LIVE |
| AC-FE-2 | US-FE-VIEWER | EC-M3-5 | — | I2 | frontend/src/tests/no-client-layout.test.ts (vitest) | T-NCL-001..022 | — | GREEN |
| AC-FE-3 | US-FE-VIEWER | (I3 pre-compliance) | — | I3 | frontend/src/tests/graphStore.test.ts (vitest) | T-GSTORE-001..016 | — | GREEN |
| AC-FE-4 | US-FE-VIEWER | EC-M3-13 | — | — | npm run lint (eslint+prettier) | T-LINT-CI | — | GREEN |
| AC-FE-5 | US-FE-VIEWER | EC-M3-5 | — | I2, I4 | frontend/src/tests/graph-transform.test.ts (vitest) | T-GTRANS-001..019 | — | GREEN |
| AC-F4-8 | US-FE-CLICK | EC-M3-7 | D5 | I2, I4 | frontend/e2e/graph-perf.spec.ts (Playwright) | T-E2E-CLICK-001 | — | DEFERRED-TO-LIVE |

Note: AC-FE-2 and AC-F4-2b are the same bundle scan check — can share a single vitest
test file. Owner: frontend-engineer (implementation), qa-test-engineer (Playwright spec).
Stack confirmed: React 19 + Vite + TypeScript + sigma.js + graphology + Zustand.
No CodeMirror, no 3-panel layout, no chat, no provider selector.

---

### G2 — No main-thread freeze (Playwright perf)

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-6 | US-G2-PERF | EC-M3-5 | D5 | I2 | frontend/e2e/graph-perf.spec.ts (Playwright) | T-E2E-G2-001..002 | — | DEFERRED-TO-LIVE |

Note: AC-F4-6 requires Playwright Performance API. Use `page.evaluate` to collect
long tasks via `new PerformanceObserver(...)` in the browser context or Playwright's
built-in CDP Performance metrics. Threshold: no task > 50ms duration. Owner: qa-test-engineer.

---

### G4 — 200-node WebGL render at ≥60fps

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-7 | US-G4-PERF | EC-M3-6 | D5 | I4, I2 | frontend/e2e/graph-perf.spec.ts (Playwright) | T-E2E-G4-001..002 | — | DEFERRED-TO-LIVE |

Note: AC-F4-7 requires a synthetic 200-node/500-edge fixture with pre-set x/y coords
seeded directly into Postgres (no FA2 run needed). QA engineer owns this fixture generator.
rAF timing measurement via `page.evaluate` — 60 frames, assert mean ≤16ms, no single
frame > 33ms.

---

### I5 / K7 — Obsidian compatibility regression check

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-OBS-V0.3 | US-OBS-COMPAT | EC-M3-9 | — | I5, I1 | backend/tests/test_obsidian_check.py | T-OBS-001..015 | — | GREEN |

Note: This is a regression check — the existing 15-test suite must remain green. No new
test code needed unless graph engine changes introduce .md frontmatter writes (which it
must NOT do per I1/I2). Owner: qa-test-engineer (verify suite unchanged), backend-engineer
(ensure no vault/ filesystem writes from graph/engine.py or graph/cache.py).

---

### D3 — Graph recompute sequence diagram

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D3v3-1 | US-D3-V3 | EC-M3-10 | D3 | I8 | backend/tests/test_docs.py | T-DOCS-030..034 | — | GREEN |
| AC-D3v3-2 | US-D3-V3 | EC-M3-10 | D3 | I8 | CI: mmdc render check | T-DOCS-MANUAL-003 | — | DEFERRED-TO-LIVE |
| AC-D3v3-3 | US-D3-V3 | EC-M3-10, EC-M3-14, EC-M3-15 | D3 | I8 | — (MANUAL GATE — architect + tech-writer) | MANUAL | — | MANUAL |

Note: AC-D3v3-2 also resolves the v0.2 carry-forward AC-D3-3 (mmdc CI render check for
all three sequence diagrams). devops-engineer must wire mmdc into CI before M3 sign-off.
Owner: tech-writer (diagram), devops-engineer (mmdc CI), architect + tech-writer (manual gate).

---

### D5 — Playwright screenshots (first required sprint)

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D5-1 | US-D5 | EC-M3-11 | D5 | I8 | frontend/e2e/graph-perf.spec.ts + backend/tests/test_docs.py | T-DOCS-046..047 + T-E2E-D5-001 | — | DEFERRED-TO-LIVE |
| AC-D5-2 | US-D5 | EC-M3-11 | D5 | I8 | frontend/e2e/graph-perf.spec.ts + backend/tests/test_docs.py | T-DOCS-047 + T-E2E-D5-002 | — | DEFERRED-TO-LIVE |
| AC-D5-3 | US-D5 | EC-M3-11 | D5 | I8 | frontend/e2e/graph-perf.spec.ts (D5 capture tests) | T-E2E-D5-003 | — | DEFERRED-TO-LIVE |
| AC-D5-4 | US-D5 | EC-M3-11 | D5 | I8 | frontend/playwright.config.ts (config registered) | T-E2E-CONFIG | — | GREEN |

Note: D5 screenshots are committed artifacts. The test_docs.py checks assert they are
non-empty; the Playwright spec generates them. `make screenshots` is a new Makefile target.
Owner: qa-test-engineer.

---

### D1/D2/D4 continuous updates

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D1v3-1 | US-DOCS-V3 | EC-M3-12 | D1 | I8 | backend/tests/test_docs.py (string check) + MANUAL architect gate | T-DOCS-035..037 | — | GREEN (automated) + MANUAL (architect) |
| AC-D2v3-1 | US-DOCS-V3 | EC-M3-12 | D2 | I8 | backend/tests/test_docs.py + backend/tests/test_models_schema.py + CI make er | T-DOCS-038..041 | — | GREEN |

Note: AC-D1v3-1 has a MANUAL component (architect + tech-writer sign-off on diagram accuracy).
AC-D2v3-1 is fully automatable via `make er` + column presence check. Owner: tech-writer (D1),
backend-engineer (D2 via make er and models.py, D4 via make openapi).

---

## M3 Exit Criteria coverage summary

| EC | Description (abbreviated) | Covering ACs | All ACs automated? |
|----|---------------------------|-------------|-------------------|
| EC-M3-1 | 4-signal weighting implemented; unit test on fixture | AC-F4-1 | GREEN (12/12 tests pass — formula AQ resolved in ADR-0012) |
| EC-M3-2 | FA2 server-side; coords in Postgres; make er regenerated | AC-F4-2, AC-F4-2c | GREEN (engine tests pass; ER zero-drift) |
| EC-M3-3 | Recompute debounced; cache-hit on second GET /graph | AC-F16db-1, AC-F16db-2, AC-F16db-3, AC-F4-4 | GREEN (13/13 cache tests + 13/13 API tests pass) |
| EC-M3-4 | GET /graph returns typed JSON; OpenAPI updated | AC-F4-3, AC-D4v3-1 | GREEN (schema tests + OpenAPI drift=0) |
| EC-M3-5 | Sigma viewer; no client-side layout; no long task >50ms | AC-F4-2b, AC-FE-1, AC-FE-2, AC-FE-5, AC-F4-6 | GREEN (static) + DEFERRED-TO-LIVE (long-task Playwright) |
| EC-M3-6 | 200-node 500-edge ≥60fps; DOM <20 nodes | AC-F4-7 | DEFERRED-TO-LIVE (Playwright, needs live browser) |
| EC-M3-7 | Viewer loads; node click shows title; no chat/editor | AC-FE-1, AC-F4-8 | DEFERRED-TO-LIVE (Playwright, needs live browser) |
| EC-M3-8 | Incremental: 1 new file → 1 new coord row | AC-F4-9 | GREEN (4/4 T-INC-GRAPH tests pass; row-count proof) |
| EC-M3-9 | Obsidian check suite 15/15 green | AC-OBS-V0.3 | GREEN (15/15 pass; engine writes no .md files) |
| EC-M3-10 | graph-recompute.mmd present; mmdc passes; reviewed | AC-D3v3-1, AC-D3v3-2, AC-D3v3-3 | GREEN (automated T-DOCS-030..034) + MANUAL (architect/tech-writer gate) |
| EC-M3-11 | ≥2 PNG screenshots committed to docs/screens/ | AC-D5-1, AC-D5-2, AC-D5-3, AC-D5-4 | DEFERRED-TO-LIVE (Playwright + running app required) |
| EC-M3-12 | D1/D2/D4 updated; component.mmd + ER + OpenAPI | AC-D1v3-1, AC-D2v3-1, AC-D4v3-1 | GREEN (automated T-DOCS-035..045) + MANUAL (architect gate) |
| EC-M3-13 | Full pytest + Playwright green; ruff+black+mypy+ts clean | AC-FE-4 + all above ACs | GREEN (366 pytest + 71 vitest; ruff+black+mypy clean) |
| EC-M3-14 | Architect gate: engine.py, cache.py, viewer bundle, GET /graph, ADR | AC-D3v3-3, AC-D1v3-1 | MANUAL |
| EC-M3-15 | Tech-writer gate: D3 + D5 + D1/D2/D4 consistent | AC-D3v3-3, AC-D1v3-1 | MANUAL |
| EC-M3-16 | PM gate: all EC-M3-1..15 MET | All above | Pending DEFERRED-TO-LIVE items + MANUAL gates |
| EC-M3-17 | Human checkpoint: Emanuele views sigma viewer in browser with live vault | — | MANUAL (Emanuele) |

---

## Gap register (v0.3)

| Gap ID | AC ID | Issue | Resolution |
|--------|-------|-------|-----------|
| GAP-v0.3-1 | AC-F4-1 | Fixture expected weight values cannot be finalized until edge-weight combining formula is confirmed (AQ-v0.3-1) | RESOLVED — ADR-0012 published; formula additive: w = 3·direct + 4·source + 1.5·AA + 1·same_type. 12 engine tests pass with exact weights. |
| GAP-v0.3-2 | AC-F16db-1, AC-F16db-2 | Test mock strategy (polling interval mock vs. asyncio task vs. event subscription) depends on GraphCache detection mechanism (AQ-v0.3-7) | RESOLVED — polling (tick() every 0.5s) chosen; FakeClock injectable; 13 cache tests pass. |
| GAP-v0.3-3 | AC-F4-3, AC-F4-4 | GET /graph response mode (synchronous 200 vs. async 202 + job_id) affects test design (AQ-v0.3-3) | RESOLVED — synchronous 200 chosen (ADR-0014); 13 API tests pass with HTTP 200 + X-Graph-Cache header. |
| GAP-v0.3-4 | AC-D3v3-3, AC-D1v3-1 | Architect and tech-writer review gates are not automatable | OPEN — record as MANUAL in sign-off register; binary gate before PM sign-off |
| GAP-v0.3-5 | EC-M3-17 | Human checkpoint (Emanuele views graph in browser) is not automatable | OPEN — record as MANUAL in sign-off register |
| GAP-v0.3-6 | AC-D3v3-2 | mmdc CI render check requires mmdc to be installed in CI (was deferred from v0.2 as best-effort) | OPEN (carried forward) — devops-engineer must add mmdc to CI. T-DOCS-MANUAL-003 sentinel passes unconditionally. Escalate to orchestrator if not resolved by M3. |
| GAP-v0.3-7 | AC-F4-2 | FA2 determinism: if architect chooses non-deterministic seed, AC-F4-2 cannot assert specific x/y values — only non-NULL + range | RESOLVED — ADR-0013: seed=42 fixed via GRAPH_LAYOUT_SEED env var; igraph seeded before every layout call. |
| GAP-v0.3-8 | AC-D5-1..3 | docs/screens/ has 0 PNGs — Playwright requires live browser + running app | DEFERRED-TO-LIVE — harness written at frontend/e2e/graph-perf.spec.ts; run against live stack to populate docs/screens/. T-DOCS-047 passes as sentinel until then. |
| GAP-v0.3-9 | AC-FE-1, AC-F4-8, AC-F4-6, AC-F4-7 | G2 Playwright (long-task), G4 Playwright (fps), viewer load Playwright require live browser | DEFERRED-TO-LIVE — harness written; seeder at backend/scripts/seed_graph_fixture.py. |

---

## Ambiguities requiring architect resolution before engineering begins (v0.3)

(Full text of each AQ in docs/sprints/v0.3-stories.md §Ambiguities)

| AQ ID | Blocks ACs | Question (abbreviated) | Recommended resolution | Urgency |
|-------|-----------|------------------------|----------------------|---------|
| AQ-v0.3-1 | AC-F4-1 | Edge-weight combining formula: additive? What is the "base" per signal? | Additive: each signal contributes (multiplier × 1_if_condition_met); AA multiplier × igraph_AA_score. Publish in ADR. | P0 — blocks fixture expected values |
| AQ-v0.3-2 | AC-F4-2 | FA2 seed: fixed int for determinism, or non-deterministic? | Recommend seed=42; allows byte-stable regression tests. | P1 |
| AQ-v0.3-3 | AC-F4-3, AC-F4-4 | GET /graph: synchronous 200 or async 202 + job_id when FA2 is running? | Recommend synchronous 200 for v0.3. | P0 — blocks API test design |
| AQ-v0.3-4 | AC-F4-9 | "Incremental" means 1 new row added (not coord stability across FA2 reruns)? | Confirm: test row-count only; coords may change after recompute. | P1 — clarifies test assertion scope |
| AQ-v0.3-5 | AC-F4-3, AC-D2v3-1 | Edges table: persistent Postgres table (written after FA2) or in-memory? | Confirm: persistent edges table per BACKLOG spec. Lock schema. | P0 — blocks D2 ER update |
| AQ-v0.3-6 | AC-F4-2, AC-D2v3-1 | x/y coords: columns on pages table or separate graph_coords table? | BACKLOG already specifies pages.x / pages.y; confirm no change. | P1 — likely already resolved by BACKLOG |
| AQ-v0.3-7 | AC-F16db-1, AC-F16db-2 | GraphCache detection mechanism: polling, LISTEN/NOTIFY, or in-process event? | Recommend polling (default 5s interval) for v0.3 simplicity. | P1 — blocks cache test mock strategy |
