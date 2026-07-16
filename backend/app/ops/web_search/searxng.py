"""
SearXNG adapter — the DEFAULT, bundled, privacy-preserving web-search backend (ADR-0070).

This is a THIN wrapper around the pre-existing ``ops/searxng.py`` module (ADR-0024/ADR-0041):
``search_many`` delegates verbatim to ``searxng_search_many`` and ``_search_one`` to
``searxng_search``. SearXNG behaviour, URL resolution (DB-over-env, ADR-0041) and every
existing SearXNG test are therefore untouched — this seam is a pure refactor for SearXNG.

configured(): mirrors the runtime SearXNG posture — DB ``vault_state.searxng_url_db`` wins
over ``SEARXNG_URL`` env (ADR-0041 §2.2). The import of the main.py cache is deferred + guarded
exactly like ``ops.searxng._resolve_searxng_url`` to avoid a circular import in unit tests.
"""

from __future__ import annotations

from app.config import settings
from app.ops.searxng import searxng_search, searxng_search_many

from .base import SearchHit, WebSearchProvider


class SearxngProvider(WebSearchProvider):
    """SearXNG backend (default). Local/self-hosted — no cloud upload, no API key (I9)."""

    name = "searxng"
    is_cloud = False
    requires_upload_warning = False

    def configured(self) -> bool:
        """
        True iff a NON-EMPTY SearXNG URL is resolvable (DB override wins over SEARXNG_URL env,
        ADR-0041 §2.2). Empty string counts as unconfigured (matches the pre-seam guards).
        """
        try:
            from app.runtime_state import (
                web_search_config_cache as _web_search_config_cache,
            )  # noqa: PLC0415

            url = _web_search_config_cache.resolved_url()
        except (ImportError, AttributeError):
            url = settings.searxng_url
        return bool(url)

    async def _search_one(self, query: str) -> list[SearchHit]:
        """Delegate one query to the existing SearXNG client (ADR-0041)."""
        return await searxng_search(query)

    async def search_many(self, queries: list[str]) -> list[SearchHit]:
        """Delegate verbatim to ``searxng_search_many`` (bounded + URL-deduped already)."""
        return await searxng_search_many(queries)
