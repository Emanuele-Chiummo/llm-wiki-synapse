# Sprint v0.8 — PM Scope Lock

> Milestone: M8 — "Content power"
> Author: product-manager
> Date: 2026-07-03
> Branch: sprint/v0.8 (cut from sprint/v0.7 after v0.7.0 tag)
> Prerequisite: M7 exit criteria met (EC-M7-1..EC-M7-HCP confirmed by Emanuele).
> Source roadmap: docs/reference/ROADMAP-v0.7-v1.0.md §v0.8

---

## 0. Engineer ground rule (READ BEFORE TOUCHING ANY FILE)

**No git restore, git checkout, git stash, or any command that discards working-tree
changes.** Other agents on the same branch may have uncommitted edits that are
legitimate in-progress work. If you find changes in a file you need to edit, read them
first and integrate, do NOT discard. Escalate to orchestrator if you cannot determine
ownership of an uncommitted change.

---

## 1. Sprint Goal

Ingest anything, at quality — leverage what already exists in-house: promote the proven
Marker engine to a first-class PDF path (with pypdf fallback always), add vision captions
and AV transcription behind host-side seams, give users vault export/backup, sharpen
search with type/date facets, complete citation wiring in the UI, and ship the Chrome
clipper as a CI-packaged artifact with an unpacked-install doc.

---

## 2. Committed Scope

Exactly the following 7 items. Anything else is out of scope and requires explicit PM
re-approval before any token is spent on it.

---

### R8-1 — Marker as first-class PDF extractor (pluggable seam + pypdf fallback)

| Field | Value |
|---|---|
| Feature ID | F12 (multi-format ingest), F3 (ingest pipeline) |
| Owner | backend-engineer (extract.py seam + fallback logic) + devops-engineer (tools/marker-converter microservice Dockerfile + compose entry) |
| Effort | M |

**Architecture decision (PM-locked, do not re-litigate without ADR):**
Marker carries heavy deps (torch, surya models, MPS/CUDA) that cannot live inside the
`backend` container. The solution is a pluggable "external extractor" seam in
`backend/app/ingest/extract.py`:

- New env var `PDF_EXTRACTOR` = `pypdf` (default) | `marker`.
- When `PDF_EXTRACTOR=marker`, `extract_text()` for `.pdf` files calls an optional
  HTTP microservice at `MARKER_SERVICE_URL` (default `http://localhost:7321`) via
  `POST /convert` with the raw PDF bytes and returns the Markdown string.
- If the marker service is unreachable (connection error or timeout), the backend
  ALWAYS falls back to pypdf silently, logging a WARNING. pypdf is never removed.
- The microservice lives in `tools/marker-converter/` as a lightweight FastAPI/Flask
  wrapper around the existing `servicenow_connector.py` engine. It exposes exactly one
  endpoint: `POST /convert` (multipart PDF upload) → `{"markdown": "..."}`.
- A new `docker-compose.override.yml` (or a named profile in the main compose) adds the
  `marker-service` container as opt-in; not started by default.

**Acceptance criteria:**
- AC-R8-1-1: `backend/app/ingest/extract.py` defines a `_extract_pdf_via_marker(path)` internal helper that calls `POST {MARKER_SERVICE_URL}/convert` with a 30 s timeout. If the call succeeds, returns the response `markdown` field. A pytest unit test with `httpx` mock asserts (a) correct request shape and (b) fallback to pypdf when the mock raises `httpx.ConnectError`.
- AC-R8-1-2: `extract_text()` dispatches to `_extract_pdf_via_marker` only when `PDF_EXTRACTOR=marker`. With the default (`pypdf`) the call path is identical to the pre-v0.8 path; existing pypdf tests remain green with zero changes.
- AC-R8-1-3: `tools/marker-converter/service.py` (new file) implements a FastAPI app with `POST /convert` (multipart `file` field) and `GET /health`. Accepts any PDF; returns `{"markdown": str, "pages": int}`. A pytest (run in the tools/ venv) asserts the health endpoint returns 200 and the convert endpoint returns a non-empty markdown string for the existing fixture PDF in `tools/marker-converter/out/`.
- AC-R8-1-4: `PDF_EXTRACTOR` and `MARKER_SERVICE_URL` are documented in `docs/DEPLOY.md` (D6b) under a new "Optional: Marker PDF extractor" section. The section explains the host-side dep isolation rationale and the docker-compose profile to enable the service.
- AC-R8-1-5: ADR-0050 (new) documents the pluggable-extractor seam decision: why Marker stays host-side, the HTTP microservice contract, and the pypdf-always-fallback invariant. ADR index updated.
- AC-R8-1-6: The static guard in `extract.py` docstring is updated to note that pypdf is still the sole container-side PDF library; Marker is called over HTTP, not imported.

**Sequencing note:** `extract.py` is the shared file for R8-1, R8-2, and R8-3. Strict
order: R8-1 FIRST (adds the seam and helper structure), then R8-2 (adds image dispatch),
then R8-3 (adds AV dispatch). Each must be merged before the next starts. R8-2 and R8-3
engineers must read the post-R8-1 extract.py before touching it.

---

### R8-2 — Vision captions for images

| Field | Value |
|---|---|
| Feature ID | F12 (multi-format ingest), F17 (InferenceProvider — image content) |
| Owner | ai-agent-engineer (provider.chat() image content routing) + backend-engineer (image_captions table, extract.py dispatch) |
| Effort | L |

**Provider support matrix (PM-verified before scope lock):**
- `ApiProvider` (Anthropic Messages API): supports image content blocks (base64 or URL).
  YES — implement.
- `ApiProvider` (OpenAI-compatible): depends on model; use `supports_vision` capability
  flag (add to `capabilities()` return dict if not already present).
- `OllamaProvider`: vision models (llava, bakllava, minicpm-v) support image content.
  YES — implement; gate on `supports_vision` capability.
- `CliAgentProvider`: Claude CLI supports image content.
  YES — implement.
- Providers without `supports_vision`: skip caption; store `None`; log INFO.

**Acceptance criteria:**
- AC-R8-2-1: `InferenceProvider.capabilities()` ABC gains a `supports_vision: bool` key
  (default `False`). `ApiProvider` (Anthropic), `CliAgentProvider`, and `OllamaProvider`
  (when the configured model name contains `llava`, `vision`, `minicpm-v`, or a
  configurable `OLLAMA_VISION_MODELS` env list) return `True`. A unit test asserts each
  provider returns the correct value for fixture model names.
- AC-R8-2-2: A new `image_captions` table is added via Alembic migration:
  `id`, `sha256` (unique), `file_path`, `vault_id`, `caption` (text), `provider_name`,
  `created_at`. The ER diagram (`docs/er/schema.mmd`) is regenerated via `make er`.
- AC-R8-2-3: `extract_text()` for image extensions (`.png`, `.jpg`, `.jpeg`, `.gif`,
  `.webp`) checks the `image_captions` table by SHA256 first. Cache HIT: return cached
  caption. Cache MISS and provider `supports_vision`: call `provider.chat([{"role":
  "user", "content": [{"type": "image", ...}, {"type": "text", "text": "Describe this
  image for a knowledge base entry."}]}])`, store result in `image_captions`, return
  caption. Cache MISS and no vision support: return the existing placeholder string.
  A pytest with a mock provider asserts all three paths.
- AC-R8-2-4: Vision captioning is bounded: max `VISION_MAX_IMAGES_PER_RUN` images per
  ingest run (default 10, env-configurable). If the run cap is reached, remaining images
  fall back to placeholder. A unit test asserts the cap is enforced. Cost is logged per
  caption call (`total_cost_usd` via existing run accounting, I7).
- AC-R8-2-5: `extract_text()` remains synchronous at the call site; if the provider is
  async, caption fetching uses `asyncio.run()` scoped to the call. No per-token streaming
  is needed for captions (I3 not applicable here; still document the design choice).
- AC-R8-2-6: `docs/api/openapi.json` regenerated; the `image_captions` table appears
  in the ER diagram. No new API endpoints are required for this item (captions are
  internal); if a `GET /image-captions/{sha256}` debug endpoint is added it must be
  documented.

**Sequencing note:** depends on R8-1 being merged first (extract.py restructured).
ai-agent-engineer owns `backend/app/ingest/provider/` changes; backend-engineer owns
`extract.py` dispatch and Alembic migration. These two sub-tasks can be parallelized
WITHIN the item but must both be merged in a single R8-2 PR to keep extract.py coherent.

---

### R8-3 — Audio/video transcription (local Whisper, opt-in)

| Field | Value |
|---|---|
| Feature ID | F12 (multi-format ingest), F3 (ingest pipeline) |
| Owner | backend-engineer (extract.py AV dispatch + host seam) + devops-engineer (whisper service Dockerfile, optional compose profile) |
| Effort | L |

**Architecture decision (mirrors R8-1):**
Whisper (whisper.cpp or mlx-whisper on MPS) carries GPU/MPS deps that cannot live in
the backend container. Same pattern as R8-1: optional HTTP microservice at
`WHISPER_SERVICE_URL` (default `http://localhost:7322`) with `POST /transcribe`
(multipart audio/video upload) → `{"transcript": str, "duration_s": float}`.
Backend falls back to the existing placeholder text if the service is unreachable.
Feature is fully opt-in: `AV_TRANSCRIPTION=false` by default.

**Acceptance criteria:**
- AC-R8-3-1: New env vars: `AV_TRANSCRIPTION` (bool, default `false`) and
  `WHISPER_SERVICE_URL` (str, default `http://localhost:7322`). When
  `AV_TRANSCRIPTION=false`, `extract_text()` for AV extensions returns the existing
  placeholder string unchanged; no network call is made. A unit test asserts no call
  when the flag is off.
- AC-R8-3-2: When `AV_TRANSCRIPTION=true`, `extract_text()` for `.mp3`, `.mp4`,
  `.wav`, `.m4a` calls `POST {WHISPER_SERVICE_URL}/transcribe` with a 120 s timeout.
  On success, returns the `transcript` string (capped at `EXTRACT_MAX_CHARS`, I7). On
  connection failure, falls back to placeholder and logs WARNING. A pytest with an
  `httpx` mock asserts the success path, the fallback path, and the output cap.
- AC-R8-3-3: `tools/whisper-service/service.py` (new directory and file) implements a
  FastAPI app with `POST /transcribe` (multipart `file` field) and `GET /health`.
  Delegates to `mlx_whisper` (Apple Silicon path, configurable) or `whisper` (CPU/CUDA
  fallback). A pytest in the tools/ venv asserts the health endpoint returns 200.
  A note in the README says real transcription tests require a GPU/MPS host and are
  skipped in CI (`@pytest.mark.skipif(not WHISPER_AVAILABLE, ...)`).
- AC-R8-3-4: Transcription is bounded: max `AV_MAX_DURATION_SECONDS` per file (default
  3600 s; if the service returns a duration exceeding this, the transcript is accepted
  as-is but a WARNING is logged — truncation is the service's responsibility). Max
  `AV_MAX_FILES_PER_RUN` per ingest run (default 5). Cost logging: local Whisper has
  zero LLM cost; log `total_cost_usd=0.00` for AV runs to keep I7 accounting
  consistent.
- AC-R8-3-5: `docs/DEPLOY.md` gains a "Optional: Whisper transcription" section
  documenting the mlx-whisper setup, the compose profile, and the env vars.
  `docs/sequences/` updated with a one-step note on the AV dispatch path (can be an
  addendum to the existing ingest sequence diagram, not a new diagram).
- AC-R8-3-6: `tools/whisper-service/` has its own `requirements.txt` and a
  `README.md` with setup instructions for MPS (Apple Silicon) and CPU paths.

**Sequencing note:** depends on R8-2 being merged first (extract.py fully restructured
for the seam pattern). This is the THIRD item to touch extract.py; merge strictly after
R8-2. devops-engineer works on `tools/whisper-service/` in parallel with backend-engineer
once the R8-1 seam pattern is understood — the two sub-tasks converge at PR time.

---

### R8-4 — Vault export / backup

| Field | Value |
|---|---|
| Feature ID | F15 (cross-platform / portability), K1 (vault integrity) |
| Owner | backend-engineer |
| Effort | M |

**Acceptance criteria:**
- AC-R8-4-1: `GET /export` returns a streaming ZIP of the entire `vault/` directory
  (raw/ + wiki/ + schema.md + purpose.md; excludes `.obsidian/` binary cache files
  by default but includes `.obsidian/*.json` config). Response header:
  `Content-Disposition: attachment; filename="synapse-vault-{date}.zip"`.
  ZIP size is bounded: if the uncompressed total exceeds `EXPORT_MAX_BYTES` (default
  500 MB), the endpoint returns HTTP 413 with a JSON error before streaming starts.
  A pytest asserts the ZIP contains at least `wiki/index.md` and `vault/schema.md`
  for a fixture vault.
- AC-R8-4-2: `GET /export/data.json` returns a JSON dump of:
  `{"pages": [...], "links": [...], "edges": [...], "runs": [...], "exported_at": "..."}`.
  All records for the current vault_id are included. Response is streamed (NDJSON or
  single JSON object with `Content-Type: application/json`). A pytest asserts the
  top-level keys are present and `pages` count matches the DB for a fixture vault.
- AC-R8-4-3: Both endpoints are documented in `docs/api/openapi.json` (D4 regenerated)
  and a new "Backup & Restore" section in `docs/USER.md` (D6a) describes how to
  download both artifacts, unzip the vault, and restart Synapse pointing at it.
  The restore path is documentation only — no import endpoint in this sprint.
- AC-R8-4-4: The export endpoints are added to `backend/app/main.py`. Because main.py
  is already large, the implementation SHOULD be extracted to
  `backend/app/export.py` (a new router module) and included via
  `app.include_router(export_router)`. This is a SHOULD, not a MUST — if the engineer
  judges an inline implementation cleaner, they must justify the choice in the PR
  description.
- AC-R8-4-5: Export is rate-limited: max 1 concurrent export per vault_id (a simple
  asyncio.Lock keyed by vault_id). If a second export request arrives while one is
  running, return HTTP 429. A pytest asserts the 429 path.

**Sequencing note:** `main.py` is shared by R8-4 and R8-5. Strict order: R8-4 FIRST
(adds export router), then R8-5 (modifies the /search endpoint). If R8-4 extracts to
`export.py`, the main.py conflict surface for R8-5 is minimized. Assign main.py
ownership to backend-engineer; R8-5 frontend-engineer does not touch main.py until R8-4
is merged.

---

### R8-5 — Search filters and sort (type facet + date sort)

| Field | Value |
|---|---|
| Feature ID | F5 (4-phase retrieval), F1 (UX gap #6) |
| Owner | backend-engineer (GET /search params) + frontend-engineer (facet sidebar UI) |
| Effort | M |

**Acceptance criteria:**
- AC-R8-5-1: `GET /search` gains two optional query parameters: `type` (string, one of
  `entity|concept|source|synthesis|comparison|query`; filters results to pages with
  matching YAML `type` frontmatter field) and `sort` (`relevance` default |
  `date_desc` | `date_asc`, sorting by `updated_at`). A pytest asserts that a fixture
  vault with pages of mixed types returns only the matching type when `type=entity`, and
  that `sort=date_desc` returns pages in descending `updated_at` order.
- AC-R8-5-2: The existing 4-phase retrieval in `backend/app/rag/retrieval.py` applies
  the `type` filter at the Qdrant query phase (metadata filter) and the `sort`
  parameter at the assembly phase (re-rank by date if requested, overriding relevance
  score). No new retrieval phase is added; the existing 4-phase structure is preserved
  (I1 spirit: no architectural regression).
- AC-R8-5-3: The UI gains a facet sidebar (or a compact filter bar above search
  results) with: a type multi-select (chips or checkboxes for each page type) and a
  sort dropdown (`Relevance` / `Newest` / `Oldest`). Filter state is stored in the
  Zustand search slice with shallow equality (I3 compliance). Vitest asserts the
  filter components render and that changing the type selection updates the query
  params sent to the backend.
- AC-R8-5-4: Facet sidebar is virtualized if the result list exceeds 50 items
  (TanStack Virtual, I4). The sidebar is responsive: on narrow viewports it collapses
  to a "Filters" button with a popover.
- AC-R8-5-5: `docs/api/openapi.json` regenerated to reflect the new `GET /search`
  parameters. i18n strings for type labels and sort options present in EN and IT (I16).

**Sequencing note:** backend-engineer must not start R8-5 until R8-4 is merged (main.py
conflict avoidance). frontend-engineer CAN start the UI component in parallel because
it does not touch main.py — but must not merge until the backend params are live (to
avoid green tests against a stale API contract).

---

### R8-6 — Citation click-through audit (wire onCitationClick everywhere)

| Field | Value |
|---|---|
| Feature ID | F5 (citations), F1 (UX gap #10) |
| Owner | frontend-engineer |
| Effort | S |

**Context:** The `onCitationClick` handler navigates from a `[n]` citation in chat to
the corresponding wiki page in the editor/preview panel. It is already implemented in
the main chat view; this item ensures it is wired in every context where `MarkdownView`
or an equivalent citation renderer is used.

**Acceptance criteria:**
- AC-R8-6-1: An audit grep is run across `frontend/src/` for all uses of
  `MarkdownView`, `CitationBadge`, `[n]` citation regex rendering, and any component
  that renders cited wiki references. The audit result (list of files) is included as a
  code comment in the PR description.
- AC-R8-6-2: Every identified location has `onCitationClick` wired and pointing to the
  correct navigation action (open the page in the preview panel or editor). If a
  location cannot receive the handler (e.g., a static render context), a comment
  explains why and the location is excluded from the gate.
- AC-R8-6-3: Vitest asserts that clicking a citation element in each wired context
  calls the navigation action with the correct page path. Minimum: chat view (already
  present, regression test), wiki page preview panel, deep-research synthesis preview
  (if it renders citations).
- AC-R8-6-4: No new `any` type escapes introduced. TypeScript strict passes. No
  prop-drilling of `onCitationClick` through more than 2 component levels — use the
  Zustand navigation action directly if needed.

**Sequencing note:** pure frontend; no shared files with R8-4 or R8-5 backend work.
Can be developed in parallel with R8-4/R8-5 backend tracks. Must not be merged until
R8-5 frontend is merged (to avoid confusion if the same MarkdownView components are
touched). If there is no conflict, parallel merge is acceptable.

---

### R8-7 — Chrome clipper packaging and CI artifact

| Field | Value |
|---|---|
| Feature ID | F11 (Chrome MV3 web clipper) |
| Owner | devops-engineer (CI zip artifact) + frontend-engineer (verify functionality + unpacked-install doc) |
| Effort | M |

**Scope boundary (PM-locked):** Chrome Web Store publication requires an owner Google
account and a one-time $5 developer fee plus review time. This is explicitly OUT OF
SCOPE for v0.8. The committed scope is: (a) verify the extension works as
unpacked/developer-mode install, (b) package it as a versioned `.zip` CI artifact,
(c) document the unpacked install flow. Store submission is documented as a future step
only.

**Current state (read before starting):** `extension/` contains `manifest.json` (MV3,
version 1.0.0), `popup.html`, `popup.js`, `options.html`, `options.js`, and
vendor libs (Readability.js, turndown.js). Icons (`icons/` directory) are referenced
in `manifest.json` but must be verified to exist. The `host_permissions` array is
empty — must be populated for the clipper to POST to the local Synapse backend.

**Acceptance criteria:**
- AC-R8-7-1: A functional audit of the extension verifies: (a) `manifest.json`
  `host_permissions` includes `"http://localhost:*/*"` and
  `"http://host.docker.internal:*/*"` so the popup can reach the Synapse API in both
  dev and Docker-on-Mac configurations; (b) all icon files referenced in
  `manifest.json` exist in `extension/icons/`; (c) `popup.js` correctly reads the
  Synapse base URL from `chrome.storage.sync` (set in `options.html`) and falls back
  to `http://localhost:8000`; (d) the `POST /clip` endpoint on the backend accepts the
  clipper payload and enqueues it for ingest (verify endpoint exists; if not, create
  it as a thin wrapper over the existing ingest path — log gap in PR description).
- AC-R8-7-2: A CI step (GitHub Actions, existing workflow or new job
  `.github/workflows/clipper.yml`) zips the `extension/` directory (excluding
  `.git`, `node_modules`, `__pycache__`) and uploads it as a GitHub Actions artifact
  named `synapse-clipper-{version}.zip` on every push to `sprint/v0.8` and on the
  release tag. A vitest/playwright smoke test is NOT required for CI (too complex for
  MV3 headless); the CI step must at minimum validate `manifest.json` is valid JSON
  and that all referenced files exist (a small Node.js/Python check script).
- AC-R8-7-3: `docs/USER.md` (D6a) gains a "Chrome Web Clipper" section with:
  step-by-step unpacked install instructions (chrome://extensions → Developer mode →
  Load unpacked → select `extension/`); how to set the Synapse URL in options;
  one-sentence note that Store publication is a future step (link to future R10 or
  similar). Screenshots are optional for this sprint.
- AC-R8-7-4: `extension/manifest.json` version is bumped to match the Synapse release
  version (`"version": "0.8.0"`). The extension `README.md` (create if absent) names
  the MV3 permissions and explains the host_permissions configuration for non-default
  ports.

**Sequencing note:** no shared backend files with R8-1 through R8-5 (beyond the
potential `/clip` endpoint check in AC-R8-7-1d, which is a read-or-thin-create).
Can be developed in parallel with all other items. devops-engineer owns CI workflow;
frontend-engineer owns the extension JS audit and doc.

---

## 3. Explicit sequencing order (same-file conflicts)

Items that share `backend/app/ingest/extract.py` MUST be sequenced strictly:

1. **R8-1** (adds pluggable seam + `_extract_pdf_via_marker`) → merge to sprint/v0.8
2. **R8-2** (adds image caption dispatch, reads post-R8-1 file) → merge
3. **R8-3** (adds AV transcription dispatch, reads post-R8-2 file) → merge

Items that share `backend/app/main.py` (or the new `export.py` router):

1. **R8-4** (adds export router, possibly extracts `export.py`) → merge
2. **R8-5 backend** (modifies `GET /search` in main.py or retrieval.py) → merge after R8-4

Items with no shared files (can be parallelized freely across all tracks):
- R8-5 frontend (facet UI) — starts in parallel with R8-4, merges after R8-5 backend is live
- R8-6 (pure frontend, no backend files)
- R8-7 (extension/ + CI workflow; thin /clip endpoint check only)

**Wave plan (suggested 2-week schedule):**

Wave 1 (days 1–4): R8-1 (backend-engineer + devops-engineer in parallel on
backend/tools sub-tasks). No other extract.py work starts.

Wave 2 (days 3–7, overlaps wave 1 tail): R8-4 (backend-engineer, once R8-1 is merged
or in final review). R8-6 (frontend-engineer). R8-7 (devops-engineer + frontend-engineer,
can start immediately — no extract.py dependency).

Wave 3 (days 6–10): R8-2 (ai-agent-engineer + backend-engineer, after R8-1 merged).
R8-5 backend (backend-engineer, after R8-4 merged). R8-5 frontend (frontend-engineer,
in parallel with backend, merges last).

Wave 4 (days 9–12): R8-3 (backend-engineer + devops-engineer, after R8-2 merged).
Final integration, test suite green pass, docs gate.

Wave 5 (days 12–14): QA-test-engineer full pass. Tech-writer docs gate. Architect
review. PM exit-criteria sign-off. Human checkpoint. Tag v0.8.0.

---

## 4. Out of scope for v0.8

Everything not listed in §2 above is explicitly out of scope. The following items are
deferred and must NOT be built during this sprint:

| Deferred item | Target release | Reason |
|---|---|---|
| Chrome Web Store publication | post-v1.0 | Requires Google developer account + review; store-specific scope |
| Vault restore/import endpoint (POST /import) | v0.9 or later | R8-4 covers export only; import is a separate design |
| R9-1: Cost dashboard | v0.9 | Observability sprint |
| R9-2: Metrics/health endpoint | v0.9 | Observability sprint |
| R9-3: purpose.md suggestions | v0.9 | AI+BE; trust sprint |
| R9-4: schema.md co-evolution | v0.9 | L effort; separate sprint |
| R9-5: Graph drill-down | v0.9 | Graph polish sprint |
| R9-6: Playwright E2E suite | v0.9 | QA sprint |
| R10-1: Authentication layer | v1.0 | Structural; design ADR first |
| R10-2: Multi-vault UI | v1.0 | Depends on R10-1 |
| R10-3: Code signing | v1.0 | Requires Apple/Windows certs |
| R10-4: Desktop auto-update signing path | v1.0 | Requires R10-3 |
| R10-5: Mobile/PWA polish | v1.0 | Polish sprint |
| R10-6: MkDocs docs site | v1.0 | Optional docs sprint |
| Any MinerU integration | never (replaced by R8-1) | Marker is the proven in-repo engine |
| Any feature not assigned a Feature ID in CLAUDE.md §4 | never without new ID | Anti-scope-creep invariant |

**Never list (invariants I1–I9):** full-rescan, main-thread force layout, per-token DOM
mutation, WYSIWYG/ProseMirror, hardcoded provider or model ID, unbounded loops (all
loops in R8-2/R8-3 are capped), skipping D-artifacts, Tavily/alt-search, reimplementing
local embeddings. These are permanent blocks regardless of sprint.

---

## 5. Exit criteria for v0.8 release (EC-M8)

All 4 sign-offs required before tagging `v0.8.0`:
QA-test-engineer + Solution-architect + Tech-writer + Product-manager.

| ID | Criterion |
|---|---|
| EC-M8-1 | All 7 committed items (R8-1..R8-7) have all ACs green in pytest + vitest. The whisper real-transcription test (AC-R8-3-3 note) is allowed to be marked `skipif` in CI; the health endpoint test must be green. |
| EC-M8-2 | `ruff check` + `black --check` + mypy strict pass tree-wide (no new violations). ESLint + prettier clean. TypeScript strict passes. The static guard in `extract.py` docstring remains accurate after all R8-1/R8-2/R8-3 changes. |
| EC-M8-3 | ER diagram zero-drift: `make er` output matches live schema (new `image_captions` table reflected). `docs/api/openapi.json` regenerated and current (new `GET /export`, `GET /export/data.json`, updated `GET /search` params all present). |
| EC-M8-4 | `docs/DEPLOY.md` updated with "Optional: Marker PDF extractor" and "Optional: Whisper transcription" sections (AC-R8-1-4, AC-R8-3-5). |
| EC-M8-5 | `docs/USER.md` updated with "Backup & Restore" section (AC-R8-4-3) and "Chrome Web Clipper" section (AC-R8-7-3). Tech-writer sign-off on both. |
| EC-M8-6 | ADR-0050 written (pluggable extractor seam, R8-1 decision). ADR index updated. If the Whisper seam warrants a separate ADR (architect's call), it is also written. |
| EC-M8-7 | `docs/sequences/` updated with an addendum on the AV dispatch path (AC-R8-3-5). |
| EC-M8-8 | `vault/wiki/` remains a valid Obsidian vault after all v0.8 ops (I5/K7). Manual spot-check by owner. |
| EC-M8-9 | CI artifact `synapse-clipper-{version}.zip` is produced by the GitHub Actions workflow and downloadable from the v0.8.0 release page (AC-R8-7-2). |
| EC-M8-10 | GitHub release `v0.8.0` created with: macOS `.dmg`, Windows `.msi`, Linux `.AppImage` desktop artifacts (carried forward from v0.7 build pipeline) plus the clipper zip. |
| EC-M8-HCP | Human checkpoint: Emanuele verifies in a live session: (a) a PDF is ingested via the Marker path (if the host service is running) and falls back correctly when stopped; (b) `GET /export` returns a valid ZIP; (c) search type filter returns only matching pages; (d) citation click-through works in the wiki preview panel. |

---

## 6. Velocity note

v0.7 carried 14 items (S/M effort). v0.8 carries 7 items, but three of them (R8-1,
R8-2, R8-3) are architecturally chained on the same file (`extract.py`) and must be
sequenced; this is the primary critical-path risk. The wave plan above mitigates by
front-loading R8-1 and parallelizing the non-chained items (R8-4 through R8-7) across
tracks.

R8-2 and R8-3 are both rated L effort. If the sprint runs over, the PM de-scope order
is: R8-3 AV transcription (opt-in, placeholder already exists — deferring has no user
regression) > R8-2 vision captions cap reduction (ship with VISION_MAX_IMAGES_PER_RUN=5
instead of 10 if needed) > R8-7 CI workflow (manual zip is an acceptable temporary
substitute). R8-1 (Marker seam), R8-4 (export), R8-5 (search filters), and R8-6
(citation wiring) are committed and must not be cut.

The `/clip` endpoint gap check in R8-7 AC-R8-7-1d has a risk: if the endpoint does not
exist, backend-engineer must create it. This is a thin wrapper (estimate: 1–2 h) but
must be factored into Wave 2 planning and not discovered at merge time.
