# ADR-0072 — Runtime toggle for remote MCP write tools (Settings UI)

- **Status:** Accepted
- **Date:** 2026-07-13
- **Amends:** ADR-0029 §2.3 (env-gated `MCP_REMOTE_WRITE_ENABLED`), extends ADR-0032 (runtime remote toggle), ADR-0033 (token posture)
- **Invariants touched:** I6 (pluggable inference — single write path preserved), I9 (no second writer)

## Context

The HTTP MCP surface (`/mcp/server`, ADR-0029/0032/0033) exposes six read-only tools always,
and three write tools (`write_page`, `resolve_review`, `trigger_source_rescan`) **only when
`MCP_REMOTE_WRITE_ENABLED=true`**. That flag is read **once at process startup**
(`main.py`: `build_http_mcp(write_enabled=settings.mcp_remote_write_enabled)`) and baked into
the mounted FastMCP instance. Consequences:

- It **cannot** be changed without editing an env var **and restarting the backend**.
- The Settings UI (*API & MCP* section) shows it only as a **read-only badge**
  ("read-only" / "read + write") — there is no control.

The owner wants to toggle remote writes from Settings at runtime, mirroring the existing
`remote_enabled` toggle (ADR-0032), which is already a DB-persisted, hot-swappable flag.

## Decision

Promote remote-write-enabled from an env-only startup constant to a **DB-persisted runtime
flag** with a Settings toggle, mirroring `vault_state.remote_mcp_enabled` exactly.

### 1. Persistence — `vault_state.remote_mcp_write_enabled` (nullable BOOLEAN)

New **nullable** column (Alembic `0030`). Precedence mirrors `_ClipConfigCache` (ADR-0040):
**DB value wins when set (non-NULL); else fall back to env `MCP_REMOTE_WRITE_ENABLED`.**
Nullable-default-NULL means existing deployments keep their env behaviour until the owner
first toggles from the UI, at which point the DB becomes authoritative. No data migration of
existing env config is needed.

### 2. In-process cache — `_mcp_write_flag`

A `RemoteMcpFlag`-shaped singleton in `main.py` (reuse the class; it is generic). Loaded at
startup from `effective = db_value if db_value is not None else settings.mcp_remote_write_enabled`.
`is_enabled()` is O(1), no I/O. Updated by the PUT endpoint after the DB write.

### 3. Registration model — **always register, guard at call time**

`build_http_mcp` gains a `write_enabled_getter: Callable[[], bool] | None` parameter. When a
getter is supplied (the HTTP surface), the three write tools are **always registered** but each
body checks `write_enabled_getter()` first and returns a structured
`{"error": "remote writes are disabled; enable them in Settings → API & MCP"}` when off — no
exception, consistent with every other tool-body error contract. When no getter is supplied
(legacy/static callers, tests), the previous static `write_enabled: bool` behaviour is retained
for backward compatibility.

**Rationale for always-register-guard over rebuild-and-remount:** remounting a Starlette
sub-app at runtime is fragile and unsupported; guarding at call time is how ADR-0032 already
models `remote_enabled`. The single write path (`write_wiki_page`, I6/I9) is untouched — the
guard sits *in front of* it, adds no second writer. `mcp/server.py` must **not** import
`main.py` (circular); the getter closure is injected from `main.py`, keeping `mcp/server.py`
import-clean.

### 4. Endpoint — `PUT /mcp/remote-write`

New endpoint mirroring `PUT /mcp/remote`. Body `{enabled: bool}`. Same-origin, unauthenticated
(consistent with the rest of the REST API, ADR-0028). Persists `vault_state`, refreshes
`_mcp_write_flag`.

**Clamp (fail-safe):** enabling writes requires an auth posture that lets the remote surface
serve at all — mirror the remote-toggle clamp: `enabled=true` is honoured only when
`token_configured OR allow_without_token`; otherwise it is clamped to `false` with
`clamped=true` (HTTP 200). `enabled=false` always succeeds. Disabling remote (`PUT /mcp/remote
{enabled:false}`) does **not** clear the write flag — write is a sub-capability that simply has
no effect while the surface is 404 for everyone; re-enabling remote restores the prior write
posture. (Simpler, matches how token/allow are independent of the remote flag.)

### 5. Introspection — `GET /mcp/info`

`remote_write_enabled` now reports **`_mcp_write_flag.is_enabled()`** (the effective runtime
value), not the raw env var. Field description updated. No token/hash ever returned (ADR-0033).

### 6. UI — Settings → API & MCP

Replace the read-only write **badge** with a **toggle switch** (reuse the exact switch markup
of the existing `data-testid="mcp-remote-toggle"`; new `data-testid="mcp-remote-write-toggle"`).
Visible only when `remoteEnabled`. Disabled when `!canEnableRemote` (same gate as the remote
toggle). `onChange` calls a new `providerClient.setMcpRemoteWrite(enabled)` → `PUT
/mcp/remote-write`, then refreshes `fetchMcpInfo`. New i18n keys under
`settings.apiMcp.remote.*` (EN + IT): `writeToggleLabel`, `writeToggleNote`. Keep the existing
`readOnlyBadge`/`readWriteBadge` keys (still used as the switch's inline state hint).

## Security posture (explicit trade-off, owner-accepted)

Before: a leaked bearer token could never mutate the vault unless the operator set an env var
and restarted — writes were **out of band** from the network surface. After: writes can be
enabled from the same-origin Settings API. This is weaker defence-in-depth, accepted because
(a) the instance is single-tenant and already behind Cloudflare Access + Tailscale, and (b) the
clamp still forbids enabling writes without a token (or an explicit allow-without-token for
private sources). The env var remains as the **initial default** (bootstrap) for fresh vaults.

## Consequences

- Write tools are now always *listed* on the HTTP surface (discovery shows them) even when the
  flag is off; they error clearly at call time. `tool_count` in `/mcp/info` reflects this.
- One new nullable column, one migration (`0030`), one new endpoint, one UI toggle. stdio MCP
  and the in-process SDK MCP server are **unchanged** — they always have all tools (I6).
- Tests: backend (toggle persist, clamp, guard-when-off returns error dict, guard-when-on
  writes) + frontend (SettingsPanel toggle renders, calls client, respects `canEnableRemote`).

## Alternatives rejected

- **Rebuild + remount FastMCP on toggle** — fragile ASGI sub-app remounting; rejected (§3).
- **Keep env as a hard ceiling, DB toggle only under it** — a UI toggle that silently does
  nothing when the env is false is confusing; rejected in favour of DB-wins precedence.
