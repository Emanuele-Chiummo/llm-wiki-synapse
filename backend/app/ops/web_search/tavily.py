"""
Tavily adapter — OPT-IN cloud web search (ADR-0070, OFF by default).

⚠️ CLOUD (I9): selecting this backend sends every query to Tavily's API. The API key is a
SECRET read from ``settings.tavily_api_key`` (env-only ``TAVILY_API_KEY``; NEVER exposed on the
config-override surface, config_overrides §2.4). When the key is unset the adapter is a no-op
that returns [] (the operator can select the provider in the UI, but nothing is sent until the
key is set in the environment).

Best-effort contract (like the MinerU adapter, ADR-0069): a real HTTP call, but on ANY failure
(missing key, non-2xx, timeout, parse error) return [] and log a WARNING — never raise.

Wire protocol (Tavily Search API, https://docs.tavily.com/documentation/api-reference):
  POST {base}/search  JSON {"api_key", "query", "max_results"}
    → {"results": [{"url", "title", "content"}]}
⚠️ Implemented against the documented contract; MUST be validated against a live Tavily key
before it is relied upon in production (mirrors ADR-0069's MinerU caveat).
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

from .base import SearchHit, WebSearchProvider

logger = logging.getLogger(__name__)

_TAVILY_BASE_URL = "https://api.tavily.com"
_TIMEOUT_S = 15.0
_MAX_RESULTS = 10


class TavilyProvider(WebSearchProvider):
    """Tavily cloud search (opt-in). is_cloud/requires_upload_warning=True (I9)."""

    name = "tavily"
    is_cloud = True
    requires_upload_warning = True

    def configured(self) -> bool:
        """True iff TAVILY_API_KEY is set (env-only secret)."""
        return bool(settings.tavily_api_key)

    async def _search_one(self, query: str) -> list[SearchHit]:
        api_key = settings.tavily_api_key
        if not api_key:
            logger.warning("tavily: TAVILY_API_KEY not set — returning [] (I9 opt-in)")
            return []
        try:
            body = {"api_key": api_key, "query": query, "max_results": _MAX_RESULTS}
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.post(f"{_TAVILY_BASE_URL}/search", json=body)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("tavily: search failed for %r: %s — returning []", query, exc)
            return []

        hits: list[SearchHit] = []
        for item in data.get("results", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or ""
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=item.get("title") or url,
                    snippet=item.get("content"),
                    engine="tavily",
                )
            )
        return hits
