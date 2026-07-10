"""
Firecrawl adapter — OPT-IN cloud web search (ADR-0070, OFF by default).

⚠️ CLOUD (I9): sends every query to Firecrawl. Secret key from ``settings.firecrawl_api_key``
(env-only ``FIRECRAWL_API_KEY``; NEVER on the config-override surface, §2.4). Unset → no-op ([]).
Best-effort: on ANY failure return [] + WARNING, never raise (mirrors ADR-0069/MinerU).

Wire protocol (Firecrawl Search, https://docs.firecrawl.dev/features/search):
  POST {base}/v1/search  (Authorization: Bearer <key>)  JSON {"query"}
    → {"data": [{"url", "title", "description"}]}
⚠️ Implemented against the documented contract; MUST be validated against a live Firecrawl key.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

from .base import SearchHit, WebSearchProvider

logger = logging.getLogger(__name__)

_FIRECRAWL_BASE_URL = "https://api.firecrawl.dev"
_TIMEOUT_S = 15.0
_LIMIT = 10


class FirecrawlProvider(WebSearchProvider):
    """Firecrawl cloud search (opt-in). is_cloud/requires_upload_warning=True (I9)."""

    name = "firecrawl"
    is_cloud = True
    requires_upload_warning = True

    def configured(self) -> bool:
        """True iff FIRECRAWL_API_KEY is set (env-only secret)."""
        return bool(settings.firecrawl_api_key)

    async def _search_one(self, query: str) -> list[SearchHit]:
        api_key = settings.firecrawl_api_key
        if not api_key:
            logger.warning("firecrawl: FIRECRAWL_API_KEY not set — returning [] (I9 opt-in)")
            return []
        try:
            body = {"query": query, "limit": _LIMIT}
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{_FIRECRAWL_BASE_URL}/v1/search", json=body, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("firecrawl: search failed for %r: %s — returning []", query, exc)
            return []

        hits: list[SearchHit] = []
        for item in data.get("data", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or ""
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=item.get("title") or url,
                    snippet=item.get("description"),
                    engine="firecrawl",
                )
            )
        return hits
