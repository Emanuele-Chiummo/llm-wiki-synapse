# ADR-0079 — WS-C Review parity: drain-sweep, stub-create, block-review enqueue (v1.7.0)

- **Status:** Accepted
- **Date:** 2026-07-14
- **Amends:** ADR-0034 (review proposal model) — adds mode parameter to create endpoint;
  ADR-0046 (IngestQueueManager) — adds drain callback; ADR-0076 (block pipeline) — closes PR5c TODO
- **Invariants touched:** I1, I6, I7, I8, F9
- **Reference:** `docs/reference/LLMWIKI-CORE-LOGIC-v0.6.3.md §2` (REVIEW model),
  `§1.1` (queue drain → sweep), `llm_wiki/src/lib/ingest-queue.ts:636 onQueueDrained`,
  `llm_wiki/src/lib/review-create-page.ts` (Create Page stub)

---

## Context

Three review behaviours in Synapse differed from nashsu/llm_wiki v0.6.3:

| Gap | llm_wiki behaviour | Synapse (before this ADR) |
|-----|--------------------|--------------------------|
| **Sweep trigger** | `onQueueDrained` — fires once when the ingest queue drains after one or more completed runs | `sweep_reviews()` called after **every** individual ingest run, even in rapid multi-file batches |
| **Create Page default** | Writes a deterministic `# <title>\n\n<description>` stub with keyword-detected type — no LLM call | Only one path existed (full LLM generation via `_run_generation`) — no stub shortcut |
| **Block-loop REVIEW blocks** | REVIEW blocks returned by the generation loop are enqueued as pending proposals | A `# TODO` comment in `_run_orchestrated_blocks` left these blocks un-enqueued |

This ADR closes all three gaps in a single sprint (WS-C).

---

## Decision

### §1 — Drain-based sweep (replaces per-run sweep)

`IngestQueueManager` (ADR-0046) gains a single async drain callback:

```python
def set_on_drained(callback: Callable[[], Awaitable[None]] | None) -> None: ...
```

`finalize()` schedules the callback as a fire-and-forget `asyncio.create_task()` exactly
once when both `_active` and `_pending` are empty AND `_completed_since_idle > 0`.  
A `_drain_in_flight: bool` guard debounces rapid successive drains (the second drain while
the first callback is still running is silently skipped).

`app/main.py` lifespan registers `sweep_reviews(vault_id)` as the drain callback after the
watcher starts. The per-run `sweep_reviews()` calls in `run_ingest_pipeline` (delegated and
orchestrated routes) are removed and replaced with a comment explaining that sweep is now
drain-driven.

**Rationale:** a burst of 10 file edits triggers one sweep after all 10 complete, not 10
sweeps — cheaper and matches llm_wiki parity.

### §2 — Deterministic stub create (new default)

`create_page_from_review` gains a keyword argument `mode: str = "stub"` (default `"stub"`).

**`mode="stub"` (new default):**
- Writes `# <title>\n\n<description>` via `write_wiki_page` without calling any LLM provider.
- Page type detected by `_detect_page_type(item_type, title)` — ports `detectPageType` from
  `review-create-page.ts`:
  1. `missing-page` → `concept`
  2. `contradiction` / `suggestion` → `query`
  3. Keyword scan (EN + CJK): entity → comparison → synthesis → concept → query (default)
- Fan-out on comma-delimited `missing-page` titles is preserved (same as `mode="generate"`).
- I6-neutral: never calls `resolve_provider_config`.
- Tags page with `["stub"]` in frontmatter.
- Post-write fire-and-forget `sweep_reviews()` to auto-resolve sibling proposals.

**`mode="generate"` (previous behaviour, now explicit):**
- Full LLM generation path via `_run_generation` — unchanged.
- Requires a configured provider (raises 409 if none configured, I6).

The REST API (POST `/review/queue/{id}/approve` and POST `/review/queue/{id}/create`) accepts
an optional JSON body `{"mode": "stub" | "generate"}` (default `"stub"` when body absent).

**Rationale:** llm_wiki's Create Page button writes a stub immediately — the human (K8) can
then enrich it or trigger Deep Research. Full generation is an explicit opt-in.

### §3 — Block-loop REVIEW block enqueue (PR5c TODO closed)

`_run_orchestrated_blocks` in `app/ingest/pipeline.py` now enqueues every `ReviewBlock`
returned by `run_block_loop` as a `ReviewItem` via `enqueue_review`:

- `proposal_origin = "ai"` (the model produced the block).
- `content_key` dedup via `_content_key(vault_id, item_type, title)` — idempotent on re-ingest.
- Unknown block types are normalised to `"suggestion"`.
- Soft-capped at `_BLOCK_REVIEW_ENQUEUE_CAP = 50` per run (I7).
- Non-fatal: any `enqueue_review` failure is caught, logged as WARNING, and does NOT fail
  the ingest run (blocks are advisory).

**Rationale:** REVIEW blocks are the model's way of flagging knowledge gaps discovered during
ingest; surfacing them in the HITL queue (F9) closes the feedback loop.

---

## Consequences

### Positive

- One sweep per queue drain instead of N sweeps per N concurrent ingests → lower DB load.
- Stub create matches llm_wiki UX: instant response, no provider required, human edits next.
- Block-loop review flags now surface in the review queue without requiring a separate step.

### Negative / trade-offs

- The drain callback debounce means a sweep is skipped if a second drain completes while
  the first sweep is still running. This is acceptable: the next drain will fire another sweep.
- Stub pages are tagged `["stub"]` and lack AI-generated body — intentional, requires human
  follow-up or mode="generate".

### Test coverage added

| Test file | New tests | What they cover |
|-----------|-----------|-----------------|
| `tests/test_ingest_queue.py` | 7 (`TestDrainCallback`) | Drain fires on idle+work, skips on failure/no-work/in-flight, debounce, clear |
| `tests/test_review_stub_create.py` | 18 (`TestDetectPageType`, `TestStubCreate`) | Keyword detection (11 cases), stub write/content/type/default-mode/fan-out/generate-regression |
| `tests/test_pipeline_blocks_format.py` | 1 (`test_blocks_format_enqueues_review_blocks`) | REVIEW block → review_items row (D3/WS-C) |
| `tests/test_review.py` | 4 updated | `TestMissingPageFanOut` — explicit `mode="generate"` |
| `tests/test_review_adr0034.py` | 1 updated | `test_approve_returns_409_when_no_provider` — POST with `{"mode":"generate"}` |
| `tests/test_review_ai_adr0034.py` | 5 updated | `TestCreateGeneration` — all calls explicit `mode="generate"` |

---

## Files changed

| File | Change |
|------|--------|
| `app/ingest/queue_manager.py` | `set_on_drained()`, `_on_drained`, `_drain_in_flight`, drain scheduling in `finalize()` |
| `app/main.py` | Register drain sweep callback after watcher start (lifespan) |
| `app/ingest/pipeline.py` | Remove per-run sweeps; add block review enqueue in `_run_orchestrated_blocks` |
| `app/ops/review.py` | `_detect_page_type()`, `_create_stub_from_review()`, `create_page_from_review(mode=)` |
| `app/routers/review.py` | `CreateReviewBody` model; `approve_review_item` + `create_review_item` accept optional body |
| `docs/adr/0079-review-parity.md` | This document |
