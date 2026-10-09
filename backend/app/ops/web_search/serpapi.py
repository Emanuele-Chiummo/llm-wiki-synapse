"""
SerpApi adapter — OPT-IN cloud web search (ADR-0070, OFF by default).

⚠️ CLOUD (I9): sends every query to SerpApi. Secret key from ``settings.serpapi_api_key``
(env-only ``SERPAPI_API_KEY``; NEVER on the config-override surface, §2.4). Unset → no-op ([]).
Best-effort: on ANY failure return [] + WARNING, never raise (mirrors ADR-0069/MinerU).

Wire protocol (SerpApi, https://serpapi.com/search-api):
  GET {base}/search.json?engine=google&q=<q>&api_key=<key>
    → {"organic_results": [{"link", "title", "snippet"}]}
⚠️ Implemented against the documented contract; MUST be validated against a live SerpApi key.

⚠️ The key travels in the QUERY STRING — SerpApi takes no auth header, so this is the one
backend of the five whose credential is part of the request URL. That makes the failure log a
leak path: ``httpx.HTTPStatusError`` formats as "Client error '401 Unauthorized' for url
'<full url>'", so interpolating the exception into a log record writes the key to the log in
plaintext. Every ``str(exc)`` here goes through :func:`_redact_key` first (``keys.py``:
"Plaintext is NEVER logged").
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from app.ops.web_search.keys import get_web_search_api_key

from .base import SearchHit, WebSearchProvider

logger = logging.getLogger(__name__)

_SERPAPI_BASE_URL = "https://serpapi.com"
_TIMEOUT_S = 15.0

# Stand-in written to the log in place of the key.
_REDACTED = "***"


def _redact_key(text: str, api_key: str) -> str:
    """
    Return *text* with every occurrence of *api_key* replaced by ``***``.

    Both the raw key and its percent-encoded form are replaced: the key reaches the log via a
    URL that ``httpx`` built with ``params=``, so a key containing a URL-unsafe byte appears
    percent-encoded in the exception text and a raw-only replace would miss it. An empty key
    is never substituted (``str.replace("", …)`` would splice ``***`` between every character).
    """
    if not api_key:
        return text
    redacted = text.replace(api_key, _REDACTED)
    encoded = quote(api_key, safe="")
    if encoded != api_key:
        redacted = redacted.replace(encoded, _REDACTED)
    return redacted


class SerpApiProvider(WebSearchProvider):
    """SerpApi cloud search (opt-in). is_cloud/requires_upload_warning=True (I9)."""

    name = "serpapi"
    is_cloud = True
    requires_upload_warning = True

    def configured(self) -> bool:
        """True iff SERPAPI_API_KEY is set (env-only secret)."""
        return get_web_search_api_key("serpapi") is not None

    async def _search_one(self, query: str) -> list[SearchHit]:
        api_key = get_web_search_api_key("serpapi")
        if not api_key:
            logger.warning("serpapi: SERPAPI_API_KEY not set — returning [] (I9 opt-in)")
            return []
        try:
            params = {"engine": "google", "q": query, "api_key": api_key}
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.get(f"{_SERPAPI_BASE_URL}/search.json", params=params)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            # _redact_key, not str(exc): the key is in the request URL and httpx puts that
            # URL in the message of every HTTPStatusError (a 401 from a wrong key, a 429 from
            # a RATE-LIMITED VALID one) — so the unredacted form logs the credential.
            logger.warning(
                "serpapi: search failed for %r: %s — returning []",
                query,
                _redact_key(str(exc), api_key),
            )
            return []

        hits: list[SearchHit] = []
        for item in data.get("organic_results", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            url = item.get("link") or ""
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=item.get("title") or url,
                    snippet=item.get("snippet"),
                    engine="serpapi",
                )
            )
        return hits
