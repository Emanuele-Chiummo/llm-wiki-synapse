"""
SearXNG JSON search client — the ONLY web-search code in the codebase (I9, ADR-0024 §4).

THE I9 RULE (P0): No other module may import any search library or call any non-SearXNG
search backend. This module is the sole place web-search HTTP calls are made.
Test AC-F10-3 performs a static scan of all ops/ .py files and fails if any forbidden
third-party search-library names are found (see test_deep_research.py for the guard
test: test_no_forbidden_search_imports).

Config: base URL from env SEARXNG_URL only (settings.searxng_url).
No API key. No fallback engine — a SearXNG failure degrades to fewer/zero hits, logged.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

# HARDCODED module constant — architect-approval gate to change (ADR-0024 §3.1).
# Shared by search AND fetch to bound total concurrent outbound connections.
CONCURRENCY: int = 3

# Module-level semaphore (single shared ceiling, ADR-0024 §3.1 / Do-NOT #4).
_semaphore: asyncio.Semaphore = asyncio.Semaphore(CONCURRENCY)


class SearchHit(BaseModel):
    """One result from SearXNG (ADR-0024 §4)."""

    url: str
    title: str
    snippet: str | None = None
    engine: str | None = None


async def searxng_search(query: str, *, max_results: int = 10) -> list[SearchHit]:
    """
    ONE SearXNG query → JSON results. Base URL from env SEARXNG_URL ONLY (I9).

    Calls GET {SEARXNG_URL}/search?q=<query>&format=json (SearXNG JSON API, R8).
    No API key. On non-200 → [] (logged), never an alternative backend.
    """
    base_url = settings.searxng_url
    if not base_url:
        logger.warning("searxng_search: SEARXNG_URL is not set — returning empty results (I9)")
        return []

    url = f"{base_url.rstrip('/')}/search"
    params = {"q": query, "format": "json"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
    except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError) as exc:
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

    raw_results = data.get("results", [])
    hits: list[SearchHit] = []
    for item in raw_results[:max_results]:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("url") or item.get("link") or ""
        if not raw_url:
            continue
        hits.append(
            SearchHit(
                url=raw_url,
                title=item.get("title") or raw_url,
                snippet=item.get("content") or item.get("snippet"),
                engine=item.get("engine"),
            )
        )

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
