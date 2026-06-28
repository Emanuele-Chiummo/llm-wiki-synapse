# Architecture Decision Records — Index

> Last updated: 2026-06-28 · Sprint v0.3→v0.4 (M4-GUX Phase 0)
> All ADRs authored by solution-architect; formatted by tech-writer.
> Status values: Accepted | Superseded | Deprecated

ADRs 0001–0006 were authored in sprint v0.1 to lock the walking-skeleton design before
engineers began coding. ADRs 0007–0011 were authored in sprint v0.2 to lock the F17
InferenceProvider abstraction, capability-aware routing, the bounded ingest loop, the
provider_config/ingest_runs/links schema, the MCP transport, and the ingest contract
schemas before engineers began coding. ADRs 0012–0015 were authored in sprint v0.3 to
lock the F4 knowledge-graph engine (4-signal edge weighting), server-side FA2 layout +
coordinate persistence (I2), the dataVersion-debounced GraphCache + GET /graph contract,
and the no-client-layout sigma.js viewer contract before engineers began coding. ADR-0016
was authored at the v0.3→v0.4 transition (M4-GUX Phase 0) to fix the same-type clique
hairball defect: structural edges only (direct link OR shared source), AA/same-type as
weight modulators, sqrt(structural_degree) node sizing, and per-edge `kind` field.
They are referenced throughout the codebase as `ADR-XXXX`.

| ADR | Title | Status | Date | Sprint | Summary |
|-----|-------|--------|------|--------|---------|
| [0001](0001-incremental-indexing-strategy.md) | Incremental indexing strategy (mtime-then-hash) | Accepted | 2026-06-28 | v0.1 | Use `st_mtime_ns` as a cheap fast-path gate; `sha256` content hash as the authoritative change signal. Satisfies I1: no full rescan, no redundant embeds. |
| [0002](0002-datastore-split-postgres-qdrant.md) | Datastore split: Postgres for metadata/links, Qdrant for vectors | Accepted | 2026-06-28 | v0.1 | Postgres is the system of record (ER/cascade-delete source of truth); Qdrant is a derived, rebuildable vector index. Point id == page UUID. |
| [0003](0003-thin-ingest-seam-preserves-f17.md) | Thin ingest seam preserves F17 pluggable provider (I6) | Accepted | 2026-06-28 | v0.1 | `ingest_file()` seam in `ingest/orchestrator.py` routes all ingest paths; v0.1 body is provider-free; v0.2 slots the InferenceProvider ABC into the marked extension point without touching callers. |
| [0004](0004-embedding-dimension-config-policy.md) | Embedding dimension and endpoint are configuration, never hardcoded | Accepted | 2026-06-28 | v0.1 | `EMBEDDING_DIM` is a required env var (default 1024); validated against the live bge-m3 service at startup; Qdrant collection creation fails fast on mismatch. |
| [0005](0005-soft-delete-and-vault-state-seeding.md) | Soft-delete for pages and startup-seeded vault_state | Accepted | 2026-06-28 | v0.1 | DELETE events set `pages.deleted_at`; Qdrant point is hard-deleted. `vault_state` seeded on startup (idempotent); `data_version` is monotonic, never reset. |
| [0006](0006-ingest-trigger-response-and-startup-behavior.md) | POST /ingest/trigger response contract and startup behaviour | Accepted | 2026-06-28 | v0.1 | 202 response with `{task_id: null, status, page_id}`; v0.2 makes it async (non-breaking superset). Startup emits one INFO line about pre-existing files; no rescan (I1). |
| [0007](0007-inference-provider-abc.md) | InferenceProvider ABC + capability-aware routing (I6) | Accepted | 2026-06-28 | v0.2 | ABC over Protocol; 3 backends; route on `capabilities().supports_agentic_loop` (never isinstance/type); analyze-once + retry-generate (AQ-1); validator contract (AQ-7); chat() stubbed. |
| [0008](0008-provider-config-schema.md) | provider_config + ingest_runs schema; secrets via env only | Accepted | 2026-06-28 | v0.2 | provider_config in Postgres with operation column + global/vault/operation precedence (AQ-5); ingest_runs table for cost auditing (AQ-2); links table; no API keys in DB (§12). |
| [0009](0009-bounded-loop-defaults.md) | Bounded ingest loop: defaults, cost accounting, token normalization (I7) | Accepted | 2026-06-28 | v0.2 | max_iter=3, token_budget 60k/100k, fallback=1; uniform `Usage` normalization per backend (AQ-4); CLI/local cost=$0.00; inline $1 anomaly WARNING (AQ-8). |
| [0010](0010-mcp-transport-and-write-path.md) | MCP server: stdio transport; write_page reuses ingest primitives | Accepted | 2026-06-28 | v0.2 | stdio for v0.2, HTTP deferred to v0.4 (AQ-6); write_page reuses persist/embed/index primitives (I1,I5); search_wiki reuses Qdrant/bge-m3 (I9). |
| [0011](0011-ingest-contract-schemas.md) | Ingest contract: Analysis/WikiPage/Message/ProviderCapabilities schemas | Accepted | 2026-06-28 | v0.2 | Locks the Pydantic/dataclass contract in `ingest/schemas.py` (AQ-3); typed WikiFrontmatter enforces I5; backend-neutral Message keeps I6. |
| [0012](0012-graph-edge-weight-formula.md) | 4-signal graph edge-weight formula (F4) | Accepted | 2026-06-28 | v0.3 | ADDITIVE weight = 3·direct + 4·source-overlap + 1.5·Adamic-Adar + 1·type-affinity over undirected page pairs; each term defined exactly; persist iff weight>0; edges table persisted (AQ-v0.3-1/5). |
| [0013](0013-server-side-fa2-coord-persistence.md) | Server-side FA2 layout, coord persistence, determinism seed (I2) | Accepted | 2026-06-28 | v0.3 | FA2 only in graph/engine.py via python-igraph; fixed seed=42; coords in pages.x/y columns; incremental = row-level (coords may move on global relayout); single bounded pass (AQ-v0.3-2/4/6). |
| [0014](0014-graph-cache-debounce-and-graph-endpoint.md) | GraphCache debounce, dataVersion trigger, GET /graph contract (I2) | Accepted | 2026-06-28 | v0.3 | In-process debounce on data_version bump (5s, injectable clock); max 1 in-flight + 1 pending (I7); GET /graph synchronous 200 with cached + X-Graph-Cache: hit|miss (AQ-v0.3-3/7). |
| [0015](0015-no-client-side-layout-sigma-contract.md) | No client-side layout: sigma.js viewer contract (I2/I4/I3) | Accepted | 2026-06-28 | v0.3 | Thin read-only sigma viewer renders precomputed coords in ONE WebGL canvas; zero client-layout code (P0 block, static bundle grep + architect review); Zustand selectors + shallow equality; G2/G4 met by construction. |
| [0016](0016-obsidian-graph-rendering.md) | Obsidian-style graph: structural edges, real-connection sizing, type-as-modulator (F4) | Accepted | 2026-06-28 | v0.3→v0.4 | Edges are STRUCTURAL only (direct link OR shared source); AA + same-type become weight MODULATORS, never edge generators (kills the 4-clique hairball). Node size = BASE + GROWTH·sqrt(structural_degree) = real connections. FA2 fed the modulated structural edge set (stays server-side, I2). Adds per-edge `kind` (link\|source). Supersedes ADR-0012 §3 inclusion rule; retains ADR-0012 weight formula. |
