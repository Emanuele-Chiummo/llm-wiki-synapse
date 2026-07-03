# Sprint v1.1 — PM Scope Lock

> Milestone: M11 — "Convert & Configure"
> Author: product-manager
> Date: 2026-07-03
> Branch: sprint/v1.1 (cut from sprint/v1.0 after v1.0.0 tag)
> Prerequisite: M10 exit criteria met (EC-M10-1..EC-M10-HCP confirmed by Emanuele).
> Sprint duration: 2–3 weeks

---

## 0. Engineer ground rules (READ BEFORE TOUCHING ANY FILE)

**Rule 1 — No destructive git operations.**
No git restore, git checkout, git stash, or any command that discards working-tree
changes. Other agents on the same branch may have uncommitted edits that are
legitimate in-progress work. If you find changes in a file you need to edit, read them
first and integrate. Do NOT discard. Escalate to orchestrator if you cannot determine
ownership of an uncommitted change.

**Rule 2 — QA gate runs ci.yml's EXACT commands.**
The QA-test-engineer MUST run the following commands verbatim (matching ci.yml jobs)
before signing off on any item. No proxy commands, no shortcuts:

```bash
# Backend lint + type check (ci.yml jobs: lint, typecheck)
cd backend && ruff check app tests
cd backend && black --check app tests
cd backend && mypy app

# Frontend (ci.yml job: frontend)
cd frontend && npx tsc --noEmit
cd frontend && npm run lint
cd frontend && npm run test

# Docs gate — ER + OpenAPI drift check (ci.yml job: docs)
cd backend && python scripts/generate_er.py
cd backend && python scripts/generate_openapi.py
git diff --exit-code docs/er/schema.mmd
git diff --exit-code docs/api/openapi.json

# Mermaid validation loop (ci.yml job: docs — validate Mermaid diagrams step)
for f in docs/architecture/*.mmd docs/er/*.mmd docs/sequences/*.mmd; do
  [ -f "$f" ] || continue
  mmdc -p /tmp/puppeteer.json -i "$f" -o /tmp/mmdc-check.svg || exit 1
done
```

All six command groups must exit 0 before any item's ACs are marked green.

---

## 1. Sprint Goal

Make Synapse genuinely self-configuring for everyday use: let the user drive Marker PDF
conversion from the UI with automatic ingest, expose all runtime-tunable settings through
a clean Settings surface (no .env editing for day-to-day operation), fix a visible logo
duplication, and sweep bounded bugfixes that degrade UX. The result is a product where a
non-technical user can onboard a PDF vault and tune Synapse behaviour without touching
docker-compose.yml.

---

## 2. Scope decision record

### R11-1 — Marker conversion from UI: IN SCOPE (L)

The existing `PDF_EXTRACTOR` env var selects the extractor at startup. The Marker
microservice (`tools/marker-converter/service.py`, default `http://host.docker.internal:8555`)
is already called by `backend/app/ingest/extract.py`. What is missing is:
(a) a UI entry point to send one or more PDF files directly through Marker, bypassing the
watcher's normal ingest path, and
(b) automatic ingest trigger after successful conversion.

**PM decision:** Extend the existing upload flow (`backend/app/upload.py` +
`frontend/src/components/ingest/UploadZone.tsx` or equivalent) to offer a
"Convert with Marker" action. The backend adds a `POST /ingest/convert-marker` endpoint
(or an `extractor=marker` query param on the existing upload endpoint — backend-engineer
chooses; document the choice in a PR comment). After successful Marker conversion and
`.extracted.md` write, the watcher picks up the file under I1 (incremental, no full
rescan). The Marker microservice is external and may be down: the UI must degrade
gracefully (show an error, do NOT silently fall back — the user explicitly chose Marker).
The silent fallback in `extract.py` is the CLI/API path; the explicit UI action is a
hard fail-with-message.

**What this is NOT:** a full file manager or batch queue. Scope is single-file or
multi-select (≤10 files per submission, bounded by MAX_UPLOAD_BYTES each) with progress
feedback. No persistent queue table. No background retry.

### R11-2 — Settings redesign (runtime config migration): IN SCOPE (L)

**PM decision on migration list (8 settings — see §3 R11-2 for rationale per item):**

MIGRATED TO UI (user-facing, runtime-tunable, survives restart):

| Setting | Config key | UI label (EN / IT) | Default shown |
|---------|-----------|-------------------|---------------|
| S1 | `PDF_EXTRACTOR` | "PDF extractor / Estrattore PDF" | pypdf |
| S2 | `MARKER_SERVICE_URL` | "Marker service URL / URL servizio Marker" | http://host.docker.internal:8555 |
| S3 | `MARKER_TIMEOUT_SECONDS` | "Marker timeout (s) / Timeout Marker (s)" | 120 |
| S4 | `COST_ALERT_THRESHOLD_USD` | "Monthly cost alert (USD) / Avviso costo mensile (USD)" | 5.00 |
| S5 | `EMBEDDINGS_ENABLED` | "Vector embeddings / Embedding vettoriale" toggle | on |
| S6 | `EMBEDDING_FORMAT` | "Embedding format / Formato embedding" | ollama |
| S7 | `OVERVIEW_LANGUAGE` | "Overview language / Lingua panoramica" | (auto) |
| S8 | `WIKILINK_ENRICH_ENABLED` | "Auto wikilink enrichment / Arricchimento wikilink" toggle | on |

EXCLUDED (infrastructure — env-only, never in UI):

| Setting | Reason for exclusion |
|---------|---------------------|
| `DATABASE_URL` | Infra secret; changing it at runtime would orphan all data |
| `QDRANT_URL` | Infra; changing while service is live corrupts the collection pointer |
| `EMBEDDING_URL` | Infra; tied to the running bge-m3 instance |
| `EMBEDDING_DIM` | Infra; changing requires re-embed of entire vault |
| `VAULT_PATH` | Infra; changing while watcher is running breaks the incremental index (I1) |
| `SYNAPSE_AUTH_TOKEN` | Security; has dedicated Settings > Security section (shipped v1.0) |
| `CORS_ALLOW_ORIGINS` | Infra; network topology decision |
| `SEARXNG_URL` | Infra; tied to the running SearXNG instance |
| `CLIP_TOKEN` | Security secret; env-only (ADR-0038 §2.1) |
| `MCP_AUTH_TOKEN` | Security secret; already handled via PUT /mcp/auth (ADR-0033) |
| `MCP_TRUSTED_PROXIES` | Infra; network topology |
| `MAX_UPLOAD_BYTES` | Infra; storage constraint |

**Persistence model:** a new `app_config` table (single row, key/value store) holds the
UI-set overrides. On startup, `config.py` reads from env as today; a thin config-override
layer in `backend/app/config_overrides.py` reads the DB row (if present) and merges over
the env defaults. Env remains the deploy-time default; UI persists a named override row per
key. This pattern mirrors the existing `clip_config` / GET-PUT pattern in `main.py`.

No migration changes the existing env-var contract. Existing deployments that rely solely
on env vars are unchanged.

ADR required: ADR-0053 (`docs/adr/ADR-0053-ui-config-overrides.md`) to document the
override model, table design, and the explicit list of MIGRATED vs EXCLUDED settings.
Must be accepted by solution-architect BEFORE any backend code is written for R11-2.

### R11-3 — Logo deduplication: IN SCOPE (S)

The Synapse logo currently appears in both `Header.tsx` (top bar branding) and
`NavRail.tsx` (top of the left icon rail). These are two distinct components that both
render the `synapse-logo.svg` asset. The user sees the logo twice in the same viewport.

**PM decision:** Remove the logo from the NavRail. The NavRail's job is navigation, not
branding. The Header retains the logo (it is the primary branding surface). The top slot
in the NavRail currently used for the logo is either removed or replaced by the first
navigation item (Chat) without the logo duplication. The exact layout is
frontend-engineer's call; the invariant is: logo appears exactly once per viewport.

No new icon library, no nav restructure, no item reordering. Scope is one file edit
(NavRail.tsx) and its test (NavRail.test.tsx).

### R11-4 — Bugfixes + performance (bounded sweep): IN SCOPE (S × 3)

PM-selected three bugs from the known backlog. The cap is three items. No additional
bugs may be added to this sprint without PM escalation.

**Selected bugs:**

| Sub-ID | Component | Description |
|--------|-----------|-------------|
| BUG-1 | `MarkdownView.tsx` / `renderMarkdown` guard (G3) | `renderMarkdown` is called without a null/empty-string guard, producing spurious DOMParser errors visible in the browser console on empty chat messages or empty preview panes. Fix: add a guard (`if (!markdown) return ""`) before the DOMParser call. |
| BUG-2 | Ingest polling deduplication | `IngestView.tsx` (or the polling hook it uses) starts a `setInterval` or `useEffect` poll on mount and does not cancel the previous interval on re-mount (e.g., when the user switches sections and returns). This produces multiple overlapping polls for the same data. Fix: ensure the polling effect returns a cleanup function that clears the interval/timeout. |
| BUG-3 | TanStack Virtual initializer zero-height recovery | The virtualized list (tree or message history) initializes with `estimateSize` returning 0, causing the virtual container to render at 0px height and show nothing until a resize event fires. Fix: provide a non-zero `estimateSize` default (e.g., `() => 40`) or add a `ResizeObserver` to the scroll container that forces a remeasure on mount. |

---

## 3. Committed Scope

Exactly the following items. Anything else is out of scope and requires explicit
PM re-approval before any token is spent on it.

---

### R11-1 — Marker conversion from UI

| Field | Value |
|---|---|
| Feature ID | F12 (multi-format ingest), F16 (UI config) |
| Owner | backend-engineer (endpoint) + frontend-engineer (UploadZone extension) |
| Effort | L |
| ADR reference | ADR-0051 (existing, Marker seam) — no new ADR required; extend existing |
| Invariant check | I1 (incremental — watcher picks up converted file, no rescan), I7 (bounded: ≤10 files per submission, MAX_UPLOAD_BYTES enforced per file), I6 (provider not touched by this flow), I5 (converted .extracted.md has valid YAML frontmatter) |

**Design decisions (PM-locked):**

- The backend adds a `POST /ingest/convert-marker` endpoint. It accepts multipart
  `files[]` (≤10 files, each ≤ MAX_UPLOAD_BYTES). It calls `_extract_pdf_via_marker()`
  from `extract.py` for each file. On success it writes the `.extracted.md` to
  `vault/raw/sources/` (the watcher directory). The watcher then picks it up and runs
  the normal ingest loop (I1 — no special path needed).
- On Marker microservice error (any HTTP error, timeout, or connection refused): the
  endpoint returns HTTP 502 with body `{"error": "marker_unavailable", "detail": "..."}`.
  The frontend shows the error inline. NO silent fallback to pypdf on this path (user
  explicitly chose Marker — degrade with message, not silently).
- The Marker health check endpoint (`GET {MARKER_SERVICE_URL}/health`) is polled by a
  new `GET /ingest/marker-health` backend proxy endpoint. The frontend shows a
  "Marker offline" badge when this returns unhealthy. The UI disables the "Convert with
  Marker" action when Marker is offline, with a tooltip explaining the state.
- Multi-file progress: the frontend shows a per-file progress indicator (pending /
  converting / done / failed) using component-local state, not Zustand global state
  (these are ephemeral UI states, not persisted data).

**Acceptance criteria:**

- AC-R11-1-1: `POST /ingest/convert-marker` exists, accepts `multipart/form-data` with
  field `files[]`. Rejects > 10 files (HTTP 400). Rejects any file > `MAX_UPLOAD_BYTES`
  (HTTP 413). Accepts only `.pdf` extension (HTTP 415 for others). A pytest asserts all
  three rejection paths.

- AC-R11-1-2: On successful Marker conversion, the endpoint writes
  `{original_stem}.extracted.md` to `vault/raw/sources/` with valid YAML frontmatter
  (`type: source`, `title`, `sources: [{path}]`). A pytest mocks the Marker HTTP call
  and asserts the file is written with correct frontmatter (I5).

- AC-R11-1-3: When the Marker microservice returns any non-2xx response or times out
  (mocked in pytest), `POST /ingest/convert-marker` returns HTTP 502 with
  `{"error": "marker_unavailable", "detail": "<message>"}`. The endpoint does NOT fall
  back to pypdf. A pytest asserts the 502 body and that no `.extracted.md` is written.

- AC-R11-1-4: `GET /ingest/marker-health` proxies a `GET {MARKER_SERVICE_URL}/health`
  call. Returns `{"status": "ok"}` (200) when Marker responds 200, and
  `{"status": "offline", "detail": "..."}` (503) when unreachable or non-200. A pytest
  mocks both cases.

- AC-R11-1-5: The frontend UploadZone (or a new sibling component in
  `frontend/src/components/ingest/`) gains a "Convert with Marker" action. The action is
  disabled (with a `title` tooltip "Marker offline") when `GET /ingest/marker-health`
  returns 503. A Vitest asserts the button is disabled when the health mock returns 503
  and enabled when it returns 200.

- AC-R11-1-6: When the user selects files and clicks "Convert with Marker", the UI shows
  a per-file status row: pending (spinner), converting (progress), done (check), failed
  (X + error message). Failed files display the `detail` string from the 502 response.
  No global Zustand action is dispatched for these ephemeral states (I3 — no heavy
  re-render on per-file progress).

- AC-R11-1-7: The file-count limit (10) and the graceful-degradation behaviour (Marker
  offline → button disabled) are documented in `docs/USER.md` under the "Ingesting PDFs"
  section. Tech-writer sign-off.

- AC-R11-1-8: `ruff check`, `black --check`, `mypy` all pass for the new backend
  endpoint. `npx tsc --noEmit` and `npm run lint` pass for the frontend component.

---

### R11-2 — Settings redesign (runtime config migration)

| Field | Value |
|---|---|
| Feature ID | F16 (settings, i18n), F17 (provider config — S5/S6 touch embedding, adjacent to F17) |
| Owner | backend-engineer (config-overrides layer + app_config table) + frontend-engineer (SettingsPanel sections) |
| Effort | L |
| ADR required | ADR-0053 MUST be accepted by solution-architect BEFORE any backend code is written |
| Invariant check | I6 (embedding format not hardcoded; S6 must route through the existing embedding adapter, not hardcode a path), I7 (no unbounded reads from app_config — single-row read at startup + on PUT), I8 (openapi.json regenerated after new endpoints) |

**Design decisions (PM-locked):**

- New table `app_config` in Postgres: `(key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMPTZ)`.
  One row per overridden setting. Migration via Alembic (required: ER drift check).
- New module `backend/app/config_overrides.py`: on startup (lifespan), loads all rows from
  `app_config` and caches them in memory. Exposes `get_override(key) -> str | None`.
  `settings` (pydantic-settings) is read first as baseline; override values are merged on
  top for the 8 migrated settings only.
- New endpoints: `GET /config/app` (returns all 8 migrated settings with current effective
  value + source: "env" or "override"), `PUT /config/app/{key}` (sets or clears an override
  for one of the 8 allowed keys — returns 400 for non-allowed keys). Auth-gated (uses the
  existing `verify_token` dependency from `auth.py`).
- SettingsPanel.tsx gains a new nav section "Extraction & Config" (or merged into the
  existing "General" section — frontend-engineer's call; document in PR). This section
  renders the 8 settings as described in the migration table in §2 R11-2, with appropriate
  input types: `<select>` for S1 (pypdf / marker), URL `<input>` for S2, number `<input>`
  for S3 and S4, `<toggle>` for S5 and S8, `<select>` for S6 (ollama / openai), and a
  free text (or select) for S7 with an "(auto)" sentinel.
- Each setting field shows the current effective value. A "(default)" badge appears when
  the value matches the env-sourced default. An "Unsaved changes" indicator and a "Save"
  button (or per-field autosave — frontend-engineer's call) trigger `PUT /config/app/{key}`.
- Env vars remain the deploy-time default. The UI does NOT expose them for editing — it
  only writes named overrides. To fully reset a setting to its env default, the user
  clicks a "Reset to default" action that calls `PUT /config/app/{key}` with `value: null`
  (or a dedicated `DELETE /config/app/{key}` endpoint — backend-engineer's call;
  document in PR).

**Acceptance criteria:**

- AC-R11-2-0: ADR-0053 committed to `docs/adr/ADR-0053-ui-config-overrides.md` and
  accepted by solution-architect BEFORE any backend code is written. ADR covers: the
  override model, the exact 8 migrated keys (matching the table in §2 R11-2), the
  `app_config` table design, the startup merge order (env → DB override), the explicit
  excluded list and rationale, and the API contract for GET/PUT.

- AC-R11-2-1: Alembic migration creates `app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`. `make er` output updated;
  `docs/er/schema.mmd` zero-drift. A pytest confirms the table exists and
  supports upsert-by-key.

- AC-R11-2-2: `GET /config/app` returns JSON `{"settings": [{key, value, source}]}` for
  all 8 migrated keys. `source` is `"override"` when a row exists in `app_config`,
  `"env"` otherwise. `value` is always the effective current value (override wins).
  A pytest asserts: (a) with no overrides, all sources are "env"; (b) after a PUT, the
  affected key's source becomes "override" and value changes.

- AC-R11-2-3: `PUT /config/app/{key}` with a valid key and valid value upserts the row
  and returns 204. `PUT /config/app/nonexistent_key` returns 400 with
  `{"error": "invalid_key", "allowed": [...]}`. A pytest asserts both paths.
  The endpoint respects `verify_token` (401 if auth is enabled and token is wrong).

- AC-R11-2-4: `PUT /config/app/{key}` with `value: null` (or a `DELETE /config/app/{key}`
  endpoint — whichever the backend-engineer implements) removes the override row, causing
  the setting to revert to the env default. A pytest asserts the revert behaviour.

- AC-R11-2-5: `backend/app/config_overrides.py` module: `load_overrides(db)` is called
  once during lifespan startup; `get_effective(key: str, env_default: str) -> str` returns
  the override value if present, else the env default. A pytest asserts the merge logic
  with mocked DB rows.

- AC-R11-2-6: The 8 settings are surfaced in SettingsPanel.tsx under a clearly labelled
  section. Each field displays the current effective value. Each has a "Reset to default"
  action. A Vitest asserts: (a) the section renders with mocked `GET /config/app` data;
  (b) editing S1 (`pdf_extractor`) from "pypdf" to "marker" and clicking Save triggers
  `PUT /config/app/pdf_extractor` with `value: "marker"`.

- AC-R11-2-7: All 8 settings have EN and IT i18n keys (label + description +
  validation hint for number fields). A Vitest asserts all new keys resolve to non-empty
  strings in both locales. Labels and descriptions use plain language, not env-var names.
  Examples: "PDF extractor" not "PDF_EXTRACTOR"; "marker" and "pypdf" are the two choices
  for S1 (the backend enum values, displayed as-is since they are already readable).

- AC-R11-2-8: `docs/api/openapi.json` regenerated. `GET /config/app` and
  `PUT /config/app/{key}` (and `DELETE /config/app/{key}` if chosen) are declared with
  correct request/response schemas and the `BearerAuth` security reference.

- AC-R11-2-9: `ruff check`, `black --check`, `mypy` pass for `config_overrides.py` and
  all new backend modules. No `Any` types. Strict mypy.

- AC-R11-2-10: `docs/USER.md` gains a "Runtime Settings" section explaining that these
  8 settings can be changed without restarting Docker, and what each setting controls.
  Tech-writer sign-off.

---

### R11-3 — Logo deduplication

| Field | Value |
|---|---|
| Feature ID | F1 (UI shell) |
| Owner | frontend-engineer |
| Effort | S |
| Invariant check | No invariant risk. Single-file edit. |

**Design decision (PM-locked):**
The logo (`synapse-logo.svg`) is removed from `NavRail.tsx`. The Header retains
sole ownership of the branding logo. After the change, exactly one `<img src={logoUrl}>`
(or equivalent SVG embed) is present in the rendered DOM at any viewport size. The top
slot in the NavRail previously occupied by the logo is either collapsed (reducing the
rail's top padding to the first nav item) or replaced with a small top padding only.

**Acceptance criteria:**

- AC-R11-3-1: `NavRail.tsx` no longer imports `logoUrl` or renders the Synapse logo.
  A Vitest asserts `queryByRole("img", {name: /synapse/i})` returns null within the
  NavRail render.

- AC-R11-3-2: `Header.tsx` continues to render the logo. A Vitest (or the existing
  `Header.test.tsx`) asserts the logo `<img>` is present within the Header render.

- AC-R11-3-3: At 1280×800 viewport width (standard desktop), there is exactly one
  instance of the Synapse logo visible. Verified by a Playwright screenshot
  `docs/screens/layout-no-logo-dupe.png`. D5 artifact.

- AC-R11-3-4: The NavRail's visual appearance after the logo removal is intentional
  and documented in a PR comment (e.g., "top padding increased to 8px to replace the
  removed logo slot"). No visual regressions in other NavRail items.

---

### R11-4 — Bugfixes (bounded sweep, 3 items)

| Field | Value |
|---|---|
| Feature ID | F1 (UI shell), F3 (I3 — renderMarkdown), F4/F5 (virtualizer) |
| Owner | frontend-engineer |
| Effort | S × 3 |
| Invariant check | I3 (renderMarkdown guard directly enforces no heavy work per token/empty-string), I4 (virtualizer fix enforces list virtualization works on mount) |

**Acceptance criteria:**

- AC-R11-4-BUG1 (renderMarkdown guard): `MarkdownView.tsx` (or wherever `renderMarkdown`
  is defined) has a guard `if (!markdown || markdown.trim() === "") return ""` before the
  DOMParser invocation. A Vitest asserts: (a) calling `renderMarkdown("")` returns `""`
  without throwing; (b) calling `renderMarkdown(null as unknown as string)` returns `""`
  without throwing. Zero browser console errors on empty preview pane (verified by
  Playwright test: open a new conversation with no messages, assert no console errors on
  the preview pane).

- AC-R11-4-BUG2 (polling dedup): The polling hook or `useEffect` in `IngestView.tsx`
  (or the hook it delegates to) has a cleanup function that clears the interval/timeout.
  A Vitest asserts: mount the component, unmount it, remount it — the number of active
  polling intervals never exceeds 1 at any point. Verified by spying on `setInterval`
  and `clearInterval` call counts.

- AC-R11-4-BUG3 (virtualizer zero-height): The TanStack Virtual `estimateSize` function
  in the affected list component (tree, message history, or both — fix whichever
  manifests the bug) returns a non-zero value (≥ 32px). Additionally, the scroll
  container has a `ResizeObserver` or a `useLayoutEffect` that calls
  `virtualizer.measure()` on mount (or after the container first gains a non-zero height).
  A Vitest asserts the virtualizer's `getTotalSize()` is > 0 on initial render with a
  non-empty item list and a mocked scroll container height of 400px.

---

## 4. Explicit sequencing and file conflict map

### Wave 1 — ADR + backend foundation (days 1–4)

**Day 1 (PM-mandated blocker for R11-2):** solution-architect writes and commits ADR-0053
(`docs/adr/ADR-0053-ui-config-overrides.md`). No backend code for R11-2 written until
ADR-0053 is accepted. R11-3 (logo dedup — no ADR needed) and R11-4 (bugfixes) can start
immediately in parallel.

**Days 1–4:** backend-engineer implements R11-1 (new endpoint + health proxy) and
begins R11-2 Alembic migration + `config_overrides.py` after ADR-0053 accepted.

### Wave 2 — Frontend + UI integration (days 3–10)

**Starts as soon as the backend endpoints for R11-1 and R11-2 are merged.**
frontend-engineer implements the UploadZone Marker extension (R11-1) and the
SettingsPanel config section (R11-2). R11-3 and R11-4 can be merged at any point from
day 1 onward (no shared files with R11-1/R11-2 backend work).

### Wave 3 — QA, docs, sign-offs (days 8–15)

QA full pass: ci.yml exact commands, all E2E specs, all new unit tests.
Tech-writer: USER.md updates (R11-1-7, R11-2-10). Architect review: ADR-0053,
new endpoint design, `config_overrides.py` merge logic. PM exit-criteria check.
Tag v1.1.0.

### Critical path

```
ADR-0053 accepted
    └─► R11-2 backend (app_config table + config_overrides + GET/PUT endpoints)
             └─► R11-2 frontend (SettingsPanel config section)
                      └─► QA full pass
                               └─► Docs gate (tech-writer)
                                        └─► Architect review
                                                 └─► PM sign-off
                                                          └─► tag v1.1.0

R11-1 backend (convert-marker endpoint + marker-health proxy)
    └─► R11-1 frontend (UploadZone Marker action)
             (parallel to R11-2 — no shared files)

R11-3 + R11-4 (day 1 onwards — no dependencies)
```

### Same-file conflict registry

| File | Items touching it | Merge order |
|------|-------------------|-------------|
| `backend/app/main.py` | R11-1 (new route registration), R11-2 (new route registration) | R11-1 backend first, then R11-2 backend; no other item touches this file until both are merged |
| `backend/app/ingest/extract.py` | R11-1 (calls existing `_extract_pdf_via_marker`) | Read-only ref; R11-1 adds NO new logic to extract.py — it calls the existing function via the new endpoint |
| `frontend/src/components/ingest/UploadZone.tsx` | R11-1 (Marker action) | R11-1 only |
| `frontend/src/components/settings/SettingsPanel.tsx` | R11-2 (new config section) | R11-2 only |
| `frontend/src/components/nav/NavRail.tsx` | R11-3 (logo removal) | R11-3 only — no conflict with R11-1/R11-2 |
| `frontend/src/components/chat/MarkdownView.tsx` | R11-4-BUG1 | R11-4 only |
| `frontend/src/components/ingest/IngestView.tsx` | R11-4-BUG2 | R11-4 only |
| `docs/er/schema.mmd` | R11-2 (new app_config table) | Regenerated after R11-2 Alembic migration |
| `docs/api/openapi.json` | R11-1 (new endpoints), R11-2 (new endpoints) | Regenerated once both backend PRs are merged |
| `docs/USER.md` | R11-1 (Ingesting PDFs section), R11-2 (Runtime Settings section) | tech-writer coordinates; two separate sections, no conflict |

---

## 5. Wave plan (suggested 2–3 week schedule)

**Wave 1 (days 1–5):**
- Day 1: solution-architect writes ADR-0053. frontend-engineer starts R11-3 (logo dedup)
  and R11-4 (three bugfixes) — no dependencies, no ADR needed.
- Days 1–3: backend-engineer implements R11-1 (`POST /ingest/convert-marker`,
  `GET /ingest/marker-health`, pytests).
- Days 2–5: backend-engineer implements R11-2 Alembic migration + `config_overrides.py`
  + `GET /config/app` + `PUT /config/app/{key}` after ADR-0053 accepted.

**Wave 2 (days 4–10):**
- Days 4–7: frontend-engineer implements R11-1 UI (UploadZone Marker action, per-file
  progress, health badge). R11-3 + R11-4 PRs merged (small, no deps).
- Days 6–10: frontend-engineer implements R11-2 UI (SettingsPanel config section, 8
  settings, i18n keys).

**Wave 3 (days 10–15):**
- qa-test-engineer full pass: ci.yml exact commands, all new unit tests, E2E regressions.
- tech-writer: USER.md updates for R11-1 and R11-2.
- solution-architect: review ADR-0053 consistency with implementation, openapi.json audit.
- PM: exit-criteria check (§6).
- Day 15 target: tag v1.1.0, GitHub release.

---

## 6. Out of scope for v1.1

Everything not listed in §3 is explicitly out of scope. The following items MUST NOT
be built during this sprint without explicit PM escalation and approval:

| Deferred item | Reason |
|---|---|
| Full file manager / upload history queue | No persistent queue table in scope; ephemeral progress only |
| Batch Marker conversion > 10 files | Beyond the bounded cap; add a persistent queue table first (post-1.1) |
| Background Marker retry on failure | Requires a persistent task queue (post-1.1) |
| WHISPER_SERVICE_URL or AV_TRANSCRIPTION_ENABLED in UI | Not in the 8 migrated settings; defer to post-1.1 |
| SEARXNG_URL in UI | Infra-class setting; excluded by PM decision |
| CLIP_TOKEN / CLIP_ENABLED in Settings UI | Security secret; env-only |
| REVIEW_* bounds in UI | Operational tuning; too many params for a clean UX; defer |
| LINT_* bounds in UI | Operational tuning; defer |
| Multi-vault UI / vault switcher | Post-1.0 (no routing foundation) |
| OIDC / multi-user | Post-1.0 |
| New feature IDs not in CLAUDE.md §4 | Never without a new ID — anti-scope-creep invariant |
| NavRail reordering or restructure | R11-3 removes the logo only; no other rail changes |
| New MkDocs nav items | R10-6 completed the MkDocs structure; no expansion in this sprint |
| Code signing | Documented in DEPLOY.md (v1.0 R10-3); no action needed |

**Permanent invariant blocks (I1–I9 apply unconditionally):** full-rescan, main-thread
force layout, per-token DOM mutation, WYSIWYG/ProseMirror, hardcoded provider or model
ID, unbounded loops, skipping D-artifacts, Tavily, reimplementing local embeddings.

---

## 7. Exit criteria for v1.1 release (EC-M11)

All 4 sign-offs required before tagging `v1.1.0`:
QA-test-engineer + Solution-architect + Tech-writer + Product-manager.

| ID | Criterion |
|---|---|
| EC-M11-1 | All committed items have all ACs green: R11-1 (Marker UI + endpoint, all 8 ACs), R11-2 (Settings migration, all 10 ACs + ADR-0053 accepted), R11-3 (logo dedup, all 4 ACs), R11-4 (3 bugfixes, all 3 ACs). |
| EC-M11-2 | ci.yml exact commands all exit 0 tree-wide: backend ruff + black + mypy; frontend tsc + lint + test. QA-test-engineer runs verbatim and signs each off. |
| EC-M11-3 | ER diagram zero-drift: `make er` output matches live schema including the new `app_config` table. `docs/er/schema.mmd` committed and current. |
| EC-M11-4 | `docs/api/openapi.json` regenerated: `POST /ingest/convert-marker`, `GET /ingest/marker-health`, `GET /config/app`, `PUT /config/app/{key}` (and DELETE if chosen) all present with correct schemas and BearerAuth reference. |
| EC-M11-5 | Mermaid validation loop passes: all `.mmd` files in `docs/architecture/`, `docs/er/`, `docs/sequences/` render without error via `mmdc`. |
| EC-M11-6 | `mkdocs build --strict` exits 0 (R10-6 CI job continues to pass; no regressions from new doc edits). |
| EC-M11-7 | D5 screenshot `docs/screens/layout-no-logo-dupe.png` captured at 1280×800 (R11-3 AC-R11-3-3). All v1.0 screenshots remain valid (no regressions). |
| EC-M11-8 | `docs/USER.md` updated: "Ingesting PDFs" section (R11-1-7) and "Runtime Settings" section (R11-2-10) present and tech-writer approved. |
| EC-M11-9 | `docs/adr/ADR-0053-ui-config-overrides.md` committed and in Accepted status. Present in the ADR index. |
| EC-M11-10 | `vault/wiki/` remains a valid Obsidian vault (I5/K7). Manual spot-check by owner. |
| EC-M11-11 | Marker offline degradation verified: with Marker microservice stopped, the "Convert with Marker" button is disabled in the UI and shows the offline badge. No unhandled errors. Verified by QA-test-engineer (mocked backend or real offline Marker). |
| EC-M11-12 | Settings override persistence verified: changing S1 (PDF_EXTRACTOR) via UI to "marker", restarting the backend, confirms the setting is still "marker" (persisted in `app_config` table) and the effective value returned by `GET /config/app` has `source: "override"`. |
| EC-M11-13 | Env-var backward compatibility: a deployment with no `app_config` rows and all env vars set to their defaults behaves identically to v1.0.0 (no behaviour change). QA-test-engineer confirms by running the v1.0 E2E suite against a v1.1 build with an empty `app_config` table. |
| EC-M11-14 | GitHub release `v1.1.0` created with desktop artifacts (macOS `.dmg`, Windows `.msi`, Linux `.AppImage`). Release notes list all items in §3. |
| EC-M11-HCP | Human checkpoint: Emanuele verifies in a live session: (a) upload one PDF via "Convert with Marker" and confirm it appears in the wiki after ingest; (b) change PDF_EXTRACTOR setting from pypdf to marker via Settings UI, restart backend, confirm the setting is still "marker"; (c) stop the Marker service, confirm the "Convert with Marker" button shows "Marker offline" and is disabled; (d) confirm the logo appears exactly once (Header only); (e) open an empty chat — no console errors in the browser developer tools. |

---

## 8. De-scope order (if sprint runs over)

Cut in this order:

1. R11-4-BUG3 (virtualizer zero-height) — lowest user impact of the three bugs; the
   resize event workaround is acceptable as a stop-gap. Document as known issue.
2. R11-4-BUG2 (polling dedup) — minor performance issue, not a functional regression.
   Document as known issue.
3. R11-2 settings S7 (`OVERVIEW_LANGUAGE`) and S8 (`WIKILINK_ENRICH_ENABLED`) — these
   two settings are the lowest-frequency user actions; cut them from the first UI
   delivery and defer to a v1.1.1 patch. The backend key-allowlist must be updated
   to exclude them consistently.

R11-1 (Marker UI), R11-2 core (S1–S6), R11-3 (logo), and R11-4-BUG1 (renderMarkdown
guard) are committed and MUST NOT be cut.

---

## 9. Velocity note

v1.0 carried 9 items (R10-1 through R10-6 + QA-LO + UX) in 3–4 weeks with one XL item.
v1.1 carries 4 items (R11-1 L, R11-2 L, R11-3 S, R11-4 S×3) in 2–3 weeks. By commit
density this is a slightly lighter sprint than v1.0, appropriate because:
- R11-2 has an ADR gate on Day 1 (same pattern as R10-1); the gate is the primary risk.
- R11-1 and R11-2 both touch `backend/app/main.py` and require sequential merge; this
  is the main sequencing constraint.
- R11-3 and R11-4 are genuinely S-sized and have no dependencies; they should be merged
  within Wave 1 to clear the branch early.

The sprint is intentionally scoped slightly under the v1.0 pace. v1.1 is a "polish and
configure" sprint, not a feature sprint. Correctness and test coverage of the config
override layer (R11-2) are the primary quality concerns.

**Feature IDs touched this sprint:** F1, F12, F16, F17 (adjacent — S6 embedding format).
**Invariants with heightened priority:** I1 (watcher incremental pick-up after Marker
conversion), I7 (bounded upload: ≤10 files, MAX_UPLOAD_BYTES per file), I8 (ER + OpenAPI
drift check, new app_config table), I3 (renderMarkdown guard closes I3 gap in BUG-1).

---

## 10. AMENDMENT — Owner design decisions (Emanuele, 2026-07-03)

> These two decisions OVERRIDE the corresponding PM defaults above. Everything else in
> §1–§9 stands. Effort re-estimate: R11-1 stays L; R11-2 rises L → **XL** (adds an
> information-architecture redesign + a first-run wizard on top of the config migration).
> Sprint is now "polish, configure AND simplify". Wave plan below is revised accordingly.

### A1 — R11-1: dedicated "Convert" surface (overrides the UploadZone-extension decision)

The Marker conversion is NOT folded into the existing UploadZone. It gets its **own
first-class UI surface** ("Convert" / "Converti"), reachable from primary navigation, so
"convert PDFs from the interface" is an obvious, visible capability.

- New component `frontend/src/components/convert/ConvertPanel.tsx` (+ a NavRail/section
  entry). Drag-drop or file-pick for 1–10 PDFs, per-file status rows (pending → converting
  → done / failed), a visible "Marker offline" badge, and an explicit **"Convert & ingest"**
  primary action.
- After successful conversion the flow **auto-triggers ingest** (writes `.extracted.md`
  under `vault/raw/sources/`; the watcher picks it up under I1 — no new ingest path). The
  UI surfaces the resulting ingest so the user sees the new wiki page(s) appear.
- Backend contract from §3 R11-1 is unchanged (`POST /ingest/convert-marker`,
  `GET /ingest/marker-health`). Only the frontend home of the feature changes: a dedicated
  ConvertPanel instead of an UploadZone button. All R11-1 ACs still apply; AC-R11-1-5/6
  now target ConvertPanel.tsx (+ ConvertPanel.test.tsx) instead of UploadZone.
- Reuses the existing upload infrastructure (multipart, MAX_UPLOAD_BYTES) — no new
  persistent queue (still out of scope per §6).

### A2 — R11-2: Settings information-architecture redesign + first-run wizard

Beyond migrating the 8 env settings into the UI, the Settings panel is **reorganised and
made plain-language**, and a guided first-run setup is added. Rationale: today's panel is
14 sections / ~3350 lines and reads like an env-var dump; the owner wants it simple and
"parlante" (speaks in user terms, not variable names).

**A2.1 — Consolidated IA.** The 14 current sections collapse into a small set of clear
top-level groups (target ~5). Proposed grouping (final split is architect + frontend call,
recorded in ADR-0053 / a UI-IA note, but the target count and plain-language rule are
PM-and-owner locked):

| Group (EN / IT) | Folds in today's sections |
|---|---|
| **Getting started / Per iniziare** | first-run wizard entry, context window (general) |
| **AI & Models / AI e Modelli** | llmModels, embeddings, webSearch, apiMcp (provider/F17) |
| **Sources & PDF / Sorgenti e PDF** | sourceWatch, webClipper, **PDF extractor + Marker (S1–S3, new)** |
| **Output & Appearance / Output e Aspetto** | output, interface, scenarios |
| **Advanced / Avanzate** | costs, security, maintenance, about + the remaining migrated keys (S4–S8) |

- No section is deleted destructively: every existing control keeps a home; the change is
  grouping, labels, and inline help — not removal of functionality.
- Every field gets a **plain-language label + one-line help** (EN + IT). Env-var names
  never appear as the primary label (an "Advanced" affordance may reveal the underlying
  key for power users, optional).

**A2.2 — First-run wizard.** A short guided setup shown when the app has no saved config
(new + returning-without-config). Steps (bounded, skippable): (1) connect backend / verify
health, (2) choose inference provider + model (reuses existing provider config), (3) choose
PDF extractor (pypdf vs Marker + Marker URL, reuses R11-2 S1–S3), (4) done → land in the
app. The wizard only writes through the SAME `PUT /config/app/{key}` + provider-config
endpoints — no parallel persistence path. It is dismissible and re-openable from
"Getting started".

**New ACs (additive to §3 R11-2):**

- AC-R11-2-11 (IA): Settings renders ~5 top-level groups; a Vitest asserts the group
  labels are present and that every pre-existing control is still reachable (no control
  lost in the reorg). Snapshot/interaction test, not pixel.
- AC-R11-2-12 (plain language): a Vitest asserts no primary field label equals an env-var
  name (e.g. no visible label is exactly `PDF_EXTRACTOR`); labels/help resolve non-empty
  in EN and IT.
- AC-R11-2-13 (wizard): a Vitest asserts the wizard appears when `GET /config/app` +
  provider config indicate an unconfigured state, is skippable, is re-openable from
  "Getting started", and writes only via the sanctioned endpoints (spy on fetch).
- AC-R11-2-14 (D5): Playwright screenshots `docs/screens/settings-redesign.png` and
  `docs/screens/first-run-wizard.png` at 1280×800.

**Effort / risk note.** A2 makes R11-2 the sprint's XL item and its main risk. The config
migration (S1–S8) and the IA/wizard can ship in two frontend sub-waves: 2a = migration
into the *current* structure (unblocks Marker settings early), 2b = the IA reorg + wizard
on top. If the sprint runs over, §8 de-scope is amended: cut A2.2 (wizard) FIRST, then the
A2.1 reorg, before cutting any functional config migration — the migration (no more
env-editing) is the owner's hard requirement; the reorg/wizard are the polish layer.

### A3 — Revised wave plan (supersedes §5 for the two amended items)

- **Wave 1 (now):** architect writes ADR-0053 (config-override model + the Settings IA
  grouping + wizard-persistence contract). In parallel, R11-3 (logo dedup) and
  R11-4-BUG3 (virtualizer) start immediately — no deps. R11-4-BUG1 (renderMarkdown) and
  R11-4-BUG2 (ingest polling) are already in flight in separate owner-launched sessions;
  the orchestrator integrates their results rather than re-doing them.
- **Wave 2a:** backend R11-1 (convert-marker + marker-health) and R11-2 backend
  (app_config + config_overrides + GET/PUT). Frontend ConvertPanel (R11-1) + config
  migration into current Settings (R11-2 S1–S8).
- **Wave 2b:** Settings IA reorg (A2.1) + first-run wizard (A2.2).
- **Wave 3:** QA (ci.yml exact commands), tech-writer (USER.md), architect review, PM
  sign-off, D5 screenshots, tag v1.1.0 with desktop artifacts + auto-update latest.json.
