# ADR-0052 — Shared Bearer token authentication (single-owner posture); env-only credential, FastAPI middleware, CORS-safe 401

- **Status:** Accepted
- **Date:** 2026-07-03
- **Sprint:** v1.0 (M10 — Distribution & multi-user; R10-1 rescoped from OIDC to a shared token)
- **Features:** F16 (Settings / config surface) · F15 (cross-platform security hardening) ·
  R10-1 (auth middleware) · R10-2 (auth UX — client contract only; UI built by frontend-engineer)
- **Reference:** docs/sprints/SPRINT-v1.0-SCOPE.md §R10-1 / §R10-2 (PM decisions locked) ·
  ADR-0033 (MCP token — salted hash in `vault_state`, source-trust gate) ·
  ADR-0038 (F11 clip token — env bearer, constant-time compare) ·
  ADR-0028 (relative browser API base) · ADR-0047 (desktop runtime server URL + Connect gate) ·
  ADR-0049 (desktop auto-update over GitHub Releases)
- **Invariants owned:** **I6** (nothing hardcoded — the token is an env var; no provider or model
  touched; the auth layer is provider-agnostic and must never branch on backend type) ·
  **I3** (the enforcement is a single `secrets.compare_digest` — a constant-time byte compare — on
  the streaming chat path; NO KDF, NO DB round-trip, NO per-token work; auth adds a fixed, negligible
  cost to `POST /chat/stream`) · **I8** (OpenAPI regenerated: `BearerAuth` scheme declared, all routes
  reference it except the exempt set, which declare `security: []`) · **I1/I5** (vault-write and
  index paths untouched — auth is a transport gate in front of the router, never inside ingest)
- **Author:** solution-architect
- **Implementers:** backend-engineer (`app/auth.py` module, middleware wiring in `main.py`, env var,
  OpenAPI security scheme, pytests) · frontend-engineer (`api/base.ts` `authHeaders()`, per-server
  `localStorage` token, ConnectScreen probe + token field, web 401 gate, Settings › Security)
  · tech-writer (D4 OpenAPI regen, DEPLOY.md Security section, USER.md auth section, D5 screenshot)
- **Gate:** This ADR **GATES all v1.0 auth code** (AC-R10-1-0). No `auth.py`, no middleware, and no
  frontend header injection may be written until this ADR is Accepted.

---

## 1. Context

R10-1 was rescoped by the PM (2026-07-03) from "token/OIDC login + request-scoped vault routing"
to a **single shared Bearer token**. The rationale is settled and NOT re-litigated here (see
SPRINT-v1.0-SCOPE.md §R10-1): Synapse is a **self-hosted single-owner** product on a private
TrueNAS box, reached over **Tailscale mesh** (private) **and Cloudflare Tunnel** (public), by
**one** person. OIDC adds an identity-provider dependency the owner does not have, and multi-user
is a structural commitment that would constrain the data model across every future sprint.
Multi-vault routing stays deferred: the `vault_id` column continues to exist but routing remains
single-vault for 1.0.

This ADR is the **technical contract** the PM decision requires before any code is written. It
specifies the credential model, the enforcement mechanism, the exact exempt set, the response +
CORS interplay, the frontend injection point, and the desktop/updater implications. Where the PM
brief and the live codebase disagree on a name (the brief says "`GET /health`"; the service
actually exposes `GET /status` and `GET /health/detailed` — there is **no** plain `/health`
route, verified in `main.py` + `health.py` + `openapi.json`), **this ADR binds to the real
endpoints.** The exempt set below uses the routes that actually exist.

Two token mechanisms already exist and are **out of scope for this ADR — they keep their own auth**:
- The **MCP HTTP surface** at `/mcp/server` (ADR-0029/0032/0033): salted-hash token in
  `vault_state`, source-trust PRIVATE/PUBLIC classifier, `remote_enabled` flag, 404-floor. This
  is a **mounted sub-app**, not a route on the main router.
- The **clip ingress** `POST /clip` (ADR-0038/0040): its own `CLIP_TOKEN` bearer + origin
  allowlist + body cap, driven by a cross-origin browser extension.

The new auth layer **must not double-gate** either surface. §2.3 spells out the exact path
exclusions.

---

## 2. Decision

### 2.1 Credential model — one shared Bearer token, `SYNAPSE_AUTH_TOKEN`, env-only, fail-OPEN when empty

- A single env var **`SYNAPSE_AUTH_TOKEN`** (string). It is read **once at startup** into
  `app/auth.py` (a new module — SHOULD keep `main.py` surface minimal, per AC-R10-1-1).
- **Empty string or absent ⇒ authentication is DISABLED.** All routes behave exactly as v0.9 —
  no 401s, no behaviour change, no header required. This is the backward-compatible default and is
  the load-bearing compatibility property (EC-M10-11 / EC-M10-HCP-a).
- **Set (non-empty) ⇒ every non-exempt request MUST carry `Authorization: Bearer <token>`**,
  compared **constant-time** with `secrets.compare_digest` against the configured value. Absent
  header, malformed header, or wrong token ⇒ **401** (§2.4).
- The comparison is a raw byte compare on the **presented** token only. **No hashing, no KDF, no
  DB read per request.** This is deliberate and is the I3 guarantee: the auth cost on the hot
  `POST /chat/stream` path is a single `compare_digest`, constant-time, O(len(token)).

**Why env-only, no DB storage, no hashing — justified against the two existing token patterns:**

| Pattern | Where stored | Hashed? | Why |
|---|---|---|---|
| Provider API keys (ADR-0008 / §12) | env only | n/a (never in DB) | Third-party billed keys; a DB/backup leak must not leak them. |
| MCP token (ADR-0033) | `vault_state`, **PBKDF2 salted hash** | yes | **UI-settable at runtime** by the owner ⇒ the app stores it ⇒ it must survive a DB dump without yielding a usable credential ⇒ hash. |
| CLI subscription token (ADR-0043) | `vault_state`, plaintext | no | Must be **replayed OUTBOUND** into the spawned `claude` CLI ⇒ a one-way hash cannot be replayed. |
| **`SYNAPSE_AUTH_TOKEN` (this ADR)** | **env only** | **no** | See below. |

The MCP token is hashed **because it is UI-settable and therefore lives in the database**, where a
`pg_dump` or backup file is a leak surface (ADR-0033 §2.1). `SYNAPSE_AUTH_TOKEN` is the opposite
case on every axis:

1. **It is deployment configuration, not app-issued state.** The owner sets it in
   `docker-compose.yml` / `.env` alongside `DATABASE_URL`, `SEARXNG_URL`, and every other secret.
   It is a peer of the existing env secrets, not of the runtime-settable MCP/clip tokens. Keeping
   it in env means it is **never** in the DB, never in a `pg_dump`, never in a backup, never in git
   — exactly the §12 posture for the highest-blast-radius credential (the one gating the **whole**
   REST API, broader than the MCP surface or the clip ingress).
2. **Hashing would buy nothing here.** Hashing protects a *stored* secret against a *storage*
   leak. This secret is not stored by the app; it lives only in the process environment. There is
   no DB row to leak. A salted hash would add a per-request KDF (ADR-0033 explicitly notes PBKDF2
   is "intentionally slow") to the **hot chat streaming path** for zero security gain — a direct
   I3 violation. Constant-time plaintext compare is both **more secure** (no stored artifact at
   all) **and** faster.
3. **Single-owner posture makes "look up the token later" a non-goal.** There is no multi-user
   rotation workflow. Rotation is an env change + container restart (§2.6). The owner always has
   the token in their compose file.

**Decision: `SYNAPSE_AUTH_TOKEN` is env-only, compared in constant time, never stored, never
hashed.** This honours §12's letter (secret in env, not code, not DB) and its spirit (no
recoverable secret in Postgres) while respecting I3 (no KDF on the hot path).

**No sessions, no cookies, no JWT, no expiry.** The token is a static bearer. Single owner ⇒ no
session lifecycle to manage. (SPRINT-v1.0-SCOPE §R10-1 locked.)

### 2.2 Enforcement — FastAPI HTTP middleware, NOT per-route dependencies

The token is enforced by a single **`app.middleware("http")`** callable installed in `main.py`
(the check itself lives in `app/auth.py`; `main.py` only wires it). It is **not** a
`Depends(verify_token)` on each route and **not** a global `dependencies=[...]` on the app
constructor.

**Why middleware, not per-route dependencies (justified):**

1. **Zero-miss guarantee across 60+ routes.** The service exposes 60+ endpoints across `main.py`
   and several included routers (`sources`, `export`, `costs`, `health`). A per-route dependency
   is a per-route decision — every new route in every future sprint must remember to add it, and a
   forgotten `Depends` is a silently unauthenticated endpoint. A single middleware in front of the
   router is **enforced by construction**: a new route is gated the moment it is registered, with
   no per-route action. For a security boundary, default-secure beats default-open.
2. **It catches paths a route dependency cannot.** A middleware runs for *every* ASGI HTTP scope,
   including 404s (unknown paths), 405s, and — critically — requests to **mounted sub-apps**. This
   lets the middleware make one authoritative decision about the whole surface and explicitly
   *exclude* the MCP mount (§2.3), rather than relying on each router's dependencies.
3. **It composes cleanly with CORS ordering** (§2.4) — a single middleware layer whose position
   relative to `CORSMiddleware` is explicit and testable, versus dependency exceptions that raise
   *inside* the route and interact with exception handlers in subtler ways.
4. **The exempt set is expressed in one place** (§2.3) as a path check, not scattered as
   `security=[]` markers across route decorators. (The OpenAPI `security` markers of §2.5 are a
   *documentation* concern and are separate from enforcement.)

Trade-off acknowledged: a middleware sees the raw path string, not the resolved route object, so
the exempt set is matched on **exact path** (not on route name or tag). This is fine — the exempt
set is small and stable (§2.3) and exact-path matching is the least surprising, least
over-matching rule (it will not accidentally exempt `/status/foo`).

**WebSocket note.** The service uses **NDJSON-over-POST** for chat streaming (`POST /chat/stream`,
ADR-0019 — "not SSE/WebSocket"). There is **no WebSocket route** in the codebase. `POST
/chat/stream` is an ordinary POST and is gated by the HTTP middleware like any other route — the
`Authorization` header rides the POST request. AC-R10-1-3's WebSocket handshake / `?token=` /
close-code-4401 requirement is therefore **not applicable to the current architecture**; it is
recorded here as a **forward constraint**: *if* a WebSocket streaming path is ever added, the
token must be verifiable on the upgrade request (query param `?token=` or first-frame handshake,
reject with close code `4401`) — and that will require its own ADR amendment because ASGI
`websocket` scopes bypass `app.middleware("http")`. For v1.0 the middleware covers 100% of the
live surface.

### 2.3 Exempt set — exact paths, and the mount exclusions (do NOT double-gate MCP or clip)

When `SYNAPSE_AUTH_TOKEN` is set, the middleware **bypasses the token check** for exactly the
following, and **only** these:

**A. Monitoring-safe endpoints (always reachable for liveness/readiness probes):**
- `GET /status` — the real service-health + `data_version` endpoint (the brief's "`GET /health`"
  maps here; there is no plain `/health`).
- `GET /health/detailed` — the per-component health snapshot (R9-2).

*Justification:* container orchestration, the frontend health poll, and external uptime monitors
must reach liveness without a credential. These endpoints expose only non-sensitive posture
(version counter, component up/down) — no page content, no vault data, no config secrets. Exempting
them is standard and safe. **`GET /status` staying exempt is also what lets the desktop
ConnectScreen auto-detect a reachable server before the user has entered a token** (§2.4, §4).

**B. API documentation (decided: EXEMPT):**
- `GET /docs` — Swagger UI.
- `GET /openapi.json` — the OpenAPI schema.
- `GET /redoc` — if present.

*Decision + justification:* these are **exempted**. Reasoning: (1) the schema is not a secret — it
describes routes, not data, and the same information is in the public git repo (`docs/api/openapi.json`);
(2) leaving `/docs` reachable lets the owner sanity-check the API and read the `BearerAuth` scheme
without first authenticating a browser tab that has no way to send a bearer header; (3) it avoids a
confusing state where the docs page loads but every "Try it out" 401s with no obvious remedy. The
schema exposes route shapes only; all data-bearing routes remain gated. *If the owner later wants
the docs gated too (e.g. a fully public tunnel), that is a one-line addition to the exempt
predicate — recorded as a documented knob, not built now.*

**C. CORS preflight:**
- **All `OPTIONS` requests** (any path) bypass the token check.

*Justification:* a CORS preflight is an unauthenticated `OPTIONS` the browser sends **before** the
real request and **cannot** carry an `Authorization` header. If the middleware 401'd the preflight,
every cross-origin call from the PWA (split-origin deployments) would fail before the real request
is ever attempted. The preflight must reach `CORSMiddleware` and return its CORS headers. (§2.4
covers ordering so this actually happens.)

**D. Mount exclusions — MCP and clip keep their OWN token (do NOT double-gate):**
- Any path **prefixed with `/mcp/server`** is **excluded from this middleware entirely**. The MCP
  HTTP surface is a **mounted sub-app** wrapped by its own gate (`_BearerAuthMiddleware` /
  ADR-0033 access gate). Applying `SYNAPSE_AUTH_TOKEN` on top would (a) double-gate it with a
  *different* credential, breaking every existing MCP client, and (b) defeat ADR-0033's
  source-trust PRIVATE/PUBLIC logic and `allow_without_token` posture. The predicate is an exact
  **prefix** check: `path == "/mcp/server" or path.startswith("/mcp/server/")`. **Note the
  boundary:** the `/mcp/*` *management* routes on the main router — `GET /mcp/info`, `PUT
  /mcp/auth`, `PUT /mcp/remote` — are **NOT** the mounted surface; they are ordinary REST routes
  and therefore **ARE** gated by `SYNAPSE_AUTH_TOKEN` like the rest of the API. Only the mounted
  `/mcp/server` sub-app is excluded.
- **`POST /clip`** keeps its own `CLIP_TOKEN` bearer (ADR-0038). It is reached by a **cross-origin
  browser extension** that sends `Authorization: Bearer <CLIP_TOKEN>` — a *different* token from
  `SYNAPSE_AUTH_TOKEN`. Gating `/clip` with `SYNAPSE_AUTH_TOKEN` would break the extension (it has
  no way to know the API token) and duplicate an already-audited gate (S-1..S-6). **Decision:
  `POST /clip` is excluded from this middleware** (exact path). Its own token + origin allowlist +
  body cap remain the gate. The clip **config** routes (`GET`/`PUT /clip/config`) are ordinary
  same-origin REST routes and **ARE** gated by `SYNAPSE_AUTH_TOKEN`.

**Exempt predicate (authoritative summary):**
```
bypass_auth(request) := (
    request.method == "OPTIONS"
    or request.url.path in {"/status", "/health/detailed", "/docs", "/redoc", "/openapi.json"}
    or request.url.path == "/mcp/server" or request.url.path.startswith("/mcp/server/")
    or request.url.path == "/clip"
)
```
Everything not matched by `bypass_auth` requires the bearer token when `SYNAPSE_AUTH_TOKEN` is set.
The exempt set is defined as a **named module-level constant** in `auth.py` (I6 — no scattered
literals); the two mount-exclusion prefixes reuse the existing `MCP_MOUNT_PATH` constant.

### 2.4 Response + CORS interplay — 401 body, `WWW-Authenticate`, and exact middleware ORDER

**401 response (auth failure):**
```
HTTP/1.1 401 Unauthorized
WWW-Authenticate: Bearer
Content-Type: application/json

{"error": "unauthorized", "hint": "Set Authorization: Bearer <token>"}
```
- The body shape is the PM-locked contract (SPRINT-v1.0-SCOPE §R10-1:
  `{"error": "unauthorized", "hint": "..."}`). The task brief's shorthand "`401 {detail}`" is
  satisfied by this concrete body; the hint field is the human-actionable part.
- `WWW-Authenticate: Bearer` is included per RFC 6750 so the response is a well-formed bearer
  challenge (and so any generic HTTP client can present the standard auth prompt).
- **The token value is NEVER echoed**, not even a prefix (Do-NOT §6).

**CORS interplay — the 401 MUST still carry CORS headers, which fixes the middleware ORDER.**

The failure mode to avoid: the browser makes a cross-origin request with a bad/absent token; the
auth middleware returns a bare 401 with no `Access-Control-Allow-Origin` header; the browser then
**hides the 401 from JavaScript** behind an opaque CORS error, so the frontend cannot detect "401"
and cannot show the token prompt (R10-2). To detect 401 in the browser (AC-R10-2-2), the 401
response itself must carry CORS headers.

In Starlette/FastAPI, **middlewares wrap in reverse order of registration** — the middleware added
**last** is the **outermost** layer (it sees the request first and the response last). Therefore
the required order is:

1. **Register `CORSMiddleware` LAST** (so it is **outermost**). `CORSMiddleware` is already
   registered in `main.py` (§the existing `app.add_middleware(CORSMiddleware, ...)` block).
2. **Register the auth middleware BEFORE `CORSMiddleware`** in source order (so auth is **inner**,
   CORS wraps it).

Concretely in `main.py`, source order becomes:
```python
# auth first in source order  ⇒  INNER layer (runs after CORS on the way in)
app.add_middleware(SynapseAuthMiddleware)      # ADR-0052 — the token gate
# CORS last in source order   ⇒  OUTER layer (adds CORS headers to EVERY response, incl. 401)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins_list, ...)
```
Because CORS is outermost, the auth middleware's 401 passes back **out through** `CORSMiddleware`,
which stamps `Access-Control-Allow-Origin` (and friends) onto it. The browser then sees a real,
readable 401 and the frontend interceptor fires (§4). This also makes the `OPTIONS` exemption
coherent: the preflight is exempted by auth (§2.3-C) **and** CORS is outermost, so preflights are
answered correctly regardless.

> Implementation note for backend-engineer: the current `main.py` uses
> `app.add_middleware(CORSMiddleware, ...)`. Add `app.add_middleware(SynapseAuthMiddleware)` on the
> line **immediately before** that call so registration order yields CORS-outermost / auth-inner. A
> pytest MUST assert a 401 to a cross-origin request carries `access-control-allow-origin`
> (AC below). Do NOT implement the gate as a bare `@app.middleware("http")` function placed after
> the CORS block — that would reverse the layering. Use `add_middleware` with the ordering above,
> or place the `@app.middleware("http")` decorator call so its registration precedes the CORS
> `add_middleware`. Verify with the CORS-on-401 test, not by reading the source.

### 2.5 OpenAPI security scheme (I8)

`docs/api/openapi.json` MUST be regenerated so that:
- `components.securitySchemes.BearerAuth` is declared:
  `{"type": "http", "scheme": "bearer"}`.
- Every route **except** the exempt set references it: `security: [{"BearerAuth": []}]`.
- The exempt routes (`GET /status`, `GET /health/detailed`) declare **`security: []`** explicitly
  (the FastAPI idiom for "no auth on this route") so the schema is truthful about what is gated.
- `/docs`, `/openapi.json`, `/redoc` are framework-served and are documented as exempt in the ADR
  text (they do not appear as `paths` entries that need a `security` marker).
- The `/mcp/server` mounted sub-app and `POST /clip` retain their existing OpenAPI posture — this
  ADR does not add `BearerAuth` to them (they use their own tokens).

The FastAPI-level wiring is a global security requirement plus per-route `security=[]` overrides on
the exempt routes; this is a **documentation** concern and is independent of the middleware
enforcement (§2.2). Both must agree (the middleware is the enforcer; OpenAPI describes it).

### 2.6 Rotation — the client/server asymmetry, stated precisely

There is a deliberate asymmetry, and the UI must not pretend otherwise:

- **Server-side rotation = env change + restart.** The owner edits `SYNAPSE_AUTH_TOKEN` in
  `docker-compose.yml` (or `.env`) and restarts the container. There is **no server-side rotation
  endpoint** — the token is env-managed by construction (§2.1). No API call can change it.
- **Client-side "rotation" = updating the STORED client token.** Settings › Security "Rotate
  token" (§4) updates the token held in the browser's `localStorage` for the current server. It
  **calls nothing server-side.** It is purely "I changed the server env; now tell my client the
  new value."

The Settings UI copy MUST make this explicit (AC-R10-2-5): *"To rotate the server token: set
`SYNAPSE_AUTH_TOKEN` to a new value in your docker-compose.yml and restart the container. Then
enter the new token here."* This asymmetry is intentional (single-owner, env-managed) and is the
correct posture for the audience — it keeps the secret out of the DB (§2.1) at the cost of a
restart, which the owner performs anyway for any config change.

---

## 3. Configuration

| Env var | Default | Description |
|---|---|---|
| `SYNAPSE_AUTH_TOKEN` | `""` (empty) | Shared Bearer token for the REST API. **Empty/absent = auth DISABLED** (backward-compatible). Set (non-empty) = every non-exempt route requires `Authorization: Bearer <token>`. Constant-time compared; never stored, never hashed, never logged. Recommend ≥ 32 chars (document in DEPLOY.md). |

No DB column. No migration. No D2/ER change (the credential lives in env, not in `vault_state` —
the contrast with ADR-0033/0040/0043, which each added a `vault_state` column, is the whole point
of §2.1). D4 (OpenAPI) regenerated (§2.5). D6b (DEPLOY.md) gains the Security section (AC-R10-1-4).

---

## 4. Frontend contract (R10-2 — API client + ConnectScreen + Settings)

**There is no shared fetch wrapper today.** The shared module is `frontend/src/api/base.ts`, which
exposes `apiBase()` (call-time server-URL resolution, ADR-0047). Every `frontend/src/api/*.ts`
client builds URLs from `apiBase()`. The `Authorization` header MUST be injected in **exactly one
place** — `base.ts` — and **no component constructs the header directly** (SPRINT-v1.0-SCOPE
§R10-2, the key architectural rule).

**4.1 Token storage — per server, in `localStorage`, never in Zustand.**
- Key: **`synapse.authToken`** (namespaced like the existing `synapse.serverUrl`, `synapse.servers`
  in `base.ts`). It survives across sessions exactly like `serverUrl` does (ADR-0047 semantics).
  *(The PM brief also names a per-server-keyed form `synapse_token_{serverUrl}`; the implementer
  MAY key per server URL to match multi-server support — the contract is: **one namespaced key,
  read by `base.ts` at request time, scoped to the current server**. Whichever form is chosen must
  be a single constant in `base.ts`, mirroring `LS_SERVER_URL`.)*
- **Never** stored in Zustand state (avoids accidental serialization into persisted store blobs).
  Read at request time only.

**4.2 The single injection point — `authHeaders()` in `base.ts`.**
Add a new exported helper `authHeaders(): Record<string, string>` (or a thin `fetchWithAuth`
wrapper) in `base.ts` that returns `{ Authorization: "Bearer <token>" }` when a token is stored
for the current server, and `{}` otherwise. Every client merges `authHeaders()` into its request
headers. This covers **all** REST calls, the NDJSON `POST /chat/stream` fetch, the graph poll, and
the 30-second `GET /health/detailed` health poll (AC-R10-2-8) — because they all go through the
`base.ts`-derived request path. **`GET /status` and `GET /health/detailed` are exempt server-side**
(§2.3), so the header is harmless on them (an exempt route ignores the header) — the client may
send it uniformly; it does no harm.

**4.3 401 handling — one interceptor, not per-component.**
When any API call returns **401**, the client layer (a single response check colocated with the
`base.ts`/fetch path) MUST: clear the stored token for the current server, and trigger the Zustand
action that resets the connected-server state so the app shows ConnectScreen with an
`"Authentication required"` error (AC-R10-2-2). Implemented once, at the client level.

**4.4 ConnectScreen — probe `/status` (exempt), then a protected endpoint to detect 401.**
- On connect, ConnectScreen first probes **`GET /status`** (exempt — always answers if the server
  is reachable, even with auth on and no token) to confirm the server exists and get
  `data_version`. This is why `/status` stays exempt (§2.3-A) and why the updater/desktop
  auto-detect still works (§5).
- It then calls a **protected** endpoint (e.g. `GET /pages`). If that returns **401**, the server
  has auth enabled and the entered/stored token is missing or wrong ⇒ show the token field with the
  inline error (AC-R10-2-4). If it returns 200, the connection is authenticated (or auth is
  disabled) ⇒ proceed and persist the token (if non-empty).
- The token field is shown **unconditionally** below the server-URL field (the owner may want to
  set the token before connecting), `type=password` with a show/hide `<Eye>`/`<EyeOff>` toggle
  (AC-R10-2-3).

**4.5 Web (non-Tauri) gets the same gate.**
The 401 → ConnectScreen flow (§4.3–4.4) is **not desktop-specific**. When the web/PWA build
receives a 401, it shows the same minimal token prompt: the ConnectScreen (or a minimal inline
token entry over the current server) with the token field auto-focused and the
`"Invalid or missing access token…"` inline error (`var(--syn-red)`, `role="alert"`, matching the
UXA-16 pattern). The web build already knows its server (same-origin, ADR-0028), so the "server
URL" field MAY be pre-filled/read-only while the token field is the actionable control. The UX
contract: **any 401, on any platform, surfaces a token entry that, once filled, retries and
proceeds** — no dead-end error.

**4.6 Settings › Security — client-side rotation only.**
A new "Security" section (Settings sidebar) shows: (a) the current server URL (read-only, for
context); (b) a "Rotate token" field where the owner pastes a new token and clicks "Update",
which replaces the stored token in `localStorage` and updates the in-memory value — **no server
call** (§2.6). Plus the info banner from §2.6 explaining the server-side procedure (env + restart).
i18n EN/IT parity for all new keys.

---

## 5. Updater / desktop implications

- **The auto-updater is unaffected.** It hits **GitHub Releases** (ADR-0049), not the Synapse
  backend. `SYNAPSE_AUTH_TOKEN` gates the backend REST API; GitHub is an external origin with its
  own (no) auth. The update check, download of `latest.json`, and artifact fetch never touch the
  gated API. **The token MUST NEVER appear in any `latest.json` flow** (Do-NOT §6).
- **ConnectScreen auto-detect still works** because `GET /status` is exempt (§2.3-A / §4.4): a
  freshly-launched desktop app can reach `/status` on the configured server without a token,
  confirm reachability + `data_version`, and *then* discover (via the protected probe) whether a
  token is required. Auth-on does not break the Connect gate; it adds a token step after detection.
- No change to the Tauri shell, the service worker, or the split-origin PWA plumbing (ADR-0028/0039)
  — the token is a request header injected in `base.ts`, transparent to all of them.

---

## 6. Do-NOT list

1. **DO NOT** hash or store `SYNAPSE_AUTH_TOKEN` in the DB — it is env-only by design (§2.1);
   hashing buys nothing and adds a KDF to the hot path (I3). Contrast ADR-0033 (UI-settable ⇒ DB ⇒
   hash); this credential is deployment config, not app state.
2. **DO NOT** log the token — not the value, not a prefix, not the hash-of (there is no hash). Not
   at INFO, not at DEBUG, not in an exception message. (Mirrors ADR-0038 Do-NOT §4.)
3. **DO NOT** put the token in a URL, a query string, a path, or a redirect. Bearer header only.
   (The one future exception — a WebSocket `?token=` — is explicitly deferred and out of scope, §2.2.)
4. **DO NOT** include the token in any `latest.json` / updater / GitHub Release flow (§5).
5. **DO NOT** enforce auth via per-route `Depends` or a global `dependencies=[...]` — use the
   middleware, so new routes are gated by construction (§2.2); a forgotten dependency is a silent
   hole.
6. **DO NOT** double-gate `/mcp/server` (ADR-0033 keeps its own token) or `POST /clip` (ADR-0038
   keeps `CLIP_TOKEN`). Exclude both from this middleware (§2.3-D). But DO gate the *management*
   routes `GET /mcp/info`, `PUT /mcp/auth`, `PUT /mcp/remote`, `GET`/`PUT /clip/config` (ordinary
   REST routes).
7. **DO NOT** 401 the CORS preflight — `OPTIONS` is always exempt (§2.3-C), and CORS must be the
   **outermost** middleware so even a real 401 carries CORS headers (§2.4). Verify with the
   CORS-on-401 test, not by reading the source.
8. **DO NOT** break backward compatibility: empty/unset `SYNAPSE_AUTH_TOKEN` MUST be byte-for-byte
   v0.9 behaviour (no 401s anywhere). The disabled path adds zero required headers (EC-M10-11).
9. **DO NOT** compare the token with `==` — use `secrets.compare_digest` (constant-time) to close
   the timing side-channel (mirrors ADR-0038 §2.2).
10. **DO NOT** store the token in Zustand or any persisted store blob — `base.ts` `localStorage`
    only, read at request time (§4.1); no component constructs the `Authorization` header directly.

---

## 7. Acceptance checks (DoD — maps to AC-R10-1-*, AC-R10-2-*)

1. **Disabled default (backward compat).** With `SYNAPSE_AUTH_TOKEN=""`, `GET /pages` returns 200
   with no `Authorization` header; the full v0.9 E2E suite passes unchanged (AC-R10-1-1a,
   EC-M10-11).
2. **Enabled — reject.** With `SYNAPSE_AUTH_TOKEN="test-token"`, `GET /pages` with no header ⇒ 401
   with body `{"error":"unauthorized","hint":...}` and `WWW-Authenticate: Bearer` (AC-R10-1-1b).
3. **Enabled — accept.** Same, with correct Bearer ⇒ 200 (AC-R10-1-1c).
4. **Exempt set.** With the token set, `GET /status` and `GET /health/detailed` return 200 with no
   header (AC-R10-1-2). `/docs` and `/openapi.json` load with no header.
5. **CORS-on-401 (the ordering test).** A cross-origin request with a bad/absent token returns 401
   **carrying** `access-control-allow-origin` (proves CORS is outermost, §2.4).
6. **OPTIONS exempt.** An `OPTIONS` preflight to any gated route returns the CORS preflight
   response (not 401), with the token set.
7. **Mount not double-gated.** With `SYNAPSE_AUTH_TOKEN` set and no `Authorization` header, a
   request to `/mcp/server` is handled by the MCP gate's own logic (ADR-0033), NOT 401'd by this
   middleware; `POST /clip` is handled by its own `CLIP_TOKEN` gate. `GET /mcp/info` and
   `GET /clip/config` ARE 401'd without the API token.
8. **No secret leak.** `grep` of logs shows no token value/prefix; no response body ever echoes it.
9. **OpenAPI (I8).** `docs/api/openapi.json` declares `BearerAuth`; all routes reference it except
   `/status` and `/health/detailed`, which carry `security: []`; `make openapi` is drift-clean
   (EC-M10-4).
10. **mypy strict / lint.** `app/auth.py` passes `ruff` + `black --check` + `mypy` (strict), no
    `Any`, `verify_token` fully typed (AC-R10-1-5).
11. **Frontend injection (R10-2).** With a token in `localStorage`, the injected `Authorization`
    header is present on REST, chat-stream, graph poll, and the health poll; absent when no token
    stored (AC-R10-2-1, -8). A 401 clears the stored token and resets connected-server state via
    the single interceptor (AC-R10-2-2).
12. **ConnectScreen + Settings.** `/status` probe then protected probe drives the token field;
    field is password + show/hide; Settings › Security rotates the *client* token with no server
    call and shows the env-restart banner; EN/IT parity (AC-R10-2-3..6). D5 screenshot
    `connect-screen-auth.png` (AC-R10-2-7).

---

## 8. Consequences

**Positive** — the owner gets a real, zero-miss access gate for exposing Synapse on a public tunnel,
with **full backward compatibility** (unset = v0.9). The credential stays in env (never DB, never a
`pg_dump`, never git — §12 for the highest-blast-radius secret) and is enforced by a single
constant-time compare that respects I3 on the streaming path. One middleware gates 60+ routes by
construction; new routes are secure by default. The MCP and clip surfaces keep their own,
already-audited, auth (no double-gating, no regression). One injection point in `base.ts` keeps the
header contract in a single file. No migration, no D2 change.

**Trade-offs (explicit)** —
- **Server-side rotation requires a restart** (§2.6). Accepted: single-owner, env-managed, keeps
  the secret out of the DB; the owner restarts for any config change anyway. The client/server
  asymmetry is documented in the Settings copy so it is not surprising.
- **One shared token, no per-user identity, no audit-per-user.** This is the PM-locked scope; OIDC
  and multi-user are deferred (SPRINT-v1.0-SCOPE §R10-1). Single-vault routing is retained.
- **`/docs` + `/openapi.json` are exempt** (§2.3-B) — the schema (route shapes, not data) is
  reachable without a token. Accepted (it is already public in git); a documented knob can gate it
  if a future deployment needs it.
- **Middleware matches on exact path, not route object** (§2.2). The exempt set is small, stable,
  and a named constant; exact-path matching avoids over-exemption.
- **WebSocket handshake auth is not built** — there is no WebSocket route today (ADR-0019 is
  POST-NDJSON). A forward constraint is recorded for if/when one is added (§2.2); it will need an
  ADR amendment because `websocket` scopes bypass `app.middleware("http")`.

**Invariant check** — **I6:** the token is an env var; the auth layer never branches on provider
type, never hardcodes a backend/model, uses named constants for the exempt set + mount prefixes.
**I3:** enforcement is a single `secrets.compare_digest` — no KDF, no DB read, no per-token work —
on `POST /chat/stream` and every other route. **I8:** OpenAPI regenerated (`BearerAuth` scheme +
per-route `security`); DEPLOY.md Security section; D5 screenshot. **I1/I5:** ingest, index, and
vault-write paths untouched — auth is a transport gate in front of the router. **I2/I4/I7/I9:**
untouched. The §12 "no secrets in DB" rule is honoured by keeping the credential in env (unlike the
UI-settable MCP/clip/CLI tokens, which had to live in `vault_state` and therefore had to be
hashed/handled specially — §2.1 makes the categorical distinction).
