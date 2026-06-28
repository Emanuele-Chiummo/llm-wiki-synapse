# ADR-0002 — Datastore split: Postgres for metadata/links, Qdrant for vectors

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.1
- Decider: solution-architect
- Invariants: I1 (incremental), I9 (reuse Qdrant + bge-m3), I8 (ER from models)
- Related: ADR-0001 (incremental strategy), ADR-0004 (embedding dim policy)

## Context

The walking skeleton must persist two distinct kinds of state for each ingested page:

1. **Structured metadata** — `type`, `title`, `sources[]`, `file_path`, hashes, soft-delete
   marker, timestamps, and (from v0.3) graph coordinates and link edges. This data is
   relational, queried by exact key, and is the source of truth for the D2 ER diagram (I8)
   and for cascade-delete (K6/F13).
2. **Dense embedding vectors** — bge-m3 output used by future vector search (F5).

Qdrant and bge-m3 are already running on TrueNAS (I9: do not reinvent). Postgres 16 is the
chosen relational store (CLAUDE.md §6). The question is the division of responsibility and
how the two stores stay consistent without distributed transactions.

## Decision

Split by data nature, with Postgres as the **system of record** and Qdrant as a
**derived index**:

- **Postgres** owns all structured metadata and (later) links and FA2 coordinates. The
  `pages` row is authoritative. It stores `qdrant_point_id` (the UUID used as the Qdrant
  point id) so the two stores are joined by a stable key.
- **Qdrant** (`synapse_pages` collection) owns only the vector plus a thin denormalised
  payload (`file_path`, `title`, `type`) for debugging and future filtered search. It is
  fully reconstructable from Postgres + a re-embed; losing Qdrant is recoverable, losing
  Postgres is not.
- **Point id == page id.** The Qdrant point id is the same UUID as `pages.id`
  (stored redundantly as `qdrant_point_id` to keep the mapping explicit and to allow the
  two to diverge only deliberately in future). Upsert replaces in place (no orphan,
  AC-WATCH-3); delete removes the point by that id (AC-WATCH-4 / AC-QD-3).
- **Write ordering for consistency without 2PC:** within one ingest the order is
  (1) compute embedding, (2) `UPSERT` Postgres row in a transaction, (3) `upsert` Qdrant
  point, (4) append `log.md`, (5) bump `data_version`. Postgres commits before Qdrant is
  touched; if the Qdrant call fails, the page is still recorded as the source of truth and
  a later reconciliation (out of v0.1 scope) can repair the derived index. We never have a
  Qdrant point without a Postgres row.
- **Soft delete:** a DELETE event sets `pages.deleted_at` (row retained for cascade-delete
  / audit) and *hard*-removes the Qdrant point (a soft-deleted page must not surface in
  vector search). This asymmetry is deliberate.

## Consequences

- (+) Single source of truth (Postgres) ⇒ ER diagram (D2) is generated from one model file
  (I8). Qdrant has no schema migration surface.
- (+) Qdrant remains a disposable, rebuildable index; satisfies I9 by reusing the running
  instance and never duplicating its role in Postgres.
- (+) `qdrant_point_id == pages.id` makes upsert/delete O(1) and orphan-free, directly
  enabling AC-WATCH-3/4 and AC-QD-2/3.
- (−) No cross-store transaction: a crash between step 2 and step 3 can leave a row without
  its vector. Accepted for v0.1; mitigated by the commit-Postgres-first ordering and a
  future reconciliation pass. Documented here so it is a known, bounded gap, not a surprise.
- (−) Slight payload denormalisation in Qdrant (`file_path/title/type`) can drift from
  Postgres on a partial failure; treated as advisory-only, never as a source of truth.
