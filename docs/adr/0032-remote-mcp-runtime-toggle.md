# ADR-0032 — Remote MCP runtime toggle: persisted `remote_mcp_enabled` flag, always-mount + 404-gate, no remount

- **Status:** Accepted (owner decided 2026-06-29: "real toggle" — a persisted runtime flag, not an env-only switch; bearer token remains the mandatory security floor; `write_page` remote control stays env-only / out of scope)
- **Superseded in part by ADR-0033** (2026-06-30): §2.4's token-floor clamp is now allow-aware — remote can be enabled when EITHER a token is configured OR `mcp_allow_without_token=true` (private sources only). The clamp still applies for public (Cloudflare tunnel) requests, which always require a token.
- **Date:** 2026-06-29
- **Sprint:** v0.5 (Feature — make the API + MCP settings tab "app-like": enable/disable the remote MCP endpoint and show its connection URL)
- **Feature:** F1-MCP-UI (Amendment — owner request, Emanuele Chiummo) · builds on ADR-0029 (remote MCP over HTTP) and ADR-0027 (`/mcp/info` introspection + read-only API+MCP panel)
- **Invariants owned:** **I9** (reuse the existing config store + existing FastMCP mount; add NO new MCP capability, NO second writer) · **I6** (nothing hardcoded — `mount_path` comes from the server; no provider touched) · **I1/I5** (no vault-write-path change — `write_page` still routes through `write_wiki_page` when enabled) · I3 (UI: a toggle + a fetch/PUT — no heavy render, no store churn) · I7 (no new loop)
- **Author:** solution-architect
- **Implementers:** backend-engineer (column + migration + flag cache + middleware gate + `PUT /mcp/remote` + `/mcp/info` fields) · frontend-engineer (toggle control + URL display + `setRemoteMcpEnabled` client + i18n) · tech-writer (D2 ER regen note, D4 OpenAPI, D6b deploy note) · devops-engineer (migration is run by the standard `alembic upgrade head` — a one-line note only, see §3)

---

## 1. Context

ADR-0029 shipped the remote MCP surface: `build_http_mcp()` (`backend/app/mcp/server.py`)
produces a FastMCP Streamable-HTTP ASGI app, mounted at `/mcp/server` in
`backend/app/main.py` **only when `MCP_AUTH_TOKEN` is set** (fail-closed), wrapped by
`_BearerAuthMiddleware` (constant-time `hmac.compare_digest`). `settings.mcp_http_enabled`
is derived `bool(mcp_auth_token)`. `MCP_REMOTE_WRITE_ENABLED` (default false) controls whether
`write_page` is on the HTTP surface.

ADR-0027 made the API + MCP settings panel (`SectionApiMcp` in
`frontend/src/components/settings/SettingsPanel.tsx`) **display-only** (I9): fetch-on-mount,
shows transport + entry command + Claude Desktop snippet + tools list. No config-write.

**Owner request (locked):** the API + MCP tab must let the owner **enable/disable the remote
MCP endpoint** and **see the connection URL when active** — "app-like". The owner explicitly
chose the **real toggle** option (a persisted runtime flag), not an env-only restart switch.

**Two facts the design must honour:**

1. **The bearer token is the security floor.** A vault-mutating-capable public endpoint must
   never be reachable without auth. So: **no token configured ⇒ remote stays OFF regardless of
   the toggle**, and the UI must tell the user to set a token first. The runtime flag can only
   ever *narrow* exposure below what the token would allow — it can never open exposure without
   a token.

2. **`write_page` remote control is out of scope.** The owner did NOT ask for UI write-control.
   `MCP_REMOTE_WRITE_ENABLED` stays env-controlled exactly as today (ADR-0029 §2.3). This ADR
   touches only the *reachability* of the surface, not which tools it carries.

**State at write time (verified in code):** `McpInfoResponse` (`main.py` ~1719) already carries
`http_enabled` and `remote_write_enabled` (ADR-0029 §2.5 was implemented). The **frontend**
`McpInfoResponse` interface (`frontend/src/api/providerClient.ts:123`) does **not** yet carry
those, nor the new fields this ADR adds. The mount is built once at module load and mounted once
(`main.py:113`, `:298`); there is no per-request gate today.

---

## 2. Decision

### 2.1 Storage — reuse `vault_state`; add one nullable boolean column (I9)

There is **no app-global KV / app-settings table** today. The candidate stores are
`provider_config` (per-scope F17 rows — wrong shape; it is a multi-row routing table, not a
singleton posture) and `vault_state` (one row per `VAULT_ID`, seeded idempotently at startup,
already read on the request path for `data_version`).

**Decision:** reuse **`vault_state`**. Add **one nullable boolean column**:

```
vault_state.remote_mcp_enabled : Mapped[bool]  (Boolean, nullable=False,
                                                default=False, server_default=sa_text("false"))
```

- **Why `vault_state` and not a new table:** the remote MCP surface is a **single origin-level
  posture** for this single-process, single-`VAULT_ID` deployment. `vault_state` already holds
  exactly one row for the active vault, is seeded idempotently (`_seed_vault_state`, `main.py:3061`),
  and is already loaded at startup. A boolean posture column rides that row with zero new table,
  zero new service. (I9 — do not add infrastructure we do not need.)
- **Why not `provider_config`:** that table is keyed by `(scope, operation, vault_id)` for F17
  routing resolution. A boolean posture has no scope semantics; forcing it in would pollute the
  routing resolver. Rejected.
- **Default = OFF (`false`).** A fresh DB, an upgrade of an existing DB (the column backfills
  `false`), and the seed path all yield remote-OFF. The owner must explicitly turn it on.
- **Naming clarity:** the column is named `remote_mcp_enabled` to read unambiguously as "the
  remote (HTTP) MCP surface is enabled at runtime", distinct from `mcp_http_enabled` (the
  derived "a token is configured" capability gate).

**This DOES add an Alembic migration** — a new revision `0011_vault_state_remote_mcp_enabled`
that `ALTER TABLE vault_state ADD COLUMN remote_mcp_enabled BOOLEAN NOT NULL DEFAULT false`.
**D2 / ER MUST regenerate** (`make er`) and match the live schema (I8). This is the one and only
schema change in this ADR. (Flagged for tech-writer + the docs gate.)

### 2.2 The flag is read through an in-process cache, DB-backed (hot-path safety)

The middleware gate (§2.3) runs on **every** `/mcp/server` request. Reading the DB per MCP
request would add a query to a hot path and couple request latency to the DB. Instead:

- A module-level **`RemoteMcpFlag`** holder (a tiny object with an `asyncio.Lock` + a cached
  `bool`) is the single read source for the middleware. It is **not** a new persistence layer —
  the DB column is the source of truth; the holder is a process cache of it.
- **Load at startup:** in `lifespan`, after `_seed_vault_state()`, read
  `vault_state.remote_mcp_enabled` for the active `VAULT_ID` into the holder (one query, once).
- **Refresh on write:** the `PUT /mcp/remote` handler (§2.3) writes the column **and** updates
  the holder in the same call, so the gate reflects the new value immediately with no DB read on
  the request path. (Single-process deployment ⇒ in-memory cache and DB never diverge.)
- The middleware reads `RemoteMcpFlag.is_enabled()` — an in-memory bool — synchronously fast.

This keeps the gate O(1) in-memory while persistence survives restart.

### 2.3 Gating — ALWAYS mount when a token is set; the middleware 404-gates on the runtime flag (NO remount)

**The mount/unmount approach is rejected.** FastMCP's `http_app()` carries its own Starlette
lifespan that starts/stops the StreamableHTTP **session manager**; that lifespan is entered once
in the FastAPI `lifespan` (`main.py:229`). Mounting/unmounting the sub-app at runtime would mean
starting/stopping that session manager mid-process — fragile, and Starlette has no first-class
runtime-unmount. We do **not** go there.

**Decision — always-mount + 404-gate (no remount):**

- The mount condition is **unchanged from ADR-0029**: when `MCP_AUTH_TOKEN` is set, the
  sub-app is built once and mounted once at `/mcp/server`; its lifespan/session-manager is
  started once. When the token is unset, nothing is mounted (fail-closed, exactly as today).
- `_BearerAuthMiddleware` gains a **flag check that runs before the bearer check**, on HTTP
  scopes only:
  - **Runtime flag OFF** ⇒ return **`404 Not Found`** — byte-identical to the path not being
    mounted. This avoids an information leak: a probe cannot distinguish "remote disabled" from
    "no MCP here at all", and in particular a 404 (vs 401) does not advertise that a protected
    MCP surface exists behind the toggle.
  - **Runtime flag ON** ⇒ enforce the bearer token exactly as today (constant-time compare;
    missing/wrong ⇒ 401).
- **Lifespan / WebSocket scopes are passed through unconditionally** (unchanged) so the session
  manager always starts/stops correctly. **The session manager runs even when the flag is OFF** —
  that is intentional and correct: the gate blocks *request routing* (returns 404 before the
  request reaches the sub-app), it does not stop the session manager. There is no session leak
  because no MCP session is ever established while the gate 404s (the request never reaches the
  protocol layer).

This means the StreamableHTTP session manager is "mounted + lifespan-started once" and the
runtime toggle is a pure request-routing decision in front of it. No remount, no
session-manager restart, no lifespan churn.

### 2.4 Config-write endpoint — `PUT /mcp/remote` (the toggle), token-floor clamp

Add one REST route to `backend/app/main.py`:

```
PUT /mcp/remote
  request  body: { "enabled": bool }
  response body: McpRemoteStateResponse {
      remote_enabled:  bool,   # the resulting persisted runtime flag (post-clamp)
      token_configured: bool,  # whether MCP_AUTH_TOKEN is set (the floor)
      mount_path:      str,    # "/mcp/server" — so the UI can rebuild the URL
      clamped:         bool    # true iff the request asked enabled=true but no token ⇒ forced off
  }
```

**Token-floor clamp (mandatory):**
- If `settings.mcp_auth_token` is **unset** and the body is `enabled=true`, the handler
  **refuses to enable**: it persists `remote_mcp_enabled = false`, sets `clamped = true`, and
  returns `remote_enabled=false, token_configured=false`. Remote can never be enabled without
  the auth floor. (HTTP status: **200** with `clamped=true` — the request is well-formed and the
  server's posture is reported truthfully; the UI renders a "set a token first" hint from
  `token_configured=false`. Returning 200-with-clamp rather than 4xx keeps the toggle's response
  shape uniform for the UI, which always reads back the authoritative posture.)
- If a token IS set: persist the requested value, refresh the in-process holder (§2.2), return it.
- `enabled=false` always succeeds (turning off needs no token).

**Side effect on token state:** if the token is later unset (env change + restart), the persisted
`remote_mcp_enabled` may still be `true` in the DB, but with no token the sub-app is **not mounted
at all** (§2.3, ADR-0029) ⇒ `/mcp/server` 404s regardless. The stored `true` is dormant and
re-applies only if a token is restored. This is correct fail-closed behaviour; `/mcp/info`
(§2.5) reports `remote_enabled` AND `token_configured` so the UI never shows a misleading "ON".

**Auth on the toggle endpoint itself.** The rest of the same-origin REST API is unauthenticated
(ADR-0028 — relative base, server-only proxy target; the threat model is a single-user
self-hosted app behind **Tailscale mesh + Cloudflare Tunnel**, CLAUDE.md §1). This endpoint
**toggles public exposure**, so it warrants thought:

- **Decision: leave it same-origin / unauthenticated, consistent with the rest of the REST API.**
  Rationale: (a) reaching the REST API at all already requires being on the Tailscale mesh or
  past the Cloudflare Tunnel — the network perimeter is the gate for *every* REST route, and this
  route is no more sensitive than, e.g., `POST /ingest` which mutates the vault; (b) the **bearer
  token remains the floor** for the MCP surface itself — flipping the flag ON without a token is
  clamped to OFF (above), so the worst an unauthenticated toggle can do is *expose a
  bearer-protected surface*, not an open one; (c) adding bespoke auth to one REST route would
  diverge from the uniform same-origin posture (ADR-0028) for marginal gain in this threat model.
- **Stated trade-off:** if the owner later puts the *web UI* behind Cloudflare Access (edge auth
  for the whole origin), the toggle inherits that protection for free — the preferred hardening
  path, and a pure-ops change (no code). We explicitly do **not** invent app-level auth for a
  single REST route here. If multi-user is ever on the table, this route (and the rest of the API)
  gets real auth together — out of scope now.

### 2.5 `/mcp/info` additions (UI reads posture + builds the URL)

`McpInfoResponse` (backend `main.py`, frontend `providerClient.ts`) gains **three** read-only
fields (token is NEVER returned):

| Field | Type | Source | Meaning |
|-------|------|--------|---------|
| `token_configured` | bool | `bool(settings.mcp_auth_token)` | True iff a bearer token is set (the floor). Never the token. |
| `remote_enabled`   | bool | `RemoteMcpFlag.is_enabled()` (the persisted runtime flag) | The runtime toggle state. |
| `mount_path`       | str  | the mount constant `"/mcp/server"` (a module constant, not a JSX/handler literal — I6) | So the UI builds the URL as `window.location.origin + mount_path`. |

- `http_enabled` (already present) stays = `bool(mcp_auth_token)` = `token_configured`. To avoid
  two names for one fact, `http_enabled` is **retained for backward compat** and
  `token_configured` is the named-for-UI alias; both reflect the same boolean. (The UI uses
  `token_configured` + `remote_enabled` for its three-state display; `http_enabled` stays so no
  existing consumer breaks.)
- `remote_write_enabled` (already present) unchanged — env-driven, out of scope here.
- **The URL is composed client-side** from `origin + mount_path`; the server never emits a host
  (it does not know the public tunnel host). No hostname is hardcoded anywhere (I6).

**Frontend type parity:** `frontend/src/api/providerClient.ts` `McpInfoResponse` must add
`http_enabled`, `remote_write_enabled` (currently missing — they exist on the wire but not the
type), plus the new `token_configured`, `remote_enabled`, `mount_path`. Add a typed
`setRemoteMcpEnabled(enabled: boolean): Promise<McpRemoteStateResponse>` client for `PUT /mcp/remote`.

### 2.6 Reconcile with ADR-0027 I9 ("display-only" panel) — scoped, documented exception

ADR-0027 §2.4 and its Do-NOT list (§4 items 3–4) made the API + MCP panel **display-only — no
tool invocation, no config-write**. This control makes the panel **write one piece of config**
(the runtime toggle). That is a **deliberate, narrowly-scoped exception**, and this ADR documents
it explicitly:

- **ADR-0032 supersedes ADR-0027's "no config-write" clause for THIS SINGLE control only.**
  Everything else in the panel stays exactly as ADR-0027 specified: transport, entry command,
  Claude Desktop snippet, and tools list remain **read-only display**.
- **The I9 headline is preserved.** ADR-0027's I9 concern was "do not reinvent MCP / do not add a
  second MCP server / do not invoke MCP tools / do not duplicate `server.py` logic." None of that
  changes: the toggle invokes **no MCP tool**, adds **no MCP capability**, and duplicates **no**
  tool logic. It flips a posture flag that gates the *existing* ADR-0029 mount. The only ADR-0027
  guardrail relaxed is "no config-write," and only for `remote_mcp_enabled`.
- **The panel still cannot mutate the vault or call a tool** — it has exactly one new write path
  (`PUT /mcp/remote`) and no others. ADR-0027 §4 items 1, 2, 3, 5, 6 (no hardcoded tools/server,
  no tool invocation, no second server) all still hold.

This interplay is the only invariant tension in the ADR and it is resolved by scoping, not by
trading I9 away.

### 2.7 Frontend control (I3 — no heavy render)

`SectionApiMcp` gains a small **Connection / Remote** sub-block above the existing read-only rows:

- A **toggle** (switch) bound to `info.remote_enabled`.
- **Three display states**, driven by `token_configured` + `remote_enabled`:
  1. `token_configured === false` → toggle **disabled**, with an inline hint
     (i18n `settings.apiMcp.remote.needToken`): "Set `MCP_AUTH_TOKEN` to enable the remote
     endpoint." No URL shown.
  2. `token_configured === true && remote_enabled === false` → toggle **off, enabled**; no URL.
  3. `token_configured === true && remote_enabled === true` → toggle **on**; show the **connection
     URL** as a mono row `window.location.origin + info.mount_path` with a copy button (mirrors
     the existing snippet copy control). No token displayed.
- Flipping the toggle calls `setRemoteMcpEnabled(next)`, then re-reads `/mcp/info` (or applies the
  returned `McpRemoteStateResponse`) to refresh the three-state display. **Local component state +
  fetch/PUT only — no new Zustand store, no store churn (I3, mirrors ADR-0027 §2.4).** The render
  cost is a switch + one mono row; no heavy work.
- **i18n:** new keys under `settings.apiMcp.remote.*` in `en.json` + `it.json` (key-set parity
  gate). The URL and the (absent) token are **never** i18n keys — only surrounding labels are.

---

## 3. New config / schema / migration

| Kind | Name | Type / default | Where | Notes |
|------|------|----------------|-------|-------|
| DB column | `vault_state.remote_mcp_enabled` | `BOOLEAN NOT NULL DEFAULT false` | `models.py` `VaultState` | **NEW — adds Alembic migration; D2/ER MUST regenerate.** Default OFF. |
| migration | `0011_vault_state_remote_mcp_enabled` | add column | `backend/alembic/versions/` | `ALTER TABLE vault_state ADD COLUMN remote_mcp_enabled BOOLEAN NOT NULL DEFAULT false`. Run by standard `alembic upgrade head` (devops: one-line note, no infra change). |
| process cache | `RemoteMcpFlag` | in-memory bool + lock | `main.py` (or a tiny module) | Loaded from DB at startup; refreshed on `PUT /mcp/remote`. Not a new persistence layer. |
| route | `PUT /mcp/remote` | body `{enabled: bool}` → `McpRemoteStateResponse` | `main.py` | Token-floor clamp; same-origin/unauthenticated (§2.4). |
| response fields | `McpInfoResponse.{token_configured, remote_enabled, mount_path}` | bool, bool, str | `main.py` + `providerClient.ts` | Token NEVER returned (§2.5). |
| no env change | — | — | — | `MCP_AUTH_TOKEN`, `MCP_REMOTE_WRITE_ENABLED` unchanged (ADR-0029). |

**Migration IS introduced.** This is the single schema change; flagged explicitly so the docs
gate regenerates `docs/er/schema.mmd` (`make er`) and confirms it matches the live schema (I8).

---

## 4. Per-agent file ownership

**backend-engineer:**
- `backend/app/models.py` — add `remote_mcp_enabled` to `VaultState`.
- `backend/alembic/versions/0011_vault_state_remote_mcp_enabled.py` — new migration (add column, default false; downgrade drops it).
- `backend/app/main.py` —
  - `RemoteMcpFlag` holder + startup load (in `lifespan`, after `_seed_vault_state`);
  - extend `_BearerAuthMiddleware` with the flag-OFF ⇒ 404 gate (before the bearer check, HTTP scopes only; lifespan/WS pass-through unchanged);
  - `PUT /mcp/remote` handler + `McpRemoteStateResponse` model (token-floor clamp + holder refresh);
  - add `token_configured`, `remote_enabled`, `mount_path` to `McpInfoResponse` + `get_mcp_info`;
  - define `MCP_MOUNT_PATH = "/mcp/server"` as a single module constant used by the mount, the gate, and `/mcp/info` (I6 — no duplicated literal).
- `backend/tests/` — gate tests (flag OFF ⇒ 404; flag ON + bad token ⇒ 401; flag ON + good token ⇒ pass; clamp when no token; `/mcp/info` fields; no token leak).

**frontend-engineer:**
- `frontend/src/api/providerClient.ts` — extend `McpInfoResponse` (add `http_enabled`, `remote_write_enabled`, `token_configured`, `remote_enabled`, `mount_path`); add `McpRemoteStateResponse` type + `setRemoteMcpEnabled()`.
- `frontend/src/components/settings/SettingsPanel.tsx` — `SectionApiMcp` Remote sub-block (toggle + three-state display + URL row + copy), local state only (I3).
- `frontend/src/i18n/en.json`, `it.json` — `settings.apiMcp.remote.*` keys (parity).
- `frontend/src/tests/` — vitest: toggle disabled when `token_configured=false`; URL shown only when `remote_enabled=true`; `setRemoteMcpEnabled` PUT shape; no token rendered.

**tech-writer:**
- `docs/er/schema.mmd` — regenerate via `make er` (D2; reflects the new column).
- `docs/api/openapi.json` — regenerate via `make openapi` (D4; `PUT /mcp/remote` + new `/mcp/info` fields).
- `docs/DEPLOY.md` (D6b) — note: the remote MCP is now toggled in the UI (persisted), gated by `MCP_AUTH_TOKEN` as the floor.
- `docs/screens/settings-api-mcp.png` — refresh via Playwright (D5) to show the toggle.

**devops-engineer:** one-line note only — the new column is applied by the standard
`alembic upgrade head` already in the deploy/compose flow; no new service, port, or env.

---

## 5. Acceptance checks (DoD)

1. **Default OFF.** Fresh DB and upgraded DB both yield `vault_state.remote_mcp_enabled = false`;
   `GET /mcp/info` returns `remote_enabled=false`.
2. **Gate OFF ⇒ 404.** With `MCP_AUTH_TOKEN` set and `remote_mcp_enabled=false`, a request to
   `/mcp/server` (with OR without a valid bearer) returns **404**, indistinguishable from
   not-mounted; no MCP session is established.
3. **Gate ON ⇒ bearer enforced.** With token set and `remote_mcp_enabled=true`: no/bad bearer ⇒
   **401**; correct bearer ⇒ tools list (3, or 4 if `MCP_REMOTE_WRITE_ENABLED=true`) — exactly
   ADR-0029 behaviour.
4. **No remount / session manager stable.** Flipping the toggle ON→OFF→ON at runtime never
   restarts the StreamableHTTP session manager (mounted + lifespan-started once); verified by log
   assertion that the session manager start/stop happens only at process start/stop.
5. **Token-floor clamp.** With `MCP_AUTH_TOKEN` **unset**, `PUT /mcp/remote {enabled:true}` returns
   `remote_enabled=false, token_configured=false, clamped=true`, and `/mcp/server` 404s (not
   mounted at all). The DB does not persist `true` in this case.
6. **`/mcp/info` posture + no leak.** `GET /mcp/info` returns `token_configured`, `remote_enabled`,
   `mount_path="/mcp/server"`, and **never** the token value. `grep` proves no token in any response.
7. **Persistence across restart.** Set ON via UI, restart the process: `remote_enabled` reloads as
   `true` from `vault_state`; the gate honours it.
8. **UI three-state.** vitest: `token_configured=false` ⇒ toggle disabled + "set a token" hint, no
   URL; `remote_enabled=true` ⇒ URL = `origin + mount_path` shown with copy; no token ever rendered.
9. **I1/I5 unchanged.** `grep` proves the HTTP `write_page` (when exposed) still routes through
   `write_wiki_page`; no new write path, no second writer.
10. **Docs gate.** `make er` regenerated and matches live schema; `make openapi` includes
    `PUT /mcp/remote` + new fields; D5 screenshot refreshed (I8).

---

## 6. Consequences

**Positive** — the owner gets a real, persisted, app-like toggle with the connection URL on
display. The bearer token stays the hard security floor (toggle can only narrow, never open).
No remount fragility: the session manager is mounted+started once; the toggle is a pure
request-routing 404-gate. Reuses `vault_state` — no new table, no new service (I9). The URL is
built client-side from `origin + mount_path`, nothing hardcoded (I6). `write_page` posture is
untouched (env-only), so the vault-write surface is unchanged (I1/I5).

**Trade-offs (explicit)** —
- **One migration is introduced** (the only schema change); D2/ER must regenerate (handled in the
  docs gate). Acceptable: a single nullable-default boolean column on an existing one-row table.
- **The toggle endpoint is same-origin / unauthenticated**, consistent with the rest of the REST
  API (ADR-0028) and the Tailscale+Cloudflare threat model. The mitigation is structural: the
  worst an unauthenticated flip can do is *expose a bearer-protected* surface (the clamp forbids a
  token-less ON). Edge auth (Cloudflare Access over the origin) is the recommended hardening path
  and is pure-ops. If multi-user ever lands, the whole REST API gets real auth together.
- **A dormant `true`** can persist in the DB while the token is unset; the surface still 404s
  (not mounted), and `/mcp/info` reports both flags so the UI never shows a misleading "ON".
- **A scoped exception to ADR-0027's "display-only" panel** is introduced — one config-write
  control. Documented in §2.6; ADR-0032 supersedes that single clause only; all other read-only
  guarantees and the I9 headline (no second server, no tool invocation, no logic duplication)
  hold.

**Invariant check** — **I1/I5:** vault-write path unchanged (same `write_wiki_page`; `write_page`
posture env-only). **I3:** UI is a switch + one fetch/PUT, local state, no store churn, no heavy
render. **I6:** `mount_path` and the URL are derived (server constant + client origin); no host or
provider hardcoded. **I7:** no new loop. **I8:** migration ⇒ D2/ER regen + D4/D5 refresh in the
docs gate. **I9:** reuses `vault_state` + the existing ADR-0029 mount + existing FastMCP server;
adds no MCP capability, no second writer, no new infra. **I2/I4 untouched.** The only invariant
tension (ADR-0027 I9 display-only) is resolved by scoping, not by trading the invariant away.

---

## 7. Decisions locked (no open owner questions)

The owner has already decided the "real toggle" path and the token-floor / write-out-of-scope
constraints. This ADR is implementable as written. The single forward-looking choice — edge auth
on the toggle endpoint — is deferred as a pure-ops hardening option (§2.4), not a code decision.
