# ADR-0020 — Document Upload + Scheduled Folder Import (M4-EXT)

> **Amendment (2026-06-28, post-review):** `POST /ingest/upload` ships **non-blocking 202**
> (`{file_path, status:"queued", overwritten}`, no `page_id`) — NOT the originally-specified
> synchronous 201 — because a synchronous call blocked the HTTP response on the full LLM ingest
> loop (poor UX). Both on-ramps (upload + scheduled scan) now ingest **exclusively via the
> watcher**; to keep that promise the watcher's accepted-extension filter was broadened to the
> SAME allow-list as upload (`{.md, .txt, .markdown}` — `app.upload._ALLOWED_EXTENSIONS`, one
> source of truth) so `.txt`/`.markdown` files are not silently dropped. Verified end-to-end.

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.4 (M4 "Usable and fluid"), M4-EXT (parallel PM addition)
- Decider: solution-architect
- Invariants: **I1** (incremental index only — upload/scan ingest only NEW or CHANGED files,
  never a re-import of unchanged content; reuse the existing mtime-then-hash gate),
  **I5** (Obsidian compat — uploads/scans write ONLY into `vault/raw/sources/`; never touch
  `vault/wiki/`, never corrupt the vault), **I7** (every loop bounded — the scheduler has a
  per-scan file cap AND a wall-clock cap; an upload has a byte-size cap; `total_cost_usd` /
  file-count logged via the existing `ingest_runs` ledger), **I8** (docs-as-DoD — ER from
  models, OpenAPI from FastAPI, both regenerated), **I9** (do not reinvent — reuse the
  watcher + the `ingest_file` seam; no new ingest engine, no new scheduler framework when the
  asyncio loop already in the lifespan suffices)
- Related: ADR-0001 (mtime-then-hash incremental gate — the I1 mechanism we reuse),
  ADR-0003 (thin ingest seam `ingest_file()` — the single ingest entry point),
  ADR-0005 (soft-delete + `vault_state` seeding — `data_version` bump semantics),
  ADR-0006 (`POST /ingest/trigger` contract + startup no-rescan behaviour),
  ADR-0008/0009 (`ingest_runs` cost ledger; bounded-loop defaults),
  ADR-0018 (NavRail / Ingest Activity View / Settings — Feature U extends the Ingest section,
  Feature S extends the Settings section; both reuse ADR-0018's stores + clients),
  CLAUDE.md §3 (I1/I5/I7/I8/I9), §4b (F1, F16), §6 (architecture — Watcher + Ingest seam),
  docs/sprints/v0.4-pm-scope.md (M4-EXT parallel scope)
- Gates: this introduces TWO new endpoints families, ONE new background scheduler, and ONE new
  table. No M4-EXT code is written before this ADR is approved (architect sign-off gate).

---

## Context

M4-EXT adds two stakeholder-requested ingest on-ramps that both feed the **existing** ingest
pipeline. Neither invents a new ingest engine — both end at `app.ingest.orchestrator.ingest_file()`
(ADR-0003), which already enforces the I1 mtime-then-hash gate (ADR-0001), writes `ingest_runs`
rows for the I7 cost ledger (ADR-0008/0009), and bumps `data_version` (ADR-0005). The job of this
ADR is to design the two **on-ramps** correctly — safely landing bytes into `vault/raw/sources/`
and then letting the proven pipeline do its work — without weakening any invariant.

**Feature U — Document upload from the UI.** Today a user must place a file into
`vault/raw/sources/` out-of-band (LiveSync, SSH, Nextcloud) and then either let the watcher pick
it up or call `POST /ingest/trigger {file_path}`. There is no in-browser way to add a document.
Feature U adds `POST /ingest/upload` (multipart) so the Ingest section's drag-and-drop zone can
push a file straight into the vault.

**Feature S — Scheduled folder import.** A user with a folder of documents (e.g. a Nextcloud
sync dir, a "drop here" inbox) wants Synapse to periodically pick up new/changed files without
manual triggering. Feature S adds a bounded background scheduler that, on a configured interval,
scans a directory **visible inside the container**, copies only NEW or CHANGED files into
`vault/raw/sources/`, and lets the normal pipeline ingest them.

**Five hard facts constrain the build:**

1. **The ingest seam already exists and already enforces I1.** `ingest_file(path)`
   (orchestrator.py) does stat → mtime gate → hash gate → (provider pipeline) → persist → embed →
   log → `bump_version` → `notify_bump`. Re-ingesting an unchanged file is a fast no-op
   returning `status="skipped"`. We MUST route both features through this seam — never duplicate
   the gate, never write to Postgres/Qdrant directly (I9 + ADR-0003).

2. **The watcher already auto-indexes `vault/raw/sources/`.** `watcher.py` registers watchdog
   handlers on `settings.raw_sources_dir` and calls `ingest_file` on CREATE/MODIFY/MOVE. So the
   instant a file lands in `raw/sources/`, the watcher ingests it. This is the cleanest reuse: an
   on-ramp's only job is to land bytes in `raw/sources/`; the watcher does the rest. We must
   decide, for each feature, whether to rely on the watcher (async, decoupled) or call
   `ingest_file` directly (synchronous, returns a `page_id`) — and justify it (§2, §4).

3. **The backend is containerized; it sees only MOUNTED paths.** `docker-compose.yml` mounts
   `./vault:/vault`. The container has NO view of the host filesystem. A scheduled-import
   `source_dir` therefore MUST be a path visible **inside the container**. There is NO host
   filesystem browse from a container, and we will NOT pretend otherwise: the UI is a **path
   text input** (validated by a backend "does this dir exist and is it readable" check), not a
   native folder picker. To make a host folder importable, the operator mounts it into the
   container (we add an `./import:/import` example mount, §6). This constraint is documented
   prominently in the AC and in DEPLOY.md (§9).

4. **`POST /ingest/trigger` takes `{file_path}` relative to `vault/raw/sources/`** (ADR-0006,
   ADR-0018 §3) and resolves it under `vault_root`. Path-traversal safety is currently weak (it
   accepts any relative path, including `../`). Feature U's upload MUST add **strict
   path-traversal protection** because the filename comes from an untrusted browser
   `multipart/form-data` part.

5. **v0.4 is text/markdown only.** Multi-format ingest (PDF/DOCX/PPTX/XLSX/images/AV) is **F12**,
   scoped to **M5** (CLAUDE.md §4b; v0.4-pm-scope §3). Feature U MUST reject non-text uploads
   with a clear **415** that names F12/M5; Feature S MUST skip non-text files in a scanned dir
   (not error the whole scan). Accepting binary formats now would silently break the text-only
   pipeline and pre-empt M5.

---

## Decision

### §1. Both on-ramps land bytes in `vault/raw/sources/`, then reuse the existing pipeline (I1, I9)

The architectural spine of both features is identical and deliberately minimal:

```
  bytes (HTTP upload  OR  file in a mounted source_dir)
     │
     ├─ sanitize filename / select target name           (Feature U §3, Feature S §4)
     ├─ write to vault/raw/sources/<name>                 (the ONLY write target — I5)
     │
     ├─ Feature U:  call ingest_file(dst) DIRECTLY        (synchronous → returns page_id, status)
     └─ Feature S:  copy file, then let the WATCHER ingest (async, decoupled)  — see §4 rationale
     │
     └─ ingest_file()  →  mtime/hash gate (I1)  →  provider pipeline  →  persist/embed/log
                       →  bump_version  →  ingest_runs row (I7)         [ALL existing, unchanged]
```

No new ingest logic is written. The I1 gate, the I7 cost ledger, the `data_version` bump, and
the GraphCache notification are all inherited from `ingest_file`. This is the I9 ("do not
reinvent") and ADR-0003 ("thin seam is the only path") discipline applied verbatim.

**`vault/raw/sources/` is the only write target for both features (I5).** Neither feature ever
writes into `vault/wiki/` (that is the LLM-generated output owned by the pipeline) or
`vault/.obsidian/`. The wiki vault cannot be corrupted by an upload or a scan because uploads/scans
only deposit *raw source* bytes; the pipeline alone authors wiki pages.

---

### §2. Feature U — `POST /ingest/upload` (multipart, strict path safety, synchronous ingest)

#### §2.1 Endpoint contract

```
POST /ingest/upload
Content-Type: multipart/form-data
  part "file": the uploaded document (required)
  (no other parts in v0.4; vault_id is the server default — single-vault M4)

201 Created  →  {
  file_path:   str,        // saved path relative to vault_root, e.g. "raw/sources/notes.md"
  page_id:     uuid,       // the pages.id produced by ingest_file
  status:      str,        // "completed" | "skipped"  (mirrors IngestResult, ADR-0006)
  overwritten: bool        // true if a same-name file already existed and was replaced
}

415 Unsupported Media Type  → {
  detail: "Only text/markdown files (.md, .txt, .markdown) are accepted in v0.4. "
          "Multi-format ingest (PDF, DOCX, …) is F12, planned for M5."
}
413 Payload Too Large       → { detail: "File exceeds the <N> MB upload limit." }
422 Unprocessable Entity    → { detail: "Filename is empty or unsafe after sanitization." }
```

**Why 201 and synchronous (not 202):** the upload IS the file creation, and `ingest_file` is
synchronous and fast (mtime/hash gate + one provider run). Returning `page_id` + `status`
immediately lets the drag-drop UX show "ingested: notes.md → page" without a poll round-trip, and
lets the Ingest run list refresh once. `POST /ingest/trigger` returns 202 by historical contract
(ADR-0006) for a *pre-existing* file; upload is a *create*, so 201 is the correct REST verb. (If a
future async ingest queue lands, upload can return 202 with a `task_id` as a non-breaking superset,
mirroring ADR-0006's evolution path.)

#### §2.2 Strict path-traversal protection (the security-critical part)

The filename arrives from untrusted `multipart/form-data`. The sanitizer is a **pure function**
(unit-testable in isolation) applied to the *uploaded filename only*, never to a
caller-supplied path:

```
def safe_source_name(raw_filename: str) -> str:
    1. name = Path(raw_filename).name          # basename ONLY — strips any directory component
                                               #   "../../etc/passwd" → "passwd"
                                               #   "/abs/evil.md"     → "evil.md"
                                               #   "a/b/c.md"         → "c.md"
    2. reject if name in {"", ".", ".."}                      → 422
    3. reject if name contains a path separator after step 1 → 422  (defensive; should be impossible)
    4. strip control chars / NUL; collapse whitespace
    5. enforce extension allow-list (.md/.txt/.markdown, case-insensitive) → else 415
    6. clamp length (e.g. ≤ 200 chars, preserving the extension)
    return name

def resolve_under_sources(name: str) -> Path:
    dst = (settings.raw_sources_dir / name).resolve()
    # belt-and-braces: the resolved path MUST be inside raw_sources_dir
    if not str(dst).startswith(str(settings.raw_sources_dir.resolve() / "")):
        raise HTTPException(422, "unsafe path")
    return dst
```

Rules, stated explicitly:
- **Basename only.** We take `Path(filename).name` and *discard* any directory component. There is
  no path joining of caller-controlled segments — the file always lands directly in
  `raw/sources/`, never in a subdirectory the caller names.
- **No `..`, no absolute paths.** Step 1 already neutralizes both, but steps 2–3 + the
  `resolve_under_sources` containment check are an explicit second gate (defence in depth). A
  resolved path that escapes `raw_sources_dir` is a 422, never written.
- **Extension allow-list before any disk write** (415 for non-text — §2.3). MIME from the upload
  is advisory; the extension is authoritative (browsers send inconsistent `Content-Type`).

#### §2.3 Type gate (v0.4 text/markdown only — F12/M5 boundary)

Accept only `.md`, `.txt`, `.markdown` (case-insensitive). Anything else → **415** with the
F12/M5 message above. The check is on the **extension** (authoritative) with `Content-Type` as a
soft hint. This honours fact #5 and keeps the text-only pipeline intact. The 415 message is a
user-facing string that names F12/M5 so the user understands it is deferred, not broken.

#### §2.4 Size limit (I7 — bounded input)

Reject uploads over a configured byte cap (default **25 MB**, env `MAX_UPLOAD_BYTES`). Enforced by
**streaming the body and aborting at the cap** (do not buffer an unbounded body into memory).
FastAPI/Starlette `UploadFile` streams to a spooled temp file; we read in chunks and stop at the
cap → **413**. This is the I7 "bounded" discipline applied to request size. 25 MB is generous for
markdown/text and small enough to bound memory; configurable for operators with larger notes.

#### §2.5 Write + ingest flow (synchronous, direct `ingest_file`)

```
1. validate content-type hint (soft) + extension (hard, §2.3)        → 415 on fail
2. stream body to a temp file, abort at MAX_UPLOAD_BYTES             → 413 on overflow
3. name = safe_source_name(upload.filename)                          → 422 on unsafe
4. dst  = resolve_under_sources(name)                                → 422 on escape
5. overwritten = dst.exists()
6. atomically move the validated temp file to dst (same-filesystem rename; both under /vault)
7. result = await ingest_file(dst)        # DIRECT call — synchronous, returns IngestResult
8. return 201 { file_path: rel(dst), page_id: result.page_id, status: result.status, overwritten }
```

**Why call `ingest_file` directly instead of relying on the watcher (decision):** the upload is a
synchronous request that should return the `page_id`/`status` to the UI. Relying on the watcher
would make the response unable to report the ingest outcome (the watcher fires asynchronously on
its own thread). A direct call is the same path `POST /ingest/trigger` already uses, so behaviour
is identical and well-tested. **The watcher may ALSO fire** for the same write (it observes
`raw/sources/`); that is **safe and idempotent by I1**: the watcher's `ingest_file` re-run hits the
mtime/hash fast-path (the file is unchanged since our direct call) and returns `status="skipped"`
with no duplicate page, no second `data_version` bump, no second provider cost. The I1 gate is
exactly what makes the "direct call + watcher also sees it" double-trigger harmless — we rely on it
rather than trying to suppress the watcher (which would be fragile coupling). This is documented so
no reviewer mistakes the double-observe for a bug.

#### §2.6 Overwrite semantics

If a same-name file already exists in `raw/sources/`, we **replace** it (`overwritten=true`).
Replacing the bytes means `ingest_file` sees a changed hash → re-ingests → updates the existing
page row in place (the partial-unique index on `(vault_id, file_path)` keeps it one live page).
This is correct I1 behaviour (a changed file updates only its own records). We do **not** silently
rename-to-avoid-collision (that would orphan the old page and surprise the user); replace is the
least-surprising, I1-correct choice, and the `overwritten` flag tells the UI to message it.

---

### §3. Frontend — drag-and-drop upload zone in the Ingest section (NOT Settings)

Feature U's UI lives in the **Ingest section** (ADR-0018 §3 `IngestView`), beside the existing
"Run Ingest" file-path control — NOT in Settings. Rationale: uploading a document is an *ingest
operation*; the Ingest section already owns the run list it will refresh.

| File | Change |
|---|---|
| `frontend/src/components/ingest/UploadZone.tsx` | **NEW.** Drag-and-drop target + a "Browse" `<input type=file accept=".md,.txt,.markdown">` fallback. On drop/select → `uploadDocument(file)` → success toast (`ingest.uploadToastStarted`) → `fetchFresh(vaultId)` to refresh the run list (reuse ADR-0018's `ingestStore.fetchFresh`). On 415/413/422 → error toast with the backend `detail`. Shows accepted types + the M5 note for others (a small helper line: "Markdown/text only — PDF/DOCX coming in M5"). Reduced-motion-safe drag highlight. No CodeMirror/WYSIWYG (I4 — it is a plain dropzone). |
| `frontend/src/components/ingest/IngestView.tsx` | **EDIT.** Mount `<UploadZone/>` above (or beside) the existing Run-Ingest form. No other change — the run list, polling, and toast plumbing are reused as-is. |
| `frontend/src/api/ingestClient.ts` | **EDIT.** Add `uploadDocument(file: File, signal?): Promise<UploadResponse>` — `POST /ingest/upload` with a `FormData` (`file` part); reuse the existing `checkResponse`/`ApiError` so 415/413/422 surface their `detail`. Do NOT set `Content-Type` manually (let the browser set the multipart boundary). |
| `frontend/src/api/types.ts` | **EDIT.** Add `UploadResponse { file_path; page_id; status; overwritten }`. |

**Client-side guard (UX, not security):** the dropzone pre-filters by extension and rejects
oversized files locally with a friendly message, but the **backend remains authoritative** (§2.2–
§2.4). The client check is a convenience to avoid a round-trip for obvious mistakes; it is never
the security boundary.

i18n keys (added to `ingest` namespace, both `en.json` + `it.json`, key parity — AC reuse of
ADR-0018 §6): `ingest.upload`, `ingest.uploadDrop` ("Drop a file here or browse"),
`ingest.uploadHint` ("Markdown/text only (.md, .txt, .markdown)"),
`ingest.uploadM5Note` ("PDF, DOCX and other formats: coming in M5"),
`ingest.uploadToastStarted` ("Uploaded and ingesting: {{file}}"),
`ingest.uploadToastError` ("Upload failed: {{detail}}"),
`ingest.uploadTooLarge`, `ingest.uploadBadType`.

---

### §4. Feature S — bounded scheduled folder import

#### §4.1 Persistence — single-row config table `import_schedules`

One configuration row per vault (M4 is single-vault; the table is keyed by `vault_id` so it
generalises). A **table** (not a env-only config) is chosen so the UI can read/write it via REST
and the scheduler reads it on each tick — and so `last_run_at`/`last_status` are durable across
restarts (the user must be able to see "last scan: 5 min ago, 3 imported" after a restart).

`import_schedules` (SQLAlchemy model + Alembic migration **0008**):

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK (`gen_random_uuid()`) | row identity |
| `vault_id` | String NOT NULL, UNIQUE | one schedule per vault (single-row-per-vault) |
| `enabled` | Boolean NOT NULL DEFAULT false | scheduler is a no-op while false |
| `source_dir` | Text NULL | **container-visible** absolute path (e.g. `/import`); NULL until set |
| `frequency` | Text NOT NULL DEFAULT `'1h'` | enum: `15m` \| `1h` \| `6h` \| `daily`. A frequency enum (not raw seconds) keeps the UI a simple select and bounds the interval to sane values (I7 — no 1-second hammer). Mapped to seconds server-side. |
| `last_run_at` | TIMESTAMPTZ NULL | set after each scan |
| `last_status` | Text NULL | `ok` \| `error` \| `running` \| `skipped_disabled` \| `dir_missing` |
| `last_imported_count` | Integer NOT NULL DEFAULT 0 | files newly imported (copied + ingested) on the last scan |
| `last_error` | Text NULL | human-readable error from the last failed scan |
| `created_at` / `updated_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | audit |

> **Frequency enum vs. raw `interval_seconds`:** we choose the **enum** (`15m/1h/6h/daily`).
> It makes the UI a bounded select (no free-form "1" → runaway), is trivially i18n-able, and maps
> to a server-side seconds table (`15m→900, 1h→3600, 6h→21600, daily→86400`). This is the I7-aligned
> choice: the user cannot configure a pathologically tight interval. (A raw-seconds column with a
> server-enforced floor would also work but invites a worse UX and a validation footgun; the enum
> is simpler and correct.)

#### §4.2 Incremental tracking — reuse the pages table, do NOT add a per-file ledger (I1, I9)

The scheduler must import only **NEW or CHANGED** files (I1 — never re-import unchanged). We do
**not** invent a separate "imported files" tracking table. The decision: the scheduler **copies a
scanned file into `raw/sources/` only when its content differs from what is already ingested**, and
the **`ingest_file` mtime/hash gate is the ultimate I1 backstop**. Concretely, each scan:

```
for each regular file F in source_dir (non-recursive by default; see §4.4 cap):
    skip if F's extension is not in {.md,.txt,.markdown}   (F12/M5 — text only; no error)
    name = safe_source_name(F.name)                         (reuse §2.2 sanitizer)
    dst  = raw_sources_dir / name
    h    = sha256(F bytes)
    if dst exists and sha256(dst bytes) == h:               → unchanged → SKIP (no copy, no ingest)
    else:
        copy F → dst (atomic: copy to temp in raw/sources/, then rename)
        imported_count += 1
        # the WATCHER observes the new/changed file in raw/sources/ and ingests it (I1 gate again)
```

Two layers of I1 protection, both reusing existing mechanisms (I9):
1. **Content-hash compare before copy** — if the destination already holds identical bytes, we do
   not even copy (avoids a needless mtime bump that would otherwise be caught downstream anyway).
   This is the cheap, correct "only new/changed" gate at the *source-scan* layer.
2. **`ingest_file`'s own mtime-then-hash gate (ADR-0001)** — even if a copy happens, if the
   content matches what Postgres already has, `ingest_file` returns `status="skipped"`. So a
   spurious copy can never produce a duplicate page or a double cost. We never bypass this gate.

This means **no new tracking table** is needed: `pages.content_hash` (already the authoritative
change signal) plus the on-disk bytes are the source of truth for "have we ingested this already".
That is the I1 + I9 minimal design.

#### §4.3 Ingest path — copy into `raw/sources/`, let the WATCHER ingest (decision + rationale)

**Decision:** the scheduler **copies** changed files into `raw/sources/` and lets the **watcher**
ingest them — it does NOT call `ingest_file` directly per file.

**Rationale (vs. Feature U, which DOES call directly):**
- A scan is a **batch, fire-and-forget** operation with no synchronous consumer waiting for a
  per-file `page_id` (unlike the upload request, which returns the result to the browser). So the
  decoupled watcher path is the natural fit: the scheduler's job ends at "the bytes are in
  `raw/sources/`", and the watcher (already running, already correct, I9) does the ingest.
- It keeps the scheduler **thin and ingest-agnostic**: the scheduler never imports the orchestrator,
  never holds provider logic, never writes Postgres/Qdrant. It only does filesystem work. This is
  the cleanest separation and the least code.
- The per-scan **count we report** (`last_imported_count`) is the number of files the scan
  *copied* (new/changed), which is the meaningful, deterministic number for the user. The actual
  ingest outcomes (completed/skipped) land in the `ingest_runs` ledger via the watcher → the user
  sees them in the Ingest Activity View (ADR-0018) exactly like any other ingest. We do **not**
  double-count or fork a parallel ledger.

> **Why not call `ingest_file` directly in the scan loop?** It would couple the scheduler to the
> orchestrator and duplicate the watcher's responsibility, and it would block the scan tick on
> N provider runs (a long, unbounded-feeling tick). Copying + watcher keeps each scan O(files),
> bounded, and fast; ingest happens asynchronously afterwards under the watcher's existing,
> already-bounded path. (If a future requirement needs synchronous per-file results from a scan,
> the seam is open: swap the copy-then-watcher step for a direct `ingest_file` call — but M4-EXT
> does not need it.)

**Does a scheduled scan create `ingest_runs` rows?** Yes — indirectly and correctly. Each copied
file is ingested by the watcher through `ingest_file` → `run_ingest_pipeline`, which writes one
`ingest_runs` row per file that has a provider configured (the existing I7 ledger, ADR-0008 §4). So
scheduled imports show up in the Ingest Activity View just like manual ones, with their cost. We
reuse the existing table — **no new run table** (consistent with ADR-0019's "no chat_runs" stance).
The `import_schedules.last_*` columns are a *scan-level* summary (when, how many copied, ok/error),
complementary to the per-file `ingest_runs` rows.

#### §4.4 Bounded scan (I7 — the scheduler cannot run away)

Every scan is bounded by **two independent caps**, both env-configurable:
- **`IMPORT_SCAN_MAX_FILES`** (default **200**): the scan copies at most N new/changed files per
  tick; the remainder are picked up on the next tick. This bounds per-tick work and bounds the
  watcher's ingest burst.
- **`IMPORT_SCAN_MAX_SECONDS`** (default **60**): a wall-clock deadline; the scan loop checks
  elapsed time each iteration and stops early if exceeded, recording `last_status="ok"` with the
  partial count (the rest follow next tick).

The scan loop is a **single non-recursive `os.scandir`** of `source_dir` (no `rglob`, no unbounded
walk) — deliberately non-recursive so a deeply nested mount cannot explode the scan (recursive
import can be a future opt-in flag with its own depth cap). The directory scandir itself is the
only enumeration, and it is bounded by `MAX_FILES`/`MAX_SECONDS`. This is the I7 "every loop has a
max_iter AND a budget" rule applied to the scan.

**Logging (I7 cost/count visibility):** each scan logs one structured line
(`scheduled_import vault=… dir=… scanned=N copied=M skipped=K elapsed=…s status=ok`) and updates
`import_schedules.last_run_at/last_status/last_imported_count/last_error`. The per-file *cost* is
captured by the existing `ingest_runs` rows (the watcher's pipeline) — we do not re-implement cost
accounting; the scan summary reports counts, the `ingest_runs` ledger reports dollars.

#### §4.5 Scheduler technology — a simple asyncio interval task (decision + justification)

**Decision:** a **single in-process asyncio interval task** managed by the FastAPI lifespan —
NOT APScheduler.

**Justification (I9 reuse / simplicity / bounded):**
- **I9 / no new heavy dependency.** The app already starts background work in the lifespan (the
  watcher observer; the `GraphCache` background debounce loop — `_graph_cache.start_background_loop()`).
  A scheduled scan is *one* periodic task with *one* configurable interval and a single in-flight
  guard. APScheduler (jobstores, executors, cron triggers, misfire grace, timezone machinery) is
  far more than we need and adds a dependency + lifecycle surface for zero benefit. The asyncio
  task is ~30 lines, uses only stdlib + the existing `asyncio` loop, and is trivially testable
  (inject the clock / drive one tick manually, exactly as `GraphCache` is tested).
- **Simplicity + correctness.** The semantics we need — "every K seconds, if enabled, run one
  bounded scan; never overlap scans" — are a `while not stopped: await sleep(interval); if enabled
  and not already running: await one_scan()` loop with a single re-entrancy flag. No cron
  expressions, no persistence of job state (the *config* persists in `import_schedules`; the
  *task* is ephemeral and re-reads config each tick).
- **Bounded by construction (I7).** One task, one in-flight scan at a time (an `asyncio.Lock` or a
  boolean guard prevents overlap if a scan outruns its interval), each scan capped by
  `MAX_FILES`/`MAX_SECONDS`. There is no possibility of unbounded concurrent scans.

```python
# app/import_scheduler.py  (sketch — backend-engineer owns the impl)
class ImportScheduler:
    def __init__(self, clock=..., scan_fn=run_one_scan): ...
    def start(self, loop): self._task = loop.create_task(self._run())     # lifespan startup
    def stop(self): self._stopping = True; self._task.cancel()            # lifespan shutdown

    async def _run(self):
        while not self._stopping:
            cfg = await load_schedule(settings.vault_id)       # re-read config each tick
            interval = FREQ_SECONDS[cfg.frequency] if cfg.enabled else DEFAULT_POLL  # e.g. 60s idle poll
            await self._sleep(interval)                        # injectable for tests
            if self._stopping: break
            cfg = await load_schedule(settings.vault_id)       # re-check (config may have changed)
            if not cfg.enabled or not cfg.source_dir:
                continue
            if self._scan_in_flight:                           # never overlap (I7)
                continue
            self._scan_in_flight = True
            try:
                await run_one_scan(cfg)                        # bounded: MAX_FILES + MAX_SECONDS
            finally:
                self._scan_in_flight = False
```

**Lifecycle (start/stop with the FastAPI lifespan; reschedule on config change):**
- **Startup** (in `main.py` lifespan, after the watcher starts — the scheduler depends on the
  watcher to actually ingest copied files): `_import_scheduler = ImportScheduler(); _import_scheduler.start(loop)`.
- **Shutdown:** `_import_scheduler.stop()` (cancel the task, await cleanup), alongside
  `stop_watcher()`.
- **Reschedule on config change:** the task **re-reads `import_schedules` at the top of every
  tick**, so a `PUT /import-schedule` that changes `enabled`/`frequency`/`source_dir` takes effect
  on the next tick **without restart**. We deliberately do NOT try to interrupt a sleeping task to
  apply a new interval immediately (that adds cancel/restart complexity for a feature where
  "applies within one idle-poll cycle" is perfectly acceptable). For an immediate run, the user has
  **`POST /import-schedule/run-now`** (§4.6). This is the simplest correct rescheduling model: the
  config is the source of truth, the task is stateless between ticks.

> **`source_dir` is container-visible (the mounted-path constraint — restated as a hard rule).**
> `run_one_scan` resolves `source_dir` **inside the container**. If the path does not exist or is
> not a readable directory, the scan does NOT error the scheduler — it records
> `last_status="dir_missing"` + `last_error` and continues (the operator likely forgot the mount).
> The scan NEVER reaches outside `/` of the container (there is no host fs). The UI and DEPLOY.md
> tell the operator to mount their folder (e.g. `./import:/import`, §6) and enter the
> **container** path (`/import`). There is no host-folder browse — see §7.

#### §4.6 Scheduled-import REST contract

```
GET /import-schedule
  200 → {                                  // current config + last-run status (single row for the vault)
    enabled:             bool,
    source_dir:          str | null,       // container-visible path
    frequency:           "15m" | "1h" | "6h" | "daily",
    last_run_at:         datetime | null,
    last_status:         "ok"|"error"|"running"|"skipped_disabled"|"dir_missing" | null,
    last_imported_count: int,
    last_error:          str | null
  }
  (returns sane defaults — {enabled:false, frequency:"1h", …} — if no row exists yet)

PUT /import-schedule
  body → { enabled?: bool, source_dir?: str, frequency?: "15m"|"1h"|"6h"|"daily" }
  - validates frequency ∈ enum (422 otherwise)
  - validates source_dir (when provided) with a backend "exists & is a readable directory INSIDE
    the container" check; if it fails, the row is still saved but the response carries a
    warning field { dir_ok: false, dir_message } so the UI can flag it (we save-then-warn rather
    than reject, because the operator may add the mount before the next tick)
  - upserts the single import_schedules row for the vault
  200 → the same shape as GET, plus { dir_ok: bool, dir_message: str | null }

POST /import-schedule/run-now
  - triggers ONE bounded scan immediately (same run_one_scan, same MAX_FILES/MAX_SECONDS caps, I7)
  - 409 Conflict if a scan is already in flight (no overlap — I7)
  - 400 if disabled or source_dir unset/dir_missing (with a clear detail)
  202 Accepted → { status: "started" }     // scan runs in the background; poll GET for the result
       (or 200 with the scan summary if we run it inline; 202 preferred so the request returns fast)
```

The **directory-validation endpoint behaviour** is folded into `PUT` (and the `run-now`
precondition) rather than a separate `GET /import-schedule/validate-dir` — fewer endpoints, and the
validation is exactly the precondition the scan itself checks. (A standalone validate endpoint is a
trivial fast-follow if the UI wants live feedback as the user types; not M4-EXT-blocking.)

---

### §5. Frontend — "Automatic import" in the Settings section

Feature S's UI lives in the **Settings section** (ADR-0018 §5 `SettingsPanel`) as a new
"Automatic import" card — Settings is the correct home (it is configuration, not a per-document
operation).

| File | Change |
|---|---|
| `frontend/src/components/settings/ImportScheduleCard.tsx` | **NEW.** A card with: an **enabled** toggle; a **source_dir** text input with the **container-path hint** (`settings.import.dirHint`: "Must be a path visible inside the backend container, e.g. /import — see DEPLOY.md"); a **frequency** select (15m / 1h / 6h / daily); a **last-run status** line (relative time + imported count + ok/error badge, reusing the `StatusBadge` pattern + `Intl.RelativeTimeFormat` from ADR-0018 §3); and a **"Run now"** button. On change → `PUT /import-schedule`; on "Run now" → `POST /import-schedule/run-now` + toast. If `PUT` returns `dir_ok:false`, show an inline warning under the input (not a hard error). |
| `frontend/src/components/settings/SettingsPanel.tsx` | **EDIT.** Mount `<ImportScheduleCard/>` as a new card below the existing context-window/language/provider cards. |
| `frontend/src/api/importScheduleClient.ts` | **NEW.** `getImportSchedule()`, `putImportSchedule(body)`, `runImportNow()` — reuse `checkResponse`/`ApiError`. |
| `frontend/src/store/settingsStore.ts` | **EDIT (light) or a small `importScheduleStore.ts`.** Hold the schedule config + last-run status; selector-gated (I3). Keeping it in `settingsStore` is acceptable (it is settings state and never re-renders the graph); a separate tiny store is also fine. Do NOT put it in `graphStore` (I3 — must not couple to graph render). |
| `frontend/src/api/types.ts` | **EDIT.** Add `ImportSchedule`, `ImportSchedulePutResponse`. |

**i18n keys** (new `settings.import` sub-namespace, `en.json` + `it.json`, key parity):
`settings.import.title` ("Automatic import"), `.enabled` ("Enabled"),
`.sourceDir` ("Source folder"), `.dirHint` (container-path hint, names DEPLOY.md),
`.frequency` ("Frequency"), `.freq15m`/`.freq1h`/`.freq6h`/`.freqDaily`,
`.lastRun` ("Last scan"), `.imported` ("{{count}} imported"), `.runNow` ("Run now"),
`.statusOk`/`.statusError`/`.statusRunning`/`.statusDirMissing`/`.statusDisabled`,
`.dirWarning` ("This folder is not visible inside the container — add a mount (see DEPLOY.md)"),
`.runNowToast` ("Import scan started"), `.runNowError`, `.never` ("never").

**I3 discipline:** the Import-schedule state is selector-gated and lives outside `graphStore`; a
schedule change or a poll of `last_run_at` must never re-render the graph or the page tree
(consistent with ADR-0018 §2's "provider/settings/ingest live in separate stores"). The card may
optionally poll `GET /import-schedule` while `last_status === "running"` (bounded `setTimeout`
chain with `AbortController`, exactly like ADR-0018 §3's ingest polling) — never a `setInterval`
leak.

---

### §6. docker-compose — add the import mount (the mounted-path enabler)

`source_dir` must be visible inside the container. We add a **commented example mount** to
`docker-compose.yml` so the operator can expose a host folder as `/import`:

```yaml
  synapse-backend:
    # ...
    volumes:
      - ./vault:/vault
      # ── Feature S (ADR-0020): scheduled folder import ────────────────────────
      # Mount any host folder you want Synapse to auto-import into the container,
      # then set the schedule's source_dir to the CONTAINER path (e.g. /import).
      # The backend can ONLY see mounted paths — there is no host filesystem browse.
      # Example (uncomment + adjust the host side to your folder):
      # - ./import:/import:ro          # read-only is recommended: Synapse copies OUT of it
    environment:
      # ...
      # ── Feature U / S bounds (ADR-0020 §2.4 / §4.4) — all I7 caps, env-configurable ──
      MAX_UPLOAD_BYTES: "26214400"     # 25 MB upload cap (Feature U)
      IMPORT_SCAN_MAX_FILES: "200"     # per-scan file cap (Feature S, I7)
      IMPORT_SCAN_MAX_SECONDS: "60"    # per-scan wall-clock cap (Feature S, I7)
```

Notes:
- We mount **read-only (`:ro`)** in the example: Feature S only *reads* the source folder and
  *copies out of it* into `vault/raw/sources/`; it never writes back to `source_dir`. Read-only is
  the safe default and we recommend it in DEPLOY.md.
- We keep it **commented** so the default `docker compose up` is unchanged for users who do not use
  scheduled import; uncommenting + setting `source_dir=/import` is the documented enable path.
- The three env caps are added to the backend `environment:` block and read by `config.py` (new
  settings fields `max_upload_bytes`, `import_scan_max_files`, `import_scan_max_seconds`).

---

### §7. The mounted-path constraint (documented prominently — hard design rule)

> **A containerized backend sees ONLY mounted paths. There is no host filesystem browse from a
> container.** Therefore:
> - `source_dir` (Feature S) MUST be a path that exists **inside** the backend container.
> - The UI is a **path text input** with a clear hint that the path is a *container* path, plus a
>   backend "does this dir exist & is it readable" validation. It is **NOT** a native folder
>   picker, and we will not fake one — a file/folder picker would imply host browsing the container
>   cannot do.
> - To import a host folder, the operator **mounts it** into the container (§6 `./import:/import`)
>   and enters the **container** path (`/import`) in the UI.
> - The backend validation distinguishes "directory not found / not readable inside the container"
>   (`dir_missing`) from a successful resolve, and surfaces it to the UI (`dir_ok:false` +
>   `dir_message`) so the operator gets an actionable message ("add a mount").
> - This same reasoning applies to Feature U only insofar as uploaded bytes always land **inside**
>   the mounted `vault/raw/sources/` — never at an arbitrary host or container path (§2.2).

This constraint is restated in DEPLOY.md (D6) and USER.md (D6) by tech-writer.

---

### §8. Invariant confirmation

- **I1 (incremental index only):** Neither feature re-imports unchanged content. Feature U routes
  through `ingest_file`'s mtime/hash gate (re-upload of identical bytes → `skipped`). Feature S
  hash-compares each scanned file against the destination **before** copying (skips unchanged), and
  the watcher's `ingest_file` gate is the final I1 backstop. No full rescan is ever introduced; the
  scan is bounded and only touches new/changed files.
- **I5 (Obsidian compat / don't corrupt the vault):** Both features write **only** into
  `vault/raw/sources/` (raw source bytes). Neither writes `vault/wiki/` or `.obsidian/`; the
  pipeline alone authors wiki pages. The wiki vault remains a valid Obsidian vault.
- **I7 (bounded loops + cost/count logged):** Upload is byte-capped (`MAX_UPLOAD_BYTES`, 413). Each
  scan is double-capped (`MAX_FILES` + `MAX_SECONDS`), non-recursive, single-in-flight (no overlap),
  and logs scanned/copied/skipped counts + status; per-file dollar cost flows into the existing
  `ingest_runs` I7 ledger via the watcher. The scheduler is one task, never spawns concurrent scans.
- **I8 (docs-as-DoD):** New `import_schedules` model → `make er` regenerates `docs/er/schema.mmd`;
  new endpoints (`/ingest/upload`, `/import-schedule`, `/import-schedule` PUT,
  `/import-schedule/run-now`) → `make openapi` regenerates `docs/api/openapi.json`. Both are
  zero-drift gates. tech-writer updates D1 `component.mmd` (add `ImportScheduler`, `UploadZone`,
  `ImportScheduleCard`) and the DEPLOY.md mounted-path section.
- **I9 (do not reinvent):** Reuse the **watcher** (Feature S ingest), the **`ingest_file` seam**
  (both features' I1 gate + pipeline + cost ledger), the **asyncio lifespan background-task pattern**
  already used by the watcher and GraphCache (the scheduler — NOT APScheduler), the **existing
  `ingest_runs` ledger** (no new run table), and ADR-0018's **ingestStore/settingsStore + clients +
  StatusBadge + Toast + Intl.RelativeTimeFormat** (the UI). No new ingest engine, no new scheduler
  framework, no new tracking table.

---

## Consequences

**Positive**
- Two in-product on-ramps (drag-drop upload; periodic folder import) with **zero new ingest logic**
  — both end at the proven `ingest_file` seam, inheriting I1 + the I7 ledger + the `data_version`
  bump for free.
- Scheduled imports appear in the existing Ingest Activity View (ADR-0018) with their cost, because
  they ingest through the watcher's normal pipeline — one ledger, one place to look.
- The asyncio-task scheduler matches the existing lifespan pattern (watcher, GraphCache): no new
  dependency, ~one small module, trivially testable with an injected clock.
- Strict basename-only sanitization closes the path-traversal hole that the looser
  `POST /ingest/trigger` would have for untrusted filenames.

**Negative / trade-offs (stated explicitly)**
- **`POST /ingest/upload` calls `ingest_file` directly AND the watcher will also observe the
  write** → the same file is ingested twice. This is **safe by I1** (the second run is a
  mtime/hash `skipped` no-op), but it is a deliberate double-observe, not a bug. We accept it
  rather than add fragile watcher-suppression coupling. (Documented in §2.5 so reviewers don't
  "fix" it.)
- **Feature S reports a *copied-files* count, not an *ingested-pages* count, in
  `last_imported_count`.** The ingest outcomes live in `ingest_runs` (per file). This is a minor
  semantic split (scan summary vs. per-file ledger) accepted for the clean scheduler/ingest
  separation; the UI labels it "imported" (copied) and links the user to the Ingest view for ingest
  detail.
- **Reschedule applies on the next tick, not instantly.** A frequency change can take up to one
  idle-poll cycle to take effect. Accepted: it is the simplest correct model, and **Run now**
  covers the "I want it now" case. (No cancel/restart-on-config-change machinery — that complexity
  is not warranted.)
- **Non-recursive scan by default.** A nested source folder's subdirectories are not scanned in
  v0.4. Accepted as the bounded-by-default choice; recursive opt-in (with a depth cap) is a
  fast-follow if requested.
- **New table `import_schedules` (migration 0008).** Unavoidable for durable, UI-editable schedule
  config + last-run status across restarts. Additive and isolated (no FK churn).
- **New env caps + an example mount in docker-compose.** Operators who want scheduled import must
  add the `./import:/import` mount and set the container `source_dir`; documented in DEPLOY.md.

**Follow-ups (NOT this ADR's scope — tracked)**
- `GET /raw/sources` listing (turns Feature U's path input and the Run-Ingest input into pickers;
  also lets the upload zone show what is already there). Fast-follow.
- F12 (M5): multi-format ingest — when it lands, Feature U's allow-list and Feature S's extension
  skip expand to PDF/DOCX/… and the 415 message is removed. The on-ramp design here is forward
  compatible (only the type gate changes).
- Recursive scheduled scan (opt-in, depth-capped); a standalone `validate-dir` endpoint for live
  UI feedback; per-operation/per-vault multiple schedules when multi-vault lands.
- An async ingest queue would let `POST /ingest/upload` return 202 + `task_id` (non-breaking
  superset of the 201 contract), mirroring ADR-0006's evolution.

---

## Implementation spec (hand to backend-engineer + frontend-engineer)

### Split ownership
- **backend-engineer** (owns): `POST /ingest/upload` (+ sanitizer); `import_schedules` model +
  Alembic **0008**; `ImportScheduler` (asyncio task) + lifespan wiring; the scan implementation
  (`run_one_scan`, bounded); the three schedule REST endpoints; new `config.py` settings
  (`max_upload_bytes`, `import_scan_max_files`, `import_scan_max_seconds`); docker-compose env +
  example mount; `make er` + `make openapi`.
- **backend-engineer ↔ ingest path (coordinate):** the scan's copy-then-watcher path and the
  upload's direct `ingest_file` call both touch the ingest seam. Do **not** modify `ingest_file` /
  `run_ingest_pipeline` / the watcher's handler logic — both features are *callers* of the existing
  seam, not changes to it (ADR-0003 guarantee). The only ingest-adjacent change permitted is reusing
  `_sha256` / `_relative_path` helpers (or duplicating the tiny sanitizer). If the watcher needs to
  observe a programmatic copy reliably, that is already true (it watches `raw/sources/` recursively).
- **frontend-engineer** (owns): `UploadZone.tsx` + `IngestView` edit + `uploadDocument` client (§3);
  `ImportScheduleCard.tsx` + `SettingsPanel` edit + `importScheduleClient` + store + types (§5); all
  i18n keys (en + it, parity); reduced-motion-safe drag/poll; toasts via the existing `Toast`.

### New / changed backend files
| File | Responsibility |
|---|---|
| `backend/app/main.py` | **EDIT.** Add `POST /ingest/upload` (multipart, §2) + `GET/PUT /import-schedule` + `POST /import-schedule/run-now` (§4.6) routes and their Pydantic models; start/stop `ImportScheduler` in the lifespan (after the watcher start, before yield; stop alongside `stop_watcher`). |
| `backend/app/upload.py` (or in `ingest/`) | **NEW.** `safe_source_name()` + `resolve_under_sources()` pure functions (§2.2) — unit-testable; the type/size gate helpers. |
| `backend/app/import_scheduler.py` | **NEW.** `ImportScheduler` asyncio task (§4.5) + `run_one_scan(cfg)` bounded scan (§4.4) + `FREQ_SECONDS` map + `load_schedule`/`upsert_schedule` helpers. Imports `settings`; does NOT import the orchestrator (copies + relies on the watcher). |
| `backend/app/models.py` | **EDIT.** Add `ImportSchedule` model (§4.1) — single source of truth for the ER row. |
| `backend/alembic/versions/0008_import_schedules.py` | **NEW.** `create_table import_schedules` (columns per §4.1); UNIQUE(vault_id). Down = drop_table. |
| `backend/app/config.py` | **EDIT.** Add `max_upload_bytes: int = 26214400`, `import_scan_max_files: int = 200`, `import_scan_max_seconds: int = 60`. |
| `docker-compose.yml` | **EDIT.** Add the three env caps + the commented `./import:/import:ro` example mount (§6). |
| `docs/er/schema.mmd`, `docs/api/openapi.json` | **REGEN.** `make er` + `make openapi` (D2/D4 zero-drift). |

### New / changed frontend files
| File | Responsibility |
|---|---|
| `components/ingest/UploadZone.tsx` | **NEW.** Drag-drop + browse; `uploadDocument`; toasts; accepted-types + M5 note; refresh run list (§3). |
| `components/ingest/IngestView.tsx` | **EDIT.** Mount `<UploadZone/>`. |
| `api/ingestClient.ts` | **EDIT.** Add `uploadDocument(file, signal?)` (FormData; no manual Content-Type). |
| `components/settings/ImportScheduleCard.tsx` | **NEW.** Enabled toggle + container-path source_dir input + frequency select + last-run status + Run-now (§5). |
| `components/settings/SettingsPanel.tsx` | **EDIT.** Mount `<ImportScheduleCard/>`. |
| `api/importScheduleClient.ts` | **NEW.** `getImportSchedule`/`putImportSchedule`/`runImportNow`. |
| `store/settingsStore.ts` (or `store/importScheduleStore.ts`) | **NEW/EDIT.** Schedule config + last-run; selector-gated; NOT in graphStore (I3). |
| `api/types.ts` | **EDIT.** `UploadResponse`, `ImportSchedule`, `ImportSchedulePutResponse`. |
| `i18n/locales/en.json`, `i18n/locales/it.json` | **EDIT.** `ingest.upload*` (§3) + `settings.import.*` (§5) keys; en↔it parity. |

### Build order
1. **Backend first** (unblocks the UI):
   a. `upload.py` sanitizer (pure, unit tests for traversal/415/413/422) → `POST /ingest/upload`.
   b. `models.py` `ImportSchedule` + Alembic `0008` + `config.py` caps.
   c. `import_scheduler.py` (`run_one_scan` bounded + `ImportScheduler` task) + lifespan wiring.
   d. `GET/PUT /import-schedule` + `POST /import-schedule/run-now`.
   e. docker-compose env + example mount; `make er` + `make openapi`; pytest.
2. `api/ingestClient.uploadDocument` + `UploadZone` + `IngestView` edit; vitest + Playwright (drop a
   `.md` → POST fired → run list refreshes; drop a `.pdf` → 415 toast names M5).
3. `api/importScheduleClient` + store + `ImportScheduleCard` + `SettingsPanel` edit; vitest (toggle/
   frequency PUT; Run-now POST; dir_ok:false warning renders).
4. i18n keys (en + it) + parity vitest.
5. Playwright D5 (optional): capture the upload zone + the Automatic-import card if PM wants the
   M4-EXT screens; otherwise fold into existing Ingest/Settings screens.

### Acceptance mapping (M4-EXT)
- **Feature U** → §2 (endpoint, sanitizer, 415 text/markdown-only naming F12/M5, 413 size cap, 201
  with page_id+status), §3 (drag-drop in Ingest section, accepted types + M5 note, run-list
  refresh), §8 (I1 reuse of `ingest_file` gate; I5 writes only `raw/sources/`; I7 byte cap).
- **Feature S** → §4.1 (`import_schedules` schema + Alembic 0008), §4.2 (incremental: hash-compare
  + `ingest_file` gate, no new ledger), §4.3 (copy → watcher ingests; scans create `ingest_runs`
  rows via the pipeline), §4.4 (bounded scan: MAX_FILES + MAX_SECONDS + non-recursive + no overlap),
  §4.5 (asyncio scheduler + lifespan + reschedule-on-next-tick), §4.6 (GET/PUT/run-now contract),
  §5 (Settings "Automatic import" card with container-path hint + i18n en/it), §6 (docker-compose
  mount), §7 (mounted-path constraint), §8 (I1/I5/I7/I8/I9).

### Do NOT
- Write anywhere except `vault/raw/sources/` from either feature (I5 — never touch `wiki/` or
  `.obsidian/`; the pipeline alone authors wiki pages).
- Join, trust, or accept caller-supplied path segments for the upload target — **basename only**;
  no `..`, no absolute paths; reject + containment-check (I-security / §2.2).
- Accept non-text uploads or ingest non-text scanned files (415 / skip; F12/M5 boundary — do not
  pre-empt multi-format).
- Re-scan or re-import unchanged files (I1 — hash-compare before copy; rely on the `ingest_file`
  mtime/hash gate; never bypass it).
- Add an unbounded or recursive-by-default scan, a `setInterval` poll, or overlapping scans
  (I7 — MAX_FILES + MAX_SECONDS, single in-flight, `setTimeout`+`AbortController` on the UI).
- Add APScheduler or any scheduler framework (I9 — a single asyncio lifespan task suffices).
- Create a new per-file import tracking table or a new run table (I9 — reuse `pages.content_hash`
  for change detection and `ingest_runs` for the cost ledger).
- Modify `ingest_file` / `run_ingest_pipeline` / the watcher handler (ADR-0003 — both features are
  callers of the seam, not changes to it).
- Put scheduled-import or upload state in `graphStore`, or subscribe to a whole store (I3 — keep it
  in settings/ingest stores; selector-gated).
- Pretend host filesystem browsing works from the container, or render a native folder picker for
  `source_dir` (§7 — it is a container-path text input + backend dir-readable check).
- Hardcode a provider/model anywhere in the new code (I6 — ingest routing stays in the existing seam).
- Skip `make er` / `make openapi` after the schema + endpoint changes (I8 zero-drift gate).
```