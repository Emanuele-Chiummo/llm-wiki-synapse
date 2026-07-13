# Plan — Product readiness hardening after Synapse 1.6.0

## Objective

Turn the 1.6.0 feature-complete application into a trustworthy, understandable and
redistributable self-hosted LLM Wiki. This cycle prioritizes secure defaults, a verified
first-run path, maintainable interface boundaries and rename-ready product identity.

The product remains a local/self-hosted LLM Wiki. A final rename is deliberately excluded
until name, repository, package and trademark conflicts have been reviewed by the owner.

## Work graph

| ID | Slice | Depends on | Verification |
|---|---|---|---|
| R0 | Architecture, first-run, brand and distribution audit | — | evidence-backed P0/P1/P2 report |
| R1 | Secure `local` / `server` deployment modes | R0 | config/auth focused tests |
| R2 | Versioned setup state and truthful completion semantics | R0 | red/green Vitest contract tests |
| R3 | First-run wizard and connection UI decomposition | R2 | Vitest, lint, responsive QA |
| R4 | Central product identity and rename boundary | R0 | identity tests + grep audit |
| R5 | Community install, desktop capability and durable jobs roadmap | R0–R4 | ADR/sprint backlog review |
| R6 | Integrated quality and adversarial review | R1–R4 | backend/frontend gates |

## Current delivery boundary

- Implement R1–R4 as independently testable vertical slices.
- Record but do not silently absorb the larger P0/P1 programs: Tauri command hardening,
  one-command community deployment, atomic wiki writes, persistent job execution and release signing.
- Preserve all user-owned untracked files and source branding material.
- Keep the existing Synapse name and mark in shipped surfaces until the owner selects a new name.
- Prefer explicit readiness states over inferring configuration from a non-empty provider list.

## Acceptance criteria

- [x] Server deployments cannot start with missing or weak authentication credentials.
- [x] Public health endpoints expose only the minimum connection/liveness contract.
- [x] Dismissing setup is distinct from completing setup and does not claim the product is ready.
- [x] A returning user can reopen setup and continue from the first incomplete check.
- [x] First-run UI uses the brand typography/tokens, has clear progress labels and remains keyboard usable.
- [x] Product name/tagline references used by core UI are centralized for a future rename.
- [x] Focused tests, full frontend gates and affected backend gates pass.
- [x] Final code review has no unresolved P0/P1 regression introduced by this cycle.

## Decision log

- 2026-07-13: the owner authorized agentic product, usability and visual improvements.
- 2026-07-13: security and first-run reliability take precedence over additional decorative work.
- 2026-07-13: branding PDF v1.0 remains the visual source of truth; the logo is retained while
  product naming is evaluated separately.

---

## Completed baseline — Synapse 1.6.0 generation lifecycle parity

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
