# ADR-0053 — Runtime UI config-override layer (`app_config` key/value store; env baseline → DB override; GET/PUT `/config/app`)

- **Status:** Accepted
- **Date:** 2026-07-03
- **Sprint:** v1.1 (M11 — "Convert & Configure"; R11-2 Settings redesign + A2 amendment)
- **Features:** F16 (Settings / config surface / i18n) · F17 (adjacent — S6 `EMBEDDING_FORMAT`, S5
  `EMBEDDINGS_ENABLED` touch the embedding data plane and MUST route through the existing adapter,
  never a hardcoded path)
- **Reference:** docs/sprints/SPRINT-v1.1-SCOPE.md §2 R11-2 (migration list + exclusions) · §3 R11-2
  (AC-R11-2-0..10) · §10 AMENDMENT A2.1 (Settings IA reorg) / A2.2 (first-run wizard) — the amendment
  OVERRIDES the base where they differ ·
  ADR-0040 (clip runtime config — GET/PUT, DB-wins-over-env, in-process cache) ·
  ADR-0041 (web-search runtime config — plain-text, non-secret, same pattern) ·
  ADR-0031 (`EMBEDDING_FORMAT` adapter seam — the seam S6 must route through) ·
  ADR-0030 (`EMBEDDINGS_ENABLED` toggle + lexical degrade — the seam S5 must route through) ·
  ADR-0051 (`PDF_EXTRACTOR` / Marker seam — S1–S3) ·
  ADR-0052 (`SYNAPSE_AUTH_TOKEN` — the auth **middleware** that gates the new routes by construction)
- **Invariants owned:** **I6** (S5/S6 must route through the existing embedding adapter — no backend/
  provider/embedding path is ever hardcoded; the override layer feeds config to the SAME seam, never a
  new one) · **I7** (bounded reads — the whole `app_config` table is read ONCE at lifespan startup and
  re-read only on a PUT; no per-request DB scan; the allow-list caps the surface) · **I8** (D2 ER + D4
  OpenAPI regenerated: new `app_config` table + `GET/PUT /config/app` with `BearerAuth`) · **I1/I5**
  (no vault re-scan and no vault write in this layer — config is Postgres-only)
- **Author:** solution-architect
- **Implementers:** backend-engineer (`app_config` model in `models.py` + Alembic migration +
  `config_overrides.py` cache + `GET /config/app` + `PUT /config/app/{key}` + `DELETE /config/app/{key}`
  + wire the 8 call sites through `get_effective` + pytests + ER/OpenAPI regen) · frontend-engineer
  (SettingsPanel section, IA reorg A2.1, first-run wizard A2.2 — writes ONLY through these endpoints) ·
  tech-writer (D2 ER, D4 OpenAPI, USER.md "Runtime Settings")
- **Gate:** This ADR **GATES all R11-2 backend code** (AC-R11-2-0). No `app_config` model, no migration,
  no `config_overrides.py`, and no `/config/app` route may be written until this ADR is **Accepted**.

---

## 1. Context

Today every runtime knob in Synapse is an **env var** loaded once at process start by
`pydantic-settings` (`backend/app/config.py`, module-level `settings` singleton). Changing any of them
— even a user-facing one like the PDF extractor or the monthly cost-alert threshold — means editing
`docker-compose.yml` / `.env` and restarting the container. The owner (SPRINT-v1.1 §1, A2) wants a
**non-technical user to tune Synapse from the UI without touching compose**, while keeping the env
contract 100 % backward-compatible for existing deployments.

Three prior ADRs already solved the "UI can override an env value, persisted in Postgres, DB-wins,
loaded once into an in-process cache" problem for **single** settings each:
- ADR-0040 (clip config — a secret token → hashed, one-time reveal),
- ADR-0041 (web-search URL — non-secret, returned verbatim),
- ADR-0033 (MCP token — secret, hashed).

Each of those added **named columns on `vault_state`** and a bespoke cache singleton. That does not
scale to **eight** heterogeneous settings (and a future N), and it would bloat `vault_state` with one
nullable column per knob. R11-2 therefore introduces a **generic key/value override table** plus a
**single** cache module. This ADR is the technical contract the PM decision (§2 R11-2) requires before
any R11-2 code is written. It fixes the table shape, the allow-list, the merge order, the API contract,
the reset semantics, and the two hard safety properties (forward-compat for unknown keys;
backward-compat when the table is empty).

**Two facts the implementer must not re-derive:**

1. **Auth is a middleware, not a route dependency.** The task brief says "auth via the existing
   `verify_token` dep." The live codebase (ADR-0052 §2.2, deliberately) enforces the REST API with a
   single `SynapseAuthMiddleware` installed in `main.py`; there is **no** per-route `Depends(verify_token)`
   pattern (`verify_token`/`_verify_token` in `main.py` is a *token-hash comparator* for the clip/MCP
   surfaces, not an auth dependency). Like ADR-0052 §1, **this ADR binds to the real mechanism**: the new
   `GET/PUT/DELETE /config/app*` routes are ordinary REST routes on the main router and are therefore
   **gated by construction** by `SynapseAuthMiddleware` the moment they are registered — when
   `SYNAPSE_AUTH_TOKEN` is set, they require `Authorization: Bearer <token>`; when it is unset, they are
   open exactly like every other route (v0.9 behaviour). No extra wiring; do NOT add a per-route
   dependency (that would be the double-gate ADR-0052 §6 forbids). The OpenAPI `BearerAuth` reference is
   the global security requirement FastAPI already emits (§4.4).

2. **The 8 settings are consumed at scattered call sites via `settings.<key>`.** Verified sites:
   `health.py` (S5), `costs.py` (S4), `main.py` (S5, S7), `ingest/orchestrator.py` (S5, S7),
   `rag/retrieval.py` (S5), `embeddings.py` (S6 via `settings.embedding_format` in `EmbeddingClient.__init__`),
   `ingest/extract.py` (S1/S2/S3), the wikilink post-pass (S8). The override is only real if these
   call sites read the **effective** value, not the raw env value. §2.5 specifies exactly how, and the
   I6-critical S5/S6 routing.

---

## 2. Decision

### 2.1 A generic `app_config` key/value table (NOT more `vault_state` columns)

One table, one row per **overridden** setting. Absent row ⇒ the env baseline governs.

```sql
CREATE TABLE app_config (
    key        TEXT        PRIMARY KEY,
    value      TEXT        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

- **`key`** — the config key, stored as the **lower-snake attribute name** used on the `settings`
  object (e.g. `pdf_extractor`, `embedding_format`, `cost_alert_threshold_usd`). This is the same token
  the API path uses (`PUT /config/app/pdf_extractor`) and the same token the frontend sends. The env
  var name (`PDF_EXTRACTOR`) is the UPPER form of the same identifier; we standardise on the
  **attribute form** everywhere in the DB and the API so there is exactly one canonical key spelling.
- **`value`** — always TEXT, `NOT NULL`. Typed settings (int/float/bool) are stored as their string
  form (`"120"`, `"5.0"`, `"true"`) and coerced at read time by the typed accessors (§2.5). A
  `NOT NULL` value column is the reason **reset-to-default is a row DELETE, not a `value: null` write**
  (§3): there is no in-band "null means default" sentinel to store, which keeps the table honest
  ("a row exists ⇔ an override is active") and avoids a nullable-value ambiguity.
- **`updated_at`** — audit / last-write, `DEFAULT now()`; refreshed on every upsert.

**Why a generic table, not per-key columns on `vault_state`** — eight (and growing) heterogeneous
knobs would add eight nullable columns and eight bespoke cache fields, each needing a migration. A
key/value table adds settings with **zero schema change** after this one migration, and the allow-list
(§2.2) — not the table shape — is the safety boundary. This is the single deliberate departure from the
ADR-0040/0041 per-column pattern, justified by cardinality.

**Alembic migration is required** (next id in sequence). ER drift check (`make er`) MUST show the new
table; `docs/er/schema.mmd` committed zero-drift (AC-R11-2-1, EC-M11-3, I8).

### 2.2 The allow-list is the security boundary — exactly 8 keys, a named constant

A **module-level frozen constant** in `config_overrides.py` is the single source of truth for which
keys may ever be overridden:

```python
# config_overrides.py — the ONLY keys the UI may override (SPRINT-v1.1 §2 R11-2)
ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset({
    "pdf_extractor",            # S1  (ADR-0051)
    "marker_service_url",       # S2  (ADR-0051)
    "marker_timeout_seconds",   # S3  (ADR-0051)
    "cost_alert_threshold_usd", # S4  (R9-1)
    "embeddings_enabled",       # S5  (ADR-0030) — routes through the embedding data-plane gate
    "embedding_format",         # S6  (ADR-0031) — routes through the embedding adapter seam (I6)
    "overview_language",        # S7  (F3)
    "wikilink_enrich_enabled",  # S8  (ADR-0036)
})
```

- A PUT/DELETE for any key **not** in this set → **400** with `{"error": "invalid_key", "allowed": [...]}`
  where `allowed` is the sorted list (AC-R11-2-3). No infra/secret key can ever be written through this
  surface, even if a row somehow existed for it (§2.6 — such rows are ignored on load).
- The list is exactly the **MIGRATED** set of §2 R11-2. The **EXCLUDED** set (§2.4 below) is never in
  this constant.

> **De-scope note (SPRINT-v1.1 §8):** if S7 (`overview_language`) and S8 (`wikilink_enrich_enabled`)
> are cut from the first UI delivery, they MUST also be removed from `ALLOWED_CONFIG_KEYS` in the same
> change (the allow-list and the UI must not diverge). The table shape and merge logic are unaffected.

### 2.3 Value validation per key (fail-closed, never persist garbage)

`PUT /config/app/{key}` validates the value against a per-key rule **before** upsert; on failure →
**422** (Pydantic-style) and no write. Minimum rules (backend-engineer may tighten):

| Key | Type | Rule | On violation |
|---|---|---|---|
| `pdf_extractor` | enum | `∈ {"pypdf","marker"}` | 422 |
| `marker_service_url` | url | starts with `http://` or `https://` (mirrors ADR-0041 §2.4) | 422 |
| `marker_timeout_seconds` | float | `> 0`, `≤ 3600` | 422 |
| `cost_alert_threshold_usd` | float | `≥ 0` (0 disables the alert — matches config.py) | 422 |
| `embeddings_enabled` | bool | `∈ {"true","false"}` (case-insensitive) | 422 |
| `embedding_format` | enum | `∈ {"ollama","openai"}` (matches ADR-0031 allowed values) | 422 |
| `overview_language` | str \| sentinel | free text ISO code, OR the `"(auto)"` sentinel → stored as a DELETE (see below) | — |
| `wikilink_enrich_enabled` | bool | `∈ {"true","false"}` (case-insensitive) | 422 |

**S7 `(auto)` handling:** the env baseline for `overview_language` is `None` ("auto-detect"). Because
`app_config.value` is `NOT NULL`, there is no way to store "auto" as a value; the UI's "(auto)" choice
therefore maps to **reset-to-default = DELETE the row** (§3), which restores the `None`/auto baseline.
Any non-empty ISO string is stored verbatim.

### 2.4 Excluded infra/secret keys — env-only, never in the UI (from §2 R11-2)

These keys are **NOT** in `ALLOWED_CONFIG_KEYS` and can never be set via `/config/app`. Reproduced from
SPRINT-v1.1 §2 R11-2 so the contract is self-contained:

| Key | Class | Reason for exclusion |
|---|---|---|
| `DATABASE_URL` | infra secret | changing it at runtime orphans all data |
| `QDRANT_URL` | infra | live change corrupts the collection pointer |
| `EMBEDDING_URL` | infra | tied to the running bge-m3 instance |
| `EMBEDDING_DIM` | infra | changing requires a full re-embed of the vault |
| `VAULT_PATH` | infra | changing while the watcher runs breaks the incremental index (I1) |
| `SYNAPSE_AUTH_TOKEN` | security | env-only by design (ADR-0052 §2.1); has its own Settings › Security surface |
| `CORS_ALLOW_ORIGINS` | infra | network-topology decision |
| `SEARXNG_URL` | infra | tied to the running SearXNG instance (its own runtime config exists — ADR-0041) |
| `CLIP_TOKEN` | security secret | env-only (ADR-0038 §2.1); clip has its own PUT /clip/config for the DB token |
| `MCP_AUTH_TOKEN` | security secret | handled via PUT /mcp/auth (ADR-0033) |
| `MCP_TRUSTED_PROXIES` | infra | network topology |
| `MAX_UPLOAD_BYTES` | infra | storage constraint |

**Rationale, one line:** every excluded key is either (a) a **secret** whose blast radius or replay
semantics require env-only or a dedicated hashed/one-time surface (auth token, clip token, MCP token),
or (b) an **infrastructure pointer** whose runtime mutation would corrupt data, the vector store, the
incremental index (I1), or the network posture. None is a per-day-use "behaviour tuning" knob — the
migration criterion is "user-facing, runtime-tunable, safe to change live," which none of these meet.

### 2.5 The override layer — `config_overrides.py`: load once, cache, `get_effective(key, env_default)`

New module `backend/app/config_overrides.py`. Contract:

```python
ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset({...})   # §2.2

async def load_overrides(session) -> None:
    """Lifespan startup: read ALL app_config rows ONCE, cache in memory.
    Rows whose key ∉ ALLOWED_CONFIG_KEYS are IGNORED (forward/back compat — §2.6).
    Single bounded SELECT; no vault scan, no per-request DB read (I7)."""

def get_effective(key: str, env_default: str) -> str:
    """Return the cached override string for `key` if present, else `env_default`.
    Pure in-memory O(1); never touches the DB."""

def source_of(key: str) -> str:           # "override" if a cached row exists for key, else "env"
def get_override(key: str) -> str | None: # cached raw value or None (per §2 R11-2 AC-R11-2-5 shape)

async def set_override(session, key, value) -> None:  # allow-list + validate + upsert + refresh cache
async def clear_override(session, key) -> None:       # allow-list + DELETE row + refresh cache
```

**Merge order (single, unambiguous): env baseline FIRST, DB override ON TOP, for the 8 allowed keys
only.** `settings.<key>` is the deploy-time default; `get_effective(key, str(settings.<key>))` is the
effective value. There is no third layer. Nothing else changes.

**Typed reads.** Because `settings.<key>` is variously `str | bool | float | None`, the call sites use
thin typed helpers colocated in `config_overrides.py` (so coercion lives in one place, mypy-strict,
no `Any`):
- `effective_str(key, default: str | None) -> str | None`
- `effective_bool(key, default: bool) -> bool`   (`"true"/"1"/"yes"` → True; else False)
- `effective_float(key, default: float) -> float`

**The 8 call sites migrate to the effective accessor** (this is the load-bearing wiring — an override
that no call site reads is a no-op):

| Setting | Current read | New read |
|---|---|---|
| S1 `pdf_extractor` | `settings.pdf_extractor` in `ingest/extract.py` | `effective_str("pdf_extractor", settings.pdf_extractor)` |
| S2 `marker_service_url` | `settings.marker_service_url` | `effective_str("marker_service_url", settings.marker_service_url)` |
| S3 `marker_timeout_seconds` | `settings.marker_timeout_seconds` | `effective_float("marker_timeout_seconds", settings.marker_timeout_seconds)` |
| S4 `cost_alert_threshold_usd` | `settings.cost_alert_threshold_usd` in `costs.py` | `effective_float("cost_alert_threshold_usd", settings.cost_alert_threshold_usd)` |
| S5 `embeddings_enabled` | `settings.embeddings_enabled` in `health.py`, `main.py`, `orchestrator.py`, `retrieval.py` | `effective_bool("embeddings_enabled", settings.embeddings_enabled)` |
| S6 `embedding_format` | `settings.embedding_format` in `embeddings.py` `EmbeddingClient.__init__` | `effective_str("embedding_format", settings.embedding_format)` fed into the SAME adapter constructor |
| S7 `overview_language` | `settings.overview_language` in `main.py`, `orchestrator.py` | `effective_str("overview_language", settings.overview_language)` (None ⇒ auto) |
| S8 `wikilink_enrich_enabled` | `settings.wikilink_enrich_enabled` | `effective_bool("wikilink_enrich_enabled", settings.wikilink_enrich_enabled)` |

**I6 — the S5/S6 routing rule (non-negotiable).** S6 (`embedding_format`) MUST be applied by passing the
effective value **into the existing `EmbeddingClient` adapter seam** (ADR-0031: `EmbeddingClient(embedding_format=...)`
or by having the client read `get_effective` at construction) — NOT by adding a new branch that hardcodes
an embedding request/response shape anywhere. The adapter (ollama vs openai body/parse) stays the ONE
place that knows the shapes. Likewise S5 (`embeddings_enabled`) MUST feed the **existing** ADR-0030
data-plane gate (ingest-skip / retrieval lexical-degrade / startup validation) — no new bypass path.
The override layer only changes *where the value comes from*, never *how it is applied*. Reviewer will
reject any S5/S6 wiring that introduces a second code path (I6).

**No live re-embed / no re-scan on change (I1).** Toggling S5 or changing S6 at runtime does exactly
what ADR-0030/0031 already specify for the env value: it changes behaviour for **subsequent** ingests/
retrievals. It MUST NOT trigger a bulk re-embed or a vault re-scan. Startup embedding validation
(`_validate_embedding_and_collection`) reads the **effective** S5 at lifespan start (after
`load_overrides`), so an override that turns embeddings off is honoured on the next boot without env
changes; an in-session toggle takes effect on the next ingest/query (documented behaviour, not a bug).

### 2.6 Startup safety — forward-compat (unknown keys ignored) and backward-compat (empty ⇒ pure env)

- **Forward/backward-compat on load:** `load_overrides` filters rows by `ALLOWED_CONFIG_KEYS`. A row
  whose key is unknown/removed (an old key, a typo, a de-scoped S7/S8) is **silently ignored** — never
  applied, never crashes startup. This lets a key be removed from the allow-list in a later version
  without a data migration, and lets a newer DB be read by an older binary.
- **Backward-compat (EC-M11-13):** an **empty `app_config` table** (no rows) means `get_effective`
  returns the env default for every key ⇒ behaviour is **byte-for-byte identical to v1.0.0**. A
  deployment that never touches the UI is unaffected; the v1.0 E2E suite passes unchanged against a
  v1.1 build with an empty table. This is the load-bearing compatibility property and MUST have an
  explicit test.
- **Deployment without the migration applied:** `load_overrides` tolerates a missing `app_config`
  table (catch the "relation does not exist" path, log once, treat as empty) exactly as ADR-0041 §6
  tolerates missing columns — env governs, zero behaviour change. (Belt-and-braces; the migration is
  required in the normal path.)

---

## 3. API contract

All three routes are ordinary REST routes on the main router, **gated by `SynapseAuthMiddleware`**
(ADR-0052) when `SYNAPSE_AUTH_TOKEN` is set, open otherwise. They MUST carry the global `BearerAuth`
security requirement in OpenAPI (§4.4). No per-route auth dependency (would double-gate — ADR-0052 §6).

### 3.1 `GET /config/app` — list all 8 settings with effective value + source

```
GET /config/app
→ 200 {
    "settings": [
      { "key": "pdf_extractor",            "value": "pypdf",  "source": "env" },
      { "key": "marker_service_url",       "value": "http://host.docker.internal:8555", "source": "env" },
      { "key": "marker_timeout_seconds",   "value": "120",    "source": "env" },
      { "key": "cost_alert_threshold_usd", "value": "5.0",    "source": "env" },
      { "key": "embeddings_enabled",       "value": "true",   "source": "env" },
      { "key": "embedding_format",         "value": "ollama", "source": "env" },
      { "key": "overview_language",        "value": "",       "source": "env" },
      { "key": "wikilink_enrich_enabled",  "value": "true",   "source": "env" },
      ...
    ]
  }
```

- Returns **exactly** the keys in `ALLOWED_CONFIG_KEYS` (order = sorted or the S1..S8 order; stable).
- `value` is always the **effective** value as a string (override wins; env baseline otherwise). For
  S7 with no override the effective value is `None` → serialised as `""` (empty string sentinel the UI
  renders as "(auto)").
- `source` is `"override"` iff a cached `app_config` row exists for the key, else `"env"`.
- All values derived from the in-process cache — **no DB round-trip per GET** (I7).
- Never returns any excluded/secret key (they are not in the allow-list, hence not iterated).

### 3.2 `PUT /config/app/{key}` — upsert one override

```
PUT /config/app/{key}
body: { "value": "<string>" }        # typed values sent as their string form
→ 204 No Content                     # on success (upsert + cache refresh)
→ 400 { "error": "invalid_key", "allowed": ["cost_alert_threshold_usd", ...] }   # key ∉ allow-list
→ 422 { ...validation detail... }    # value fails the per-key rule (§2.3)
→ 401                                 # if SYNAPSE_AUTH_TOKEN set and bearer missing/wrong (middleware)
```

- Upserts `app_config(key, value, updated_at=now())` by primary key, then refreshes the in-process
  cache so a subsequent `GET` immediately reflects `source: "override"` and the new value (AC-R11-2-2b).
- A pytest asserts: valid key+value → 204 and `source` flips to `"override"`; unknown key → 400 with
  the sorted `allowed` list; a value violating §2.3 → 422 with no row written.

### 3.3 Reset-to-default — **DECISION: `DELETE /config/app/{key}`** (chosen over `PUT value:null`)

```
DELETE /config/app/{key}
→ 204 No Content                     # override row removed; setting reverts to env default
→ 400 { "error": "invalid_key", "allowed": [...] }   # key ∉ allow-list
→ 401                                 # middleware, as above
```

**Why DELETE, not `PUT {value: null}` (justified):**

1. **`app_config.value` is `NOT NULL` by design** (§2.1). A `PUT {value: null}` would either require a
   nullable value column (re-introducing the "row exists but means default" ambiguity we deliberately
   removed) or a magic in-band null that the layer must special-case on every read. DELETE keeps the
   table invariant crisp: **a row exists ⇔ an override is active.** `source` derivation, cache logic,
   and the "empty table ⇒ pure env" property (§2.6) all fall out of that invariant for free.
2. **It is the correct REST verb** — "remove the override resource" is a deletion, not an update to a
   null state. It reads unambiguously in the OpenAPI schema and in client code.
3. **It matches the layer's mental model** — env is the immutable baseline the app cannot edit; the
   override is a *separate resource* laid on top. Creating it is PUT; removing it is DELETE. There is
   no third "null" state to represent.

The frontend "Reset to default" action calls `DELETE /config/app/{key}`; the S7 "(auto)" choice maps to
the same DELETE (§2.3). SPRINT-v1.1 AC-R11-2-4 explicitly permits DELETE as the implementation — this
ADR **binds** it as the single reset mechanism. (`PUT {value:null}` is NOT implemented; if a client
sends `value:null` the request is rejected 422 by body validation — `value` is a required non-null
string — steering all resets to DELETE.)

---

## 4. Persistence, cache, and OpenAPI wiring (mirrors the ADR-0040/0041 proven pattern)

### 4.1 In-process cache, loaded at lifespan startup

- A single module-level cache in `config_overrides.py` (a dict `key → value`, guarded by an
  `asyncio.Lock` for the PUT/DELETE refresh, mirroring `_ClipConfigCache` / `_WebSearchConfigCache`).
- `load_overrides(session)` is called **once** during lifespan startup, in `main.py`, in the same
  block that calls `_load_clip_config_cache()` / `_load_web_search_config_cache()` and **before**
  `_validate_embedding_and_collection` (so the effective S5 governs startup validation — §2.5).
- Every `GET /config/app` and every call site read is O(1) in-memory. The DB is touched only at
  startup and on a PUT/DELETE (I7 — no unbounded / per-request reads).

### 4.2 Endpoint shape mirrors clip/web-search config

`GET`/`PUT`/`DELETE /config/app*` follow the exact request→validate→persist→refresh-cache→respond shape
of `PUT /clip/config` (`main.py`) — Pydantic request/response models, session scope for the write,
cache refresh outside the session, structured log line on write (`key` + `source`; **never** log a
value — some are innocuous but the pattern must be uniform and a future secret-adjacent key must not
leak). No new dependency; reuses SQLAlchemy 2 + the existing session helper.

### 4.3 ER (D2, I8)

`app_config` is added to `models.py` as an ORM model (`__tablename__ = "app_config"`, `key` TEXT PK,
`value` TEXT NOT NULL, `updated_at` TIMESTAMPTZ NOT NULL server_default `now()`), so `make er` emits it
into `docs/er/schema.mmd` with zero drift (EC-M11-3). The Alembic migration is the next id in sequence.

### 4.4 OpenAPI (D4, I8)

`docs/api/openapi.json` regenerated so `GET /config/app`, `PUT /config/app/{key}`, and
`DELETE /config/app/{key}` are declared with correct request/response schemas AND the global
`BearerAuth` security reference (they are NOT in the ADR-0052 exempt set, so they inherit the global
`security: [{"BearerAuth": []}]` — the reviewer verifies each of the three appears with `BearerAuth`,
AC-R11-2-8 / EC-M11-4).

---

## 5. Settings IA (A2.1) and the wizard (A2.2) — persistence is this ADR; grouping is the frontend's

The Settings information-architecture reorg (A2.1) and the first-run wizard (A2.2) are **frontend
concerns**; their *persistence contract* is this ADR and nothing else.

- **Endorsed (PM-and-owner-locked):** the ~14 current sections collapse to a **~5-group** target
  (Getting started / AI & Models / Sources & PDF / Output & Appearance / Advanced — A2.1). The
  grouping, labels, and inline help are the frontend-engineer's to finalise; this ADR does not fix the
  exact split. **This ADR endorses the target group count (~5) and the plain-language rule.**
- **"No env-var-name as primary label" rule (endorsed):** no visible primary field label may equal an
  env-var name (e.g. the S1 label is "PDF extractor / Estrattore PDF", never `PDF_EXTRACTOR`).
  Enum *values* that are already readable (`pypdf`, `marker`, `ollama`, `openai`) may be shown as-is.
  An optional "Advanced" affordance MAY reveal the underlying key for power users. (AC-R11-2-11/12.)
- **Single source of truth for persistence:** grouping is presentation only. The **only** way the
  UI (SettingsPanel AND the first-run wizard) mutates any of the 8 settings is
  `PUT /config/app/{key}` / `DELETE /config/app/{key}`; provider/model choices in the wizard go through
  the **existing provider-config endpoints** (ADR-0008/0021). **No parallel persistence path** — the
  wizard writes nothing directly to the DB, nothing to env, nothing to a new endpoint. A Vitest spies
  on `fetch` and asserts the wizard writes only through these sanctioned endpoints (AC-R11-2-13).
- **Wizard "unconfigured" detection** reads `GET /config/app` + the provider-config GET; it is bounded,
  skippable, and re-openable from "Getting started" (A2.2). No backend change beyond this ADR's
  endpoints is required for the wizard.

---

## 6. Do-NOT list

1. **DO NOT** add per-key columns to `vault_state` for these 8 settings — use the generic `app_config`
   table (§2.1). (The ADR-0040/0041 per-column pattern does not scale to N keys.)
2. **DO NOT** allow any key outside `ALLOWED_CONFIG_KEYS` — PUT/DELETE for a non-allowed key → 400 with
   the sorted `allowed` list (§2.2). No infra/secret key is ever writable here (§2.4).
3. **DO NOT** hardcode an embedding request/response shape or a provider path when applying S5/S6 —
   route the effective value through the **existing** ADR-0030 data-plane gate (S5) and the ADR-0031
   `EmbeddingClient` adapter seam (S6). A second code path is an I6 violation and will be rejected (§2.5).
4. **DO NOT** trigger a re-embed or a vault re-scan when S5/S6 change — behaviour applies to subsequent
   ingests/queries only (I1, §2.5).
5. **DO NOT** read `app_config` per request — load once at lifespan, cache in memory, refresh only on
   PUT/DELETE (I7). No `while` loop, no unbounded scan (single bounded SELECT).
6. **DO NOT** implement reset as `PUT {value: null}` — reset is `DELETE /config/app/{key}` (§3.3);
   `app_config.value` stays `NOT NULL`; `value` is a required field in the PUT body.
7. **DO NOT** add a per-route auth dependency to the new routes — they are gated by `SynapseAuthMiddleware`
   by construction (ADR-0052); a per-route `Depends` double-gates (ADR-0052 §6).
8. **DO NOT** log an override *value* — log `key` + `source` only (uniform with the clip/web-search
   pattern; forward-safe if a future key is secret-adjacent).
9. **DO NOT** let an unknown/removed key in `app_config` crash startup or be applied — filter by the
   allow-list on load; ignore the rest (forward/back compat, §2.6).
10. **DO NOT** change the env contract — an empty `app_config` table is byte-for-byte v1.0.0 behaviour
    (EC-M11-13); existing env-only deployments are unaffected (§2.6).
11. **DO NOT** let the frontend wizard or SettingsPanel write config through any path other than
    `PUT/DELETE /config/app/{key}` and the existing provider-config endpoints (§5) — no parallel
    persistence.
12. **DO NOT** skip the ER/OpenAPI regen — `docs/er/schema.mmd` (new `app_config` table) and
    `docs/api/openapi.json` (3 new routes + `BearerAuth`) MUST be drift-clean (I8, EC-M11-3/4).

---

## 7. Acceptance checks (DoD — maps to AC-R11-2-0..14)

1. **ADR accepted before code** — this file is Accepted and in the ADR index before any R11-2 backend
   code (AC-R11-2-0, EC-M11-9).
2. **Table + migration** — Alembic migration creates `app_config (key TEXT PK, value TEXT NOT NULL,
   updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`; ER zero-drift; a pytest confirms the table exists
   and supports upsert-by-key (AC-R11-2-1, EC-M11-3).
3. **GET contract** — `GET /config/app` returns `{"settings":[{key,value,source}]}` for the 8 keys;
   with no overrides all `source == "env"`; after a PUT the affected key flips to `"override"` and
   `value` changes; effective value wins (AC-R11-2-2).
4. **PUT contract** — valid key+value → 204 + upsert; unknown key → 400 `{"error":"invalid_key","allowed":[...]}`;
   a §2.3 rule violation → 422 no write; 401 under auth-on with a bad/absent bearer (AC-R11-2-3).
5. **Reset = DELETE** — `DELETE /config/app/{key}` removes the row and reverts to the env default; a
   pytest asserts the revert (AC-R11-2-4, §3.3).
6. **Layer logic** — `load_overrides` called once at lifespan; `get_effective(key, env_default)`
   returns override-else-default; typed accessors coerce int/float/bool; a pytest asserts the merge
   with mocked rows (AC-R11-2-5, §2.5).
7. **I6 routing** — a test/review confirms S6 flows through `EmbeddingClient` (no new shape branch) and
   S5 through the ADR-0030 gate (no new bypass) (§2.5, §6.3).
8. **Backward compat (EC-M11-13)** — empty `app_config` ⇒ v1.0 behaviour; the v1.0 E2E suite passes
   against a v1.1 build with an empty table (§2.6).
9. **Forward compat** — a row with a key ∉ allow-list is ignored on load; startup does not crash (§2.6).
10. **OpenAPI (I8)** — the 3 routes present with correct schemas + `BearerAuth`; `make openapi`
    drift-clean (AC-R11-2-8, EC-M11-4).
11. **Frontend persistence boundary** — SettingsPanel + wizard write only via `/config/app` +
    provider-config endpoints (fetch-spy test) (AC-R11-2-13, §5).
12. **mypy strict / lint** — `config_overrides.py` and the new routes pass `ruff` + `black --check` +
    `mypy` (strict), no `Any` (AC-R11-2-9).

---

## 8. Consequences

**Positive** — the owner tunes 8 real user-facing knobs from the UI, persisted across restarts, with
**zero** change to the env contract (empty table = v1.0.0). A single generic table + one cache module
scales to future settings with no schema churn, and the **allow-list is the security boundary** — infra
and secret keys are structurally unreachable. The pattern is a direct, proven extension of
ADR-0040/0041 (load-once cache, DB-wins-over-env, GET/PUT), so review surface is small. The I6 rule is
preserved by feeding effective values into the **existing** embedding/provider seams, never a new path.
The wizard/IA reorg get a single, unambiguous persistence contract, preventing a parallel write path.

**Trade-offs (explicit)** —
- **The 8 call sites must be migrated to `get_effective`** — an override that no call site reads is a
  silent no-op. This is the main implementation risk; §2.5 enumerates every site and the test in §7.6
  guards the merge logic. Reviewer verifies each call site individually.
- **S5/S6 in-session changes apply on the next ingest/query, not retroactively** — no live re-embed
  (I1). Documented as intended behaviour (USER.md "Runtime Settings"), not a bug. Startup validation
  reads the effective S5, so a persisted toggle is fully honoured on the next boot.
- **`value TEXT NOT NULL` + typed coercion at read** — all values stored as strings; the typed
  accessors are the single coercion point. A malformed stored value can only exist if it bypassed §2.3
  validation (it cannot via the API); the accessors fail closed to the default on a coercion error.
- **Reset is DELETE, not a null write** — one extra verb, bought for a crisp "row exists ⇔ override
  active" invariant (§3.3). Clients that expected `PUT {value:null}` get a 422; the UI uses DELETE.
- **Auth is the ADR-0052 middleware, not a route dep** — the brief's "verify_token dep" does not exist
  as an auth mechanism; binding to the real middleware keeps gating default-secure and avoids the
  double-gate (ADR-0052 §6).

**Invariant check** — **I6:** S5/S6 route through the existing embedding gate/adapter; no provider,
model, or embedding shape is hardcoded; the allow-list is a named constant. **I7:** whole-table read
once at startup + on write; O(1) per-request reads from cache; no unbounded loop. **I8:** ER +
OpenAPI (with `BearerAuth`) regenerated. **I1/I5:** no vault re-scan, no vault write — config lives in
Postgres only; S5/S6 changes never re-embed or re-scan. **I2/I3/I4/I9:** untouched.
```
