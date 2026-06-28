# ADR-0001 — Incremental indexing strategy (mtime-then-hash)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.1
- Decider: solution-architect
- Invariants: I1 (incremental index only), I9 (reuse existing infra)
- Supersedes / Superseded by: —

## Context

Invariant I1 forbids any full directory rescan; a file change must update only the
affected records. The watcher (`backend/app/watcher.py`) reacts to watchdog
CREATE / MODIFY / DELETE events on `vault/raw/sources/`. For every event it must decide
whether the file actually changed, because watchdog fires on events that do not change
content (re-save with no edit, copy that preserves `mtime`, editor atomic-rename
sequences). Re-indexing an unchanged file would:

- create a duplicate Qdrant point and a redundant embedding HTTP call,
- append a spurious `log.md` entry (violates K4 append-only-but-meaningful contract),
- bump `data_version` (a false graph-recompute trigger for I2 later),

all of which functionally re-introduce bottleneck #1 of llm_wiki even without a literal
rescan loop.

Three candidate change-detection policies (functional-analyst AQ-2):

1. **mtime-only** — cheap, but unsafe: `cp -p`, restore-from-backup, and LiveSync can
   reproduce a byte-identical file with an *older or equal* mtime, or change mtime
   without changing content. Both false-negatives and false-positives occur.
2. **hash-only** — always correct, but reads and hashes the full file on every event,
   including the common no-op re-save storm an editor produces.
3. **mtime-then-hash** — use mtime as a cheap gate; compute the content hash only when
   mtime differs from the stored value; treat the **hash** as the authoritative
   change signal.

## Decision

Adopt **mtime-then-hash** as the single, locked change-detection policy.

Per MODIFY/CREATE event for a path `p` already tracked in Postgres:

1. `stat(p).st_mtime_ns` == stored `source_mtime_ns` → **SKIP** (no read, no hash, no
   write, no log, no `data_version` bump). This is the I1 fast path.
2. mtime differs → read file, compute `content_hash = sha256(raw_bytes)`.
   - `content_hash` == stored `content_hash` → **touch stored mtime only** (so the next
     event re-hits the fast path), no metadata/embedding/log/version change.
   - `content_hash` differs → **UPSERT** (metadata + Qdrant vector replace at the same
     point id), append one `log.md` line, bump `data_version`.
3. Path not yet tracked (true CREATE) → always read, hash, INSERT, embed, log, bump.

`content_hash` is the authoritative equality signal; `mtime` is only an optimisation gate.
Correctness never depends on mtime alone, which closes the `cp -p` false-negative hole.

The hash is **sha256 over the raw file bytes** (frontmatter + body), so a frontmatter-only
edit is correctly detected as a change.

## Consequences

- (+) I1 honoured strictly: the only filesystem traversal is watchdog's own event stream;
  the watcher never lists or walks the directory. The fast path performs a single `stat`
  and one integer comparison — no I/O amplification on no-op re-save storms.
- (+) Correct on copy/restore/LiveSync because the hash is authoritative.
- (+) Deterministic and trivially testable: AC-WATCH-2 simulates identical content+mtime
  (fast-path skip) and AC-WATCH-3 changes content (hash mismatch → upsert).
- (−) One extra column pair stored per page (`content_hash`, `source_mtime_ns`). Accepted;
  both are required by the policy and appear in the D2 ER diagram.
- (−) A pathological editor that rewrites identical bytes with a *new* mtime forces one
  hash computation (case 2b) but still no write. Acceptable cost.
- Forward note: the same hash gate is what a future re-embedding decision (model swap)
  will key off; no rework needed.
