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
| AC-F17-1 | US-F17 | EC-M2-1 | D1, D7 | I6 | test_provider_abc.py | — | — | PENDING |
| AC-F17-2 | US-F17 | EC-M2-1 | — | I6 | test_provider_abc.py | — | — | PENDING |
| AC-F17-3 | US-F17 | EC-M2-1, EC-M2-3 | — | I6 | test_provider_abc.py | — | — | PENDING |
| AC-F17-4 | US-F17 | EC-M2-1 | — | I6 | test_code_quality.py | — | — | PENDING |
| AC-F17-5 | US-F17 | EC-M2-2 | D2 | I6, I8 | test_models_schema.py | — | — | PENDING |
| AC-F17-6 | US-F17 | EC-M2-2 | — | I6 | test_provider_config.py | — | — | PENDING |
| AC-F17-7 | US-F17 | EC-M2-2 | D4 | I6, I8 | test_api.py | — | — | PENDING |
| AC-F17-8 | US-F17 | EC-M2-1 | — | I6 | test_code_quality.py | — | — | PENDING |

Note: AC-F17-4 and AC-F17-8 share the same test file (test_code_quality.py) and extend
the existing I6 guard tests from v0.1. Blocks: AQ-v0.2-5 (operation scope column) must be
resolved before AC-F17-5 migration is written; AQ-v0.2-3 must be resolved before AC-F17-1
abstract method signatures can be asserted.

---

### K2 (ingest op) — Orchestrated ingest loop with capability routing

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-K2-1 | US-K2-INGEST | EC-M2-5, EC-M2-6 | — | I1, I5, I6 | test_ingest_orchestrator.py (live-infra: @pytest.mark.live) | — | — | LIVE |
| AC-K2-2 | US-K2-INGEST | EC-M2-5, EC-M2-6 | — | I1, I5, I6 | test_ingest_orchestrator.py (live-infra: @pytest.mark.live) | — | — | LIVE |
| AC-K2-3 | US-K2-INGEST | EC-M2-5, EC-M2-6 | — | I1, I5, I6 | test_ingest_orchestrator.py (live-infra: @pytest.mark.live) | — | — | LIVE |
| AC-K2-4 | US-K2-INGEST | EC-M2-3 | — | I6 | test_ingest_routing.py | — | — | PENDING |
| AC-K2-5 | US-K2-INGEST | EC-M2-4 | — | I7 | test_ingest_loop_bound.py | — | — | PENDING |
| AC-K2-6 | US-K2-INGEST | EC-M2-4 | — | I7 | test_ingest_loop_bound.py | — | — | PENDING |
| AC-K2-7 | US-K2-INGEST | EC-M2-4 | — | I7 | test_ingest_loop_bound.py | — | — | PENDING |
| AC-K2-8 | US-K2-INGEST | EC-M2-6 | — | I5 | test_ingest_orchestrator.py | — | — | PENDING |

Note: AC-K2-1, AC-K2-2, AC-K2-3 are live-infra tests (require Ollama + Anthropic API key +
claude-agent-sdk on TrueNAS). Marked LIVE — confirmed at EC-M2-5 human checkpoint only.
AC-K2-5 and AC-K2-6 share test_ingest_loop_bound.py; AQ-v0.2-1 (analyze re-run on retry)
must be resolved before AC-K2-5 call_count assertion is finalisable. AQ-v0.2-2 (log storage
mechanism) must be resolved before AC-K2-6 log capture strategy is implemented.

---

### F3 — Two-step CoT ingest, source traceability, language-aware

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-F3-1 | US-F3 | EC-M2-6 | — | I6 | test_ingest_cot.py | — | — | PENDING |
| AC-F3-2 | US-F3 | EC-M2-6 | — | I5, I6 | test_ingest_cot.py | — | — | PENDING |
| AC-F3-3 | US-F3 | EC-M2-6 | — | I5 | test_ingest_cot.py | — | — | PENDING |
| AC-F3-4 | US-F3 | EC-M2-6 | — | — | test_ingest_cot.py (may need live provider for language detection) | — | — | PENDING |
| AC-F3-5 | US-F3 | EC-M2-6 | — | I5 | test_ingest_orchestrator.py | — | — | PENDING |
| AC-F3-6 | US-F3 | EC-M2-6 | — | I5 | test_ingest_orchestrator.py | — | — | PENDING |
| AC-F3-7 | US-F3 | EC-M2-6 | — | I7 | test_ingest_cot.py | — | — | PENDING |

Note: AC-F3-1 and AC-F3-2 are blocked on AQ-v0.2-3 (exact Analysis and WikiPage Pydantic
schemas must be defined before tests can assert field names). AC-F3-4 may require a mock
provider that returns deterministic language detection output to be unit-testable; live
language detection is a supplemental check in EC-M2-5.

---

### K5 — Wikilink parser + links table

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-K5-1 | US-K5 | EC-M2-8 | — | — | test_wikilink_parser.py | — | — | PENDING |
| AC-K5-2 | US-K5 | EC-M2-8 | — | — | test_wikilink_parser.py | — | — | PENDING |
| AC-K5-3 | US-K5 | EC-M2-8 | — | — | test_wikilink_parser.py | — | — | PENDING |
| AC-K5-4 | US-K5 | EC-M2-8 | D2 | I1 | test_wikilink_integration.py | — | — | PENDING |
| AC-K5-5 | US-K5 | EC-M2-8 | — | I5 | test_wikilink_integration.py | — | — | PENDING |
| AC-K5-6 | US-K5 | EC-M2-8 | D2 | I8 | test_models_schema.py | — | — | PENDING |
| AC-K5-7 | US-K5 | EC-M2-8 | — | — | test_wikilink_parser.py | — | — | PENDING |

Note: AC-K5-1, AC-K5-2, AC-K5-3, AC-K5-7 are pure unit tests (no DB/filesystem); can be
written immediately without resolving any AQ. AC-K5-4 and AC-K5-5 require the links table
migration (AC-F17-5 / AC-K5-6 migration) to be applied first.

---

### K3 — index.md catalogue auto-maintained

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-K3-1 | US-K3 | EC-M2-9 | — | I5 | test_index_catalogue.py | — | — | PENDING |
| AC-K3-2 | US-K3 | EC-M2-9 | — | I5 | test_index_catalogue.py | — | — | PENDING |
| AC-K3-3 | US-K3 | EC-M2-9 | — | I5 | test_index_catalogue.py | — | — | PENDING |
| AC-K3-4 | US-K3 | EC-M2-9 | — | I5 | test_index_catalogue.py | — | — | PENDING |
| AC-K3-5 | US-K3 | EC-M2-9 | — | I1, I5 | test_index_catalogue.py | — | — | PENDING |
| AC-K3-6 | US-K3 | EC-M2-9 | — | — | test_index_catalogue.py | — | — | PENDING |

Note: All K3 tests use mock providers (no live inference required). The test file
test_index_catalogue.py can exercise the index update logic independently of the full
ingest loop by calling the index-update function directly with a pre-constructed WikiPage.

---

### MCP server — FastMCP standalone server

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-MCP-1 | US-MCP | EC-M2-7 | D4 | I6 | test_mcp_server.py | — | — | PENDING |
| AC-MCP-2 | US-MCP | EC-M2-7 | — | I9 | test_mcp_integration.py | — | — | PENDING |
| AC-MCP-3 | US-MCP | EC-M2-7 | — | I1, I5, I6 | test_mcp_integration.py | — | — | PENDING |
| AC-MCP-4 | US-MCP | EC-M2-7 | — | — | test_mcp_integration.py | — | — | PENDING |
| AC-MCP-5 | US-MCP | EC-M2-7 | — | — | test_mcp_integration.py | — | — | PENDING |
| AC-MCP-6 | US-MCP | EC-M2-7 | — | I1, I6 | test_mcp_integration.py | — | — | PENDING |
| AC-MCP-7 | US-MCP | EC-M2-7 | D4 | I8 | test_docs.py | — | — | PENDING |
| AC-MCP-8 | US-MCP | EC-M2-5, EC-M2-7 | — | I6 | test_ingest_orchestrator.py (live-infra) | — | — | LIVE |

Note: AC-MCP-1 through AC-MCP-6 use FastMCP's TestClient (in-process, no subprocess);
blocked on AQ-v0.2-6 (stdio vs HTTP transport decision). AC-MCP-7 shares test_docs.py
with D4 assertions from v0.1. AC-MCP-8 is live-infra only (requires running CLI provider
against real MCP server).

---

### D3 — Sequence diagrams

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D3-1 | US-D3 | EC-M2-10 | D3 | I8 | test_docs.py (mmdc render) | — | — | PENDING |
| AC-D3-2 | US-D3 | EC-M2-10 | D3 | I8 | test_docs.py (mmdc render) | — | — | PENDING |
| AC-D3-3 | US-D3 | EC-M2-10 | D3 | I8 | CI: mmdc render step | — | — | PENDING |
| AC-D3-4 | US-D3 | EC-M2-10, EC-M2-15, EC-M2-16 | D3 | I8 | — (MANUAL GATE — architect + tech-writer joint review) | MANUAL | — | MANUAL |

Note: AC-D3-3 requires a CI mmdc render step — this is the same step noted as a carry-forward
from v0.1 (v0.1-m1-closure.md) and must be added to the CI pipeline this sprint as a
devops sub-task.

---

### D7 — Architecture Decision Records

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D7-1 | US-D7 | EC-M2-11 | D7 | I8 | test_docs.py (structure check) | — | — | PENDING |
| AC-D7-2 | US-D7 | EC-M2-11 | D7 | I8 | test_docs.py (structure check) | — | — | PENDING |
| AC-D7-3 | US-D7 | EC-M2-11 | D7 | I8 | test_docs.py (structure check) | — | — | PENDING |
| AC-D7-4 | US-D7 | EC-M2-11 | D7 | I8 | test_docs.py (structure check) | — | — | PENDING |
| AC-D7-5 | US-D7 | EC-M2-11, EC-M2-15, EC-M2-16 | D7 | I8 | — (MANUAL GATE — architect + tech-writer joint review) | MANUAL | — | MANUAL |

Note: AC-D7-1 through AC-D7-4 are automated structure checks (keyword presence, section
headers, file length) added to test_docs.py. AC-D7-5 is a mandatory human gate; cannot
be automated.

---

### D1/D2/D4 updates

| AC ID | User Story | EC | D-artifacts | Invariants | Planned test file | Test ID | PR | Status |
|-------|------------|----|-------------|------------|-------------------|---------|----|--------|
| AC-D1u-1 | US-D1u | EC-M2-12 | D1 | I8 | test_docs.py (mmdc + content check) | — | — | PENDING |
| AC-D1u-2 | US-D1u | EC-M2-12 | D1 | I8 | test_docs.py (mmdc + content check) | — | — | PENDING |
| AC-D2u-1 | US-D2u | EC-M2-12 | D2 | I8 | test_docs.py, test_models_schema.py | — | — | PENDING |
| AC-D4u-1 | US-D4u | EC-M2-12 | D4 | I8 | test_docs.py (openapi validator) | — | — | PENDING |
| AC-D4u-2 | US-D4u | EC-M2-12 | D4 | I8 | test_docs.py | — | — | PENDING |

Note: AC-D2u-1 extends the existing `make er` test from v0.1 (AC-D2-1); the existing
test_models_schema.py assertions on table names must be updated to include `provider_config`
and `links`.

---

## M2 Exit Criteria coverage summary

| EC | Description (abbreviated) | Covering ACs | All ACs automated? |
|----|---------------------------|-------------|-------------------|
| EC-M2-1 | F17 ABC + 3 backends complete | AC-F17-1, AC-F17-2, AC-F17-3, AC-F17-4, AC-F17-8 | Yes (all pytest) |
| EC-M2-2 | provider_config table + REST endpoints | AC-F17-5, AC-F17-6, AC-F17-7 | Yes (pytest + API test) |
| EC-M2-3 | Capability routing correct branch | AC-K2-4, AC-F17-3 | Yes |
| EC-M2-4 | I7 bounded loop stops at max_iter | AC-K2-5, AC-K2-6, AC-K2-7 | Yes |
| EC-M2-5 | 3-provider smoke matrix (live) | AC-K2-1, AC-K2-2, AC-K2-3, AC-MCP-8 | No — LIVE infra + human checkpoint (EC-M2-17) |
| EC-M2-6 | Generated pages schema-valid | AC-F3-1..7, AC-K2-1, AC-K2-2, AC-K2-8 | Partial — F3 unit tests use mock provider; full validation in LIVE smoke matrix |
| EC-M2-7 | MCP server tools exposed and callable | AC-MCP-1..7 | Yes (FastMCP TestClient) |
| EC-M2-8 | K5 wikilink parser + links table | AC-K5-1..7 | Yes |
| EC-M2-9 | K3 index.md auto-updated | AC-K3-1..6 | Yes |
| EC-M2-10 | D3 sequence diagrams present | AC-D3-1..4 | Partial — AC-D3-4 is MANUAL |
| EC-M2-11 | D7 ADRs present | AC-D7-1..5 | Partial — AC-D7-5 is MANUAL |
| EC-M2-12 | D1/D2/D4 updated | AC-D1u-1, AC-D1u-2, AC-D2u-1, AC-D4u-1, AC-D4u-2 | Yes (mmdc + make er + make openapi) |
| EC-M2-13 | I5 Obsidian still valid post-ingest | AC-K2-1 (Obsidian check) | No — MANUAL (Emanuele opens vault in Obsidian) |
| EC-M2-14 | QA gate — green suite | All automated ACs above | QA sign-off required |
| EC-M2-15 | Architect gate | AC-D3-4, AC-D7-5, AC-D1u-1 | MANUAL (architect sign-off) |
| EC-M2-16 | Tech-writer gate | AC-D3-1..4, AC-D7-4..5, AC-D1u-1..2 | MANUAL (tech-writer sign-off) |
| EC-M2-17 | Human checkpoint | EC-M2-5 live demo | MANUAL (Emanuele confirms) |

---

## Planned test file index (v0.2 — forward references)

| File | Layer | Primary ACs | Notes |
|------|-------|-------------|-------|
| backend/tests/test_provider_abc.py | unit | AC-F17-1, AC-F17-2, AC-F17-3 | Tests InferenceProvider ABC and all 3 concrete providers; no live inference |
| backend/tests/test_provider_config.py | unit / DB | AC-F17-6 | Config resolution order; uses test DB with 3 seed rows |
| backend/tests/test_ingest_routing.py | unit | AC-K2-4 | Routing reads capabilities(), not class name; uses CustomAgentic stub |
| backend/tests/test_ingest_loop_bound.py | unit | AC-K2-5, AC-K2-6, AC-K2-7 | I7 compliance; mock provider that always returns invalid pages |
| backend/tests/test_ingest_cot.py | unit | AC-F3-1, AC-F3-2, AC-F3-3, AC-F3-4, AC-F3-7 | Two-step CoT; Analysis + WikiPage schema validation; mock provider |
| backend/tests/test_ingest_orchestrator.py | integration | AC-K2-8, AC-F3-5, AC-F3-6, AC-K2-1*, AC-K2-2*, AC-K2-3* | * = live-infra (@pytest.mark.live) |
| backend/tests/test_wikilink_parser.py | unit | AC-K5-1, AC-K5-2, AC-K5-3, AC-K5-7 | Pure parser unit tests; no DB |
| backend/tests/test_wikilink_integration.py | integration | AC-K5-4, AC-K5-5 | Requires links table migration applied |
| backend/tests/test_index_catalogue.py | unit / integration | AC-K3-1..6 | Uses mock provider; exercises index update function directly |
| backend/tests/test_mcp_server.py | unit | AC-MCP-1 | FastMCP tool registry check via TestClient |
| backend/tests/test_mcp_integration.py | integration | AC-MCP-2..6 | All 4 tools; DB + filesystem state assertions |
| backend/tests/test_models_schema.py | static introspection | AC-F17-5, AC-K5-6, AC-D2u-1 | Extends v0.1 file; adds provider_config + links table assertions |
| backend/tests/test_code_quality.py | static / lint | AC-F17-4, AC-F17-8 | Extends v0.1 I6 guard; zero provider imports outside provider/ |
| backend/tests/test_api.py | integration (ASGI) | AC-F17-7, AC-D4u-1 | Extends v0.1 file; adds /provider/config endpoint tests |
| backend/tests/test_docs.py | CI artefact | AC-D3-1..3, AC-D7-1..4, AC-D1u-1..2, AC-D2u-1, AC-D4u-1..2, AC-MCP-7 | Extends v0.1 file; adds D3, D7, component.mmd, mcp-tools.json checks |
| CI: mmdc render step | CI | AC-D3-3 | Added to CI pipeline this sprint (carry-forward from v0.1-m1-closure) |

---

## Gap register (v0.2)

| Gap ID | AC ID | Issue | Resolution |
|--------|-------|-------|-----------|
| GAP-v0.2-1 | AC-K2-1, AC-K2-2, AC-K2-3, AC-MCP-8 | Live-infra tests require Ollama on TrueNAS, Anthropic API key, and working claude-agent-sdk — not available in dev sandbox or CI unit-test run | Mark @pytest.mark.live; run only during EC-M2-5 smoke matrix on TrueNAS; human confirms at EC-M2-17 checkpoint |
| GAP-v0.2-2 | AC-D3-4, AC-D7-5, AC-D1u-1 (architect approval), AC-D1u-2 (tech-writer) | Architect and tech-writer review gates are not automatable as pytest tests | Record as MANUAL in sign-off register; binary gate before PM sign-off |
| GAP-v0.2-3 | AC-F3-4 | Language detection (EN/IT) requires a provider that returns deterministic language output; purely mock-based test may not exercise the actual model's language capability | Unit test uses a mock provider returning preset language codes; live language detection validated in EC-M2-5 smoke matrix as supplemental (not a separate automated gate) |
| GAP-v0.2-4 | AC-K2-6 | Log entry storage mechanism (Python structlog vs. Postgres ingest_runs table) is unresolved — see AQ-v0.2-2 | Blocked on architect resolution of AQ-v0.2-2; test implementation deferred until decision is recorded in ADR-0009 |
| GAP-v0.2-5 | AC-F17-1, AC-F3-1, AC-F3-2 | Exact Pydantic schemas for Analysis, WikiPage, Message, ProviderCapabilities not yet defined — see AQ-v0.2-3 | Blocked on architect/ai-agent-engineer defining schemas in backend/app/ingest/schemas.py; test field assertions cannot be finalised before this |
| GAP-v0.2-6 | AC-MCP-1..6 | MCP transport (stdio vs HTTP) not yet decided — see AQ-v0.2-6 | FastMCP TestClient works regardless of transport for unit tests; integration test setup (subprocess vs in-process) depends on architect's decision |

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
