# ADR-0041 — SearXNG web-search runtime configuration (GET/PUT /web-search/config; DB wins over env)

- **Status:** Accepted
- **Date:** 2026-07-01
- **Sprint:** v0.6 (Amendment — F10 Deep Research runtime configuration)
- **Feature:** F10 (Deep Research, ADR-0024); mirrors the clip config pattern (ADR-0040) but simpler (URL is not a secret)
- **Invariants owned:** **I8** (D2 ER + D4 OpenAPI updated) · **I9** (SearXNG is the ONLY web-search backend; no Tavily/serpapi/duckduckgo/google-search)
- **Author:** solution-architect
- **Implementers:** backend-engineer (models + migration `0016` + in-process cache + `GET/PUT /web-search/config` + update `POST /research/start` 503 gate + `ops/searxng.py` URL resolver + tests + ADR) · tech-writer (D2 ER regen, D4 OpenAPI, README row)

---

## 1. Context

ADR-0024 shipped F10 Deep Research as an **env-only** configuration: `SEARXNG_URL` controls
which SearXNG instance is used, and `DEEP_RESEARCH_MAX_QUERIES` sets the per-iteration query
cap. There is no way to change these values from the web UI without editing env vars and
restarting the container.

**Owner request:** add runtime configuration for the SearXNG web-search backend, mirroring
the clip config pattern (ADR-0040) but simpler — the SearXNG URL is an **open internal service
URL, not a secret**, so:
- GET returns the URL in full (no masking, no token_configured pattern)
- No PBKDF2, no one-time-reveal, no hash storage
- DB value is plain text; same blast-radius as the `.env` file

---

## 2. Decision

### 2.1 Three new columns on `vault_state`; DB wins when set; env is the fallback

**Resolution precedence (most specific wins):**

| Config aspect | DB column | Env var | Resolution rule |
|---|---|---|---|
| SearXNG base URL | `searxng_url_db` (TEXT NULL) | `SEARXNG_URL` | DB NOT NULL → DB wins; DB NULL → env |
| SearXNG categories | `searxng_categories_db` (TEXT NULL) | *(code default: empty)* | DB NOT NULL → DB wins; DB NULL → code default (empty list → SearXNG decides) |
| Max queries per iteration | `searxng_max_queries_db` (INTEGER NULL) | `DEEP_RESEARCH_MAX_QUERIES` | DB NOT NULL → DB wins; DB NULL → env |

**Why DB wins over env:** same rationale as ADR-0040 and ADR-0033 — the UI-set value is the
*current operator intent*. The env value is the *bootstrap / deployment default*. Once the
operator has used the UI to change a setting, the UI value should hold.

**Why `searxng_url_db` is TEXT NULL (not a boolean-plus-text like clip_enabled):** the URL
itself is the enablement signal. NULL means "not set in DB; fall back to env". A non-NULL
value means "operator has explicitly set this via the UI".

### 2.2 URL is NOT a secret — returned verbatim by GET

**KEY DIFFERENCE FROM CLIP (ADR-0040) AND MCP (ADR-0033):**

- The clip token and MCP token are authentication credentials. They are NEVER returned by GET.
- The SearXNG URL is an internal service URL (e.g., `http://searxng:8080`). It is no more
  sensitive than the URL in the `.env` file. Any user with UI access already knows what
  SearXNG instance is configured.
- **Decision:** GET /web-search/config returns the URL in full. No masking, no
  `token_configured` indirection, no one-time-reveal.

### 2.3 GET /web-search/config contract

```
GET /web-search/config
→ 200 {
    configured: bool,          // true iff a SearXNG URL is available (DB or env)
    url: str | null,           // resolved URL (returned in full — NOT a secret)
    categories: list[str],     // resolved categories (split from DB comma-separated)
    max_queries: int,          // resolved max queries (DB wins over env)
    source: "db"|"env"|"none"  // which URL source is authoritative
  }
```

All values derived from the in-process `_web_search_config_cache` — no DB round-trip per GET.

### 2.4 PUT /web-search/config contract

```
PUT /web-search/config {
    set_url?: str | null,      // set searxng_url_db; must be http(s); null → leave unchanged
    set_categories?: str,      // comma-separated; "" clears to default; omit → leave unchanged
    set_max_queries?: int,     // 1–50; omit → leave unchanged
    clear?: bool               // if true, null ALL three DB columns → full env fallback
}
→ 200 WebSearchConfigStateResponse  // same shape as GET response, post-write posture
```

Validation:
- `set_url` must start with `http://` or `https://` (case-insensitive); else 422.
- `set_max_queries` must be 1–50 (Pydantic `ge=1, le=50`); else 422.
- `clear=true` is applied FIRST; then `set_*` fields are applied (allows "clear + set" in one call).

**I9 guard:** No `provider` field. PUT /web-search/config rejects any attempt to configure a
non-SearXNG search provider with 422. SearXNG is the ONLY web-search backend (I9).

### 2.5 In-process `_WebSearchConfigCache` singleton

Mirrors `_ClipConfigCache` (ADR-0040) but simpler:
- Loaded from `vault_state` at startup via `_load_web_search_config_cache()`.
- Refreshed on each PUT /web-search/config write (set_url_db / set_categories_db / set_max_queries_db).
- All handlers read resolved values O(1) — no DB round-trip per request.
- Thread/coroutine-safe via `asyncio.Lock`.

### 2.6 Circular import resolution: deferred import in `ops/searxng.py`

`app.main` imports `ops.searxng`. If `ops.searxng` imported `app.main._web_search_config_cache`
at module level, a circular import would occur. Solution: a `_resolve_searxng_url()` helper in
`ops/searxng.py` that does a **deferred import** inside the function body, wrapped in
`try/except (ImportError, AttributeError)` to fall back to `settings.searxng_url` when the cache
is not yet initialised (e.g., during unit tests that import `ops.searxng` directly without the
full app startup sequence).

```python
def _resolve_searxng_url() -> str | None:
    try:
        from app.main import _web_search_config_cache  # noqa: PLC0415
        return _web_search_config_cache.resolved_url()
    except (ImportError, AttributeError):
        return settings.searxng_url
```

### 2.7 503 gate in `POST /research/start`

Before ADR-0041, the check was:
```python
if not settings.searxng_url:
    raise HTTPException(status_code=503, ...)
```

After ADR-0041, the check uses the cache:
```python
if not _web_search_config_cache.configured():
    raise HTTPException(status_code=503, ...)
```

This preserves the existing 503 behavior when neither DB nor env is set, and enables the
deep-research endpoint when only the DB URL is set (no env var required after runtime config).

---

## 3. Schema change — Alembic migration 0016

```sql
ALTER TABLE vault_state ADD COLUMN searxng_url_db TEXT;
ALTER TABLE vault_state ADD COLUMN searxng_categories_db TEXT;
ALTER TABLE vault_state ADD COLUMN searxng_max_queries_db INTEGER;
```

All columns are nullable (NULL = not set in DB; env fallback applies). Migration id: `0016`.

---

## 4. Env-fallback precedence summary

```
_web_search_config_cache.resolved_url()
  → if _url_db is not None: return _url_db         # DB wins
  → return settings.searxng_url                     # env fallback (may be None)

_web_search_config_cache.resolved_categories()
  → if _categories_db is not None: split + return  # DB wins
  → return []                                       # code default (SearXNG decides)

_web_search_config_cache.resolved_max_queries()
  → if _max_queries_db is not None: return value   # DB wins
  → return settings.deep_research_max_queries       # env fallback (default: 5)
```

---

## 5. I9 enforcement

1. **No `provider` field in PUT /web-search/config** — only SearXNG config fields accepted.
2. **`ops/searxng.py` static guard (ADR-0024):** imports of tavily/serpapi/duckduckgo/
   google-search are forbidden; test `test_i9_no_non_searxng_provider_imports` scans ops/ at
   AST + raw-text level.
3. **URL validation:** `set_url` must be http(s) — rejects non-URL strings.

---

## 6. Backward compatibility

- Deployments without migration 0016 applied: `getattr(state, "searxng_url_db", None)` in
  `_load_web_search_config_cache()` returns None for missing columns → env governs → zero
  behavior change.
- All 992 pre-existing tests pass unchanged (env-fallback path preserved).
- The 503 detail message still contains "SEARXNG_URL" so existing test assertions on the
  message text pass unchanged.

---

## 7. Do-NOT list

1. Do NOT add Tavily, serpapi, duckduckgo, or any non-SearXNG search provider (I9).
2. Do NOT add PBKDF2 or token masking — the URL is not a secret (§2.2).
3. Do NOT mask the URL in GET /web-search/config — it MUST be returned verbatim (§2.3).
4. Do NOT add a `provider` field to PUT /web-search/config (I9 guard).
5. Do NOT import `_web_search_config_cache` at module level in `ops/searxng.py` (circular import — §2.6).
6. Do NOT re-read DB on every `POST /research/start` — use the cache (O(1) per request).
7. Do NOT skip migration 0016 — the columns must exist for the cache to persist across restarts.
8. Do NOT add frontend changes (backend-only feature).
9. Do NOT add new deps (reuses existing SearXNG/Postgres/SQLAlchemy stack — I9).
10. Do NOT use a `while True` loop — SearXNG lookup is a single function call, not a loop (I7).
