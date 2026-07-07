# ADR-0062 — Cloudflare Access edge authentication + client service tokens (v1.3.9)

**Status:** Accepted
**Date:** 2026-07-07
**Supersedes/relates:** [ADR-0052](0052-auth-token-model.md) (app Bearer token), [ADR-0029](0029-remote-mcp-over-http.md), [ADR-0033](0033-ui-settable-mcp-token-allow-without-token.md) (remote MCP auth)

## Context

A defensive audit of the production deployment (`synapse.<domain>`, TrueNAS SCALE +
Cloudflare Tunnel) on 2026-07-07 found the **entire backend API public in the clear**:
`GET /pages`, `/graph`, `/search`, `/vault/meta` returned the whole vault with no
authentication. The vault contained professional/confidential material, so this is a
confidentiality incident, not a backlog item. The existing app-level Bearer token
(ADR-0052, `SYNAPSE_AUTH_TOKEN`) was **not enabled** on this deployment.

We needed to close the exposure immediately, with the smallest reliable change for a
solo developer, while leaving room for a real per-user authorization model later.

## Decision

Put **Cloudflare Access (Zero Trust)** in front of the whole app at the edge, terminating
authentication *before* traffic reaches Synapse. No app-level login UI is built.

- **Browser / PWA:** interactive One-time-PIN (or IdP) login; the `CF_Authorization`
  cookie carries same-origin requests through. No app change required.
- **Non-browser clients** (native iOS app, Chrome clipper, `curl`/scripts, and — where the
  transport allows headers — remote MCP): authenticate with a Cloudflare Access
  **service token**, sent as `CF-Access-Client-Id` / `CF-Access-Client-Secret` headers,
  matched by an Access policy with action **"Service Auth"**. Recommendation: one token
  per client for independent revocation.
- The web frontend, the iOS app, and the Chrome clipper each gained a settings/options
  field to store the service-token pair and inject the two headers at their single request
  choke point (frontend: `api/base.ts::cfAccessHeaders()` merged into `apiFetch`).
- `/mcp/server` and `POST /clip` retain their own independent tokens; where a client cannot
  send CF headers (e.g. the claude.ai remote-MCP connector, Bearer-only), the path may be
  **excluded from CF Access** (Access "Bypass" policy) without downgrading protection —
  see DEPLOY.md §5.6b.

Edge auth is treated as **interim**: it answers *"are you allowed in"* but not *"who are
you, and which vaults are yours."*

## Alternatives considered

- **Enable app-level Bearer auth (ADR-0052) only.** Rejected as the primary control: a
  single shared secret, no per-identity story, and every client would need to hold it; CF
  Access gives real identities (email/IdP) and a managed login with no app code.
- **Build app-level login now (FastAPI-Users / OIDC).** Rejected for a solo evening
  project: password/session/MFA/reset code is security-critical surface to own. Deferred to
  the tenancy work below.
- **Tailscale-only (no public exposure).** Viable and even safer, but loses public/mobile
  access the owner wants; kept as the fallback posture.

## Consequences

- Closes audit findings **C1/C2** (public API, unauthenticated mutations) at the edge.
- **CORS** (`allow_credentials: true`, permissive origin — audit I1) stays as-is: harmless
  while there is no app-level session/cookie to steal, to be tightened when app auth lands.
- The **PWA manifest** must be fetched with credentials (`crossorigin="use-credentials"`)
  or CF Access redirects it to login (fixed in v1.3.9).
- Long-running requests now traverse the Cloudflare edge (~100 s proxy limit); slow
  operations must stay under it or move to async fire-and-poll.
- **Future work (deferred, warrants its own ADR):** a real `user → owns → vault` tenancy
  model. CF Access already forwards a verified identity in the `Cf-Access-Jwt-Assertion`
  header; the backend can validate it, map to a `user` row, add `owner_id` to vaults, and
  scope queries — turning edge "authentication" into app "authorization" without building a
  login UI.
