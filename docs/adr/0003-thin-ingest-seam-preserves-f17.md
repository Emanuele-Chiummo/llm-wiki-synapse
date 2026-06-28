# ADR-0003 — Thin ingest seam preserves F17 pluggable provider (I6)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.1
- Decider: solution-architect
- Invariants: I6 (pluggable inference — never hardcode a provider), I1, I7 (bounded loops)
- Related: CLAUDE.md §5 (F17), v0.2 backlog (InferenceProvider ABC)

## Context

v0.1 has **zero LLM calls** (scope §3 defers F17 to v0.2). The temptation is to wire the
watcher straight into Postgres/Qdrant with no seam, because there is no provider yet. But
v0.2 must insert the **orchestrated ingest loop** (`analyze → generate → validate → retry`,
bounded by I7) for Local/API providers, and **delegate** the whole ingest to the CLI
provider when `supports_agentic_loop == True`. If v0.1 hardwires the only ingest path as
"parse frontmatter + embed + persist", that path becomes load-bearing and v0.2 has to
surgically split it — exactly the kind of retrofit I6 exists to prevent.

The risk is not writing provider code now (that would violate the scope lock); the risk is
shaping the v0.1 code so that the provider *cannot* be slotted in later without rewriting
the caller.

## Decision

Introduce a single, thin, provider-agnostic seam now and route v0.1 through it:

- Define one entry function, conceptually
  `ingest_file(file_path) -> IngestResult`, living in
  `backend/app/ingest/orchestrator.py`. In v0.1 its body does only the **mechanical**
  steps: read bytes → hash/mtime gate (ADR-0001) → parse frontmatter (K6) →
  persist metadata (Postgres) → embed + upsert vector (Qdrant) → append `log.md` →
  bump `data_version`.
- The watcher and `POST /ingest/trigger` both call **only** `ingest_file`. They never
  touch Postgres/Qdrant/embedding directly. This is the seam.
- v0.1 performs **no analysis/generation** — there is no `InferenceProvider` instance, no
  import of provider modules, no model id anywhere. The seam has a clearly commented
  extension point: where v0.2 will (a) resolve a provider from `provider_config`, (b) call
  `capabilities()`, and (c) branch to either the orchestrated loop or the delegated CLI
  path. v0.1 leaves that branch empty by design.
- The mechanical persistence steps are factored as small functions
  (`persist_metadata`, `upsert_vector`, `append_log`, `bump_version`) so the v0.2
  orchestrated loop can reuse them as its `validate`/`write_page` primitives without
  re-implementation.
- **No provider, no model id, no backend name** appears in v0.1 code. `EMBEDDING_URL` is an
  embedding endpoint, not an inference provider, and is explicitly scoped to vectorisation
  (I9), not to the F17 abstraction.

## Consequences

- (+) I6 future-proofed: v0.2 adds `ingest/provider/base.py` (the ABC) and capability
  routing *inside* `ingest_file` without changing a single caller. The seam is the only
  thing v0.2 modifies.
- (+) I7 future-proofed: the empty branch is where the bounded loop (`max_iter` +
  `token_budget`) will live; v0.1's straight-line body imposes no loop assumptions.
- (+) Honours the scope lock: no provider code, no LLM call, no model id ships in v0.1.
- (−) A one-function indirection in v0.1 that "does nothing clever" may look like
  over-engineering to a reviewer. Justified: it is the cheapest possible insurance against
  an I6 violation in v0.2 and costs one function boundary.
- Review rule (enforced by architect): any v0.1 PR in which the watcher or the REST handler
  reaches into Postgres/Qdrant/embedding directly — bypassing `ingest_file` — is rejected,
  because it pre-bakes a hardcoded path the provider would later have to displace.
