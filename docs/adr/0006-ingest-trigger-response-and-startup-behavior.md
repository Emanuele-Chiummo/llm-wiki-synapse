# ADR-0006 — POST /ingest/trigger response contract and startup behaviour

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.1
- Decider: solution-architect
- Invariants: I1 (no startup rescan), I8 (OpenAPI feeds D4)
- Resolves: AQ-3, AQ-6
- Related: ADR-0003 (thin seam), v0.2 async ingest

## Context

Two API/runtime contracts must be locked before engineers code, both forward-looking:

- **AQ-6 — `POST /ingest/trigger` response shape.** v0.1 runs ingest *synchronously* and
  returns 202 (AC-REST-4). v0.2 makes it async with a real task id. The v0.1 response must
  be shaped so the v0.2 change is non-breaking.
- **AQ-3 — startup with pre-existing files.** I1 forbids a startup rescan (AC-WATCH-5). The
  test harness needs to know whether the service is silent or logs that unscanned files
  exist.

## Decision

**AQ-6 — response contract for `POST /ingest/trigger`:**

- Request body: `{"file_path": "<path under vault/raw/sources/>"}` (422 on missing/invalid).
- v0.1 runs `ingest_file` synchronously, then returns **HTTP 202** with body:

  ```json
  { "task_id": null, "status": "completed", "page_id": "<uuid>" }
  ```

- `status` is an enum: v0.1 emits only `"completed"` (or `"skipped"` when the I1 fast-path
  determined no change — `page_id` then references the existing page). v0.2 adds
  `"queued"` / `"running"`.
- `task_id` is `null` in v0.1 (synchronous, no task). v0.2 fills it with a real async id and
  flips the default `status` to `"queued"`; existing clients that read `page_id` keep
  working, and clients that poll on `task_id` are a pure addition. **No field is removed or
  retyped**, so v0.2 is non-breaking.
- 202 (not 200) is deliberate even though v0.1 is synchronous: it pre-commits the "accepted,
  may be async" semantics so the status code itself does not change in v0.2.

**AQ-3 — startup behaviour with pre-existing files:**

- The watcher registers watchdog handlers only; it **never enumerates** `vault/raw/sources/`
  on startup (I1, AC-WATCH-5). No DB write happens at startup from pre-existing files.
- On startup the service emits exactly one **INFO** log line of the form
  `startup: watching <abs path>; pre-existing files are NOT auto-indexed (I1). Use POST
  /ingest/trigger to index them.` when the watched directory is non-empty. It is an
  informational notice, **not** a warning and **not** an action — it changes no state.
- The test asserts both: zero `pages` rows written during startup *and* the presence of this
  INFO line when the directory was pre-populated. This removes the AQ-3 ambiguity for QA.

## Consequences

- (+) v0.2 async ingest is a non-breaking superset of the v0.1 contract; D4/OpenAPI consumers
  are stable across the milestone boundary.
- (+) Startup behaviour is unambiguous and testable; I1 is observable (the log states the
  rule it is honouring).
- (+) `"skipped"` status surfaces the I1 fast-path to API callers, useful for the EC-2 demo.
- (−) Pre-existing files require an explicit `POST /ingest/trigger` (or a touch) to enter the
  index; this is intended for v0.1 and documented in the startup log line itself.
