"""
Web-search provider seam — base ABC (v1.5 P3-e, ADR-0066/ADR-0070).

ADR-0066 amended I9: SearXNG stays the DEFAULT, bundled, privacy-preserving backend, but
additional providers (Tavily · SerpApi · Firecrawl · Brave · Ollama-Web) are ALLOWED as
opt-in, OFF-by-default alternatives. This module defines the abstraction ALL web-search calls
route through so the concrete backend is selected at runtime from the ``web_search_provider``
config key — never hardcoded (I6-spirit).

Contract
--------
``WebSearchProvider.search_many(queries)`` runs a bounded fan-out (shared asyncio.Semaphore
from ops/searxng.py — I7) and returns a URL-deduped ``list[SearchHit]`` (first-seen order,
identical to ``searxng_search_many``). Concrete adapters implement ``_search_one`` (one query
→ hits) and ``configured`` (is this backend usable?). The SearXNG adapter overrides
``search_many`` to delegate verbatim to the existing ``ops/searxng.py`` code (refactor-safe:
existing SearXNG behaviour + tests are untouched).

Metadata (surfaced to the UI / guards):
  name                        — stable id ("searxng" | "tavily" | ...).
  is_cloud                    — True when queries leave the local network (I9 warning).
  requires_upload_warning     — True when the UI must warn the operator about third-party upload.

SearchHit is re-exported here so callers import the single canonical model from one place.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

# Re-export the canonical SearchHit model + shared concurrency ceiling (I7).
# ops/searxng.py never imports this package, so there is no circular import.
from app.ops.searxng import CONCURRENCY, SearchHit, _semaphore

logger = logging.getLogger(__name__)

__all__ = ["CONCURRENCY", "SearchHit", "WebSearchProvider", "_semaphore"]


class WebSearchProvider(ABC):
    """
    Abstract web-search backend (ADR-0070).

    Subclasses set the three metadata attributes and implement ``configured`` + ``_search_one``.
    The default ``search_many`` gives every non-SearXNG adapter identical bounding (I7) and
    URL-dedup semantics for free.
    """

    #: Stable provider id (matches the ``web_search_provider`` config enum value).
    name: str = "base"
    #: True when the provider sends queries to an external cloud service (I9).
    is_cloud: bool = False
    #: True when the UI must warn the operator that queries leave the local network (I9).
    requires_upload_warning: bool = False

    @abstractmethod
    def configured(self) -> bool:
        """Return True iff this backend has everything it needs to run (URL / API key)."""
        raise NotImplementedError

    @abstractmethod
    async def _search_one(self, query: str) -> list[SearchHit]:
        """
        Run ONE query → hits. Best-effort: on ANY failure return [] and log a WARNING —
        never raise into the caller (degrade to fewer/zero hits, same as SearXNG, I9).
        """
        raise NotImplementedError

    async def search_many(self, queries: list[str]) -> list[SearchHit]:
        """
        Run all queries bounded by the shared ``asyncio.Semaphore(CONCURRENCY)`` (I7) and
        de-dupe hits by URL preserving first-seen order — behaviourally identical to
        ``ops.searxng.searxng_search_many``.
        """
        if not queries:
            return []

        async def _bounded(q: str) -> list[SearchHit]:
            async with _semaphore:
                return await self._search_one(q)

        results_nested = await asyncio.gather(*[_bounded(q) for q in queries])

        seen_urls: set[str] = set()
        deduped: list[SearchHit] = []
        for batch in results_nested:
            for hit in batch:
                if hit.url not in seen_urls:
                    seen_urls.add(hit.url)
                    deduped.append(hit)

        logger.debug(
            "%s.search_many: %d queries → %d unique hits",
            self.name,
            len(queries),
            len(deduped),
        )
        return deduped
