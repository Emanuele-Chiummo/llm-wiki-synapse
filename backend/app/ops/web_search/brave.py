"""
Brave adapter — OPT-IN cloud web search (ADR-0070, OFF by default).

⚠️ CLOUD (I9): sends every query to the Brave Search API. Secret key from
``settings.brave_api_key`` (env-only ``BRAVE_API_KEY``; NEVER on the config-override surface,
§2.4). Unset → no-op ([]). Best-effort: on ANY failure return [] + WARNING (mirrors ADR-0069).

Wire protocol (Brave Search API, https://api-dashboard.search.brave.com/app/documentation):
  GET {base}/res/v1/web/search?q=<q>  X-Subscription-Token: <key>
    → {"web":{"results":[{"url","title","description"}]}}
⚠️ Implemented against the documented contract; MUST be validated against a live Brave key.
"""

from __future__ import annotations

import logging

import httpx

from app.ops.web_search.keys import get_web_search_api_key

from .base import SearchHit, WebSearchProvider

logger = logging.getLogger(__name__)

_BRAVE_BASE_URL = "https://api.search.brave.com"
_TIMEOUT_S = 15.0
_COUNT = 10


class BraveProvider(WebSearchProvider):
    """Brave cloud search (opt-in). is_cloud/requires_upload_warning=True (I9)."""

    name = "brave"
    is_cloud = True
    requires_upload_warning = True

    def configured(self) -> bool:
        """True iff BRAVE_API_KEY is set (env-only secret)."""
        return get_web_search_api_key("brave") is not None

    async def _search_one(self, query: str) -> list[SearchHit]:
        api_key = get_web_search_api_key("brave")
        if not api_key:
            logger.warning("brave: BRAVE_API_KEY not set — returning [] (I9 opt-in)")
            return []
        try:
            params = {"q": query, "count": _COUNT}
            headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.get(
                    f"{_BRAVE_BASE_URL}/res/v1/web/search", params=params, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("brave: search failed for %r: %s — returning []", query, exc)
            return []

        web = data.get("web", {}) if isinstance(data, dict) else {}
        results = web.get("results", []) if isinstance(web, dict) else []
        hits: list[SearchHit] = []
        for item in results:
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
                    engine="brave",
                )
            )
        return hits
