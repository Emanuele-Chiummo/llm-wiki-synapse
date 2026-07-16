# ADR-0070 — Multi-provider web search behind a provider seam (P3-e)

- **Status:** Accepted
- **Date:** 2026-07-10
- **Feature:** F10 (Deep Research / web search) · v1.5 LLM Wiki parity, slice P3-e
- **Relates:** [[ADR-0024]] (SearXNG search stack) · [[ADR-0041]] (runtime SearXNG URL config) · [[ADR-0066]] (parity program — amends I9 for opt-in providers) · [[ADR-0069]] (MinerU — the mirrored config split for a cloud provider)

## Context

Until now **all** web search went through `backend/app/ops/searxng.py` — SearXNG was the single,
bundled backend (ADR-0024/ADR-0041, original I9: "SearXNG only, never Tavily"). ADR-0066 amended
I9: SearXNG stays the **default, bundled, privacy-preserving** backend, but additional providers
(Tavily · SerpApi · Firecrawl · Brave · Ollama-Web) are **ALLOWED as opt-in, OFF by default**.
This ADR builds the seam, adapters, config, and UI under those constraints.

## Decision

1. **New package `backend/app/ops/web_search/`.**
   - `base.py`: `WebSearchProvider` ABC — `async search_many(queries) -> list[SearchHit]`,
     `configured() -> bool`, and metadata `name` / `is_cloud` / `requires_upload_warning`. The
     default `search_many` gives every adapter the shared `asyncio.Semaphore(CONCURRENCY=3)` bound
     (I7) and first-seen URL-dedup (identical to `searxng_search_many`). `SearchHit` is re-exported.
   - `searxng.py`: thin wrapper delegating verbatim to the existing `ops/searxng.py`
     (`searxng_search_many` / `searxng_search`). SearXNG behaviour, URL resolution (DB-over-env,
     ADR-0041), and every existing SearXNG test are untouched — a pure refactor for SearXNG.
   - `tavily.py` · `serpapi.py` · `firecrawl.py` · `brave.py` · `ollama_web.py`: defensive
     best-effort adapters (mirroring the MinerU adapter, ADR-0069) — a real HTTP call, but on ANY
     failure (missing key, non-2xx, timeout, parse error) they return `[]` and log a WARNING,
     never raising. Ollama-Web uses the local `OLLAMA_URL` endpoint (no cloud key).
   - `__init__.py`: `get_web_search_provider()` factory (reads the effective `web_search_provider`
     config key, default `"searxng"`, unknown → fail-safe SearXNG) and `web_search_many(queries)`
     dispatcher — the single web-search entry point.

2. **Config split mirrors ADR-0069 exactly.** The **non-secret** selector `web_search_provider`
   (S23) is a runtime config-override key (enum `searxng|tavily|serpapi|firecrawl|brave|ollama_web`,
   default `searxng`, validated). The **secret** cloud API keys — `TAVILY_API_KEY`,
   `SERPAPI_API_KEY`, `FIRECRAWL_API_KEY`, `BRAVE_API_KEY` — are **env-only** in `config.py` and are
   **structurally excluded** from the config-override surface (config_overrides §2.4). Adding S23
   took `ALLOWED_CONFIG_KEYS` 22 → 23; snapshot/count tests updated.

3. **Callers route through the seam.** `ops/deep_research.py` (`_search_searxng`) and
   `chat/web_context.py` now call `web_search_many` / the provider seam. Bounds (max_queries +
   shared semaphore, I7) and URL-dedup are unchanged.

4. **"Not configured" guard is provider-aware.** `POST /research/start` and the review deep-research
   action now 503 when **the selected provider** `.configured()` is false, with a message naming the
   provider and how to configure it (SEARXNG_URL / the matching API key / OLLAMA_URL, or switch via
   `PUT /config/app/web_search_provider`). SearXNG stays the default and treats an empty URL as
   unconfigured (matches the pre-seam guards).

5. **UI (SectionWebSearch).** A provider selector card (one row per provider) — SearXNG selected by
   default and labelled "Default · privacy-preserving"; selecting a cloud backend shows an amber
   opt-in warning that queries are sent to a third-party service and require the API key in the
   environment. The existing SearXNG URL/categories/max-queries fields show only when SearXNG is
   active. Branding: never black — selected state uses `var(--syn-accent)` + white; warnings use
   `var(--syn-amber)`. All existing `data-testid`s preserved.

6. **Wire protocols are provisional.** The four cloud adapters implement each vendor's documented
   search contract, but — as with ADR-0069/MinerU — the exact endpoint/response shape MUST be
   validated against a live API key before relying on cloud search (no keys in dev/CI). The seam,
   selection, gating, dedup, fallback, and UX are complete and tested; the live cloud round-trip is
   the one untested piece. Ollama-Web's `/api/web_search` contract likewise needs live validation.

## Consequences

- Six web-search backends; only SearXNG and Ollama-Web keep queries on the local network. The
  privacy posture is explicit in the UI (default badge vs amber cloud warning).
- No provider is hardcoded (I6): every call resolves `web_search_provider` at runtime.
- Search stays bounded (I7): the shared semaphore and `max_queries` cap are unchanged across
  providers.
- The two I9 static-guard tests (`test_no_forbidden_search_imports`,
  `test_i9_no_non_searxng_provider_imports`) now exclude the sanctioned `ops/web_search/` seam and
  still fail if an alternative backend leaks anywhere else in `ops/`.

## Tests

`backend/tests/test_web_search_seam.py` — provider selection via config (default + each value +
unknown→fallback), registry↔enum parity, cloud metadata/upload-warning, opt-in `configured()`
(no key ⇒ no HTTP call ⇒ []), mocked-httpx routing (Tavily/SerpApi/Brave parse), non-2xx →
[] degrade, dedup, dispatcher wiring, SearXNG refactor preserved. Guard/count tests updated in
`test_deep_research.py`, `test_web_search_config.py`, `test_config_overrides.py`, `test_stats.py`,
`test_ops_scheduler.py`. Chat: `test_b2_chat_composer.py` re-pointed to the seam. Frontend:
`SettingsPanel.test.tsx` covers the provider selector default + cloud-select persist + amber warning.
