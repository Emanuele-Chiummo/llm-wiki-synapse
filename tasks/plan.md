# Plan — Full UI/UX review and product polish for Synapse 1.6.0

## Objective

Review the complete product surface as a self-hosted LLM Wiki, then implement the
highest-leverage visual and usability corrections. The product must explain its state,
guide a first-time user toward a useful wiki, and remain coherent across desktop and
narrow viewports without turning into a generic AI dashboard.

## Work graph

| ID  | Slice                                                 | Depends on | Verification                             |
| --- | ----------------------------------------------------- | ---------- | ---------------------------------------- |
| UI0 | Screen-by-screen runtime, source and brand audit      | —          | desktop/mobile evidence + review matrix  |
| UI1 | Shared connection/readiness state in the shell        | UI0        | store/component red-green tests          |
| UI2 | Recoverable Home and provider failure states          | UI1        | focused Vitest + browser offline path    |
| UI3 | Visual-system consolidation and first-use hierarchy   | UI0–UI2    | CSS/component tests + responsive QA      |
| UI4 | Brand, logo and naming recommendations                | UI0        | documented decision options              |
| UI5 | Integrated accessibility, performance and code review | UI1–UI4    | full frontend gates + adversarial review |

## Delivery boundary

- Apply P0/P1 fixes that affect trust, orientation or task completion across the product.
- Prefer shared shell and design-system remedies over one-off decoration in every screen.
- Record screen-specific P2 redesigns rather than mixing a dozen unrelated rewrites into one change.
- Preserve the existing information architecture and the LLM Wiki mental model: sources become
  linked pages, pages become a graph, and generated knowledge remains reviewable.
- Keep the current product name until the owner approves a rename and legal/domain checks finish.

## Acceptance criteria

- [x] Every primary section has a documented visual/UX assessment and recommendation.
- [x] Backend loss never leaves the shell, Home or provider selector in an indefinite loading state.
- [x] Connection failures provide a human-readable explanation and a recovery action.
- [x] Shared visual primitives reduce repeated page-state and status styling.
- [x] First-time users can understand the sequence: connect, configure, create/open a project,
  add sources, generate, then review.
- [x] Branding guidance covers logo usage, descriptor and rename-safe identity.
- [x] Focused and full frontend gates pass; browser QA covers desktop and narrow screens.

---

# Plan — Product readiness hardening after Synapse 1.6.0

## Objective

Turn the 1.6.0 feature-complete application into a trustworthy, understandable and
redistributable self-hosted LLM Wiki. This cycle prioritizes secure defaults, a verified
first-run path, maintainable interface boundaries and rename-ready product identity.

The product remains a local/self-hosted LLM Wiki. A final rename is deliberately excluded
until name, repository, package and trademark conflicts have been reviewed by the owner.

## Work graph

| ID  | Slice                                                          | Depends on | Verification                    |
| --- | -------------------------------------------------------------- | ---------- | ------------------------------- |
| R0  | Architecture, first-run, brand and distribution audit          | —          | evidence-backed P0/P1/P2 report |
| R1  | Secure `local` / `server` deployment modes                     | R0         | config/auth focused tests       |
| R2  | Versioned setup state and truthful completion semantics        | R0         | red/green Vitest contract tests |
| R3  | First-run wizard and connection UI decomposition               | R2         | Vitest, lint, responsive QA     |
| R4  | Central product identity and rename boundary                   | R0         | identity tests + grep audit     |
| R5  | Community install, desktop capability and durable jobs roadmap | R0–R4      | ADR/sprint backlog review       |
| R6  | Integrated quality and adversarial review                      | R1–R4      | backend/frontend gates          |

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

| ID  | Slice                                                       | Depends on | Primary ownership         | Verification                   |
| --- | ----------------------------------------------------------- | ---------- | ------------------------- | ------------------------------ |
| P0  | Spec, superseding ADRs, API/data contract                   | —          | Solution architect + root | ADR/invariant review           |
| P1  | Red tests for generation + delegated review                 | P0         | Backend ingest/review     | focused pytest                 |
| P2  | Shared six-type generation contract + delegated hard bounds | P1         | Backend ingest/review     | provider contract tests        |
| P3  | Review source context + split budgets                       | P1         | Backend ingest/review     | review/orchestrator tests      |
| P4  | Review origin migration/API/filter/effective type           | P0         | Backend ingest/review     | migration/router/service tests |
| P5  | Corpus domain guard + indexed cluster idempotency/audit     | P0         | Root                      | synth tests + dry-run test     |
| P6  | Review UI contract, filters, quality and responsive layout  | P4         | Frontend                  | Vitest + browser QA            |
| P7  | Corpus polling/diagnostics UI                               | P5         | Frontend                  | Vitest + browser QA            |
| P8  | Version, OpenAPI, changelog, release notes                  | P2–P7      | Root                      | bump-check + openapi drift     |
| P9  | Full quality, security, accessibility and release gates     | P8         | Root + adversarial review | all project gates              |

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
