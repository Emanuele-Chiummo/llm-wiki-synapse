# ADR-0080 — Lint parity assessment: at parity, with deliberate UX enhancements (v1.7.0)

- **Status:** Accepted
- **Date:** 2026-07-14
- **Extends:** ADR-0037 (lint-fix loop), ADR-0058 (lint parity extension)
- **Invariants touched:** I7, I8
- **Reference:** `docs/reference/LLMWIKI-CORE-LOGIC-v0.6.3.md` §3

## Context

WS-D of the 1.7.0 program set out to align Lint with nashsu/llm_wiki. A code-level audit against
the reference (`src/lib/lint.ts`, `lint-fixes.ts`, `components/lint/lint-view.tsx`) found Synapse's
lint **already at behavioral parity** on every substantive rule, threshold and fix — and a superset
on UX. This ADR records that assessment so the parity claim is auditable and the deliberate
divergences are not "fixed" into regressions later.

## Assessment — at parity

| Reference behavior (llm_wiki v0.6.3) | Synapse (`backend/app/ops/lint.py`, `wiki/links.py`) |
|---|---|
| Structural `orphan` (info) + `suggestedSource` | `orphan-page`, severity `info`, best token-overlap suggestion — present |
| Structural `no-outlinks` (info) + `suggestedTarget` | `no-outlinks`, severity `info` — present |
| Structural `broken-link` (warning), suggestion if similarity ≥ **0.74** | `broken-wikilink`, severity `warning`, `_BROKEN_LINK_SUGGESTION_MIN_SCORE = 0.74` (`wiki/links.py:174`) — verbatim |
| Deterministic fix: append `- [[target]]` under `## Related` (idempotent) | present |
| Deterministic fix: rewrite broken target, alias preserved | present |
| Deterministic fix: create stub `wiki/queries/<slug>.md`, `tags: [stub, lint]` | present (`lint.py:1883`) |
| Semantic categories: contradiction / stale / missing-page / suggestion | `_SEMANTIC_CATEGORIES = {contradiction, stale-claim, missing-page, suggestion}` — present |
| Semantic findings need human judgment → go to Review, never auto-fixed | `send_finding_to_review` writes a ReviewItem with `proposal_origin="lint"` (`lint.py:617`) — present |

## Deliberate divergences (Synapse is a superset — do NOT "align" these away)

1. **Semantic scan is a BOUNDED LOOP, not a single pass.** llm_wiki's `runSemanticLint` is one LLM
   call. Synapse runs `for n in range(1, max_iter+1)` (`lint_max_iter=3`) with a per-round
   token-budget gate and early convergence (I7). This is a strict superset: it finds *at least* what
   a single pass finds, never less, and stops early when a round adds nothing. Setting
   `lint_max_iter=1` reproduces the reference exactly.

2. **Semantic findings route to Review ON DEMAND, not automatically.** llm_wiki emits review items
   directly from the semantic pass. Synapse surfaces them in the Lint view first and offers an
   explicit **Send to Review** action (single + batch, `send_finding_to_review`), preserving the
   richer decision surface (see the findings before routing) while reaching the same end state (a
   `proposal_origin="lint"` ReviewItem). The block-based ingest path additionally enqueues its own
   `---LINT:`-style semantic signals through the same seam once wired.

3. **Names kept in the DB, aliased at the edge.** Category strings (`broken-wikilink` vs the
   reference's `broken-link`) are stable DB values; display names/severities are presented via the
   API/i18n layer. A data migration for enum spelling has cost and zero E2E-visible payoff.

## Decision

No lint code change ships in 1.7.0: the substantive parity already exists (ADR-0037/0058), and the
three divergences above are intentional enhancements that a strict "align" would regress. The
`lint_max_iter` knob makes single-pass parity available per-run for anyone who wants byte-for-byte
reference behavior.

## Consequences

- The "3 core ops" alignment goal is met for Lint: same rules, thresholds, fixes, and Review routing.
- I7 preserved (bounded loop + token budget + findings cap). I8: this ADR + the existing lint test
  suite (`test_lint*.py`, `test_links_fuzzy_suggest.py`) document and pin the parity.
- If a future strict-parity requirement emerges, it is a config default (`lint_max_iter=1`) plus an
  opt-in auto-route flag — not a rewrite.
