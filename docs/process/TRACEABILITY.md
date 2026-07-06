# Synapse — Traceability Matrix
> Maintained by: functional-analyst (stub), qa-test-engineer (fills Test ID + Status columns)
> Last updated: 2026-06-29 (Sprint 5 / v0.5 — M5 Phase 5 ACs flipped to GREEN; M5 milestone docs consolidation; tech-writer docs gate)
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

Note: AC-F4-1 formula resolved (ADR-0012 published; GREEN). **ADR-0016 correction (Sprint 4,
Phase 0):** the ADR-0012 §5 worked-fixture expectation for the P3–P5 pair has been updated.
BEFORE (ADR-0012 §3 rule "persist iff weight > 0"): P3–P5 (same-type only, no link, no shared
source) → weight == 1.0, edge PRESENT and stored.
AFTER (ADR-0016 §1 structural gate, supersedes ADR-0012 §3): P3–P5 → NO edge (absent).
type-only affinity no longer materializes an edge; it only modulates weight on edges that already
exist via a structural tie. The T-GENG fixture expectations for this pair must assert `edge absent`
(not weight 1.0). Lower-bound assertions for P1–P2 (≥11), P1–P4 (≥8), P2–P4 (≥5) are UNCHANGED
(those pairs are structural via direct link or shared source and keep identical stored weights).
This correction prevents the type-clique hairball from being silently re-introduced on any future
engine.py edit that reverts to the old gate. Owner: qa-test-engineer (update T-GENG fixture).

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
| GAP-v0.3-1 | AC-F4-1 | Fixture expected weight values cannot be finalized until edge-weight combining formula is confirmed (AQ-v0.3-1) | RESOLVED — ADR-0012 published; formula additive: w = 3·direct + 4·source + 1.5·AA + 1·same_type. 12 engine tests pass with exact weights. **SUPERSEDED (Sprint 4):** ADR-0016 §1 changes the edge *inclusion* gate — same_type alone no longer materialises an edge. T-GENG P3–P5 fixture expectation corrected from "weight 1.0, present" to "absent". See AC-F4-GUX-1. |
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

---

## Sprint 4 — v0.4 / M4-GUX Coverage

> Scope: GraphUX formalization bucket (M4-GUX-1..8 per docs/sprints/v0.4-pm-scope.md §1b).
> These items were implemented at the v0.3 → v0.4 boundary before the formal sprint kickoff;
> this section formalizes them into the traceability record so they are tested, gated, and
> auditable. All other M4 features (F1, F6, F7, F8, F14, F17-UI, F16-rest, G3, D5-update, D6,
> NB-6..8) are tracked in a subsequent section added per phase.
>
> ADR reference: docs/adr/0016-obsidian-graph-rendering.md (ADR-0016)
> Exit criteria: EC-M4-Phase0 — T-GENG suite green (ADR-0016 changes), ADR-0016 signed off by
> architect + tech-writer, D2 + D4 zero drift, at least 1 committed graph PNG.
>
> Column guide (same as Sprint 1/2/3):
>   Feature ID  — F4 (graph engine / viewer) or accessibility sub-feature
>   User Story  — US-<label> (M4-GUX stories; no separate stories file; full story text below)
>   AC ID       — AC-F4-GUX-<N> (sequential within this GraphUX bucket)
>   EC          — M4 Phase 0 exit condition or M4 DoD gate number
>   D-artifacts — D1–D7 touched
>   Invariants  — I1–I9 exercised
>   Planned test file — path relative to backend/tests/ or frontend/src/tests/
>   Test ID     — filled by qa-test-engineer after tests are written
>   PR          — filled by engineer
>   Status      — PENDING / GREEN / MANUAL / DEFERRED-TO-LIVE

---

### M4-GUX-1 / M4-GUX-2 — Structural-only edges + per-edge kind field (F4)

**User story (US-F4-GUX-STRUCTURAL):** As a vault owner, I want the knowledge graph to show
only edges that represent real document connections (wikilinks and shared sources), so that the
graph reflects genuine knowledge structure instead of a hairball of same-type nodes.

**Invariants:** I2 (server-side layout stays cached; only the edge input set changes), I1
(engine reads Postgres only; no vault rescan), I7 (single bounded FA2 pass, strengthened by
smaller edge count), I8 (D2/D4 must reflect the kind column).

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-GUX-1 | US-F4-GUX-STRUCTURAL | M4-DoD-gate-8 | D2, D3 | I1, I2, I7 | backend/tests/test_graph_engine.py | T-GENG-001..012 (fixture corrected) | — | GREEN |
| AC-F4-GUX-2 | US-F4-GUX-STRUCTURAL | M4-DoD-gate-8 | D2, D3 | I1, I7 | backend/tests/test_graph_engine.py | T-GENG-013+ | — | GREEN |
| AC-F4-GUX-3 | US-F4-GUX-STRUCTURAL | M4-DoD-gate-8 | D4 | I2, I8 | backend/tests/test_graph_api.py | T-GRAPI-014+ | — | GREEN |

**Acceptance criteria:**

1. AC-F4-GUX-1 (P3-P5 correction — CRITICAL): `pytest` on `test_graph_engine.py` with the
   updated fixture asserts that a page-pair whose only non-zero signal is `same_type` (direct=0,
   source=0) produces ZERO edges in the output — `assert (P3_id, P5_id) not in edge_set and
   (P5_id, P3_id) not in edge_set`. PASS = absent; FAIL = any row with only same_type present.
   This directly replaces the old ADR-0012 §5 expectation "P3–P5 → weight 1.0, present".
   Lower-bound weight assertions P1–P2 (≥11), P1–P4 (≥8), P2–P4 (≥5) remain unchanged and
   must continue to pass in the same test run.

2. AC-F4-GUX-2: The 200-node scale-free demo dataset (seed_demo_vault.py, `type = i % 4`) produces
   strictly fewer than 4900 edges under the structural gate. `pytest` assertion:
   `assert len(edges) < 4900`. The test must also assert `len(edges) >= 1` (at least one link
   exists in the scale-free graph). Rationale: old behavior = exactly 4900 (complete 4-cliques);
   new behavior = only real structural edges.

3. AC-F4-GUX-3: `GET /graph` response schema includes `kind` on every edge object. Pydantic
   validation passes with `kind: Literal["link", "source"]`. No edge object in the response
   may have `kind` absent or set to any other value. `pytest` assertion via `GraphEdgeResponse`
   schema validation on the full response payload. Backward-compatibility: existing fields
   (`weight`, `source`, `target`) are present and unchanged.

Note: AC-F4-GUX-1 is the direct operationalization of the ADR-0016 §6 handoff. The corrected
fixture must be committed before Phase 0 is signed off. The type-clique defect cannot silently
return as long as this test remains in the suite. Owner: qa-test-engineer (fixture update),
backend-engineer (engine.py structural gate).

---

### M4-GUX-3 — Node size proportional to structural degree (F4)

**User story (US-F4-GUX-SIZE):** As a vault reader viewing the graph, I want nodes with more
real connections to appear visibly larger, so that I can identify hub notes at a glance without
counting edges manually.

**Invariants:** I2 (size computation is server-side; client uses the returned `size` value
verbatim without recalculating), I4 (no size computation on the UI main thread).

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-GUX-4 | US-F4-GUX-SIZE | M4-DoD-gate-8 | D3 | I2, I4 | backend/tests/test_graph_engine.py | T-GENG-014+ | — | GREEN |
| AC-F4-GUX-5 | US-F4-GUX-SIZE | M4-DoD-gate-8 | — | I2, I4 | frontend/src/tests/graph-transform.test.ts | T-GTRANS-020+ | — | GREEN |

**Acceptance criteria:**

4. AC-F4-GUX-4: `pytest` asserts the server-side size formula `size = 1.0 + 1.0 * sqrt(degree)`
   for integer structural degrees 0, 1, 2, 4, 9: expected values are 1.0, 2.0, ≈2.414, 3.0, 4.0
   respectively (float tolerance ±0.001). A node with 0 structural edges has `size == 1.0`
   (isolated node, still clickable). A node with 9 structural edges has `size == 4.0`. Both
   assertions must pass in the same test.

5. AC-F4-GUX-5: vitest on `graph-transform.test.ts` asserts that `graphTransform` (the frontend
   transformer) reads `node.size` from the server response verbatim and does NOT apply any
   independent degree-based size calculation. If `node.size` is not present in the response, the
   fallback must be a fixed constant (not `1 + ln(1 + node.degree)`). PASS = no `Math.log` or
   `Math.sqrt(degree)` call in the size path when `node.size` is provided.

Note: The client ×5 display scale (graphTransform.ts) is a pure multiplier and is not tested
here — it is a presentation constant, not a business rule. Owner: backend-engineer (formula in
engine.py), frontend-engineer (remove stale client-side curve if present), qa-test-engineer.

---

### M4-GUX-4 — Database migrations (edges.kind, pages.pinned) + ER/OpenAPI update (F4)

**User story (US-F4-GUX-MIGRATIONS):** As a backend-engineer deploying ADR-0016, I want the
database schema to include the `edges.kind` column and `pages.pinned` boolean so that the
graph API can persist and return structural edge kind and user-pinned node positions without
additional queries.

**Invariants:** I8 (D2 ER diagram and D4 OpenAPI must reflect schema; make er and make openapi
must exit 0 with zero drift).

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-GUX-6 | US-F4-GUX-MIGRATIONS | M4-DoD-gate-8 | D2 | I8 | backend/tests/test_models_schema.py | T-PG-031+ | — | GREEN |
| AC-F4-GUX-7 | US-F4-GUX-MIGRATIONS | M4-DoD-gate-8 | D4 | I8 | backend/tests/test_docs.py | T-DOCS-048+ | — | GREEN |

**Acceptance criteria:**

6. AC-F4-GUX-6: `pytest` on `test_models_schema.py` statically introspects the SQLAlchemy
   `Edge` model and asserts: (a) `Edge.kind` column exists with type `TEXT`, nullable=True;
   (b) `Page.pinned` column exists with type `BOOLEAN`, nullable=False, default=False.
   Both assertions must pass. `make er` must exit 0 with the updated `docs/er/schema.mmd`
   reflecting both columns (CI gate: T-DOCS-038..041 range extended or new test added).

7. AC-F4-GUX-7: `pytest` on `test_docs.py` asserts that `docs/api/openapi.json` contains the
   string `"kind"` in the `GraphEdgeResponse` schema definition (field present in the schema
   object). `make openapi` must exit 0 and produce a non-empty openapi.json. Both conditions
   must pass in the same test.

Note: Alembic migrations 0004 (edges.kind) and 0005 (pages.pinned) must be present in
`backend/alembic/versions/`. The migration files are excluded from the I6 hardcode guard
(as per AQ-7 resolution in Sprint 1). Owner: backend-engineer (Alembic migrations, models.py).

---

### M4-GUX-5 — Near-circular server-side layout envelope (F4/I2)

**User story (US-F4-GUX-LAYOUT):** As a graph viewer user, I want nodes to be distributed in a
roughly circular layout so that the graph fits naturally in a square viewport without long
thin clusters extending off-screen.

**Invariants:** I2 (layout is computed server-side; _compress_to_disc runs in engine.py, not
in the browser; the no-client-layout bundle assertion T-NCL-001..022 must still pass).

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-GUX-8 | US-F4-GUX-LAYOUT | M4-DoD-gate-8 | D3 | I1, I2 | backend/tests/test_graph_engine.py | T-GENG-015+ | — | GREEN |
| AC-F4-GUX-9 | US-F4-GUX-LAYOUT | M4-DoD-gate-8 | — | I2 | frontend/src/tests/no-client-layout.test.ts | T-NCL-001..022 | — | GREEN |

**Acceptance criteria:**

8. AC-F4-GUX-8: `pytest` on a fixture graph with ≥10 nodes asserts that after engine layout,
   all node coordinates satisfy `x**2 + y**2 <= r**2 * 1.1` where `r` is the radius of the
   bounding disc (computed as `max(abs(x), abs(y))` across all nodes). The 1.1 tolerance allows
   for floating-point rounding in `_compress_to_disc`. PASS = all nodes inside disc envelope;
   FAIL = any node outside. Aspect ratio assertion: `assert 0.9 <= (x_range / y_range) <= 1.2`
   for the full node set.

9. AC-F4-GUX-9: vitest bundle assertion (`no-client-layout.test.ts` T-NCL-001..022) still passes
   unchanged after all M4-GUX frontend changes. This test scans the compiled bundle and asserts
   zero import of force-layout libraries. PASS = T-NCL suite exits 0 with all 22 assertions
   green. Any regression here is a P0 blocker (I2 violation).

Note: _compress_to_disc is a server-side utility in engine.py. It must not be imported or
replicated in any frontend file. Owner: backend-engineer (engine.py), qa-test-engineer
(T-NCL regression confirmation).

---

### M4-GUX-6 — Single-node drag with position persistence (F4/I2)

**User story (US-F4-GUX-DRAG):** As a graph viewer user, I want to drag individual nodes to a
custom position and have that position persist across page reloads and graph recomputes, so that
I can manually organize nodes that the automatic layout places inconveniently.

**Invariants:** I2 (drag is single-node direct manipulation; it does NOT run a force layout;
PATCH /pages/{id}/position must not trigger FA2 or bump data_version), I1 (position written to
Postgres pages table rows only; no vault filesystem write).

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-GUX-10 | US-F4-GUX-DRAG | M4-DoD-gate-8 | D4 | I1, I2 | backend/tests/test_graph_api.py | T-GRAPI-015+ | — | GREEN |
| AC-F4-GUX-11 | US-F4-GUX-DRAG | M4-DoD-gate-8 | D2 | I1, I2 | backend/tests/test_graph_engine.py | T-GENG-016+ | — | GREEN |
| AC-F4-GUX-12 | US-F4-GUX-DRAG | M4-DoD-gate-8 | — | I2 | frontend/src/tests/no-client-layout.test.ts | T-NCL-001..022 | — | GREEN |

**Acceptance criteria:**

10. AC-F4-GUX-10: `pytest` on `test_graph_api.py` sends `PATCH /pages/{id}/position` with body
    `{"x": 1.5, "y": -2.3}` and asserts: (a) HTTP 200 returned; (b) `pages.x == 1.5` and
    `pages.y == -2.3` in Postgres after the call; (c) `pages.pinned == True` in Postgres after
    the call; (d) `data_version` in `vault_state` is UNCHANGED (same integer before and after
    the PATCH). All four assertions must pass in the same test. FAIL on any one = FAIL overall.

11. AC-F4-GUX-11: `pytest` on `test_graph_engine.py` calls the engine recompute with at least
    one node having `pinned=True`. After recompute, asserts that the pinned node's `x` and `y`
    values in the output snapshot equal the pre-recompute values (±0.001 tolerance). Non-pinned
    nodes may have any coordinates. PASS = pinned coords preserved; FAIL = pinned coords changed.

12. AC-F4-GUX-12: vitest `no-client-layout.test.ts` T-NCL-001..022 all pass after the drag
    feature is introduced. The drag handler (sigma.js `dragNode` event) must call
    `PATCH /pages/{id}/position` via the store's `patchPosition` action; it must NOT import or
    call any force-layout function. Bundle scan must detect zero layout-library imports.
    PASS = T-NCL exits 0; FAIL = any layout import found.

Note: The distinction between single-node drag (I2-compatible) and a force layout (I2-violating)
is binary and must be enforced by the T-NCL bundle scan. If the drag implementation accidentally
pulls in a layout library the scan will catch it. Owner: frontend-engineer (sigma drag handler +
store patchPosition action), backend-engineer (PATCH /pages/{id}/position endpoint), qa-test-engineer.

---

### M4-GUX-7 — Obsidian-style viewer: color, hover-dim, accessible labels, LOD (F4, Accessibility)

**User story (US-F4-GUX-VIEWER):** As a vault reader, I want the graph viewer to use color
coding by note type, dim non-related nodes on hover, show readable labels at appropriate zoom
levels, and announce the selected node to screen readers, so that the graph is both visually
clear and accessible.

**Invariants:** I4 (sigma.js WebGL rendering; no DOM node per graph node; no main-thread
force layout), I2 (viewer reads pre-computed coords from server; no layout on client).

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-GUX-13 | US-F4-GUX-VIEWER | M4-DoD-gate-8 | D5 | I2, I4 | frontend/src/tests/graphViewer.test.ts (vitest-jsdom) | T-GVIEW-001+ | — | GREEN |
| AC-F4-GUX-14 | US-F4-GUX-VIEWER | M4-DoD-gate-8 | D5 | I4 | frontend/src/tests/graphViewer.test.ts | T-GVIEW-002+ | — | GREEN |
| AC-F4-GUX-15 | US-F4-GUX-VIEWER | M4-DoD-gate-8 | D5 | — | frontend/src/tests/graphViewer.test.ts | T-GVIEW-003+ | — | GREEN |
| AC-F4-GUX-16 | US-F4-GUX-VIEWER | M4-DoD-gate-8 | D5 | — | frontend/e2e/graph-accessibility.spec.ts (Playwright) | T-E2E-A11Y-001+ | — | DEFERRED-TO-LIVE |

**Acceptance criteria:**

13. AC-F4-GUX-13: vitest on `graphViewer.test.ts`: simulate a hover event on node N. Assert
    that all non-neighbor nodes receive an opacity class or sigma attribute that resolves to a
    rendered opacity ≤ 0.2 (faded). Neighbor nodes and node N itself must have opacity ≥ 0.9
    (highlighted). PASS = opacity split is binary (faded vs. highlighted); FAIL = any non-neighbor
    with opacity > 0.2 or any neighbor with opacity < 0.9. Assert via vitest-jsdom attribute
    inspection or sigma `getNodeAttributes` call mock.

14. AC-F4-GUX-14: vitest on `graphViewer.test.ts`: assert the graph container element has an
    `aria-live` attribute (value "polite" or "assertive"). When a node is selected (click event
    simulated), assert the aria-live region's text content equals the selected node's `title`
    field from the server response. PASS = aria-live present and text updated; FAIL = attribute
    absent or text not updated.

15. AC-F4-GUX-15: vitest on `graphViewer.test.ts` using `window.matchMedia` mock: set
    `(prefers-reduced-motion: reduce)` to active. Assert that the hover-dim transition-duration
    applied to nodes/edges is `0ms` (or the transition property is `none`). When
    `(prefers-reduced-motion: reduce)` is NOT active, assert transition-duration is > 0ms.
    PASS = transition-duration == 0ms when reduced motion is active; FAIL = any non-zero
    duration when reduced motion active.

16. AC-F4-GUX-16 (DEFERRED-TO-LIVE): Playwright accessibility check on the live graph viewer
    page: run `axe-core` or `@axe-core/playwright` and assert zero critical accessibility
    violations. The aria-live region (AC-F4-GUX-14), color contrast of halo labels (~16:1 AAA
    target), and label-on-demand LOD must all pass. PASS = zero critical axe violations; FAIL =
    any critical violation. Owner: qa-test-engineer (Playwright spec), frontend-engineer
    (CVD-safe palette, accessible label contrast).

Note: CVD-safe color palette is a design constraint verified by AC-F4-GUX-16 (contrast check)
and visually confirmed in the D5 screenshot gate (M4-DoD-gate-5). The palette itself is not
tested numerically in unit tests — the contrast check in Playwright is the automation gate.
Owner: frontend-engineer (sigma renderer hooks), qa-test-engineer (vitest mocks + Playwright).

---

### M4-GUX-8 — Demo dataset 140-node scale-free (F4)

**User story (US-F4-GUX-DEMO):** As a developer verifying the graph viewer, I want a realistic
demo dataset with 140 nodes and a scale-free degree distribution, so that node-size variation
is visually apparent in screenshots and the viewer can be demonstrated without real vault content.

**Invariants:** I1 (seed script writes to Postgres only; does not modify vault/raw/ or vault/wiki/),
I8 (D5 screenshots from this dataset must be committed).

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F4-GUX-17 | US-F4-GUX-DEMO | M4-DoD-gate-8 | D5 | I1 | backend/tests/test_seed_demo.py | T-SEED-001+ | — | GREEN |
| AC-F4-GUX-18 | US-F4-GUX-DEMO | M4-DoD-gate-5 | D5 | I8 | frontend/e2e/graph-perf.spec.ts (D5 capture) | T-E2E-D5-002 | — | DEFERRED-TO-LIVE |

**Acceptance criteria:**

17. AC-F4-GUX-17: `pytest` on `test_seed_demo.py` runs `seed_demo_vault.py` against an in-memory
    SQLite or test Postgres instance and asserts: (a) exactly 140 page rows inserted; (b) at least
    one node has `degree >= 10` (hub node exists — scale-free property); (c) at least 30 nodes have
    `degree == 1` (leaf nodes exist — scale-free property); (d) the seed script writes ONLY to
    Postgres tables (`pages`, `edges`) and does not create any file under `vault/`. All four
    assertions must pass. PASS = all four conditions true; FAIL = any condition false.

18. AC-F4-GUX-18 (DEFERRED-TO-LIVE): Playwright captures `docs/screens/graph-viewer-structural.png`
    after running `seed_demo_vault.py` on the live stack. The screenshot must show: visible node-size
    variation (at least 2 visibly different node sizes present — verified by human review at M4
    checkpoint), structural-only edges (no dense clique visible — verified by human review). This
    screenshot satisfies the outstanding v0.3 GAP-v0.3-8 DEFERRED-TO-LIVE condition AND provides
    the M4-GUX D5 evidence. Owner: qa-test-engineer (Playwright capture), Emanuele (human review
    at EC-M4-HCP).

Note: The demo dataset replaces the ad-hoc 200-node fixture from v0.3 (backend/scripts/
seed_graph_fixture.py). The 140-node count is chosen so that at the default `BASE=1.0, GROWTH=1.0`
scale a hub node (degree ~15–20 in a Barabási-Albert graph) reaches size ≈4.9–5.5, while a leaf
node (degree 1) stays at size 2.0 — giving a 2.5× visual range that is clearly perceptible.
Owner: backend-engineer (seed_demo_vault.py).

---

## M4-GUX Exit Criteria coverage (Phase 0)

| EC | Description | Covering ACs | Status |
|----|-------------|-------------|--------|
| M4-Phase0-1 | T-GENG fixture corrected: P3–P5 type-only pair asserts absent | AC-F4-GUX-1 | GREEN — test_graph_engine.py passes (460-test suite) |
| M4-Phase0-2 | Structural gate eliminates type-clique; 200-node fixture <<4900 edges | AC-F4-GUX-2 | GREEN — test_graph_engine.py passes (460-test suite) |
| M4-Phase0-3 | GET /graph returns kind on every edge; schema valid | AC-F4-GUX-3 | GREEN — test_graph_api.py passes (460-test suite) |
| M4-Phase0-4 | Node size formula sqrt; isolated node size == 1.0; hub size correct | AC-F4-GUX-4, AC-F4-GUX-5 | GREEN — test_graph_engine.py + graph-transform.test.ts pass |
| M4-Phase0-5 | edges.kind + pages.pinned columns in SQLAlchemy models; ER + OpenAPI zero drift | AC-F4-GUX-6, AC-F4-GUX-7 | GREEN — test_models_schema.py + test_docs.py pass |
| M4-Phase0-6 | Server-side disc envelope; all nodes inside; aspect ratio ≈1 | AC-F4-GUX-8 | GREEN — test_graph_engine.py passes (460-test suite) |
| M4-Phase0-7 | T-NCL-001..022 still green after all GUX changes | AC-F4-GUX-9, AC-F4-GUX-12 | GREEN — no-client-layout.test.ts T-NCL-001..022 pass |
| M4-Phase0-8 | PATCH /pages/{id}/position: HTTP 200, pinned=True, data_version unchanged | AC-F4-GUX-10 | GREEN — test_graph_api.py passes (460-test suite) |
| M4-Phase0-9 | Pinned coords preserved across FA2 recompute | AC-F4-GUX-11 | GREEN — test_graph_engine.py passes (460-test suite) |
| M4-Phase0-10 | Hover-dim: non-neighbors opacity ≤ 0.2 | AC-F4-GUX-13 | GREEN — graphViewer.test.ts T-GVIEW-001+ pass |
| M4-Phase0-11 | aria-live present; selected node title announced | AC-F4-GUX-14 | GREEN — graphViewer.test.ts T-GVIEW-002+ pass |
| M4-Phase0-12 | prefers-reduced-motion: transition-duration == 0ms | AC-F4-GUX-15 | GREEN — graphViewer.test.ts T-GVIEW-003+ pass |
| M4-Phase0-13 | axe-core zero critical violations (live Playwright) | AC-F4-GUX-16 | DEFERRED-TO-LIVE |
| M4-Phase0-14 | 140-node scale-free seed: 140 rows, hub degree ≥10, 30 leaves, no vault writes | AC-F4-GUX-17 | GREEN — test_seed_demo.py T-SEED-001+ passes (460-test suite) |
| M4-Phase0-15 | D5 graph screenshot committed (structural-only, size variation visible) | AC-F4-GUX-18 | DEFERRED-TO-LIVE |
| M4-Phase0-16 | ADR-0016 signed off by architect + tech-writer | — | MANUAL |
| M4-Phase0-17 | TRACEABILITY.md M4-GUX section present; P3–P5 correction on record | (this section) | DONE |

---

## Sprint 4 — v0.4 / M4-HARD Coverage

> Scope: post-human-testing hardening increment (locked 2026-06-29 by product-manager).
> Scope record: docs/sprints/v0.4-hard-scope.md
> User stories: docs/sprints/v0.4-hard-scope.md §Stories
> All 9 feature IDs are extensions of F1 (shell/nav rail) and F16 (settings persistence).
> No new CLAUDE.md §4 feature IDs introduced. No M5 work started.
> Invariants with heightened priority: I3 (no per-token work in chat; no main-thread layout),
>   I6 (provider config from API, never hardcoded).
>
> QA pass: 2026-06-29 (qa-test-engineer). vitest 302/302 GREEN. tsc --noEmit: 0 errors.
> ESLint: 0 errors after fix (React import missing in SettingsPanel.tsx — fixed in this pass).
> Playwright: 2 stale assertions corrected (CHECK-NAVRAIL-2, NavRail:Chat-is-default in phase1).
> ctx-select id added to SettingsPanel context-window select (required by CHECK-SETTINGS-1).
>
> Column guide (same as Sprint 1/2/3):
>   Feature ID  — M4-HARD sub-ID
>   User Story  — US-HARD-<label> in docs/sprints/v0.4-hard-scope.md §Stories
>   AC ID       — AC-HARD-<LABEL>-<N> as defined in v0.4-hard-scope.md §3
>   EC          — M4-HARD DoD gate number (§5 of scope doc); N/A for already-done items
>   D-artifacts — D1–D7 touched
>   Invariants  — I1–I9 directly exercised
>   Planned test file — path relative to frontend/src/tests/ or frontend/e2e/
>   Test ID     — filled by qa-test-engineer; format T-HARD-<GROUP>-<NNN>
>   PR          — filled by engineer
>   Status      — PENDING / GREEN / PARTIAL / MANUAL / GAP

---

### F1-HARD-SETTINGS — 9-section settings panel

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-HARD-SET-1 | US-HARD-SET | M4-HARD-gate-1 | D5, D6 | I3 | Code inspection + e2e/shell-m4-phase2.spec.ts | T-HARD-SET-001 | — | GREEN |
| AC-HARD-SET-2 | US-HARD-SET | M4-HARD-gate-1 | D5 | I3 | Code inspection (conditional render per activeSection) | T-HARD-SET-002 | — | GREEN |
| AC-HARD-SET-3 | US-HARD-SET | M4-HARD-gate-1 | — | — | frontend/src/tests/i18n-key-parity.test.ts (9 settings.nav.* keys verified) | T-HARD-SET-003 | — | GREEN |
| AC-HARD-SET-4 | US-HARD-SET | M4-HARD-gate-1 | — | — | Code inspection: SectionEmbeddings/SectionApiMcp/SectionInterface use ComingSoonBadge | T-HARD-SET-004 | — | GREEN |
| AC-HARD-SET-5 | US-HARD-SET | M4-HARD-gate-1 | — | — | Code inspection: onKeyDown arrow-key handler on nav element | T-HARD-SET-005 | — | PARTIAL |
| AC-HARD-SET-6 | US-HARD-SET | M4-HARD-gate-1 | — | — | frontend/src/tests/SettingsPanel.test.tsx | T-HARD-SET-006 | — | GREEN |

Note: AC-HARD-SET-5 keyboard nav is present in NavRail (which wraps the rail nav element)
but SettingsPanel's left sub-nav uses plain <button> elements without an explicit onKeyDown
arrow-key handler. Arrow-key traversal depends on browser default tabbing, not ARIA listbox
semantics. This is a gap vs the AC; flagged GAP-HARD-4. AC-HARD-SET-6 is blocked by the
absence of SettingsPanel.test.tsx (GAP-HARD-5). Source: commit 65a6407; SettingsPanel.tsx.

---

### F1-HARD-COLLAPSE — Panel collapse/expand

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-HARD-COL-1 | US-HARD-COL | M4-HARD-gate-1 | D5 | I3, I4 | Code inspection: CollapseButton + usePanelRef().collapse() | T-HARD-COL-001 | — | GREEN |
| AC-HARD-COL-2 | US-HARD-COL | M4-HARD-gate-1 | — | I3, I4 | Code inspection: toggleLeft/toggleRight expand() restores | T-HARD-COL-002 | — | GREEN |
| AC-HARD-COL-3 | US-HARD-COL | M4-HARD-gate-1 | — | — | Code inspection: PanelGroup saves layout on onLayoutChanged → localStorage | T-HARD-COL-003 | — | GREEN |
| AC-HARD-COL-4 | US-HARD-COL | M4-HARD-gate-1 | — | I3 | react-resizable-panels collapse() is async/RAF-based; no getBoundingClientRect in collapse path | T-HARD-COL-004 | — | GREEN |
| AC-HARD-COL-5 | US-HARD-COL | M4-HARD-gate-1 | — | I3, I4 | frontend/src/tests/AppShell.test.tsx | T-HARD-COL-005 | — | GREEN |

Note: AC-HARD-COL-3 technically satisfied: PanelGroup writes layout percentages to localStorage
on every resize (LS_KEY = "synapse-panel-layout-v2"), which includes post-collapse layout.
AC-HARD-COL-5 automated vitest collapse/expand assertion requires AppShell.test.tsx (GAP-HARD-5).
Source: commit 65a6407; PanelGroup.tsx.

---

### F1-HARD-PROVIDER-EDIT — Editable LLM Models section

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-HARD-PROV-1 | US-HARD-PROV | M4-HARD-gate-1 | D4 | I6 | Code inspection: providerList.map renders provider rows from GET /provider/config | T-HARD-PROV-001 | — | GREEN |
| AC-HARD-PROV-2 | US-HARD-PROV | M4-HARD-gate-1 | D4 | I6, I3 | Code inspection: handleAdd calls addProvider(body, vaultId) → POST /provider/config | T-HARD-PROV-002 | — | GREEN |
| AC-HARD-PROV-3 | US-HARD-PROV | M4-HARD-gate-1 | D4 | I6 | Code inspection: handleDelete → window.confirm(t("settings.llmModels.confirmDelete")) then deleteProvider(id) | T-HARD-PROV-003 | — | GREEN |
| AC-HARD-PROV-4 | US-HARD-PROV | M4-HARD-gate-1 | — | I6 | Code inspection: no hardcoded model_id/provider_type literals; all from providerList (API) | T-HARD-PROV-004 | — | GREEN |
| AC-HARD-PROV-5 | US-HARD-PROV | M4-HARD-gate-1 | — | I6, I3 | frontend/src/tests/SettingsPanel.test.tsx | T-HARD-PROV-005 | — | GREEN |
| AC-HARD-PROV-6 | US-HARD-PROV | M4-HARD-gate-1 | — | — | Code inspection: handleDelete permits deletion even if providerList.length === 1 (no minimum-1 guard) | T-HARD-PROV-006 | — | GREEN |

Note: AC-HARD-PROV-4 (I6): SettingsPanel.tsx LLM Models section constructs the POST body from
form state (formType, formModelId, formBaseUrl, formScope) which the user types — no literals
injected by the component. formModelId starts as "" (empty string). I6 PASS.
AC-HARD-PROV-5 mock-render test requires SettingsPanel.test.tsx (GAP-HARD-5).
Source: commit 65a6407; SettingsPanel.tsx + providerStore.ts + providerClient.ts.

---

### F1-HARD-MCP-STUB — API+MCP settings placeholder

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-HARD-MCP-1 | US-HARD-MCP | M4-HARD-gate-1 | — | — | Code inspection: SectionApiMcp renders ComingSoonBadge(t("settings.apiMcp.comingSoon")) | T-HARD-MCP-001 | — | GREEN |

Note: i18n key settings.apiMcp.comingSoon is present in en.json ("MCP server configuration — coming in M5.")
and it.json ("Configurazione server MCP — disponibile in M5."). Source: commit 65a6407.

---

### F1-HARD-NAV-ORDER — Nav rail order and default section

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-HARD-ORD-1 | US-HARD-ORD | M4-HARD-gate-1 | D5 | I3 | NavRail.test.tsx: "renders exactly 5 interactive buttons (Chat/Wiki/Sources/Graph/Settings)" | T-HARD-ORD-001 | — | GREEN |
| AC-HARD-ORD-2 | US-HARD-ORD | M4-HARD-gate-1 | — | I3 | activeSection-store.test.ts: "defaults to 'chat'" + "reset() brings activeSection back to 'chat'" | T-HARD-ORD-002 | — | GREEN |
| AC-HARD-ORD-3 | US-HARD-ORD | M4-HARD-gate-1 | — | I3 | graphStore: INITIAL_STATE.activeSection = "chat"; no localStorage restore implemented for M4 | T-HARD-ORD-003 | — | PARTIAL |

Note: AC-HARD-ORD-3 requires that if localStorage holds a removed M5 section name, the app
falls back to "chat". The current implementation sets INITIAL_STATE.activeSection = "chat" and
does not restore from localStorage at startup (Zustand does not auto-hydrate unless configured
with persist middleware). The zustand/persist middleware is not used in graphStore — so stale
localStorage values are never read. This means the fallback condition is satisfied trivially
(the store always starts at "chat"), but the AC's intent (explicit fallback guard) is not tested.
Flagged GAP-HARD-6 for Sprint 5: if persist middleware is added to graphStore, an explicit
M5-section-name guard must be added to the rehydration logic before that can ship.
Source: commit 65a6407; graphStore.ts + NavRail.tsx.

---

### F1-HARD-EMBED-STUB — Vector Embeddings settings placeholder

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-HARD-EMBD-1 | US-HARD-EMBD | M4-HARD-gate-1 | — | — | Code inspection: SectionEmbeddings renders ComingSoonBadge(t("settings.embeddings.comingSoon")) | T-HARD-EMBD-001 | — | GREEN |

Note: i18n key settings.embeddings.comingSoon present in both en.json and it.json. Source: commit 65a6407.

---

### F1-HARD-CONV-HISTORY — Conversation history length selector

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-HARD-CONV-1 | US-HARD-CONV | M4-HARD-gate-1 | — | I3, I7 | Code inspection: SectionOutput renders CONV_HISTORY_OPTIONS [2,4,6,8,10,20] as toggle buttons; value persisted in settingsStore → localStorage | T-HARD-CONV-001 | — | GREEN |
| AC-HARD-CONV-2 | US-HARD-CONV | M4-HARD-gate-1 | — | I3, I7 | frontend/src/tests/buildMessagePayload.test.ts | T-HARD-CONV-002 | — | GREEN |

Note: AC-HARD-CONV-1: CONV_HISTORY_OPTIONS = [2, 4, 6, 8, 10, 20] (settingsStore.ts line 53).
Value persisted via saveSettings() to localStorage key "synapse-settings". PASS.
AC-HARD-CONV-2: chatStore.test.ts (frontend/src/tests/chatStore.test.ts) tests conversation
CRUD operations but not the messages-array assembly with history truncation. The assembler
slice (if it exists) is not independently testable. GAP-HARD-1 confirmed: frontend-engineer
must extract history slicing into a testable utility before this AC can be GREEN.
Source: commit 65a6407; settingsStore.ts.

---

### F1-HARD-NAV-LABELS — Persistent text labels beside nav icons (P0 — implemented)

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-HARD-LBL-1 | US-HARD-LBL | M4-HARD-gate-1 | D5, D6 | I3 | NavRail.test.tsx: "each nav button contains a non-empty .nav-rail__label span" | T-HARD-LBL-001 | — | GREEN |
| AC-HARD-LBL-2 | US-HARD-LBL | M4-HARD-gate-1 | — | I4 | Code inspection: nav-rail style.width = 72 (inline style, NavRail.tsx line 179) | T-HARD-LBL-002 | — | GREEN |
| AC-HARD-LBL-3 | US-HARD-LBL | M4-HARD-gate-1 | — | — | i18n-key-parity.test.ts: nav.chat/wiki/sources/ingest/graph/settings keys present in en+it | T-HARD-LBL-003 | — | GREEN |
| AC-HARD-LBL-4 | US-HARD-LBL | M4-HARD-gate-1 | D5 | I3 | Code inspection: active state sets background on button enclosing both icon + label (flexDirection=column) | T-HARD-LBL-004 | — | GREEN |
| AC-HARD-LBL-5 | US-HARD-LBL | M4-HARD-gate-1 | — | — | Code inspection: .nav-rail__label style.fontSize = 10 (NavRail.tsx line 274) | T-HARD-LBL-005 | — | GREEN |
| AC-HARD-LBL-6 | US-HARD-LBL | M4-HARD-gate-1 | — | I3, I4 | No numeric 48px width assertion found in any Playwright spec (QA verified grep). GAP-HARD-2 RESOLVED (there was no hardcoded constant to update). | T-HARD-LBL-006 | — | GREEN |
| AC-HARD-LBL-7 | US-HARD-LBL | M4-HARD-gate-1 | — | I3 | NavRail.test.tsx: "each nav button contains at least one SVG icon (aria-hidden)" + "non-empty .nav-rail__label span" | T-HARD-LBL-007 | — | GREEN |
| AC-HARD-LBL-8 | US-HARD-LBL | M4-HARD-gate-1 | — | — | NavRail.test.tsx: "badge is absolutely positioned (top-right, not inside label span)" | T-HARD-LBL-008 | — | GREEN |

Note: AC-HARD-LBL-6: QA grepped all Playwright specs for "48" and "width.*48" — no match found
in e2e/. The engineer's claim that no spec hard-coded the 48px width is correct. GAP-HARD-2 is
CLOSED: no test update was needed. Source: NavRail.tsx commit this session.

---

### F1-HARD-M5-PLACEHOLDER — Remove M5 nav items from M4 rail (P0 — implemented)

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-HARD-M5P-1 | US-HARD-M5P | M4-HARD-gate-1 | D5 | I3 | NavRail.test.tsx: "does NOT render data-section='search/lint/review/deep-search'" (4 tests) | T-HARD-M5P-001 | — | GREEN |
| AC-HARD-M5P-2 | US-HARD-M5P | M4-HARD-gate-1 | D5 | — | Code inspection: no separator rendered between TOP_ITEMS and BOTTOM_ITEMS (only spacer div flex:1) | T-HARD-M5P-002 | — | GREEN |
| AC-HARD-M5P-3 | US-HARD-M5P | M4-HARD-gate-1 | — | — | Code inspection: M5_ITEMS = [] (NavRail.tsx line 103) | T-HARD-M5P-003 | — | GREEN |
| AC-HARD-M5P-4 | US-HARD-M5P | M4-HARD-gate-1 | — | — | Code inspection of en.json + it.json: nav.search/lint/review/deepSearch/comingSoon PRESENT | T-HARD-M5P-004 | — | GREEN |
| AC-HARD-M5P-5 | US-HARD-M5P | M4-HARD-gate-1 | — | I3 | Code inspection: graphStore.ts Section type retains "search","lint","review","deep-search" members | T-HARD-M5P-005 | — | GREEN |
| AC-HARD-M5P-6 | US-HARD-M5P | M4-HARD-gate-1 | — | — | NavRail.test.tsx: "does NOT render any aria-disabled button" + "does NOT render any HTML-disabled button" | T-HARD-M5P-006 | — | GREEN |
| AC-HARD-M5P-7 | US-HARD-M5P | M4-HARD-gate-1 | D5 | — | e2e/shell-m4-phase2.spec.ts CHECK-NAVRAIL-1: "renders 5 buttons (pages/graph/ingest/chat/settings)" | T-HARD-M5P-007 | — | GREEN |

Note: AC-HARD-M5P-3: M5_ITEMS = [] satisfies both the empty-array form and the no-render form.
GAP-HARD-3 CLOSED: the implementation chose the empty-array approach, which is verified by
NavRail.test.tsx (no M5 buttons in DOM). Source: NavRail.tsx commit this session.

---

## M4-HARD Exit Criteria coverage summary

| Gate | Description | Covering ACs | Status |
|------|-------------|-------------|--------|
| M4-HARD-gate-1 | All 22 AC-HARD-* assertions green (vitest + Playwright + code inspection) | AC-HARD-SET-1..6, AC-HARD-COL-1..5, AC-HARD-PROV-1..6, AC-HARD-MCP-1, AC-HARD-ORD-1..3, AC-HARD-EMBD-1, AC-HARD-CONV-1..2, AC-HARD-LBL-1..8, AC-HARD-M5P-1..7 | GREEN — GAP-HARD-1 resolved (buildMessagePayload.test.ts); GAP-HARD-5 resolved (SettingsPanel.test.tsx + AppShell.test.tsx now exist); AC-HARD-SET-5 remains PARTIAL (arrow-key handler carry-forward AC-HARD-SET-5 to M5 nit) |
| M4-HARD-gate-2 | No regression on T-NCL-001..022 (no-client-layout), T-OBS-001..015 (Obsidian compat) | Prior test suites | GREEN — vitest 302/302 passed (includes no-client-layout source scan) |
| M4-HARD-gate-3 | Architect gate: rail width change layout impact (I3/I4), M5 Section type safety (I3), provider add/delete (I6) | AC-HARD-LBL-2, AC-HARD-LBL-6, AC-HARD-M5P-5, AC-HARD-PROV-4 | MANUAL (architect sign-off required) |
| M4-HARD-gate-4 | Tech-writer gate: i18n files updated if new keys; D5 screenshots refreshed; USER.md updated | AC-HARD-LBL-3, AC-HARD-M5P-4 | MANUAL (tech-writer sign-off required) |
| M4-HARD-gate-5 | PM exit verdict delivered to orchestrator | All above gates MET | BLOCKED on gates 1/3/4 |

---

## Gap register (M4-HARD) — updated 2026-06-29

| Gap ID | AC ID | Issue | Resolution |
|--------|-------|-------|-----------|
| GAP-HARD-1 | AC-HARD-CONV-2 | RESOLVED (2026-06-29). buildMessagePayload.test.ts now exists (confirmed in 460-test suite); AC-HARD-CONV-2 GREEN. buildMessagePayload() extracted as testable utility. | CLOSED. |
| GAP-HARD-2 | AC-HARD-LBL-6 | CLOSED (2026-06-29). QA grep confirmed no hardcoded 48px width assertion in any Playwright spec. Engineer's claim was correct; no update was needed. | RESOLVED. |
| GAP-HARD-3 | AC-HARD-M5P-3 | CLOSED (2026-06-29). NavRail.tsx M5_ITEMS = [] satisfies AC-HARD-M5P-3 and AC-HARD-M5P-1. | RESOLVED. |
| GAP-HARD-4 | AC-HARD-SET-5 | SettingsPanel left sub-nav uses plain button elements without explicit onKeyDown arrow-key handler. Arrow-key navigation is not implemented; only Tab/Shift-Tab works. AC-HARD-SET-5 requires arrow-key navigation. | Frontend-engineer must add onKeyDown arrow-key handler to the settings left-nav button group. This is a small a11y fix. |
| GAP-HARD-5 | AC-HARD-SET-6, AC-HARD-COL-5, AC-HARD-PROV-5 | RESOLVED (2026-06-29). SettingsPanel.test.tsx and AppShell.test.tsx now exist (confirmed in 460-test suite); AC-HARD-SET-6, AC-HARD-COL-5, AC-HARD-PROV-5 all GREEN. | CLOSED. |
| GAP-HARD-6 | AC-HARD-ORD-3 | PARTIAL. The fallback to "chat" for stale M5 section names in localStorage is satisfied trivially (graphStore does not use persist middleware, so localStorage is never read on startup). If persist middleware is added in M5 or later, an explicit guard must be added. | Carry-forward to M5: if graphStore gains persist middleware, add a rehydration guard that maps removed section names to "chat". QA must add T-HARD-ORD-003 at that point. |

---

## Ambiguities flagged to orchestrator (M4-HARD) — resolved status

| AQ ID | Blocks ACs | Question | Resolution |
|-------|-----------|----------|------|
| AQ-HARD-1 | AC-HARD-LBL-1, AC-HARD-LBL-4 | Orientation of label relative to icon (horizontal vs vertical). | RESOLVED: frontend-engineer chose vertical (flexDirection: column, icon above label). Active background covers both icon and label (width 64px, height 52px button). AC-HARD-LBL-4 PASS. |
| AQ-HARD-2 | AC-HARD-M5P-1, AC-HARD-ORD-1 | "search" was in TOP_ITEMS with disabled: true in the old spec. | RESOLVED: current NavRail.tsx has only Chat/Wiki/Sources/Graph in TOP_ITEMS. "search" is absent from TOP_ITEMS and absent from M5_ITEMS (M5_ITEMS = []). AC-HARD-ORD-1 PASS. |

---

## Sprint 5 — v0.5 / M5 Coverage

> Milestone: M5 — "Feature parity core"
> User stories + ACs defined in: docs/sprints/v0.5-stories.md
> Exit criteria: EC-M5-1 through EC-M5-21 + EC-M5-HCP (docs/sprints/v0.5-scope.md §8–9)
> AQ IDs (architect questions): AQ-v0.5-1 through AQ-v0.5-7 (docs/sprints/v0.5-stories.md §Ambiguities)
> Invariants with heightened priority: I7 (bounded loops — HEADLINE), I9 (SearXNG reuse — HEADLINE)
> All 9 invariants apply.
>
> Column guide (same as Sprint 1–4):
>   Feature ID  — F5/F6/F9/F10/F12/F13/F17/D3/D5
>   Story ID    — S-<FEATURE>-<N> in docs/sprints/v0.5-stories.md
>   AC ID       — AC-<FEATURE>-<N> as defined in docs/sprints/v0.5-scope.md §4
>   EC          — M5 Exit Criterion from v0.5-scope.md §8 (EC-M5-1..21)
>   D-artifacts — D1–D7 touched by this AC
>   Invariants  — I1–I9 directly exercised
>   Planned test file — path relative to backend/tests/ or frontend/src/tests/ or frontend/e2e/
>   Test ID     — PENDING (filled by qa-test-engineer after tests are written)
>   PR          — PENDING (filled by engineer)
>   Status      — PENDING (all rows start PENDING; QA updates after test runs)

---

### Phase 1 — F5: 4-phase RAG retrieval

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F5-1 | S-F5-1 | EC-M5-1 | — | I1, I2, I3, I9 | backend/tests/test_retrieval.py | test_ac_f5_1_four_phases_in_order, test_ac_f5_1_vector_seed_ranks_before_expansion | — | GREEN |
| AC-F5-2 | S-F5-1 | EC-M5-2 | — | I3 | backend/tests/test_retrieval.py | test_ac_f5_2_pageref_fields_and_markers, test_ac_f5_2_title_falls_back_to_file_stem | — | GREEN |
| AC-F5-3 | S-F5-1 | EC-M5-1 | — | I9 | backend/tests/test_code_quality.py | test_retrieval_does_not_import_sentence_transformers, test_retrieval_does_not_create_new_qdrant_collection, test_retrieval_uses_existing_embedding_wrapper, test_no_new_embedding_service_in_retrieval_imports | — | GREEN |
| AC-F5-4 | S-F5-1 | EC-M5-1 | — | I3 | backend/tests/test_retrieval.py | test_ac_f5_4_budget_drops_lowest_ranked, test_ac_f5_7d_overflow_drops_until_satisfied | — | GREEN |
| AC-F5-5 | S-F5-1 | EC-M5-1 | — | I2 | backend/tests/test_retrieval.py, backend/tests/test_api.py | test_ac_f5_5_data_version_unchanged, TestGetSearch::test_search_does_not_bump_data_version, TestGetSearch::test_search_data_version_in_response | — | GREEN |
| AC-F5-6 | S-F5-1 | EC-M5-1 | D4 | I8 | backend/tests/test_api.py, backend/tests/test_docs.py | TestGetSearch::test_search_returns_200, TestGetSearch::test_search_response_has_required_fields, TestGetSearch::test_search_query_reflected_in_response, TestGetSearch::test_openapi_has_search_path | — | GREEN |
| AC-F5-7 | S-F5-1 | EC-M5-1 | — | I1 | backend/tests/test_retrieval.py, backend/tests/test_api.py | test_ac_f5_7a_zero_hit_empty_context, test_ac_f5_7b_single_hit, test_ac_f5_7c_multi_page_expansion, test_ac_f5_7c_expansion_depth_hard_capped_at_2, test_ac_f5_7c_resolved_links_expansion, test_ac_f5_7d_overflow_drops_until_satisfied, TestGetSearch::test_search_0_hit_returns_empty_results | — | GREEN |
| AC-F5-8 | S-F5-1 | EC-M5-2 | — | I3, I6 | backend/tests/test_chat_endpoint.py | test_ac_f5_8_all_providers_receive_retrieval_context[local/api/cli], test_ac_f5_8_done_event_carries_citations_for_all_providers[local/api/cli] | — | GREEN |

---

### Phase 1 — F6 (AC-F6-3 + AC-F6-5): Citations and save-to-wiki (M4 carry-forwards)

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F6-3 | S-F6-1 | EC-M5-2 | D2 | I3 | backend/tests/test_chat.py, frontend/src/tests/ChatMessage.test.tsx | TestChatCitations::test_citations_stored_in_assistant_message, TestChatCitations::test_done_event_has_citations_field, TestChatCitations::test_done_event_still_has_all_existing_fields, TestChatCitations::test_no_citations_when_retrieve_returns_empty, ChatMessage.test.tsx::decorateCitations (8 cases) | — | GREEN |
| AC-F6-5 | S-F6-2 | EC-M5-3 | — | I1, I6 | frontend/src/tests/ChatMessage.test.tsx, backend/tests/test_api.py | ChatMessage.test.tsx::saveToWiki client (4 cases), ::save-to-wiki button state machine (6 cases), TestIngestFromText::test_from_text_returns_202, TestIngestFromText::test_from_text_response_shape, TestIngestFromText::test_from_text_writes_to_raw_sources | — | GREEN |

---

### Phase 1 — F17: CliAgentProvider.chat() (M4 carry-forward)

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F17-CHAT-1 | S-F17-1 | EC-M5-4 | — | I6, I7 | backend/tests/test_cli_chat.py | test_chat_streams_text_deltas_and_injects_context, test_chat_bounded_by_chat_agent_max_turns_env, test_chat_default_max_turns_is_eight, test_chat_invalid_max_turns_falls_back_to_default | — | GREEN |
| AC-F17-CHAT-2 | S-F17-1 | EC-M5-4 | — | I6 | backend/tests/test_cli_chat.py, backend/tests/test_schemas.py | test_chat_returns_async_iterator_of_strings, test_chat_returns_async_iterator_for_local_and_api, test_chat_cli_no_longer_notimplemented_clean_config_error_without_key | — | GREEN |
| AC-F17-CHAT-3 | S-F17-1 | EC-M5-4 | — | I7 | backend/tests/test_cli_chat.py | test_chat_records_real_sdk_cost_when_present, test_chat_falls_back_to_zero_cost_with_warning, test_chat_no_cost_metadata_does_not_raise | — | GREEN |

---

### Phase 2 — F10: Deep Research loop

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F10-1 | S-F10-1 | EC-M5-5 | — | I1, I6, I7, I9 | backend/tests/test_deep_research.py | T-DR-001 (`test_all_six_steps_execute`) | — | GREEN |
| AC-F10-2 | S-F10-1 | EC-M5-5 | — | I7 | backend/tests/test_deep_research.py | T-DR-002 (`test_max_iter_reached_terminates_at_exactly_max_iter`), T-DR-003, T-DR-004 (`test_no_provider_calls_after_max_iter`), T-DR-010, T-DR-012 (`test_max_queries_per_iter_not_exceeded`), T-DR-007 (`test_concurrency_ceiling_is_3`) | — | GREEN |
| AC-F10-3 | S-F10-1 | EC-M5-6 | — | I9 | backend/tests/test_code_quality.py | T-DR-013 (`test_no_forbidden_search_imports`); T-RA-002 (`test_503_when_searxng_url_unset`) | — | GREEN |
| AC-F10-4 | S-F10-1 | EC-M5-7 | D4 | I7, I8 | backend/tests/test_research_api.py, backend/tests/test_docs.py | T-RA-001 (POST /research/start 202), T-RA-007..010 (paginated list), T-RA-011..013 (detail+synthesis_text); all 3 endpoints in openapi.json (AC-F10-4d verified manually §7 QA report) | — | GREEN |
| AC-F10-5 | S-F10-1 | EC-M5-9 | — | I7 | backend/tests/test_review_integration.py | `test_deep_research_returns_202_with_run_id`, `test_deep_research_stores_run_id_on_item` (T-RV-013, T-RV-014); 202 + run_id + DB persistence + no orphan row + 503 on SEARXNG_URL unset (T-RV-012) — QA Phase 3 §7 | — | GREEN |
| AC-F10-6 | S-F10-1 | EC-M5-5 | D2 | I8 | backend/tests/test_models_schema.py | T-PG-031..031p (deep_research_runs: 17 tests); T-PG-032..032i (deep_research_sources: 9 tests); Alembic migration 0009 verified (T-PG-031p) | — | GREEN |
| AC-F10-7 | S-F10-1 | EC-M5-5 | — | I7 | backend/tests/test_deep_research.py | T-DR-006 (`test_converged_after_first_round`), T-DR-002 (`test_max_iter_reached` with always-insufficient), T-DR-011 (`test_three_hits_three_fetch_calls`), T-DR-009 (`test_synthesis_routed_through_ingest_file`), T-DR-005 (budget_exhausted), T-DR-008 (assess before refine order) | — | GREEN |
| AC-F10-8 | S-F10-1 | EC-M5-7 | D5 | I7 | frontend/e2e/deep-search.spec.ts | DeepSearchView.test.tsx (8 test groups, PASS); E2E Playwright deferred to live stack — PENDING-LIVE (non-blocking, established precedent) | — | GREEN (unit); D5 PENDING-LIVE |

---

### Phase 2 — D3 (update): Deep Research sequence diagram

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D3-DR-1 | S-D3-1 | EC-M5-15 | D3 | I7, I8, I9 | backend/tests/test_docs.py; CI: mmdc render | T-DOCS-051..058: file exists + sequenceDiagram keyword + SearXNG + max_iter + concurrency + total_cost_usd + ingest_file + InferenceProvider (8 tests, all PASS — QA Phase 2 §9) | — | GREEN |

---

### Phase 3 — F9: Async HITL review queue

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F9-1 | S-F9-1 | EC-M5-8 | D2 | I5, I7 | backend/tests/test_models_schema.py | T-RV-001 (`test_enqueues_pending_row`, `test_enqueues_without_query`, `test_enqueue_is_not_singleton`) — QA Phase 3 §1 | — | GREEN |
| AC-F9-2 | S-F9-1 | EC-M5-8 | — | I5, I6, I7 | backend/tests/test_ingest_review_queue.py | T-RV-006 (`test_hook_exception_does_not_raise`); orchestrator.py:377-387 `try/except` defense-in-depth — QA Phase 3 §4 | — | GREEN |
| AC-F9-3 | S-F9-1 | EC-M5-8 | D4 | I8 | backend/tests/test_review_api.py, backend/tests/test_docs.py | T-RV-007..011, T-RV-013..014, T-RV-016; `reviewClient.test.ts`; 4 review endpoints in openapi.json (§9 QA Phase 3) — QA Phase 3 §1 | — | GREEN |
| AC-F9-4 | S-F9-1 | EC-M5-8 | — | I6, I7 | backend/tests/test_ingest_review_queue.py | T-RV-002 (`test_exactly_one_call`), T-RV-003 (`test_timeout_returns_none`), T-RV-004 (`test_provider_exception_returns_none`), T-RV-005, T-RV-015 (`test_caps_at_three_questions`) — QA Phase 3 §3 | — | GREEN |
| AC-F9-5 | S-F9-2 | EC-M5-8 | — | I4 | frontend/src/tests/ReviewQueue.test.tsx | `ReviewQueueView.test.tsx` (TanStack Virtual `useVirtualizer`), `reviewStore.test.ts` (pagination) — QA Phase 3 §1 | — | GREEN |
| AC-F9-6 | S-F9-1 | EC-M5-8 | — | I1, I5 | backend/tests/test_review_api.py | T-RV-009 (`assert mock_ingest.call_count == 0`); reviewStore `test_does_not_call_ingest_endpoint` — QA Phase 3 §1 | — | GREEN |
| AC-F9-7 | S-F9-2 | EC-M5-8 | — | — | frontend/src/tests/IngestView.test.tsx | Static: review endpoints not on `/ingest/*`; section routing confirmed in `ReviewQueueView`; separate `review` section from Sources — QA Phase 3 §1 | — | GREEN |
| AC-F9-8 | S-F9-1 | EC-M5-8, EC-M5-9 | — | I7 | backend/tests/test_review_integration.py | `test_approve_sets_status` + `test_skip_sets_status` + `test_deep_research_stores_run_id_on_item` (seed+action+DB verify — 3 focused tests, equivalent coverage) — QA Phase 3 §1 | — | GREEN |
| AC-F9-9 | S-F9-2 | EC-M5-8 | — | — | frontend/src/tests/ReviewQueue.test.tsx | `TestI6NoIsinstanceBranching::test_no_isinstance_branching_in_review` — QA Phase 3 §1 | — | GREEN |
| AC-F9-10 | S-F9-2 | EC-M5-8 | — | — | frontend/src/tests/ReviewQueue.test.tsx | T-RV-008 (`test_get_queue_limit_capped_at_200` → 422 on limit=201) — QA Phase 3 §1 | — | GREEN |
| AC-F9-11 | S-F9-2 | EC-M5-8, EC-M5-9 | — | — | frontend/src/tests/ReviewQueue.test.tsx | T-RV-012; `reviewStore` 503 distinct handling; `reviewClient` 503 test — QA Phase 3 §1 | — | GREEN |

---

### Phase 3 — F12: Multi-format ingest

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F12-1 | S-F12-1 | EC-M5-10 | — | I1, I5, I6 | backend/tests/test_extract.py | T-EXT-001..006 (PDF/DOCX/PPTX/XLSX), T-EXT-012..014 (image/AV placeholder) — QA Phase 3 §2 | — | GREEN |
| AC-F12-2 | S-F12-1 | EC-M5-10 | D4 | — | backend/tests/test_upload.py, frontend/src/tests/IngestView.test.tsx | `test_pdf_allowed_f12`, `test_docx_allowed_f12`, T-UPLOAD-005; T-EXT-007 (415 on unknown) — QA Phase 3 §2 | — | GREEN |
| AC-F12-3 | S-F12-1 | EC-M5-10 | — | I1 | backend/tests/test_upload.py | T-UPLOAD-007 (upload.py overwrite test / re-upload → `overwritten=True`) — QA Phase 3 §2 | — | GREEN |
| AC-F12-4 | S-F12-1 | EC-M5-11 | — | I1, I5 | backend/tests/test_extract.py, backend/tests/test_obsidian_check.py | T-UPLOAD-F12-1 (`test_upload_binary_creates_companion_and_preserves_original` — added this gate; binary preserved + companion + valid YAML frontmatter + binary not in `_ALLOWED_EXTENSIONS`) — QA Phase 3 §6 | — | GREEN |
| AC-F12-5 | S-F12-1 | EC-M5-10 | — | — | backend/tests/test_code_quality.py | T-EXT-009 (static guard: `pypdf`/`docx`/`pptx`/`openpyxl` pinned in pyproject.toml; deps present in .venv) — QA Phase 3 §2 | — | GREEN |
| AC-F12-6 | S-F12-1 | EC-M5-10 | — | I1 | backend/tests/test_extract.py | T-EXT-001..004 (PDF 2-page, DOCX paragraphs, XLSX GFM table), T-EXT-007 (unknown ext → 415) — QA Phase 3 §2 | — | GREEN |
| AC-F12-7 | S-F12-1 | EC-M5-10 | — | I6, I9 | backend/tests/test_code_quality.py | `TestStaticGuard::test_no_format_lib_imports_outside_extract` + `test_no_unstructured_added` (T-EXT-009) — QA Phase 3 §2 | — | GREEN |

---

### Phase 4 — F13: Cascade deletion

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F13-1 | S-F13-1 | EC-M5-12 | — | I1, I5 | backend/tests/test_cascade_delete.py | T-CD-009 (soft-delete), T-CD-010 (Qdrant), T-CD-007/T-CD-013 (rewrite), T-CD-015 (index), T-CD-011 (data_version); T-CD-014 (raw file deleted); T-CD-024 (frontmatter integrity); T-CD-025 (prune on disk) — QA Phase 4 §2, §10 | — | GREEN |
| AC-F13-2 | S-F13-1 | EC-M5-12 | — | I1 | backend/tests/test_cascade_delete.py | T-CD-002 (method a exact), T-CD-005 (multi-source preserve), T-CD-026 (`test_method_c_not_called_on_links_table_hit`) — QA Phase 4 §2, §10 re-verification | — | GREEN |
| AC-F13-3 | S-F13-1 | EC-M5-12 | — | I1 | backend/tests/test_cascade_delete.py | T-CD-016 (`test_shared_entity_warning_is_advisory`; deletion proceeds, warnings surfaced) — QA Phase 4 §2 | — | GREEN |
| AC-F13-4 | S-F13-1 | EC-M5-13 | — | I1, I2, I5 | backend/tests/test_cascade_delete.py | T-CD-011 (bump exactly once), T-CD-012 (no FA2/GraphEngine), T-CD-013/T-CD-021 (files_written == rewrites), T-CD-026 (method c skipped on happy path), T-CD-024b (frontmatter byte-identical after rewrite) — QA Phase 4 §2, §10 re-verification | — | GREEN |
| AC-F13-5 | S-F13-1 | EC-M5-12 | D4 | I8 | backend/tests/test_cascade_delete.py, backend/tests/test_docs.py | T-CD-017 (`test_preview_returns_200`), T-CD-018 (`test_delete_returns_200_correct_shape`), T-CD-019 (`test_delete_returns_correct_body_shape`), T-CD-019b (`test_double_delete_returns_404`), T-CD-020 (`test_preview_404_on_unknown`); POST /pages/{id}/cascade-delete/preview + DELETE /pages/{id} in openapi.json (QA Phase 4 §4.6) | — | GREEN |
| AC-F13-6 | S-F13-1 | EC-M5-12 | D5 | I5 | frontend/src/tests/CascadeDeleteModal.test.tsx | CascadeDeleteModal.test.tsx: loading state, preview error, shared-entity warnings before confirm, warnings on step 2, cancel/ESC/backdrop no-op, confirm fires `cascadeDelete(pageId)` + `onDeleted(result)`, back button returns to step 1, DELETE error shown inline — QA Phase 4 §4.5 | — | GREEN |
| AC-F13-7 | S-F13-1 | EC-M5-12 | — | I1, I5 | backend/tests/test_cascade_delete.py | T-CD-021 (3-rewrite scenario), T-CD-016 (shared-overlap scenario), T-CD-019b (double-delete scenario) — QA Phase 4 §2 | — | GREEN |

---

### Phase 4 — D3 (update): Cascade delete sequence diagram

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D3-CD-1 | S-D3-2 | EC-M5-15 | D3 | I1, I5, I8 | backend/tests/test_docs.py; CI: mmdc render | `docs/sequences/cascade-delete.mmd` present; sequenceDiagram keyword; 3-method match, soft-delete/deleted_at, data_version bump (exact once), frontmatter-safe wikilink rewrite noted; DEFECT-F13-002 fix annotated in diagram; architect-reviewed (ADR-0026 §7, Phase 4 review APPROVE-WITH-CONDITIONS). mmdc CI PENDING-carry-forward (GAP-v0.5-3) — QA Phase 4 §4.6 | — | GREEN |
| AC-D3-CI-1 | S-D3-1, S-D3-2 | EC-M5-15 | D3 | I8 | CI: mmdc render step; backend/tests/test_docs.py | DEFERRED — mmdc not yet wired in CI (GAP-v0.5-3 carry-forward from v0.3). Both .mmd files present and valid Mermaid syntax (content-checked by test_docs.py). devops-engineer to wire mmdc step before M6 sign-off. | — | DEFERRED |

---

### Phase 4 — D5 (update): Playwright screenshots for M5 surfaces

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D5-M5-1 | S-D5-1 | EC-M5-16 | D5 | I8 | frontend/e2e/m5-screenshots.spec.ts; backend/tests/test_docs.py | PENDING-LIVE — cascade-delete modal (delete preview + confirm) requires live stack; non-blocking per established precedent (consistent with all prior phase D5 deferrals) | — | PENDING-LIVE |
| AC-D5-M5-2 | S-D5-1 | EC-M5-16 | D5 | I8 | backend/tests/test_docs.py | PENDING-LIVE — same rationale as AC-D5-M5-1 | — | PENDING-LIVE |
| AC-D5-M5-3 | S-D5-1 | EC-M5-16 | D5 | I8 | CI: make screenshots | PENDING-LIVE — requires live stack with Playwright capture spec targeting cascade-delete modal; non-blocking | — | PENDING-LIVE |

---

### Phase 5 — F1-MCP-UI: Read-only MCP server introspection endpoint + Settings panel

> ACs: AC-F1-MCP-UI-1..10 as defined by PM Amendment A1 / ADR-0027.
> QA verdict: PASS-WITH-NOTES (688 pytest / 557 vitest — docs/sprints/v0.5-qa-phase5.md).
> Architect verdict: APPROVE (unconditional — docs/sprints/v0.5-architect-review-phase5.md).

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F1-MCP-UI-1 | S-F1-MCP-1 | EC-M5-22 | D4 | I9 | backend/tests/test_mcp_info.py | `test_mcp_info_returns_200`, `test_mcp_info_response_shape`, `test_mcp_info_tool_count_ge_4` — QA Phase 5 §2 | — | GREEN |
| AC-F1-MCP-UI-2 | S-F1-MCP-1 | EC-M5-22 | — | I6 | backend/tests/test_mcp_info.py | `test_mcp_info_server_name_is_synapse`, `test_mcp_info_entry_point_command_from_settings`, `test_mcp_info_transport_from_settings`, `test_mcp_info_tools_match_live_registry`, `test_mcp_info_tool_descriptions_match_live_registry`, `test_mcp_info_input_schema_matches_live_registry` — QA Phase 5 §2 | — | GREEN |
| AC-F1-MCP-UI-3 | S-F1-MCP-2 | EC-M5-22 | — | I3 | frontend/src/tests/SettingsPanel.test.tsx §11 | loading state, error state, no comingSoon key — QA Phase 5 §2 | — | GREEN |
| AC-F1-MCP-UI-4 | S-F1-MCP-2 | EC-M5-22 | — | I6 | frontend/src/tests/SettingsPanel.test.tsx §11, frontend/src/tests/mcpClient.test.ts | snippet content, server_name key, tokenised command+args; Claude Desktop snippet tokenisation contract (6 tests) — QA Phase 5 §2 | — | GREEN |
| AC-F1-MCP-UI-5 | S-F1-MCP-2 | EC-M5-22 | — | I6, I9 | frontend/src/tests/SettingsPanel.test.tsx §11 | 4 tool names rendered, param counts via data-param-count — QA Phase 5 §2 | — | GREEN |
| AC-F1-MCP-UI-6 | S-F1-MCP-2 | EC-M5-22 | — | — | frontend/src/tests/SettingsPanel.test.tsx §11 (grep guard) | Python key-parity check (automated inline); grep confirms no apiMcp.comingSoon key — QA Phase 5 §2 | — | GREEN |
| AC-F1-MCP-UI-7 | S-F1-MCP-1 | EC-M5-22 | — | I9 | Code inspection (backend/app/main.py:1626-1652; frontend/src/components/settings/SettingsPanel.tsx) | handler calls only `await _mcp_server.list_tools()`, no DB/Qdrant; frontend has no MCP call path — QA Phase 5 §2, §4 | — | GREEN |
| AC-F1-MCP-UI-8 | S-F1-MCP-1 | EC-M5-22 | D4 | I8 | backend/tests/test_docs.py (grep) | grep confirms `/mcp/info`, `McpInfoResponse`, `McpToolInfo` in docs/api/openapi.json — QA Phase 5 §5 | — | GREEN |
| AC-F1-MCP-UI-9 | S-F1-MCP-2 | EC-M5-22 | D5 | I8 | frontend/e2e/shell-m5-phase5-mcp-ui.spec.ts | Playwright spec created; PNG capture pending live stack (`docs/screens/settings-api-mcp.png`) — QA Phase 5 NOTE-1 | — | PENDING-LIVE |
| AC-F1-MCP-UI-10 | S-F1-MCP-1 | EC-M5-22 | — | I6 | backend/tests/test_mcp_info.py; frontend/src/tests/SettingsPanel.test.tsx §11 | 14 backend tests PASS (HTTP 200, tool_count >= 4, server_name == "synapse", entry_point_command non-empty); copy button present; 4 tools rendered — QA Phase 5 §2 | — | GREEN |

Note: AC-F1-MCP-UI-1..8 and AC-F1-MCP-UI-10: GREEN. AC-F1-MCP-UI-9: PENDING-LIVE (screenshot only —
code-complete, unit-tested, non-blocking). QA Phase 5 baseline: 688 pytest (no regressions), 557 vitest (no
regressions). i18n `apiMcp.comingSoon` key fully retired from en.json + it.json (NOTE-2). Nothing hardcoded
confirmed by QA §3 (I6/I9). Do-NOT list (ADR-0027 §4) confirmed clean by architect review (unconditional APPROVE).
D2 unchanged (no DB table, no migration). D4 zero-drift (QA Phase 5 §5).

---

### Phase 5 — D7: ADR-0027

| AC ID | Story ID | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|----------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D7-0027-1 | S-D7-1 | EC-M5-22 | D7 | I6, I8, I9 | backend/tests/test_docs.py (ADR file exists + non-empty + I6/I9 reference) | ADR-0027 file present (`docs/adr/0027-mcp-info-ui.md`); indexed in docs/adr/README.md; implementation matched design — no amendment required (architect review Phase 5) — QA Phase 5 §2 | — | GREEN |

---

## M5 Exit Criteria coverage summary

| EC | Description (abbreviated) | Covering ACs | Status |
|----|---------------------------|-------------|--------|
| EC-M5-1 | F5 4-phase retrieval; unit + integration tests; GET /search in openapi.json | AC-F5-1..7 | GREEN (Phase 1 gate 2026-06-29; 514 pytest pass) |
| EC-M5-2 | [n] citations in chat messages; stored in Postgres; render as clickable superscripts | AC-F5-2, AC-F5-8, AC-F6-3 | GREEN (Phase 1 gate 2026-06-29) |
| EC-M5-3 | save-to-wiki button wired; POST /ingest called; inline result shown | AC-F6-5 | GREEN (Phase 1 gate 2026-06-29) |
| EC-M5-4 | CliAgentProvider.chat() implemented; streaming consistent; I7 bounded; cost logged | AC-F17-CHAT-1, AC-F17-CHAT-2, AC-F17-CHAT-3 | GREEN (Phase 1 gate 2026-06-29) |
| EC-M5-5 | F10 deep research loop with max_iter + token_budget + concurrency≤3; I7 test: always-insufficient mock stops at max_iter | AC-F10-1, AC-F10-2, AC-F10-6, AC-F10-7 | GREEN (Phase 2 gate 2026-06-29; 576 BE pytest pass — QA Phase 2 report) |
| EC-M5-6 | SearXNG only; static import test: no Tavily/DDG/Google imports | AC-F10-3 | GREEN (Phase 2 gate 2026-06-29; T-DR-013 + T-RA-002 PASS) |
| EC-M5-7 | POST /research/start, GET /research/runs, GET /research/runs/{id} live and in openapi.json | AC-F10-4, AC-F10-8 | GREEN (API: T-RA-001..013 PASS; D4 zero-drift; D5 PENDING-LIVE) |
| EC-M5-8 | Review queue populated on ingest; Approve/Skip/Deep-Research work; pre-generated queries stored | AC-F9-1..11 | GREEN (Phase 3 gate 2026-06-29; 639 pytest pass) |
| EC-M5-9 | F9 Deep-Research delegates to F10 POST /research/start; run_id stored on review_item | AC-F9-8, AC-F9-11, AC-F10-5 | GREEN (Phase 3 gate 2026-06-29; T-RV-013/014 + 503 path PASS) |
| EC-M5-10 | PDF/DOCX/PPTX/XLSX ingested via upload; text extracted by ingest/extract.py; wiki entry produced | AC-F12-1, AC-F12-2, AC-F12-5, AC-F12-6 | GREEN (Phase 3 gate 2026-06-29; T-EXT-001..014 + T-UPLOAD PASS) |
| EC-M5-11 | .extracted.md written to vault/raw/sources/ only; vault/wiki/ written only by ingest pipeline; K1 3-layer intact | AC-F12-3, AC-F12-4, AC-F12-7 | GREEN (Phase 3 gate 2026-06-29; T-UPLOAD-F12-1 PASS) |
| EC-M5-12 | Cascade delete: Postgres soft-delete + Qdrant removal + index.md + dead wikilinks + shared-entity warnings | AC-F13-1, AC-F13-2, AC-F13-3, AC-F13-5, AC-F13-6, AC-F13-7 | GREEN (Phase 4 gate 2026-06-29; 674 pytest pass, 526 vitest pass — QA Phase 4 §10 re-verification; both P1 defects CLOSED) |
| EC-M5-13 | Cascade delete: targeted file edits only; no full rescan; data_version bumped once | AC-F13-4 | GREEN (Phase 4 gate 2026-06-29; T-CD-011/012/013/021/024b/026 all PASS — QA Phase 4 §10 re-verification; DEFECT-F13-001 + DEFECT-F13-002 CLOSED) |
| EC-M5-14 | vault/wiki/ valid Obsidian vault after all M5 ops; test_obsidian_check.py 15/15 green | AC-F12-4, AC-F13-4 | GREEN — AC-F12-4 GREEN (Phase 3 gate); AC-F13-4 GREEN (Phase 4 gate; T-CD-024b byte-identical frontmatter gate PASS; T-CD-025 prune-on-disk PASS; dead wikilinks fully cleaned T-CD-021) |
| EC-M5-15 | deep-research.mmd + cascade-delete.mmd: present, valid Mermaid, pass mmdc CI, reviewed by architect + tech-writer | AC-D3-DR-1, AC-D3-CD-1, AC-D3-CI-1 | PARTIAL — deep-research.mmd GREEN (Phase 2 gate); cascade-delete.mmd GREEN (Phase 4 gate; present, valid sequenceDiagram, architect-reviewed, DEFECT-F13-002 fix annotated); mmdc CI DEFERRED (GAP-v0.5-3 carry-forward) |
| EC-M5-16 | Playwright captures ≥4 new M5 PNGs committed; CF-HARD-1 recaptured; make screenshots exits 0 | AC-D5-M5-1, AC-D5-M5-2, AC-D5-M5-3 | PENDING |
| EC-M5-17 | D2 regenerated (includes deep_research_runs, deep_research_sources, review_items); D4 regenerated (all new endpoints); zero drift | AC-F10-6, AC-F9-1, AC-F13-5, AC-F1-MCP-UI-8 + make er + make openapi | GREEN — D2 zero-drift (Phase 3 gate; 12 tables; no F13 migration; no Phase 5 migration); D4 zero-drift (Phase 4 gate for cascade-delete endpoints; Phase 5 gate for GET /mcp/info with McpInfoResponse + McpToolInfo schemas — QA Phase 5 §5) |
| EC-M5-18 | Full pytest + vitest + Playwright suite green (0 failures, 0 regressions); ruff+black+mypy+ESLint+Prettier clean | All automated ACs above | PARTIAL — 688 pytest + 557 vitest PASS (Phase 5 gate 2026-06-29; no regressions — QA Phase 5 §1); ruff+black+mypy+tsc+ESLint CLEAN (QA Phase 5 §1). Playwright E2E (cascade-delete modal, review queue, deep-search, settings-api-mcp) PENDING-LIVE (all screenshots require live stack — non-blocking, consistent precedent). |
| EC-M5-19 | Architect gate: rag/retrieval.py (I1/I2/I3), deep_research.py (I7/I9), cascade_delete.py (I1/I5), review.py (I6/I7), CliAgentProvider.chat() (I6/I7) | All above | MANUAL |
| EC-M5-20 | Tech-writer gate: D3 + D5 + D2/D4 consistent with implementation; D6 current with M5 additions | AC-D3-DR-1, AC-D3-CD-1, AC-D5-M5-1 | MANUAL |
| EC-M5-21 | PM gate: all EC-M5-1..22 MET; M4 carry-forward nits disposed; velocity note filed | All above | PENDING |
| EC-M5-22 | GET /mcp/info returns correct live-registry JSON; SectionApiMcp replaces stub; snippet generated from payload; D4 zero-drift; D5 spec created | AC-F1-MCP-UI-1..10 | CONDITIONAL — AC-F1-MCP-UI-1..8 and -10 GREEN (Phase 5 gate 2026-06-29); AC-F1-MCP-UI-9 (D5 settings-api-mcp.png) PENDING-LIVE. Architect APPROVE unconditional. Becomes GREEN when screenshot committed. |
| EC-M5-HCP | Human checkpoint: Emanuele confirms conditions in browser including all live-stack Playwright screenshot captures (docs/sprints/v0.5-scope.md §9) | EC-M5-HCP-1..6 + all PENDING-LIVE D5 screenshots | MANUAL — includes the consolidated D5 screenshot capture session (settings-api-mcp.png, cascade-delete-preview/confirm.png, review-queue.png, upload-multiformat.png, deep-search-running/complete.png, chat-citations.png, save-to-wiki.png) — see EC-M5-HCP-7 |

---

## Gap register (M5) — initial

| Gap ID | AC ID | Issue | Resolution |
|--------|-------|-------|-----------|
| GAP-v0.5-1 | AC-F5-1 | BM25 hybrid Qdrant availability not confirmed for v0.5 Phase 1. | Carry as AQ-v0.5-1. Phase 1 starts dense-only; BM25 added if confirmed available. No implementation gap until architect decision. |
| GAP-v0.5-2 | AC-F10-8 | Deep Search Playwright E2E requires a live app with SearXNG reachable. | Test target written (frontend/e2e/deep-search.spec.ts); may be DEFERRED-TO-LIVE if SearXNG not reachable in CI. Mock mode must cover core assertions. |
| GAP-v0.5-3 | AC-D3-CI-1 | mmdc CI render step was DEFERRED in v0.3 (AC-D3-3) and v0.2. Must be confirmed wired before M5 sign-off. | devops-engineer to confirm mmdc is installed in CI. If not, wire it as part of Phase 2 (deep-research.mmd is the first new diagram that requires it). |
| GAP-v0.5-4 | AC-F12-4 | unstructured OCR for image extraction may not be installable in all CI environments. | AC-F12-1 specifies graceful fallback to "image file: no text extracted" if unstructured is absent. No test failure if the package is missing — only if the fallback path errors. |

---

## Ambiguities requiring architect resolution before M5 Phase 1 begins

(Full text of each AQ in docs/sprints/v0.5-stories.md §Ambiguities)

| AQ ID | Blocks ACs | Question (abbreviated) | Recommended resolution | Urgency |
|-------|-----------|------------------------|----------------------|---------|
| AQ-v0.5-1 | AC-F5-1 | BM25 hybrid search: available on Qdrant 1.9+ instance? | Start dense-only; add BM25 if confirmed. Document in ADR if hybrid chosen. | P2 |
| AQ-v0.5-2 | AC-F5-4 | Token budget counting: tiktoken vs. char/4? | tiktoken cl100k_base for API/CLI; char/4 for Ollama. Consistent with F14. | P1 |
| AQ-v0.5-3 | AC-F10-1 | Synthesis: full analyze→generate loop or generate()-only? | generate()-only for synthesis (pre-analyzed input). Document in ADR. | P1 |
| AQ-v0.5-4 | AC-F10-2 | max_iter default: 3 fixed or configurable via provider_config? | Configurable default=3; stored in provider_config or deep_research_config table. | P1 |
| AQ-v0.5-5 | AC-F13-1 | Soft-delete: does the watcher ignore soft-deleted pages on restart? | Watcher skips pages with deleted_at IS NOT NULL. Confirm in watcher.py docstring. | P1 |
| AQ-v0.5-6 | AC-F9-1 | review_items.vault_id: FK to vaults table or to vault_state.vault_id? | Use vault_state.vault_id convention; no separate vaults table. Architect confirms. | P1 |
| AQ-v0.5-7 | AC-F17-CHAT-1 | CliAgentProvider.chat() max_iter: shared with ingest max_iter or separate chat_max_iter? | Separate chat_max_iter in provider_config recommended. Architect confirms. | P1 |

---

## Sprint 6 — v0.6 / M6 Coverage

> Milestone: M6 — "Shippable" (target: v1.0.0 tag after EC-M6-HCP)
> Sprint scope: docs/sprints/v0.6-scope.md
> Status tracker: docs/sprints/v0.6-m6-status.md
> ADRs: ADR-0037 (lint-fix loop), ADR-0038 (web clipper), ADR-0039 (Tauri v2 shell)
> Invariants with heightened priority: I7 (bounded loops — lint + clip), I6 (provider-free clip endpoint), I8 (docs-as-DoD — all D-artifacts complete)
> Last updated: 2026-06-30 (tech-writer M6 docs gate)
>
> Column guide (same as Sprint 1–5):
>   Feature ID  — K2 / F2 / F11 / F15 / D-artifact
>   AC ID       — AC-<FEATURE>-<N> as defined in docs/sprints/v0.6-scope.md
>   EC          — M6 Exit Criterion (EC-M6-1..7, EC-M6-HCP)
>   D-artifacts — D1–D7 touched by this AC
>   Invariants  — I1–I9 directly exercised
>   Test file   — backend/tests/ or frontend/src/tests/ (actual, not forward-reference)
>   Test ID     — T-<abbrev>-<NNN> as defined in test file docstrings
>   Commit      — short SHA of the commit that delivered the feature
>   Status      — GREEN / PENDING-LIVE / MANUAL / DEFERRED

---

### K2 (lint-fix op) — Lint-fix bounded HITL loop + 6 REST endpoints + migration 0014

> Backend commit: `745600f` (ops/lint.py + 6 /lint/* endpoints + migration 0014)
> Frontend commit: `ac90d35` (LintView + lintStore + lintClient + nav slot + i18n EN/IT)
> ADR: ADR-0037

| AC ID | EC | D-artifacts | Invariants | Test file | Test IDs | Commit | Status |
|-------|-----|------------|------------|-----------|----------|--------|--------|
| AC-K2L-1 | EC-M6-1 | D2 | I7 | backend/tests/test_lint.py | T-LINT-001 | 745600f | GREEN |
| AC-K2L-2 | EC-M6-1 | — | I7 | backend/tests/test_lint.py | T-LINT-002, T-LINT-003 | 745600f | GREEN |
| AC-K2L-3 | EC-M6-1 | — | I7 | backend/tests/test_lint.py | T-LINT-004 | 745600f | GREEN |
| AC-K2L-4 | EC-M6-1 | — | I1 | backend/tests/test_lint.py | T-LINT-005 | 745600f | GREEN |
| AC-K2L-5 | EC-M6-1 | — | I7 | backend/tests/test_lint.py | T-LINT-006, T-LINT-007, T-LINT-008, T-LINT-009 | 745600f | GREEN |
| AC-K2L-6 | EC-M6-1 | D4 | I8 | backend/tests/test_lint.py | T-LINT-010, T-LINT-011, T-LINT-012, T-LINT-013 | 745600f | GREEN |
| AC-K2L-7 | EC-M6-1 | — | I6 | backend/tests/test_lint.py | T-LINT-014 | 745600f | GREEN |
| AC-K2L-8 | EC-M6-1 | — | I1 | backend/tests/test_lint.py | T-LINT-015 | 745600f | GREEN |
| AC-K2L-9 | EC-M6-1 | — | I7 | backend/tests/test_lint.py | T-LINT-016 | 745600f | GREEN |
| AC-K2L-10 | EC-M6-1 | D5, D6 | I8 | frontend/src/tests/lintStore.test.ts | vitest 711 suite | ac90d35 | GREEN |

Note: Migration 0014 adds `lint_runs` and `lint_findings` tables. D2 (schema.mmd) regenerated
(`make er`) after migration 0014; zero drift confirmed. D3 `lint-fix.mmd` present and valid
(sequenceDiagram keyword; mmdc CI check wired in F15 commit `8bbd2a9`). ADR-0037 indexed.
Flag-only categories for orphan-page, contradiction, stale-claim: no wikilink/file edits for
those — status transitions to `applied` with zero writes (T-LINT-006 gate). T-LINT-015 proves
no full vault rescan (I1). T-LINT-014 proves no isinstance/class-name branching (I6).

---

### F11 — Web clipper (Chrome MV3) + secure ingress POST /clip

> Commit: `7c91354` (extension/ + hardened POST /clip + ADR-0038 + web-clip.mmd)
> ADR: ADR-0038

| AC ID | EC | D-artifacts | Invariants | Test file | Test IDs | Commit | Status |
|-------|-----|------------|------------|-----------|----------|--------|--------|
| AC-F11-1 | EC-M6-2 | — | I6 | backend/tests/test_clip.py | TC-CLIP-01, TC-CLIP-02 | 7c91354 | GREEN |
| AC-F11-2 | EC-M6-2 | — | — | backend/tests/test_clip.py | TC-CLIP-03 | 7c91354 | GREEN |
| AC-F11-3 | EC-M6-2 | — | I7 | backend/tests/test_clip.py | TC-CLIP-04, TC-CLIP-12 | 7c91354 | GREEN |
| AC-F11-4 | EC-M6-2 | — | I1 | backend/tests/test_clip.py | TC-CLIP-05 | 7c91354 | GREEN |
| AC-F11-5 | EC-M6-2 | D4 | I8 | backend/tests/test_clip.py | TC-CLIP-06 | 7c91354 | GREEN |
| AC-F11-6 | EC-M6-2 | — | I1 | backend/tests/test_clip.py | TC-CLIP-07 | 7c91354 | GREEN |
| AC-F11-7 | EC-M6-2 | — | — | backend/tests/test_clip.py | TC-CLIP-08 | 7c91354 | GREEN |
| AC-F11-8 | EC-M6-2 | — | — | backend/tests/test_clip.py | TC-CLIP-09, TC-CLIP-10, TC-CLIP-11 | 7c91354 | GREEN |

Note: 22 security tests total (401/403/413/safe-join path checks) verified by `pytest 968`.
POST /clip is documented in `docs/api/openapi.json` (D4 zero-drift; `make openapi` wired in
F15 CI commit). D3 `web-clip.mmd` present and valid (sequenceDiagram + mmdc CI check). ADR-0038
security model (bearer token, origin allowlist, body cap, safe-join) indexed. `extension/`
directory contains the complete MV3 manifest + content script (Readability + Turndown).
No Qdrant or embedding calls in the clip path — POST /clip writes raw/sources/ and triggers
the watcher; ingest runs on the normal provider-routed path (I6 unchanged).

---

### F15 — CI gate + PWA + Tauri v2 desktop shell

> CI commit: `8bbd2a9` (tests in CI, sprint/** trigger, pinned ruff/black, mmdc check)
> PWA commit: `cc365f6` (manifest + offline app-shell SW; API NetworkOnly)
> Tauri commit: `f68b2ad` (src-tauri/ scaffold + tag-only multi-OS CI + ADR-0039)
> ADR: ADR-0039

| AC ID | EC | D-artifacts | Invariants | Test file / evidence | Test IDs | Commit | Status |
|-------|-----|------------|------------|---------------------|----------|--------|--------|
| AC-F15-1 | EC-M6-3 | — | I8 | CI: GitHub Actions (sprint/** trigger; ruff 0.15.20, black 24.1.0 pinned) | CI-LINT-PIN | 8bbd2a9 | GREEN |
| AC-F15-2 | EC-M6-3 | D3 | I8 | CI: mmdc render check for all .mmd files under docs/ | CI-MMDC | 8bbd2a9 | GREEN |
| AC-F15-3 | EC-M6-3 | — | — | frontend/src/tests/ (vitest 711; tsc+eslint clean) | vitest 711 suite | cc365f6 | GREEN |
| AC-F15-4 | EC-M6-3 | — | — | Vite build emits sw.js (offline app-shell); API routes NetworkOnly | CI-BUILD-PWA | cc365f6 | GREEN |
| AC-F15-5 | EC-M6-3 | — | — | src-tauri/ scaffold valid (cargo resolve, tauri.conf.json); tag-only multi-OS CI matrix | CI-TAURI | f68b2ad | GREEN |

Note: AC-F15-1 resolves GAP-v0.5-3 (mmdc was deferred from v0.3/v0.5) and NB-9 (CI branch
filter). Pinned linter versions (ruff 0.15.20, black 24.1.0) enforce tree-wide clean status
confirmed at `968 pytest / 711 vitest` baseline. Tauri native binary build is CI-only / runs on
tag push — live local verification is part of EC-M6-HCP §5.3. ADR-0039 documents Tauri v2
choice; indexed in docs/adr/README.md.

---

### F2 — purpose.md injected into ingest + chat provider contexts

> Evidence: pre-existing wiring (ingest orchestrator.py:961; chat context.py:86)
> Verified by: `test_chat::test_includes_purpose_and_overview` (backend/tests/test_chat.py)

| AC ID | EC | D-artifacts | Invariants | Test file | Test IDs | Commit | Status |
|-------|-----|------------|------------|-----------|----------|--------|--------|
| AC-F2-1 | EC-M6-4 | D6 | I6 | backend/tests/test_chat.py | test_includes_purpose_and_overview | pre-existing | GREEN |
| AC-F2-2 | EC-M6-4 | D6 | I6 | backend/tests/test_chat.py | test_budget_caps_large_purpose | pre-existing | GREEN |

Note: F2 was wired in a prior sprint; EC-M6-4 confirms the test passes in the M6 suite
(968 pytest green). No code change needed for M6; the AC was marked GREEN on test evidence.
USER.md updated (M6 docs gate) to document `vault/purpose.md` creation and its role in
provider context injection.

---

### D3 — M6 sequence diagrams

| AC ID | EC | D-artifacts | Invariants | Test file | Test IDs | Commit | Status |
|-------|-----|------------|------------|-----------|----------|--------|--------|
| AC-D3-M6-1 | EC-M6-6 | D3 | I8 | docs/sequences/lint-fix.mmd (sequenceDiagram; mmdc CI) | CI-MMDC | 745600f | GREEN |
| AC-D3-M6-2 | EC-M6-6 | D3 | I8 | docs/sequences/web-clip.mmd (sequenceDiagram; mmdc CI) | CI-MMDC | 7c91354 | GREEN |

Note: Both diagrams verified to contain the `sequenceDiagram` keyword and pass mmdc rendering
in CI (wired by commit `8bbd2a9`). Heading comment updated to `v0.6 M6-docs-gate | 2026-06-30`.

---

### D6 — USER.md + DEPLOY.md M6 updates (docs gate)

| AC ID | EC | D-artifacts | Invariants | Evidence | Commit | Status |
|-------|-----|------------|------------|----------|--------|--------|
| AC-D6-M6-1 | EC-M6-6 | D6a | I8 | docs/USER.md: Lint section + Web Clipper section + FU-P3-2 note + nav rail + M6 feature table | M6 docs gate | GREEN |
| AC-D6-M6-2 | EC-M6-6 | D6b | I8 | docs/DEPLOY.md: 8 LINT_* + CLIP_* env vars added to §2.1 table; migration 0014 noted | M6 docs gate | GREEN |

Note: FU-P3-2 documented in USER.md Review section: CLI (CliAgentProvider) delegated-ingest
path does NOT enqueue review items (ADR-0025 §7 — conscious design gap). DEPLOY.md env var
table expanded with LINT_MAX_ITER (default 3), LINT_TOKEN_BUDGET (20000), LINT_MAX_FINDINGS (50),
LINT_TIMEOUT_SECONDS (30.0), CLIP_ENABLED (false), CLIP_TOKEN (none; secret),
CLIP_ALLOWED_ORIGINS (""), CLIP_MAX_BODY_BYTES (2097152).

---

### D5 — M6 screenshots (PENDING-LIVE)

| AC ID | EC | D-artifacts | Invariants | Evidence | Status |
|-------|-----|------------|------------|----------|--------|
| AC-D5-M6-1 | EC-M6-HCP | D5 | I8 | Lint view PNG (LintView; run lint; findings table) | PENDING-LIVE |
| AC-D5-M6-2 | EC-M6-HCP | D5 | I8 | Web Clipper flow PNG (extension popup + Options page) | PENDING-LIVE |
| AC-D5-M6-3 | EC-M6-HCP | D5 | I8 | PWA install prompt PNG (browser install banner) | PENDING-LIVE |
| AC-D5-M6-4 | EC-M6-HCP | D5 | I8 | Tauri desktop window PNG (native window with Synapse UI) | PENDING-LIVE |
| AC-D5-M5-* | EC-M6-HCP | D5 | I8 | All M5 PENDING-LIVE screenshots (carried forward from M5 EC-M5-HCP-7) | PENDING-LIVE |

Note: All D5 screenshots require a live stack (docker compose up + browser). Captured during
EC-M6-HCP (human checkpoint) via `make screenshots` / Playwright against the live stack.
Non-blocking for code-correctness gate; blocking for the `v1.0.0` tag (EC-M6-HCP item 4).

---

## M6 Exit Criteria coverage summary

| EC | Description | Covering ACs | Status |
|----|-------------|-------------|--------|
| EC-M6-1 | K2 lint-fix loop: backend + frontend; ACs proven by tests | AC-K2L-1..10 | GREEN (pytest 968; vitest 711; T-LINT-001..016 all PASS) |
| EC-M6-2 | F11 web clipper + secure ingress; 22 security ACs proven | AC-F11-1..8 | GREEN (pytest 968; TC-CLIP-01..12 all PASS) |
| EC-M6-3 | F15: PWA + Tauri shell; CI runs tests + pinned linters + mmdc on sprint/** | AC-F15-1..5 | GREEN (ruff+black+tsc+eslint clean; vitest 711; Tauri native build CI-only/live-verified) |
| EC-M6-4 | F2 verified; deferred FUs disposed | AC-F2-1..2 | GREEN (test_includes_purpose_and_overview PASS; FU-P3-2 documented in USER.md) |
| EC-M6-5 | Full suite green: pytest 968 + vitest 711; linters clean tree-wide | All above | GREEN |
| EC-M6-6 | D1–D7 complete and consistent; ER zero-drift; ADR index complete; D6 updated | AC-D3-M6-1..2, AC-D6-M6-1..2 | PARTIAL — D1-D7 updated (this gate); D5 PENDING-LIVE (see EC-M6-HCP) |
| EC-M6-7 | vault/ valid Obsidian vault; test_obsidian_check.py 15/15 green | (regression check) | GREEN (no vault-format change; test_obsidian_check.py unaffected by M6 features) |
| EC-M6-HCP | Human checkpoint: live build + browser/desktop verification + D5 screenshots | AC-D5-M6-1..4, AC-D5-M5-* | PENDING — requires EC-M6-HCP §5 items (see docs/sprints/v0.6-m6-status.md §5) |

---

## Gap register (M6)

| Gap ID | AC ID | Issue | Resolution |
|--------|-------|-------|-----------|
| GAP-M6-1 | AC-D5-M6-1..4, AC-D5-M5-* | All D5 screenshots require a live running stack; not available in CI sandbox | PENDING-LIVE — captured during EC-M6-HCP session (`make screenshots`). Non-blocking for code gate; blocks v1.0.0 tag. |
| GAP-M6-2 | AC-F15-5 | Tauri native binary build (macOS/Windows/Linux) requires native runners; CI-only or local build | CI matrix on tag push OR local `cargo tauri build`. Verified during EC-M6-HCP (human checkpoint §5.3). |
| FU-P4-3 | (docs) | ADR-0026 cascade_delete non-atomic (5 separate DB sessions); partial-run leaves transiently-inconsistent state | DOCUMENTED in ADR-0026 §9 (FU-P4-3 note). No code change; idempotent-on-retry via lint-fix loop. |
| FU-P3-2 | AC-D6-M6-1 | CLI delegated-ingest path does NOT enqueue review items (ADR-0025 §7) | DOCUMENTED in docs/USER.md Review section. Conscious design gap — review items populated only by orchestrated ingest paths (Local + API providers). |

---

## Sprint 13 — v1.3 — M13 «Foundations»

> Sprint date: 2026-07-05. Theme: structural debt paydown (T1–T10). No new user-facing
> feature surface; all changes are refactor, hardening, and docs.
> Source: docs/reference/ROADMAP-v1.3-v2.0.md §v1.3

### R13-3 — Cancel in-flight ingest (K2 / F17 / G-P2-3)

| AC ID | EC | D-artifacts | Invariants | Test file | Test IDs | Commit | Status |
|-------|-----|------------|------------|-----------|----------|--------|--------|
| AC-R13-3-1 | EC-M13-3 | — | K2, F17, I1, I7 | backend/tests/test_ingest_cancel.py | test_delete_ingest_run_cancels | sprint/v1.3 | GREEN |
| AC-R13-3-2 | EC-M13-3 | — | K2, I7 | backend/tests/test_ingest_cancel.py | test_cancelled_run_not_restartable | sprint/v1.3 | GREEN |

Note: `DELETE /ingest/{run_id}` wired to ADR-0046 cancellation queue. Activity bar cancel
button now functional. Closes G-P2-3, the last open parity item vs llm_wiki matrix — full
parity achieved as of v1.3. See SYNAPSE-VS-LLMWIKI-PARITY.md (updated 2026-07-05).

---

### R13-4 — Persistent scheduler state (T4 / I7)

| AC ID | EC | D-artifacts | Invariants | Test file | Test IDs | Commit | Status |
|-------|-----|------------|------------|-----------|----------|--------|--------|
| AC-R13-4-1 | EC-M13-4 | — | I7, T4 | backend/tests/test_scheduler.py | test_scheduler_state_survives_restart | sprint/v1.3 | GREEN |

Note: `ops_scheduler` last-run timestamps persisted into `app_config` table (ADR-0053
mechanism). Scheduled jobs resume correctly after container restart (Watchtower-safe).

---

### R13-9 — Deploy security pass: SSRF guard + rate limit (T5 / I7 / B2 / B11)

| AC ID | EC | D-artifacts | Invariants | Test file | Test IDs | Commit | Status |
|-------|-----|------------|------------|-----------|----------|--------|--------|
| AC-R13-9-1 | EC-M13-9 | D6b | I7, T5 | backend/tests/test_ssrf_guard.py | test_ssrf_blocks_private_ip | sprint/v1.3 | GREEN |
| AC-R13-9-2 | EC-M13-9 | D6b | I7, T5 | backend/tests/test_ssrf_guard.py | test_ssrf_blocks_loopback | sprint/v1.3 | GREEN |
| AC-R13-9-3 | EC-M13-9 | D6b | I7, T5 | backend/tests/test_ssrf_guard.py | test_ssrf_blocks_link_local | sprint/v1.3 | GREEN |
| AC-R13-9-4 | EC-M13-9 | D6b | I7 | backend/tests/test_rate_limit.py | test_chat_rate_limited | sprint/v1.3 | GREEN |
| AC-R13-9-5 | EC-M13-9 | D6b | I7 | backend/tests/test_rate_limit.py | test_ingest_trigger_rate_limited | sprint/v1.3 | GREEN |

Note: SSRF guard is a shared util (used by deep_research.py and searxng.py). Blocks
private/loopback/link-local/metadata-IP ranges + redirect chains. Method-aware auth
exempt list (B11) applied. Default 5432 port publish removed from compose.
DEPLOY.md §3.6 documents the hardening; see ADR-0052 (auth model).

---

### R13-11 — Responsive iPhone/iPad (F1 / F15 / ADR-0057)

| AC ID | EC | D-artifacts | Invariants | Test file | Test IDs | Commit | Status |
|-------|-----|------------|------------|-----------|----------|--------|--------|
| AC-R13-11-1 | EC-M13-11 | D5 | F1, F15, I3 | frontend/tests/responsive.spec.ts | responsive-mobile-layout | sprint/v1.3 | GREEN |
| AC-R13-11-2 | EC-M13-11 | D5 | F1, F15, I3 | frontend/tests/responsive.spec.ts | responsive-tablet-layout | sprint/v1.3 | GREEN |
| AC-R13-11-3 | EC-M13-11 | — | F15, I5 | frontend/tests/responsive.spec.ts | viewport-safe-area-dvh | sprint/v1.3 | GREEN |

Note: Three viewport tiers (≤767 mobile / 768–1023 tablet / ≥1024 desktop via ADR-0057).
`useViewport()` hook + `uiStore` absorbs PanelGroup collapse state. `PanelDrawer` renders
tree/preview as slide-over drawers on mobile; preview drawer on tablet. iOS `100dvh` + safe-area
insets applied. i18n EN/IT strings added for drawer labels. ADR-0057 accepted.

---

### R13-10 — Docs hygiene (T8 / T9 / I8)

| AC ID | EC | D-artifacts | Invariants | Evidence | Status |
|-------|-----|------------|------------|----------|--------|
| AC-R13-10-1 | EC-M13-10 | D7 | I8 | docs/adr/index.md: ADR-0039 row added; 0023 gap footnote added; Distribution & Security heading updated | GREEN |
| AC-R13-10-2 | EC-M13-10 | D2 | I8 | `docs/er/schema 2.mmd` deleted (stale duplicate) | GREEN |
| AC-R13-10-3 | EC-M13-10 | — | I8 | docs/process/BACKLOG.md: Sprint 12 all items set to done; Sprint 13 section added | GREEN |
| AC-R13-10-4 | EC-M13-10 | — | I8 | docs/reference/SYNAPSE-VS-LLMWIKI-PARITY.md: G-P2-3 closed, full parity note added | GREEN |
| AC-R13-10-5 | EC-M13-10 | — | I8 | docs/process/TRACEABILITY.md: v1.3 section added (R13-3, R13-4, R13-9, R13-11) | GREEN |
| AC-R13-10-6 | EC-M13-10 | — | I8 | docker-compose.yml: whisper service added under `profiles: ["av"]` (port 8666) | GREEN |
| AC-R13-10-7 | EC-M13-10 | — | I8 | CONTRIBUTING.md created (branching, commit format, release rule, version bump) | GREEN |
| AC-R13-10-8 | EC-M13-10 | — | I8 | docs/process/DOCS_STATUS.md: v1.3 gate added; overall verdict ALL UP-TO-DATE | GREEN |
| AC-R13-10-D5 | EC-M13-HCP | D5 | I8 | D5 screenshots: pending CI E2E job run (R13-8 E2E job must execute against live stack) | PENDING-LIVE |

---

## M13 Exit Criteria coverage summary

| EC | Description | Covering ACs | Status |
|----|-------------|-------------|--------|
| EC-M13-3 | Cancel in-flight ingest: DELETE /ingest/{run_id} functional; G-P2-3 closed | AC-R13-3-1..2 | GREEN |
| EC-M13-4 | Scheduler state survives container restart | AC-R13-4-1 | GREEN |
| EC-M13-9 | SSRF guard rejects metadata-IP results; rate limit on cost endpoints | AC-R13-9-1..5 | GREEN |
| EC-M13-11 | Responsive layout: 3 tiers, PanelDrawer, safe-area — mobile and tablet verified | AC-R13-11-1..3 | GREEN |
| EC-M13-10 | Docs gate: all T8/T9 hygiene items resolved; DOCS_STATUS = ALL UP-TO-DATE | AC-R13-10-1..8 | GREEN |
| EC-M13-HCP | Human checkpoint: E2E job green in CI; arm64 image; D5 screenshots refreshed | AC-R13-10-D5 | PENDING-LIVE |

---

## Gap register (M13)

| Gap ID | AC ID | Issue | Resolution |
|--------|-------|-------|-----------|
| GAP-M13-1 | AC-R13-10-D5 | D5 screenshots require a live stack + CI E2E job (R13-8) to have executed | PENDING-LIVE — captured by R13-8 E2E job on first CI run post-merge. Non-blocking for code gate; blocking for EC-M13-HCP human checkpoint. |
| GAP-M13-2 | EC-M13-HCP | arm64 image pull requires Actions runner with ARM capacity or QEMU buildx | PENDING — "da validare al primo run Actions" per R13-8 roadmap note. |

---

## Sprint 14 — v1.3.6 Coverage

> Milestone: M136 — "Diagnostics, Real-time & Stability"
> Scope lock: docs/sprints/SPRINT-v1.3.6-SCOPE.md (authoritative)
> Phase-0 root causes: docs/sprints/PHASE0-FINDINGS-v1.3.6.md (authoritative)
> Branch: sprint/v1.3.6
> Date authored: 2026-07-06
> Author: functional-analyst
>
> In-scope workstreams: WS-A, WS-B, WS-C, WS-D(7), WS-D(8), WS-G
> Explicitly deferred (do NOT write tests): WS-E (domain wizard), WS-F (settings restructure)
>
> Column guide (same as prior sprints):
>   Feature ID  — K1–K8 / F1–F17 per CLAUDE.md §4
>   User Story  — US-<label> defined in this section
>   AC ID       — AC-WS-<X>-<N> as defined in SPRINT-v1.3.6-SCOPE.md §4
>   EC          — M136 Exit Criterion (EC-M136-1 … EC-M136-HCP)
>   D-artifacts — D1–D7 touched by this AC
>   Invariants  — I1–I9 directly exercised
>   Planned test file — path relative to backend/tests/ or frontend/tests/
>   Test ID     — filled by qa-test-engineer after tests are written
>   PR          — PR number (filled by engineer)
>   Status      — PENDING / GREEN / MANUAL / LIVE-SMOKE / N/A (conditional)

---

### WS-A — Real-time freshness for Home dashboard and Graph viewer

**Feature IDs:** F16, F4, F18
**Invariants touched:** I2 (server-side layout cached; no client-side FA2), I3 (no re-render per polling tick)
**D-artifacts:** D4 (OpenAPI must show zero drift), D5 (screenshots refreshed if UI changes)

**User Story US-WS-A:**
As a Synapse user, I want the Home dashboard statistics cards and the Graph viewer to automatically reflect the latest data without a manual page refresh, so that I always see an accurate picture of the vault state after ingest or other operations complete.

**Acceptance criteria — engineering notes:**

1. AC-WS-A-1: When `GET /status` returns a `data_version` value different from the one recorded at the previous polling tick, the Home dashboard re-fetches `GET /stats/overview` AND `GET /stats/sections`; the rendered KPI cards update in the same render cycle without a full page reload.
   - QA test type: vitest (unit/component). Mock the fetch calls; assert that a version bump triggers exactly two fetch calls (overview + sections) and that a stable version triggers zero.
   - Binary pass/fail: assert fetch call count equals expected value.

2. AC-WS-A-2: When `data_version` changes, the Graph viewer re-fetches `GET /graph` and renders the updated precomputed coordinates. No client-side force-layout computation (FA2 or any spring/force algorithm) is invoked during or after the re-fetch.
   - QA test type: vitest. Assert no call to any layout function (e.g., `sigma.refresh()` with layout side-effects, or any `igraph`/`graphology-layout-forceatlas2` entry point) occurs on graph re-fetch. The assertion is a mock-call-count check: layout function call count === 0.
   - I2 compliance verified by code inspection + test.

3. AC-WS-A-3: When `data_version` is unchanged between two consecutive polling ticks, neither the Home dashboard nor the Graph viewer triggers a data fetch, and no component re-renders due to the tick alone.
   - QA test type: vitest. Assert fetch call count === 0 for a stable-version tick. Assert Zustand selector output reference identity is preserved (shallow equality guard holds: `Object.is(prev, next) === true`).
   - I3 compliance verified by this assertion.

4. AC-WS-A-4: No WebSocket connection, no new REST endpoint, and no additional polling loop are introduced by this workstream. The only polling mechanism is the existing `GET /status` interval call.
   - QA test type: static analysis (CI). OpenAPI drift check `git diff --exit-code docs/api/openapi.json` exits 0. A grep for `new WebSocket` / `WebSocket(` in the diff of frontend source files returns 0 matches.

5. AC-WS-A-5: The polling interval for the Home dashboard dataVersion check is 10 seconds; for the Graph viewer it is 5 seconds (matching the existing ActivityBar interval). Both values are named constants in the frontend source, not magic numbers, and are documented in the commit or ADR note filed by the architect.
   - QA test type: static analysis + code review. Grep for hardcoded numeric literals (e.g., `10000`, `5000`) in the component files; assert zero raw literals — only the named constant references.

6. AC-WS-A-6: A dedicated vitest spec asserts the full state-machine contract: given a mock that alternates stable/bumped `data_version`, the sequence of fetch calls matches exactly [0, 1, 1, 0, 1] (5 ticks: stable, bump, bump, stable, bump) for the endpoint under test.
   - QA test type: vitest. Binary assertion on mock call history array.

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-WS-A-1 | US-WS-A | EC-M136-2 | D4, D5 | I2, I3 | frontend/tests/homePolling.spec.ts | — | — | PENDING |
| AC-WS-A-2 | US-WS-A | EC-M136-2 | D4 | I2 | frontend/tests/graphPolling.spec.ts | — | — | PENDING |
| AC-WS-A-3 | US-WS-A | EC-M136-2 | — | I2, I3 | frontend/tests/homePolling.spec.ts, frontend/tests/graphPolling.spec.ts | — | — | PENDING |
| AC-WS-A-4 | US-WS-A | EC-M136-2 | D4 | I2, I3 | CI: git diff --exit-code docs/api/openapi.json; grep WebSocket in diff | — | — | PENDING |
| AC-WS-A-5 | US-WS-A | EC-M136-2 | — | I2 | CI: static grep for raw numeric literals in polling components | — | — | PENDING |
| AC-WS-A-6 | US-WS-A | EC-M136-2 | — | I2, I3 | frontend/tests/homePolling.spec.ts | — | — | PENDING |

---

### WS-B — Review queue resolved/dismissed card distinction

**Feature IDs:** F9
**Invariants touched:** (none from I1–I9 directly; correctness-only fix)
**D-artifacts:** D5 (screenshot of review tabs), D4 (no contract change)
**Phase-0 root cause (P0-1):** Backend returns correct disjoint sets per status. Defect is pure frontend: resolved/dismissed items render the identical card UI as pending items, exposing Crea/Ricerca Profonda actions and showing no resolution badge. Both tabs appear identical to the user.
**Gate:** AC-WS-B-0 must be satisfied before AC-WS-B-1..3 are green. Phase-0 confirms the bug (CONFIRMED per PHASE0-FINDINGS-v1.3.6.md §P0-1).

**User Story US-WS-B:**
As a Synapse user reviewing the Review queue, I want resolved and dismissed items to show a visually distinct card state with a resolution badge, timestamp, and link to the created page (if applicable), and without Crea/Ricerca Profonda action buttons, so that I can clearly distinguish what has been addressed from what still needs my attention.

**Acceptance criteria — engineering notes:**

0. AC-WS-B-0 (GATE): Phase-0 CONFIRMS the bug (satisfied — see PHASE0-FINDINGS-v1.3.6.md §P0-1). All subsequent ACs are unblocked.
   - Evidence: direct API calls in Phase-0 session confirmed `status=pending` → 109 items, `status=resolved` → 2 items; tabs showed visually identical cards.

1. AC-WS-B-1: The review item card component applies a distinct visual variant (e.g., CSS class `card--resolved` or `card--dismissed`) when `item.status` is `resolved` or `auto_resolved` or `dismissed`. This variant replaces the primary action row (Crea / Salta / Ignora / Ricerca Profonda) with a read-only resolution badge showing the resolution status label and the `resolved_at` timestamp (formatted per user locale IT/EN).
   - QA test type: vitest component test. Render the card with `status="auto_resolved"` and assert: (a) no element matching `[data-testid="action-crea"]` is present; (b) no element matching `[data-testid="action-ricerca-profonda"]` is present; (c) an element with `[data-testid="resolution-badge"]` is present; (d) the badge text contains the formatted timestamp.

2. AC-WS-B-2: When a resolved item has a `created_page_id` (i.e., the review action created a wiki page), the resolution badge includes a navigable link (anchor or router-link) to that page. When `created_page_id` is null, no broken link is rendered.
   - QA test type: vitest component test. Two cases: (a) with `created_page_id` set — assert one `<a>` or `<Link>` with href containing the page ID; (b) with `created_page_id = null` — assert zero such links.

3. AC-WS-B-3: A Playwright or vitest integration spec seeds the mock review store with at least one pending item and at least one resolved item, clicks "In attesa" tab, asserts Crea button is visible for the pending card AND absent for the resolved card; then clicks "Risolti" tab and asserts the resolved card has the resolution badge AND the pending card's Crea button is not visible.
   - QA test type: Playwright (preferred, uses real DOM) or vitest (using Testing Library queries). Binary assertion on DOM presence.

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-WS-B-0 | US-WS-B | EC-M136-3 | — | — | Evidence: PHASE0-FINDINGS-v1.3.6.md §P0-1 | — | — | CONFIRMED |
| AC-WS-B-1 | US-WS-B | EC-M136-3 | D5 | — | frontend/tests/reviewCard.spec.ts | — | — | PENDING |
| AC-WS-B-2 | US-WS-B | EC-M136-3 | D5 | — | frontend/tests/reviewCard.spec.ts | — | — | PENDING |
| AC-WS-B-3 | US-WS-B | EC-M136-3 | D5 | — | frontend/tests/review.spec.ts (Playwright) | — | — | PENDING |

---

### WS-C — Ingest progress visibility (batch % and ETA in Lavori-attivi widget)

**Feature IDs:** F3, F16
**Invariants touched:** I3 (no heavy rerender per tick), I7 (no new loop; existing ActivityBar 1.5s loop only)
**D-artifacts:** D4 (OpenAPI must show zero drift — no new endpoint)
**Phase-0 root cause (P0-5):** `/ingest/queue` already returns `batch` (with `done`, `total`, `eta_seconds`) and `tasks[]` (with per-task `phase`/`progress`). The Home "Lavori attivi" widget only surfaces `processing` count. No backend change needed.

**User Story US-WS-C:**
As a Synapse user, I want the "Lavori attivi" widget on the Home dashboard to show an overall progress bar and ETA when a batch ingest is running, and a per-task phase label for each active task, so that I can understand how far along the current ingest operation is without navigating away.

**Acceptance criteria — engineering notes:**

1. AC-WS-C-1: When the `/ingest/queue` snapshot contains `batch.total > 0`, the "Lavori attivi" widget renders a progress bar whose filled width equals `Math.round((batch.done / batch.total) * 100)`%. The percentage is also displayed as a numeric label (e.g., "40%"). When `batch` is null or `batch.total === 0`, the progress bar is hidden (no empty bar rendered).
   - QA test type: vitest component test. Render with `batch={done:2, total:5, eta_seconds:30}` and assert the progress element has `style.width` === "40%" (or equivalent aria/role attribute) and text "40%". Render with `batch=null` and assert no progress element in DOM.

2. AC-WS-C-2: The ETA display shows the string "ETA ~30s" (EN) or "Stima ~30s" (IT — or equivalent localized form using existing i18n keys) when `batch.eta_seconds === 30`. When `batch.eta_seconds` is null, the ETA element is not rendered (display:none or not mounted).
   - QA test type: vitest component test. Assert text content for each locale. Assert absence when `eta_seconds=null`.

3. AC-WS-C-3: For each entry in `tasks[]` where `task.status === "processing"`, the widget renders a phase label inline using the i18n key corresponding to `task.phase` (e.g., `"analyze"` → "Analisi..." in IT, "Analysis..." in EN). Phase keys not yet present in the i18n translation files for both EN and IT must be added before the PR is merged.
   - QA test type: vitest. Assert that for a task with `phase="analyze"`, the rendered text matches the i18n-resolved string. Assert that the i18n files (`frontend/src/i18n/en.json` and `frontend/src/i18n/it.json`) contain all phase keys used by the component (static key enumeration check).

4. AC-WS-C-4: No new backend REST endpoint is introduced. The OpenAPI spec is unchanged. The CI docs-gate command `git diff --exit-code docs/api/openapi.json` exits 0 after the PR lands.
   - QA test type: CI docs-gate (automated). Binary: exit code 0 = pass.

5. AC-WS-C-5: The progress bar is implemented as a CSS `<div>` or `<progress>` element — no canvas, no animation library import added to the bundle. The component does not trigger a re-render on every ActivityBar polling tick when the batch data has not changed (Zustand selector with shallow equality guards the update).
   - QA test type: vitest (render count assertion) + static analysis (grep for `canvas` or animation library import in the component file). Assert render count === 1 for two consecutive identical snapshots fed to the component.

6. AC-WS-C-6: A vitest spec covers the canonical batch snapshot: `batch={done:2, total:5, eta_seconds:30}` produces a rendered widget containing "40%", "ETA ~30s" (or locale-equivalent), and at least one task phase label. Assert all three are present in the same render.
   - QA test type: vitest. Single spec, three assertions, one render call.

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-WS-C-1 | US-WS-C | EC-M136-4 | D4 | I3 | frontend/tests/lavoriAttiviWidget.spec.ts | — | — | PENDING |
| AC-WS-C-2 | US-WS-C | EC-M136-4 | D4 | I3 | frontend/tests/lavoriAttiviWidget.spec.ts | — | — | PENDING |
| AC-WS-C-3 | US-WS-C | EC-M136-4 | — | I3 | frontend/tests/lavoriAttiviWidget.spec.ts | — | — | PENDING |
| AC-WS-C-4 | US-WS-C | EC-M136-4 | D4 | I7 | CI: git diff --exit-code docs/api/openapi.json | — | — | PENDING |
| AC-WS-C-5 | US-WS-C | EC-M136-4 | — | I3, I7 | frontend/tests/lavoriAttiviWidget.spec.ts; CI: grep canvas in component diff | — | — | PENDING |
| AC-WS-C-6 | US-WS-C | EC-M136-4 | — | I3 | frontend/tests/lavoriAttiviWidget.spec.ts | — | — | PENDING |

---

### WS-D(7) — NoteView nested-scroll and metadata header overlap (all pages)

**Feature IDs:** K6, K5, I5 (read: F1 for layout component)
**Invariants touched:** I5 (Obsidian compatibility must hold — rendered output unchanged), I4 (editor is CodeMirror 6; no WYSIWYG change)
**D-artifacts:** D5 (NoteView screenshot refreshed after fix)
**Phase-0 root cause (P0-2):** CONFIRMED. Root cause is two nested `overflow:auto` containers in NoteView — the metadata header (~379px, 46% of the 824px note area) is a static flex sibling, and `.note-view__body` is a separate inner scroll pane. The ancestor also has `overflow:auto`. Result: double-scroll on every page; body text visually collides with the SORGENTI/CORRELATE block. This is NOT a frontmatter parsing bug.
**Note:** WS-D(7) is no longer conditional — Phase-0 CONFIRMED the bug. All ACs are active.

**User Story US-WS-D7:**
As a Synapse user reading a wiki page in the NoteView panel, I want the page content to scroll smoothly in a single scroll container so that the body text never overlaps or collides with the title, type, sources, or related-pages metadata header while scrolling.

**Acceptance criteria — engineering notes:**

1. AC-WS-D7-1: The NoteView component uses exactly one scroll container for the full note area (metadata header + body). The fix removes the double `overflow:auto` nesting: either (a) the metadata header is `position:sticky; top:0` with a solid background and z-index above the body, placed above a single scrollable body; or (b) both header and body are siblings inside one scrolling parent with no inner `overflow:auto` on the body element.
   - QA test type: Playwright E2E. Navigate to any wiki page; assert that the DOM has at most one ancestor element with `overflow: auto` or `overflow: scroll` in the note panel subtree. (Use `page.evaluate` to walk the ancestor chain and count scroll containers.)

2. AC-WS-D7-2: After scrolling the note body to a position where the body text would previously have collided with the metadata block, no text content from the body overlaps the bounding box of the metadata header section. This is verified visually via a Playwright screenshot comparison or by asserting that the top edge of the first body paragraph's bounding rect is below the bottom edge of the metadata header's bounding rect.
   - QA test type: Playwright. `page.locator('.note-view__header').boundingBox()` bottom <= `page.locator('.note-view__body p:first-child').boundingBox()` top. Binary assertion.

3. AC-WS-D7-3: The metadata header height does not exceed 50% of the visible note panel height (825px reference viewport). If the metadata header exceeds this threshold, it must be collapsible (toggle button) so the user can hide it and expose more of the body. Collapsed state is the default when the panel height is below 700px.
   - QA test type: Playwright. At 1280x824 viewport, assert `header.boundingBox().height <= 412`. If collapsible: assert that clicking the toggle sets the header to collapsed (height ≤ 48px) and that the body is fully visible below.

4. AC-WS-D7-4: Existing renderMarkdown unit tests remain green. The fix touches only the NoteView layout component (CSS/JSX), not renderMarkdown.ts or the frontmatter-stripping logic. No YAML content appears in the rendered body for any test fixture page.
   - QA test type: vitest (`npm run test` suite green). `renderMarkdown` unit tests must all pass without modification.

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-WS-D7-1 | US-WS-D7 | EC-M136-6 | D5 | I4, I5 | frontend/tests/noteViewScroll.spec.ts (Playwright) | — | — | PENDING |
| AC-WS-D7-2 | US-WS-D7 | EC-M136-6 | D5 | I5 | frontend/tests/noteViewScroll.spec.ts (Playwright) | — | — | PENDING |
| AC-WS-D7-3 | US-WS-D7 | EC-M136-6 | D5 | I4 | frontend/tests/noteViewScroll.spec.ts (Playwright) | — | — | PENDING |
| AC-WS-D7-4 | US-WS-D7 | EC-M136-6 | — | I5 | frontend/tests/renderMarkdown.spec.ts (existing, must stay green) | — | — | PENDING |

---

### WS-D(8) — Vault/Meta tree node (schema.md and purpose.md accessible from tree)

**Feature IDs:** K1, K6
**Invariants touched:** I1 (no new ingest pipeline, no Postgres write, no Qdrant upsert), I5 (files remain valid Obsidian Markdown)
**D-artifacts:** D4 (new GET /vault/meta endpoint must appear in OpenAPI), D5 (screenshot of tree with Vault/Meta node)
**Phase-0 root cause (P0-3):** CONFIRMED. `schema.md` and `purpose.md` are bootstrap-written but never indexed as Page records. They are unreachable from the wiki tree.

**User Story US-WS-D8:**
As a Synapse user, I want to see and open schema.md and purpose.md from the wiki tree under a dedicated "Vault / Meta" section, so that I can read the vault rules and purpose statement without navigating the raw filesystem.

**Acceptance criteria — engineering notes:**

1. AC-WS-D8-1: A "Vault / Meta" section is always present at the bottom of the wiki file tree. It contains exactly two entries: `schema.md` and `purpose.md` (display names localized: "Schema" and "Scopo" in IT; "Schema" and "Purpose" in EN, or approved i18n strings). The section is present even when the rest of the tree is empty.
   - QA test type: Playwright. Navigate to the app; assert one element with `[data-testid="tree-section-vault-meta"]` is present; assert it contains exactly two child items whose `data-path` attributes equal the fixed paths for schema.md and purpose.md.

2. AC-WS-D8-2: Clicking the `schema.md` entry navigates to its content in the editor/preview panel, served by `GET /vault/meta?file=schema` (or equivalent endpoint). The same applies to `purpose.md`. The rendered content is the raw Markdown of the file (YAML frontmatter stripped from the rendered view per existing renderMarkdown logic).
   - QA test type: Playwright. Click the schema.md tree node; assert that the preview panel contains text that matches a known string from the actual schema.md file (e.g., the `type` field definition). Binary text-presence assertion.

3. AC-WS-D8-3: `schema.md` and `purpose.md` do NOT appear as rows in the `pages` Postgres table (verified by querying `SELECT count(*) FROM pages WHERE file_path LIKE '%schema.md' OR file_path LIKE '%purpose.md'`). No new Qdrant point is created for either file.
   - QA test type: backend pytest. In a test with an in-memory SQLite DB (StaticPool), bootstrap the vault, run the watcher scan, and assert the pages table contains 0 rows matching the two fixed paths. Optionally, assert FakeQdrantClient upsert_points was not called for these paths.
   - I1 compliance verified by this test.

4. AC-WS-D8-4: The `GET /vault/meta` endpoint (or equivalent) is accessible without authentication (matching the existing GET /pages behavior) and returns a JSON body containing at minimum `{"file": "schema", "content": "<raw markdown string>"}`. The endpoint appears in the OpenAPI spec (docs/api/openapi.json regenerated).
   - QA test type: backend pytest (ASGI TestClient). Assert status 200 for `GET /vault/meta?file=schema` and `GET /vault/meta?file=purpose`. Assert response body has `content` key with non-empty string value. CI docs-gate: `git diff --exit-code docs/api/openapi.json` exits 1 (diff expected for new endpoint), then after regeneration exits 0.

5. AC-WS-D8-5: I5 holds: both files remain valid Obsidian Markdown with YAML frontmatter. The "Vault / Meta" tree node is read-only from the tree perspective (no inline rename, delete, or create operations exposed on these two entries). The existing editor can still open and edit them if the user navigates to them directly.
   - QA test type: static assertion + Playwright. Assert that no rename/delete context menu option is present on the vault-meta tree entries (Playwright right-click assertion). Backend: assert that `schema.md` on disk still contains a valid YAML frontmatter block (`---` … `---`) after the feature is added (no file mutation).

6. AC-WS-D8-6: If either file is absent from disk at the time of the tree request (edge case: fresh install before bootstrap completes), the corresponding entry in the "Vault / Meta" section shows the label "Not yet generated" (or i18n equivalent) and is non-clickable (no navigation triggered). No 500 error is thrown.
   - QA test type: backend pytest. Mock the file-read to raise `FileNotFoundError` for schema.md; call `GET /vault/meta?file=schema`; assert status 200 with `{"file": "schema", "content": null, "missing": true}` (or equivalent agreed schema). Frontend: vitest component test — render the tree node with `missing=true`; assert text "Not yet generated" and no click handler attached.

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-WS-D8-1 | US-WS-D8 | EC-M136-5 | D5 | I1, I5 | frontend/tests/vaultMetaTree.spec.ts (Playwright) | — | — | PENDING |
| AC-WS-D8-2 | US-WS-D8 | EC-M136-5 | D5 | I5 | frontend/tests/vaultMetaTree.spec.ts (Playwright) | — | — | PENDING |
| AC-WS-D8-3 | US-WS-D8 | EC-M136-5 | — | I1 | backend/tests/test_vault_meta.py | — | — | PENDING |
| AC-WS-D8-4 | US-WS-D8 | EC-M136-5 | D4 | I8 | backend/tests/test_vault_meta.py; CI: generate_openapi.py + git diff | — | — | PENDING |
| AC-WS-D8-5 | US-WS-D8 | EC-M136-5 | — | I1, I5 | frontend/tests/vaultMetaTree.spec.ts (Playwright right-click); backend/tests/test_vault_meta.py | — | — | PENDING |
| AC-WS-D8-6 | US-WS-D8 | EC-M136-5 | — | I1 | backend/tests/test_vault_meta.py; frontend/tests/vaultMetaTree.spec.ts | — | — | PENDING |

**Ambiguity flag AQ-M136-1:** The SPRINT-v1.3.6-SCOPE.md §3 WS-D(8) states the solution must NOT index these files via the watcher or ingest path. However, the file-read path for `GET /vault/meta` requires reading from a known fixed disk location. If the implementation approach requires storing the absolute vault path in a new config key beyond what is already in `VAULT_PATH` env var, the backend-engineer must confirm with solution-architect before writing the endpoint. Escalation trigger: any approach that touches `app/models.py` or adds a column to an existing table.

---

### WS-G — Automations functional verification (lint, backfill-domains, schema_review, reclassify)

**Feature IDs:** K2, F3, F16, F18
**Invariants touched:** I7 (all ops must respect bounded loops and log `total_cost_usd`), I1 (ops must not trigger full re-scan), I5 (ops must not corrupt vault Markdown)
**D-artifacts:** D4 (OpenAPI unchanged), D7 (ADR referenced if regression root cause is architectural)
**Phase-0 root cause (P0-6):** All four ops confirmed healthy at the endpoint level (`GET /ops/schedules` returns 200); however, all have `last_run_at: null` on this vault — they have never been run against the production vault. Functional verification requires controlled runs. `lint` can be run as a free smoke; `backfill-domains`, `schema_review`, `reclassify` require controlled QA runs with provider cost awareness (I7).

**User Story US-WS-G:**
As a Synapse operator, I want to confirm that the four scheduled automations (lint, backfill-domains, schema_review, reclassify) each complete successfully after the v1.3.5 changes (log.md format, frontmatter timestamps, schema.md completeness), so that I can trust the nightly scheduler to maintain vault quality without silent failures.

**Acceptance criteria — engineering notes:**

1. AC-WS-G-1: The `lint` operation runs to completion when triggered via `POST /ops/run-now` with `op="lint"` against the live vault (986 pages). It does not raise an uncaught exception. It produces at least one row in the `lint_findings` table (or logs "no findings" with exit status OK). The `last_status` field on the schedules record is updated to `"success"` (not null, not `"error"`).
   - QA test type: LIVE-SMOKE (run against live TrueNAS vault). qa-test-engineer triggers via API call; inspects response and DB row. Also covered by backend unit test using mock provider (mock returns a pre-canned lint report; assert `lint_findings` upsert is called with expected payload).
   - Backend unit test file: `backend/tests/test_ops_lint.py` (mock provider path).

2. AC-WS-G-2: The `backfill-domains` operation runs to completion when triggered via `POST /ops/run-now` with `op="backfill_domains"`. It classifies at least one page into a non-null domain tag (or logs "nothing to backfill" if all pages already have domains — acceptable). It logs `total_cost_usd` to the `ingest_runs` table. The `last_status` is `"success"`.
   - QA test type: backend unit test (mock provider, pre-seeded pages table with pages missing domain tags) + LIVE-SMOKE (controlled: verify `total_cost_usd` > 0 in `ingest_runs` after run). I7: assert the op respects the configured `max_iter` cap (unit test: inject `max_iter=2` and verify loop exits after 2 iterations).
   - Backend unit test file: `backend/tests/test_ops_backfill.py`.

3. AC-WS-G-3: The `schema_review` operation runs to completion when triggered via `POST /ops/run-now` with `op="schema_review"`. It either produces a `ReviewItem` of type `schema_suggestion` in the `review_items` table, or logs a "no suggestions" message with exit status OK. It does NOT silently discard a non-empty provider response. The `last_status` is `"success"`.
   - QA test type: backend unit test (mock provider returns a non-empty suggestion; assert `review_items` insert is called with `item_type="schema_suggestion"`). LIVE-SMOKE: confirm `last_status` via API.
   - Backend unit test file: `backend/tests/test_ops_schema_review.py`.

4. AC-WS-G-4: The `reclassify` operation runs to completion when triggered via `POST /ops/run-now` with `op="reclassify"`. It either re-tags at least one page (updates `pages.domain` column) or logs "nothing to reclassify". It does not exit with an unhandled exception. The `last_status` is `"success"`.
   - QA test type: backend unit test (mock provider + pre-seeded stale pages) + LIVE-SMOKE. I7: assert `total_cost_usd` logged in `ingest_runs`.
   - Backend unit test file: `backend/tests/test_ops_reclassify.py`.

5. AC-WS-G-5: If any of the four ops produces a regression specific to v1.3.5 changes (log.md format, frontmatter timestamp field name, schema.md field set), the fix is bounded to restoring the pre-v1.3.5 behavior. No logic is added to the op beyond the minimum change. The fix must be accompanied by a regression test that would have caught the original breakage.
   - QA test type: backend pytest (regression test added to the relevant test_ops_*.py file). The test must fail on the pre-fix code and pass on the fixed code.

6. AC-WS-G-6: After each op runs successfully, the `log.md` file in the vault contains a new append entry in the v1.3.5 format (ISO 8601 timestamp in frontmatter `updated` field; entry body identifies the op by name; no raw Python exception traceback in the log entry).
   - QA test type: backend unit test. Mock the `log_md_append` function; assert it is called once per op run with a payload matching the v1.3.5 format regex (timestamp + op name). LIVE-SMOKE: grep `log.md` for the new entry after the live run.

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-WS-G-1 | US-WS-G | EC-M136-7 | — | I1, I5, I7 | backend/tests/test_ops_lint.py; LIVE-SMOKE | — | — | PENDING |
| AC-WS-G-2 | US-WS-G | EC-M136-7 | — | I1, I7 | backend/tests/test_ops_backfill.py; LIVE-SMOKE | — | — | PENDING |
| AC-WS-G-3 | US-WS-G | EC-M136-7 | — | I7 | backend/tests/test_ops_schema_review.py; LIVE-SMOKE | — | — | PENDING |
| AC-WS-G-4 | US-WS-G | EC-M136-7 | — | I1, I7 | backend/tests/test_ops_reclassify.py; LIVE-SMOKE | — | — | PENDING |
| AC-WS-G-5 | US-WS-G | EC-M136-7 | — | I7 | backend/tests/test_ops_{lint,backfill,schema_review,reclassify}.py (regression tests) | — | — | PENDING |
| AC-WS-G-6 | US-WS-G | EC-M136-7 | — | I5 | backend/tests/test_ops_lint.py (log format assertion) | — | — | PENDING |

---

## M136 Exit Criteria coverage summary

| EC | Description | Covering ACs | Status |
|----|-------------|-------------|--------|
| EC-M136-1 | Phase-0 defect report in BACKLOG.md (WS-H) | Evidence: PHASE0-FINDINGS-v1.3.6.md filed; WS-B CONFIRMED; WS-D(7) CONFIRMED | GREEN (Phase-0 complete) |
| EC-M136-2 | WS-A: all 6 ACs green; CI suite green | AC-WS-A-1..6 | PENDING |
| EC-M136-3 | WS-B: all 3 ACs green (bug CONFIRMED + fixed) | AC-WS-B-0..3 | PENDING (B-0 CONFIRMED; B-1..3 pending engineering) |
| EC-M136-4 | WS-C: all 6 ACs green; OpenAPI drift = 0 | AC-WS-C-1..6 | PENDING |
| EC-M136-5 | WS-D(8): all 6 ACs green | AC-WS-D8-1..6 | PENDING |
| EC-M136-6 | WS-D(7): all 4 ACs green (bug CONFIRMED, unconditional) | AC-WS-D7-1..4 | PENDING |
| EC-M136-7 | WS-G: all 6 ACs green; all 4 ops verified on live vault | AC-WS-G-1..6 | PENDING |
| EC-M136-8 | Docs gate: D2/D4 regenerated; D7 indexed if ADR written; BACKLOG.md updated; DOCS_STATUS.md gate entry added | D2, D4, D7 | PENDING (tech-writer sign-off required) |
| EC-M136-9 | Architect review: I1/I2/I3/I5 invariant notes verified per workstream | Invariant columns above | PENDING (MANUAL — architect sign-off) |
| EC-M136-10 | All CI jobs green (ruff, black, mypy, tsc, eslint, vitest, docs drift, mmdc) | All automated ACs | PENDING |
| EC-M136-HCP | Human checkpoint: Emanuele confirms WS-A, WS-B, WS-C, WS-D(8) on live v1.3.6 app | MANUAL | PENDING (MANUAL — Emanuele) |

---

## Gap register (M136)

| Gap ID | AC ID | Issue | Resolution |
|--------|-------|-------|-----------|
| GAP-M136-1 | AC-WS-G-1..6 | Four ops have `last_run_at: null` on the production vault — they have never been run against it. Full live functional verification requires a controlled run on TrueNAS (provider cost for backfill/schema_review/reclassify). | qa-test-engineer runs `lint` as free smoke first; runs remaining ops with mock provider for unit tests; schedules one controlled live run per op before EC-M136-7 sign-off. Log `total_cost_usd` for each live run per I7. |
| GAP-M136-2 | AC-WS-D8-4 | The `GET /vault/meta` endpoint is new — OpenAPI will drift after implementation. The docs-gate must be re-run after the endpoint is added, and the regenerated `openapi.json` committed before CI can pass. | Engineer runs `python scripts/generate_openapi.py` and commits the updated `docs/api/openapi.json` as part of the WS-D(8) PR. |
| GAP-M136-3 | AC-WS-D7-3 | The collapsible metadata header requirement (if height > 50%) may be disputed as out-of-scope UX work. If architect deems it a new feature beyond the scroll-fix, AC-WS-D7-3 is descoped to a DEFERRED entry and only AC-WS-D7-1/2/4 remain mandatory. | Escalate to solution-architect before implementing the collapsible; if rejected, mark AC-WS-D7-3 as DEFERRED and remove from EC-M136-6 gate. |
| GAP-M136-4 | AC-WS-B-3 | Playwright requires a running frontend+backend stack. If the CI suite does not run Playwright E2E for this spec, the test is LIVE only. | If no E2E job is configured for review tab tests, classify AC-WS-B-3 as LIVE-SMOKE and require a manual Playwright run against the live stack before EC-M136-3 sign-off. |

---

## Ambiguities requiring resolution before engineering begins (M136)

| AQ ID | Blocks ACs | Question | Recommended resolution |
|-------|-----------|----------|----------------------|
| AQ-M136-1 | AC-WS-D8-3..6 | The `GET /vault/meta` endpoint reads schema.md/purpose.md from disk. What is the authoritative disk path? Is it always `{VAULT_PATH}/schema.md` and `{VAULT_PATH}/purpose.md` at vault root, or can they be nested? | Confirm with solution-architect: fixed paths `{VAULT_PATH}/schema.md` and `{VAULT_PATH}/purpose.md` only. If not found at root, return `missing:true`. No recursive search. |
| AQ-M136-2 | AC-WS-D8-6 | What is the agreed response schema for the `/vault/meta` endpoint when a file is missing? `{"file": "schema", "content": null, "missing": true}` is recommended. | Frontend-engineer and backend-engineer must agree before implementation; functional-analyst recommendation is the `missing:true` payload above. |
| AQ-M136-3 | AC-WS-A-5 | Who documents the polling interval decision — architect via ADR note or engineer via code comment? | If architect decides to deviate from the 10s/5s defaults, a brief ADR note (not a full ADR) is sufficient. If the defaults are accepted as-is, a code comment in the polling hook is sufficient. Confirm before merging. |
| AQ-M136-4 | AC-WS-D7-3 | Is the collapsible metadata header part of the scroll-fix or a separate UX feature? | Escalate to solution-architect. If it is a UX feature, defer to v1.3.7. See GAP-M136-3. |
