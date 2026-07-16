# ADR-0083 — Parity E2E harness and tolerance bands (v1.7.0)

- **Status:** Accepted
- **Date:** 2026-07-14
- **Invariants touched:** I5, I8
- **Runbook:** `docs/process/PARITY-E2E-RUNBOOK.md`

## Context

The 1.7.0 program's closing gate is a 1:1 behavioral comparison: the same documents ingested in
both nashsu/llm_wiki and Synapse (same Claude CLI provider) must produce comparable wikis. LLM
output at temperature 0.1 is not byte-deterministic, and llm_wiki is a Tauri desktop app that can't
be driven headlessly in CI. So the harness must (a) compare distributions inside tolerance bands,
not exact bytes, and (b) accept a manually-produced gold snapshot.

## Decision

1. **Deterministic corpus** in `backend/tests/fixtures/parity_corpus/` — 3 engineered docs (shared
   entities for cross-linking, a comparison pair, a planted contradiction + open question), so the
   check is repeatable independent of Emanuele's real documents (which are run additionally).
2. **File-based comparator** `scripts/parity_e2e/compare.py`, reusing the per-vault `analyse()` from
   the existing `scripts/parity_report.py` and adding a wikilink-density metric + a regression
   sentinel. It walks the two `wiki/` trees and applies bands: source pages == 3; pages-per-type
   within ±1 or ±40% (total ±30%); wikilink density 0.7×–2× gold; **total links ≥ the recorded 1.5.6
   baseline** (the sentinel that proves the regression is fixed); link resolution ≥ 0.6× gold;
   index.md `## Recently Updated` present; log.md ≥3 `## [YYYY-MM-DD] ingest | Title` entries. Exits
   non-zero on any violation so it can gate the release.
3. **Manual gold + runbook.** llm_wiki gold is produced by a documented manual procedure (General
   template, English, Claude CLI, drop the corpus, let autoIngest drain, snapshot). The review-queue
   comparison (Synapse `GET /review/queue` vs llm_wiki `.llm-wiki/review.json`) is a separate
   documented check — the file comparator covers vault files only.
4. **Live stack for the candidate.** Synapse is exercised on `docker-compose.ci.yml` (real Postgres)
   — a green SQLite unit suite does not prove Postgres SQL.

## Consequences

- The link-regression fix is falsifiable and gated: the sentinel fails the release if the total
  wikilink count regresses below 1.5.6.
- The comparator is deterministic and reusable (no LLM in the scoring path); only the vault
  *generation* is nondeterministic, which the bands absorb.
- The E2E's provider is the CLI/delegated route — the path PR6b fixes — matching how Emanuele runs
  the comparison. I5 is asserted (the candidate `wiki/` must remain a valid Obsidian vault).
- Not wired as a default CI job (needs a manual gold + the CLI provider); it is a release-gate
  runbook plus a standalone, CI-shaped comparator.
