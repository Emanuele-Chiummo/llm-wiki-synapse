"""
Tests for GET/PUT /web-search/config — SearXNG runtime configuration (ADR-0041).

Acceptance checks per ADR-0041:
  TC-WS-01  GET /web-search/config — source='none' (no DB, no env).
  TC-WS-02  GET /web-search/config — source='env' (SEARXNG_URL env set, no DB).
  TC-WS-03  GET /web-search/config — source='db' (DB URL set; env also present, DB wins).
  TC-WS-04  PUT /web-search/config set_url — persists URL, cache refreshed, source='db'.
  TC-WS-05  PUT /web-search/config set_categories — persists comma-separated list.
  TC-WS-06  PUT /web-search/config set_max_queries — persists integer, cache reflects it.
  TC-WS-07  PUT /web-search/config clear=true — nulls all three DB columns → env fallback.
  TC-WS-08  PUT /web-search/config set_url invalid scheme → 422.
  TC-WS-09  PUT /web-search/config set_max_queries=0 (< 1) → 422.
  TC-WS-10  PUT /web-search/config set_max_queries=51 (> 50) → 422.
  TC-WS-11  GET /web-search/config response shape — all required fields present.
  TC-WS-12  URL IS returned by GET (not a secret — unlike clip token).
  TC-WS-13  _WebSearchConfigCache unit — url_source resolution (db/env/none).
  TC-WS-14  _WebSearchConfigCache unit — categories resolution (db/default).
  TC-WS-15  _WebSearchConfigCache unit — max_queries resolution (db/env).
  TC-WS-16  deep-research POST /research/start 503 when DB=None and env=None.
  TC-WS-17  deep-research POST /research/start 202 when DB URL is set (DB over env).
  TC-WS-18  I9 guard — static scan: no Tavily/serpapi/duckduckgo/google-search import in ops/.

Uses no-op lifespan + monkeypatching; no live Postgres, Qdrant, or SearXNG needed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _make_client() -> AsyncClient:
    """Build an AsyncClient against the FastAPI app with a no-op lifespan."""
    from app.main import app
    from fastapi import FastAPI

    @asynccontextmanager
    async def _test_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield

    app.router.lifespan_context = _test_lifespan
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _make_vault_state_row(
    searxng_url_db: str | None = None,
    searxng_categories_db: str | None = None,
    searxng_max_queries_db: int | None = None,
) -> MagicMock:
    """Return a mock VaultState row with ADR-0041 columns."""
    row = MagicMock()
    row.vault_id = "test-vault"
    row.data_version = 0
    row.remote_mcp_enabled = False
    row.mcp_access_token_hash = None
    row.mcp_allow_without_token = False
    row.clip_enabled_db = None
    row.clip_access_token = None
    row.clip_allowed_origins_db = None
    row.searxng_url_db = searxng_url_db
    row.searxng_categories_db = searxng_categories_db
    row.searxng_max_queries_db = searxng_max_queries_db
    row.updated_at = None
    return row


def _make_db_session_mock(vault_state_row: Any) -> MagicMock:
    """Build a mock async context manager for get_session() → execute → scalar_one_or_none."""
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = vault_state_row

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    return mock_ctx


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-01: GET /web-search/config — source='none'
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_web_search_config_source_none() -> None:
    """TC-WS-01: No DB URL, no env URL → source='none', configured=False, url=null."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )
    try:
        cfg.settings.searxng_url = None
        await main_mod._web_search_config_cache.load(None, None, None)
        async with await _make_client() as client:
            resp = await client.get("/web-search/config")
    finally:
        cfg.settings.searxng_url = original_url
        await main_mod._web_search_config_cache.load(*original_cache)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "none"
    assert body["configured"] is False
    assert body["url"] is None


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-02: GET /web-search/config — source='env'
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_web_search_config_source_env() -> None:
    """TC-WS-02: No DB URL, SEARXNG_URL env set → source='env', configured=True, url=env URL."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )
    env_url = "http://searxng-env:8080"
    try:
        cfg.settings.searxng_url = env_url
        await main_mod._web_search_config_cache.load(None, None, None)
        async with await _make_client() as client:
            resp = await client.get("/web-search/config")
    finally:
        cfg.settings.searxng_url = original_url
        await main_mod._web_search_config_cache.load(*original_cache)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "env"
    assert body["configured"] is True
    assert body["url"] == env_url


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-03: GET /web-search/config — source='db' (DB wins over env)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_web_search_config_source_db() -> None:
    """TC-WS-03: DB URL set AND env URL set → source='db', url=DB URL (DB wins)."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )
    db_url = "http://searxng-db:9090"
    env_url = "http://searxng-env:8080"
    try:
        cfg.settings.searxng_url = env_url
        await main_mod._web_search_config_cache.load(db_url, None, None)
        async with await _make_client() as client:
            resp = await client.get("/web-search/config")
    finally:
        cfg.settings.searxng_url = original_url
        await main_mod._web_search_config_cache.load(*original_cache)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "db"
    assert body["configured"] is True
    assert body["url"] == db_url, f"DB URL must win over env URL, got: {body['url']!r}"
    assert body["url"] != env_url, "Env URL must NOT override DB URL"


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-04: PUT /web-search/config set_url
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_web_search_config_set_url() -> None:
    """TC-WS-04: set_url persists URL to DB, cache refreshed, source='db'."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )

    new_url = "http://searxng-new:8888"
    state_row = _make_vault_state_row()
    mock_session = _make_db_session_mock(state_row)

    try:
        cfg.settings.searxng_url = None
        await main_mod._web_search_config_cache.load(None, None, None)

        with patch("app.main.get_session", return_value=mock_session):
            async with await _make_client() as client:
                resp = await client.put("/web-search/config", json={"set_url": new_url})
    finally:
        cfg.settings.searxng_url = original_url
        await main_mod._web_search_config_cache.load(*original_cache)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source"] == "db"
    assert body["configured"] is True
    assert body["url"] == new_url


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-05: PUT /web-search/config set_categories
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_web_search_config_set_categories() -> None:
    """TC-WS-05: set_categories persists comma-separated list; resolved_categories() splits."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )

    base_url = "http://searxng-cats:8080"
    state_row = _make_vault_state_row(searxng_url_db=base_url)
    mock_session = _make_db_session_mock(state_row)

    try:
        cfg.settings.searxng_url = None
        await main_mod._web_search_config_cache.load(base_url, None, None)

        with patch("app.main.get_session", return_value=mock_session):
            async with await _make_client() as client:
                resp = await client.put(
                    "/web-search/config",
                    json={"set_categories": "general, news, science"},
                )
    finally:
        cfg.settings.searxng_url = original_url
        await main_mod._web_search_config_cache.load(*original_cache)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Categories are split by comma and stripped
    assert isinstance(body["categories"], list)
    assert "general" in body["categories"]
    assert "news" in body["categories"]
    assert "science" in body["categories"]


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-06: PUT /web-search/config set_max_queries
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_web_search_config_set_max_queries() -> None:
    """TC-WS-06: set_max_queries persists integer; max_queries reflected post-write."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )

    base_url = "http://searxng-mq:8080"
    state_row = _make_vault_state_row(searxng_url_db=base_url)
    mock_session = _make_db_session_mock(state_row)

    try:
        cfg.settings.searxng_url = None
        await main_mod._web_search_config_cache.load(base_url, None, None)

        with patch("app.main.get_session", return_value=mock_session):
            async with await _make_client() as client:
                resp = await client.put("/web-search/config", json={"set_max_queries": 12})
    finally:
        cfg.settings.searxng_url = original_url
        await main_mod._web_search_config_cache.load(*original_cache)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["max_queries"] == 12


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-07: PUT /web-search/config clear=true
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_web_search_config_clear() -> None:
    """TC-WS-07: clear=true nulls all three DB columns; falls back to env/defaults."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )

    # Pre-populate cache as if DB had values
    db_url = "http://searxng-clear:8080"
    state_row = _make_vault_state_row(
        searxng_url_db=db_url,
        searxng_categories_db="general,news",
        searxng_max_queries_db=7,
    )
    mock_session = _make_db_session_mock(state_row)

    env_url = "http://searxng-env-fallback:8080"
    try:
        cfg.settings.searxng_url = env_url
        await main_mod._web_search_config_cache.load(db_url, "general,news", 7)

        with patch("app.main.get_session", return_value=mock_session):
            async with await _make_client() as client:
                resp = await client.put("/web-search/config", json={"clear": True})
    finally:
        cfg.settings.searxng_url = original_url
        await main_mod._web_search_config_cache.load(*original_cache)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # After clear, DB columns are NULL → falls back to env URL
    assert body["source"] == "env", f"Expected 'env' after clear, got {body['source']!r}"
    assert body["url"] == env_url
    assert body["configured"] is True


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-08: PUT /web-search/config set_url invalid scheme → 422
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_web_search_config_invalid_url_scheme() -> None:
    """TC-WS-08: set_url with non-http(s) scheme → 422."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )

    # URL validation happens inside async with get_session(), so we need a mock session.
    state_row = _make_vault_state_row()
    mock_session = _make_db_session_mock(state_row)

    try:
        cfg.settings.searxng_url = None
        await main_mod._web_search_config_cache.load(None, None, None)
        with patch("app.main.get_session", return_value=mock_session):
            async with await _make_client() as client:
                resp = await client.put(
                    "/web-search/config", json={"set_url": "ftp://invalid-scheme"}
                )
    finally:
        cfg.settings.searxng_url = original_url
        await main_mod._web_search_config_cache.load(*original_cache)

    assert (
        resp.status_code == 422
    ), f"Expected 422 for invalid URL scheme, got {resp.status_code}: {resp.text}"


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-09: PUT /web-search/config set_max_queries=0 → 422
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_web_search_config_max_queries_below_min() -> None:
    """TC-WS-09: set_max_queries=0 (< 1) → 422 (ge=1 constraint)."""
    import app.main as main_mod

    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )
    try:
        async with await _make_client() as client:
            resp = await client.put("/web-search/config", json={"set_max_queries": 0})
    finally:
        await main_mod._web_search_config_cache.load(*original_cache)

    assert (
        resp.status_code == 422
    ), f"Expected 422 for set_max_queries=0, got {resp.status_code}: {resp.text}"


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-10: PUT /web-search/config set_max_queries=51 → 422
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_web_search_config_max_queries_above_max() -> None:
    """TC-WS-10: set_max_queries=51 (> 50) → 422 (le=50 constraint)."""
    import app.main as main_mod

    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )
    try:
        async with await _make_client() as client:
            resp = await client.put("/web-search/config", json={"set_max_queries": 51})
    finally:
        await main_mod._web_search_config_cache.load(*original_cache)

    assert (
        resp.status_code == 422
    ), f"Expected 422 for set_max_queries=51, got {resp.status_code}: {resp.text}"


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-11: GET /web-search/config response shape
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_web_search_config_response_shape() -> None:
    """TC-WS-11: GET /web-search/config returns all required fields with correct types."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )
    try:
        cfg.settings.searxng_url = "http://searxng-shape:8080"
        await main_mod._web_search_config_cache.load(None, None, None)
        async with await _make_client() as client:
            resp = await client.get("/web-search/config")
    finally:
        cfg.settings.searxng_url = original_url
        await main_mod._web_search_config_cache.load(*original_cache)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    required_fields = {"configured", "url", "categories", "max_queries", "source"}
    missing = required_fields - set(body.keys())
    assert not missing, f"Missing fields in GET /web-search/config response: {missing}"
    assert isinstance(body["configured"], bool)
    assert isinstance(body["categories"], list)
    assert isinstance(body["max_queries"], int)
    assert isinstance(body["source"], str)
    assert body["source"] in ("db", "env", "none"), f"Unexpected source: {body['source']!r}"


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-12: URL IS returned (not a secret, unlike clip token)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_web_search_config_url_is_returned_not_masked() -> None:
    """TC-WS-12: SearXNG URL IS returned in full by GET (NOT a secret — ADR-0041 §2.1)."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )
    db_url = "http://searxng-sentinel-NOT-A-SECRET:9999"
    try:
        cfg.settings.searxng_url = None
        await main_mod._web_search_config_cache.load(db_url, None, None)
        async with await _make_client() as client:
            resp = await client.get("/web-search/config")
    finally:
        cfg.settings.searxng_url = original_url
        await main_mod._web_search_config_cache.load(*original_cache)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # The URL must appear verbatim in the response (it IS public — ADR-0041 §2.1)
    assert body["url"] == db_url, (
        f"SearXNG URL must be returned verbatim by GET /web-search/config, " f"got: {body['url']!r}"
    )
    assert (
        db_url in resp.text
    ), "SearXNG URL value must appear in the response text — it is NOT a secret (ADR-0041 §2.1)"


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-13/14/15: _WebSearchConfigCache unit tests
# ─────────────────────────────────────────────────────────────────────────────


class TestWebSearchConfigCacheResolution:
    """Unit tests for _WebSearchConfigCache resolution (ADR-0041 §2.2)."""

    @pytest.mark.asyncio
    async def test_url_source_db(self) -> None:
        """TC-WS-13a: DB url_db set → url_source='db'."""
        import app.main as main_mod
        from app import config as cfg

        original = cfg.settings.searxng_url
        try:
            cfg.settings.searxng_url = "http://env-url:8080"
            cache = main_mod._WebSearchConfigCache()
            await cache.load("http://db-url:9090", None, None)
            assert cache.url_source() == "db"
            assert cache.resolved_url() == "http://db-url:9090"
            assert cache.configured() is True
        finally:
            cfg.settings.searxng_url = original

    @pytest.mark.asyncio
    async def test_url_source_env(self) -> None:
        """TC-WS-13b: No DB url_db, SEARXNG_URL env set → url_source='env'."""
        import app.main as main_mod
        from app import config as cfg

        original = cfg.settings.searxng_url
        try:
            cfg.settings.searxng_url = "http://env-only:8080"
            cache = main_mod._WebSearchConfigCache()
            await cache.load(None, None, None)
            assert cache.url_source() == "env"
            assert cache.resolved_url() == "http://env-only:8080"
            assert cache.configured() is True
        finally:
            cfg.settings.searxng_url = original

    @pytest.mark.asyncio
    async def test_url_source_none(self) -> None:
        """TC-WS-13c: No DB url_db, no env → url_source='none', configured=False."""
        import app.main as main_mod
        from app import config as cfg

        original = cfg.settings.searxng_url
        try:
            cfg.settings.searxng_url = None
            cache = main_mod._WebSearchConfigCache()
            await cache.load(None, None, None)
            assert cache.url_source() == "none"
            assert cache.resolved_url() is None
            assert cache.configured() is False
        finally:
            cfg.settings.searxng_url = original

    @pytest.mark.asyncio
    async def test_db_url_wins_over_env(self) -> None:
        """TC-WS-13d: DB url_db takes precedence over env (ADR-0041 §2.2)."""
        import app.main as main_mod
        from app import config as cfg

        original = cfg.settings.searxng_url
        try:
            cfg.settings.searxng_url = "http://env-url:8080"
            cache = main_mod._WebSearchConfigCache()
            await cache.load("http://db-wins:9999", None, None)
            assert cache.url_source() == "db"
            assert cache.resolved_url() == "http://db-wins:9999"
            assert cache.resolved_url() != cfg.settings.searxng_url
        finally:
            cfg.settings.searxng_url = original

    @pytest.mark.asyncio
    async def test_categories_source_db(self) -> None:
        """TC-WS-14a: DB categories_db set → categories_source='db'; split by comma."""
        import app.main as main_mod

        cache = main_mod._WebSearchConfigCache()
        await cache.load(None, "general, news, science", None)
        assert cache.categories_source() == "db"
        cats = cache.resolved_categories()
        assert "general" in cats
        assert "news" in cats
        assert "science" in cats

    @pytest.mark.asyncio
    async def test_categories_source_default(self) -> None:
        """TC-WS-14b: No DB categories → categories_source='default'; resolved_categories=[]."""
        import app.main as main_mod

        cache = main_mod._WebSearchConfigCache()
        await cache.load(None, None, None)
        assert cache.categories_source() == "default"
        assert cache.resolved_categories() == []

    @pytest.mark.asyncio
    async def test_max_queries_source_db(self) -> None:
        """TC-WS-15a: DB max_queries_db set → max_queries_source='db'."""
        import app.main as main_mod

        cache = main_mod._WebSearchConfigCache()
        await cache.load(None, None, 20)
        assert cache.max_queries_source() == "db"
        assert cache.resolved_max_queries() == 20

    @pytest.mark.asyncio
    async def test_max_queries_source_env(self) -> None:
        """TC-WS-15b: No DB max_queries → max_queries_source='env'; resolves from settings."""
        import app.main as main_mod
        from app import config as cfg

        cache = main_mod._WebSearchConfigCache()
        await cache.load(None, None, None)
        assert cache.max_queries_source() == "env"
        assert cache.resolved_max_queries() == cfg.settings.deep_research_max_queries

    @pytest.mark.asyncio
    async def test_set_url_db_updates_cache(self) -> None:
        """set_url_db() atomically updates cached URL (used after DB write)."""
        import app.main as main_mod
        from app import config as cfg

        original = cfg.settings.searxng_url
        try:
            cfg.settings.searxng_url = None
            cache = main_mod._WebSearchConfigCache()
            await cache.load(None, None, None)
            assert cache.url_source() == "none"
            await cache.set_url_db("http://new-db-url:7777")
            assert cache.url_source() == "db"
            assert cache.resolved_url() == "http://new-db-url:7777"
        finally:
            cfg.settings.searxng_url = original

    @pytest.mark.asyncio
    async def test_set_categories_db_updates_cache(self) -> None:
        """set_categories_db() atomically updates cached categories."""
        import app.main as main_mod

        cache = main_mod._WebSearchConfigCache()
        await cache.load(None, None, None)
        assert cache.categories_source() == "default"
        await cache.set_categories_db("it, tech")
        assert cache.categories_source() == "db"
        assert cache.resolved_categories() == ["it", "tech"]

    @pytest.mark.asyncio
    async def test_set_max_queries_db_updates_cache(self) -> None:
        """set_max_queries_db() atomically updates cached max_queries."""
        import app.main as main_mod

        cache = main_mod._WebSearchConfigCache()
        await cache.load(None, None, None)
        await cache.set_max_queries_db(30)
        assert cache.max_queries_source() == "db"
        assert cache.resolved_max_queries() == 30


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-16/17: deep-research 503/202 honoring DB vs env
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_research_start_503_when_neither_db_nor_env_configured() -> None:
    """TC-WS-16: POST /research/start → 503 when DB=None and env=None (ADR-0041)."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )
    try:
        cfg.settings.searxng_url = None
        await main_mod._web_search_config_cache.load(None, None, None)
        async with await _make_client() as client:
            resp = await client.post(
                "/research/start",
                json={"vault_id": "test-vault", "topic": "test topic"},
            )
    finally:
        cfg.settings.searxng_url = original_url
        await main_mod._web_search_config_cache.load(*original_cache)

    assert (
        resp.status_code == 503
    ), f"Expected 503 when no SearXNG configured, got {resp.status_code}: {resp.text}"
    detail = resp.json().get("detail", "")
    assert (
        "SEARXNG_URL" in detail or "searxng" in detail.lower()
    ), f"503 detail should mention SEARXNG_URL, got: {detail!r}"


@pytest.mark.asyncio
async def test_research_start_202_when_db_url_set() -> None:
    """TC-WS-17: POST /research/start → 202 when DB URL is set (env=None; DB wins — ADR-0041)."""
    import app.main as main_mod
    from app import config as cfg

    original_url = cfg.settings.searxng_url
    original_cache = (
        main_mod._web_search_config_cache._url_db,
        main_mod._web_search_config_cache._categories_db,
        main_mod._web_search_config_cache._max_queries_db,
    )

    db_url = "http://searxng-db-research:8080"

    # We need to mock the background task so it doesn't actually run deep research.
    with patch("app.main.asyncio.create_task"):
        # Also mock get_session for the DB write in research_start
        state_row = MagicMock()
        state_row.vault_id = "test-vault"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # no existing run
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        try:
            cfg.settings.searxng_url = None  # env disabled
            await main_mod._web_search_config_cache.load(db_url, None, None)  # DB URL set

            with patch("app.main.get_session", return_value=mock_ctx):
                async with await _make_client() as client:
                    resp = await client.post(
                        "/research/start",
                        json={"vault_id": "test-vault", "topic": "DB URL test topic"},
                    )
        finally:
            cfg.settings.searxng_url = original_url
            await main_mod._web_search_config_cache.load(*original_cache)

    assert resp.status_code == 202, (
        f"Expected 202 when DB URL is set (DB wins over unset env), "
        f"got {resp.status_code}: {resp.text}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# TC-WS-18: I9 guard — static scan for forbidden web-search imports
# ─────────────────────────────────────────────────────────────────────────────


def test_i9_no_non_searxng_provider_imports() -> None:
    """TC-WS-18: I9 — no Tavily/serpapi/duckduckgo/google-search import in ops/.

    SearXNG is the ONLY web-search backend (I9, ADR-0041). This static guard
    ensures no forbidden alternative search provider has been imported anywhere
    in the ops/ directory. Mirrors the I6 guard pattern.
    """
    import ast

    ops_dir = Path(__file__).parent.parent / "app" / "ops"
    assert ops_dir.exists(), f"ops/ directory not found at {ops_dir}"

    forbidden_patterns = {
        "tavily",
        "serpapi",
        "duckduckgo",
        "google_search",
        "google-search",
        "googlesearch",
        "brave_search",
        "bing_search",
    }

    violations: list[str] = []

    for py_file in sorted(ops_dir.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        # 1. Raw text scan (catches string literals, comments, etc.)
        source_lower = source.lower()
        for pattern in forbidden_patterns:
            if pattern.lower() in source_lower:
                violations.append(
                    f"{py_file.relative_to(ops_dir.parent.parent)}: "
                    f"forbidden pattern {pattern!r} found (raw scan)"
                )

        # 2. AST scan (catches actual import statements)
        try:
            tree = ast.parse(source, filename=str(py_file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                else:
                    names = [node.module or ""]
                for name in names:
                    if name and any(p in name.lower() for p in forbidden_patterns):
                        violations.append(
                            f"{py_file.relative_to(ops_dir.parent.parent)}: "
                            f"forbidden import {name!r} (AST scan)"
                        )

    assert not violations, (
        "I9 VIOLATION: Non-SearXNG web-search providers found in ops/. "
        "SearXNG is the ONLY web-search backend (I9, ADR-0041).\n" + "\n".join(violations)
    )
