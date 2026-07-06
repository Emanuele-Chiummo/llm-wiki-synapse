# Sequence Diagrams

This section contains Mermaid sequence diagrams documenting the major workflows and interaction patterns in Synapse.

## Workflows

The following diagrams illustrate key operational flows:

### Ingest loop

The ingest loop is the core workflow that processes raw documents into wiki pages. It involves:

1. **Watcher** detects a file change (mtime-then-hash)
2. **Orchestrator** chooses an inference provider (capability-aware routing)
3. **Provider** analyzes the document and generates wiki pages
4. **Validation** checks the output; on failure, refine and retry (bounded loop)
5. **Indexing** updates Postgres metadata and Qdrant embeddings

### Retrieval (4-phase)

The chat retrieval pipeline assembles a context window for the LLM:

1. **Tokenized vector search** — find candidate pages via bge-m3 embeddings
2. **Graph expansion** — expand the search results using the knowledge graph
3. **Budget constraint** — select pages within the token window
4. **Assembly** — assemble the retrieved context as citations for the LLM

### Deep Research

The deep-research loop fetches and synthesizes information from the web:

1. **Query generation** — form search queries from the user's question
2. **Web search** — retrieve results via SearXNG
3. **Assessment** — evaluate results for relevance and sufficiency
4. **Refinement** — iterate if needed (bounded loop)
5. **Synthesis** — combine findings into a wiki page
6. **Ingest** — auto-ingest the synthesized page

### Cascade delete

Deletion of a wiki page with referential integrity:

1. **Find dependents** — identify pages that reference the deleted page
2. **Match & categorize** — direct links, indirect (via shared concepts)
3. **Preserve shared** — keep concepts referenced elsewhere
4. **Cleanup** — remove wikilinks from dependents, update index.md

### Lint-fix loop

The bounded lint-fix workflow maintains wiki health:

1. **Scan rules** — check all pages against the schema
2. **Find violations** — missing frontmatter, broken wikilinks, type mismatches
3. **Propose fixes** — generate corrected pages
4. **Human gate** — show fixes to the user for approval
5. **Apply** — update pages with approved fixes (bounded loop, max iterations)

### Chat retrieval mode + web-search block (B2 / ADR-0059)

The chat turn retrieval pipeline with B2 composer controls:

1. **Retrieval-mode preset** — `retrieval_mode` enum maps to frozen `(k, expansion_depth, budget)` table
2. **4-phase retrieve()** — vector/lexical seeds → BFS graph expansion (depth ≤ 2) → budget gate → context assembly with `[n]` wiki citations
3. **Web-search block (opt-in)** — `use_web_search=true` triggers one bounded SearXNG call; results assembled as `## Web results (external)` with `[W1]…[Wn]` namespace; appended AFTER wiki block; single-shot, no loop
4. **Provider chat()** — capability gate (`supports_vision`) decides whether images are included; stream carries text deltas only (I3)
5. **done event** — dual citation fields: `citations` (wiki `[n]`) and `web_citations` (`[Wn]` external); distinct namespaces never interleaved

---

## Diagram files

All sequence diagrams are stored as Mermaid `.mmd` files and are rendered during the docs build. They are:

- **Version controlled** — committed alongside code changes
- **Validated in CI** — Mermaid syntax checked via `mmdc`
- **Linked from ADRs** — each major decision references its sequence diagram
- **Buildable offline** — no external dependencies

To regenerate diagrams locally:

```bash
npm install -g @mermaid-js/mermaid-cli
for f in docs/sequences/*.mmd; do
  mmdc -i "$f" -o "${f%.mmd}.png"
done
```

---

## Related resources

- **[Architecture Diagrams](../architecture/index.md)** — C4 system and component views
- **[ADRs](../adr/index.md)** — detailed rationale for each workflow decision
- **[API Reference](../api/index.md)** — REST endpoints and contracts
