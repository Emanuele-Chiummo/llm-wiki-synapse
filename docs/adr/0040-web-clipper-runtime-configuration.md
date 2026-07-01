# ADR-0040 — Web clipper runtime configuration (GET/PUT /clip/config; DB wins over env)

- **Status:** Accepted
- **Date:** 2026-06-30
- **Sprint:** v0.6 (Amendment — F11 web clipper runtime management)
- **Feature:** F11 (Web Clipper ingress, ADR-0038); parity with MCP token management (ADR-0033)
- **Invariants owned:** **I5** (vault unchanged) · **I7** (no new loop) · **I8** (D2 ER + D4 OpenAPI updated) · **I9** (reuse `vault_state` + existing `POST /clip`; no new process, no new dep)
- **Author:** solution-architect
- **Implementers:** backend-engineer (models + migration `0015` + in-process cache + `GET/PUT /clip/config` + update `POST /clip` gates + tests + ADR) · tech-writer (D2 ER regen, D4 OpenAPI, README row)

---

## 1. Context

ADR-0038 shipped the F11 web clipper ingress (`POST /clip`) as an **env-only** configuration:
`CLIP_ENABLED`, `CLIP_TOKEN`, `CLIP_ALLOWED_ORIGINS`, `CLIP_MAX_BODY_BYTES` control every
security property. This is deliberately fail-closed and correct, but incoherent with the MCP
surface, which gained **runtime token management** in ADR-0033 (`PUT /mcp/auth`,
`GET /mcp/info`). The clip ingress has no way to be configured from the web UI without editing
env vars and restarting the container.

**Owner request:** add runtime configuration for the clip ingress, **mirroring the proven MCP
auth pattern** (ADR-0033) exactly: a Settings UI can enable the clipper, generate/rotate/clear
its token, and set the allowed-origins list without touching `.env` or restarting.

---

## 2. Decision

### 2.1 Three new columns on `vault_state`; DB wins when set; env is the fallback

**Resolution precedence (most specific wins):**

| Config aspect | DB column | Env var | Resolution rule |
|---|---|---|---|
| Enabled gate | `clip_enabled_db` (BOOLEAN NULL) | `CLIP_ENABLED` | DB NOT NULL → DB wins; DB NULL → env |
| Bearer token | `clip_access_token` (TEXT NULL) | `CLIP_TOKEN` | DB NOT NULL → DB wins; DB NULL → env |
| Allowed origins | `clip_allowed_origins_db` (TEXT NULL) | `CLIP_ALLOWED_ORIGINS` | DB NOT NULL → DB wins; DB NULL → env |
| Max body bytes | *(not runtime-settable)* | `CLIP_MAX_BODY_BYTES` | env-only (no DB column; change requires restart) |

**Why DB wins over env (not env wins over DB):** The UI-set value is the *current operator
intent*. The env value is the *bootstrap / deployment default*. Once the operator has used the
UI to change a setting, the UI value should hold — exactly as ADR-0033 §2.1 decided for the
MCP token ("DB hash takes precedence when set").

**Why `clip_enabled_db` is three-state (BOOLEAN NULL) not two-state:** a DB value of `NULL`
means "the operator has not used the UI to override this" — so the env var governs. A DB value
of `TRUE` or `FALSE` is an explicit UI override. This allows a clean initial state where only
env governs (zero-friction upgrade), and lets the operator later revert to env governance by
clearing the DB value via `PUT /clip/config` with `set_enabled=null` (future option; current
API sets or leaves unchanged — clearing requires a separate DB null-able path, TBD per owner
if ever needed).

### 2.2 Token storage: PBKDF2-SHA256 hash (mirrors ADR-0033)

**Decision: `clip_access_token` in `vault_state` stores a PBKDF2-SHA256 hash, not the raw
token. This mirrors ADR-0033 §2.1 exactly.**

`PUT /clip/config {rotate_token:true}`:
1. Generates a raw token via `secrets.token_urlsafe(32)`.
2. Hashes it with `_hash_token()` (PBKDF2-SHA256, 260,000 iterations, random salt) — the
   same helper used by the MCP path (ADR-0033).
3. Stores **only the hash** in `vault_state.clip_access_token`. The raw token is never
   persisted anywhere.
4. Returns the raw token **once** in `generated_token` of the PUT response. Subsequent
   `GET /clip/config` calls NEVER return it.

**`POST /clip` auth is source-aware (constant-time throughout):**

| `token_source` | Verification method |
|---|---|
| `"db"` | `_verify_token(presented, db_hash)` — PBKDF2 constant-time compare (same as MCP) |
| `"env"` | `hmac.compare_digest(presented, env_token)` — plaintext constant-time compare |
| `"none"` | 401 immediately |

**Why env path stays plaintext:** The env var `CLIP_TOKEN` is an operator-provided pre-shared
secret stored in `.env` / shell environment — it cannot be hashed at rest by Synapse because
Synapse never sees the original plaintext at hash-time for env vars. The DB path does see the
plaintext at rotate-time, so it can and does hash it. This split is correct and documented.

**Why PBKDF2 for the DB path (same as MCP):** A DB or backup leak must not yield a usable
credential. The 260k-iteration PBKDF2 hash means an attacker who obtains the DB row cannot
brute-force the token in reasonable time. The per-request PBKDF2 latency is acceptable for
the clip ingress (typically one ingest per browser clip, not a high-frequency endpoint).

**Token value NEVER returned by GET /clip/config.** The endpoint returns only:
- `token_configured: bool`
- `token_source: "db" | "env" | "none"`

Never the value, hash, or salt. This is the load-bearing invariant of this ADR and is tested
explicitly in the test suite (`test_clip_config_response_never_contains_token_value`,
`test_put_clip_config_rotate_stores_pbkdf2_hash`, `test_rotate_token_authenticates_post_clip`).

### 2.3 Endpoints

**GET /clip/config** (read-only posture, mirrors GET /mcp/info):
```
GET /clip/config
  response: ClipConfigResponse {
    enabled:          bool,          # resolved (DB or env)
    token_configured: bool,          # DB or env token present; NEVER the value
    token_source:     "db"|"env"|"none",
    allowed_origins:  list[str],     # resolved (DB or env) + implicit loopback
    max_body_bytes:   int            # CLIP_MAX_BODY_BYTES env; not runtime-settable
  }
```

**PUT /clip/config** (write posture, mirrors PUT /mcp/auth):
```
PUT /clip/config
  request body: ClipConfigRequest {
    rotate_token:        bool | None,  # true ⇒ generate new token, return ONCE in generated_token
    clear_token:         bool | None,  # true ⇒ set clip_access_token = NULL (env fallback)
    set_enabled:         bool | None,  # set clip_enabled_db; omit = no change
    set_allowed_origins: str  | None   # comma-separated; "" = clear to env fallback; omit = no change
  }
  response body: ClipConfigStateResponse {
    enabled:          bool,
    token_configured: bool,
    token_source:     "db"|"env"|"none",
    allowed_origins:  list[str],
    max_body_bytes:   int,
    generated_token:  str | None     # ONLY on rotate_token=true — shown ONCE, never again
  }
```

**Authentication on the endpoints themselves:** same-origin / unauthenticated, consistent with
`PUT /mcp/auth` (ADR-0033 §2.5) and the rest of the REST API. The network perimeter +
bearer floor are the outer gates; the Settings UI is same-origin.

### 2.4 In-process cache: `_ClipConfigCache`

Mirrors `_McpAuthCache` (ADR-0033): a module-level singleton loaded from `vault_state` at
startup, refreshed on every `PUT /clip/config` write. Key methods:

| Method | Returns | Notes |
|---|---|---|
| `get_hash()` | `str \| None` | PBKDF2 hash for DB path; `None` = fall back to env. NEVER log or return. |
| `token_source()` | `"db"\|"env"\|"none"` | `get_hash() is not None` → `"db"`; else env present → `"env"`; else → `"none"` |
| `token_configured()` | `bool` | True when source is `"db"` or `"env"` |
| `resolved_enabled()` | `bool` | DB NOT NULL wins; else env `CLIP_ENABLED` |
| `resolved_allowed_origins_list()` | `list[str]` | DB NOT NULL wins; else env; plus loopback origins |
| `set_hash(v)` | — | Updates stored hash after PUT rotate/clear |

The `POST /clip` handler reads all resolved values O(1) from the cache (no DB round-trip per
clip request). The cache is the single point of resolution — the handler never reads env vars
directly.

### 2.5 POST /clip handler updates

The `POST /clip` handler is updated to read from `_clip_config_cache` instead of reading
`settings` directly for the three configurable aspects:

1. **Enabled gate:** `_clip_config_cache.resolved_enabled()` (DB or env).
2. **Token auth (source-aware):**
   - `token_source == "db"`: `_verify_token(presented, _clip_config_cache.get_hash())` — PBKDF2
   - `token_source == "env"`: `hmac.compare_digest(presented, settings.clip_token)` — plaintext
   - `token_source == "none"`: immediate 401
3. **Origin check:** `_clip_config_cache.resolved_allowed_origins_list()` passed to
   `_clip_origin_allowed()`.

`CLIP_MAX_BODY_BYTES` is not cached (env-only; reads `settings.clip_max_body_bytes` directly).
All 4 security properties from ADR-0038 §2 are preserved unchanged.

### 2.6 Existing tests unchanged

All 22 tests in `test_clip.py` (TC-CLIP-01..12 + unit helpers) remain green. Their env-based
setup (patching `cfg.settings.clip_enabled`, `cfg.settings.clip_token`,
`cfg.settings.clip_allowed_origins`) continues to work because:
- `clip_env` fixture patches settings directly AND the `_clip_config_cache` is not
  pre-loaded in those tests (the cache falls back to env when its DB slots are `None`).
- This is the defined env-fallback behaviour — the existing tests exercise the env path
  correctly, proving the fallback works.

No test in `test_clip.py` needs to be changed. The new `test_clip_config.py` tests the DB
path explicitly.

---

## 3. New config / schema / migration

| Kind | Name | Type / default | Where | Notes |
|---|---|---|---|---|
| DB column | `vault_state.clip_enabled_db` | `BOOLEAN NULL` | `models.py` `VaultState` | **NEW — Alembic `0015`.** NULL = env governs; NOT NULL = DB wins. |
| DB column | `vault_state.clip_access_token` | `TEXT NULL` | `models.py` `VaultState` | **NEW (same migration `0015`).** PBKDF2-SHA256 hash (prefix `pbkdf2_sha256$`); NEVER logged; raw token shown once (one-time reveal), then unhashable from DB. |
| DB column | `vault_state.clip_allowed_origins_db` | `TEXT NULL` | `models.py` `VaultState` | **NEW (same migration `0015`).** Comma-separated; NULL = env governs. |
| migration | `0015_vault_state_clip_config` | add 3 columns | `backend/alembic/versions/` | Run by standard `alembic upgrade head`. D2/ER regen (I8). |
| env (retained) | `CLIP_ENABLED` | bool, default false | `config.py` | Bootstrap fallback; DB `clip_enabled_db` wins when NOT NULL. |
| env (retained) | `CLIP_TOKEN` | secret str, no default | `config.py` | Bootstrap fallback; DB `clip_access_token` wins when NOT NULL. |
| env (retained) | `CLIP_ALLOWED_ORIGINS` | comma list, default "" | `config.py` | Bootstrap fallback; DB `clip_allowed_origins_db` wins when NOT NULL. |
| env (retained) | `CLIP_MAX_BODY_BYTES` | int, default 2MB | `config.py` | Not runtime-settable via PUT /clip/config; env-only. |
| in-process cache | `_ClipConfigCache` | class | `main.py` | Mirrors `_McpAuthCache`; loaded at startup; refreshed on PUT /clip/config. |
| route | `GET /clip/config` | `ClipConfigResponse` | `main.py` | Read-only posture; token NEVER returned. |
| route | `PUT /clip/config` | `ClipConfigRequest` → `ClipConfigStateResponse` | `main.py` | Set/rotate/clear token + enabled/origins. One-time `generated_token`. |

**One migration, three columns.** Flagged for the docs gate: `make er` + `make openapi`.

---

## 4. Acceptance checks (DoD)

1. **Token never returned.** `GET /clip/config` with a DB token set returns `token_source="db"`,
   `token_configured=true`, and the token value NEVER appears in the response body.
   (`test_clip_config_response_never_contains_token_value` + `test_get_clip_config_token_source_db`).
2. **One-time reveal.** `PUT /clip/config {rotate_token:true}` returns `generated_token` once;
   subsequent `GET /clip/config` does not return it.
   (`test_put_clip_config_rotate_token_one_time_reveal` + `test_get_clip_config_never_returns_token_after_rotate`).
3. **DB stores PBKDF2 hash, not raw token.** After rotate, `vault_state.clip_access_token`
   starts with `pbkdf2_sha256$` and does NOT contain the raw `generated_token` value.
   `_verify_token(generated_token, stored_hash)` returns True.
   (`test_put_clip_config_rotate_stores_pbkdf2_hash`).
4. **Rotated token authenticates POST /clip.** `generated_token` from PUT authenticates the next
   `POST /clip`; wrong/old tokens are rejected with 401.
   (`test_rotate_token_authenticates_post_clip`).
5. **DB wins over env — token.** DB token set → env token rejected at `POST /clip`.
   (`test_clip_honours_db_token_over_env`).
6. **DB wins over env — enabled.** DB `clip_enabled_db=False` → `POST /clip` returns 503 even
   when `CLIP_ENABLED=true`. DB `clip_enabled_db=True` → enabled even when env is false.
   (`test_put_clip_config_set_enabled_false_gates_clip_endpoint` +
   `test_put_clip_config_set_enabled_true_enables_ingress`).
7. **DB wins over env — origins.** DB origins → env-only origin rejected.
   (`test_clip_honours_db_allowed_origins`).
8. **Token source = 'db'/'env'/'none'.** Correct precedence: DB token → 'db'; no DB, env set → 'env';
   neither → 'none'. (`TestClipTokenSourceResolution`).
9. **Env fallback.** With no DB overrides, env vars govern (all three config aspects).
   (`TestClipEnabledResolution`, `TestClipAllowedOriginsResolution`).
10. **Existing test_clip.py green.** All 22 pre-existing clip tests pass unchanged.
    (env-fallback path covers their assertions).
11. **clear_token.** `clear_token=true` nulls DB token; source falls back to env or none.
    (`test_put_clip_config_clear_token`).
12. **Docs gate.** `make er` matches live schema (three new columns); `make openapi` includes
    `/clip/config` GET+PUT (I8).

---

## 5. Consequences

**Positive** — the operator can enable the clipper, generate/rotate/clear its token, and
manage allowed origins from the Settings UI without editing `.env` or restarting. Env vars
remain as bootstrap fallback — zero breakage for existing deployments.

**Trade-offs** —
- **One migration, three columns** (the only schema change; D2/ER regen in docs gate).
- **PBKDF2 hash per clip request (DB path):** 260k iterations adds latency on `POST /clip` when
  token_source is "db". Acceptable for a personal homelab clipper (one ingest per browser action).
  Env path continues to use `hmac.compare_digest` (plaintext, zero overhead).
- **Token not re-displayable** — after the one-time reveal, the operator must rotate if they
  lose the token. This is the more secure UX (matches MCP).
- **Cache invalidation is single-process** — on a multi-process deployment, cache is stale after
  PUT /clip/config until restart. Acceptable for a personal homelab deployment (single process).

**Invariant check** — **I1:** no new ingest path; `POST /clip` still writes to `raw/sources/`
and lets the watcher ingest (unchanged). **I5:** vault-write path unchanged. **I6:** no provider
touched. **I7:** no new loop. **I8:** migration → D2/ER regen + D4 OpenAPI update. **I9:**
reuses `vault_state` + existing `POST /clip` — no new process, no new dep.
