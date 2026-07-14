# Parity E2E Runbook — Synapse vs nashsu/llm_wiki (WS-G, ADR-0083)

The closing gate of the v1.7.0 program: ingest the SAME documents in both apps with the SAME
provider and confirm the outputs are comparable. LLM output at temperature 0.1 is not
byte-deterministic, so we compare **distributions inside tolerance bands**, not exact files —
`scripts/parity_e2e/compare.py` computes the scorecard and exits non-zero on any band violation.

## Corpus

- **Deterministic corpus** (committed): `backend/tests/fixtures/parity_corpus/` — 3 small docs on a
  coherent ML topic engineered to exercise cross-linking (shared entities: Transformer, attention,
  Vaswani), a comparison (GPT vs BERT), and a planted contradiction + open question (context-window
  debate). Use these for the repeatable CI-style check.
- **Emanuele's 3 real documents**: supplied at run time for an additional real-world validation.
  Same procedure; run the comparator twice (once per corpus).

## 1. Gold — llm_wiki.app (manual; a Tauri app, not CI-automatable)

1. Open `/Applications/LLM Wiki.app`.
2. New Project → Template **General**, AI output language **English**, provider **Claude CLI**.
3. Copy the 3 corpus docs into the project's `raw/sources/`.
4. Let `autoIngest` drain (the queue empties; the review sweep runs on drain).
5. Snapshot the vault's `wiki/` tree to `backend/tests/fixtures/parity_gold/` (or point the
   comparator at the live project folder). Record the total `[[wikilink]]` count for the sentinel.
6. Refresh the gold when the llm_wiki version or the corpus changes.

## 2. Candidate — Synapse

1. Bring up the real stack: `docker compose -f docker-compose.ci.yml up` (real Postgres — a green
   SQLite unit suite does NOT prove Postgres SQL; see the live-test rule).
2. Create a vault via the onboarding wizard: **General**, language **English**, provider **Claude
   CLI** (delegated route — this is the path the link fix targets, PR6b).
3. Copy the same 3 corpus docs into the vault's `raw/sources/` (or POST to the ingest endpoint).
4. Wait for the ingest queue to drain (`GET /ingest/queue` empty); the review sweep fires on drain.

## 3. Score

```
python scripts/parity_e2e/compare.py \
    --gold <llm_wiki_vault> \
    --candidate <synapse_vault> \
    --baseline-links <recorded 1.5.6 total>
```

Bands (see `compare.py`): source pages == 3; pages-per-type within ±1 or ±40% (total ±30%);
wikilink density 0.7×–2× gold; **total links ≥ the 1.5.6 baseline** (the regression sentinel);
link resolution ≥ 0.6× gold; index.md has `## Recently Updated`; log.md has ≥3
`## [YYYY-MM-DD] ingest | Title` entries. Exit 0 ⇒ comparable.

## 4. Review-queue comparison (not covered by the file comparator)

The comparator reads vault files only. Compare review surfaces separately:
- **Synapse**: `GET /review/queue` — count pending items by type; confirm ≥1 contradiction-or-query
  item for the planted contradiction, and that `suggestion`/`missing-page` items carry non-empty
  `search_queries`.
- **llm_wiki**: `<project>/.llm-wiki/review.json` — same counts.
- Band: pending count within ±50% of gold, and the contradiction is surfaced by both.

## 5. Verdict

Close the program when: the file scorecard passes all bands on BOTH corpora, the review-queue
comparison is within band, `wiki/` remains a valid Obsidian vault (I5), and the Synapse total
wikilink count is at or above the 1.5.6 baseline (the regression is fixed). Then cut the release
(CHANGELOG 1.7.0, release notes, tag).
