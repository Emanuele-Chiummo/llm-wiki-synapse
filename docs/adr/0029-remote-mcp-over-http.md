# ADR-0029 — Remote MCP over HTTP: mount `mcp.http_app()` at `/mcp/server`, bearer-token auth, read-only by default

- **Status:** Accepted (owner decided 2026-06-29: static bearer token `MCP_AUTH_TOKEN`, fail-closed; read-only by default, `MCP_REMOTE_WRITE_ENABLED=false`) — implemented
- **Superseded in part by ADR-0033** (2026-06-30): §2.2's "unset MCP_AUTH_TOKEN ⇒ route not mounted" condition is now allow-aware — the mount is always built when `MCP_AUTH_TOKEN` is set OR when `mcp_allow_without_token=true`; public Cloudflare sources always require a token regardless.
- **Date:** 2026-06-29
- **Sprint:** v0.5 (Feature A — remote MCP reachable from claude.ai)
- **Feature:** F17-adjacent (MCP surface) · builds on ADR-0010 (MCP transport + shared write path) and ADR-0027 (`/mcp/info` introspection)
- **Invariants owned:** I1 (write_page stays an incremental upsert) · I5 (frontmatter validated before write) · I6 (no hardcoded provider — the MCP surface does not touch InferenceProvider) · I9 (reuse FastMCP/Qdrant/bge-m3, no new gateway)
- **Author:** solution-architect
- **Implementers:** backend-engineer (mount + auth middleware + config) · devops-engineer (Cloudflare Tunnel route + env) · frontend-engineer (none — `/mcp/info` already exists) · tech-writer (D4 MCP reference, D6b deploy note)

---

## 1. Context

`backend/app/mcp/server.py` defines `mcp = FastMCP(name="synapse")` with four tools
(`search_wiki`, `get_page`, `list_pages` — read; `write_page` — mutating). Today the only
runtime transport is **stdio** (`mcp.run(transport="stdio")`, ADR-0010 §1); the server is
**not** mounted into the FastAPI app. `GET /mcp/info` (main.py ~1614) only *introspects* the
live `mcp` object — it opens no transport session.

The owner wants the four tools reachable as a **remote MCP server in claude.ai**. claude.ai
remote connectors speak **Streamable HTTP** (the current MCP HTTP transport; SSE is the older
variant). So we need a live HTTP listener exposing the MCP protocol at a public URL, secured —
`write_page` mutates the vault and must never be open to the internet.

fastmcp is pinned `>=2.0.0` (`backend/pyproject.toml:22`). FastMCP 2.x exposes
`mcp.http_app()` → a mountable Starlette/ASGI sub-app (Streamable HTTP), and
`mcp.run(transport="http")` for a standalone listener. Both are one-liners; ADR-0010 §1
already anticipated this ("HTTP is `mcp.run(transport=...)` if needed later").

The owner already runs **Cloudflare Tunnel + Tailscale** (CLAUDE.md §1), and ADR-0028 just
established the relative-base / `BACKEND_PROXY_TARGET` contract. The Vite dev proxy already has
a `/mcp` route — used by `/mcp/info`. **Path-collision flag:** any mounted MCP ASGI app must
NOT shadow the existing `/mcp/info` REST route. We therefore mount under **`/mcp/server`**, not
`/mcp`, and the Vite `/mcp` proxy entry continues to cover both `/mcp/info` and `/mcp/server/*`
since it proxies the `/mcp` prefix (no Vite change required; verify in acceptance).

---

## 2. Decision

### 2.1 Serve by **mounting `mcp.http_app()` into the existing FastAPI app** — not a second process

`backend/app/main.py` mounts the FastMCP Streamable-HTTP ASGI app under **`/mcp/server`**:

```python
# main.py — after `app = FastAPI(...)`, BEFORE adding routes that could shadow it.
from app.mcp.server import mcp as _mcp_server
_mcp_http_app = _mcp_server.http_app(path="/")   # Streamable HTTP ASGI app
app.mount("/mcp/server", _mcp_http_app)
```

**Rationale (mount vs separate process):**
- **One origin, one tunnel.** The owner exposes a single FastAPI origin over Cloudflare Tunnel
  (ADR-0028). A mounted sub-app rides that same origin/tunnel — no second port to publish, no
  second Cloudflare route, no second TLS endpoint. A separate `mcp.run(transport="http")`
  process would need its own published port + tunnel route + lifecycle in compose, for zero
  functional gain. (I9 — do not add infrastructure we do not need.)
- **Shared lifespan & singletons.** Mounted, the MCP tools run in the same process as the
  FastAPI app, so `get_embedding_client()`, the Qdrant client, and `write_wiki_page` are the
  exact same instances the REST API and orchestrator use. No divergent second writer (preserves
  the ADR-0010 §2 single-write-path guarantee).
- **stdio path is untouched.** `mcp.run(transport="stdio")` (the `__main__` entry, ADR-0010 §1)
  remains for the CliAgentProvider delegated path. HTTP mount is **additive** — the CLI provider
  keeps using stdio in-process; F17 routing is unchanged. **FastMCP lifespan note:** `http_app()`
  carries its own Starlette lifespan; the implementer MUST chain it into the FastAPI `lifespan`
  (pass `app.mount` the sub-app and combine lifespans per FastMCP docs) so the MCP session
  manager starts/stops correctly. This is a known FastMCP-mount requirement — verify in tests.

**Path collision (flagged):** mount path is **`/mcp/server`**. `GET /mcp/info` (REST,
ADR-0027) stays at `/mcp/info`. No route shadows another: FastAPI matches the explicit
`/mcp/info` route and the `/mcp/server` mount independently. The Vite `/mcp` proxy entry
(ADR-0028 list) already forwards the whole `/mcp` prefix, so it covers `/mcp/server/*` with **no
Vite change** — implementer confirms via the acceptance check.

### 2.2 AUTH — static bearer token via `MCP_AUTH_TOKEN`, enforced by a scoped ASGI middleware (recommended default)

A public, vault-mutating endpoint MUST be authenticated. The **recommended default for a solo
self-hosted operator behind Cloudflare Tunnel** is a **static bearer token**:

- New env var **`MCP_AUTH_TOKEN`** (secret; no default; read in `config.py`). If **unset**, the
  `/mcp/server` mount is **NOT mounted at all** (fail-closed: no token ⇒ no public MCP surface).
  The stdio path is unaffected.
- A small ASGI middleware wrapping **only the `/mcp/server` sub-app** requires
  `Authorization: Bearer <MCP_AUTH_TOKEN>` (constant-time compare). Missing/wrong → `401`.
  It does NOT touch any other route (the REST API auth posture is unchanged in this ADR).

**What claude.ai actually requires for a remote MCP connector:** claude.ai's remote-connector
UI supports adding a server by URL with either **OAuth** (full authorization-code flow the
server advertises) **or** a **token/header** the user pastes. A static bearer token satisfies
the token path: the owner pastes `Bearer <MCP_AUTH_TOKEN>` (or the raw token) in the connector
config. This is the pragmatic minimum and needs no OAuth server.

**Alternatives (the owner picks — §6):**
- **(A1) Static bearer token `MCP_AUTH_TOKEN` (RECOMMENDED).** Simplest; one secret in env;
  fail-closed. Trade-off: a long-lived shared secret with no rotation/expiry UX. Mitigation:
  rotate by changing the env var; keep the value out of git (it already is — §12).
- **(A2) Cloudflare Access in front of `/mcp/server`.** Auth handled entirely at the edge
  (Cloudflare service-token or SSO); the app needs no auth code. Trade-off: claude.ai must be
  able to present the Cloudflare service-token headers on every MCP call — workable with a
  service-token, but more setup on the Cloudflare side and claude.ai must allow custom headers.
  Best if the owner wants edge-enforced auth and possibly SSO.
- **(A3) Full OAuth (FastMCP auth provider).** claude.ai's richest path; supports per-client
  consent + token expiry. Trade-off: materially more code (authorization server / token
  issuance) for a single-user homelab. **Not recommended now**; revisit if multi-user.

This ADR builds **A1** as the default and leaves A2 as a pure-ops alternative (no code change —
devops puts Cloudflare Access in front and the owner may set `MCP_AUTH_TOKEN` to a value the
edge also injects, or disable app-level auth only if the edge is proven to gate it). **A1 vs A2
is the owner decision (§6).**

### 2.3 Read-only by default; `write_page` gated behind an explicit opt-in flag

The remote surface is **read-only by default**. New env var **`MCP_REMOTE_WRITE_ENABLED`**
(bool, default **`false`**):

- `false` (default): the mounted HTTP server exposes **only** `search_wiki`, `get_page`,
  `list_pages`. `write_page` is **not registered** on the HTTP surface.
- `true`: `write_page` is also exposed over HTTP (still bearer-gated by §2.2).

Implementation constraint: there is **one** `mcp` object and the stdio/CLI path needs
`write_page`. So the read-only filtering must be applied to the **HTTP-mounted app only**, not
by un-registering the tool globally. The implementer achieves this by building the HTTP surface
from a tool set that excludes `write_page` when `MCP_REMOTE_WRITE_ENABLED=false` (e.g. a
second `FastMCP` instance that re-exports only the read tools, or FastMCP's tool-filtering on
`http_app()`). The stdio server (`__main__`) keeps all four tools unconditionally. **The
ai-agent-engineer signs off that the stdio/CLI delegated ingest still has `write_page`.**

This is defence-in-depth: even with a leaked token, the default surface cannot mutate the vault.

### 2.4 Write path is unchanged — still the shared `write_wiki_page` primitive

When `write_page` IS exposed (§2.3 true), it remains the **existing** tool in
`backend/app/mcp/server.py`, which calls `write_wiki_page()` (ADR-0010 §2). No new write path,
no raw filesystem write. I1 (incremental upsert) and I5 (frontmatter validated) hold
identically because it is literally the same code. **No change to `write_page` internals in
this ADR.**

### 2.5 Config + CORS + docs

- `config.py` adds `mcp_http_enabled` (derived: `True` iff `MCP_AUTH_TOKEN` set),
  `MCP_AUTH_TOKEN`, `MCP_REMOTE_WRITE_ENABLED` (§3).
- `/mcp/info` (ADR-0027) gains two read-only fields so the UI can show the remote posture:
  `http_enabled: bool` and `remote_write_enabled: bool`. **No secret is ever returned**
  (mirrors the provider_config "no api key in responses" rule, §12).
- The MCP ASGI sub-app handles its own protocol; FastAPI CORS (ADR/§ main.py:182) is for the
  REST API and does not need to cover claude.ai (claude.ai's MCP client is server-to-server, not
  a browser fetch from our origin). Implementer verifies no CORS regression on REST.
- D4 (MCP reference) documents the public URL shape `https://<tunnel-host>/mcp/server` and the
  bearer requirement. D6b (DEPLOY) documents setting `MCP_AUTH_TOKEN` and the Cloudflare route.

---

## 3. New config / env / schema

| Kind | Name | Type / default | Read in | Notes |
|------|------|----------------|---------|-------|
| env | `MCP_AUTH_TOKEN` | secret str, **no default** | `config.py` | Unset ⇒ HTTP MCP NOT mounted (fail-closed). Never logged/returned. |
| env | `MCP_REMOTE_WRITE_ENABLED` | bool, default `false` | `config.py` | `true` exposes `write_page` over HTTP (still bearer-gated). |
| derived | `settings.mcp_http_enabled` | bool | `config.py` property | `True` iff `MCP_AUTH_TOKEN` is set. |
| mount path | `/mcp/server` | — | `main.py` | Streamable-HTTP MCP. Distinct from `/mcp/info`. |

**No DB schema change. No migration. No D2 (ER) change.** (The MCP surface is stateless config.)

---

## 4. Acceptance check (DoD)

1. With `MCP_AUTH_TOKEN` set: a Streamable-HTTP MCP client (e.g. the FastMCP client, or
   claude.ai) reaches `https://<host>/mcp/server`, lists exactly 3 tools when
   `MCP_REMOTE_WRITE_ENABLED=false`, and 4 when `true`.
2. A request to `/mcp/server` **without** a valid `Authorization: Bearer <MCP_AUTH_TOKEN>`
   returns `401` and invokes no tool.
3. With `MCP_AUTH_TOKEN` **unset**, `/mcp/server` is not mounted (404) and startup still
   succeeds; the stdio entry (`python -m app.mcp.server`) is unaffected.
4. `GET /mcp/info` returns `http_enabled` and `remote_write_enabled` and **no token**.
5. `grep` proves the HTTP `write_page` (when exposed) still routes through `write_wiki_page`
   (no second writer) — I1/I5 unchanged.
6. The Vite `/mcp` proxy still serves `/mcp/info` and `/mcp/server/*` with no `vite.config.ts`
   change (ADR-0028 route list intact).

---

## 5. Consequences

**Positive** — single origin/tunnel; same process/singletons as REST (no divergent writer);
fail-closed when no token; read-only by default; stdio/CLI path untouched (F17 routing intact).

**Trade-offs (explicit)** — a static bearer token is a long-lived shared secret with no built-in
rotation/expiry (mitigated by env rotation + A2/A3 escape hatches). Mounting couples the MCP
session-manager lifespan to FastAPI's lifespan — the implementer MUST chain lifespans or the MCP
sessions will not start (verified in AC). FastMCP `http_app()` API surface is the load-bearing
dependency; pin behaviour in a test.

**Invariant check** — I1/I5: write path unchanged (same `write_wiki_page`). I6: no provider
hardcoded; MCP surface does not touch InferenceProvider. I9: reuses FastMCP + Qdrant + bge-m3 +
the existing tunnel; adds no gateway, no port, no second search service. I2/I3/I4/I7/I8: untouched.
**No invariant is traded for convenience.**

## 6. Decision the owner must make before coding

**MCP auth mechanism.** RECOMMENDED: **A1 — static `MCP_AUTH_TOKEN` bearer**, fail-closed,
read-only by default. Alternatives: **A2 — Cloudflare Access** (edge-enforced, more Cloudflare
setup, claude.ai must present service-token headers) · **A3 — full OAuth** (most code; defer
unless multi-user). The build proceeds on A1 unless the owner selects A2/A3.
