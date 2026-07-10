"""
Web-search provider seam — factory + dispatcher (v1.5 P3-e, ADR-0066/ADR-0070).

ALL web search in Synapse routes through :func:`web_search_many`, which resolves the concrete
backend from the ``web_search_provider`` config-override key (default ``"searxng"`` — I6: never
hardcoded). SearXNG is the DEFAULT, bundled, privacy-preserving backend; the other adapters
(Tavily · SerpApi · Firecrawl · Brave · Ollama-Web) are OPT-IN, OFF by default (ADR-0066
amends I9). Selecting an unknown value fails safe to SearXNG.

Public API
----------
get_web_search_provider() -> WebSearchProvider     — resolve the effective backend (factory)
async web_search_many(queries) -> list[SearchHit]  — the single dispatcher callers use
PROVIDERS: dict[str, type[WebSearchProvider]]       — id → adapter class (also the enum source)
SearchHit                                           — re-exported canonical result model
"""

from __future__ import annotations

import logging

from app.config_overrides import effective_str

from .base import SearchHit, WebSearchProvider
from .brave import BraveProvider
from .firecrawl import FirecrawlProvider
from .ollama_web import OllamaWebProvider
from .searxng import SearxngProvider
from .serpapi import SerpApiProvider
from .tavily import TavilyProvider

logger = logging.getLogger(__name__)

DEFAULT_PROVIDER = "searxng"

# id → adapter class. Also the single source of truth for the config enum + UI catalog.
PROVIDERS: dict[str, type[WebSearchProvider]] = {
    "searxng": SearxngProvider,
    "tavily": TavilyProvider,
    "serpapi": SerpApiProvider,
    "firecrawl": FirecrawlProvider,
    "brave": BraveProvider,
    "ollama_web": OllamaWebProvider,
}

__all__ = [
    "PROVIDERS",
    "SearchHit",
    "WebSearchProvider",
    "get_web_search_provider",
    "web_search_many",
]


def get_web_search_provider() -> WebSearchProvider:
    """
    Resolve the effective web-search backend from the ``web_search_provider`` config key.

    Reads the ADR-0053 override cache (default ``"searxng"``). An unknown/blank value fails safe
    to SearXNG (logged). No DB round-trip — ``effective_str`` is an O(1) in-memory read (I7).
    """
    name = (effective_str("web_search_provider", DEFAULT_PROVIDER) or DEFAULT_PROVIDER).strip()
    provider_cls = PROVIDERS.get(name)
    if provider_cls is None:
        logger.warning(
            "web_search: unknown web_search_provider %r — falling back to %s (ADR-0070)",
            name,
            DEFAULT_PROVIDER,
        )
        provider_cls = SearxngProvider
    return provider_cls()


async def web_search_many(queries: list[str]) -> list[SearchHit]:
    """
    Run ``queries`` through the currently-selected web-search backend (ADR-0070).

    The single web-search entry point for all callers (deep_research, chat web-context).
    Bounding (shared semaphore, I7) and URL-dedup live in the provider; this is a thin dispatch.
    """
    provider = get_web_search_provider()
    return await provider.search_many(queries)
