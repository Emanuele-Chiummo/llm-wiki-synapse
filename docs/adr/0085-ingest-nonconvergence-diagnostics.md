# ADR-0085 — Ingest non-convergence diagnostics + on-demand live-smoke lane (v1.9.1 W5)

- **Status:** Accepted
- **Date:** 2026-07-16
- **Invariants touched:** I6, I7, I8
- **Finding:** NC-1 (observed live 2026-07-16), QA-TEST-2

## Context

A live CLI ingest run on a long FinOps document surfaced "Non convergito" in the UI with no
further explanation, after $1.90 of cost and a single fallback source-summary page. The root
cause: `app.ingest.block_loop.run_block_loop` (and the JSON twin,
`app.ingest.loop.run_orchestrated_loop`) exhausted `max_iter` because `_validate_block_batch` /
`validate_pages` kept failing, and while the loop logs `stop_reason` + the last iteration's
validation errors (`logger.warning`), neither was ever persisted. `ingest_runs.error_message` is
`NULL` by design on `converged_false` rows (ADR-0018 §7 — reserved for actual run *failures*, not
non-convergence), so there was nowhere for the UI to read an explanation from.

Separately, QA-TEST-2 asked for an on-demand lane that exercises the live 3-provider smoke matrix
(`tests/test_smoke_providers.py`, `@pytest.mark.live`) and the ADR-0083 parity/E2E harness, without
adding either to the default merge-blocking CI (`ci.yml`'s `unit` job runs a plain
`python -m pytest -q` with no `-m` filter — the `live` marker existed but was never excluded).

## Decision

1. **`ingest_runs.diagnostics`** (migration 0035, nullable JSON/JSONB):
   `{stop_reason, iterations, last_errors, tokens_used, token_budget}`. Populated by BOTH loop
   shapes on every terminal outcome (converged or not) via a `diagnostics()` method added to the
   existing result dataclasses (`LoopResult`, `BlockLoopResult`) — no parallel channel. `NULL` on
   the delegated/CLI route (no bounded loop to report) and for legacy rows.
2. **API**: `GET /ingest/runs` / `IngestRunResponse` gains a `diagnostics` field, mapped straight
   from the ORM row.
3. **UI**: `IngestRunDetail` renders a "why it didn't converge" panel — stop reason, iterations
   run, tokens used vs. budget, and the last validation errors — whenever
   `diagnostics.stop_reason != "converged"`, replacing the bare "Non convergito" label with an
   actionable explanation. i18n keys added to both `en.json`/`it.json` (parity maintained).
4. **Retry-with-context (stretch, deferred)**: `POST /ingest/runs/{id}/retry`
   (`IngestQueueManager.request_retry`) re-dispatches purely by `source_path` — it re-reads the
   raw file and reruns the pipeline from scratch, with no seam for injecting prior validation
   errors into the retry's provider call. Building one is a distinct feature (new retry-context
   plumbing through the queue manager + pipeline + prompt builder), not a corollary of persisting
   diagnostics, so it is explicitly deferred rather than bolted on here.
5. **`pyproject.toml`**: `addopts = "-m 'not live'"` — the `live` marker existed
   (`tests/test_smoke_providers.py`) but nothing excluded it from the default `unit` CI job.
   pytest's `-m` flag is store-not-append (the last one on the command line wins), so the new
   `.github/workflows/live-smoke.yml` overrides with an explicit `pytest -m live`.
6. **`.github/workflows/live-smoke.yml`** (`workflow_dispatch` only, never on push/PR): one job
   runs the live 3-provider smoke matrix (each test self-skips via `pytest.mark.skipif` when its
   own env var is absent, so the job degrades gracefully rather than failing on a runner missing a
   given backend); a second, gold-gated job optionally runs the ADR-0083 comparator against a
   pre-produced llm_wiki gold snapshot path supplied at dispatch time (the gold is a manual,
   Tauri-desktop artifact per `docs/process/PARITY-E2E-RUNBOOK.md` §1 — not reproducible headlessly
   on a GitHub-hosted runner, so this job is a no-op notice, not a failure, when no gold path is
   supplied).

## Consequences

- A `converged_false` ingest run is now self-explanatory in the UI: the operator sees the same
  stop reason and validation errors that previously only existed in backend logs.
- `ingest_runs` gains one nullable column (migration 0035, additive/non-destructive); ER + OpenAPI
  regenerated (I8).
- The `live` test lane is now truly opt-in: normal CI is unaffected (2984 tests, unchanged
  baseline other than the 2 new diagnostics assertions' worth of coverage), and a human explicitly
  dispatches `live-smoke.yml` before a release, per the existing "live-test before release" house
  rule.
- The retry-with-injected-context UX improvement remains a backlog item, not silently dropped.
