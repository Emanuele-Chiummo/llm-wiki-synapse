# Tasks — Full UI/UX review and product polish

## Evidence and architecture

- [x] Audit every primary screen in the running application and source.
- [x] Map the complete first-user journey and recovery paths.
- [x] Review design tokens, shared components, large files and brand compliance.
- [x] Publish the screen-by-screen review with P0/P1/P2 recommendations.

## Reliability and orientation

- [x] Add a shared backend connection state to the existing status poll.
- [x] Add a concise global offline/recovery surface without another poller.
- [x] Replace Home's infinite failure skeleton with a recoverable error state.
- [x] Make the provider selector distinguish loading, offline and unconfigured states.

## Visual system and onboarding

- [x] Consolidate repeated page-state/status presentation into shared primitives.
- [x] Improve first-use hierarchy without adding a competing dashboard metaphor.
- [x] Verify focus, keyboard navigation, text contrast and narrow layouts.
- [x] Record logo and naming recommendations without applying an unapproved rename.

## Quality

- [x] Run focused red-green tests for each behavioral slice.
- [x] Run full frontend tests, lint and production build.
- [x] Re-run desktop and narrow browser QA; only expected offline-backend requests fail.
- [x] Resolve all introduced P0/P1 findings in final multi-axis review.

---

# Tasks — Product readiness hardening

## Audit and planning

- [x] Inspect the supplied 12-page branding guide and existing asset integration.
- [x] Run a staff-level architecture and distribution audit.
- [x] Complete the first-run usability and product naming audits.
- [x] Define a bounded implementation graph and acceptance criteria.

## Secure defaults

- [x] Add explicit local/server deployment mode validation.
- [x] Fail fast for missing or weak server authentication.
- [x] Reduce unauthenticated health detail exposure.
- [x] Forward the documented trust-boundary variables through Docker Compose.
- [x] Add focused backend regression tests, including real environment loading.

## Trustworthy first run

- [x] Replace the boolean-only setup flag with versioned setup state.
- [x] Distinguish dismissed, deferred and completed setup outcomes.
- [x] Stop inferring readiness from provider row count alone.
- [x] Probe a provider candidate before persisting it or claiming readiness.
- [x] Add progress labels, readiness summary and clear next actions.
- [x] Remove duplicate side effects in the wizard.
- [x] Decompose the setup implementation into testable modules.

## Product identity and interface

- [x] Centralize core product name, descriptor and tagline references.
- [x] Apply Geist consistently to the shell/connection flow and brand tokens to setup UI.
- [x] Keep current mark compliant, including the simplified variant below 24 px.
- [x] Produce a rename shortlist with collision evidence; do not rename without approval.
- [x] Align public metadata and extension version with 1.6.0.
- [x] Make Settings responsive and keep navigation reachable on short viewports.

## Quality and handoff

- [x] Run focused setup/auth tests first.
- [x] Run frontend test, lint and build gates.
- [x] Run affected backend lint/type/test gates.
- [x] Perform responsive/browser QA at desktop, 375×812 and 320×568.
- [x] Run final multi-axis code review and resolve introduced P0/P1 findings.

---

## Completed baseline — Synapse 1.6.0

## Planning and architecture

- [x] Record 1.6.0 functional scope and acceptance criteria.
- [x] Create dependency-aware implementation plan.
- [x] Accept ADR-0073/0074 and supersede incompatible ADR-0067 generation decisions.
- [x] Freeze additive DB/API contracts.

## F3 — direct generation

- [x] Add red contract tests for all six page types and grounding rules.
- [x] Update shared analyze/generate/delegated prompt policy.
- [x] Prove orchestrated persistence of query/comparison/synthesis.
- [x] Prove delegated persistence through the existing MCP writer.
- [x] Enforce configured delegated `max_turns` and token-budget stop.

## F9 — review integrity

- [x] Add delegated raw-source + bounded-written-page context tests.
- [x] Split rule and detailed proposal budgets; retain a global cap.
- [x] Improve deterministic missing-link search queries.
- [x] Add `proposal_origin` migration/model/schema/backfill.
- [x] Add composable review list filters.
- [x] Expose created effective page type.
- [x] Harden proposed-to-effective page-type resolution.
- [x] Add backend regression and API tests.

## F18 — corpus synthesis

- [x] Reject untagged and cross-domain automatic clusters.
- [x] Create stable kind/member cluster signatures.
- [x] Persist signatures through indexed page metadata + Obsidian-valid frontmatter.
- [x] Skip existing signatures across repeated and forced runs.
- [x] Cap candidate evaluation independently from pages written.
- [x] Add non-destructive legacy duplicate audit/report.
- [x] Expose diagnostic status counters.
- [x] Add idempotency/domain/audit tests.

## Interface

- [x] Add review origin/proposed/effective type display and filters.
- [x] Add query-quality states and `query` i18n/type parity.
- [x] Make Deep Research panel responsive on mobile/tablet.
- [x] Poll corpus runs to terminal state and show diagnostics.
- [x] Add frontend tests and EN/IT parity coverage.
- [x] Verify keyboard, focus, accessible names and narrow viewport behavior.

## Release

- [x] Bump every version surface to 1.6.0.
- [x] Regenerate OpenAPI and any affected architecture docs.
- [x] Update changelog and write 1.6.0 release notes/operator guidance.
- [x] Run focused and full backend/frontend quality gates.
- [x] Capture browser screenshots and console/network evidence.
- [x] Run final multi-axis review and resolve all release-blocking findings.
- [x] Prepare intentional commits on `codex/release-1.6.0-generation-parity`.
