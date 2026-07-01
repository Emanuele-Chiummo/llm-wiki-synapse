# ADR-0046 — Live ingest activity queue with cancel / pause / retry (F9-adjacent, watcher, I1/I7)

- Status: Accepted
- Date: 2026-07-01
- Sprint: v0.6
- Decider: solution-architect
- Invariants: **I1** (cancel cascade-deletes partial output — no half-written index),
  **I7** (retry capped at MAX_RETRIES=3, cooperative cancel checked only at loop boundaries),
  **I6** (cancellation lives in the orchestrator/queue layer — providers untouched),
  **I8** (schema delta → Alembic 0021 + `make er` regenerates `docs/er/schema.mmd`)
- Related: ADR-0001 (incremental mtime/hash gate, watcher), ADR-0003 (ingest seam),
  ADR-0006 (no startup rescan, POST /ingest/trigger), ADR-0008/0009 (IngestRun cost ledger,
  bounded loop + fallback), ADR-0018 §7 (IngestRun view fields, GET /ingest/runs),
  ADR-0005 (soft/hard delete), cascade_delete (`backend/app/ops/cascade_delete.py`)
- Reference: nashsu/llm_wiki Activity Panel (states pending/processing/failed; MAX_RETRIES=3;
  retry/cancel/pause; queue-level progress, not per-token)

## Context

Synapse ingest is **server-side and watcher-driven** (event-based), not a client-pushed
queue like nashsu/llm_wiki. `backend/app/watcher.py` (`_MarkdownHandler`) debounces FS events
per path (`_pending`/`_inflight`/`_dirty`, lines 68–142) and calls `ingest_file(path)`
(`backend/app/ingest/orchestrator.py:99`), which routes into `run_ingest_pipeline`
(orchestrator.py:319).

The `ingest_runs` row is written **once, at the END** of a run — on success at
orchestrator.py:520 and on failure at orchestrator.py:485, both via `_write_ingest_run`
(orchestrator.py:1420 → `session.add(IngestRun(...))` at 1446). There is **no
`status="running"` row while a run is in flight**, and no `source_path` column. Consequently:

- In-flight ingests are **invisible** to the API. `GET /ingest/runs` (main.py:2669) only ever
  shows terminal rows.
- The frontend `ingestStore` polls every 5 s while `runningCount > 0`, but `runningCount` is
  always ~0 because no running row is ever persisted → the poll never activates.
- There is **no handle** to an in-flight run, so cancel/pause is impossible.

We want to replicate the llm_wiki Activity Panel — a live queue with per-task **cancel**,
**retry** (bounded), and queue-level **pause/resume** — while honoring Synapse's invariants.
Because Synapse is single-vault (`settings.vault_id`), we drop llm_wiki's per-project
partitioning: the queue is global to the instance.

Two hard constraints shape the design:

1. **I1 (incremental, no half-written index).** A cancel mid-run must not leave orphan wiki
   pages, embeddings, or links. The clean-up primitive already exists: `cascade_delete(page_id)`
   (cascade_delete.py:630) removes a derived page, its Qdrant point, and dead wikilinks. We
   reuse it — it is the direct analog of llm_wiki's `cleanupWrittenFiles`.
2. **I7 (bounded).** Retry must be capped (MAX_RETRIES=3, enforced via a `retry_count` column).
   Cancellation must be **cooperative** — never a hard `Task.cancel()` mid-write that could
   tear a page half-written — and must terminate **promptly** at loop boundaries.

## Decision

Introduce a small in-process **queue manager** module, promote `ingest_runs` to carry a live
`running` row + `source_path` + `retry_count`, make the orchestrated loop check a cooperative
cancel signal at its (already bounded) iteration boundary, and add five REST endpoints. No
provider code changes (I6).

### 1. Schema delta (Alembic 0021, migration `0021_ingest_runs_queue_fields.py`)

Add two columns to `ingest_runs` (`backend/app/models.py:569`):

| Column | Type | Nullable | Default | Purpose |
|--------|------|----------|---------|---------|
| `source_path` | `Text` | yes | `NULL` | Relative raw source path (`raw/sources/…`) the run is ingesting. NULL for historical rows. Lets the queue show `filename` and lets cancel/retry target a file without a `page_id`. |
| `retry_count` | `Integer` | no | `0` (`server_default '0'`) | Times this source has been retried. Enforces MAX_RETRIES=3 (I7). |

`finished_at` is **already** non-null with `server_default now()` (models.py:670). We keep it
non-null: a `running` row simply has `finished_at == started_at` at insert; the terminal UPDATE
overwrites it. `GET /ingest/runs` already nulls `completed_at` in the response when
`status == "running"` (main.py:2740) — that response mapping needs **no change**.

The `status` column comment (models.py:685) already documents `running`; the value now
actually occurs. Migration is additive (two `add_column`) — no backfill needed beyond the
column server-defaults.

**I8:** after the migration, run `make er` to regenerate `docs/er/schema.mmd` so the ER
diagram matches the live schema. No sprint-done without it.

### 2. Run lifecycle: insert `running` at START, UPDATE to terminal at END

Split `_write_ingest_run` (orchestrator.py:1420) into an **insert-running** and an
**update-terminal** operation, and thread a `run_id` through `run_ingest_pipeline`
(orchestrator.py:319):

- **At START** (top of `run_ingest_pipeline`, before the route try-block at ~line 354):
  `run_id = await _open_ingest_run(source_path=origin_source, provider_name=caps.name,
  provider_type=caps.mode, model_id=…, route=route, started_at=started_at)`.
  This `INSERT`s one row with `id=run_id`, `status="running"`, `source_path=origin_source`,
  `started_at=now`, `finished_at=now` (placeholder), zeros for cost/tokens/pages, and
  `retry_count` copied from the queue manager's per-path counter (see §5 retry).
- **At END** (both the success path ~line 520 and the failure path ~line 485): call
  `_finalize_ingest_run(run_id, …)` which `UPDATE ... WHERE id = run_id` sets the terminal
  `status`, `finished_at`, `total_cost_usd`, `total_tokens`, `pages_created`, `converged`,
  `cost_anomaly`, `error_message`. This replaces the current `session.add(IngestRun(...))`
  INSERT — the row already exists.
- **On cancel** (see §3): the finally path sets `status="cancelled"` (a new terminal value;
  extend the `status` column comment to `running | completed | failed | converged_false |
  cancelled`).

The `run_id` is generated at START (`uuid.uuid4()`), registered in the queue manager keyed by
`origin_source` **and** returned so the cancel endpoint can target it. It is threaded to
`write_wiki_page` calls (orchestrator.py:424) so each written page_id is recorded against the
run for cancel-time cascade cleanup (see §3).

`ingest_file` (orchestrator.py:99) is the caller for the watcher path. The mechanical
source-indexing that follows the pipeline (persist_metadata/upsert_vector, lines 167–209) is
**inside** the run's lifetime; the running row therefore also covers the source-summary index.

### 3. Cooperative cancellation

New module **`backend/app/ingest/queue_manager.py`** — a single module-level singleton
`ingest_queue` (mirrors the `_watcher` singleton pattern, watcher.py:243). It owns:

```python
class RunHandle:
    run_id: uuid.UUID
    source_path: str                 # relative raw/sources/... path
    cancel_event: asyncio.Event      # set() by cancel(run_id)
    written_page_ids: list[uuid.UUID]  # appended by write_wiki_page during the run
    started_at: datetime

class IngestQueueManager:
    _active: dict[str, RunHandle]        # source_path -> handle (in-flight)
    _pending: dict[str, PendingEntry]    # source_path -> queued-but-not-dispatched (§4)
    _retry_counts: dict[str, int]        # source_path -> retries so far (I7)
    _paused: bool
    _completed_since_idle: int
```

**Registration.** `run_ingest_pipeline` calls `ingest_queue.open(run_id, source_path)` at
START (returns the `RunHandle`) and `ingest_queue.close(source_path, terminal_status)` in a
`finally`. `close` removes the active handle and, if the run finished cleanly, bumps
`_completed_since_idle`; when both `_active` and `_pending` are empty it resets that counter to
current on next transition (queue idle = "done removed", llm_wiki semantics).

**Cancel request.** `cancel(run_id)` looks up the handle by run_id, calls
`handle.cancel_event.set()`, and returns whether a run was actually in flight.

**Cancel checkpoints (I7 — bounded, loop-boundary only).** We do **not** hard-cancel the
asyncio Task (that risks a torn write mid-`write_wiki_page`, violating I1). Instead the
orchestrated loop checks the event at its natural boundary. In
`run_orchestrated_loop` (`backend/app/ingest/loop.py:140`, the `for i in range(1, max_iter+1)`
head), add at the **top of each iteration**, before `generate()`:

```python
if cancel_event is not None and cancel_event.is_set():
    stop_reason = "cancelled"
    raise IngestCancelled(origin_source)
```

`cancel_event` is passed down from `run_ingest_pipeline` → `_run_orchestrated`
(orchestrator.py:598) → `run_orchestrated_loop`. This is a **bounded, cooperative** check: it
fires between provider calls, never mid-call, so at most one in-flight `generate()` completes
before abort. Because the loop is already `max_iter`-bounded (I7), no new unbounded surface is
added.

**Delegated (CLI) route.** `supports_agentic_loop == True` runs the provider's own agent loop
inside `_delegate_ingest` (orchestrator.py:651) — Synapse cannot inject a boundary check there
without touching provider internals (**I6 forbids it**). Decision: for the delegated route,
cancel is **best-effort deferred** — the event is checked once *after* `_delegate_ingest`
returns and *before* the post-write hooks (overview/proposals). If set, we skip the hooks and
proceed straight to cascade cleanup. The UI must therefore label cancel on a delegated run as
"will clean up when the current step finishes." (Hard `asyncio.Task.cancel()` is explicitly
rejected: it cannot be scoped to a loop boundary through the SDK and could tear a page write.)

**Partial-output cleanup (I1).** On `IngestCancelled` (or the deferred delegated skip), the
`run_ingest_pipeline` handler:

1. iterates `handle.written_page_ids` and calls `cascade_delete(page_id)`
   (cascade_delete.py:630) for each — removing the derived wiki page, its Qdrant point, and any
   dead wikilinks. cascade_delete's shared-entity preservation (rule 6) protects pages that
   another live source still references, so cancelling one ingest never nukes a page a prior
   ingest legitimately owns.
2. calls `_finalize_ingest_run(run_id, status="cancelled", pages_created=0,
   error_message="cancelled by user", total_cost_usd=<accumulated so far>)` — the I7 cost
   ledger stays truthful (cost incurred before abort is still recorded).
3. does **not** re-raise into the watcher (cancel is a normal, user-initiated terminal state).

The raw `raw/sources/<file>` document is **NOT** deleted on cancel (unlike a full
cascade-delete, cascade_delete.py step 9). Cancel aborts *processing*; the source file stays so
the user can retry. `cascade_delete(page_id)` deletes the raw file only for a *source* page;
here we pass the **derived** page_ids only, so the raw file is untouched. If a derived page IS
the source-summary page whose file_path starts with `raw/sources/`, exclude it from the
cascade set and instead soft-handle it — see §6 "needs-care" for backend-engineer.

**Watcher re-fire suppression.** A cancelled file must not immediately re-ingest. On cancel,
the queue manager records `source_path` in a short-lived `_suppress: dict[str, float]`
(path → monotonic deadline, window = `2 × WATCH_DEBOUNCE_SECONDS`). The watcher's `_fire`
(watcher.py:120) consults `ingest_queue.should_skip(path)` before dispatching; if suppressed,
it drops the event and clears the entry. This prevents the cancel's own `cascade_delete` file
mutations (and any editor re-touch) from instantly re-triggering. A genuine user edit *after*
the window re-ingests normally (I1 unaffected — mtime/hash gate still governs).

### 4. Pause / resume (queue gating, status-mirror only — no rescan, I1)

`pause()` sets `_paused = True`. `resume()` sets it False and drains `_pending`.

Because the watcher is event-based (no explicit enqueue like llm_wiki), gating happens at the
**dispatch boundary** in the watcher's `_fire` (watcher.py:120): before creating the ingest
task, call `ingest_queue.admit(path, action)`:

- **not paused** → `admit` returns `True`; watcher proceeds exactly as today (`_inflight` +
  `create_task(_run)`).
- **paused** → `admit` records `PendingEntry(source_path, action, first_seen_at)` in
  `_pending` and returns `False`; watcher does **not** dispatch. The debounce/`_dirty`
  machinery is unchanged — a pending path that gets re-saved just updates its `PendingEntry`
  action (last-writer-wins), it does not stack.

`resume()` replays each `_pending` entry through the watcher's normal `_arm(path, action)`
seam (a public hook the queue manager calls back into) so they flow through the *same*
debounce → `_fire` → `ingest_file` path. This is a **status mirror only**: `_pending` holds
paths already surfaced by FS events — the manager never enumerates the vault (I1 preserved; no
`listdir`/`glob`/`rglob`, consistent with watcher.py's contract, lines 13/183).

Queue counts are derived from the manager's live maps — never from a scan:
- `pending` = `len(_pending)`
- `processing` = `len(_active)`
- `failed` = count of in-window handles/entries whose last terminal status was `failed`
  (the manager keeps a small `_recent_failed: dict[str, FailedEntry]` so failed tasks stay
  visible for retry, matching llm_wiki where `done` is removed but `failed` persists).
- `completed_since_idle` = `_completed_since_idle`
- `total` = `pending + processing + len(_recent_failed)`

### 5. Retry (bounded, I7)

`retry(run_id)`:
1. resolve the run's `source_path` (from the manager's failed entry, or the `ingest_runs` row).
2. read `_retry_counts[source_path]` (0 if absent). If `>= MAX_RETRIES` (3) → reject
   (see §6 API `409`).
3. increment `_retry_counts[source_path]`, drop the `_recent_failed` entry, and re-dispatch by
   calling the watcher's `_arm(abs_path, "ingest")` (same seam resume uses). The new run's
   `_open_ingest_run` reads the bumped count into the row's `retry_count` column.

MAX_RETRIES lives as a module constant `MAX_INGEST_RETRIES = 3` in `queue_manager.py`. A
successful run clears `_retry_counts[source_path]`.

### 6. API contracts

**`GET /ingest/queue`** — live snapshot from the queue manager (no DB scan for the summary;
`tasks` reads `_active`/`_pending`/`_recent_failed`, enriched with the running-row fields):

```json
{
  "paused": false,
  "pending": 2,
  "processing": 1,
  "failed": 1,
  "completed_since_idle": 4,
  "total": 4,
  "tasks": [
    { "run_id": "3f…", "source_path": "raw/sources/paper.md", "filename": "paper.md",
      "status": "processing", "retry_count": 0, "error": null,
      "started_at": "2026-07-01T18:22:04Z" },
    { "run_id": null, "source_path": "raw/sources/notes.md", "filename": "notes.md",
      "status": "pending", "retry_count": 0, "error": null, "started_at": null },
    { "run_id": "a1…", "source_path": "raw/sources/bad.md", "filename": "bad.md",
      "status": "failed", "retry_count": 1,
      "error": "schema validation failed", "started_at": "2026-07-01T18:20:00Z" }
  ]
}
```
- `run_id` is `null` for `pending` tasks (no row opened yet).
- `status` ∈ `pending | processing | failed`. `completed`/`cancelled` tasks drop out of
  `tasks` (llm_wiki: done removed) but `completed_since_idle` counts them until the queue
  goes idle.
- Cheap read: pure in-memory maps + optional 1 indexed SELECT for failed-row enrichment. No
  heavy compute (respects I2/I3 spirit — this endpoint is polled).

**`POST /ingest/runs/{id}/cancel`** →
```json
{ "run_id": "3f…", "status": "cancelling", "cleaned_pages": 0 }
```
- `202` while abort is requested; the cascade cleanup completes asynchronously at the next
  loop boundary. `cleaned_pages` is `0` at request time (final count logged, not awaited).
- `404` if `run_id` unknown. `409` if the run is already terminal
  (`completed|failed|converged_false|cancelled`) — cannot cancel a finished run.

**`POST /ingest/runs/{id}/retry`** →
```json
{ "run_id_prev": "a1…", "source_path": "raw/sources/bad.md",
  "retry_count": 2, "status": "queued" }
```
- `202`; a new run will open on re-dispatch.
- `404` if `run_id` unknown. `409` if the prior run is **not** in a retryable state (only
  `failed`/`cancelled`/`converged_false` are retryable; retrying a `running` or `completed`
  run is rejected). `409` with `detail:"max_retries_exceeded"` if
  `retry_count >= MAX_INGEST_RETRIES` (I7).

**`POST /ingest/queue/pause`** → `{ "paused": true }` (idempotent; `200`).

**`POST /ingest/queue/resume`** → `{ "paused": false, "drained": 2 }` (`drained` = pending
entries replayed; idempotent; `200`).

All five endpoints live in `backend/app/main.py` near the existing ingest block
(after `/ingest/runs`, ~line 2728), reusing `IngestTriggerResponse`-style typed Pydantic
models so they appear in OpenAPI (D4).

## Invariant compliance

- **I1** — cancel calls `cascade_delete` on every page written this run; the index never
  retains half-written pages/embeddings/links. Pause/resume and queue counts are pure status
  mirrors of FS-event-surfaced paths — **no vault enumeration** anywhere (no listdir/glob).
  The mtime/hash gate (ADR-0001) still governs all (re)ingest.
- **I6** — no provider or InferenceProvider file is touched. The cancel event is threaded as a
  plain optional arg into the orchestrator loop and checked *outside* any provider call.
  Delegated (CLI) route cancel is deferred to a post-return boundary precisely because we will
  not reach into provider internals.
- **I7** — retry hard-capped at `MAX_INGEST_RETRIES = 3` with a `retry_count` column and a
  `409` on exceed. Cancel is cooperative and checked only at the already-bounded
  `max_iter`/`token_budget` loop boundary — no new unbounded loop, prompt termination.
- **I8** — schema change ships as Alembic `0021_ingest_runs_queue_fields.py`; `make er`
  regenerates `docs/er/schema.mmd` in the same PR; OpenAPI (D4) picks up the five new
  endpoints automatically. A sequence-diagram stub (D3) for cancel→cascade is handed to
  tech-writer.

## Consequences

**Positive**
- In-flight ingests become visible (the `running` row finally exists); the frontend's existing
  5 s poll activates as designed.
- Users get llm_wiki-parity control (cancel/retry/pause) without per-token machinery — queue
  is file-granular, matching I3's "no per-token heavy work" ethos.
- Reuses `cascade_delete` and the watcher's `_arm` seam — minimal new surface, one new module.

**Negative / trade-offs**
- Cancel latency = up to one provider `generate()` call (orchestrated) or one full agent step
  (delegated). We accept this over a hard `Task.cancel()` that could tear a write (I1). UI must
  communicate "cancelling…".
- Queue state is **in-process** (module singleton). A backend restart loses `_pending`/
  `_active`; any run interrupted by a crash is left as a stale `running` row. Mitigation
  (backend-engineer, needs-care): a lifespan-startup sweep that marks orphan `running` rows
  (finished_at == started_at, older than a threshold) as `failed` with
  `error_message="interrupted (backend restart)"`. This is a status fix only — **no rescan,
  no re-ingest** (I1). `_pending` loss is acceptable: those files are still on disk; a future
  edit re-surfaces them, and the startup notice (ADR-0006) already tells users pre-existing
  files aren't auto-indexed.
- Single-vault only (by design — `settings.vault_id`); llm_wiki's per-project queue is dropped.

## Split of work

**backend-engineer**
- `models.py:569` — add `source_path`, `retry_count`. *SAFE* (additive).
- `backend/alembic/versions/0021_ingest_runs_queue_fields.py` — two `add_column`. *SAFE*.
- `make er` → `docs/er/schema.mmd`. *SAFE* (generated).
- `backend/app/ingest/queue_manager.py` — new module: singleton, RunHandle, admit/open/close/
  cancel/retry/pause/resume/should_skip, `MAX_INGEST_RETRIES=3`. *needs-care* (concurrency:
  all mutations happen on the loop thread, mirror watcher's no-lock invariant, lines 65–76).
- `orchestrator.py` — split `_write_ingest_run` (1420) into `_open_ingest_run` (INSERT running
  at ~354) + `_finalize_ingest_run` (UPDATE at 485 & 520); thread `run_id`/`cancel_event`;
  record written page_ids on the handle at the `write_wiki_page` loop (424); add the
  `IngestCancelled` handler that cascade-deletes and finalizes as `cancelled`. *needs-care*
  (must not change provider/loop cost logic — NB guard; delegated-route deferred-cancel
  boundary; exclude any raw/sources/ source-summary page from the cascade set).
- `backend/app/ingest/loop.py:140` — top-of-iteration `cancel_event.is_set()` check raising
  `IngestCancelled`. *needs-care* (boundary only — never inside a provider call, I7).
- `watcher.py` — `_fire` (120) consults `should_skip` + `admit`; expose `_arm` for
  resume/retry replay. *needs-care* (preserve `_inflight`/`_dirty` coalescing, I1).
- `main.py` (~after 2728) — five endpoints + typed models; lifespan orphan-`running` sweep.
  *needs-care* (409/404 error cases exactly as §6; sweep is status-only, no re-ingest).

**frontend-engineer**
- ActivityBar → activity panel: poll `GET /ingest/queue` (extend/replace `ingestStore`'s
  existing 5 s poll). *SAFE*.
- Per-task action buttons → `cancel`/`retry`; queue-level pause/resume toggle. *SAFE*.
- Auto-expand panel while `processing > 0` (llm_wiki behavior). *SAFE*.
- Disable retry when `retry_count >= 3`; surface "cancelling…" transient state. *needs-care*
  (reflect deferred cancel latency; do not spam-poll — keep 5 s, no per-token, I3).
- i18n (IT/EN) strings for states/actions. *SAFE*.
- NO force layout / heavy render in the panel (I2/I3/I4 — virtualize the task list if long,
  TanStack Virtual). *needs-care*.

## Handoffs
- ADR (this file) → tech-writer.
- Interface contracts (§6 API, `queue_manager.py` surface) → backend-engineer, frontend-engineer.
- Cancel→cascade sequence-diagram stub (D3) → tech-writer.
- ER regeneration reminder (D2) → devops-engineer / backend-engineer.
- PR verdicts → orchestrator.
