# ADR-0004 — Embedding dimension and embedding endpoint are configuration, never hardcoded

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.1
- Decider: solution-architect
- Invariants: I9 (reuse running bge-m3; never reinvent), I8 (reproducible)
- Resolves: AQ-1
- Related: ADR-0002 (datastore split)

## Context

The Qdrant `synapse_pages` collection must be created with a fixed vector dimension. The
backlog (AC-QD-1) says "bge-m3 = 1024 dims — verify against running instance", and the
functional-analyst flagged AQ-1 because bge-m3 has variants and the running TrueNAS service
must be the authority, not a literature value. A wrong hardcoded dimension makes every
upsert fail at runtime. Equally, I9 forbids loading a local model: embeddings must come from
the already-running service over HTTP.

Two values are involved and both must be externalised:

- the **embedding endpoint** (`EMBEDDING_URL`) — already mandated by AC-QD-4 / AC-WATCH-6,
- the **vector dimension** used to create the Qdrant collection.

## Decision

1. **`EMBEDDING_DIM` is a required environment variable.** The Qdrant collection is created
   with `size = int(os.environ["EMBEDDING_DIM"])`. No integer literal for the dimension
   appears anywhere in application code. Default documented in `.env.example` and
   `docker-compose.yml` as **1024** (the expected bge-m3 value), but the value the service
   *uses* is always read from config.
2. **The configured dimension is validated against the live service at startup.** On
   startup the service requests one embedding from `EMBEDDING_URL` (or reads the model's
   reported dimension) and asserts `len(vector) == EMBEDDING_DIM`. On mismatch the service
   fails fast with a clear error rather than creating a malformed collection. This makes the
   running bge-m3 instance the authority and turns AQ-1 from a guess into a checked
   invariant.
3. **Collection-exists guard.** If `synapse_pages` already exists with a different
   dimension, the service does not silently recreate it; it logs an error and refuses to
   start, so a dimension change is a deliberate, human-acknowledged migration.
4. **`EMBEDDING_URL` is the only path to bge-m3** (I9). No embedding model is imported or
   spawned in-process; verified by AC-WATCH-6 / AC-QD-4.

## Consequences

- (+) Resolves AQ-1 without hardcoding: the real running dimension wins, and a misconfig is
  caught at boot, not at first ingest.
- (+) I9 satisfied: embeddings are always remote; the dimension is a config contract, not a
  literal.
- (+) Reproducible (I8): `.env.example` + compose document the default; CI sets the var
  explicitly.
- (−) Adds one startup HTTP round-trip to bge-m3. Negligible, and it doubles as a liveness
  probe for the embedding dependency.
- (−) If `EMBEDDING_DIM` is unset the service refuses to start (required var, no silent
  fallback). Intentional — a silent wrong default is worse than a loud missing one.
