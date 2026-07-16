# Sprint v1.0 — PM Scope Lock

> Milestone: M10 — "Distribution & multi-user"
> Author: product-manager
> Date: 2026-07-03
> Branch: sprint/v1.0 (cut from sprint/v0.9 after v0.9.0 tag)
> Prerequisite: M9 exit criteria met (EC-M9-1..EC-M9-HCP confirmed by Emanuele).
> Source roadmap: docs/reference/ROADMAP-v0.7-v1.0.md §v1.0 (rescoped per PM brief)
> Sprint duration: 3–4 weeks

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
A locally-green vitest run does NOT substitute for `npm run test` (they are the same
command; this rule exists to prevent skipping the step).

---

## 1. Sprint Goal

Turn Synapse from a personal tool into a shippable product: add a lightweight but
real access-control layer, polish the install experience, and publish the documentation
site. Everything a solo homelab owner needs to safely expose Synapse on their Tailscale
mesh — without the complexity (and cost) of full OIDC multi-user auth.

---

## 2. Scope decision record

### R10-1 RESCOPED — Authentication: shared token, not OIDC

**Original roadmap item:** "Authentication layer — token/OIDC login, request-scoped
vault routing (unlocks multi-vault/multi-user; foundational, design ADR first)."

**PM decision (2026-07-03):**
OIDC and multi-user are deferred to post-1.0. Rationale: Synapse is a self-hosted
single-owner product running on a private TrueNAS box behind Tailscale. Full OIDC adds
an identity provider dependency that the target user does not have and does not want.
It is also a structural commitment that would constrain the data model across all future
sprints. The correct 1.0 auth story for this audience is:

- A single shared access token (`SYNAPSE_AUTH_TOKEN` env var).
- When set: FastAPI middleware enforces `Authorization: Bearer <token>` on ALL routes
  except `GET /health` and `GET /status` (the two monitoring-safe endpoints).
- When empty / unset: auth is disabled entirely (backward-compatible default).
- The frontend sends the token as Bearer on every request; it stores the token per
  server URL in `localStorage` (key: `synapse_token_{serverUrl}`).
- When the backend returns HTTP 401, the frontend shows the ConnectScreen with a token
  input field (the "auth UX" item, R10-2 rescoped below).

**Multi-vault routing** stays deferred. Single-vault is correct for 1.0. This is
explicitly documented in ADR-0052 (required before any code is written — see AC-R10-1-0).

**Why this is still XL effort:** The middleware touches `backend/app/main.py` (1 new
dependency injected into EVERY route or a global middleware). The frontend header
injection touches EVERY API client call — the shared `api.ts` / `apiClient` module
is the single entry point to change, but it must be done carefully given concurrent
use in chat streaming, graph polling, health polling, and all REST calls.

### R10-2 RESCOPED — Auth UX (not multi-vault UI)

**Original roadmap item:** "Multi-vault UI — vault switcher, per-vault provider config
surfaced."

**PM decision (2026-07-03):**
Multi-vault UI depends on multi-vault routing which is deferred. The slot is replaced by
the auth UX that R10-1 requires: ConnectScreen token field, per-server token storage,
Settings token rotation. This is a natural pairing with R10-1 and costs approximately
the same effort as the original L-tagged item.

### R10-3 — Code signing: OUT OF SCOPE

Code signing requires a paid Apple Developer account ($99/year) and a Windows EV cert
(~$200+/year) plus notarization toolchain that the CI environment does not currently
have. This is a one-time infrastructure spend the owner has not yet made. Shipping
unsigned builds is already the status quo since v0.7. This item is replaced by a
complete step-by-step guide in `docs/DEPLOY.md` (see AC-R10-3).

### R10-4 — Desktop auto-update: ALREADY SHIPPED

Auto-update shipped in v0.8.1 (tauri-plugin-updater against GitHub releases).
This item is marked DONE in this scope lock. No work required. Exit criterion is a
verification note in the release checklist confirming v1.0.0 update chain works.

### R10-5 — Mobile/PWA polish: IN SCOPE (M, tightly scoped)

Breakpoints for viewports <768px (nav rail collapses to bottom tab bar or hamburger,
panels stack), touch targets minimum 44×44px, graph pinch-zoom sanity (existing sigma
scroll-zoom also responds to pinch via standard pointer events — verify and document).
No new navigation library. No full mobile redesign. Scope is the three specific
mechanical changes listed in AC-R10-5-*.

### R10-6 — MkDocs Material docs site: IN SCOPE (M)

`mkdocs.yml` with Material theme, nav covering USER.md, DEPLOY.md, ADR index, and the
architecture diagrams from `docs/architecture/`. CI job builds the site (no deploy —
deploy to GitHub Pages requires the owner to enable Pages in repo settings; the CI job
produces the artifact and a note). Local `make docs-serve` target.

---

## 3. Committed Scope

Exactly the following items. Anything else is out of scope and requires explicit
PM re-approval before any token is spent on it.

---

### R10-1 — Authentication middleware (shared Bearer token)

| Field | Value |
|---|---|
| Feature ID | F16 (Settings/config), F15 (cross-platform security hardening) |
| Owner | backend-engineer (middleware + env var) + ai-agent-engineer (ADR, leads design) |
| Effort | XL |
| Roadmap source | ROADMAP-v0.7-v1.0.md §v1.0 R10-1 (rescoped) |
| ADR required | ADR-0052 MUST be accepted by solution-architect BEFORE any code is written |

**Design decision (PM-locked — do not re-litigate without a new ADR):**

Auth model:
- `SYNAPSE_AUTH_TOKEN` env var (string). Empty string or absent = auth disabled.
- FastAPI: a single `HTTPBearer` security scheme + `Depends(verify_token)` dependency
  injected globally (preferred: FastAPI middleware using `app.middleware("http")` so
  it catches all routes including WebSocket upgrades, not just REST) or via a global
  `dependencies=[Depends(verify_token)]` on the FastAPI app constructor.
- Excluded from auth: `GET /health`, `GET /status`, `GET /health/detailed`.
- 401 response body: `{"error": "unauthorized", "hint": "Set Authorization: Bearer <token>"}`.
- No session cookies. No JWT. No expiry. Token rotation = restart with new env var.
- HTTPS is the owner's responsibility (Cloudflare Tunnel or Tailscale HTTPS — already
  in use). Document this in DEPLOY.md.

Multi-vault routing: deferred. The `vault_id` column continues to exist in the schema
but routing remains single-vault. Document this explicitly in ADR-0052.

**Acceptance criteria:**

- AC-R10-1-0: ADR-0052 committed to `docs/adr/0052-auth-token-model.md` and accepted
  by solution-architect BEFORE any implementation code is written. ADR covers: why shared
  token not OIDC, why OIDC is deferred, single-vault scope for 1.0, HTTPS responsibility
  model, rotation procedure, excluded endpoints.

- AC-R10-1-1: `SYNAPSE_AUTH_TOKEN` env var read in `backend/app/main.py` (or a new
  `backend/app/auth.py` module — SHOULD extract to `auth.py` to minimize main.py surface).
  When the env var is absent or empty, authentication is disabled and all existing
  behaviour is unchanged (backward-compatible default). A pytest asserts: (a) with
  `SYNAPSE_AUTH_TOKEN=""`, `GET /pages` returns 200 with no `Authorization` header;
  (b) with `SYNAPSE_AUTH_TOKEN="test-token"`, `GET /pages` with no header returns 401;
  (c) with correct Bearer token, `GET /pages` returns 200.

- AC-R10-1-2: `GET /health`, `GET /status`, and `GET /health/detailed` are excluded from
  auth regardless of `SYNAPSE_AUTH_TOKEN` value. A pytest asserts all three return 200
  without an `Authorization` header when the token is set.

- AC-R10-1-3: The middleware or dependency handles the WebSocket upgrade path (used by
  any streaming endpoint). If Synapse uses WebSocket for chat streaming, the token must
  be verifiable on the upgrade request (query param `?token=` or first message handshake
  — engineer chooses and documents in ADR-0052). A pytest asserts the WebSocket
  connection is rejected (close code 4401) when the token is wrong.

- AC-R10-1-4: `SYNAPSE_AUTH_TOKEN` is documented in `docs/DEPLOY.md` under a new
  "Security" section. Section content: how to set, what "disabled" means, rotation
  procedure (set new env var + restart container), HTTPS responsibility note. Tech-writer
  sign-off required.

- AC-R10-1-5: `ruff check app tests` + `black --check app tests` + `mypy app` all pass
  with the new `auth.py` module (mypy strict; `verify_token` has fully typed signature).
  No `Any` types in `auth.py`. Mypy strict mode confirmed.

- AC-R10-1-6: `docs/api/openapi.json` regenerated. The security scheme (`BearerAuth`) is
  declared in the OpenAPI spec components; all routes except the three excluded endpoints
  reference it via `security: [{BearerAuth: []}]`. The excluded endpoints have
  `security: []` explicitly (FastAPI pattern).

**Sequencing note:** R10-1 is the FIRST item to be merged because it introduces the auth
middleware that changes how the frontend must communicate. R10-2 (auth UX) MUST start
only after R10-1 backend is merged so the frontend can test against real 401 responses.
No other item in this sprint touches `backend/app/main.py` before R10-1 is merged.

---

### R10-2 — Auth UX (ConnectScreen token field + Settings rotation)

| Field | Value |
|---|---|
| Feature ID | F1 (UI shell), F16 (Settings), F15 (security UX) |
| Owner | frontend-engineer |
| Effort | M |
| Roadmap source | ROADMAP-v0.7-v1.0.md §v1.0 R10-2 (rescoped from multi-vault UI) |
| Depends on | R10-1 merged |

**Design decision (PM-locked):**

Token is stored per server URL in `localStorage` under the key
`synapse_token_{encodeURIComponent(serverUrl)}`. It is never stored in Zustand state
(avoids accidental serialization). It is read at request time by the API client module.

The shared API client (`frontend/src/api.ts` or equivalent — whatever module wraps
`fetch` or `axios` today) is the SINGLE place where the `Authorization: Bearer` header
is injected. No component constructs this header directly. This is the key architectural
rule: one place to change, not scattered across all API callers.

**Acceptance criteria:**

- AC-R10-2-1: The shared API client module (`api.ts` or the primary fetch wrapper) reads
  the stored token from `localStorage` for the current server URL and adds
  `Authorization: Bearer <token>` to every outgoing request. When no token is stored,
  the header is omitted. A Vitest asserts: (a) with token in localStorage, the injected
  header is present; (b) with no token, the header is absent.

- AC-R10-2-2: When any API call returns HTTP 401, the frontend clears the stored token
  for that server URL and shows ConnectScreen with an error state `"Authentication
  required"`. This behaviour is implemented at the API client level (a response
  interceptor), not per-component. A Vitest asserts the response interceptor triggers
  the Zustand action that resets the connected-server state on 401.

- AC-R10-2-3: ConnectScreen gains a token input field. The field is shown unconditionally
  (not only on 401 — the user may want to configure the token before connecting). It is
  below the server URL field. Label: "Access token" / "Token di accesso" (i18n). The
  field type is `password` (hidden by default with a show/hide toggle using `<Eye>` /
  `<EyeOff>` Lucide icons). On successful connection with a non-empty token, the token
  is persisted to `localStorage` under the server-specific key.

- AC-R10-2-4: When ConnectScreen is shown because of a 401, the token field is
  auto-focused and an inline error message reads "Invalid or missing access token. Enter
  the token set by SYNAPSE_AUTH_TOKEN on the server." / Italian equivalent. The error
  uses `var(--syn-red)` and `role="alert"` (consistent with UXA-16 fix from v0.9).

- AC-R10-2-5: Settings gains a "Security" section (new nav item in the settings sidebar).
  Content: (a) current server URL (read-only, for context); (b) "Rotate token" field —
  user pastes a new token, clicks "Update", the new token replaces the stored one
  locally and `localStorage` is updated. No API call is made (token rotation is
  server-side restart + client-side update). An info banner: "To rotate the server
  token: set SYNAPSE_AUTH_TOKEN to a new value in your docker-compose.yml and restart
  the container. Then enter the new token here." Tech-writer sign-off on copy.

- AC-R10-2-6: All EN and IT i18n keys for the new ConnectScreen fields and Settings
  section are present in `en.json` and `it.json`. A Vitest asserts all new keys resolve
  to non-empty strings in both locales.

- AC-R10-2-7: Playwright screenshot `docs/screens/connect-screen-auth.png` captured
  showing ConnectScreen with the token field visible. D5 artifact.

- AC-R10-2-8: The `Authorization` header injection is verified to cover: REST calls,
  WebSocket upgrade (query param or first-frame as decided in R10-1 AC-R10-1-3), and the
  health polling interval (the 30-second `GET /health/detailed` poll from v0.9 R9-2).
  A Vitest asserts that the mock health-poll interval call carries the header.

**Sequencing note:** frontend-engineer must NOT start AC-R10-2-1 through AC-R10-2-8
until R10-1 backend middleware is merged. The ConnectScreen component and Settings
Security section can be scaffolded (skeleton UI, mocked 401 response) in parallel during
R10-1 backend work, but the scaffolding MUST NOT be merged to sprint/v1.0 until R10-1
is merged. Use a feature sub-branch.

---

### R10-3 — Code signing guide in DEPLOY.md

| Field | Value |
|---|---|
| Feature ID | F15 (distribution), D6b (DEPLOY.md) |
| Owner | tech-writer + devops-engineer |
| Effort | S |
| Roadmap source | ROADMAP-v0.7-v1.0.md §v1.0 R10-3 (OUT OF SCOPE for code — doc only) |

**Acceptance criteria:**

- AC-R10-3-1: `docs/DEPLOY.md` gains a "Code Signing (Desktop Builds)" section. Content:
  (a) macOS: Apple Developer account enrollment URL, where to create a Developer ID
  certificate, required GitHub Actions secrets (`APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`,
  `APPLE_SIGNING_IDENTITY`, `APPLE_ID`, `APPLE_TEAM_ID`), tauri.conf.json change to
  enable signing, notarization via `xcrun notarytool`, stapling. Reference the official
  Tauri v2 signing guide (https://tauri.app/distribute/sign/macos/).
  (b) Windows: EV certificate acquisition, required GitHub Actions secrets
  (`WINDOWS_CERTIFICATE`, `WINDOWS_CERTIFICATE_PASSWORD`), tauri.conf.json change.
  Reference the official Tauri v2 signing guide (https://tauri.app/distribute/sign/windows/).
  (c) Why unsigned: explain that the current build is unsigned-but-functional for
  Tailscale/LAN deployment; the user must bypass Gatekeeper on macOS (`xattr -cr`
  command documented) and SmartScreen on Windows. Document the security model clearly.

- AC-R10-3-2: The section is reviewed and approved by tech-writer (prose quality) and
  devops-engineer (technical accuracy). No CI job is added for signing (no secrets
  available).

---

### R10-4 — Desktop auto-update: DONE (carried from v0.8.1)

| Field | Value |
|---|---|
| Feature ID | F15 (desktop packaging) |
| Status | DONE — shipped v0.8.1 |
| Sprint | v1.0 (carried for exit-criteria verification only) |

**Exit criterion only — no code work:**

- AC-R10-4-verify: During the v1.0.0 release process, confirm that the auto-update
  chain works: tag v1.0.0 → GitHub Actions builds and publishes release artifacts →
  a running v0.9.0 Tauri desktop app receives the update prompt and updates to v1.0.0.
  This is a manual verification step by Emanuele. Document the result in the release
  checklist.

---

### R10-5 — Mobile/PWA polish (breakpoints + touch targets + graph pinch-zoom)

| Field | Value |
|---|---|
| Feature ID | F15 (PWA), F1 (UI shell responsive) |
| Owner | frontend-engineer |
| Effort | M |
| Roadmap source | ROADMAP-v0.7-v1.0.md §v1.0 R10-5 |

**Scope (tightly bounded — do not expand without PM re-approval):**
Three mechanical changes only: (1) breakpoint CSS at <768px, (2) touch target audit and
fix, (3) pinch-zoom verification on the sigma graph canvas. No navigation library
changes. No full mobile redesign. The 3-panel shell is allowed to collapse gracefully
rather than be fully redesigned.

**Acceptance criteria:**

- AC-R10-5-1: A `@media (max-width: 767px)` block in the main CSS (or `theme.css`)
  implements: (a) the left nav rail collapses to a bottom tab bar (icon-only, no labels)
  OR hides and is replaced by a hamburger `<Menu>` icon that opens a drawer — engineer
  chooses the simpler implementation and documents the choice in a PR comment;
  (b) the three panels (tree / editor / preview) stack vertically (CSS `flex-direction:
  column`) with each panel collapsible via a chevron toggle;
  (c) the right preview panel is hidden by default on <768px and revealed by a toggle.
  A Vitest or Playwright assertion confirms the nav rail is not visible at 375px viewport
  width (or the bottom tab bar is visible in its place).

- AC-R10-5-2: All interactive elements (buttons, nav items, list items in the tree and
  conversation list) have a minimum touch target of 44×44px at <768px. Verified by a
  Playwright test that checks `getBoundingClientRect().height >= 44` and `width >= 44`
  for the 5 most critical interactive elements (nav buttons, send button, tree items,
  conversation list items, ConnectScreen connect button) at 375px viewport width.

- AC-R10-5-3: The sigma.js graph canvas responds to pinch-zoom (two-finger spread on
  mobile) via the standard pointer events that sigma.js supports. Verify: open the graph
  view at 375px viewport width (Playwright or manual), perform a simulated pinch gesture,
  confirm the graph scale changes. The graph canvas must NOT trigger a full page scroll
  (add `touch-action: none` to the graph canvas container if not already present). A
  comment in the sigma wrapper component documents this. Screenshot `docs/screens/graph-mobile.png`
  captured at 375px viewport. D5 artifact.

- AC-R10-5-4: At <768px the graph pinch-zoom does not violate I2 (no main-thread layout
  triggered). The zoom operation changes the sigma camera (precomputed coords, zoom
  scalar applied by sigma renderer) — no FA2 re-invocation. A code-level assertion (grep
  confirming no `forceAtlas2` call in the zoom handler) suffices.

- AC-R10-5-5: `docs/USER.md` updated with a "Mobile / PWA" section: how to install as a
  PWA on iOS/Android (Add to Home Screen), note on unsupported features at mobile
  viewport (complex 3-panel editing), graph interaction on touch. Tech-writer sign-off.

---

### R10-6 — MkDocs Material docs site

| Field | Value |
|---|---|
| Feature ID | D1/D6/D7 (docs artifacts published), F15 (distribution) |
| Owner | tech-writer (content + nav) + devops-engineer (CI job + Makefile) |
| Effort | M |
| Roadmap source | ROADMAP-v0.7-v1.0.md §v1.0 R10-6; CLAUDE.md §9 v0.6 optional |

**Acceptance criteria:**

- AC-R10-6-1: `mkdocs.yml` committed at repo root. Must use `theme: material` (MkDocs
  Material). Nav structure:
  ```yaml
  nav:
    - Home: docs/USER.md
    - Deploy: docs/DEPLOY.md
    - Architecture:
        - Overview: docs/architecture/context.mmd (embedded as image or fenced block)
        - C4 Diagrams: docs/architecture/
    - ADR Index: docs/adr/   # auto-listed
    - API Reference: docs/api/openapi.json  # rendered via swagger-ui or redoc plugin
  ```
  The exact nav keys are the tech-writer's call; the items above are required minimums.

- AC-R10-6-2: `make docs-serve` target in `Makefile` runs `mkdocs serve` locally.
  `make docs-build` runs `mkdocs build --strict` (strict mode fails on warnings).
  Both targets documented in `docs/DEPLOY.md`.

- AC-R10-6-3: A new CI job `docs-site` in `.github/workflows/ci.yml` runs
  `mkdocs build --strict` on every push to `sprint/**` and `main`. Failure blocks merge
  (consistent with docs-as-DoD I8). The job does NOT deploy (GitHub Pages deployment
  requires the owner to enable Pages in repo Settings — this is documented in a comment
  in the job file and in `docs/DEPLOY.md`).

- AC-R10-6-4: Mermaid diagrams in the docs site are rendered correctly. The MkDocs
  Material theme with `pymdownx.superfences` + mermaid support handles `.mmd` files
  embedded in Markdown. All architecture diagrams in `docs/architecture/` and
  `docs/sequences/` are viewable in the built site. The existing Mermaid validation
  loop in ci.yml (Stage 5) continues to run independently.

- AC-R10-6-5: `docs/USER.md` and `docs/DEPLOY.md` are complete and polished (not stub
  state). Tech-writer reviews both for v1.0 accuracy: all env vars documented, all
  operations covered, auth token documented (cross-ref R10-1 AC-R10-1-4), code-signing
  guide present (cross-ref R10-3). This is the D6 final completion gate.

- AC-R10-6-6: ADR index page (or auto-listing) makes all ADR-0001 through ADR-0052
  navigable from the docs site. At minimum a `docs/adr/index.md` listing all ADRs by
  number + title is created and committed. Each ADR file already follows the standard
  format; no content changes to individual ADRs are required for this item.

---

### QA-v0.9-leftovers — E2E test fixes from v0.9 (mandatory carry-forward)

| Field | Value |
|---|---|
| Feature ID | F15 (QA, cross-platform) |
| Owner | qa-test-engineer |
| Effort | S |
| Source | v0.9 QA known gaps carried to v1.0 backlog |

These are two concrete test failures identified in v0.9 QA that were not de-scoped
but were not resolved before the v0.9.0 tag. They must be fixed in Wave 1.

**Acceptance criteria:**

- AC-QA-LO-1 (E2E Cost testid/locator gap): The Playwright E2E spec for the Settings
  "Costi" section (R9-1, spec: `settings-cost`) uses a locator or `data-testid` that
  does not exist in the rendered DOM — causing the spec to fail. Fix by:
  (a) adding `data-testid="settings-cost-section"` to the Cost & Usage section container
  in the Settings component, AND `data-testid="cost-monthly-total"` to the monthly total
  display element;
  (b) updating the Playwright spec to use `page.getByTestId("settings-cost-section")`
  and `page.getByTestId("cost-monthly-total")` instead of the failing locator.
  After fix: the `settings-cost` E2E spec must pass against a running backend with at
  least one fixture cost run in the DB.

- AC-QA-LO-2 (EdgeDetail `computed_at` field): The `GET /graph/edge/{src_id}/{dst_id}`
  endpoint (R9-5) response schema includes a `computed_at` field in the OpenAPI spec but
  the actual response does not include it (or vice versa — the field is in the response
  but missing from the schema). Fix: align the backend response model and the OpenAPI
  schema. If the field is not useful, remove it from both the response and the schema
  and regenerate `docs/api/openapi.json`. If it is useful, add it to the Pydantic
  response model (type: `datetime | None`), populate it from the `edges` table
  `updated_at` column, and regenerate the spec. A pytest asserts the endpoint response
  matches the OpenAPI schema exactly (using `jsonschema.validate`).

---

### UX-v1.0 — P2/P3 quick items (capped at 3, PM-selected)

| Field | Value |
|---|---|
| Feature ID | F1 (UI shell), F16 (i18n / design system) |
| Owner | frontend-engineer |
| Effort | S × 3 |
| Source | UX-AUDIT-2026-07.md §"Remaining open items" |

PM judgment: from the 18 remaining P2/P3 items, the following 3 are selected for v1.0
because they are each S-effort, have no file conflicts with R10-1/R10-2, and directly
affect professional quality or accessibility of the shipping product. All others are
deferred to post-1.0.

**Selected items:**

| Sub-ID | Audit ref | Effort | Rationale for inclusion |
|--------|-----------|--------|------------------------|
| UX-v1.0-A | UXA-08 | S | Role labels dominate message content — the most visible chat UX debt that affects every conversation; simple CSS change |
| UX-v1.0-B | UXA-15 | S | Mixed `role="dialog"` / `role="listbox"` in ProviderSelector — accessibility correctness on a control used in every session |
| UX-v1.0-C | UXA-18 | S | Raw i18n key `review.itemType.new_page` rendered in Review Queue — makes the product look broken; one normalisation line |

**Acceptance criteria:**

- AC-UX-A: `UXA-08` — Role labels in `MessageList.tsx` reduced to `fontSize: 9px`,
  `color: var(--syn-text-dim)`, no uppercase transformation. A left-border stripe
  (`3px solid`) is added per message turn: `var(--syn-accent-soft)` for user turns,
  `var(--syn-notice-success-bg)` for assistant turns. The role label text itself is
  retained (not removed) to preserve screenreader semantics. A Vitest snapshot asserts
  the role label renders with `font-size: 9px` class or inline style.

- AC-UX-B: `UXA-15` — `ProviderSelector.tsx` fixed to use `role="dialog" aria-modal="true"`
  on the panel with standard `<button>` elements inside (remove the incorrect
  `role="listbox"` from the inner list container). Trigger button retains
  `aria-haspopup="dialog"`. A Vitest asserts the panel element has `role="dialog"` and
  `aria-modal="true"` and the inner list does NOT have `role="listbox"`.

- AC-UX-C: `UXA-18` — `ItemTypeBadge` component normalises `item_type` from the backend
  by replacing underscores with hyphens before the `t()` lookup:
  `item_type.replace(/_/g, "-")`. A Vitest asserts that `"new_page"` maps to the
  translation key `"review.itemType.new-page"` (or `"missing-page"` — whichever the
  EN locale defines) and renders a non-empty string (not the raw key).

**File conflict note:** UX-v1.0-A touches `MessageList.tsx`. UX-v1.0-B touches
`ProviderSelector.tsx`. UX-v1.0-C touches `ItemTypeBadge` component. None of these files
are touched by R10-1 or R10-2. All three UX items can proceed in parallel with
R10-2 scaffolding work (ConnectScreen + Settings) with no conflict. However, do NOT
merge UX-v1.0-A/B/C until R10-1 is merged (to avoid cross-merge conflicts in the same
PR run on the same branch).

---

## 4. Explicit sequencing and file conflict map

### Wave 1 — ADR first, then auth backend (days 1–4)

**Day 1 (PM-mandated blocker):** solution-architect writes and commits ADR-0052
(`docs/adr/0052-auth-token-model.md`). No backend code written until ADR-0052 is
accepted. QA-v0.9-leftovers (AC-QA-LO-1 and AC-QA-LO-2) start in parallel (no shared
files with auth).

**Days 2–4:** backend-engineer implements R10-1 (auth middleware, `auth.py` module,
pytests). R10-5 (mobile CSS) and R10-6 (MkDocs) start in parallel — they have no
dependency on R10-1 backend.

### Wave 2 — Auth frontend + UX items (days 3–8, overlaps Wave 1 tail)

**Starts after R10-1 merged.** frontend-engineer merges the R10-2 scaffold branch
(ConnectScreen token field, Settings Security section, API client Bearer injection).
UX-v1.0-A/B/C start after R10-2 scaffold is on branch but MUST NOT merge before R10-1.

### Wave 3 — Docs, PWA, MkDocs completion (days 5–12)

R10-3 (DEPLOY.md signing guide): tech-writer + devops-engineer; no file conflicts.
R10-5 (mobile breakpoints): frontend-engineer; touches CSS + sigma wrapper only.
R10-6 (MkDocs): tech-writer drives mkdocs.yml + Makefile; devops-engineer adds CI job.

### Critical path

```
ADR-0052 accepted
    └─► R10-1 backend merged
             └─► R10-2 frontend merged
                      └─► UX-v1.0-A/B/C merged
                               └─► QA full pass (ci.yml exact commands)
                                        └─► Docs gate (tech-writer)
                                                 └─► Architect review
                                                          └─► PM sign-off
                                                                   └─► tag v1.0.0
```

### Same-file conflict registry

| File | Items touching it | Merge order |
|------|-------------------|-------------|
| `backend/app/main.py` | R10-1 (middleware + security scheme) | R10-1 ONLY; no other item may touch this file until R10-1 is merged |
| `backend/app/auth.py` (new) | R10-1 (owns it entirely) | No conflict |
| `frontend/src/api.ts` (or fetch wrapper) | R10-2 (Bearer header injection) | R10-2 after R10-1 |
| `frontend/src/components/ConnectScreen.tsx` | R10-2 (token field) | R10-2 only; no other item touches this file |
| `frontend/src/styles/theme.css` | R10-5 (mobile breakpoints) | R10-5 is standalone; no conflict |
| `frontend/src/components/MessageList.tsx` | UX-v1.0-A (role labels) | UX-v1.0-A after R10-1 merged |
| `frontend/src/components/ProviderSelector.tsx` | UX-v1.0-B (ARIA) | UX-v1.0-B after R10-1 merged |
| `docs/DEPLOY.md` | R10-1 (Security section), R10-3 (signing guide), R10-5 (PWA section), R10-6 (make docs-build) | tech-writer coordinates sequential edits; suggest one PR per section |
| `docs/api/openapi.json` | R10-1 (BearerAuth scheme), QA-LO-2 (EdgeDetail schema fix) | QA-LO-2 first (fixes existing endpoint), then R10-1 (adds security to all routes); or merge both in R10-1 PR after QA-LO-2 is fixed — backend-engineer coordinates |
| `.github/workflows/ci.yml` | R10-6 (docs-site job) | R10-6 adds one new job; no conflict with existing jobs |

---

## 5. Wave plan (suggested 3-week schedule)

**Wave 1 (days 1–5):**
- Day 1: solution-architect writes ADR-0052. QA-test-engineer starts QA-LO-1 and QA-LO-2.
- Days 2–4: backend-engineer implements R10-1 (`auth.py`, middleware, pytests, openapi regen).
- Days 2–5: tech-writer starts R10-6 mkdocs.yml + nav structure. devops-engineer adds
  `make docs-serve` + `make docs-build` + CI job scaffold.
- Days 2–5: frontend-engineer scaffolds R10-2 feature branch (ConnectScreen token field,
  Settings Security section — all mocked, not merged to main branch yet). Also starts
  R10-5 mobile breakpoints (no dependency on R10-1).

**Wave 2 (days 5–12):**
- Day 5: R10-1 merged. frontend-engineer merges R10-2 scaffold branch to sprint/v1.0.
- Days 5–8: frontend-engineer completes R10-2 (API client Bearer injection, WebSocket
  path, real 401 interceptor test against running backend).
- Days 6–10: UX-v1.0-A/B/C (frontend-engineer; parallel to R10-2 tail; no file conflicts).
- Days 6–10: tech-writer completes R10-3 (DEPLOY.md signing guide), R10-5 USER.md PWA
  section, R10-6 content review.
- Days 7–12: R10-5 mobile CSS complete + Playwright screenshot at 375px.

**Wave 3 (days 12–18):**
- qa-test-engineer full pass: ci.yml exact commands, all E2E specs, QA-LO-1/LO-2 verified.
- tech-writer docs gate: USER.md + DEPLOY.md final review, ADR-0052 prose check.
- solution-architect review: R10-1 auth design, ADR-0052 consistency, openapi.json
  security scheme review.
- PM exit-criteria verification (§6 below).
- Day 18 target: tag v1.0.0, create GitHub release with desktop artifacts.

---

## 6. Out of scope for v1.0

Everything not listed in §3 above is explicitly out of scope. The following items MUST
NOT be built during this sprint without explicit PM escalation and approval:

| Deferred item | Reason |
|---|---|
| OIDC / multi-user auth | Structural; requires identity provider dependency; post-1.0 |
| Multi-vault UI / vault switcher | Requires multi-vault routing; deferred to post-1.0 |
| Per-vault provider config UI surface | Part of multi-vault; deferred |
| Code signing (CI implementation) | Requires paid certs; guide-only per PM decision |
| UXA-09 (skeleton panel state) | P2; no file conflict risk but effort exceeds cap |
| UXA-10 (Dismiss confirmation modal) | P2; M effort; beyond 3-item UX cap |
| UXA-11 (ActivityBar two-tier status) | P2; beyond 3-item UX cap |
| UXA-12 (ProviderSelector dedup) | P2; beyond 3-item UX cap |
| UXA-13 (stale Settings layout) | P2; needs verification first |
| UXA-19 (graph label dimming on hover) | P3; beyond 3-item UX cap |
| UXA-20 (destructive button standardisation) | P3; beyond 3-item UX cap |
| UXA-21 (hardcoded "more failed tasks" EN string) | P3; beyond 3-item UX cap |
| UXA-22 (Italian error fragment naturalness) | P3; beyond 3-item UX cap |
| UXA-23 (auto-detect checkmark in ConnectScreen) | P3; beyond 3-item UX cap |
| UXA-24 (review queue card density) | P3; beyond 3-item UX cap |
| UXA-25 (Unicode chevrons) | P3; beyond 3-item UX cap |
| UXA-26 (⌘K discoverability) | P3; beyond 3-item UX cap |
| UXA-27 (graph zoom control visibility) | P3; beyond 3-item UX cap |
| UXA-28 (inline keyframe injection) | P3; beyond 3-item UX cap |
| Vault restore / POST /import endpoint | Post-1.0 |
| Any new feature ID not in CLAUDE.md §4 | Never without new ID — anti-scope-creep invariant |

**Permanent invariant blocks (I1–I9 apply unconditionally):** full-rescan, main-thread
force layout, per-token DOM mutation, WYSIWYG/ProseMirror, hardcoded provider or model
ID, unbounded loops, skipping D-artifacts, Tavily, reimplementing local embeddings.

---

## 7. Exit criteria for v1.0 release (EC-M10)

All 4 sign-offs required before tagging `v1.0.0`:
QA-test-engineer + Solution-architect + Tech-writer + Product-manager.

| ID | Criterion |
|---|---|
| EC-M10-1 | All committed items have all ACs green: ADR-0052 accepted, R10-1 (auth middleware), R10-2 (auth UX), R10-3 (signing guide in DEPLOY.md), R10-4 (auto-update verify), R10-5 (mobile/PWA), R10-6 (MkDocs site CI green), QA-v0.9-leftovers (LO-1 + LO-2), UX-v1.0-A/B/C. |
| EC-M10-2 | ci.yml exact commands all exit 0 tree-wide: `cd backend && ruff check app tests`, `cd backend && black --check app tests`, `cd backend && mypy app`, `cd frontend && npx tsc --noEmit`, `cd frontend && npm run lint`, `cd frontend && npm run test`. QA-test-engineer runs these verbatim and signs each off. |
| EC-M10-3 | ER diagram zero-drift: `make er` output matches live schema. `docs/er/schema.mmd` committed and current. |
| EC-M10-4 | `docs/api/openapi.json` regenerated and current: BearerAuth security scheme declared, all routes reference it except the three excluded endpoints (`/health`, `/status`, `/health/detailed`), EdgeDetail schema matches actual response (QA-LO-2 fix confirmed). |
| EC-M10-5 | Mermaid validation loop passes: all `.mmd` files in `docs/architecture/`, `docs/er/`, `docs/sequences/` render without error via `mmdc`. |
| EC-M10-6 | `mkdocs build --strict` exits 0 in CI (R10-6 CI job green). |
| EC-M10-7 | All D5 screenshots current: at minimum `connect-screen-auth.png` (R10-2), `graph-mobile.png` (R10-5), plus all v0.9 screenshots still valid (no regressions). |
| EC-M10-8 | `docs/DEPLOY.md` complete and v1.0-accurate: `SYNAPSE_AUTH_TOKEN` documented with rotation procedure (R10-1), code-signing guide present (R10-3), `make docs-serve` / `make docs-build` documented (R10-6), PWA install instructions (R10-5). Tech-writer sign-off. |
| EC-M10-9 | `docs/USER.md` complete and v1.0-accurate: all features documented through v1.0, "Mobile / PWA" section present (R10-5), auth token usage documented from end-user perspective. Tech-writer sign-off. |
| EC-M10-10 | `vault/wiki/` remains a valid Obsidian vault (I5/K7). Manual spot-check by owner. |
| EC-M10-11 | Auth backward compatibility verified: deploying v1.0.0 with `SYNAPSE_AUTH_TOKEN=""` (or unset) produces identical behaviour to v0.9.0 — no 401s, no behaviour change. QA-test-engineer runs existing v0.9 E2E suite against v1.0 with auth disabled and confirms all specs still pass. |
| EC-M10-12 | `docs/adr/0052-auth-token-model.md` committed and in Accepted status. ADR-0052 linked from the ADR index (R10-6 AC-R10-6-6). |
| EC-M10-13 | GitHub release `v1.0.0` created with desktop artifacts (macOS `.dmg`, Windows `.msi`, Linux `.AppImage`) from the Tauri v2 build pipeline. Release notes list all items in §3. |
| EC-M10-HCP | Human checkpoint: Emanuele verifies in a live session: (a) with `SYNAPSE_AUTH_TOKEN` unset, the app behaves identically to v0.9.0 (no 401 anywhere); (b) with `SYNAPSE_AUTH_TOKEN="test-token"`, a raw `curl GET /pages` without the header returns 401; the frontend shows ConnectScreen token field and connects successfully with the correct token; (c) the ConnectScreen token field is present and functional; (d) Settings > Security shows the token rotation UI; (e) the docs site builds locally via `make docs-serve`; (f) the app is usable on a 375px-wide browser window (panels stack, nav accessible). |

---

## 8. De-scope order (if sprint runs over)

Cut in this order:

1. UX-v1.0-A/B/C as a group (S × 3; no user regression — deferred items are non-breaking UX improvements). Remove all three together, not selectively, to keep the file-conflict surface clean.
2. R10-6 MkDocs CI deploy hint (keep the `mkdocs.yml` and `make docs-serve` target; just remove the CI job addition and the Pages deployment note — reduces devops-engineer scope).
3. R10-5 graph pinch-zoom verification only (keep breakpoints + touch targets; mark the sigma pinch-zoom as "manual verified" in the PR if the Playwright simulation proves unreliable).

R10-1 (auth), R10-2 (auth UX), QA-v0.9-leftovers, and R10-3 (signing guide) are committed and MUST NOT be cut.

---

## 9. Velocity note

v0.9 carried 9 items (2 UX waves + 7 roadmap items) in 2 weeks. v1.0 carries 9 items
(R10-1 through R10-6 + QA-LO-1/LO-2 + UX-v1.0 group) in 3–4 weeks. This is a lighter
sprint by commit density but R10-1 is XL and has a hard sequencing gate (ADR first,
then backend, then frontend). The ADR gate is the primary scheduling risk: if solution-
architect is unavailable Day 1, the entire R10-1/R10-2 chain slips. Escalate to
orchestrator immediately if ADR-0052 is not accepted by end of Day 1.

The sprint is designed slightly underloaded compared to v0.9 pace, which is intentional:
v1.0 is the public release; the QA gate and human checkpoint are the real deadline, not
feature count. Under-scope is acceptable. Over-scope is not.

**Feature IDs touched this sprint:** F1, F15, F16, D1, D6, D6b, D7.
**Invariants with heightened priority:** I6 (auth must not hardcode any provider), I8
(docs-as-DoD — MkDocs site is the culmination), I3 (auth middleware must not add latency
to the streaming chat path beyond a constant-time token comparison).
