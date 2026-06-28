# Architecture Decision Records — Index

> Last updated: 2026-06-28 · Sprint v0.2
> All ADRs authored by solution-architect; formatted by tech-writer.
> Status values: Accepted | Superseded | Deprecated

ADRs 0001–0006 were authored in sprint v0.1 to lock the walking-skeleton design before
engineers began coding. ADRs 0007–0011 were authored in sprint v0.2 to lock the F17
InferenceProvider abstraction, capability-aware routing, the bounded ingest loop, the
provider_config/ingest_runs/links schema, the MCP transport, and the ingest contract
schemas before engineers began coding. They are referenced throughout the codebase as
`ADR-XXXX`.

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
