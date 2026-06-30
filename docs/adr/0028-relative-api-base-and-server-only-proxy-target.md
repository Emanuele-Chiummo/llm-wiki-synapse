# ADR-0028 — Browser API base is relative; proxy target is a server-only env var

- **Status:** Accepted
- **Date:** 2026-06-29
- **Sprint:** v0.5 (M5 — live-UI review bugfix; Bug 1 critical/latent + Bug 2)
- **Feature:** F1 (3-panel shell / web service plumbing) · F15 (cross-platform: Tailscale + Cloudflare Tunnel reachability) · F16 (multi-provider chat over a tunnel — the only client that worked there today)
- **Builds on:** the per-client `VITE_API_BASE` convention established across v0.3–v0.5 (`frontend/src/api/*.ts`) and the Vite dev proxy in `frontend/vite.config.ts`.
- **Invariants owned:** I5 (Obsidian compat — unaffected, but the change MUST stay pure transport plumbing) · I9 (do not reinvent — no new client/proxy framework; reuse the existing Vite proxy). No invariant is traded for convenience.
- **Author:** solution-architect
- **Implementers:** frontend-engineer (`frontend/src/api/*.ts`, `frontend/vite.config.ts`, `frontend/.env.example`) · devops-engineer (`docker-compose.dev.yml`)

---

## 1. Context

A live UI review surfaced two related defects rooted in **one variable carrying two
incompatible meanings**.

`VITE_API_BASE` is read in **two layers that run in two different places**:

1. **Browser bundle** — `import.meta.env["VITE_API_BASE"]` in each `frontend/src/api/*.ts`
   client. Vite **inlines this value into the JS shipped to the browser at build time**.
   The browser, not the container, performs the fetch.
2. **Vite dev server (Node)** — `process.env["VITE_API_BASE"]` in `vite.config.ts`, used as
   the **server-side proxy `target`**. This runs inside the frontend container.

These two consumers need **opposite values**:

- The browser must reach the backend over whatever origin the *user* is on — host
  `localhost`, a Tailscale MagicDNS name, or a Cloudflare Tunnel hostname. The only value
  that is correct for *all three* is **relative** (`""`): same-origin, let the page's own
  host/scheme decide.
- The dev proxy must reach the backend over the **Docker network**, i.e. the service name
  `http://synapse-backend:8000`, which is resolvable *only inside the compose network*.

`docker-compose.dev.yml:65` sets `VITE_API_BASE: http://synapse-backend:8000` to satisfy
consumer (2). But because Vite inlines it for consumer (1), **the browser is told to fetch a
Docker-internal hostname it cannot resolve**. It appears to work only by accident: the
developer's ISP NXDOMAIN-redirects unknown names to `127.0.0.1`, which happens to be the
host where the backend port is published. On Tailscale or Cloudflare Tunnel — the actual
deployment targets (F15) — `synapse-backend` does not resolve and **every API call fails**.
This is **Bug 1: critical and latent** (silent on the dev box, broken everywhere it matters).

Compounding it (**Bug 2**): 8 of 9 clients default `VITE_API_BASE` to
`http://localhost:8000`; only `chatClient.ts` defaults to `""`. So over a tunnel **chat is
the only feature that works** — everything else points the browser at the user's own
`localhost`. The inconsistency masked Bug 1 differently per client.

---

## 2. Decision

**Split the one overloaded variable into two, by execution context, and make the browser
default relative.**

### 2.1 Browser fetch base — relative `""` is the default

Every client in `frontend/src/api/*.ts` reads `import.meta.env["VITE_API_BASE"]` and
**defaults to `""`** (matching the already-correct `chatClient.ts`):

```ts
const API_BASE = (import.meta.env["VITE_API_BASE"] as string | undefined) ?? "";
```

- `""` ⇒ all requests are **same-origin relative paths** (`/graph`, `/pages`, `/chat`, …).
  Correct on host `localhost`, Tailscale, and Cloudflare Tunnel, because the browser uses
  the page's own origin. In prod the same origin serves both the static bundle and the API.
- `VITE_API_BASE` MAY still be set to an absolute URL for **unusual split-origin
  deployments** (static host ≠ API host). That is an opt-in escape hatch, **not** the
  default, and **must never be set to a Docker-internal name** (that name has no meaning in
  a browser).

This makes all 9 clients identical and removes the 8-vs-1 inconsistency (Bug 2).

### 2.2 Proxy target — a new **server-only** env var

The dev proxy target moves to a **new variable that is NOT prefixed `VITE_`**, so Vite
**never inlines it into the browser bundle** (the `VITE_` prefix is exactly Vite's "expose
to client" marker). Definitive name and default:

| Variable | Read by | Where it runs | Default | Inlined into browser? |
|----------|---------|---------------|---------|-----------------------|
| `VITE_API_BASE` | `frontend/src/api/*.ts` (`import.meta.env`) | **Browser** | `""` (relative) | Yes (by design) — but unset by default |
| `BACKEND_PROXY_TARGET` | `frontend/vite.config.ts` proxy `target` (`process.env`) | **Vite dev server (Node, in container)** | `http://localhost:8000` | **No** (no `VITE_` prefix) |

`vite.config.ts` proxy entries read **`process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000"`**
and **must stop reading `VITE_API_BASE`** for the proxy target. (The 14 existing proxy
entries — `/graph`, `/pages`, `/status`, `/ingest`, `/provider`, `/conversations`, `/chat`,
`/import-schedule`, `/config`, `/mcp`, `/research`, `/review`, `/search` — change only the
`target` expression; route list and `changeOrigin: true` are unchanged.)

### 2.3 `docker-compose.dev.yml` frontend service

Replace the misused var. The browser base stays relative because `VITE_API_BASE` is **not
set**; the proxy reaches the backend over the Docker network:

```yaml
environment:
  BACKEND_PROXY_TARGET: http://synapse-backend:8000
  # Do NOT set VITE_API_BASE — the browser base must stay relative ("").
```

Flow in dev: browser → relative `/graph` → Vite dev server (origin the browser is already
on: `localhost:5173`, the Tailscale host, or the tunnel) → proxy → `synapse-backend:8000`
over the Docker network. The Docker-internal hostname now lives **only** where it resolves.

### 2.4 `.env.example`

Document both variables and the new defaults, so the contract is discoverable:

```
# Browser API base. Leave EMPTY for same-origin (correct for localhost, Tailscale,
# Cloudflare Tunnel). Set to an absolute URL ONLY for split-origin deployments.
# NEVER set this to a Docker-internal hostname — it is inlined into the browser bundle.
VITE_API_BASE=

# Dev-only, server-side Vite proxy target (NOT exposed to the browser).
# Default http://localhost:8000; compose overrides to http://synapse-backend:8000.
BACKEND_PROXY_TARGET=http://localhost:8000
```

### 2.5 Docs (I8)

- No D2/D4 change (no schema, no API surface change). 
- D6b (`docs/DEPLOY.md`) should note the two-variable contract when the deploy guide is
  completed in v0.6; flagged, not blocking for this bugfix.

---

## 3. Consequences

**Positive**
- Bug 1 fixed at the root: the browser never receives a Docker-internal hostname. Synapse
  becomes reachable over Tailscale and Cloudflare Tunnel (F15), not just on the dev box's
  accidental ISP redirect.
- Bug 2 fixed: all 9 clients share one identical, correct default (`""`). No
  feature-by-feature divergence.
- One variable, one meaning, one execution context each. The `VITE_` prefix now correctly
  signals "browser-exposed", and the absence of it on `BACKEND_PROXY_TARGET` correctly keeps
  the Docker hostname server-side.
- Same-origin by default also sidesteps CORS entirely in the common deployment.

**Trade-offs / limitations (stated explicitly)**
- Split-origin deployments (static bundle and API on different hosts) now require an
  **explicit opt-in** by setting `VITE_API_BASE` to an absolute URL. This is intentional:
  the safe, tunnel-correct behaviour is the default; the unusual case is the one that must
  be configured.
- A developer running the Vite dev server **outside** Docker (bare `npm run dev`) must have
  the backend on `localhost:8000` (the `BACKEND_PROXY_TARGET` default) or set the var. This
  matches today's bare-metal expectation; no regression.
- `VITE_API_BASE` is build-time inlined: changing it requires a rebuild, not just a restart.
  Unchanged from today; the default-empty value means most users never touch it.

**Invariant check**
- **I5 (Obsidian):** unaffected — this is transport plumbing only; no change to vault
  files, frontmatter, or wikilinks. The implementers MUST keep it that way.
- **I9 (do not reinvent):** reuses the existing Vite proxy and `import.meta.env` mechanism;
  introduces no new client, gateway, or proxy framework. One renamed/added env var.
- No other invariant (I1–I4, I6–I8) is touched. **No invariant is traded for convenience.**

---

## 4. Contract the implementers follow verbatim

1. **Every** `frontend/src/api/*.ts` client defaults `VITE_API_BASE` to **`""`** (not
   `http://localhost:8000`). After the change, `grep -r 'http://localhost:8000' frontend/src/api`
   returns **nothing**.
2. `frontend/vite.config.ts` proxy `target` reads
   **`process.env["BACKEND_PROXY_TARGET"] ?? "http://localhost:8000"`** for all proxy
   entries, and **no longer references `VITE_API_BASE`**.
3. `docker-compose.dev.yml` frontend service sets **`BACKEND_PROXY_TARGET: http://synapse-backend:8000`**
   and **does NOT set `VITE_API_BASE`**.
4. `frontend/.env.example` documents both vars per §2.4 (`VITE_API_BASE` empty default;
   `BACKEND_PROXY_TARGET=http://localhost:8000`).

## 5. Do NOT (reject any PR that does these)

1. Do NOT set `VITE_API_BASE` to a Docker-internal hostname (`synapse-backend`) anywhere —
   it is inlined into the browser bundle, which cannot resolve it.
2. Do NOT keep `http://localhost:8000` as a client default in any `api/*.ts` file.
3. Do NOT read `VITE_API_BASE` in `vite.config.ts` for the proxy target — use
   `BACKEND_PROXY_TARGET`.
4. Do NOT prefix the proxy-target var with `VITE_` (that would re-expose the server-only
   value to the browser and recreate Bug 1).
