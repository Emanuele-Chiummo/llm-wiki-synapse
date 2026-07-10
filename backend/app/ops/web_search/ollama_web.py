"""
Ollama-Web adapter — OPT-IN LOCAL web search (ADR-0070, OFF by default).

Uses the LOCAL Ollama endpoint's web-search route (``OLLAMA_URL`` env — the already-running
Ollama on the homelab, I9). Unlike the four cloud adapters this needs no cloud API key and the
queries stay on the local network, so ``is_cloud`` / ``requires_upload_warning`` are False — but
it is still OPT-IN (SearXNG remains the default). Unset ``OLLAMA_URL`` → no-op ([]).

Best-effort: on ANY failure return [] + WARNING, never raise (mirrors ADR-0069/MinerU).

Wire protocol (Ollama web search):
  POST {OLLAMA_URL}/api/web_search  JSON {"query"} → {"results":[{"url","title","content"}]}
⚠️ Implemented against the documented contract; MUST be validated against a live Ollama
web-search endpoint before it is relied upon.
"""

from __future__ import annotations

import logging
import os

import httpx

from .base import SearchHit, WebSearchProvider

logger = logging.getLogger(__name__)

_OLLAMA_URL_ENV = "OLLAMA_URL"
_TIMEOUT_S = 30.0
_MAX_RESULTS = 10


def _ollama_base() -> str:
    """Resolve the local Ollama base URL from OLLAMA_URL env ('' when unset)."""
    return os.environ.get(_OLLAMA_URL_ENV, "").rstrip("/")


class OllamaWebProvider(WebSearchProvider):
    """Ollama local web search (opt-in). Local — no cloud upload, no cloud key (I9)."""

    name = "ollama_web"
    is_cloud = False
    requires_upload_warning = False

    def configured(self) -> bool:
        """True iff OLLAMA_URL is set (local endpoint)."""
        return bool(_ollama_base())

    async def _search_one(self, query: str) -> list[SearchHit]:
        base = _ollama_base()
        if not base:
            logger.warning("ollama_web: OLLAMA_URL not set — returning [] (I9 opt-in)")
            return []
        try:
            body = {"query": query, "max_results": _MAX_RESULTS}
            async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
                resp = await client.post(f"{base}/api/web_search", json=body)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ollama_web: search failed for %r: %s — returning []", query, exc)
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
                    snippet=item.get("content") or item.get("snippet"),
                    engine="ollama_web",
                )
            )
        return hits
