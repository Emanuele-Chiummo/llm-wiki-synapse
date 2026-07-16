# ADR-0065 — Marker large-PDF conversion via page-range chunking

- **Status:** Accepted
- **Date:** 2026-07-10
- **Sprint:** v1.4.1
- **Extends:** ADR-0051 (Marker microservice as opt-in PDF extractor, `PDF_EXTRACTOR=marker`) ·
  ADR-0025 §4.2 (F12 binary ingest → `.extracted.md` companion) · ADR-0053 (effective Marker
  settings captured at enqueue time). Does **not** supersede any of them: the microservice stays
  opt-in, single-GPU/single-flight, and the companion-file → watcher (I1) path is unchanged.
- **Features:** F12 (multi-format ingest) · ServiceNow doc connector (Marker engine).
- **Invariants owned:** **I7** (bounded work: the split is a fixed page-range loop; one Marker
  batch at a time; peak VRAM bounded to *models + one chunk*) · **I1** (still writes one
  `<stem>.extracted.md` companion; the watcher ingests it incrementally — no extra ingest call) ·
  **I5** (companion keeps YAML frontmatter). No provider is hardcoded (I6 untouched — Marker is an
  extractor, not an `InferenceProvider`).

---

## 1. Context

Importing a ~190 MB ServiceNow documentation export for Marker conversion **blocked/failed**.
Two independent size gates and a hardware ceiling were in play:

1. **Backend gate** — `POST /ingest/convert-marker` aborted the upload stream at
   `MAX_UPLOAD_BYTES` (25 MB, the generic text-upload cap) → HTTP 413 before Marker was ever
   called.
2. **Marker service gate** — `service.py --max-upload-mb` defaulted to 50 MB → a second 413.
3. **Hardware** — even with both limits raised, running Marker on a several-hundred-page PDF in a
   single pass would exhaust the RTX 3060's 12 GB VRAM (OOM) or blow the 120 s HTTP timeout.

A tempting user-facing framing was *"split the giant PDF by topic"*. That is the wrong layer:
identifying topics requires **understanding** the document, which requires converting it first —
exactly the blocked step. Topical splitting into wiki pages **already** happens downstream in the
ingest orchestrator (long-source chunking, ADR-0063) once a `.extracted.md` exists. The only real
bottleneck is producing that markdown from an oversized PDF.

## 2. Decision

Split large PDFs **by page range inside the Marker service**, convert each chunk serially with a
single shared model set, and concatenate the per-chunk markdown into one result. The service's
`/convert` contract is unchanged for callers (`{markdown, pages}`, now `+ chunks`).

**Where the split lives — the Marker service, not the backend driver.** The GPU host owns the
memory pressure, so it owns the chunking. The backend stays a thin driver that makes one
`/convert` call per file (unchanged); it needs no `pypdfium2` dependency and no per-chunk
orchestration. `pypdfium2` (already a Marker dependency, used for page counting) does the split —
no new package.

**Mechanics (`tools/marker-converter/service.py`):**
- `_build_converter()` loads the Surya/Marker model set **once** per job; `_convert_one()` runs it
  per PDF path. Models are the fixed per-job cost — never reloaded per chunk.
- `_convert_pdf_bytes()`: count pages; if `pages <= pages_per_chunk` (or count unknown, or
  chunking disabled) → whole-file conversion, **byte-for-byte the old behaviour**. Otherwise split
  into `pages_per_chunk`-page sub-PDFs (`_split_pdf_pages`, pypdfium2 `PdfDocument.new()` +
  `import_pages` + `save`), convert each with the shared converter, join markdown in page order.
- **Graceful fallback:** any split error logs a warning and falls back to a single whole-file
  conversion — chunking never makes a previously-working file fail.
- New knob `--pages-per-chunk` (default **25**, `<= 0` disables). `--max-upload-mb` default raised
  **50 → 300**.

**Backend (`backend/app/`):**
- New `MARKER_MAX_UPLOAD_BYTES` (default **300 MB**), used **only** by `/ingest/convert-marker`.
  The generic `MAX_UPLOAD_BYTES` (25 MB) is untouched — every other upload path keeps it.
- `MARKER_TIMEOUT_SECONDS` default raised **120 → 1800 s**. A chunked job runs all chunks inside a
  **single** HTTP request, so the timeout must cover the whole job. It is a ceiling, not a fixed
  wait — small PDFs still finish in seconds.

## 3. Consequences

- A 190 MB / several-hundred-page ServiceNow export now converts on a 12 GB GPU: peak VRAM is
  bounded to *models + one chunk*, and no single Marker call is large enough to time out.
- Contract is backward-compatible: small PDFs take the identical whole-file path; the response
  gains an additive `chunks` field.
- **Reverse-proxy caveat (documented, not code):** uploads through Cloudflare Tunnel hit a ~100 MB
  request-body ceiling regardless of app config. Very large PDFs must be imported over the
  LAN / Tailscale directly to the backend. Called out in the `MARKER_MAX_UPLOAD_BYTES` docstring.
- **Trade-off:** a genuinely hung Marker service now holds the single-flight batch slot up to
  30 min (was 2). Acceptable for a self-hosted single-user tool; the batch is still single-flight
  and returns 429 to concurrent callers.

## 4. Alternatives considered

- **Split in the backend driver** (M `/convert` calls per file). Rejected: pushes GPU-memory
  concerns into the backend, adds a `pypdfium2` backend dependency, and reloads Marker models per
  chunk unless a warm-model service is introduced anyway.
- **Just raise the limits.** Rejected: does nothing for the VRAM OOM / timeout on a whole-PDF pass.
- **Split by topic before conversion.** Impossible — requires converting first (see §1); topical
  splitting already exists downstream (ADR-0063).

## 5. Tests

- `backend/tests/test_marker_chunking.py` — orchestration of `_convert_pdf_bytes` with the
  pypdfium2/marker helpers stubbed: whole-file (small / uncountable / chunking-disabled), split +
  in-order concatenation, and split-failure fallback.
- `backend/tests/test_convert_marker.py::test_convert_marker_rejects_oversize_file` — updated to
  assert the endpoint enforces `MARKER_MAX_UPLOAD_BYTES` (not the generic 25 MB cap).
