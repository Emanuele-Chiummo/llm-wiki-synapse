# ADR-0068 — Source Watch imports wider file types (P3-c)

- **Status:** Accepted
- **Date:** 2026-07-10
- **Feature:** F12 (multi-format ingest) · v1.5 LLM Wiki parity, slice P3-c
- **Supersedes/relates:** [[ADR-0020]] (scheduled folder import), [[ADR-0025]] (F12 extractor), [[ADR-0066]] (1:1 parity program)

## Context

Synapse's scheduled folder import ("Source Watch", ADR-0020) previously copied only
`.md/.txt/.markdown` files to `raw/sources/`; the watcher ingested them directly. Binary and
convertible documents (`.pdf/.docx/...`) were extracted **only** on the synchronous upload path
(`POST /ingest`), never by the scheduler. LLM Wiki's folder watcher imports a wider set of
document types. Slice P3-c closes that gap.

The extractor layer (ADR-0025 / `ingest/extract.py`) was extended (commit 32ec935/2a6f913) to
support `.csv/.html/.mdx/.rtf/.odt/.ods/.odp` in addition to `.pdf/.docx/.pptx/.xlsx`. This ADR
covers wiring those into the **scheduled scan** plus per-schedule controls.

## Decision

1. **Scheduled scan extracts, like the upload path.** `import_scheduler.run_one_scan()` now, for
   each accepted file whose extension is in `_EXTRACTABLE_EXTENSIONS`, copies the original into
   `raw/sources/` **and** writes a `<stem>.extracted.md` companion (valid Obsidian YAML
   frontmatter, I5). The watcher ignores the binary and ingests the companion — identical to the
   upload handler. Extraction happens in the scheduler driver, **never** inside the watcher
   (Do-NOT #12). Text types (`.md/.txt/.markdown`) are still copied as-is with no companion.

2. **Three per-schedule config fields** (model `ImportSchedule`, Alembic 0028):
   - `allowed_extensions` (TEXT, NULL → default **wider** set = text ∪ extractable). Placeholder
     image/AV types are deliberately **not** auto-imported by the scheduler.
   - `excluded_folders` (TEXT, NULL → none) — folder names skipped during the recursive scan.
   - `max_size_mb` (INTEGER, NULL/0 → no cap) — files larger than the cap are skipped (I7).

3. **Bounds preserved.** The scan stays bounded by `IMPORT_SCAN_MAX_FILES` +
   `IMPORT_SCAN_MAX_SECONDS` (I7) and incremental via the content-hash compare (I1). Extraction is
   best-effort: a failure logs a warning and leaves the binary copied (companion absent), never
   aborting the scan.

4. **`.doc` remains out of scope** — no lightweight pure-Python OLE Word extractor; deferred
   (documented in `ingest/extract.py`).

## Consequences

- Source Watch now imports the same document types as drag-and-drop upload; the settings page
  (`ImportScheduleCard`) exposes grouped-checkbox type selection, excluded folders, and max size.
- A pathological single file can still consume up to one tick's wall-clock during its own
  extraction (same risk as the upload path); the per-tick cap resumes it next tick.
- Back-compatible: existing schedule rows read the three new columns as NULL and get the wider
  default automatically.

## Tests

`backend/tests/test_source_watch_p3c.py` (helpers + scan behaviour) and the updated
`test_upload_and_schedule.py` (placeholder media still skipped). Frontend:
`import-schedule-store.test.ts` contract + live preview verification of the card.
