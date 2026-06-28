# ADR-0008 — provider_config + ingest_runs schema; secrets via env only (I6, §12)

- Status: Accepted
- Date: 2026-06-28
- Sprint: v0.2
- Decider: solution-architect
- Invariants: I6 (pluggable inference; provider selectable from config), I7 (cost auditing), I8 (D2 must match live schema), §12 (no secrets in code/db)
- Related: CLAUDE.md §5 (provider_config), v0.2-scope §6, ADR-0007, ADR-0009
- Resolves: AQ-v0.2-2 (ingest run logging), AQ-v0.2-5 (operation scope column)

## Context

I6 requires the provider to be selectable per vault and per operation, with a global default.
v0.2 introduces the first real LLM spend, so I7 requires per-run cost auditing. Two storage
questions had to be settled:
1. Where does provider selection live — a YAML/JSON file or a Postgres table? And how is the
   "operation" scope (ingest vs chat vs lint) parameterised? (AQ-v0.2-5)
2. Where do per-run cost records live — a structured log line or a Postgres table? (AQ-v0.2-2)

A hard constraint from §12 frames both: **no secrets in code or database.** API keys are
environment-only.

## Decision

### 1. provider_config in Postgres, not a file

Provider selection lives in a Postgres `provider_config` table, not a YAML/JSON file:
- v0.4 exposes a Provider Selector **UI** that writes config; a REST/DB-backed store is the
  natural backing for `POST /provider/config` (AC-F17-7). A flat file would need a
  file-write API and a reload mechanism, reinventing a database.
- Resolution is a query the FastAPI service already has a session for; no file I/O on the
  request path.
- D2 (ER) already treats Postgres as the system of record (ADR-0002); config belongs there.

### 2. Scope model: explicit `operation` column (resolves AQ-v0.2-5)

`scope` is `Literal["global","vault","operation"]`. To make `operation` scope meaningful we
add a **separate nullable `operation` column** (`Literal["ingest","chat","lint"] | null`) —
option (c) from AQ-v0.2-5, chosen over encoding it inside the scope string (option b, which
would make the scope column un-queryable and un-typed). Columns:

| Column | Type | Null | Notes |
|--------|------|------|-------|
| id | uuid PK | no | `gen_random_uuid()` |
| scope | text | no | `global` \| `vault` \| `operation` |
| operation | text | yes | `ingest` \| `chat` \| `lint`; NULL unless scope='operation' |
| vault_id | text | yes | NULL at global scope; required at vault/operation scope |
| provider_type | text | no | `local` \| `api` \| `cli` |
| model_id | text | no | model name, e.g. claude-sonnet-4-6 — value lives ONLY in DB rows, never in source (AC-F17-8) |
| base_url | text | yes | OpenAI-compatible endpoint for ApiProvider; NULL for Anthropic/local default |
| max_iter | integer | no | default 3 (I7) |
| token_budget | integer | no | default 60000 orchestrated / 100000 cli (I7) |
| is_fallback | boolean | no | default false; marks the single fallback row for a scope (ADR-0009 §fallback) |
| created_at | timestamptz | no | `now()` |
| updated_at | timestamptz | no | `now()`, onupdate |

**Resolution order (most specific wins):**
`(scope='operation' AND vault_id=? AND operation=?)` → `(scope='vault' AND vault_id=?)` →
`(scope='global')`. AC-F17-6 proves the three-row precedence. The resolver returns the first
match in that order; absence of any row falls back to the §6 hardcoded defaults (max_iter=3,
token_budget=60000, but provider_type/model_id are required — a missing global row is a
configuration error surfaced to the caller, not a silent default backend, because silently
choosing a backend would violate I6's "never hardcode a provider").

### 3. Secrets via env ONLY — no API keys in the table (§12)

`provider_config` holds **no** API key column. `ApiProvider` reads `ANTHROPIC_API_KEY` (or
the OpenAI-compatible key) from the environment inside `api.py` only. `base_url` is endpoint
configuration, not a secret, so it is safe in the DB. This keeps the database dump and any D2
artifact free of credentials and keeps the I6 "no key outside provider/" rule intact.

### 4. ingest_runs: a Postgres table, not just a log line (resolves AQ-v0.2-2)

Per-run cost/convergence records are persisted to a Postgres `ingest_runs` table (a
structured log line is **also** emitted for live tailing, but the table is the system of
record). Rationale: I7 and §11 require queryable cost auditing ("flag anomalies",
"log total_cost_usd for every run"); a table enables `SELECT sum(total_cost_usd) ...`
dashboards and the v0.4 cost UI, which a log stream cannot answer without aggregation
infrastructure. Columns:

| Column | Type | Null | Notes |
|--------|------|------|-------|
| id | uuid PK | no | |
| vault_id | text | no | |
| page_id | uuid | yes | originating source page (FK pages.id); NULL if pre-write failure |
| provider_name | text | no | e.g. "OllamaProvider" (the class `name`, for audit only — NOT used for routing) |
| provider_type | text | no | local \| api \| cli |
| model_id | text | no | resolved model used |
| route | text | no | `orchestrated` \| `delegated` |
| max_iter_used | integer | no | iterations actually consumed (1..max_iter) |
| total_tokens | integer | no | input+output across all iterations |
| total_cost_usd | numeric(10,4) | no | 0.0000 for local/cli (ADR-0009) |
| converged | boolean | no | true if a valid batch was produced within max_iter |
| cost_anomaly | boolean | no | default false; true if total_cost_usd > 1.00 (ADR-0009) |
| started_at | timestamptz | no | |
| finished_at | timestamptz | no | |

### 5. links table (K5) — defined here so the schema changes in ONE migration

To avoid a mid-Sprint-3 migration (per scope §2 K3/K5 decision), the K5 `links` table ships
in the same Alembic migration as `provider_config` and `ingest_runs`:

| Column | Type | Null | Notes |
|--------|------|------|-------|
| id | uuid PK | no | |
| source_page_id | uuid | no | FK → pages.id |
| target_title | text | no | the `[[Target]]` title |
| target_page_id | uuid | yes | resolved FK → pages.id; NULL while unresolved |
| alias | text | yes | the `\|alias` part, if any |
| dangling | boolean | no | default false; true when target_page_id is unresolved (AC-K5-5) |
| created_at | timestamptz | no | |

`target_page_id` is included now (nullable) so v0.3's graph engine can resolve edges by FK
without a schema change; `dangling` is the denormalised convenience flag for the warn-not-error
path.

## Consequences

- (+) I6: provider fully selectable from config with a clean, typed precedence; no backend
  hardcoded; model ids live only in seeded rows (AC-F17-8).
- (+) §12: zero secrets in DB or D2; keys are env-only, confined to `provider/`.
- (+) I7: cost is queryable and auditable per run; the `cost_anomaly` flag is persisted, not
  just logged.
- (+) One Alembic migration adds all three tables (provider_config, ingest_runs, links),
  honouring the scope-§2 "one schema-change event" decision.
- (−) A missing global provider_config row is a hard error rather than a silent default. This
  is deliberate (I6) and must be covered by startup seeding: the Alembic data migration seeds
  one `global` row using the current model ids from §12.
- (−) `provider_name` is stored in ingest_runs for audit; reviewers must ensure it is never
  read back into a routing decision (I6) — it is audit metadata only.
