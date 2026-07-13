# Architecture Overview

Synapse is a self-hosted FastAPI service that watches a vault filesystem, runs capability-aware AI-driven ingest loops, and maintains a self-organizing knowledge wiki. This section contains the C4 architecture diagrams describing the system at different levels of detail.

---

## System Context (C4 Level 1)

The **System Context** diagram shows Synapse and its external dependencies at the highest level.

- **Emanuele** (vault owner) — drops documents, selects inference providers, reads the generated wiki
- **Synapse** — the FastAPI service + FastMCP server
- **External services** — Ollama (local inference), Anthropic API / OpenAI-compatible endpoints (cloud inference), Qdrant (vector store), bge-m3 (embeddings), vault filesystem

[View: context.mmd](context.mmd)

---

## Container Architecture (C4 Level 2)

The **Container** diagram breaks down Synapse's major internal containers:

- **FastAPI Service** — REST/WebSocket API, request routing, ingest orchestration
- **Watcher** — filesystem monitor for incremental indexing (watchdog + mtime-then-hash)
- **Ingest Orchestrator** — bounded analyze→generate→validate loop with capability-aware provider routing
- **Graph Engine** — 4-signal edge weighting, FA2 server-side layout, dataVersion-debounced caching
- **RAG / Retrieval** — 4-phase (vector → graph expansion → budget → assembly) retrieval for chat
- **Database** — Postgres (metadata, config, schema) + Qdrant (embeddings)
- **FastMCP Server** — optional standalone MCP-over-HTTP for CLI agents

[View: container.mmd](container.mmd)

---

## Component Architecture (C4 Level 3)

The **Component** diagram details the internal structure of the FastAPI service:

- **Models** — SQLAlchemy ORM for Postgres schema
- **InferenceProvider ABC** — abstract base for pluggable AI backends (Local, API, CLI)
- **Ingest Seam** — thin, provider-agnostic interface for analyze/generate/validate
- **Graph Cache** — debounced FA2 layout computation and coordinate persistence
- **RAG Pipeline** — tokenized retrieval, graph-expansion signal, budget-aware assembly
- **MCP Handler** — optional MCP tool registration for filesystem access
- **API Endpoints** — REST/WebSocket routes for pages, graph, chat, ingest, health, config

[View: component.mmd](component.mmd)

## Generation Lifecycle (v1.6.0)

The generation lifecycle diagram shows the shared six-type direct-ingest contract, the
source-grounded Review hand-off, and the separate domain-safe/idempotent corpus pass.

[View: corpus-generation-lifecycle.mmd](corpus-generation-lifecycle.mmd)

---

## Key Design Principles

These diagrams illustrate the non-negotiable invariants from CLAUDE.md:

| Invariant | Reflected in | Notes |
|-----------|--------------|-------|
| **I1: Incremental Index Only** | Container (Watcher → mtime-then-hash) | No full-rescan; changes update only affected records |
| **I2: Server-side Graph Layout** | Container (Graph Engine); Component (GraphCache) | FA2 runs offline; coordinates cached in Postgres; sigma renders precomputed coords |
| **I3: No Per-token DOM Mutation** | Component (RAG Pipeline, MessageList renderer) | Markdown/LaTeX parsed at stream end; Zustand selectors prevent re-renders |
| **I4: CodeMirror 6 Editor** | Component (API endpoints, Editor component) | No WYSIWYG; all lists virtualized (TanStack Virtual) |
| **I5: Obsidian Compatibility** | Component (Models → frontmatter + wikilinks) | `wiki/` remains a valid Obsidian vault; auto-generated `.obsidian/` config |
| **I6: Pluggable Inference (F17)** | Component (InferenceProvider ABC + Ingest Seam) | Local, API, CLI backends; capability-aware routing; no hardcoded provider |
| **I7: Bounded Loops** | Container (Ingest Orchestrator); Component (all loops) | max_iter + token_budget caps; cost logging |
| **I8: Docs-as-DoD** | This section (architecture.md + diagrams) | Mermaid C4/sequences, Playwright screenshots, OpenAPI auto-generated |
| **I9: Do Not Reinvent** | Container (reuses Ollama, Qdrant, bge-m3, SearXNG) | External services are NOT containerized |

---

## Diagrams as Code

All architecture diagrams are Mermaid `.mmd` files committed to the repository. They are:

- **Source of truth** for design decisions
- **Kept in sync** with the code via peer review
- **Rendered in CI** to catch syntax errors early
- **Embedded in the docs site** (MkDocs Material) with syntax highlighting

To regenerate or validate the diagrams locally:

```bash
# Install mmdc (Mermaid CLI) if not present
npm install -g @mermaid-js/mermaid-cli

# Validate all diagrams
for f in docs/architecture/*.mmd docs/sequences/*.mmd docs/er/*.mmd; do
  [ -f "$f" ] || continue
  mmdc -i "$f" -o /tmp/test.svg && echo "✓ $f" || echo "✗ $f"
done
```

---

## Related Resources

- **ER Diagram:** [docs/er/schema.mmd](../er/schema.mmd) — data model (generated from SQLAlchemy)
- **Sequence Diagrams:** [docs/sequences/](../sequences/index.md) — ingest loop, retrieval, deep-research, cascade-delete, lint-fix workflows
- **API Reference:** [docs/api/](../api/openapi.json) — OpenAPI specification
- **ADRs:** [docs/adr/](../adr/index.md) — detailed design rationale for each decision
