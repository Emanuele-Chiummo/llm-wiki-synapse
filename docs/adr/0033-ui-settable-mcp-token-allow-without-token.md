# ADR-0033 — UI-settable MCP access token + "allow without token" (loopback/private only); hashed storage in `vault_state`, public source always requires the token

- **Status:** Proposed (owner decision pending on two points — §7) — DESIGN ONLY, no implementation
- **Date:** 2026-06-30
- **Sprint:** v0.5 (Feature — adopt the nashsu/llm_wiki auth UX: token configurable from Settings + an "allow access without a token" option)
- **Feature:** F1-MCP-UI (Amendment — owner request, Emanuele Chiummo) · supersedes the *env-only token* parts of **ADR-0029 §2.2** and integrates with **ADR-0032** (runtime toggle) · reconciles **ADR-0008 / CLAUDE.md §12** ("no secrets in DB")
- **Invariants owned:** **I6** (nothing hardcoded — no provider touched; mount path + IP ranges are named constants) · **I9** (reuse `vault_state` + the existing ADR-0029 mount + existing FastMCP server; NO second process, NO new gateway, NO new tool, NO broadened tool set) · **I3** (UI: a token field + a switch + one PUT — local state, no store churn) · **I1/I5** (vault-write path unchanged — `write_page` still routes through `write_wiki_page`) · **I7** (no new loop)
- **Author:** solution-architect
- **Implementers:** backend-engineer (column + migration `0012` + token hashing + middleware source-trust gate + `PUT /mcp/auth` + `/mcp/info` fields) · frontend-engineer (token field + "allow without token" switch + one-time-token reveal + i18n) · tech-writer (D2 ER regen, D4 OpenAPI, D6b deploy note, D5 screenshot) · devops-engineer (one-line migration note; confirm the tunnel sets `CF-Connecting-IP` / trusted-proxy posture)

---

## 1. Context

ADR-0029 shipped the remote MCP surface as an **env-only fail-closed** design:
`MCP_AUTH_TOKEN` (env, no default) both (a) decides whether the FastMCP Streamable-HTTP
sub-app is mounted at all (`main.py:367` — `if _http_mcp_asgi_app is not None and
settings.mcp_auth_token is not None`) and (b) is the bearer secret enforced by
`_BearerAuthMiddleware` (constant-time `hmac.compare_digest`, `main.py:225`). ADR-0032 added a
persisted `vault_state.remote_mcp_enabled` runtime flag (Alembic `0011`) that 404-gates requests
**before** the bearer check, with a token-floor clamp on `PUT /mcp/remote`.

**Owner request (locked):** adopt the nashsu/llm_wiki auth UX — **only the auth parts**:

1. The access token is **configured from the UI** (Settings → API + MCP), not env-only.
2. There is an **"Allow access without a token"** option; when on, a client may connect
   unauthenticated.
3. Security posture from the reference: bind loopback by default, file reads via allow-list,
   never pass tokens on the CLI.

**Explicitly NOT adopted** (owner): separate "Enable HTTP API" / "Enable MCP access" toggles,
a broader tool set, the separate-process proxy. **KEEP:** in-process FastMCP, the 4 tools, the
ADR-0032 runtime toggle.

**The deployment reality that makes "allow without token" dangerous.** The owner reaches the
single FastAPI origin over **two** networks (CLAUDE.md §1): **Tailscale mesh** (private, CGNAT
`100.64.0.0/10`) **and Cloudflare Tunnel** (public internet). "Allow without token" is benign on
loopback / Tailscale but is a **vault-mutating-capable open endpoint on the public internet**.
The reference model assumes a `127.0.0.1` bind; we cannot assume that, because the same origin is
deliberately published publicly. Therefore the "allow without token" option must be **scoped to
non-public request sources** — this is the load-bearing security decision of this ADR (§2.3).

**The ADR-0008 tension.** ADR-0008 and CLAUDE.md §12 say "**no secrets in code or database**;
API keys are environment-only." The owner now wants a **UI-settable** token, which implies the
token is created/stored by the app, not pasted into env. §2.1 resolves this tension precisely.

---

## 2. Decision

### 2.1 The MCP access token is an APP-ISSUED ACCESS CREDENTIAL — exempt from ADR-0008's "provider keys" rule; stored as a SALTED HASH in `vault_state`

**Reconciliation with ADR-0008 / §12 (the key item).** ADR-0008's "no secrets in DB" rule
governs **third-party provider API keys** (Anthropic key, OpenAI-compatible key) — credentials
that authenticate *Synapse to an external service*. Its rationale is: those keys are
high-blast-radius bearer secrets for *someone else's* billed API, and leaking the DB must not
leak them; env keeps them out of backups/git/`pg_dump`.

The **MCP access token is categorically different**: it is a credential **Synapse issues to
authenticate callers to itself** (like a session token or an app API key), scoped to one local
surface, revocable in one click, with blast radius = "read/maybe-write this one vault." It is
**not** a third-party provider key. **Decision: the MCP access token is exempt from ADR-0008's
provider-key rule** — but we still honour the *spirit* of §12 (never store a recoverable secret
in the DB) by storing **only a salted hash**, never the plaintext.

**Storage decision — SALTED HASH, never plaintext (strongly weighted for security):**

- Add **`vault_state.mcp_access_token_hash : Mapped[str | None]`** (`Text`, nullable, default
  `NULL`). Stores a salted, slow-ish KDF hash of the token (PBKDF2-HMAC-SHA256 via stdlib
  `hashlib.pbkdf2_hmac`, or `argon2`/`bcrypt` if already vendored — **no new heavy dep if
  avoidable**; PBKDF2 from stdlib is sufficient and dependency-free). Encoded as
  `pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>` so the verifier is self-describing.
- The token **plaintext is shown exactly ONCE**, at generation time, in the `PUT /mcp/auth`
  response (or a dedicated rotate response). It is **never re-displayed** and **never returned by
  any GET**. `/mcp/info` reports only `token_configured: bool` (derived
  `mcp_access_token_hash IS NOT NULL`), never the value or the hash.
- **Why hash, not re-displayable plaintext:** a re-displayable token requires storing a
  recoverable secret in Postgres — exactly what §12 forbids in spirit, and it makes a DB
  dump / backup leak the live credential. A salted hash means a DB leak yields no usable token
  (the surface stays closed). The cost — the owner cannot "look up" a lost token and must
  **rotate** instead — is acceptable and is in fact the more secure UX (matches "shown once" in
  many app token UIs). This is the recommended option and the ADR builds it.
- **Constant-time verification:** the middleware computes the candidate's PBKDF2 hash with the
  stored salt+iterations and compares with `hmac.compare_digest` against the stored hash. (PBKDF2
  is intentionally slow; the per-request cost is a one-time KDF on the *presented* token only when
  a token is required — acceptable for a low-QPS personal MCP surface. If KDF latency is ever a
  concern, an in-process verified-token cache keyed by a fast hash of the bearer can be added; not
  needed at v0.5 volumes.)

**Env `MCP_AUTH_TOKEN` stays as a BOOTSTRAP fallback (not removed).**
- If `vault_state.mcp_access_token_hash` is set, **the DB hash is authoritative** (the UI-set
  token wins).
- Else if `settings.mcp_auth_token` (env) is set, it is the **bootstrap token**: the middleware
  verifies the bearer against the env value (plaintext compare, constant-time — unchanged
  ADR-0029 path). This preserves every existing deployment and lets the owner seed a token before
  the UI is reachable.
- Else **no token is configured** (and "allow without token" governs reachability — §2.3).
- This makes migration zero-friction: existing `MCP_AUTH_TOKEN` users keep working untouched;
  the UI token is purely additive and, once set, takes precedence.

**Migration: YES.** New revision **`0012_vault_state_mcp_access_token_hash`** —
`ALTER TABLE vault_state ADD COLUMN mcp_access_token_hash TEXT NULL`. **D2/ER MUST regenerate**
(`make er`) and match the live schema (I8). This is the one and only schema change in this ADR.
Default `NULL` ⇒ on upgrade, no UI token exists yet ⇒ env-or-none behaviour holds (no posture
change on upgrade).

### 2.2 Scope — the token gates the MCP REMOTE surface (`/mcp/server`) ONLY; the rest of the API stays unauthenticated

This is confirmed and explicit: the token gates **only** the `/mcp/server` sub-app, exactly as
ADR-0029 scoped `_BearerAuthMiddleware`. The **same-origin React web UI** and the **rest of the
REST API** stay **unauthenticated** (ADR-0028 relative base; the browser sends no auth). Adding
auth to the whole API would break the browser UI and is out of scope.

- The MCP **stdio** server (`python -m app.mcp.server`, launched locally by Claude Desktop) is
  unchanged and unauthenticated — it is local-only by construction (ADR-0010).
- An authenticated *external REST API* path is **NOT** built here. (Open question O3 §7 records it
  if the owner ever wants it; default = MCP-surface-only.)

### 2.3 "Allow without token" is permitted ONLY for non-public request sources; a public source ALWAYS requires the token (CRITICAL)

This is the load-bearing security decision. We classify every `/mcp/server` request as **PRIVATE**
or **PUBLIC** and let "allow without token" relax auth **only for PRIVATE**.

**A request is PUBLIC (token always required, regardless of `allow_without_token`) if ANY of:**
- It carries a Cloudflare edge header: **`CF-Connecting-IP`** or **`CF-Ray`** is present. The
  owner's public path is Cloudflare Tunnel; cloudflared injects these on every edge request.
  Their presence is a positive, hard signal of "this came through the public tunnel."
- The resolved source IP (see trust model below) is **not** in a private/trusted range.

**A request is PRIVATE (eligible for token-less access when `allow_without_token` is ON) only if
BOTH:**
- It carries **no** Cloudflare edge header (`CF-Connecting-IP` / `CF-Ray` absent), AND
- The resolved source IP is in a **trusted private range** (allow-list):
  - **loopback** `127.0.0.0/8`, `::1`
  - **Tailscale CGNAT** `100.64.0.0/10`
  - **RFC1918** `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`
  - **link-local** `169.254.0.0/16`, `fe80::/10` (and ULA `fc00::/7` for IPv6 private)

**Source-IP trust model (X-Forwarded-For spoofing — addressed):**
- **Default and fail-safe: use the transport peer IP** (`scope["client"][0]` — the actual TCP
  peer ASGI reports). **Do NOT trust `X-Forwarded-For` by default**, because a public client can
  forge it.
- `X-Forwarded-For` / `Forwarded` are honoured **only** when the immediate peer is a configured
  **trusted proxy** (`MCP_TRUSTED_PROXIES`, env, default **empty** ⇒ XFF ignored entirely). When
  the peer is trusted, take the **last** XFF hop that the trusted proxy appended (the
  proxy-attested client), not the leftmost (client-controlled) entry.
- **`CF-Connecting-IP` is treated as a PUBLIC *signal*, never as a *trust* grant.** Its mere
  presence forces PUBLIC (token required). We never use it to *grant* private access. So even if
  an attacker forges `CF-Connecting-IP`, the only effect is to make their request *more*
  restricted (PUBLIC), never less — fail-safe by construction.
- **Fail-safe default = PUBLIC (require token) when uncertain.** If the source cannot be resolved,
  is ambiguous, or any classification step is unsure, the request is treated as PUBLIC and the
  token is required. Token-less access is granted **only** on a positive PRIVATE determination.

**Net effect:** "Allow without token" is a convenience for loopback / LAN / Tailscale, and is
**structurally incapable** of opening the surface to the Cloudflare-tunnel public path. The worst
an `allow_without_token=ON` posture can do over the public tunnel is… nothing — the public path
still demands the token (or 404s if no token is configured — §2.4 row).

**Binding note (defence-in-depth, ops):** the reference's "bind 127.0.0.1" is an *ops* hardening,
not enforceable in-app here because the surface is mounted in the shared public FastAPI origin
(I9 — one origin, one tunnel). We compensate at the application layer with the source-trust gate
above. D6b documents that the owner MAY additionally put Cloudflare Access in front of
`/mcp/server` for edge-enforced auth (pure-ops, no code) — the preferred extra layer.

### 2.4 Mount/gate integration — the gate is the sole arbiter; mount whenever the surface *could* be reachable (no remount)

**Structural change from ADR-0029/0032.** Today the sub-app is mounted **only if a token is set**
(`main.py:367`). With "allow without token," the surface must be reachable **without** a token, so
the mount condition can no longer be "token set." We move to: **build + mount the sub-app once
whenever the MCP HTTP capability is compiled in; the middleware decides per-request** (still
**no remount** — ADR-0032 §2.3 stands).

- **Mount condition (new):** build `_http_mcp_asgi_app` and mount it under `MCP_MOUNT_PATH`
  **unconditionally at startup** (the FastMCP lifespan/session-manager starts once, as ADR-0032
  requires). The middleware now carries `token | None`, the `RemoteMcpFlag`, the
  `allow_without_token` flag, and the source-classifier.
- `_BearerAuthMiddleware` is extended (renamed conceptually to the *MCP access gate*) to the
  decision table below. **Order:** runtime-flag check → source classification → token/allow logic.
  Lifespan/WS scopes pass through unconditionally (unchanged).

**Decision table** (HTTP request to `/mcp/server`; `remote_enabled` = ADR-0032 flag;
`token_configured` = DB hash set **or** env bootstrap set; `allow` = `allow_without_token`):

| `remote_enabled` | source | `token_configured` | `allow` | bearer presented | **Result** |
|---|---|---|---|---|---|
| **OFF** | any | any | any | any | **404** (ADR-0032 — indistinguishable from not-mounted) |
| ON | any | any | any | valid token | **PASS** (a valid token always works) |
| ON | PRIVATE | yes | OFF | none/bad | **401** (token required; private but allow is off) |
| ON | PRIVATE | yes | **ON** | none | **PASS** (token-less allowed on private source) |
| ON | PRIVATE | **no** | **ON** | none | **PASS** (open on private source — no token exists, allow is on) |
| ON | PRIVATE | **no** | OFF | none | **404** (no token AND allow off ⇒ surface effectively closed; 404 not 401 to avoid the "protected thing exists" tell when nothing can authenticate) |
| ON | **PUBLIC** | yes | any | none/bad | **401** (public ALWAYS requires the token — §2.3) |
| ON | **PUBLIC** | **no** | any | any | **404** (public, no token can ever be presented ⇒ closed; never open the public surface token-lessly even if `allow=ON`) |

**Key rows restated for emphasis:**
- **(b) allow ON + PUBLIC source ⇒ STILL 401/404** — `allow_without_token` is *ignored* for public
  sources (§2.3). This is the whole point.
- **(d) `remote_enabled` OFF ⇒ 404** regardless of everything — ADR-0032 floor preserved.
- The **token-floor clamp of ADR-0032 §2.4 is REPLACED** by an `allow`-aware floor (§2.5):
  `remote_enabled=ON` is now permitted when **either** a token is configured **or**
  `allow_without_token=ON` (because a token-less private posture is a legitimate, owner-chosen
  configuration). Without *either*, enabling remote is pointless (the surface 404s for everyone),
  so we clamp `remote_enabled` to OFF (preserving the "no misleading ON" property).

### 2.5 Endpoints — `PUT /mcp/auth` (set/rotate token + allow flag); ADR-0032 `PUT /mcp/remote` retained with an `allow`-aware clamp

**New: `PUT /mcp/auth`** — sets/rotates the token and the allow-without-token flag.

```
PUT /mcp/auth
  request body: McpAuthRequest {
    rotate_token:        bool | None,   # true ⇒ generate a new random token, store its hash, return plaintext ONCE
    token:               str  | None,   # OR set an explicit token (owner-supplied); stored as hash, NOT echoed
    clear_token:         bool | None,   # true ⇒ delete the stored token (hash → NULL)
    allow_without_token: bool | None    # set the allow flag; omitted ⇒ unchanged
  }
  response body: McpAuthStateResponse {
    token_configured:    bool,          # hash set OR env bootstrap present
    token_source:        "db" | "env" | "none",
    allow_without_token: bool,
    remote_enabled:      bool,          # post-clamp (§2.4)
    mount_path:          str,           # "/mcp/server"
    generated_token:     str | None     # ONLY populated when rotate_token=true — shown ONCE, never again
  }
```

Semantics:
- **`rotate_token=true`:** server generates a high-entropy token (`secrets.token_urlsafe(32)`),
  stores `pbkdf2_sha256$...` in `mcp_access_token_hash`, and returns it **once** in
  `generated_token`. Subsequent GET/PUT never return it.
- **`token=<value>`:** owner supplies their own token; server stores only its hash;
  `generated_token` stays `null` (owner already knows it).
- **`clear_token=true`:** set hash `NULL`. If this leaves `token_configured=false` **and**
  `allow_without_token=false`, the `remote_enabled` clamp may force it OFF (§2.4).
- **`allow_without_token`:** persisted (new column/field — §3); if turning `allow=OFF` would leave
  no token AND remote ON, `remote_enabled` is clamped OFF and reported.
- **Plaintext token is NEVER stored and NEVER returned except the one-time `generated_token`.**
- **Auth on the endpoint itself:** same-origin / unauthenticated, **consistent with ADR-0032
  §2.4** (the network perimeter + the bearer floor are the gates; the worst an unauthenticated
  flip can do is expose a *token-protected* surface, or — for `allow_without_token` — a
  *private-source-only* surface, never a public-open one). Stated trade-off identical to ADR-0032
  §2.4; edge auth (Cloudflare Access) is the pure-ops hardening path. **However**, because
  `allow_without_token` is a more sensitive flip than the plain toggle, this is flagged as
  owner-decision **O2 (§7)**: hard-block public-open by design (chosen here) vs. add per-route auth
  on `/mcp/auth`.

**Retained: `PUT /mcp/remote`** (ADR-0032 §2.4) — unchanged shape, but its clamp becomes
`allow`-aware (§2.4): enabling is permitted when `token_configured OR allow_without_token`.

**`/mcp/info` additions** (`McpInfoResponse`, backend `main.py` + frontend `providerClient.ts`):

| Field | Type | Source | Meaning |
|---|---|---|---|
| `token_configured` | bool | `mcp_access_token_hash IS NOT NULL OR bool(env token)` | **retained**; now true for DB-or-env token. Never the value. |
| `token_source` | `"db"\|"env"\|"none"` | precedence resolver (§2.1) | which token is authoritative (lets UI say "set in UI" vs "from env"). |
| `allow_without_token` | bool | persisted flag (§3) | the new option's state. |
| `remote_enabled` | bool | `RemoteMcpFlag` | unchanged (ADR-0032). |
| `mount_path` | str | `MCP_MOUNT_PATH` constant | unchanged (I6). |

`http_enabled` / `remote_write_enabled` unchanged. **No token, no hash, no salt is ever returned
by any GET.** (`http_enabled` was `bool(env token)`; it becomes `token_configured` semantics —
retained as alias for backward compat, ADR-0032 §2.5.)

### 2.6 UI (I3 — no heavy render)

`SectionApiMcp` in `SettingsPanel.tsx` gains a small **Access** sub-block (local component state +
fetch/PUT only — no Zustand, mirrors ADR-0027/0032):
- A **token control**: "Generate token" (calls `PUT /mcp/auth {rotate_token:true}`, then shows the
  returned `generated_token` **once** in a copy-once reveal with a "you won't see this again"
  warning), "Rotate", and "Clear". When a token exists, show only `token_configured=true` +
  `token_source` — **never** the value.
- An **"Allow access without a token"** switch bound to `allow_without_token`. When ON, render an
  inline **security caveat** (i18n) explaining it applies to **private sources only** (loopback /
  LAN / Tailscale) and that the public tunnel still requires the token.
- The existing ADR-0032 remote toggle + URL row stays; its three-state display now also honours
  `allow_without_token` (remote can be ON with a token-less private posture).
- i18n keys under `settings.apiMcp.access.*` in `en.json` + `it.json` (parity gate). The token
  value and hash are **never** i18n keys.

---

## 3. New config / schema / migration

| Kind | Name | Type / default | Where | Notes |
|---|---|---|---|---|
| DB column | `vault_state.mcp_access_token_hash` | `TEXT NULL` | `models.py` `VaultState` | **NEW — Alembic `0012`; D2/ER MUST regenerate.** Salted PBKDF2 hash; never plaintext. |
| DB column | `vault_state.mcp_allow_without_token` | `BOOLEAN NOT NULL DEFAULT false` | `models.py` `VaultState` | **NEW (same migration `0012`).** Persisted "allow without token" flag. Default OFF (fail-closed). |
| migration | `0012_vault_state_mcp_access_token_and_allow` | add 2 columns | `backend/alembic/versions/` | Run by standard `alembic upgrade head`. D2/ER regen (I8). |
| env (retained) | `MCP_AUTH_TOKEN` | secret str, no default | `config.py` | **Now a BOOTSTRAP fallback** (§2.1): used iff DB hash is NULL. Existing deployments keep working. |
| env (new) | `MCP_TRUSTED_PROXIES` | comma-list of CIDRs, default **empty** | `config.py` | Peers whose `X-Forwarded-For` is trusted (§2.3). Empty ⇒ XFF ignored (fail-safe). |
| constants | `MCP_PRIVATE_CIDRS` | tuple of CIDRs | `main.py`/module | loopback/CGNAT/RFC1918/link-local/ULA (§2.3). Named constant (I6 — no scattered literals). |
| route | `PUT /mcp/auth` | `McpAuthRequest` → `McpAuthStateResponse` | `main.py` | Set/rotate/clear token + allow flag. One-time `generated_token`. |
| route (retained) | `PUT /mcp/remote` | unchanged shape | `main.py` | Clamp becomes `allow`-aware (§2.4). |
| response fields | `McpInfoResponse.{token_source, allow_without_token}` (+ retained `token_configured`) | str, bool | `main.py` + `providerClient.ts` | Token/hash NEVER returned. |
| derived | `settings.mcp_http_enabled` | bool | `config.py` | **Now `True` unconditionally** (capability compiled in) OR derived from "token-or-allow can ever apply" — implementer aligns with the new always-mount (§2.4). The *reachability* is decided by the gate, not by mount. |

**One migration, two columns.** Flagged for the docs gate: `make er` + `make openapi` + D5/D6b.

---

## 4. Per-agent file ownership

**backend-engineer:**
- `backend/app/models.py` — add `mcp_access_token_hash` (Text, nullable) + `mcp_allow_without_token` (Bool, default false) to `VaultState`.
- `backend/alembic/versions/0012_vault_state_mcp_access_token_and_allow.py` — add the two columns; downgrade drops them.
- `backend/app/config.py` — keep `mcp_auth_token` as bootstrap; add `mcp_trusted_proxies` parse; align `mcp_http_enabled` with always-mount (§2.4/§3).
- `backend/app/main.py` —
  - token hashing/verification helpers (PBKDF2 via stdlib `hashlib`; `secrets.token_urlsafe` for generation; `hmac.compare_digest` on the hash);
  - precedence resolver `db hash → env → none` + `token_source`;
  - **source classifier** (peer-IP + CF-header → PRIVATE/PUBLIC; XFF only via `MCP_TRUSTED_PROXIES`; fail-safe PUBLIC) — `MCP_PRIVATE_CIDRS` named constant;
  - extend the MCP access gate (formerly `_BearerAuthMiddleware`) to the §2.4 decision table; **always-mount** the sub-app (drop the token-conditional mount at `main.py:367`); keep no-remount;
  - `PUT /mcp/auth` + `McpAuthRequest`/`McpAuthStateResponse` (one-time `generated_token`; never echo otherwise);
  - make `PUT /mcp/remote` clamp `allow`-aware;
  - add `token_source`, `allow_without_token` to `McpInfoResponse` + `get_mcp_info`; load `mcp_allow_without_token` into the in-process posture at startup (alongside `RemoteMcpFlag`).
- `backend/tests/` — decision-table tests (every row of §2.4); PRIVATE vs PUBLIC classification (loopback/CGNAT/RFC1918 ⇒ PRIVATE; `CF-Connecting-IP`/`CF-Ray` present ⇒ PUBLIC even from a private peer; forged XFF ignored when peer untrusted; forged `CF-Connecting-IP` only ever *restricts*); token hash round-trip + constant-time verify; **no token/hash in any response** (grep); env-bootstrap precedence; clamp behaviour.

**frontend-engineer:**
- `frontend/src/api/providerClient.ts` — extend `McpInfoResponse` (`token_source`, `allow_without_token`); add `McpAuthRequest`/`McpAuthStateResponse` + `setMcpAuth()`.
- `frontend/src/components/settings/SettingsPanel.tsx` — `SectionApiMcp` Access sub-block: generate/rotate/clear token, one-time reveal, "allow without token" switch + security caveat; local state only (I3).
- `frontend/src/i18n/en.json`, `it.json` — `settings.apiMcp.access.*` (parity).
- `frontend/src/tests/` — vitest: token never rendered when configured; `generated_token` shown once; allow-switch caveat present; PUT shapes.

**tech-writer:**
- `docs/er/schema.mmd` — `make er` (D2; two new columns).
- `docs/api/openapi.json` — `make openapi` (D4; `PUT /mcp/auth`, new `/mcp/info` fields).
- `docs/DEPLOY.md` (D6b) — token now UI-settable (env = bootstrap fallback); "allow without token" is **private-source-only**, public tunnel always needs the token; `MCP_TRUSTED_PROXIES` guidance; recommend optional Cloudflare Access on `/mcp/server`.
- `docs/screens/settings-api-mcp.png` — refresh via Playwright (D5).
- **Amend ADR-0029 (§2.2) and ADR-0032 (§2.4 clamp):** add a "Superseded in part by ADR-0033" note to each (token now DB-hash-with-env-bootstrap; clamp now allow-aware). One-line cross-refs only — do not rewrite them.

**devops-engineer:** one-line note — migration applied by standard `alembic upgrade head`; **verify cloudflared sets `CF-Connecting-IP`** on tunnel requests (it does by default) and document the `MCP_TRUSTED_PROXIES` posture (default empty = trust only the transport peer). No new service/port/env beyond `MCP_TRUSTED_PROXIES`.

---

## 5. Acceptance checks (DoD)

1. **Token UI round-trip.** `PUT /mcp/auth {rotate_token:true}` returns `generated_token` once; a
   subsequent bearer with that token reaches the tools; `/mcp/info` shows `token_configured=true`,
   `token_source="db"`, and **never** the token/hash. A second GET/PUT never re-returns the value.
2. **Hash storage, no plaintext.** `mcp_access_token_hash` is a PBKDF2 string; `grep`/DB dump shows
   no plaintext token anywhere; verification is constant-time.
3. **Env bootstrap precedence.** With DB hash NULL and `MCP_AUTH_TOKEN` set, the env token
   authenticates (`token_source="env"`). After `PUT /mcp/auth` sets a DB token, the DB token wins
   (`token_source="db"`); the env token no longer authenticates.
4. **PRIVATE allow-without-token works.** `remote_enabled=ON`, `allow_without_token=ON`, no token,
   request from loopback/CGNAT/RFC1918 peer with **no** CF header ⇒ **PASS** (tools reachable).
5. **PUBLIC always requires token (CRITICAL).** Same posture but the request carries
   `CF-Connecting-IP` (or `CF-Ray`), or comes from a non-private peer ⇒ **401** (if a token is
   configured) or **404** (if none) — token-less PUBLIC access is **impossible**. Verified for both
   "CF header present from a private peer" and "non-private peer, no CF header."
6. **XFF spoof ignored.** A request from an untrusted peer with a forged
   `X-Forwarded-For: 127.0.0.1` is classified by the **peer** IP (PUBLIC) ⇒ token required. With a
   peer listed in `MCP_TRUSTED_PROXIES`, the proxy-attested last hop is used.
7. **CF-header forge only restricts.** A request with a forged `CF-Connecting-IP` is forced PUBLIC
   (never granted private access) — the forge cannot *relax* auth.
8. **Decision table.** Every row of §2.4 has a passing test, including `remote_enabled=OFF ⇒ 404`
   (ADR-0032 floor) and `no token + allow OFF ⇒ 404`.
9. **Allow-aware clamp.** `PUT /mcp/remote {enabled:true}` succeeds when `allow_without_token=ON`
   even with no token (private-open posture); clears to OFF when neither token nor allow is set.
10. **No remount / session manager stable.** Flipping any flag never restarts the StreamableHTTP
    session manager (mounted+started once — ADR-0032 §2.3).
11. **I1/I5 unchanged.** `grep` proves HTTP `write_page` (when exposed) still routes through
    `write_wiki_page`.
12. **Docs gate.** `make er` matches live schema (two new columns); `make openapi` includes
    `PUT /mcp/auth` + new fields; D5 refreshed; ADR-0029/0032 amendment notes added (I8).

---

## 6. Consequences

**Positive** — the owner gets the nashsu/llm_wiki auth UX (UI-settable token + "allow without
token") without weakening the public surface: token-less access is **structurally impossible** over
the Cloudflare tunnel (§2.3 fail-safe). The token is stored as a salted hash (a DB/backup leak
yields no usable credential), honouring §12's spirit while correctly exempting an app-issued access
credential from ADR-0008's *provider-key* rule. Env `MCP_AUTH_TOKEN` stays as a bootstrap ⇒ zero
breakage for existing deployments. Reuses `vault_state` + the existing mount + FastMCP — no second
process, no new tool, no broadened surface (I9). No remount (ADR-0032 §2.3 stands).

**Trade-offs (explicit)** —
- **One migration, two columns** (the only schema change); D2/ER regen in the docs gate.
- **Lost tokens cannot be recovered — only rotated** (hash storage). This is the more secure UX and
  is intentional; the UI says so.
- **Per-request PBKDF2** on the presented token when a token is required — negligible at personal
  MCP volumes; an in-process verified-token cache is a documented escape hatch if ever needed.
- **`allow_without_token` is owner-settable from an unauthenticated same-origin endpoint** — but it
  can only ever open a **private-source-only** posture (public stays token-gated), so the worst case
  is bounded. Edge auth (Cloudflare Access) remains the pure-ops hardening path. See O2 (§7).
- **Source classification depends on `CF-Connecting-IP`/peer-IP correctness.** Mitigated by the
  fail-safe-PUBLIC default and by never trusting XFF or CF headers to *grant* access (only to
  *restrict*). If the owner runs a different public proxy that does not set CF headers, they must
  ensure that proxy is NOT in `MCP_PRIVATE_CIDRS` and is reached such that the peer IP is public
  (documented in D6b).

**Invariant check** — **I1/I5:** vault-write path unchanged (same `write_wiki_page`; `write_page`
posture still env-only per ADR-0029 §2.3, untouched here). **I3:** UI is a token field + a switch +
one PUT, local state, no store churn. **I6:** mount path, private CIDRs, and IP ranges are named
constants; no provider, host, or token hardcoded. **I7:** no new loop. **I8:** migration ⇒ D2/ER
regen + D4/D5 refresh + ADR amendments. **I9:** reuses `vault_state` + the existing ADR-0029 mount +
existing FastMCP server; adds NO MCP capability, NO second writer, NO second process, NO broadened
tool set. **I2/I4 untouched.** The ADR-0008 tension is resolved by *categorising* the credential
(app-issued access token ≠ third-party provider key) and by hashing — not by trading §12 away.

---

## 7. Decisions the owner must make before coding

- **O1 — Token storage: salted HASH (recommended, built here) vs re-displayable PLAINTEXT.** This
  ADR builds the **hash** (DB-leak-safe; token shown once, then rotate-only). If the owner strongly
  prefers being able to *re-view* the token in the UI later (plaintext in DB), that re-introduces a
  recoverable secret in Postgres (against §12's spirit) and is **not recommended** — but it is the
  owner's call. **Default: hash.**
- **O2 — Should token-less PUBLIC be HARD-BLOCKED (chosen here) or ALLOWED-WITH-WARNING?** This ADR
  **hard-blocks** token-less public access by design (public always requires the token). The
  alternative — allow public-open with a scary UI warning — is **explicitly not recommended**
  (a vault-mutating-capable open endpoint on the public internet). Confirm the hard-block. *(Also:
  do we additionally want per-route auth on `PUT /mcp/auth` itself, beyond the same-origin posture
  ADR-0032 settled? Default = no, consistent with ADR-0032 §2.4.)*
- **O3 — Authenticated external REST API (out of scope, noted).** This ADR gates **only**
  `/mcp/server`; the rest of the REST API stays unauthenticated (else the browser UI breaks). If the
  owner ever wants an authenticated external REST path, that is a separate ADR. **Default:
  MCP-surface-only.**
