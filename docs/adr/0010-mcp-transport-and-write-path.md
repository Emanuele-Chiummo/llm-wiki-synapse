# ADR-0010 — MCP server: stdio transport in v0.2; write_page reuses the ingest primitives (I6, I1, I9)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.2
- Decider: solution-architect
- Invariants: I6 (CLI provider uses MCP tools, not direct FS writes), I1 (write_page is an incremental upsert), I5 (write_page validates frontmatter), I9 (search reuses Qdrant/bge-m3, not a new service)
- Related: CLAUDE.md §5 (CLI provider), v0.2-scope EC-M2-7, ADR-0003, ADR-0007
- Resolves: AQ-v0.2-6 (MCP transport)

## Context

The CliAgentProvider delegates the whole ingest to a claude-agent-sdk agent. That agent must
read and write `vault/wiki/` **through the Synapse service layer** (so frontmatter validation,
K5 wikilink parsing, K3 index update, and Qdrant upsert all run) — never via raw filesystem
writes that would bypass I1/I5. A FastMCP standalone server exposes four tools
(`search_wiki`, `write_page`, `get_page`, `list_pages`). The open question (AQ-v0.2-6) is the
transport: stdio-only, HTTP-only, or both.

## Decision

### 1. stdio is the v0.2 transport; HTTP is optional/deferred

The MCP server starts in **stdio** mode for v0.2 (`make mcp` → `python -m app.mcp.server`).
Rationale: the only v0.2 consumer is the CliAgentProvider, which the claude-agent-sdk wires as
an in-process / subprocess MCP server over stdio — the SDK's native, lowest-friction path. The
scope mentions HTTP "for future clients," but no v0.2 acceptance test requires a live HTTP
listener; the FastMCP **test client** (AC-MCP-1..6) exercises the tools in-process without a
transport. HTTP transport is therefore **deferred to v0.4** (when external/Web-UI clients
appear) and noted as a one-line FastMCP option, not built now. This avoids the
two-process / multiplexing complexity AQ-v0.2-6 flagged. (If pipeline time allows, a
`make mcp-http` target may be added best-effort, but it is not an M2 gate.)

### 2. write_page reuses the ingest seam primitives — no second write path

`write_page(title, content, frontmatter)` does **not** write files directly. It:
1. Validates `frontmatter` against the WikiPage validator (ADR-0007 §5) — returns a structured
   tool error (not an exception) on missing `type`/`title`/`sources` (AC-MCP-3).
2. Slugs the title to a typed path (`wiki/<type-plural>/<slug>.md`), writes the file.
3. Calls the same `persist_metadata` / `upsert_vector` / `append_log` / `bump_version`
   helpers the orchestrator uses (ADR-0003), plus the K5 wikilink parse and K3 index update.

This guarantees the CLI delegated path and the orchestrated path converge on **one**
write primitive — satisfying I1 (incremental upsert, no rescan) and I5 (frontmatter validated
before write) identically regardless of who called.

### 3. search_wiki reuses Qdrant + bge-m3 (I9)

`search_wiki(query)` embeds the query via the existing EmbeddingClient and queries the existing
`synapse_pages` Qdrant collection — no new search service (I9). It returns `PageRef` objects
with `relevance_score` from the Qdrant similarity score normalized to [0,1]. It is **not** the
F5 4-phase RAG pipeline (deferred to v0.5); it is a simple vector lookup.

## Consequences

- (+) Single write path: CLI delegation and orchestrated ingest share `persist_metadata` et al,
  so I1/I5 hold uniformly and there is no divergent second writer to keep in sync.
- (+) stdio-only keeps v0.2 simple and matches the SDK's native wiring; no multiplexing server.
- (+) I9 honoured: search is Qdrant/bge-m3, not a new index.
- (−) No live HTTP MCP endpoint in v0.2; external non-CLI MCP clients wait until v0.4. Accepted:
  no v0.2 consumer needs it, and the test client covers the tool contracts without a socket.
- (−) write_page must keep its validation in lockstep with the orchestrator's validator. Risk
  mitigated by both calling the **same** validator function (ADR-0007 §5), not two copies.
