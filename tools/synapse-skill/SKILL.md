# Synapse Agent Skill

A trigger-disciplined agent skill that lets Claude Code (or any MCP-capable agent) query a
locally-running Synapse instance via its REST and MCP surfaces.

---

## Trigger discipline

**ONLY activate this skill when the user explicitly references Synapse or their personal
knowledge base.** Accepted trigger phrases include:

- "Synapse", "my Synapse", "la mia wiki Synapse"
- "my wiki", "il mio wiki"
- "my knowledge base Synapse", "knowledge base Synapse"
- "cerca nel wiki", "search my wiki", "search my notes in Synapse"
- "la mia base di conoscenza"

**DO NOT activate on generic phrases** such as:
- "search my notes" (ambiguous — could be any notes app)
- "find something", "look it up"
- "remember that I said..."

If uncertain, ask: "Do you mean your Synapse knowledge base?"

---

## What Synapse is

Synapse is a self-hosted FastAPI service that builds and maintains a self-organizing wiki from
raw documents. It exposes:

- **REST API** at `http://localhost:8000` (or the configured `SYNAPSE_BASE_URL`)
- **MCP server** at `http://localhost:8000/mcp/server` (Streamable-HTTP, token-protected)
  — also available as stdio transport: `python -m app.mcp.server`

The wiki lives at `vault/wiki/` inside the Synapse data directory. Pages use YAML frontmatter
(`type`, `title`, `sources[]`, `lang`, `tags`) and `[[wikilink]]` syntax (Obsidian-compatible).

---

## Configuration

Set these environment variables before using this skill:

```bash
export SYNAPSE_BASE_URL="http://localhost:8000"   # REST base URL
export SYNAPSE_MCP_TOKEN=""                        # Bearer token (empty = no token)
export SYNAPSE_VAULT_ID="default"                 # Vault scope
```

For MCP stdio use, add to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "synapse": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/path/to/synapse/backend"
    }
  }
}
```

---

## Available MCP tools

These tools are available via the stdio MCP server (always) and the HTTP MCP surface (when
configured with `MCP_AUTH_TOKEN`). All read-only tools are always available. Write tools on
the HTTP surface require `MCP_REMOTE_WRITE_ENABLED=true`.

### Read-only tools

| Tool | Signature | Description |
|------|-----------|-------------|
| `search_wiki` | `(query: str, k: int = 5)` | Hybrid search via the shared 4-phase retrieval pipeline (dense vector + graph expansion + budget). Returns `[{id, title, type, relevance_score}]`. k capped at 50. |
| `get_page` | `(title: str)` | Retrieve a wiki page by exact title. Returns `{title, type, content, frontmatter}` or `{error}`. |
| `list_pages` | `(type: str | None = None)` | List all live pages; optional type filter (entity/concept/source/synthesis/comparison). Returns `[{id, title, type, relevance_score: 0.0}]`. |
| `get_graph_neighborhood` | `(title: str, depth: int = 1)` | Return a page + its 1–2 hop neighbors from the persisted knowledge graph. depth capped at 2 (I7). Returns `{center, nodes, edges}`. |
| `list_reviews` | `(status: str = "open", limit: int = 20)` | List HITL review queue items. status: open/pending/resolved/dismissed/all. limit capped at 100. Returns `[{id, type, proposed_title, status}]`. |
| `read_source_file` | `(path: str)` | Read a raw/sources/ file as text. path is relative to raw/sources/. Confined: rejects traversal, binary files. Returns `{path, name, size_bytes, truncated, content}` or `{error}`. |

### Write tools (stdio always; HTTP requires write_enabled)

| Tool | Signature | Description |
|------|-----------|-------------|
| `write_page` | `(title, content, frontmatter, origin_source="")` | Create or update a wiki page. Validates frontmatter `{type, title, sources[], lang}` before writing. content = markdown body only (no YAML block). Returns `{id, title, type, relevance_score: 0.0}` or `{error}`. |
| `resolve_review` | `(review_id: str, action: str)` | Resolve a review item. action must be `skip` (considered, declined) or `dismiss` (hide without acting). Returns `{id, status, action, proposed_title}` or `{error}`. |
| `trigger_source_rescan` | `()` | Kick the incremental raw/sources/ ingest scan. Uses mtime-then-hash gate — never full-rescan. Returns `{started, candidate_files}` or `{error}`. |

---

## Available REST endpoints

Base URL: `$SYNAPSE_BASE_URL` (default `http://localhost:8000`)

### Search & retrieval

```http
GET /search?q=<query>&vault_id=<vault>&k=8
Authorization: Bearer <token>
```

Returns the same 4-phase retrieval result as `search_wiki` but with full citation text.

### Pages

```http
GET /pages?vault_id=<vault>&limit=50&offset=0
GET /pages/<uuid>
GET /pages/<uuid>/related
```

### Graph

```http
GET /graph?vault_id=<vault>
```

Returns precomputed FA2 node coordinates + weighted edges as JSON. Never triggers a
server-side recompute (I2 — coordinates are pre-computed and stored in Postgres).

### Sources

```http
GET /sources                          # list raw/sources/ tree
GET /sources/content?path=<rel>       # metadata + text preview
POST /sources/ingest-all              # trigger incremental scan (I1 — never full rescan)
GET  /sources/ingest-all/status       # progress {running, done, total}
```

### Review queue (HITL — F9)

```http
GET  /review/queue?vault_id=<vault>&status=pending&limit=50
POST /review/queue/<id>/skip
POST /review/queue/<id>/dismiss
POST /review/queue/<id>/create        # lazy on-demand page generation (AI-powered)
POST /review/queue/<id>/deep-research # delegate to Deep Research (SearXNG, F10)
POST /review/queue/bulk-resolve       # bulk skip/dismiss by id list (cap 200)
  Body: {"ids": ["<uuid>", ...], "action": "skip|dismiss"}
  Returns: {"resolved": N, "not_found": N, "count": N}
PATCH /review/queue/<id>              # resolve or reopen
  Body: {"resolved": true, "action": "skip|dismiss"} | {"resolved": false}
  Returns: ReviewItemResponse
POST /review/queue/sweep              # auto-resolution sweep (rule-based + LLM)
```

---

## Example prompts

### Search

"Search my Synapse wiki for pages about Qdrant vector search"
→ use `search_wiki("Qdrant vector search", k=5)` or `GET /search?q=...`

"Find pages related to the Docker Compose setup in Synapse"
→ use `search_wiki("Docker Compose setup", k=8)`

### Read pages

"Show me the wiki page titled 'Qdrant' in Synapse"
→ use `get_page("Qdrant")`

"List all entity-type pages in my Synapse wiki"
→ use `list_pages(type="entity")`

### Graph exploration

"What pages are connected to 'Ollama' in my Synapse knowledge graph?"
→ use `get_graph_neighborhood("Ollama", depth=1)`

"Explore the two-hop neighborhood of 'LLM inference' in Synapse"
→ use `get_graph_neighborhood("LLM inference", depth=2)`

### Review queue

"Show me the open review items in my Synapse wiki"
→ use `list_reviews(status="open", limit=20)`

"Skip this review item in Synapse" (with known id)
→ use `resolve_review("<uuid>", "skip")`

"Dismiss all these review items in Synapse" (with a list)
→ `POST /review/queue/bulk-resolve` with `{"ids": [...], "action": "dismiss"}`

### Source files

"Read the source file 'notes/docker-setup.md' in Synapse"
→ use `read_source_file("notes/docker-setup.md")`

"Re-index my Synapse source files"
→ use `trigger_source_rescan()` or `POST /sources/ingest-all`

---

## Read-only default

By default this skill is **READ-ONLY**. Write tools (`write_page`, `resolve_review`,
`trigger_source_rescan`) and write REST endpoints (`POST /ingest/*`, `DELETE /sources`,
`POST /review/queue/<id>/create`, `PATCH /review/queue/<id>`) require explicit user consent
before use. Ask the user before performing any write operation.

---

## Safety rules

1. **Never ingest arbitrary internet content** without user confirmation.
2. **Never delete source files or pages** without explicit user instruction and a dry-run preview.
3. **Never trigger rescan/ingest** while the user is actively editing files in the vault.
4. **Cite wiki pages** using `[[Title]]` wikilink syntax when quoting from `get_page`.
5. **Respect bounded caps**: do not exceed k=50 for search, depth=2 for graph, limit=100 for
   reviews, 200 ids for bulk-resolve.
6. All loops (deep-research, ingest, lint) are bounded server-side — do not attempt to
   circumvent these limits.

---

## MCP tool contract summary (for integration testing)

```
Tool                    | R/W | Returns
------------------------|-----|------------------------------------------
search_wiki             | R   | list[{id, title, type, relevance_score}]
get_page                | R   | {title, type, content, frontmatter} | {error}
list_pages              | R   | list[{id, title, type, relevance_score}]
get_graph_neighborhood  | R   | {center, nodes, edges} | {error}
list_reviews            | R   | list[{id, type, proposed_title, status}]
read_source_file        | R   | {path, name, size_bytes, truncated, content} | {error}
write_page              | W   | {id, title, type, relevance_score} | {error}
resolve_review          | W   | {id, status, action, proposed_title} | {error}
trigger_source_rescan   | W   | {started, candidate_files} | {error}
```

Write tools return structured error dicts (never raise exceptions) so agents can retry.
