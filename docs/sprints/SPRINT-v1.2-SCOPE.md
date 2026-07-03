# Sprint v1.2 — PM Scope Lock

> Milestone: M12 — "Home & Insights"
> Author: product-manager
> Date: 2026-07-03
> Branch: sprint/v1.2 (to be cut from main after v1.1.0 tag)
> Prerequisite: M11 exit criteria met (EC-M11-1..EC-M11-HCP confirmed by Emanuele before this sprint starts).
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

Give the owner a glanceable home screen that answers "what is in my wiki and how is it
growing?" — page counts by type, community topology, AI spend, review queue depth, and
per-section (domain) breakdowns — while establishing the controlled vocabulary machinery
(domain tags) that will make the wiki self-organising across future sprints. Add a
release-channel mechanism so TrueNAS-hosted deployments learn about and can act on new
backend image versions without manual version tracking. Fix a residual ingest polling
regression if it was not resolved in v1.1.

The sprint has four committed items: R12-1 (Home dashboard), R12-2 (Domain vocabulary +
auto-tag), R12-3 (Server release channel), R12-4 (Ingest polling dedup bug).

---

## 2. Scope decision record

### R12-1 — Home dashboard: IN SCOPE (L)

**Context.** The NavRail's top slot is now free (logo removed in R11-3). The owner
wants a "home / landing" experience that gives an at-a-glance summary of vault health,
activity, and per-domain breakdowns. The existing `GET /costs/summary` aggregation
(costs.py), the `GET /graph` community summaries (main.py `GraphResponsePayload.communities`),
and `pages.updated_at` / `pages.page_type` / `pages.tags` columns provide everything
needed server-side.

**PM decisions (locked):**

- Two new read-only endpoints: `GET /stats/overview` and `GET /stats/sections`.
  These are aggregate-read-only (I1 — no vault mutation, no graph recompute).
- `GET /stats/overview` returns: pages_by_type (dict of page_type → count),
  total_links (edge count from the edges table), communities_count (from the cached
  graph snapshot or vault_state — whichever is cheapest; architect decides), review_queue_depth
  (open review_items count), lint_findings_open (open lint_findings count),
  monthly_ai_spend_usd (reusing the same aggregation logic as costs.py for the current
  month — NOT a copy-paste; extract a shared helper or call the costs module directly),
  recent_activity (list of last N pages by updated_at, max 10, fields: title, page_type,
  updated_at), data_version (vault_state.data_version).
- `GET /stats/sections` returns an array, one entry per domain in the active vocabulary
  (empty array when vocabulary is empty): domain (tag string),
  page_count, pages_by_type (dict), last_activity (max updated_at among pages tagged
  with that domain), top_pages_by_degree (top 3 pages by graph degree in that domain:
  title, page_type, degree).
- The frontend `HomeDashboard.tsx` is a new React component, mounted at a new section
  entry (Home icon at the top of the NavRail — the slot freed by R11-3). It is the
  default landing section.
- Rendering: plain CSS layout + SVG sparklines for the monthly spend trend (plain SVG,
  no charting library — I3). No heavy computation on the main thread. All data is fetched
  once on mount via the two endpoints; no WebSocket, no polling.
- Section card click: navigates to the wiki tree or graph view with a filter applied for
  that domain tag. The filter mechanism is a URL query param or a Zustand slice
  (architect/frontend decide; the requirement is that tree/graph show only pages tagged
  with the clicked domain). The tree and graph components themselves are NOT rebuilt or
  reimplemented in this sprint — only the filter entry-point is added.
- Long lists within HomeDashboard (e.g., recent_activity list >50 items): virtualised
  via TanStack Virtual if the list can exceed 50 items; the endpoint caps recent_activity
  at 10, so no virtualisation is required for that specific list. If top_pages_by_degree
  per section exceeds 50 rows (impossible given the cap of 3), virtualise. No
  virtualisation needed in practice, but the I4 rule applies if a list is unbounded.
- I3 compliance: no heavy rerender per mount; the spend sparkline is plain SVG computed
  once from the by_day array already returned by the endpoint.

**What this is NOT:** a reporting suite, a charting library integration, a second graph
viewer, or a per-page analytics surface. Scope is a single-screen summary card grid.

### R12-2 — Domain vocabulary + auto-tag: IN SCOPE (L)

**Context.** The owner's knowledge base covers domains such as ServiceNow, SAM,
Procurement, Regolamentazioni, TPRM. He wants pages automatically tagged against a
user-controlled controlled vocabulary so the home dashboard can break down content by
domain, and future features (multi-domain filter, cross-domain synthesis) have a clean
tagging foundation.

**PM decisions (locked):**

- **Vocabulary storage:** the vocabulary is stored via the ADR-0053 `app_config`
  mechanism as a new allowed key `domain_vocabulary`. The exact storage format
  (comma-separated string or JSON array) is an architectural decision: the
  solution-architect MUST document it in ADR-0054 before any R12-2 backend code
  is written. The owner's examples (ServiceNow, SAM, Procurement, Regolamentazioni,
  TPRM) are the reference. The key `domain_vocabulary` is added to `ALLOWED_CONFIG_KEYS`
  in `config_overrides.py` (extending the v1.1 allow-list — the only schema-change
  needed is in the constant, not in the DB table).
- **Settings UI:** the vocabulary is editable in Settings > Advanced as a tag input or
  textarea (frontend-engineer's call; document in PR). Uses `PUT /config/app/domain_vocabulary`.
- **Auto-tag on ingest:** after the existing ingest loop writes a page, a new bounded
  step classifies the page into 0..N vocabulary domains. Classification uses the active
  provider (F17 / I6 — routed through InferenceProvider, not hardcoded). It is a single
  bounded call per page (`max_iter=1`, capped token budget logged per I7). The result is
  written into `pages.tags` using a `"domain/"` prefix convention
  (e.g., `["domain/ServiceNow", "domain/SAM"]`). If the vocabulary is empty, this step
  is skipped entirely — zero provider calls, zero cost.
- **Tag prefix convention:** the `"domain/"` prefix distinguishes domain vocabulary tags
  from other uses of `pages.tags`. This convention is the PM decision; the architect
  confirms it in ADR-0054 (no separate tags-schema ADR needed if ADR-0054 covers it).
- **Backfill:** `POST /ops/backfill-domains` triggers a one-time bounded backfill of
  existing pages against the current vocabulary. Parameters: `max_pages` (int, default
  500, capped at 2000) and `token_budget_usd` (float). Bounded by I7: the backfill
  loop stops at max_pages or token_budget_usd exhaustion, logs `total_cost_usd` and
  `pages_tagged`, and is idempotent (re-running skips pages whose tags already have
  a `"domain/"` entry). Progress is surfaced via a response body `{job_id, status,
  pages_tagged, total_cost_usd, stopped_reason}` — a simple synchronous bounded run
  returning 200 when complete (no persistent job queue required; the max_pages cap keeps
  runtime manageable). If the vocabulary is empty the endpoint returns 200 immediately
  with `pages_tagged: 0`.
- **Empty vocabulary = feature dormant:** when `domain_vocabulary` is unset or empty,
  the auto-tag step is a no-op, `GET /stats/sections` returns `[]`, and no provider
  calls are made. The dashboard shows global KPIs only (R12-1 overview).
- **ADR gate:** ADR-0054 must be accepted by solution-architect BEFORE any R12-2
  backend code that touches `config_overrides.py` or the ingest orchestrator is written.

**What this is NOT:** per-domain provider routing, auto-discovery of domains from
content, renaming or removing existing (non-domain) tags, a free-tagging UX, or
a multi-level taxonomy.

### R12-3 — Server release channel + optional auto-update: IN SCOPE (M)

**Context.** The owner asked how TrueNAS Docker deployments learn about and apply
new Synapse backend versions. The answer is structural: CI publishes images to GHCR,
docker-compose.yml references a versioned image, the frontend notices a version
mismatch and shows a non-blocking notice, and an optional Watchtower block enables
zero-touch auto-updates scoped to the backend service only.

**PM decisions (locked):**

- CI (devops-engineer decides between extending `desktop-release.yml` or a new workflow)
  builds and pushes the backend image to `ghcr.io/<org>/synapse-backend:<tag>` on every
  `vX.Y.Z` tag. Both `vX.Y.Z` and `latest` tags are pushed atomically in the same
  workflow step. Watchtower requires the `latest` mobile tag to detect updates; the
  versioned tag is for pinning and rollback. No other registries.
- `docker-compose.yml` gains an `image: ghcr.io/<org>/synapse-backend:latest` variant.
  The `build:` directive is kept as the dev fallback (or both are documented — devops
  decides; document in PR comment and in DEPLOY.md).
- `GET /status` gains a `backend_version` field (string, e.g. `"1.2.0"`) set from the
  `APP_VERSION` env var (injected by the CI Docker build via `--build-arg` or
  `ARG`/`ENV` in the Dockerfile). If `APP_VERSION` is unset (local dev build),
  `backend_version` is `"dev"`.
- The frontend defines `__APP_VERSION__` at Vite build time via `define` in
  `vite.config.ts` (injected from `VITE_APP_VERSION` env var or the `package.json`
  version field). On app load it compares `__APP_VERSION__` to `backend_version` from
  `GET /status`. If they differ (and `backend_version != "dev"`), it shows a
  non-blocking, dismissible banner: "A server update is available (backend vX.Y.Z /
  frontend vA.B.C). Pull the new image on TrueNAS to update." The banner is
  dismissible per session (sessionStorage flag). It does NOT block any action.
- **Watchtower optional block (owner decision, Emanuele 2026-07-03):** `docker-compose.yml`
  gains an optional Watchtower service block, disabled by default using a Compose profile
  named `autoupdate` (devops decides between profile-based or comment-disabled — document
  the choice in the PR comment). The block: `image: containrrr/watchtower`, mounts
  `/var/run/docker.sock`, runs with `--interval 3600 --label-enable`. The Synapse backend
  service acquires the label `com.centurylinklabs.watchtower.enable=true` so Watchtower
  targets ONLY the backend — postgres and qdrant are never auto-updated by Watchtower.
  To activate: `docker compose --profile autoupdate up -d` (or equivalent if devops
  uses a different mechanism). To leave disabled: `docker compose up -d` (unchanged
  behaviour for existing deployments). Data services (postgres, qdrant) must never
  carry the Watchtower enable label.
- DEPLOY.md gets a new "Updating Synapse" section with two subsections: "Manual updates"
  (`docker compose pull && docker compose up -d`) and "Automatic server updates" with
  three options documented: (a) Watchtower with backend-only label scoping and hourly
  interval — zero-touch, recommended for Emanuele's TrueNAS; safe because Alembic
  migrations run on startup and releases are backward-compatible; (b) TrueNAS SCALE
  Custom App update button — semi-automatic, UI shows update available; (c) Diun
  notify-only — most conservative. An explicit caveat: DB containers (postgres, qdrant)
  MUST NOT be auto-updated blindly; always read the release notes for schema changes
  before updating data services.

**What this is NOT:** a push-notification system, an in-app one-click updater, or
a forced Watchtower deployment (the block is optional, off by default).

### R12-4 — Ingest polling dedup (carry-over from v1.1 BUG-2): IN SCOPE (S)

**Context.** BUG-2 from v1.1 (AC-R11-4-BUG2) may or may not have been resolved in
the v1.1 sprint. This item integrates the fix if it landed, or implements it fresh if
the v1.1 session stalled on it. The cap is exactly one item in this slot; no additional
bugs may be added.

**PM decision:** if AC-R11-4-BUG2 was already merged in v1.1 and is confirmed green by
QA, this item is automatically closed as "done on merge" with no work required.
If it was not resolved, the frontend-engineer implements the fix now: the polling hook
or `useEffect` in `IngestView.tsx` (or the hook it delegates to) returns a cleanup
function that clears the interval/timeout on unmount. A Vitest asserts: mount the
component, unmount it, remount it — the number of active polling intervals never exceeds
1. Verified by spying on `setInterval` and `clearInterval` call counts.

---

## 3. Committed Scope

Exactly the following items. Anything else is out of scope and requires explicit PM
re-approval before any token is spent on it.

---

### R12-1 — Home dashboard

| Field | Value |
|---|---|
| Feature ID | F18 (new — registered this sprint; see §2 F18 note below), F1 (UI shell), F4 (graph community count reused), F16 (dataVersion reused) |
| Owner | backend-engineer (GET /stats/overview, GET /stats/sections) + frontend-engineer (HomeDashboard.tsx, NavRail Home entry, filter navigation) |
| Effort | L |
| ADR reference | No new ADR required for the dashboard endpoints themselves. Filter mechanism (URL param vs Zustand slice) to be documented in a PR comment; if the solution involves a structural routing change an ADR may be needed (architect's call). |
| Invariant check | I1 (read-only endpoints, no vault mutation), I2 (no graph recompute triggered — reads cached snapshot), I3 (no heavy main-thread work; plain SVG sparkline computed once), I4 (TanStack Virtual if any list can exceed 50 items at runtime), I8 (openapi.json regenerated with new endpoints) |

**F18 registration note.** The Home dashboard is the first feature that does not map to
any existing ID in CLAUDE.md §4 (K1–K8, F1–F17). Per the anti-scope-creep invariant
("Never approve work without a feature ID"), this sprint registers F18 as the first
extension ID. F18 is defined as: "Home dashboard + per-section domain insights — a
landing screen surfacing vault KPIs, community topology, AI spend, and domain-vocabulary
breakdowns." CLAUDE.md §4 must be updated to include F18 before the first backend
commit for this item. The tech-writer updates CLAUDE.md §4 (and the BACKLOG.md F18
entry) as a Wave 1 deliverable.

**Acceptance criteria:**

- AC-R12-1-1: `GET /stats/overview` returns HTTP 200 with JSON containing all required
  fields: `pages_by_type` (object, page_type keys → int counts, only live non-deleted
  pages), `total_links` (int, count of rows in the edges table), `communities_count`
  (int, count of distinct community ids from the cached graph snapshot or vault_state;
  returns 0 if graph has not been computed yet), `review_queue_depth` (int, count of
  open review_items), `lint_findings_open` (int, count of lint_findings with
  status="open"), `monthly_ai_spend_usd` (float, current month total from the shared
  costs aggregation helper — not duplicated logic), `recent_activity` (array of max 10
  objects: `{title, page_type, updated_at}` ordered by updated_at DESC), `data_version`
  (int). A pytest with a seeded test DB asserts all fields are present and of the
  correct type; `recent_activity` has at most 10 items.

- AC-R12-1-2: `GET /stats/sections` returns HTTP 200 with a JSON array. When the
  vocabulary is empty (no `domain_vocabulary` in app_config), the array is `[]`. When
  the vocabulary has entries, each element has: `domain` (string, the vocabulary term
  without the "domain/" prefix), `page_count` (int), `pages_by_type` (object),
  `last_activity` (ISO-8601 string or null), `top_pages_by_degree` (array of max 3
  objects: `{title, page_type, degree}`). A pytest with a seeded vocabulary and tagged
  pages asserts: (a) empty vocabulary → `[]`; (b) seeded vocabulary → one entry per
  vocabulary domain with correct counts.

- AC-R12-1-3: The monthly_ai_spend_usd field in `GET /stats/overview` is computed by
  the same logic as `GET /costs/summary` (shared helper, not duplicated SQL). A pytest
  asserts that seeding the same ingest_run rows produces identical totals from both
  endpoints.

- AC-R12-1-4: `HomeDashboard.tsx` renders as the default landing section (displayed on
  app load before any navigation). The NavRail has a Home icon at the top slot (the
  slot freed by the R11-3 logo removal). A Vitest asserts the Home icon is present in
  the NavRail render and the `HomeDashboard` component is mounted when the Home section
  is active.

- AC-R12-1-5: HomeDashboard renders the overview KPI cards and the sections grid using
  mocked API responses. A Vitest asserts: (a) each KPI field from the mock response
  is displayed (by aria-label or data-testid); (b) with an empty sections response the
  sections grid shows a "No domains configured" placeholder; (c) with a non-empty
  sections response each domain card is rendered with the correct page_count.

- AC-R12-1-6: The monthly spend sparkline is a plain SVG element (no charting library
  imported — bundle analysis or grep on the Vitest output confirms no chart dependency).
  A Vitest asserts the SVG element is present in the HomeDashboard render with mocked
  by_day data. No layout function runs on the main thread (I3).

- AC-R12-1-7: Clicking a domain section card triggers navigation to the wiki tree or
  graph view with a domain filter applied (URL param `?domain=<tag>` or equivalent
  Zustand slice update — whichever the frontend-engineer implements). A Vitest asserts
  that clicking the card dispatches the expected navigation action or URL change. The
  tree and graph components themselves are not re-implemented; only the filter entry
  is added (the filter may show all pages if the tree/graph does not yet consume it —
  that is acceptable; the filter dispatch is the AC).

- AC-R12-1-8: `docs/api/openapi.json` regenerated; `GET /stats/overview` and
  `GET /stats/sections` are documented with correct response schemas. `ruff check`,
  `black --check`, `mypy` pass for all new backend modules. `npx tsc --noEmit` and
  `npm run lint` pass for HomeDashboard.tsx.

- AC-R12-1-9: A Playwright screenshot `docs/screens/home-dashboard.png` at 1280×800
  shows the HomeDashboard with at least one KPI card visible. D5 artifact.

- AC-R12-1-10: `docs/USER.md` gains a "Home Dashboard" section explaining the KPI
  cards, the sections grid, and what the domain filter navigation does (or what it will
  do when the tree/graph consumes the filter). Tech-writer sign-off.

---

### R12-2 — Domain vocabulary + auto-tag

| Field | Value |
|---|---|
| Feature ID | F18 (shares the home/insights feature ID), F17 (I6 — classification routed through InferenceProvider), K6 (pages.tags extended with domain/ convention) |
| Owner | solution-architect (ADR-0054) + backend-engineer (config key, ingest hook, backfill endpoint) + frontend-engineer (Settings > Advanced vocabulary editor) |
| Effort | L |
| ADR required | ADR-0054 MUST be accepted by solution-architect BEFORE any R12-2 backend code is written (specifically: before any change to config_overrides.py ALLOWED_CONFIG_KEYS or the ingest orchestrator) |
| Invariant check | I6 (classification routed through InferenceProvider — no hardcoded provider or model; empty vocabulary = zero provider calls), I7 (one bounded call per page, max_iter=1, token_budget logged; backfill bounded by max_pages cap + token_budget_usd + total_cost_usd logged), I1 (backfill writes only to pages.tags via the existing page upsert path — no rescan, no new watcher events), I8 (openapi.json updated with new backfill endpoint; ER not changed — pages.tags already exists) |

**Acceptance criteria:**

- AC-R12-2-0: ADR-0054 committed to `docs/adr/ADR-0054-domain-vocabulary.md` and
  accepted by solution-architect BEFORE any R12-2 backend code is written. ADR covers:
  storage format for `domain_vocabulary` in `app_config` (comma-separated or JSON array;
  architect decides and documents the canonical form), the `"domain/"` prefix
  convention for `pages.tags`, the empty-vocabulary skip rule, the single-call bounded
  classification contract (max_iter=1, provider-routed), and the backfill idempotency
  rule.

- AC-R12-2-1: `domain_vocabulary` is added to `ALLOWED_CONFIG_KEYS` in
  `config_overrides.py`. `PUT /config/app/domain_vocabulary` accepts the vocabulary
  in the format decided by ADR-0054 (comma-separated string or JSON array) and
  validates it (no empty entries, no entry with a slash — slashes are reserved for
  the "domain/" prefix convention). `GET /config/app` returns `domain_vocabulary`
  among the settings list. A pytest asserts: valid vocabulary → 204; invalid format
  (e.g., an entry containing "/") → 422; empty string → 204 (empty vocabulary is valid;
  it disables the feature).

- AC-R12-2-2: After ingest of a fixture page, if `domain_vocabulary` is non-empty,
  the ingest orchestrator makes exactly one bounded provider call to classify the page
  against the vocabulary (mocked provider in pytest). The result writes
  `"domain/<term>"` strings into `pages.tags` for each matched domain. A pytest
  asserts: (a) with empty vocabulary no provider call is made; (b) with a seeded
  vocabulary the mock provider is called once and the returned domain tags appear in
  `pages.tags` with the "domain/" prefix; (c) the cost of the classification call is
  included in the ingest run's `total_cost_usd`.

- AC-R12-2-3: `POST /ops/backfill-domains` accepts optional body `{max_pages: int,
  token_budget_usd: float}`. Returns HTTP 200 with
  `{pages_tagged: int, total_cost_usd: float, stopped_reason: string}`.
  stopped_reason is one of: `"completed"` (all untagged pages processed),
  `"max_pages_reached"`, `"budget_exhausted"`, `"vocabulary_empty"`.
  A pytest asserts: (a) with empty vocabulary returns immediately with
  `pages_tagged: 0, stopped_reason: "vocabulary_empty"`; (b) with a seeded vocabulary
  and 3 untagged fixture pages and mock provider, returns `pages_tagged: 3,
  stopped_reason: "completed"`; (c) with `max_pages: 1` and 3 untagged pages, returns
  `pages_tagged: 1, stopped_reason: "max_pages_reached"`.

- AC-R12-2-4: Backfill is idempotent: pages whose `pages.tags` already contains at
  least one `"domain/"` entry are skipped (not re-classified). A pytest asserts that
  running the backfill twice on the same seeded pages results in the provider being
  called only once (on the first run), not twice.

- AC-R12-2-5: `GET /stats/sections` (R12-1 AC-R12-1-2) correctly reads domain tags
  from `pages.tags` using the `"domain/"` prefix filter. This AC links R12-1 and R12-2:
  the sections endpoint is only meaningful when R12-2 has tagged at least one page.
  A combined pytest seeds pages with `"domain/ServiceNow"` tags and asserts the
  sections response includes a `ServiceNow` entry with correct counts.

- AC-R12-2-6: The Settings > Advanced section in the frontend gains a vocabulary
  editor. A Vitest asserts: (a) the editor renders the current vocabulary from
  `GET /config/app` mock; (b) adding a new domain term and saving triggers
  `PUT /config/app/domain_vocabulary` with the updated value; (c) the editor does
  not show a "domain/" prefix in the UI (the prefix is an implementation detail, hidden
  from the user — users enter "ServiceNow", not "domain/ServiceNow").

- AC-R12-2-7: `docs/api/openapi.json` regenerated; `POST /ops/backfill-domains` is
  documented with request body and response schema. `ruff check`, `black --check`,
  `mypy` pass. `npx tsc --noEmit` and `npm run lint` pass.

- AC-R12-2-8: `docs/USER.md` gains a "Domain Vocabulary" section explaining: how to
  set the vocabulary in Settings > Advanced, what auto-tag does on ingest, when to run
  the backfill, and what the `domain/` prefix means in pages.tags. Tech-writer
  sign-off.

---

### R12-3 — Server release channel

| Field | Value |
|---|---|
| Feature ID | F15 (CI/CD, cross-platform), F16 (version surfacing in status endpoint) |
| Owner | devops-engineer (GHCR workflow, Dockerfile APP_VERSION arg, docker-compose.yml image variant + Watchtower block, DEPLOY.md) + backend-engineer (StatusResponse.backend_version field) + frontend-engineer (version mismatch banner) |
| Effort | M |
| ADR reference | No new ADR required. Devops-engineer documents all structural decisions (workflow name, tag scheme, image name, profile mechanism) in a PR comment. |
| Invariant check | I8 (openapi.json updated — StatusResponse gains backend_version field), I9 (reuses existing GHCR infrastructure, Watchtower is an existing open-source tool; no new registry introduced) |

**Acceptance criteria:**

- AC-R12-3-1: A CI workflow (new or extended) builds and pushes
  `ghcr.io/<org>/synapse-backend:<vX.Y.Z>` and `ghcr.io/<org>/synapse-backend:latest`
  on every `vX.Y.Z` git tag. Both tags are pushed in the same workflow run. A devops
  comment in the PR documents the org/image name and tag scheme.

- AC-R12-3-2: The Dockerfile accepts `APP_VERSION` as a build argument
  (`ARG APP_VERSION=dev`) and sets it as an env var (`ENV APP_VERSION=$APP_VERSION`).
  The CI workflow passes `--build-arg APP_VERSION=${{ github.ref_name }}` when building
  the image.

- AC-R12-3-3: `GET /status` response includes `backend_version: str` (e.g. `"1.2.0"`
  when `APP_VERSION=1.2.0` is set; `"dev"` when unset). The `StatusResponse` Pydantic
  model is updated accordingly. A pytest asserts `backend_version` is present in the
  response and equals the value of the `APP_VERSION` env var (or `"dev"` when unset).
  `docs/api/openapi.json` is regenerated to include the new field.

- AC-R12-3-4: `docker-compose.yml` gains an `image: ghcr.io/<org>/synapse-backend:latest`
  line on the backend service. The existing `build:` directive is retained as a comment
  or as a parallel `docker-compose.override.yml` for local dev (devops-engineer decides;
  documented in a PR comment and in DEPLOY.md).

- AC-R12-3-5: The frontend defines `__APP_VERSION__` at Vite build time. On app load,
  `GET /status` is fetched (already done for the status indicator); if
  `backend_version !== __APP_VERSION__` AND `backend_version !== "dev"`, a dismissible
  banner is shown: text must include both version strings. The banner is dismissed via
  a sessionStorage flag (survives component re-renders, not browser restarts). A Vitest
  asserts: (a) matching versions → no banner; (b) `backend_version: "dev"` → no banner;
  (c) mismatched non-dev versions → banner rendered with both version strings.

- AC-R12-3-6: `docker-compose.yml` contains an optional Watchtower service block
  (disabled by default via a Compose profile `autoupdate` or an equivalent mechanism
  documented in a PR comment). The Synapse backend service carries the label
  `com.centurylinklabs.watchtower.enable=true`. No postgres or qdrant service carries
  this label. Activating the profile starts Watchtower with `--interval 3600
  --label-enable`. Existing `docker compose up -d` (without the profile) leaves
  Watchtower disabled — no behaviour change for existing deployments. A devops
  PR comment documents the exact activation command and the chosen mechanism
  (profile vs commented-out block).

- AC-R12-3-7: `docs/DEPLOY.md` gains an "Updating Synapse" section with two
  subsections: "Manual updates" (exact commands: `docker compose pull && docker compose
  up -d`) and "Automatic server updates" documenting three options: (a) Watchtower
  with backend-only label scoping, hourly interval, and the safety rationale (Alembic
  migrations run on startup; backward-compatible releases); (b) TrueNAS SCALE Custom
  App update button (semi-automatic); (c) Diun notify-only (conservative). An explicit
  caveat paragraph states that DB containers (postgres, qdrant) MUST NOT be
  auto-updated blindly and always requires reading release notes for schema changes
  before updating data services. Tech-writer sign-off.

- AC-R12-3-8: `ruff check`, `black --check`, `mypy` pass for the StatusResponse change.
  `npx tsc --noEmit` and `npm run lint` pass for the version mismatch banner component.

---

### R12-4 — Ingest polling dedup (BUG-2 carry-over)

| Field | Value |
|---|---|
| Feature ID | F1 (UI shell), F16 (ingest view) |
| Owner | frontend-engineer |
| Effort | S |
| Invariant check | I3 (fixing a polling leak directly reduces unnecessary background work on the main thread) |

**Condition:** if AC-R11-4-BUG2 was already merged and QA confirmed green in v1.1,
this item is automatically DONE and no work is required. The QA-test-engineer verifies
this before any new work is started.

**Acceptance criteria (if not already resolved in v1.1):**

- AC-R12-4-1: The polling hook or `useEffect` in `IngestView.tsx` (or the hook it
  delegates to) returns a cleanup function that clears the interval/timeout on unmount.
  A Vitest asserts: mount the component, unmount it, remount it — the number of active
  polling intervals never exceeds 1. Verified by spying on `setInterval` and
  `clearInterval` call counts.

- AC-R12-4-2: `npx tsc --noEmit` and `npm run lint` pass for the modified file(s).

---

## 4. Explicit sequencing and file conflict map

### Critical path

```
ADR-0054 accepted
    └─► R12-2 backend (domain_vocabulary config key + ingest hook + backfill endpoint)
             └─► R12-2 frontend (Settings vocabulary editor)
                      └─► R12-1 GET /stats/sections (depends on R12-2 tagging pages)
                               └─► HomeDashboard sections grid
                                        └─► QA full pass
                                                 └─► Docs gate (tech-writer)
                                                          └─► Architect review
                                                                   └─► PM sign-off
                                                                            └─► tag v1.2.0

R12-1 GET /stats/overview (no dependency on R12-2)
    └─► HomeDashboard KPI cards (parallel to R12-2)

R12-3 (devops-engineer leads; no shared files with R12-1/R12-2 backend)
    └─► backend_version in StatusResponse
    └─► frontend version mismatch banner

R12-4 (frontend-engineer; earliest possible — no deps)
```

### Wave 1 — ADR + backend foundation (days 1–4)

**Day 1 (PM-mandated blocker for R12-2):** solution-architect writes and commits ADR-0054
(`docs/adr/ADR-0054-domain-vocabulary.md`). No R12-2 backend code written until accepted.
Also Day 1: tech-writer adds F18 to CLAUDE.md §4 and BACKLOG.md. R12-4 (if needed) and
R12-3 backend (StatusResponse.backend_version) can start immediately — no dependencies.

**Days 1–4:** backend-engineer implements R12-1 overview endpoint and R12-3 status
field in parallel. devops-engineer implements GHCR workflow and Dockerfile APP_VERSION.
After ADR-0054 accepted: backend-engineer implements R12-2 vocabulary config key,
ingest hook, and backfill endpoint.

### Wave 2 — Frontend + UI integration (days 3–10)

Starts as soon as R12-1 overview endpoint is merged. frontend-engineer implements
HomeDashboard.tsx, NavRail Home entry, and R12-3 version mismatch banner. R12-2
frontend (vocabulary editor) follows after R12-2 backend is merged.

### Wave 3 — QA, docs, sign-offs (days 8–15)

QA full pass: ci.yml exact commands, all new unit tests, E2E regressions. Tech-writer:
USER.md updates (R12-1-10, R12-2-8, R12-3-6) and DEPLOY.md update (R12-3-6). Architect
review: ADR-0054, backfill endpoint, ingest hook. PM exit-criteria check. Tag v1.2.0.

### Same-file conflict registry

| File | Items touching it | Merge order |
|------|-------------------|-------------|
| `backend/app/main.py` | R12-1 (new route registration), R12-2 (backfill endpoint), R12-3 (StatusResponse update) | R12-3 StatusResponse first (small, no deps), then R12-1 endpoints, then R12-2 endpoint |
| `backend/app/config_overrides.py` | R12-2 (domain_vocabulary key added to ALLOWED_CONFIG_KEYS) | R12-2 only; after ADR-0054 accepted |
| `backend/app/ingest/orchestrator.py` | R12-2 (domain classification step after page write) | R12-2 only; after ADR-0054 accepted |
| `backend/app/models.py` | No new table; pages.tags already exists | No migration needed for R12-1/R12-2 |
| `frontend/src/components/nav/NavRail.tsx` | R12-1 (Home icon added at top slot) | R12-1 only |
| `frontend/src/components/settings/SettingsPanel.tsx` | R12-2 (vocabulary editor in Advanced section) | R12-2 only |
| `frontend/src/components/home/HomeDashboard.tsx` | R12-1 (new component) | R12-1 only; new file, no conflict |
| `docs/api/openapi.json` | R12-1 (two new endpoints), R12-2 (backfill endpoint), R12-3 (backend_version field) | Regenerated once all backend PRs are merged |
| `docs/USER.md` | R12-1 (Home Dashboard section), R12-2 (Domain Vocabulary section) | Tech-writer coordinates; two separate sections, no conflict |
| `docs/DEPLOY.md` | R12-3 (Updating Synapse section) | R12-3 only |
| `CLAUDE.md` | F18 registration in §4 | Tech-writer on Day 1, before any F18 commit |
| `docker-compose.yml` | R12-3 (image variant + Watchtower service block + backend label) | R12-3 only; devops-engineer |
| `Dockerfile` | R12-3 (APP_VERSION ARG/ENV) | R12-3 only; devops-engineer |

---

## 5. Wave plan (suggested 2–3 week schedule)

**Wave 1 (days 1–5):**
- Day 1: solution-architect writes ADR-0054. Tech-writer adds F18 to CLAUDE.md §4 and
  BACKLOG.md. frontend-engineer implements R12-4 (if not resolved in v1.1) — no
  dependencies. devops-engineer starts GHCR workflow + Dockerfile APP_VERSION.
- Days 1–3: backend-engineer implements R12-3 StatusResponse.backend_version and
  R12-1 `GET /stats/overview` (no dependency on ADR-0054).
- Days 2–5: backend-engineer implements R12-2 vocabulary config key + ingest
  classification hook + `POST /ops/backfill-domains` after ADR-0054 accepted.
  backend-engineer implements `GET /stats/sections` (depends on R12-2 tagging contract
  being settled in ADR-0054).

**Wave 2 (days 4–10):**
- Days 4–7: frontend-engineer implements HomeDashboard.tsx (KPI cards + spend sparkline
  + sections grid) and NavRail Home entry. R12-3 version mismatch banner (depends on
  backend R12-3 being merged).
- Days 6–10: frontend-engineer implements R12-2 vocabulary editor in Settings > Advanced.

**Wave 3 (days 10–15):**
- qa-test-engineer full pass: ci.yml exact commands, all new unit tests, E2E regressions.
- tech-writer: USER.md (Home Dashboard, Domain Vocabulary sections), DEPLOY.md
  (Updating Synapse section), CLAUDE.md F18 entry confirmation.
- solution-architect: ADR-0054 review vs implementation; openapi.json audit; invariant
  spot-check on I6 for the classification hook.
- PM: exit-criteria check (§7).
- Day 15 target: tag v1.2.0, GitHub release.

---

## 6. Out of scope for v1.2

Everything not listed in §3 is explicitly out of scope. The following items MUST NOT
be built during this sprint without explicit PM escalation and approval:

| Deferred item | Reason |
|---|---|
| Multi-vault switcher | No routing foundation; post-1.x |
| Push notifications (new page, ingest complete) | No WebSocket broadcast in scope; post-1.x |
| Collaborative editing | Post-1.x; out of current roadmap |
| Per-domain provider routing | F17 routing is global; per-domain override is a future enhancement |
| Forced Watchtower deployment (always-on) | Watchtower is optional (Compose profile off by default); forcing it on would change existing deployment behaviour without consent |
| Renaming or removing existing non-domain tags | pages.tags is also used by K6 navigation; changing non-domain tag behaviour is a separate item |
| Auto-discovery of domains from content | Vocabulary is owner-controlled; LLM-proposed vocabulary list is future work |
| Dashboard charts via a charting library | Plain SVG only per I3; no recharts/d3/chart.js |
| Per-page analytics surface | Not a KPI dashboard; out of scope |
| Second graph viewer or new graph layout | I2 and graph work is done; no new layout algorithm this sprint |
| Additional bugfixes beyond R12-4 | Bug cap is one item (BUG-2 carry-over); any new bug requires PM escalation |
| New Settings sections beyond vocabulary editor | Settings IA was done in v1.1; no additional reorganisation |
| OIDC / multi-user | Post-1.x |
| New feature IDs beyond F18 | Anti-scope-creep invariant: no work without a registered ID |

**Permanent invariant blocks (I1–I9 apply unconditionally):** full-rescan, main-thread
force layout, per-token DOM mutation, WYSIWYG/ProseMirror, hardcoded provider or model
ID, unbounded loops, skipping D-artifacts, Tavily, reimplementing local embeddings.

---

## 7. Exit criteria for v1.2 release (EC-M12)

All 4 sign-offs required before tagging `v1.2.0`:
QA-test-engineer + Solution-architect + Tech-writer + Product-manager.

| ID | Criterion |
|---|---|
| EC-M12-1 | All committed items have all ACs green: R12-1 (Home dashboard, all 10 ACs: AC-R12-1-1..10), R12-2 (Domain vocabulary, all 9 ACs: AC-R12-2-0..8, including ADR-0054 accepted), R12-3 (Release channel + auto-update, all 8 ACs: AC-R12-3-1..8), R12-4 (polling dedup, 2 ACs: AC-R12-4-1..2, or confirmed done from v1.1). |
| EC-M12-2 | ci.yml exact commands all exit 0 tree-wide: backend ruff + black + mypy; frontend tsc + lint + test. QA-test-engineer runs verbatim and signs each off. |
| EC-M12-3 | ER diagram zero-drift: `make er` output matches live schema. `docs/er/schema.mmd` committed and current. No new table introduced by this sprint (pages.tags already exists; app_config already exists from v1.1); any other schema change must be justified in an ADR and the ER updated. |
| EC-M12-4 | `docs/api/openapi.json` regenerated: `GET /stats/overview`, `GET /stats/sections`, `POST /ops/backfill-domains`, updated `GET /status` (with backend_version) all present with correct schemas and BearerAuth reference. |
| EC-M12-5 | Mermaid validation loop passes: all `.mmd` files in `docs/architecture/`, `docs/er/`, `docs/sequences/` render without error via `mmdc`. |
| EC-M12-6 | `mkdocs build --strict` exits 0 (no regressions from new doc edits). |
| EC-M12-7 | D5 screenshot `docs/screens/home-dashboard.png` captured at 1280×800 (R12-1 AC-R12-1-9). All prior screenshots remain valid (no regressions). |
| EC-M12-8 | `docs/USER.md` updated: "Home Dashboard" section (R12-1-10) and "Domain Vocabulary" section (R12-2-8) present and tech-writer approved. |
| EC-M12-9 | `docs/DEPLOY.md` updated: "Updating Synapse on TrueNAS" section (R12-3-6) present and tech-writer approved. |
| EC-M12-10 | `docs/adr/ADR-0054-domain-vocabulary.md` committed and in Accepted status. Present in the ADR index. |
| EC-M12-11 | CLAUDE.md §4 includes F18 entry ("Home dashboard + per-section domain insights"). BACKLOG.md Sprint 12 section includes all four R12 items with correct feature IDs. Tech-writer confirms both files updated. |
| EC-M12-12 | Empty vocabulary = feature dormant verified: with `domain_vocabulary` unset or empty, ingest of a fixture page makes zero provider calls for domain classification. `GET /stats/sections` returns `[]`. QA-test-engineer confirms by running the ingest E2E with a mocked empty vocabulary. |
| EC-M12-13 | I7 backfill log verified: `POST /ops/backfill-domains` with a fixture vault returns a response body with `total_cost_usd` and `pages_tagged` populated; the backend log contains a line with `total_cost_usd`. QA confirms by inspection of the test output. |
| EC-M12-14 | R12-3 GHCR workflow verified: a dry-run or CI log shows the image build and push steps for both `vX.Y.Z` and `latest` tags (devops-engineer confirms on the PR). |
| EC-M12-15 | R12-3 version mismatch banner verified: with `backend_version` set to a different value than `__APP_VERSION__` (via env override in test), the banner is visible and includes both version strings. With matching versions, no banner. QA-test-engineer verifies via Vitest and a manual check. |
| EC-M12-16 | `vault/wiki/` remains a valid Obsidian vault (I5/K7). Manual spot-check by owner. |
| EC-M12-17 | GitHub release `v1.2.0` created. Release notes list all items in §3. |
| EC-M12-HCP | Human checkpoint: Emanuele verifies in a live session: (a) open the app — the Home dashboard is the landing screen with KPI cards visible; (b) set a vocabulary (e.g., "ServiceNow, SAM") in Settings > Advanced, ingest a fixture document, confirm the page gains `domain/ServiceNow` or `domain/SAM` tags; (c) run `POST /ops/backfill-domains` and confirm the response includes `total_cost_usd`; (d) confirm `GET /stats/sections` returns entries for ServiceNow and SAM; (e) click a section card and confirm the navigation dispatches correctly; (f) verify the GHCR image name matches what was pushed in CI; (g) set a mismatched backend_version and confirm the version banner appears and is dismissible; (h) run `docker compose --profile autoupdate up -d` and confirm Watchtower starts and shows backend-only label in its logs; run `docker compose up -d` (without profile) and confirm Watchtower is not started. |

---

## 8. De-scope order (if sprint runs over)

Cut in this order:

1. R12-1 sections grid (the `GET /stats/sections` endpoint and the HomeDashboard
   sections grid) — the overview KPI cards stand alone without domain breakdowns; cut
   the sections grid and ship the overview-only dashboard. Document as "sections require
   domain vocabulary to be set" in USER.md.
2. R12-2 backfill endpoint (`POST /ops/backfill-domains`) — the auto-tag on new ingest
   is the core value; the backfill is a convenience for the existing vault. Cut it and
   document manual re-ingest as the fallback for existing pages.
3. R12-3 version mismatch banner — the GHCR publish + StatusResponse.backend_version +
   docker-compose image variant are the load-bearing structural changes; the frontend
   banner is polish. Cut the banner if the sprint runs over; the operator can compare
   versions manually via `GET /status`.

R12-1 overview KPI cards, R12-2 auto-tag on new ingest (vocabulary + ingest hook),
R12-3 GHCR workflow + StatusResponse.backend_version + DEPLOY.md update, and R12-4
polling dedup are committed and MUST NOT be cut.

---

## 9. Velocity note

v1.1 carried 4 items (R11-1 L, R11-2 XL after amendment, R11-3 S, R11-4 S×3) in
2–3 weeks. By commit density v1.1 was a heavy sprint due to the XL Settings redesign
and first-run wizard.

v1.2 carries 4 items (R12-1 L, R12-2 L, R12-3 M, R12-4 S) in 2–3 weeks. This is
comparable to v1.1 in total scope, with two L items and one M instead of one XL and
one L. Risk profile:

- The ADR-0054 gate on Day 1 is the primary sequencing risk (same pattern as ADR-0053
  in v1.1; expected to resolve within 1 day).
- R12-1 and R12-3 have no ADR dependency and can start immediately, reducing the
  blocking window.
- R12-2 ingest hook touches `orchestrator.py`, which is a shared hot path. The
  empty-vocabulary skip rule ensures the hook is a no-op on most existing test fixtures,
  reducing regression risk.
- R12-4 is either already done (closed as "done on merge" from v1.1) or a 1-day fix.
  It should not affect the critical path.
- The main new risk vs v1.1 is the GHCR workflow (R12-3) crossing into devops territory.
  This is rated M (not L) because the structural CI change is bounded and well-defined.

The sprint is intentionally at the same velocity as v1.1 but more evenly distributed
across agents (backend, frontend, devops all active in Wave 1). No single XL item.

**Feature IDs touched this sprint:** F18 (new), F1, F4 (community count reused), F15,
F16 (status version field), F17 (I6 — classification hook).
**New feature ID registered:** F18 (Home dashboard + per-section domain insights).
**Invariants with heightened priority:** I6 (empty-vocabulary skip rule + classification
routed through InferenceProvider), I7 (backfill bounded by max_pages + token_budget +
cost logged), I8 (openapi.json + CLAUDE.md §4 updated before first F18 commit).

---

## 10. AMENDMENT — Owner feedback on R12-1 (Emanuele, 2026-07-03, post-first-render)

> Supersedes the R12-1 rendering priorities. "Le sezioni della home devono essere dinamiche
> in base alle note dell'utente (raggruppate per fare i count); la home non deve essere solo
> una dashboard ma un'overview sul sistema in generale."

**A1 — Dynamic groups (zero-config).** New `GET /stats/groups`: sections derived
automatically from the existing server-side Louvain communities (I2 — already computed,
no new heavy work). Shape (FROZEN): `{groups:[{community:int, label:str, pages_total:int,
pages_by_type:{}, top_pages:[{id,title,slug,degree}] (cap 5), last_activity:iso|null}]}`,
ordered by pages_total DESC, cap 12; label = title of the highest-degree page in the
community (truncated 48 chars); memoised on data_version. No AI call (heuristic label).

**A2 — Home = system overview.** HomeDashboard gains a "System status" block sourced from
the EXISTING `/health/detailed` + `/status` + active provider config: component health
(watcher, scheduler, ingest queue, DB, Qdrant, graph cache), active provider/model,
backend version, uptime, data version. No new backend endpoint.

**A3 — Vocabulary demoted to optional curated layer.** Domain sections (R12-2) render
ABOVE auto-groups when a vocabulary is defined; with no vocabulary the Home is fully
functional with auto-groups only (zero-config default). R12-2 backfill/auto-tag unchanged.

**A4 (owner, 2026-07-03 sera) — Active jobs block + groups cap.** (1) HomeDashboard gains
an "Active jobs" block (between system status and KPIs), visible ONLY when something is
running: ingest queue processing/pending (reuse the activityStore /ingest/queue snapshot —
no new poller), running deep-research runs, domain backfill in flight (GET
/ops/backfill-domains), import scan in flight. Each row: kind, label, state, link to its
section. (2) GRUPPI AUTOMATICI renders the TOP 4 by size with an "Espandi/Comprimi"
toggle revealing the full capped list (12).
