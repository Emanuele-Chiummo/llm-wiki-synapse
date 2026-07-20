# ADR-0089 — overview.md regen bumps data_version (post-2.1.1)

- **Status:** Accepted
- **Date:** 2026-07-20
- **Amends:** ADR-0078 §Refinement (queue-drain batch regeneration)
- **Invariants touched:** I1, I7

## Context

ADR-0078 (v1.7.0 refinement) wired `regenerate_overview()` to fire once per queue-drain
via `app.main._queue_drain_sweep → ops.overview.regenerate_overview →
orch._update_overview`. This correctly limits the provider call to one per batch instead
of one per document.

However, `_write_and_index_overview` did not call `bump_version()` after overwriting
overview.md and upserting the Page row. The consequence:

- The SSE `/events` channel (which notifies the frontend by polling `data_version`) was
  not notified that overview.md had changed.
- The frontend could not display an updated overview without a full page reload.

## Decision

Add `await bump_version()` inside `_write_and_index_overview` immediately after
`_index_overview_file` succeeds. This makes overview regeneration a first-class
content-changing event (same as any other page write).

Rules:
- Bump fires ONLY on a successful narrative overwrite (the degrade/timeout/empty paths all
  return early before reaching `_write_and_index_overview`, so no spurious bumps).
- `bump_version()` is already called once by the ingest pipeline for the ingested pages.
  The overview bump is a separate, subsequent event (overview.md is a distinct file) —
  this does not violate ADR-0054 §3.2 ("one ingest ⇒ one bump") because the overview
  regen is a distinct, asynchronous operation fired after the queue drains.

## Why it is now safe

ADR-0078's original concern was N provider calls per batch (one per document). That was
fixed by moving to once-per-drain. The data_version bump adds no provider call and no
DB round-trip beyond the single `UPDATE vault_state SET data_version = data_version + 1`
that `bump_version()` already does atomically — it is O(1), vault-scoped, and bounded
(I1/I7).

## Tests added

| ID | File | What |
|----|------|------|
| T-OV-6 | `test_overview_regen.py` | successful regen increments data_version by 1 |
| T-OV-7 | `test_overview_regen.py` | degrade/failure path does NOT bump data_version |
| T-OV-8 | `test_overview_regen.py` | page-digest query is vault-scoped (I1 regression) |
| OWN-06 | `test_overview_not_touched_by_ingest.py` | queue-drain path delegates correctly to `orch._update_overview` |
