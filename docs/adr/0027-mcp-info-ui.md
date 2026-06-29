# ADR-0027 — Read-only MCP server introspection endpoint + Settings panel (F1-MCP-UI)

- **Status:** Accepted
- **Date:** 2026-06-29
- **Sprint:** v0.5 (M5 Phase 5)
- **Feature:** F1-MCP-UI (Amendment A1 — stakeholder request, Emanuele Chiummo); extends F1 (Settings shell) + surfaces F17/MCP infra
- **Builds on:** ADR-0010 (MCP server: stdio transport; the server itself, its 4 tools, and the `python -m app.mcp.server` entry point were fully delivered in v0.2/M2). This ADR does NOT change the MCP server — it only reflects it.
- **Invariants owned:** **I9** (HEADLINE — display only; reuse the EXISTING FastMCP server, add NO new MCP capability, NO tool invocation, NO config-write) · **I6** (server name + tool list + schemas DERIVED from the live `mcp` object; nothing about the MCP server hardcoded) · I3 (panel uses fetch-on-mount + typed state, mirrors `SectionEmbeddings`; no store churn)
- **Author:** solution-architect

---

## 1. Context

The Settings > **API + MCP** section (`SectionApiMcp` in `frontend/src/components/settings/SettingsPanel.tsx`)
currently renders a `ComingSoonBadge` (i18n key `settings.apiMcp.comingSoon`) — a promise made
during M4-HARD. PM Amendment A1 (2026-06-29) closes that gap by making the promise real, as a
**lean, read-only display feature**. The MCP server has been live since v0.2:

- Server instance: `app.mcp.server.mcp` — a `FastMCP(name="synapse", ...)` (fastmcp 3.4.2).
- Transport: **stdio** (ADR-0010 §1).
- Entry point: **`python -m app.mcp.server`** (the `if __name__ == "__main__": mcp.run(transport="stdio")` block).
- Tools (verified by introspecting the live registry, see §2.2): **`search_wiki`, `write_page`,
  `get_page`, `list_pages`** — exactly the four the amendment assumed; **no name correction needed**.

The headline invariant is **I9**: do not reinvent MCP. F1-MCP-UI must NOT add a second MCP
server, MUST NOT call any MCP tool, MUST NOT duplicate the tool logic in `server.py`, and MUST
NOT add CRUD/config-write. It is a window onto the existing server, nothing more.

The only new backend work is one read-only introspection endpoint, mirroring the existing
`GET /config/embedding` (which already exposes read-only, env-derived config to a Settings panel).

---

## 2. Decision

### 2.1 Backend — `GET /mcp/info` (read-only introspection)

Add one endpoint to `backend/app/main.py` (alongside `GET /config/embedding`, same shape of thing).
Response model:

```
McpToolInfo:
  name:         str           # tool.name
  description:  str            # tool.description (full; the UI truncates)
  input_schema: dict           # tool.parameters — the JSON-Schema object for the tool args

McpInfoResponse:
  server_name:          str            # mcp.name → "synapse"        (I6: from the live object)
  transport:            str            # settings.mcp_transport, default "stdio"  (I6: from settings)
  entry_point_command:  str            # "python -m app.mcp.server"  (from settings, see §2.3)
  tool_count:           int            # len(tools)
  tools:                list[McpToolInfo]
```

**Every value is derived from the live `mcp` object + `settings`** (I6). No tool name, description,
schema, or server name is a literal in the handler.

### 2.2 How the FastMCP registry is introspected (the await-in-handler note)

fastmcp 3.4.2 exposes the registry via an **async** coroutine:

```
tools = await mcp.list_tools()          # -> list[FunctionTool]
# each FunctionTool:
#   .name          -> str
#   .description    -> str | None
#   .parameters     -> dict   (the JSON-Schema input schema, e.g.
#                              {"type":"object","properties":{...},"required":[...],
#                               "additionalProperties": false})
```

> **Verified facts (fastmcp 3.4.2, live `synapse` server):** `mcp.name == "synapse"`;
> `await mcp.list_tools()` returns 4 `FunctionTool` objects; `.parameters` carries the input
> schema (NOT `.inputSchema`, which is `None` on `FunctionTool`); `get_tools()` does **not**
> exist in this version — use `list_tools()`.

**The async concern, handled cleanly:** `list_tools()` is a coroutine, but the FastAPI route
handler `async def get_mcp_info(...)` is itself async — so the handler simply `await`s it
directly. **No `asyncio.run()`, no thread-pool bridge, no await-in-sync.** This is pure
import-time introspection of the already-constructed `mcp` object: **no DB query, no Qdrant call,
no live MCP transport/stdio session** is opened. The endpoint reflects the code-level registry
as it exists at process start.

(If a future fastmcp drops the async `list_tools`, the fallback is the synchronous per-tool
accessor `mcp.get_tool(name)`; but for v0.5 the single `await mcp.list_tools()` call is the
contract.)

### 2.3 Transport + entry-point command come from settings (I6)

Add two read-only settings to `app/config.py` (env-overridable, sensible defaults — mirrors how
`embedding_url` etc. are read):

- `mcp_transport: str = "stdio"` (env `MCP_TRANSPORT`) — must stay consistent with ADR-0010.
- `mcp_entry_command: str = "python -m app.mcp.server"` (env `MCP_ENTRY_COMMAND`) — the documented
  run entry point (verified: `server.py` docstring + `__main__` block).

The handler returns `settings.mcp_transport` / `settings.mcp_entry_command`. No string about the
MCP server is hardcoded inside the route function (I6).

### 2.4 Frontend — replace the `SectionApiMcp` stub

Replace the `ComingSoonBadge` body of `SectionApiMcp` with a real read-only panel that **mirrors
`SectionEmbeddings`** (the existing read-only config panel): `useState` for the fetched payload +
a degraded-state boolean, `fetchMcpInfo(signal)` on mount via an `AbortController`, three render
states (error → degraded message; `null` → loading; loaded → content). No new Zustand store, no
store churn (I3). Add a typed `fetchMcpInfo()` to the existing API client alongside
`fetchEmbeddingConfig`. The panel has two sub-sections:

**Connection** (mirrors the `EmbedRow` mono-value rows):
- Transport (`info.transport`) and Entry-point command (`info.entry_point_command`) as labelled
  mono rows.
- A **copy-to-clipboard** button for the Claude Desktop config snippet. The snippet is
  **generated from the API payload**, not a JSX literal (I6):
  ```
  { "mcpServers": { [info.server_name]: { "command": <argv0 of entry_point_command>,
                                          "args": <rest of entry_point_command split> } } }
  ```
  i.e. `info.entry_point_command` is tokenised so the JSON is built from the real server metadata
  (server_name keyed dynamically; command/args derived). The displayed JSON is the single source
  copied to the clipboard.

**Tools** (sourced exclusively from `info.tools`):
- One row per tool: `tool.name` (mono), the **first sentence of `tool.description` truncated to
  80 chars**, and the **param count** = number of keys in `tool.input_schema.properties` (0 if
  absent). No tool name or description is hardcoded in the frontend (I6/I9).

**i18n:** all strings under `settings.apiMcp.*` in `en.json` + `it.json` (key-set parity, the
existing gate). Replace/retire `settings.apiMcp.comingSoon`; add keys for the Connection/Tools
labels, copy button, copied confirmation, and the degraded-state message. Values that come from
the API (server name, transport, tool names/descriptions, the JSON snippet) are NEVER i18n keys —
only the surrounding labels are translated.

### 2.5 Docs (I8)

- **D4:** `GET /mcp/info` appears in `openapi.json` after `make openapi` — zero-drift gate.
- **D5:** `docs/screens/settings-api-mcp.png` captured by Playwright in `make screenshots`.
- **D2:** unchanged — **no schema change** (no DB table, no migration).

---

## 3. Consequences

**Positive**
- The promised panel becomes real with the smallest possible surface: one read-only endpoint +
  one panel + i18n. No DB, no migration, no new MCP capability.
- I6 holds end-to-end: if a tool is added/renamed/removed in `server.py`, the UI updates
  automatically — there is no second list to keep in sync.
- I9 holds by construction: the UI cannot invoke a tool or mutate config — it has no code path to.
- Mirrors two proven patterns (`GET /config/embedding` backend, `SectionEmbeddings` frontend),
  minimising review surface and regression risk.

**Trade-offs / limitations (stated explicitly)**
- `GET /mcp/info` reflects the **import-time** registry, not a live stdio handshake. This is
  intentional (no transport session is opened) and correct for a static, code-defined tool set.
  If tools ever become dynamically registered at runtime, this endpoint would need to re-introspect
  per request — acceptable, since `list_tools()` is already called per request (cheap, in-process).
- The copy-to-clipboard snippet assumes a simple `command + args` launch (the documented stdio
  entry point). Bespoke launch wrappers (e.g. docker exec) are out of scope for M5; the snippet is
  a correct default derived from `mcp_entry_command`, editable by the user after pasting.
- Coupling to fastmcp 3.4.2's `list_tools()`/`.parameters` API. Mitigated by the §2.2 fallback note
  and pinned `fastmcp>=2.0.0` in `pyproject.toml`; a major fastmcp bump must re-verify this call.

**Verification (AC mapping)**
- AC-F1-MCP-UI-1/2/8 → §2.1–2.3 (endpoint, no-hardcode, openapi). 
- AC-F1-MCP-UI-3/4/5/6 → §2.4 (stub replaced, connection, tools, i18n parity).
- AC-F1-MCP-UI-7 → §3 (I9: no second server, no tool call, no logic duplication).
- AC-F1-MCP-UI-9/10 → §2.5 + tests (pytest: 200, `tool_count >= 4`, `server_name == "synapse"`,
  `entry_point_command` non-empty; vitest: 4 tool names rendered, copy button present).

---

## 4. Do NOT (F1-MCP-UI guardrails — reject any PR that does these)

1. **Do NOT hardcode the tool list** (or any tool name/description/schema) anywhere in backend or
   frontend — always from `await mcp.list_tools()` → `GET /mcp/info` (I6/I9).
2. **Do NOT hardcode the server name, transport, or entry command** in the route handler or JSX —
   from `mcp.name` / `settings.mcp_transport` / `settings.mcp_entry_command` (I6).
3. **Do NOT invoke an MCP tool from the UI** (no call into search_wiki/write_page/get_page/
   list_pages, no MCP stdio session opened by the endpoint) — display only (I9).
4. **Do NOT add config-write / CRUD / mutation** to this panel — read-only; any write capability is
   out of scope and an escalation (PM §5 defers tool invocation/CRUD/config-write to M6).
5. **Do NOT add a new MCP capability, a second MCP server, or duplicate `server.py` tool logic** —
   reuse the existing FastMCP server (I9).
6. **Do NOT add a DB table or migration** — this feature is migration-free (D2 unchanged).
7. **Do NOT add a Zustand store for the panel** — fetch-on-mount local state, mirror
   `SectionEmbeddings` (I3, no store churn).
