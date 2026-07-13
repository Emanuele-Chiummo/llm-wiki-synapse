# Synapse v1.6.0 — Generation lifecycle parity

**Status:** Complete

**Target:** 1.6.0

**Date:** 2026-07-13

**Features:** F3 ingest, F9 review, F18 corpus synthesis
**Baseline:** Synapse 1.5.6; comparison target `nashsu/llm_wiki` 0.6.x

## Problem statement

With a substantially equivalent knowledge base, Synapse and LLM Wiki produce materially
different query, comparison and synthesis pages. The difference is not explained by the extra
documents in Synapse: the direct-ingest contract currently forbids those page types, the review
queue can be saturated by deterministic missing-link proposals, and the corpus operation groups
untagged pages globally and can recreate an already-generated cluster. The UI then hides key
information needed to diagnose those decisions: proposal origin, query quality, final page type,
and a continuously updated corpus-run status.

v1.6.0 aligns the **generation lifecycle**, not only the count of generated files. The result must
remain provider-neutral, bounded, source-grounded, idempotent and safe for an existing production
vault.

## Goals

1. Permit all six generative page types (`entity`, `concept`, `source`, `query`, `comparison`,
   `synthesis`) in the shared direct-ingest contract when the source and Stage-1 analysis support
   them.
2. Give orchestrated and delegated/CLI providers the same policy, source context, limits and
   review semantics without introducing a second writer.
3. Prevent rule-based missing-link proposals from starving detailed LLM proposals, preserve the
   origin of every proposal, and validate the final page type at review acceptance.
4. Make corpus synthesis conservative and repeatable: no cross-domain automatic generation, no
   untagged global cluster, no duplicate member cluster across runs.
5. Make those decisions visible and operable in the UI on desktop, tablet and mobile.
6. Ship a reproducible 1.6.0 release with migrations, OpenAPI, tests, release notes and visual QA.

## Non-goals

- Reproducing LLM Wiki's exact output text or matching its page counts mechanically.
- Hard-coding a specific inference provider or model.
- Deleting, merging or rewriting existing production pages automatically.
- Running an unbounded full-vault scan during ordinary ingest.
- Deploying or mutating the production vault as part of this implementation branch.

## Functional specification

### A. Direct ingest generation contract (F3)

- The shared generation prompt and structured schema list all six generative page types.
- `query` pages are generated only for a source-grounded research question that benefits from
  explicit retrieval queries; title-only or generic queries are rejected by the prompt contract.
- `comparison` pages require at least two source-supported subjects and a meaningful comparison
  axis. `synthesis` pages require multiple compatible claims or sources and an explicit integrated
  thesis. The model must not manufacture missing evidence merely to emit a special page type.
- Existing page writer, indexing, frontmatter and link extraction paths remain the only write path.
- The delegated/CLI system scaffold receives the same page-type and grounding rules as the
  orchestrated provider path.
- All generated pages remain bounded by the existing page, token and iteration budgets.

### B. Review generation and type integrity (F9)

- Delegated review receives the raw source text plus bounded excerpts from only the pages written
  by that ingest run. When no typed `Analysis` exists, the review prompt omits that section rather
  than fabricating one from titles. This is incremental and may not scan the vault.
- Rule and model proposal budgets are separate. Rule-based missing-link items may not consume the
  entire detailed-model budget. The combined persisted total remains capped.
- Deterministic missing-link queries include useful context instead of only repeating the target
  title.
- Each review item persists a stable proposal origin (`rule`, `ai`, `corpus`, `system`, `legacy`).
  Existing rows migrate to `legacy`; no existing decision is reopened.
- The proposed page type is advisory. Acceptance resolves the effective type from proposal
  structure/text first, then a compatible explicit hint, then the review-item fallback. An
  incompatible hint cannot force a comparison/synthesis/query into the wrong directory.
- The response contract exposes proposal origin, proposed type, and—when a page was created—the
  effective created-page type.
- Review list filters support status, item type, proposal origin and proposed page type while
  preserving the existing pagination contract.

### C. Corpus synthesis/comparison (F18)

- Automatic cluster generation requires a real, shared `domain/*` tag. Untagged pages are counted
  and skipped; they are never grouped together as a synthetic global domain.
- A stable cluster signature is derived from output kind and sorted canonical member paths. A
  generated page records that signature as indexed `generation_key` metadata and the reserved
  `synapse_generation_key` YAML field that stays Obsidian-compatible. A live-row partial unique
  index is the race-safe duplicate guard.
- A repeated run skips a signature that already exists. `force` may recompute and update the same
  deterministic corpus page, but may not create a second page for that signature.
- Existing comparison/synthesis pages are audited non-destructively. The release includes a
  dry-run report path for legacy duplicates; cleanup requires an explicit later operator action.
- Candidate evaluation has its own `max_candidates` cap so low-confidence Review proposals cannot
  bypass the automatic-write `max_pages` bound.
- Status/summary expose running state and diagnostic counters at minimum: generated, proposed,
  duplicate clusters skipped, untagged pages skipped, and errors.
- The operation remains manually triggered in 1.6.0 unless the final architecture review proves a
  bounded and idempotent automatic hook safe. Direct ingest already covers source-local special
  pages, so corpus generation is a separate global pass.

### D. UI and interaction parity

- Review cards show proposal origin and proposed type; completed items also show the effective
  created type when available.
- Filters cover status, item type, origin and proposed type. `query` is a first-class review type
  in TypeScript contracts, labels, chips and both EN/IT dictionaries.
- Search-query presentation distinguishes absent, title-only and contextual queries so the owner
  can spot weak proposals before accepting them.
- Corpus controls poll while a run is active, surface readiness/skipped/duplicate counters, and
  provide clear completion/error feedback.
- The review/deep-research layout follows the existing responsive tiers. At 320–767 px the queue
  remains usable and the 264 px research panel becomes a drawer or stacked surface rather than
  compressing the primary list.
- All new controls have accessible names, keyboard operation, visible focus, semantic state and
  IT/EN parity. No user-facing strings are hard-coded in components.

## Data and API changes

- Additive review persistence migration for `proposal_origin`; default/backfill `legacy`.
- Additive nullable `pages.generation_key` plus a live-row partial unique index. Existing rows are
  left null; no heuristic data backfill runs during migration.
- Additive review response fields and optional list filters. Existing clients remain valid.
- Additive synth status/summary diagnostic fields. Existing clients remain valid.
- Stable cluster-signature metadata uses the existing page/frontmatter write path and indexed page
  row; no parallel datastore is introduced.
- Regenerate `docs/api/openapi.json` after contracts settle.

## Invariants and constraints

- **I1:** read only the current source and pages written by the current run during ingest; corpus
  work remains an explicit bounded operation.
- **I5:** generated Markdown/frontmatter/tags remain Obsidian-compatible.
- **I6/I9:** prompts and logic are provider-neutral and reuse the single page writer.
- **I7:** separate proposal caps, token budgets, page caps and corpus limits are logged and tested.
  Delegated CLI sessions set `max_turns` from provider configuration and stop at SDK message
  boundaries once the configured token budget is reached.
- **I8:** ADR, OpenAPI, release notes, changelog and screenshots are release gates.
- No destructive migration or automatic production cleanup is allowed.

## Acceptance criteria

1. Contract tests fail if any direct-ingest prompt/schema again excludes `query`, `comparison` or
   `synthesis`, or if it omits their grounding rules.
2. Both orchestrated and delegated test paths can persist each special page type through the
   existing writer; provider selection remains configuration-driven.
3. A run with at least eight dangling links still retains capacity for an LLM review proposal.
4. Delegated review tests prove source text and bounded generated-page context reach the proposal
   call; no vault scan is introduced.
5. Review origin survives persistence/listing, filters compose, and legacy rows return `legacy`.
6. Acceptance tests prove incompatible proposed-type hints cannot misfile created pages.
7. Repeating the same corpus run creates zero duplicate cluster pages. Untagged-only corpora create
   zero automatic comparison/synthesis pages and report why.
8. Frontend unit tests cover labels, filters, query-quality state, final type and corpus polling.
9. Browser QA covers desktop, tablet and 320–767 px mobile layouts with no queue compression,
   console errors or inaccessible controls.
10. Backend tests, lint, typecheck, frontend tests/build, OpenAPI drift and version consistency all
    pass for 1.6.0.

## Delivery slices

1. Freeze corrected contracts with failing tests.
2. Direct ingest + delegated review context and proposal-budget fixes.
3. Proposal provenance, filters and effective-type integrity across DB/API/UI.
4. Conservative/idempotent corpus synthesis and non-destructive audit.
5. Responsive review and observable corpus-run UI.
6. Version/docs/OpenAPI/visual evidence, full regression and release review.

## Rollback and compatibility

- Code rollback leaves the additive origin column and cluster tags harmless.
- Old clients ignore additive response fields; old rows remain readable via the `legacy` origin.
- No migration deletes data. Any legacy duplicate report is informational only.
- If special-page generation quality regresses, the shared prompt policy can be reverted without a
  schema rollback; existing pages are retained for owner review.
