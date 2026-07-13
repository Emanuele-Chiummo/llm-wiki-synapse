# ADR-0075 — Explicit deployment trust mode and authenticated health diagnostics (v1.6.0)

- **Status:** Accepted
- **Date:** 2026-07-13
- **Amends:** ADR-0052 §§2.1 and 2.3 (shared bearer token and exempt routes)
- **Related:** ADR-0047 (desktop connection gate), ADR-0062 (edge authentication)
- **Invariants touched:** I3, I6, I8

## Context

ADR-0052 intentionally made `SYNAPSE_AUTH_TOKEN` optional so existing local installations could
upgrade without configuring credentials. That compatibility rule did not distinguish a loopback
development process from a network-reachable server. An operator could therefore deploy Synapse
on a LAN, through a tunnel, or behind a reverse proxy while accidentally retaining the open local
default.

The original public probe set also included `GET /health/detailed`. Its component, dependency,
queue and scheduler diagnostics are useful to an authenticated operator, but are not needed to
prove that the process is alive or to establish the desktop connection. Publishing those details
increases reconnaissance value without improving liveness detection.

Synapse needs an explicit trust boundary that preserves zero-configuration local use while making
a declared server deployment fail closed.

## Decision

### 1. Deployment mode is explicit

`SYNAPSE_DEPLOYMENT_MODE` accepts exactly two values:

- `local` is the backward-compatible default. An empty `SYNAPSE_AUTH_TOKEN` remains valid for
  loopback development and trusted single-machine use. Setting a token still enables the shared
  bearer gate.
- `server` declares a network-reachable deployment. Configuration validation runs at startup and
  refuses to start unless `SYNAPSE_AUTH_TOKEN` is present and passes the minimum strength checks.

In `server` mode the token must contain at least 32 characters, contain no whitespace and contain
at least eight distinct characters. These checks reject missing, short and obviously repeated
credentials; they are not an entropy estimator. Operators must generate the token with a
cryptographically secure generator, for example `openssl rand -hex 32`.

The token remains env-only and is still compared with `secrets.compare_digest` by the single
middleware defined by ADR-0052. There is no database copy, runtime token endpoint, per-route auth
dependency or change to the independent MCP and clipper credentials.

`local` describes compatibility, not network safety. It must not be used as an authentication
bypass for a process reachable by other machines.

### 2. Public health is limited to connection and liveness signals

The unauthenticated health boundary is:

- `GET`/`HEAD /health/live`: returns only `{"status":"ok"}` and no vault, dependency,
  configuration, error or component details.
- `GET`/`HEAD /status`: retained for the existing desktop/server connection contract. It exposes
  process status and data-version metadata, but no page content or credentials.

`GET`/`HEAD /health/detailed` is removed from the auth exemption set. When the bearer gate is
enabled it requires `Authorization: Bearer <SYNAPSE_AUTH_TOKEN>`. In `server` mode the bearer gate
is necessarily enabled because startup cannot complete without a valid token.

The non-health exemptions from ADR-0052 remain unchanged: CORS `OPTIONS`, public API schema/docs,
the independently authenticated `/mcp/server` mount and `POST /clip`. Exemptions continue to be
method-aware so a future mutating route cannot inherit a probe exemption accidentally.

### 3. Migration is configuration-only and atomic

Existing installations remain in `local` mode until the operator opts into `server`; no database
migration is required. Moving a network-reachable installation from `local` to `server` requires
setting both environment variables before the same restart:

```env
SYNAPSE_DEPLOYMENT_MODE=server
SYNAPSE_AUTH_TOKEN=<cryptographically-random-token>
```

Container and unauthenticated uptime probes must move from `/health/detailed` to `/health/live`.
Operational dashboards that need component diagnostics must keep `/health/detailed` and add the
bearer header. A startup failure after selecting `server` is a configuration signal to fix, not a
condition to bypass by reverting to `local` on a network-reachable host.

## Consequences

- A declared server can no longer start in an accidentally unauthenticated state.
- Local development remains zero-configuration and backward-compatible.
- Public liveness monitoring has a stable, non-diagnostic endpoint.
- Detailed health consumers must manage the same REST API credential as other protected clients.
- Edge authentication remains defence in depth and does not replace the application token in
  `server` mode.
- Renaming the environment variables or introducing per-user identity remains out of scope.

## Alternatives considered

- **Require auth in every mode:** safest default, but breaks the established local first-run and
  test workflow without a migration path.
- **Infer server mode from bind address or proxy headers:** fragile across Docker, Tauri, tunnels
  and reverse proxies; implicit security posture is the problem this ADR removes.
- **Keep `/health/detailed` public:** convenient for legacy probes, but exposes operational
  topology that liveness checks do not require.
- **Make `/status` authenticated immediately:** reduces public metadata further, but breaks the
  current desktop connection handshake. A later protocol revision may collapse connection checks
  onto `/health/live` and then reconsider this exemption.
- **Treat a long repeated token as strong:** length alone accepts common placeholder values and
  creates false confidence in server deployments.

## Verification

- Configuration tests cover `local` compatibility, the two accepted enum values, and fail-fast
  rejection of missing, short, whitespace-containing and low-diversity server tokens.
- Auth-policy tests prove `GET`/`HEAD /health/live` and `/status` are public while
  `/health/detailed` is protected.
- The live endpoint response contains only the top-level `status` field.
- Deploy documentation includes secure token generation, local-to-server migration, authenticated
  detailed probes and rollback cautions.
