"""
SearXNG JSON search client — the ONLY web-search code in the codebase (I9, ADR-0024 §4).

THE I9 RULE (P0): No other module may import any search library or call any non-SearXNG
search backend. This module is the sole place web-search HTTP calls are made.
Test AC-F10-3 performs a static scan of all ops/ .py files and fails if any forbidden
third-party search-library names are found (see test_deep_research.py for the guard
test: test_no_forbidden_search_imports).

Config: base URL from runtime config cache (ADR-0041) or env SEARXNG_URL fallback.
No API key. No fallback engine — a SearXNG failure degrades to fewer/zero hits, logged.

URL resolution precedence (ADR-0041 §2.2):
  1. DB vault_state.searxng_url_db (if set via PUT /web-search/config) — wins.
  2. SEARXNG_URL env var (settings.searxng_url) — fallback.
  3. None — not configured; search returns []; POST /research/start returns 503.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)

# HARDCODED module constant — architect-approval gate to change (ADR-0024 §3.1).
# Shared by search AND fetch to bound total concurrent outbound connections.
CONCURRENCY: int = 3

# Module-level semaphore (single shared ceiling, ADR-0024 §3.1 / Do-NOT #4).
_semaphore: asyncio.Semaphore = asyncio.Semaphore(CONCURRENCY)


def _resolve_searxng_url() -> str | None:
    """
    Resolve the active SearXNG base URL with DB-over-env precedence (ADR-0041 §2.2).

    Precedence:
      1. DB vault_state.searxng_url_db (if set) — from _web_search_config_cache.
      2. SEARXNG_URL env var (settings.searxng_url) — fallback.
      3. None — neither configured.

    The import of _web_search_config_cache from app.main is deferred to avoid a
    circular import (main → ops/searxng → main). It is lazy-safe: if the cache
    singleton does not yet exist (e.g., tests that do not start the lifespan), the
    ImportError is caught and we fall back to the env var.
    """
    try:
        from app.runtime_state import (
            web_search_config_cache as _web_search_config_cache,
        )  # noqa: PLC0415

        return _web_search_config_cache.resolved_url()
    except (ImportError, AttributeError):
        # Fall back to env if main has not been imported (e.g., isolated unit tests).
        return settings.searxng_url


class SearchHit(BaseModel):
    """One result from SearXNG (ADR-0024 §4)."""

    url: str
    title: str
    snippet: str | None = None
    engine: str | None = None


async def searxng_search(query: str, *, max_results: int = 10) -> list[SearchHit]:
    """
    ONE SearXNG query → JSON results. URL resolved via ADR-0041 precedence (I9).

    Calls GET {SEARXNG_URL}/search?q=<query>&format=json (SearXNG JSON API, R8).
    No API key. On non-200 → [] (logged), never an alternative backend.

    Best-effort, like every other backend behind ``WebSearchProvider._search_one``: ANY
    failure — transport, decode, or malformed payload — returns [] with a WARNING and
    never raises into the caller.
    """
    base_url = _resolve_searxng_url()
    if not base_url:
        logger.warning("searxng_search: SEARXNG_URL is not set — returning empty results (I9)")
        return []

    url = f"{base_url.rstrip('/')}/search"
    params = {"q": query, "format": "json"}

    # Best-effort, exactly as WebSearchProvider._search_one declares for EVERY backend:
    # "on ANY failure return [] and log a WARNING — never raise into the caller". The
    # previous except list named three httpx types and therefore honoured that contract
    # for only part of httpx's error surface: ConnectError is already a NetworkError, so
    # it really caught just TimeoutException + NetworkError, leaving every sibling of
    # NetworkError under TransportError to escape — RemoteProtocolError (a server that
    # disconnects mid-response, the ordinary failure mode behind a tunnel or a restarting
    # SearXNG), ProxyError, and UnsupportedProtocol (an operator saving a scheme-less
    # "searxng.local:8080" through PUT /web-search/config, which is stored and used
    # verbatim) — plus DecodingError and InvalidURL, which are not TransportErrors at all.
    # That mattered because SearXNG is the DEFAULT backend and the only one that misses
    # the contract: an escaping error propagates through searxng_search_many's gather,
    # cancelling the other in-flight queries, up to run_deep_research's terminal handler,
    # which fails the WHOLE research run — where every opt-in adapter in ops/web_search/
    # wraps its call in a blanket except and simply degrades to zero hits.
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
    except Exception as exc:  # noqa: BLE001 — see above; CancelledError is a BaseException
        logger.warning("searxng_search: request failed for %r: %s — returning []", query, exc)
        return []

    if response.status_code != 200:
        logger.warning(
            "searxng_search: SearXNG returned HTTP %d for query %r — returning []",
            response.status_code,
            query,
        )
        return []

    try:
        data = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("searxng_search: JSON parse error for query %r: %s", query, exc)
        return []

    # Same contract as above, applied to the SHAPE of the decoded body: it is a remote
    # service's JSON, not a validated schema. A top-level array made `data.get` an
    # AttributeError, a non-list "results" made the slice a TypeError, and a non-string
    # url/title made the SearchHit construction a pydantic ValidationError — each of them
    # an exception escaping a function documented to return [] instead.
    raw_results = data.get("results", []) if isinstance(data, dict) else []
    if not isinstance(raw_results, list):
        raw_results = []
    hits: list[SearchHit] = []
    for item in raw_results[:max_results]:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("url") or item.get("link") or ""
        if not isinstance(raw_url, str) or not raw_url:
            continue
        try:
            hit = SearchHit(
                url=raw_url,
                title=item.get("title") or raw_url,
                snippet=item.get("content") or item.get("snippet"),
                engine=item.get("engine"),
            )
        except ValidationError as exc:
            logger.warning(
                "searxng_search: dropping malformed result %r for query %r: %s",
                raw_url,
                query,
                exc,
            )
            continue
        hits.append(hit)

    logger.debug("searxng_search: %d hits for query %r", len(hits), query)
    return hits


async def searxng_search_many(queries: list[str]) -> list[SearchHit]:
    """
    Run all queries with concurrency bounded by the module asyncio.Semaphore(CONCURRENCY=3).

    Implemented as asyncio.gather over searxng_search, each acquiring the semaphore.
    De-dupes hits by URL (preserves first-seen order).
    This is the ONLY concurrency in F10 search (Do-NOT #4).
    """
    if not queries:
        return []

    async def _bounded_search(q: str) -> list[SearchHit]:
        async with _semaphore:
            return await searxng_search(q)

    results_nested = await asyncio.gather(*[_bounded_search(q) for q in queries])

    # De-dupe by URL, preserving first-seen order
    seen_urls: set[str] = set()
    deduped: list[SearchHit] = []
    for batch in results_nested:
        for hit in batch:
            if hit.url not in seen_urls:
                seen_urls.add(hit.url)
                deduped.append(hit)

    logger.debug(
        "searxng_search_many: %d queries → %d unique hits",
        len(queries),
        len(deduped),
    )
    return deduped
