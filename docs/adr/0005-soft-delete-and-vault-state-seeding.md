# ADR-0005 — Soft-delete for pages and startup-seeded vault_state

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.1
- Decider: solution-architect
- Invariants: I1, I2 (data_version is the future graph-recompute signal), I8
- Resolves: AQ-4
- Related: ADR-0002, F13 (cascade delete, v0.5)

## Context

Two persistence decisions are coupled and both touch later sprints:

- **Page deletion.** A DELETE event must not destroy the metadata row, because cascade-delete
  (K6/F13, v0.5) needs the row to find shared entities and dead wikilinks before deciding what
  to remove. AC-WATCH-4 requires `deleted_at` to be set, not the row dropped.
- **`vault_state` lifecycle (AQ-4).** `data_version` is the debounce signal for FA2 graph
  recompute (I2). AC-F16dv-4 requires it to survive restarts and never reset. That only holds
  if the row exists independently of ingest activity.

## Decision

**Soft delete for `pages`:**

- DELETE event → set `pages.deleted_at = now()`, leave all other columns intact, and
  hard-remove the Qdrant point (ADR-0002). The row is never physically deleted in v0.1.
- All read endpoints (`GET /pages`, `GET /pages/{id}`) filter `deleted_at IS NULL` by
  default, so a deleted page is invisible to the API but available to future graph /
  cascade logic.
- Re-creating a previously-deleted `file_path` clears `deleted_at` (resurrection via upsert
  on the same path), rather than inserting a second row — `file_path` stays effectively
  unique among live rows.

**`vault_state` seeding (AQ-4 resolution):**

- Exactly **one row per `vault_id`**; `vault_id` is read from the `VAULT_ID` env var
  (single vault in v0.1, but the column and key support multi-vault later).
- The row is **seeded on service startup** (idempotent upsert) with `data_version = 0` if no
  row exists for that `vault_id`. It is *not* created lazily on first ingest, so
  `GET /status` works before any ingest and the value survives restarts.
- `data_version` is **monotonic non-decreasing**: only ever `+1` on a successful upsert
  ingest (ADR-0001 case where content hash changed). Startup seeding, restart, GET requests,
  duplicate-skip, and deletion never change it (satisfies AC-F16dv-4). Deletion does not bump
  it in v0.1 (no graph yet); revisited in v0.3 when the graph consumes the signal.

## Consequences

- (+) Cascade-delete (F13) has the data it needs; no schema change required in v0.5.
- (+) AC-F16dv-4 holds by construction: the only writer that changes `data_version` is the
  successful-ingest path, and it only increments.
- (+) `GET /status` returns a valid `data_version` immediately after startup on an empty
  vault.
- (−) Soft-deleted rows accumulate; a future purge/GC is needed (out of v0.1 scope, noted).
- (−) Unique-live-`file_path` is enforced by application logic (upsert-by-path), not yet by a
  partial unique index. A partial unique index `(vault_id, file_path) WHERE deleted_at IS
  NULL` is recommended and may be added in v0.1 if cheap; flagged for the engineer.
