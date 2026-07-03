# ADR-0051 — Pluggable PDF extractor seam: Marker over HTTP with pypdf fallback

- **Status:** Accepted
- **Date:** 2026-07-03
- **Sprint:** v0.8 (M8 — "Content power"; R8-1)
- **Features:** F12 (multi-format ingest), F3 (ingest pipeline)
- **Builds on:** ADR-0025 (F12 multi-format ingest seam — `extract.py` static guard) ·
  ADR-0003 (thin ingest seam preserves F17) · ADR-0009 (bounded loops / I7)
- **Reference:** R8-1 (SPRINT-v0.8-SCOPE.md §R8-1); tools/marker-converter/ (existing
  Marker engine from ServiceNow connector); CLAUDE.md §3 invariants I1/I6/I7/I9
- **Invariants owned:** I6 (pluggable inference — Marker is called over HTTP, not imported;
  the backend never carries torch/surya deps) · I7 (loops bounded — marker call has a
  configured timeout; pypdf fallback is unconditional) · I9 (do not reinvent — reuses the
  existing Marker venv from `tools/marker-converter/`)
- **Author:** backend-engineer

---

## 1. Context

`backend/app/ingest/extract.py` provides the single dispatch point for text extraction
(ADR-0025). Prior to v0.8 the PDF path was pypdf-only: lightweight, pure-Python, no GPU
dependency, but limited to embedded text. Scanned PDFs and layout-heavy documents (tables,
multi-column) extract poorly or not at all.

The project already contains a high-quality Marker-based extraction engine in
`tools/marker-converter/servicenow_connector.py`, which uses
`marker-pdf` + `torch` + `surya-ocr` models. These dependencies (several GB of model
weights, GPU/MPS requirements) cannot live inside the `backend` Docker container without
breaking the lightweight deployment model and adding build-time ML stack complexity.

The owner requested Marker as a first-class PDF extraction option that works in a
TrueNAS SCALE homelab with an RTX 3060 (already driving Ollama), while keeping the
`backend` container lean.

---

## 2. Decision

**Pluggable HTTP seam with unconditional pypdf fallback.**

1. A new env var `PDF_EXTRACTOR` (values: `pypdf` | `marker`; default `pypdf`) controls
   which extraction backend is used for `.pdf` files in `extract_text()`.

2. When `PDF_EXTRACTOR=marker`, `extract.py` calls a lightweight FastAPI microservice
   (`tools/marker-converter/service.py`) at `MARKER_SERVICE_URL` (default
   `http://host.docker.internal:8555`) via `POST /convert` with the raw PDF bytes
   (multipart `file` field) and a bounded timeout (`MARKER_TIMEOUT_SECONDS`, default 120 s).
   The service returns `{"markdown": str, "pages": int}`.

3. On **any failure** — connection refused, timeout, non-200 HTTP status, invalid/missing
   JSON field — `extract.py` logs a WARNING and **unconditionally falls back to pypdf**.
   This fallback is permanent and cannot be suppressed (PM decision: pypdf is never removed).

4. The Marker package (`marker-pdf`, `torch`, `surya-ocr`) is installed **only** in the
   `tools/marker-converter/` venv, not in the backend container. The backend calls Marker
   over HTTP; it never imports it. The ADR-0025 static guard is extended to note this.

5. `tools/marker-converter/service.py` is a self-contained FastAPI app with:
   - `POST /convert` — multipart PDF upload → `{"markdown", "pages"}`. One-at-a-time
     (asyncio.Lock; 429 when busy). Max 50 MB upload guard (I7). Marker runs in a thread
     pool executor (non-blocking).
   - `GET /health` — 200 `{"status": "ok"}`.

6. The service is opt-in: it is not part of the default `docker-compose.yml` and not
   required for normal operation. `PDF_EXTRACTOR=pypdf` (default) requires no running
   microservice.

---

## 3. Consequences

**Positive:**
- Marker's superior extraction quality (tables, multi-column, scanned PDFs via OCR) is
  available without adding ML deps to the backend container.
- pypdf is retained as the zero-config default and guaranteed fallback. The backend never
  has a hard dependency on the microservice.
- The seam follows the same pattern as the planned Whisper transcription microservice
  (R8-3), establishing a reusable template for host-side GPU workloads.
- The one-at-a-time asyncio.Lock on the service side prevents GPU memory exhaustion from
  concurrent conversion requests.

**Negative / risks:**
- The microservice must be started manually on the host (not auto-started by the backend
  container). A misconfigured `MARKER_SERVICE_URL` silently degrades to pypdf with a
  WARNING logged (by design).
- Marker model download (~4 GB) is a one-time cost on first run in the venv.
- The 120 s default timeout is generous for large PDFs but means a stuck service call
  ties up one extraction slot for up to 2 minutes before falling back.

**Invariant compliance:**
- **I1**: no vault re-scan — extraction is per-file, triggered by the watcher (unchanged).
- **I6**: `extract_text()` remains the single dispatch point; Marker is behind the seam,
  not a new code path exposed to callers.
- **I7**: the marker call is bounded by `MARKER_TIMEOUT_SECONDS`; the service enforces a
  50 MB upload cap and a 429 concurrency gate.
- **I9**: reuses the Marker engine already present in `tools/marker-converter/`, adds
  FastAPI/uvicorn to the existing venv requirements.

---

## 4. HTTP microservice contract

```
POST {MARKER_SERVICE_URL}/convert
Content-Type: multipart/form-data
  file: <PDF bytes>  (field name must be "file")

200 OK
Content-Type: application/json
{"markdown": "<extracted markdown string>", "pages": <int>}

Non-200 responses:
  413  Upload exceeds size limit
  429  Conversion already in progress
  500  Marker conversion failed (detail in body)
```

The backend considers any non-200 or network error a fallback trigger (permanent,
unconditional pypdf degradation). The caller does not need to distinguish between
a 429 (busy) and a 500 (error) — both degrade identically.

---

## 5. Configuration reference

| Env var | Default | Description |
|---|---|---|
| `PDF_EXTRACTOR` | `pypdf` | `pypdf` or `marker` |
| `MARKER_SERVICE_URL` | `http://host.docker.internal:8555` | Microservice base URL |
| `MARKER_TIMEOUT_SECONDS` | `120.0` | Per-call HTTP timeout (I7) |

---

## 6. ADR index update note

This ADR is number 0051. The sprint scope document (SPRINT-v0.8-SCOPE.md EC-M8-6)
references "ADR-0050" for this decision; ADR-0050 was already taken by
`0050-retrieval-wiki-only-scope.md` (written during v0.7 hardening). The next free
number is 0051; the sprint scope reference is superseded by this document.
