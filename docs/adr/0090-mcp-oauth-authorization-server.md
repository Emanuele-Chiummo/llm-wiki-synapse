# ADR-0090 — MCP OAuth 2.1 + PKCE authorization server for claude.ai custom connectors

- **Status:** Accepted
- **Date:** 2026-07-20
- **Amends:** ADR-0029 §2.2 (deferred OAuth as A3, "not recommended now; revisit if
  multi-user")
- **Invariants touched:** I6, I7

## Context

Live evidence (2026-07): adding Synapse's remote MCP server (`/mcp/server`, ADR-0029) as a
**claude.ai web "Custom connector"** silently failed — clicking "Connect" redirected the
browser to:

```
https://synapse.<domain>/authorize?response_type=code&client_id=<id>&redirect_uri=
https%3A%2F%2Fclaude.ai%2Fapi%2Fmcp%2Fauth_callback&code_challenge=<c>&
code_challenge_method=S256&state=<s>
```

and landed on the ordinary Synapse frontend (dashboard), because neither `/authorize` nor
any discovery/registration endpoint existed on the backend, and the nginx/Vite reverse
proxy has no path prefix that would route it there anyway (both fall through to the SPA's
`try_files ... /index.html` fallback).

Root cause: **claude.ai's web "Custom connector" UI speaks ONLY OAuth 2.1
authorization_code + PKCE.** Unlike Claude Desktop's JSON `mcpServers` config (which
supports arbitrary custom headers, so a static `Authorization: Bearer <MCP_AUTH_TOKEN>`
header already works today per ADR-0029/0033), the web UI has no field to paste a bearer
token for a custom connector — it always attempts the OAuth dance.

ADR-0029 §2.2 evaluated three MCP-auth options and picked the static bearer token (A1),
explicitly deferring full OAuth (A3 — "not recommended now; revisit if multi-user") as
unnecessary complexity for a single-operator deployment. That evaluation was correct at
the time; the constraint that changed is not "do we need multi-user OAuth" but "does the
specific client (claude.ai web) require OAuth as its ONLY auth mechanism, regardless of
how many users exist." It does. This ADR adds OAuth as an **additional** access path to
the *same* `/mcp/server` gate — it does not replace or weaken the existing static-token
model, and it deliberately avoids building a general multi-user auth system.

## Decision

### 1. Minimal, single-operator-oriented authorization server

Add `backend/app/mcp/oauth.py`, a small OAuth 2.1 + PKCE (RFC 6749/7636) authorization
server:

| Endpoint | Purpose |
|---|---|
| `GET /.well-known/oauth-authorization-server` | RFC 8414 discovery |
| `GET /.well-known/oauth-protected-resource` | RFC 9728 discovery |
| `POST /register` | RFC 7591 Dynamic Client Registration (public clients only) |
| `GET /authorize` | Renders a consent form |
| `POST /authorize` | Verifies the operator's STATIC MCP token, issues a code, redirects |
| `POST /token` | `authorization_code` and `refresh_token` grants |

**The only real credential in the whole flow is the SAME static MCP token that already
gates `/mcp/server` directly** (DB hash via `PUT /mcp/auth`, or `MCP_AUTH_TOKEN` env
bootstrap, ADR-0033 §2.1). The operator types it once into the `/authorize` consent form
to approve a grant. `verify_static_mcp_token()` (app.runtime_state) checks ONLY this
credential — an OAuth-issued access token can never be used to approve ANOTHER OAuth
grant (no delegation chain).

An OAuth-issued access token, once minted, is accepted by `BearerAuthMiddleware` exactly
like the static bearer (`McpOAuthTokenCache.find_match`, checked in `_verify_bearer` after
the DB-hash/env checks) — it grants the SAME access to `/mcp/server`, nothing more. There
is no separate scope model; this remains a single-vault, single-operator deployment.

### 2. Root-level paths, not under `/mcp/server`

`/authorize`, `/token`, `/register`, and both `.well-known` paths are mounted at the **app
root**, matching what claude.ai already tried against the live deployment (see the
observed redirect URL above). This is a pragmatic choice: claude.ai's OAuth client
apparently falls back to conventional root-level paths when no
`.well-known/oauth-protected-resource` document is discoverable at connector-creation
time. Choosing a different (more "proper") prefix like `/mcp/oauth/authorize` would work
for a *newly-added* connector doing full discovery, but would NOT fix the connector the
user already has configured in claude.ai (which cached the root-level URL on first
attempt) without deleting and re-adding it. The discovery documents (`.well-known/*`) DO
correctly advertise these same root-level paths going forward, so future clients that
follow discovery will use them too — there is no dependency on the fallback convention
being spec-correct, only on it being what claude.ai already tried.

Consequence: `frontend/nginx.conf.template` (prod reverse proxy) and
`frontend/vite.config.ts` (dev proxy + PWA prefixes) both needed new location/proxy
entries for these exact paths — without them, the paths fall through to the SPA fallback
exactly as observed live. This is the same class of gotcha as any new backend path prefix
(see the existing "Vite proxy: new endpoint" pattern already documented in project
memory) — now generalized to root-level (non-prefixed) paths for the first time.

### 3. JIT (just-in-time) client registration; PKCE as the only client credential

Public clients only — PKCE (S256, mandatory; `plain` rejected) is the confidentiality
mechanism, so `mcp_oauth_clients` has NO `client_secret` column (RFC 6749 §2.1: a public
client's `client_id` is an identifier, not a secret).

Some MCP clients (observed live with claude.ai) self-assign a `client_id` and go straight
to `/authorize` without ever calling `/register` — presumably because no
`registration_endpoint` was discoverable when the connector was first configured.
Requiring strict pre-registration would break that already-configured connector (forcing
the user to delete and re-add it). Instead: `/authorize` JIT-registers a previously-unseen
`client_id`, binding it to the `redirect_uri` presented at that first approval. **Once
bound, a `client_id` can NEVER be silently rebound to a different `redirect_uri`** — this
is the actual security boundary against a stolen/spoofed `client_id` being pointed at an
attacker-controlled callback (open-redirect guard), not the `client_id` value itself.

### 4. Ephemeral codes, persisted tokens, rotate-on-use refresh

- Authorization codes: 120s TTL, single-use, held in an **in-process dict** — NOT
  persisted. This matches the single-process-deployment assumption already documented for
  `RemoteMcpFlag`/`McpAuthCache` (app.runtime_state) — a restart within 120s of a pending
  authorization losing that one in-flight grant is an acceptable, self-healing failure
  mode (the user just clicks "Connect" again).
- Access/refresh tokens: 1h / 90d TTL respectively, **persisted PBKDF2-hashed**
  (`mcp_oauth_tokens`, migration 0038) using the SAME hashing helpers as
  `api_tokens.secret_hash` / `vault_state.mcp_access_token_hash` — never plaintext, so
  they survive a backend restart.
- Refresh is rotate-on-use (OAuth 2.1 best practice): each `grant_type=refresh_token` call
  revokes the presented row and mints a brand-new access/refresh pair. Reusing an
  already-rotated refresh token fails — the standard replay-detection signal.

### 5. Shared floor with `/mcp/server`

Every route in `app.mcp.oauth` checks the SAME `remote_mcp_enabled` flag (ADR-0032) that
gates `/mcp/server` itself — when that flag is OFF, the entire OAuth surface 404s. There
is no new independent on/off switch to configure; enabling remote MCP enables both access
paths (static bearer AND OAuth) together, consistent with "OAuth exists only to grant
access to a surface that is otherwise closed."

### 6. Cloudflare Access interaction

Where production puts Cloudflare Access in front of the whole app (ADR per
`docs/DEPLOY.md` §5.6b), the SAME reasoning that already applies to `/mcp/server` extends
to these new paths: `/token` and `/register` are called server-to-server by the OAuth
client (claude.ai's own backend) and cannot carry a CF Access service-token header;
`/authorize` is browser-navigated (the user's own browser, which DOES carry the CF Access
session cookie if logged in) but is simplest to bundle into the same CF Access **Bypass**
scope for consistency. `docs/DEPLOY.md` §5.6b is updated accordingly.

## Consequences

- claude.ai's web "Custom connector" now works end-to-end without requiring the user to
  switch to Claude Desktop.
- No general multi-user auth system was built — the security model is still fundamentally
  "one operator, one static credential," just with an OAuth-shaped front door for clients
  that require one.
- `mcp_oauth_clients`/`mcp_oauth_tokens` (migration 0038) are additive; nothing about the
  existing static-bearer `/mcp/server` gate changed for existing Claude Desktop / curl
  users.
- If Synapse ever becomes genuinely multi-user, this authorization server would need real
  per-user identity behind the `/authorize` consent step (today it is a single shared
  operator credential) — out of scope here, flagged for whenever that need arises.

## Alternatives considered

- **Do nothing / tell the user to use Claude Desktop instead.** Rejected — the user
  explicitly requested first-class support for the claude.ai web connector.
- **Full multi-user OAuth (real per-user login at `/authorize`).** Rejected as
  over-engineering for a single-operator personal vault; revisit if/when multi-user
  becomes a real requirement (same conclusion ADR-0029 §2.2 originally reached, still
  valid for the *identity* question — only the *transport* requirement changed).
- **Namespace all OAuth endpoints under `/mcp/oauth/*`.** Rejected as the sole choice
  because it would not fix the ALREADY-CONFIGURED claude.ai connector (cached root-level
  URL from its first attempt) without a delete-and-re-add; root-level paths fix both the
  existing and any future connector, since discovery documents advertise the same paths.
