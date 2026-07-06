# synapse-skill

An installable agent skill that gives Claude Code (or any MCP-capable agent) access to a
locally-running Synapse knowledge base via its REST and MCP surfaces.

## What it does

- Searches your Synapse wiki (hybrid vector + graph retrieval)
- Reads pages, source files, and the knowledge graph neighborhood
- Lists and resolves HITL review queue items
- Triggers incremental re-indexing of raw source files

Read-only by default. Write operations require explicit user consent.

## Trigger discipline

Only activates on explicit Synapse / wiki / knowledge-base references — never on generic
"search my notes". See `SKILL.md` for the full trigger phrase list.

## Quick start

**Stdio MCP (claude_desktop_config.json):**

```json
{
  "mcpServers": {
    "synapse": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "cwd": "/path/to/synapse/backend",
      "env": {}
    }
  }
}
```

**HTTP MCP (when Synapse is running):**

```json
{
  "mcpServers": {
    "synapse": {
      "type": "http",
      "url": "http://localhost:8000/mcp/server",
      "headers": {"Authorization": "Bearer <your-token>"}
    }
  }
}
```

## Tools

| Tool | Type | Description |
|------|------|-------------|
| `search_wiki` | R | Hybrid wiki search |
| `get_page` | R | Read a page by title |
| `list_pages` | R | List all pages (optional type filter) |
| `get_graph_neighborhood` | R | 1-2 hop graph neighbors |
| `list_reviews` | R | HITL review queue |
| `read_source_file` | R | Read a raw/sources/ file |
| `write_page` | W | Create or update a page |
| `resolve_review` | W | Skip or dismiss a review item |
| `trigger_source_rescan` | W | Re-index raw/sources/ |

Full tool contracts: see `SKILL.md`.

## REST API reference

Base: `http://localhost:8000` (default)

- `GET /search` — 4-phase retrieval
- `GET /pages` — page list
- `GET /graph` — precomputed knowledge graph
- `GET /sources` / `GET /sources/content` — source file browser
- `GET /review/queue` — HITL review queue
- `POST /review/queue/bulk-resolve` — bulk skip/dismiss
- `PATCH /review/queue/{id}` — resolve or reopen
- `POST /sources/ingest-all` — incremental rescan

## Synapse project

See [github.com/emanuelechiummo/synapse](https://github.com/emanuelechiummo/synapse) (TBD)
for the full self-hosted deployment guide.
