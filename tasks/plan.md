# Plan — Synapse 1.6.0 generation lifecycle parity

## Objective

Deliver the approved v1.6.0 scope in
`docs/sprints/SPRINT-v1.6.0-SCOPE.md`: align direct generation, review decisions, corpus
synthesis and their UI while preserving Synapse invariants and existing production data.

## Work graph

| ID | Slice | Depends on | Primary ownership | Verification |
|---|---|---|---|---|
| P0 | Spec, superseding ADRs, API/data contract | — | Solution architect + root | ADR/invariant review |
| P1 | Red tests for generation + delegated review | P0 | Backend ingest/review | focused pytest |
| P2 | Shared six-type generation contract + delegated hard bounds | P1 | Backend ingest/review | provider contract tests |
| P3 | Review source context + split budgets | P1 | Backend ingest/review | review/orchestrator tests |
| P4 | Review origin migration/API/filter/effective type | P0 | Backend ingest/review | migration/router/service tests |
| P5 | Corpus domain guard + indexed cluster idempotency/audit | P0 | Root | synth tests + dry-run test |
| P6 | Review UI contract, filters, quality and responsive layout | P4 | Frontend | Vitest + browser QA |
| P7 | Corpus polling/diagnostics UI | P5 | Frontend | Vitest + browser QA |
| P8 | Version, OpenAPI, changelog, release notes | P2–P7 | Root | bump-check + openapi drift |
| P9 | Full quality, security, accessibility and release gates | P8 | Root + adversarial review | all project gates |

## Execution rules

- Each behavioral slice starts with a failing test and ends with its focused suite green.
- File ownership is exclusive while parallel work is active; integration happens only after the
  owning slice reports its verification result.
- Existing untracked user files (`Brand/`, `docs/er/schema 4.mmd`, `docs/er/schema 5.mmd`,
  `graphify-out/`) are out of scope.
- No production deployment, vault deletion or automatic legacy cleanup.
- Every commit references F3, F9 or F18 and uses the repository commit convention.

## Release gates

- [x] ADR-0073/0074 accepted and ADR index updated.
- [x] Database migration upgrades from 1.5.6 and downgrades without data loss.
- [x] Focused backend and frontend tests pass for every slice.
- [x] Backend tests/lint/typecheck and frontend tests/lint/build pass.
- [x] OpenAPI artifact is regenerated and drift check passes.
- [x] Desktop/tablet/mobile browser evidence is captured with clean console/network state.
- [x] EN/IT key parity and keyboard/accessibility checks pass.
- [x] All version surfaces report 1.6.0.
- [x] Changelog, release notes and operator guidance are complete.
- [x] Multi-axis code review has no unresolved P0/P1 findings.

## Decision log

- 2026-07-13: user authorized a complete 1.6.0 implementation, including UI and agentic work.
- 2026-07-13: production data mutation and deployment are excluded until local verification passes.
- 2026-07-13: direct source-local special pages and the global corpus pass remain distinct bounded
  phases; count parity alone is not an acceptance target.
