# ADR-0069 — MinerU cloud PDF extractor (P3-d)

- **Status:** Accepted
- **Date:** 2026-07-10
- **Feature:** F12 (multi-format ingest) · v1.5 LLM Wiki parity, slice P3-d
- **Relates:** [[ADR-0051]] (pluggable PDF extractor seam), [[ADR-0066]] (parity program — amends I9 for opt-in cloud providers)

## Context

The PDF extractor seam (ADR-0051) supports `pypdf` (default, pure-Python) and `marker` (local
ML microservice). LLM Wiki offers **MinerU** as a high-quality cloud extractor. ADR-0066 amended
I9 to permit cloud providers **as opt-in, off by default, with an upload warning**. This ADR adds
MinerU as a third `pdf_extractor` value under those constraints.

## Decision

1. **`pdf_extractor` gains `mineru`** (config_overrides `_PDF_EXTRACTOR_VALUES` = {pypdf, marker,
   mineru}). Default stays `pypdf`. Selecting `mineru` routes `extract_text()` PDF dispatch to
   `_extract_pdf_via_mineru()`.

2. **Cloud call is best-effort with unconditional pypdf fallback.** On ANY failure — missing API
   key, non-2xx, timeout, invalid/empty body — the adapter logs a WARNING and returns None; the
   caller falls back to pypdf. PDF ingest never breaks (mirrors the Marker contract).

3. **The API key is a SECRET → env-only.** `MINERU_API_KEY` (settings.mineru_api_key) is read from
   the environment and is **structurally excluded** from the runtime config-override surface
   (config_overrides §2.4 — secrets are never PUT-able). Until the key is set, selecting `mineru`
   is a guaranteed no-op fallback: **nothing is uploaded**. The non-secret `mineru_api_url` (S21)
   and `mineru_timeout_seconds` (S22) ARE runtime-tunable via `PUT /config/app/{key}`.

4. **I9 upload warning in the UI.** The PDF settings page shows an amber warning whenever `mineru`
   is selected: it uploads PDF content to an external service, stays off until `MINERU_API_KEY` is
   set, and falls back to pypdf on error.

5. **Wire protocol is provisional.** MinerU's public v4 API is task-based (submit → poll). This
   adapter implements a defensive submit-and-read contract; the exact endpoint/response shape MUST
   be validated against a live `MINERU_API_KEY` before relying on cloud extraction. This is called
   out in the code and here — the seam, gating, fallback, and UX are complete and tested; the live
   cloud round-trip is the one untested piece (no key available in dev/CI).

## Consequences

- Three PDF extractors: pypdf (default/offline), marker (local, high quality), mineru (cloud,
  opt-in). Privacy posture is explicit: only pypdf and marker keep content on-prem.
- Bounded by `mineru_timeout_seconds` (I7); no loop.
- Adding two runtime keys took ALLOWED_CONFIG_KEYS 20 → 22; snapshot tests updated accordingly.

## Tests

`backend/tests/test_p3d_mineru_seam.py` — config-override validation, secret-exclusion, routing,
no-key-no-upload, mocked 2xx success, non-2xx fallback. Frontend: live-verified the option +
amber warning + mineru_api_url/timeout cards render (grouped nav, brand colours).
