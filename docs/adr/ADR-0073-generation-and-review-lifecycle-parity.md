# ADR-0073 — Source-grounded generation and review lifecycle parity (v1.6.0)

- **Status:** Accepted
- **Date:** 2026-07-13
- **Supersedes:** ADR-0067 D1/D3/D4 only where they prohibit direct generation of `query`,
  `comparison` and `synthesis`
- **Amends:** ADR-0007, ADR-0011, ADR-0034, ADR-0044, ADR-0063
- **Invariants touched:** I1, I5, I6, I7, I8, I9

## Context

The v1.5 parity work reached the same model and nearly the same source corpus as LLM Wiki, but
kept an architectural prohibition that LLM Wiki does not have: Synapse's shared direct-ingest
prompt and structured schema allow only `entity`, `concept` and `source`. Query, comparison and
synthesis therefore cannot be produced by ordinary ingest even when one source contains a valid
research question, comparison axis or multi-claim synthesis.

The review lifecycle compounds the discrepancy. Rule-based dangling-link proposals are prepended
and the merged list is truncated to a small constant, so they can starve the model proposals. The
delegated CLI route fabricates an `Analysis` from written titles and omits the source body even
though the source is already in memory. Finally, proposal provenance and the effective created
type are not visible to API/UI clients.

## Decision

### 1. One six-type generation contract

The shared analyze/generate policy permits all six generative page types:
`entity`, `concept`, `source`, `query`, `comparison`, `synthesis`.

Special types have stricter evidence gates:

- `query`: a source-grounded open research question plus useful contextual search queries;
- `comparison`: two or more source-supported subjects plus a meaningful common axis;
- `synthesis`: compatible evidence across several claims/sections and an integrated thesis.

The prompt explicitly says that emitting every special type is optional and that unsupported
special pages must be omitted. Pydantic validation, the existing type-to-directory mapping and
the single `write_wiki_page` seam remain authoritative. No provider-specific branch is added.

### 2. Delegated review uses real available context, not invented analysis

No new MCP `record_analysis` tool is introduced. Such a tool would make parity depend on a model
choosing to call an additional provider-specific tool and would expand the agent surface without
adding evidence that is not already available.

Instead, `propose_reviews` accepts `Analysis | None`. The orchestrated route passes its typed
Stage-1 analysis. The delegated route passes:

- raw source text already held by the ingest pipeline, bounded before prompt assembly;
- excerpts from only the page IDs captured during that delegated run (maximum 20 pages, 800
  characters per page and 6,000 characters total);
- `analysis=None`, whose section is omitted from the prompt rather than synthesized from titles.

This is an indexed, per-run read and never walks the vault (I1). It preserves one bounded review
provider call (I7).

### 3. Separate review budgets with one global ceiling

Rule and AI proposal budgets are computed independently. Deterministic missing-link proposals are
capped first, but the AI budget is reserved and cannot be consumed by rules. A final global cap is
still enforced after deduplication. The caps are configuration-driven, clamped and logged.

Missing-link search queries include target plus local page/source context; a bare title may be one
query but cannot be the only contextual signal when context exists.

### 4. Persist proposal provenance; derive the created type

Migration 0031 adds `review_items.proposal_origin` with application values
`rule | ai | corpus | system | lint | legacy`. It is non-null and defaults/backfills to `legacy`.
New enqueue sites set an explicit origin. Terminal review decisions are not reopened.

`GET /review/queue` gains optional, composable filters for item type, origin and proposed page
type. Responses expose `proposal_origin` and derive `created_page_type` by joining the existing
`created_page_id` to `pages`; no redundant type column is added.

The proposed type remains advisory. Creation resolves structural cues first, then a compatible
explicit proposal, then the review-category fallback. The effective created type is returned and
shown by the UI.

### 5. Agentic loop limits are real limits

The delegated CLI session sets SDK `max_turns` from the resolved provider `max_iter`. It checks
reported SDK usage at message boundaries and stops receiving/continuing once the configured token
budget is reached. This is best-effort at an SDK message boundary, documented and tested; it does
not claim token-perfect preemption.

## Consequences

- Direct ingest can now create useful special pages, but output counts remain evidence-dependent.
- Existing inverse prohibition tests are replaced with grounding and six-type contract tests.
- Both provider routes use the same generation policy and review seam without a new MCP tool.
- Review filters are server-side so pagination totals remain truthful.
- The UI can explain where a proposal came from and what was actually created.
- One additive migration is required; legacy rows stay readable as `legacy`.

## Rejected alternatives

- **Keep special pages corpus-only:** preserves the measured discrepancy and upstream mismatch.
- **Add an SDK-only `record_analysis` MCP tool:** agent-dependent, expands surface and is not
  required to ground review when source plus written-page excerpts already exist.
- **Infer proposal origin from rationale text:** unstable, non-filterable and not localisable.
- **Filter the loaded frontend page only:** corrupts counts and pagination semantics.

## Verification

- Prompt/schema contract tests for all six types and their evidence gates.
- Orchestrated and delegated writer-path tests for every special type.
- Delegated context bounds and no-vault-walk tests.
- Starvation regression: eight or more rule candidates still permit an AI proposal.
- Migration, origin/filter/join and effective-type tests.
- SDK max-turn/token-bound tests.
- OpenAPI, EN/IT parity, responsive browser and accessibility evidence.
