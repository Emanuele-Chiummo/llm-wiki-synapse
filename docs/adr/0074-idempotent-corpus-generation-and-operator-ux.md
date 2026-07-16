# ADR-0074 — Idempotent corpus generation and operator-visible diagnostics (v1.6.0)

- **Status:** Accepted
- **Date:** 2026-07-13
- **Supersedes:** ADR-0067 D3 implementation details
- **Amends:** ADR-0067 D2 reserved metadata and ADR-0054 domain taxonomy
- **Invariants touched:** I1, I5, I6, I7, I8, I9

## Context

The v1.5 corpus operation groups pages through a deterministic graph heuristic, but treats an
untagged page as compatible with every domain and places untagged entities in a shared
`__none__` bucket. In a vault whose live pages have little or no `domain/*` coverage this creates
semantically unrelated comparisons. A second run recomputes the same member cluster but has no
persisted identity, so a different model title can create a duplicate page. `max_pages` limits
automatic writes but does not limit low-confidence clusters proposed to Review.

The UI triggers the job and fetches status immediately, but does not poll to terminal state or
explain skipped untagged/duplicate candidates.

## Decision

### 1. Domain is mandatory for automatic corpus grouping

Corpus comparison/synthesis clusters require every member to share the same non-null
`domain/*` tag. Untagged pages are counted and skipped; they never form a global synthetic
domain. Domain backfill remains an explicit, bounded prerequisite.

### 2. Stable generation identity in the authoritative page row

For each candidate, Synapse computes:

`generation_key = corpus:<kind>:<sha256(sorted canonical member file paths)>`

Migration 0031 adds nullable `pages.generation_key` and a partial unique index on
`(vault_id, generation_key)` for live rows. Legacy pages remain null; no heuristic backfill runs
during migration. New corpus pages also emit the reserved `synapse_generation_key` YAML field so the
identity survives export/re-index and remains valid Obsidian frontmatter.

When a below-threshold candidate is routed through Review, its validated generation key is kept
in the proposal's opaque `content_key`. Accepting **Create** forwards that identity into both the
orchestrated writer and delegated SDK MCP server; the server overwrites any provider value. The
Review detour therefore cannot create a title-only page that a later automatic run duplicates.

Canonical paths are used instead of database UUIDs so identity survives a database rebuild. The
database index, not an in-memory cache or model title, is the race-safe authority. A normal
repeated run skips a live key before provider generation. With `force=true`, the provider may
regenerate content but the writer updates the same deterministic corpus file/key; a uniqueness
race degrades to a counted duplicate skip instead of a second page.

### 3. Every candidate path is bounded

The corpus file slug is deterministic from kind plus the generation-key digest rather than the
model-generated title. `max_candidates` is distinct from `max_pages` and is clamped server-side. It bounds total cluster
evaluation, including below-threshold skips and Review proposals. Provider tokens and automatic
page writes retain their existing independent caps. `force` means “re-seed, regenerate and update
the same keyed page”; it never disables idempotency.

An optional `mode=auto | review-only` is additive. `review-only` never auto-writes and routes
eligible candidates to the existing Review seam under the same candidate cap.

### 4. Non-destructive legacy audit

A dry-run audit reports legacy comparison/synthesis pages with the same resolved member set or a
matching generation key. It does not delete, merge, rename or tag them automatically. Any cleanup
is an explicit later operator decision through Review or a separately approved maintenance run.

### 5. Observable job state

The existing POST/GET endpoints remain backward-compatible. Status summaries add:

- candidates evaluated and untagged pages skipped;
- existing/racing generation keys skipped;
- generated synthesis/comparison pages, Review proposals, failures and stop reason;
- effective `max_candidates`, `max_pages`, token budget and mode.

The UI polls only while the module reports a run active, stops at terminal/unmount, and presents
`budget`/`maxpages`/`max_candidates` as partial completion. Home and Review expose the same
diagnostics; Review can filter `proposal_origin=corpus`.

Persisting full job history is deferred: v1.6.0 requires terminal observability for the current
process, not a new run ledger. A future change may reuse the common operations ledger rather than
introduce a synthesis-specific table.

The POST boundary reserves the in-process single-flight slot synchronously before scheduling the
background task. Concurrent requests cannot both observe an idle state; scheduling failure releases
the reservation, while the task's `finally` block releases it after terminal completion.

## Consequences

- A vault without domain tags produces no automatic corpus pages and states the remediation.
- Repeated runs are cheap and idempotent before any model call.
- One nullable page column and live partial unique index are added in migration 0031.
- `synapse_generation_key` becomes reserved frontmatter metadata parsed by the shared page indexer.
- Counts may be lower than LLM Wiki; semantic safety is the release target, not count matching.

## Rejected alternatives

- **Use a `cluster/<hash>` navigation tag only:** portable but lacks a direct race-safe unique
  constraint and mixes internal identity with user taxonomy.
- **Deduplicate by generated title:** model-dependent and fails when wording changes.
- **Allow untagged pages into any domain:** recreates the observed cross-topic comparisons.
- **Persist a new synthesis-run table now:** useful later, but unnecessary for terminal polling and
  disproportionate to the current lifecycle fix.
- **Automatically clean legacy pages:** destructive and unsafe without owner review.

## Verification

- Pure cluster tests reject untagged/mixed-domain members.
- Repeated normal runs generate zero duplicate pages and spend zero provider tokens for known
  generation keys; forced runs may spend tokens but update the same deterministic page.
- Concurrency/uniqueness failure is counted, not fatal.
- Concurrent POST requests prove exactly one run is reserved and the other receives 409.
- Review Create preserves the generation key through orchestrated and delegated write boundaries.
- `max_candidates` caps auto, review and skipped paths.
- `force=true` updates the same deterministic page and never increases the live page count.
- Migration upgrade/downgrade and generation-key re-index tests.
- Status/API compatibility and UI polling/terminal-state tests.
- Dry-run audit proves zero writes.
