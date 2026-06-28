# Synapse — Traceability Matrix
> Maintained by: functional-analyst (stub), qa-test-engineer (fills Test ID + Status columns)
> Last updated: 2026-06-28 (Sprint 1 / v0.1 — QA pass; Test IDs filled, statuses updated)
> Source of truth for feature IDs: CLAUDE.md §4
> User stories + ACs: docs/sprints/v0.1-stories.md
> Sprint scope + Exit Criteria (EC-x): docs/sprints/v0.1-scope.md §5
> Backlog ACs (PM-authored): BACKLOG.md §Sprint 1
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
