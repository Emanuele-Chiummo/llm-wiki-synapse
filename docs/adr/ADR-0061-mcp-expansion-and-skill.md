# ADR-0061 — MCP tool expansion + review bulk-resolve + installable agent skill (README-delta parity, B5/D2)

- **Status:** Accepted
- **Date:** 2026-07-06
- **Sprint:** B5 (README-delta parity — `feat/b5-mcp-skill`)
- **Feature:** F17-adjacent (MCP surface) + F9 (review queue) · closes UI-ALIGNMENT-PLAN-2026-07 §B5 row **D2**
- **Extends:** ADR-0010 (MCP transport + shared write path) · ADR-0029/0032/0033 (remote HTTP surface, runtime toggle, UI token + source-trust gate) · ADR-0044 (review bulk actions + `status` filter) · ADR-0034 (review proposal model)
- **Invariants owned:** **I6** (every new tool reuses an existing provider-neutral seam; NO tool touches `InferenceProvider`, NO provider is hardcoded) · **I9** (reuse the existing FastMCP server, `rag.retrieval`, the graph engine, and the watcher rescan — no new search/graph/gateway) · **I1** (the write tools are incremental upserts / status writes; the rescan is the incremental watcher, never a full re-scan) · **I7** (every read is capped: `depth ≤ 2`, `limit ≤ 100`, `ids ≤ 200`; no new loop) · **I5** (write tools keep the ADR-0010 §2 shared validated write path)
- **Author:** solution-architect
- **Implementers:** backend-engineer (MCP tool bodies + `build_http_mcp` gating + `POST /review/queue/bulk-resolve` + `PATCH /review/queue/{id}`) · ai-agent-engineer (co-sign the tool contracts + `tools/synapse-skill/SKILL.md` trigger discipline) · tech-writer (D4 MCP reference + OpenAPI regen)

---

## 1. Context

The current llm_wiki README (2026-07) has evolved past the v0.5.4 audit baseline. Its local
service now exposes an **MCP server with a richer tool surface** (graph neighbourhood, review
listing/resolution, source-file read, source rescan), a **review bulk-resolve + PATCH** REST
surface, and an **installable agent skill** (`npx skills add llm_wiki_skill`) that lets a Claude
Code / Codex agent operate the wiki from a disciplined trigger.

Synapse's MCP server (`backend/app/mcp/server.py`) exposes exactly **four** tools —
`search_wiki`, `write_page`, `get_page`, `list_pages` — split into read-only bodies plus one
gated write body (`write_page`), with `build_http_mcp(write_enabled=...)` deciding whether the
HTTP surface registers the write tool (ADR-0029 §2.3 / ADR-0033 §2.4). The review queue has
per-item actions and a `POST /review/queue/bulk` (ADR-0044) but **no `bulk-resolve` verb and no
`PATCH`**. There is no packaged agent skill.

This is a **parity gap, not a redesign.** The abstraction is already correct: read bodies +
one write body, a stdio `mcp`, an in-process SDK server, and a token-gated HTTP surface. This
ADR extends that surface with more tools of the *same two kinds* and one narrow REST pair, and
adds a trigger-disciplined skill package. It does **not** change transport, auth, the write
path, or provider routing.

---

## 2. Decision

### 2.1 Three new READ-ONLY MCP tools (bounded, reuse existing seams)

Added as new shared `_*_body` functions and registered on the stdio `mcp`, the `build_http_mcp`
read set (always present, like `get_page`/`list_pages`), and the SDK server:

| Tool | Signature | Seam reused (I9) | Bound (I7) |
|---|---|---|---|
| `get_graph_neighborhood` | `(title: str, depth: int = 1)` | the existing graph engine / links tables (ADR-0012/0016) — **read the persisted edges, never recompute layout** (I2) | `depth` clamped to `1..2`; **2 is the hard ceiling** |
| `list_reviews` | `(status: str \| None = None, limit: int = 50)` | `ops.review` list path + the ADR-0044 `status` filter | `limit` clamped to `1..100`; excludes nothing new |
| `read_source_file` | `(path: str)` | reads a file under the vault `raw/sources/` root only | path resolved and confined to `raw/sources/` (§2.4) |

All three are **read-only**: they never mutate the vault, so they are registered on the HTTP
surface unconditionally (same class as `search_wiki`/`get_page`/`list_pages`) and require no
`write_enabled`. `get_graph_neighborhood` reads the **already-computed** graph — it must not
trigger a layout or a graph recompute (I2 stands; it is a lookup, mirroring how `search_wiki`
is a lookup, not the F5 pipeline rebuild).

### 2.2 Two new WRITE MCP tools (gated exactly like `write_page`)

| Tool | Signature | Effect | Gating |
|---|---|---|---|
| `resolve_review` | `(id: str, action: str)` | set a review item's status via the **existing** `ops.review` seam (§2.5 action set) | `write_enabled` only — same gate as `write_page` |
| `trigger_source_rescan` | `()` | enqueue the **incremental watcher** rescan of `raw/sources/` (I1) — never a full re-scan | `write_enabled` only |

Both are **mutating**, so they follow `write_page` exactly: registered on the stdio `mcp` and
the SDK server unconditionally, but on the HTTP surface **only inside the
`if write_enabled:` block of `build_http_mcp`** (ADR-0029 §2.3). Remote/HTTP therefore stays
**read-only by default** (ADR-0033 §2.4 decision table unchanged) — a leaked token cannot
resolve reviews or trigger a rescan unless the operator explicitly opted the write surface in.
`resolve_review` reuses `ops.review._set_status` / the existing action handlers, so terminal-
status and idempotency guarantees (ADR-0044) hold identically — no second review writer.
`trigger_source_rescan` calls the existing watcher entry point; it does **not** implement a new
scan (I1/I9).

### 2.3 REST — `POST /review/queue/bulk-resolve` + `PATCH /review/queue/{id}`

Parity with the llm_wiki "reviews export/PATCH/bulk-resolve" surface, layered on ADR-0044:

- **`POST /review/queue/bulk-resolve {ids: list[str], action: str}`** — apply one action to up
  to **200** items (`ids ≤ 200`, request rejected `422` above the cap). Bounded DB writes over
  the existing `_set_status` / action handlers; no loop over an unbounded set (I7). This is a
  distinct verb from ADR-0044's `POST /review/queue/bulk` (which is UI-selection bulk over the
  live statuses); `bulk-resolve` takes an explicit id list + action for API/agent callers.
- **`PATCH /review/queue/{id} {action: str}` (or status field)** — single-item state transition
  through the same seam, for callers that prefer a REST PATCH over the per-verb routes.

Both route into the **existing** `ops.review` handlers — no new review logic, no new table, no
migration (D2/ER unchanged, I8 has nothing to regenerate here beyond OpenAPI).

### 2.4 `read_source_file` is confined to `raw/sources/` (path safety — CRITICAL)

`read_source_file(path)` MUST resolve the requested path against the vault `raw/sources/` root
and **reject anything that escapes it** (`..`, absolute paths, symlink traversal). The
implementation resolves to a real path and verifies it is a descendant of the `raw/sources/`
directory; a miss returns a structured `{"error": ...}` (never an exception, never file bytes
from elsewhere). It reads **source inputs only** — never `wiki/`, never `.obsidian/`, never
config, never anything outside `raw/sources/`. This mirrors the reference's allow-list file-read
posture and keeps the tool from becoming an arbitrary-file-read primitive over the token-gated
HTTP surface.

### 2.5 `resolve_review` action set = the existing review actions

`resolve_review(id, action)` accepts only the **already-defined** review actions/statuses from
ADR-0034/0044 (`skip`, `dismiss`, `mark-resolved`/`resolved`, and — where the item supports it —
`create` / `deep-research` via their existing handlers). No new status value is introduced. An
unknown action returns a structured error. `bulk-resolve` shares this same action validation.
Terminal statuses (ADR-0044) are never re-mutated.

### 2.6 Agent skill — `tools/synapse-skill/SKILL.md`, trigger-disciplined

Ship an installable skill that drives the REST/MCP surface, mirroring llm_wiki's
trigger-disciplined `llm_wiki_skill`:

- **Trigger discipline (load-bearing):** the skill activates **only** on an explicit
  reference to **"Synapse"** or **"my wiki"** (the user's own vault). It MUST NOT trigger on a
  generic "search my notes", "look something up", or any ambient note/file phrasing. The skill
  description states the trigger narrowly so it does not hijack unrelated requests.
- It operates entirely through the **existing** REST endpoints and MCP tools defined above
  (search/read/graph/review/rescan/write); it introduces **no** new capability and **no**
  provider logic (I6/I9). It is a client of the surface, not part of it.
- Lives in `tools/synapse-skill/` (backend-agent owned); this ADR fixes only its **contract and
  trigger rule**, not its prose.

---

## 3. Do-NOT list (binding)

1. **Do NOT expose any write tool (`resolve_review`, `trigger_source_rescan`, `write_page`) on
   the HTTP surface without the `write_enabled` gate.** They belong inside the
   `if write_enabled:` block of `build_http_mcp`, exactly like `write_page`. Remote/HTTP stays
   read-only by default (ADR-0029 §2.3 / ADR-0033 §2.4).
2. **Do NOT let `read_source_file` escape `raw/sources/`.** No `..`, no absolute paths, no
   symlink traversal, no reading `wiki/`/`.obsidian/`/config. Confinement is verified against a
   resolved real path; a miss returns a structured error.
3. **Do NOT exceed the caps:** `get_graph_neighborhood` `depth ≤ 2`; `list_reviews` `limit ≤ 100`;
   `bulk-resolve` `ids ≤ 200`. Clamp/reject rather than run unbounded (I7).
4. **Do NOT make the skill trigger on generic phrasing** ("search my notes", "find a file").
   It fires only on explicit "Synapse" / "my wiki".
5. **Do NOT recompute the graph or run a layout** inside `get_graph_neighborhood` — read the
   persisted edges only (I2).
6. **Do NOT add a second review writer or a new status value** — `resolve_review` /
   `bulk-resolve` / `PATCH` route through the existing `ops.review` seam (ADR-0034/0044).
7. **Do NOT implement a new scan** in `trigger_source_rescan` — enqueue the existing incremental
   watcher (I1).

---

## 4. Acceptance check (DoD)

1. stdio `mcp` and the SDK server register all tools (4 existing + 5 new = read: `search_wiki`,
   `get_page`, `list_pages`, `get_graph_neighborhood`, `list_reviews`, `read_source_file`;
   write: `write_page`, `resolve_review`, `trigger_source_rescan`).
2. `build_http_mcp(write_enabled=False)` registers **only** the read tools (no `resolve_review`,
   no `trigger_source_rescan`, no `write_page`); `write_enabled=True` adds all three write tools.
3. `get_graph_neighborhood(depth=5)` is clamped to `2`; `list_reviews(limit=999)` clamped to
   `100`; `POST /review/queue/bulk-resolve` with `>200` ids → `422`.
4. `read_source_file("../wiki/index.md")` (and absolute/symlink variants) → structured error, no
   bytes returned; a valid `raw/sources/*` path returns content.
5. `resolve_review` / `bulk-resolve` / `PATCH` route through `ops.review` (grep proves no second
   writer); terminal statuses unchanged.
6. `trigger_source_rescan` enqueues the incremental watcher — no full re-scan path invoked (I1).
7. The skill description triggers on "Synapse"/"my wiki" and not on "search my notes" (documented
   trigger rule).
8. `make openapi` includes `POST /review/queue/bulk-resolve` and `PATCH /review/queue/{id}`
   (I8). No DB migration ⇒ no ER change.

---

## 5. Consequences

**Positive** — closes the B5/D2 parity gap by *extending* the surface Synapse already has: the
new tools are the same two kinds (read bodies + gated write bodies) wired through the same
`build_http_mcp` split, so remote-read-only-by-default and the shared validated write path are
preserved for free. The skill is a disciplined client, adding no runtime capability. No
transport, auth, provider-routing, or schema change.

**Trade-offs (explicit)** — five more tools widen the MCP surface area, so the write-gating and
the `read_source_file` confinement are the two things that MUST be verified in tests (they are
the only new attack surface). `resolve_review` over HTTP (when the operator opts write in) lets a
token holder resolve reviews remotely — acceptable and symmetric with the already-gated
`write_page`. The skill's usefulness depends on trigger discipline; a loose trigger would be a UX
regression, hence the explicit Do-NOT.

**Invariant check** — **I6:** no tool touches `InferenceProvider`; nothing hardcoded. **I9:**
reuses FastMCP, `rag.retrieval`, the graph engine, the watcher, and `ops.review` — no new
search/graph/gateway/writer. **I1:** rescan is the incremental watcher; review writes are
status writes. **I7:** every read/bulk is capped (`depth ≤ 2`, `limit ≤ 100`, `ids ≤ 200`); no
new loop. **I5:** the shared validated write path (ADR-0010 §2) is untouched. **I2/I3/I4/I8:**
untouched (no layout recompute, no UI-thread work, no schema change). **No invariant is traded
for convenience.**
