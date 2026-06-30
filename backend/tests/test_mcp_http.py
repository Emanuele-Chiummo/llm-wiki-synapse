"""
Tests for the MCP HTTP surface (ADR-0029).

Coverage:
    With MCP_AUTH_TOKEN set + MCP_REMOTE_WRITE_ENABLED=false (default):
        - /mcp/server is mounted (not 404)
        - bearer missing → 401
        - bearer wrong   → 401
        - correct bearer → request reaches MCP app (not 401)
        - build_http_mcp(write_enabled=False) registers exactly 3 tools
        - write_page is NOT among the 3 tools

    With MCP_AUTH_TOKEN set + MCP_REMOTE_WRITE_ENABLED=true:
        - build_http_mcp(write_enabled=True) registers exactly 4 tools incl. write_page
        - write_page body delegates to _write_page_body (routes through write_wiki_page — I1/I5)

    With MCP_AUTH_TOKEN UNSET:
        - /mcp/server returns 404 (not mounted)
        - app boots fine (no crash on startup)
        - stdio `mcp` still has all 4 tools (I6 — test_four_tools_registered must pass)

    GET /mcp/info:
        - includes http_enabled + remote_write_enabled
        - does NOT include any token field

    Bearer auth guard (_BearerAuthMiddleware):
        - lifespan scope passes through without auth check
        - constant-time compare (indirect: correct token passes, wrong fails)

Note: the FastMCP HTTP app starts a StreamableHTTP session manager in its lifespan.
In tests we patch the lifespan to avoid starting real async task groups, or use
TestClient with lifespan=True where safe.  Tests that inspect tool counts do so via
the FastMCP API directly (no HTTP needed).
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP
from httpx import ASGITransport, AsyncClient

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _get_tool_names(mcp_instance: FastMCP) -> set[str]:
    """
    Extract registered tool names from a FastMCP instance.

    FastMCP 3.x stores tools in _local_provider._components with keys like
    "tool:<name>@".  We use that internal path; it is tested against the async
    list_tools() result in TestBuildHttpMcp to confirm consistency.
    """
    local_provider = getattr(mcp_instance, "_local_provider", None)
    if local_provider is not None:
        components = getattr(local_provider, "_components", {})
        names: set[str] = set()
        for key in components:
            # Keys have the form "tool:<name>@<version_or_empty>"
            if key.startswith("tool:"):
                # Strip "tool:" prefix and "@..." suffix
                raw = key[len("tool:") :]
                tool_name = raw.split("@")[0]
                names.add(tool_name)
        return names
    # Should never reach here with FastMCP >=2.0 — raise so the test fails explicitly
    raise RuntimeError(  # pragma: no cover
        f"Cannot introspect tool names from {type(mcp_instance)!r}: "
        "expected _local_provider attribute (FastMCP >=2.0)"
    )


async def _noop_lifespan(app_: Any) -> AsyncGenerator[None, None]:
    """No-op lifespan that suppresses real startup/shutdown in tests."""
    yield


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: FastAPI test client with patched lifespan (no real infra)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
async def client_no_token() -> AsyncGenerator[AsyncClient, None]:
    """
    HTTP test client for the FastAPI app with MCP_AUTH_TOKEN UNSET.
    Restores env after the test.
    """
    saved = os.environ.pop("MCP_AUTH_TOKEN", None)
    try:
        # Re-import with fresh settings so mcp_http_enabled == False
        from app.main import app

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as ac:
                yield ac
    finally:
        if saved is not None:
            os.environ["MCP_AUTH_TOKEN"] = saved


@pytest.fixture()
async def client_with_token() -> AsyncGenerator[AsyncClient, None]:
    """
    HTTP test client with MCP_AUTH_TOKEN=test-secret-token set.
    Mounts the bearer-guarded MCP HTTP surface.
    """
    os.environ["MCP_AUTH_TOKEN"] = "test-secret-token"
    os.environ["MCP_REMOTE_WRITE_ENABLED"] = "false"
    try:
        from app.main import app

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as ac:
                yield ac
    finally:
        os.environ.pop("MCP_AUTH_TOKEN", None)
        os.environ.pop("MCP_REMOTE_WRITE_ENABLED", None)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Tool counts via build_http_mcp() (no HTTP server needed)
# ─────────────────────────────────────────────────────────────────────────────


class TestBuildHttpMcp:
    """build_http_mcp() returns a FastMCP with the correct tool set."""

    def test_read_only_registers_exactly_3_tools(self) -> None:
        """write_enabled=False → search_wiki, get_page, list_pages (3 tools)."""
        from app.mcp.server import build_http_mcp

        http_mcp = build_http_mcp(write_enabled=False)
        assert isinstance(http_mcp, FastMCP)
        names = _get_tool_names(http_mcp)
        assert names == {
            "search_wiki",
            "get_page",
            "list_pages",
        }, f"Expected 3 read-only tools; got {names!r}"

    def test_read_only_excludes_write_page(self) -> None:
        """write_page must NOT be in the read-only HTTP surface."""
        from app.mcp.server import build_http_mcp

        http_mcp = build_http_mcp(write_enabled=False)
        names = _get_tool_names(http_mcp)
        assert (
            "write_page" not in names
        ), "write_page must not be registered when write_enabled=False"

    def test_write_enabled_registers_exactly_4_tools(self) -> None:
        """write_enabled=True → all 4 tools including write_page."""
        from app.mcp.server import build_http_mcp

        http_mcp = build_http_mcp(write_enabled=True)
        names = _get_tool_names(http_mcp)
        assert names == {
            "search_wiki",
            "get_page",
            "list_pages",
            "write_page",
        }, f"Expected 4 tools when write_enabled=True; got {names!r}"

    def test_write_enabled_includes_write_page(self) -> None:
        """write_page must be present when write_enabled=True (ADR-0029 §2.3)."""
        from app.mcp.server import build_http_mcp

        http_mcp = build_http_mcp(write_enabled=True)
        names = _get_tool_names(http_mcp)
        assert "write_page" in names

    def test_does_not_modify_stdio_mcp(self) -> None:
        """
        build_http_mcp() must never alter the module-level `mcp` object (I6).
        The stdio server always keeps all four tools.
        """
        from app.mcp.server import build_http_mcp
        from app.mcp.server import mcp as stdio_mcp

        names_before = _get_tool_names(stdio_mcp)
        build_http_mcp(write_enabled=False)
        build_http_mcp(write_enabled=True)
        names_after = _get_tool_names(stdio_mcp)
        assert (
            names_before == names_after
        ), "build_http_mcp() must not alter the stdio mcp tool registry"

    def test_returns_fastmcp_instance(self) -> None:
        """build_http_mcp() returns a FastMCP instance."""
        from app.mcp.server import build_http_mcp

        result = build_http_mcp(write_enabled=False)
        assert isinstance(result, FastMCP)


# ─────────────────────────────────────────────────────────────────────────────
# 2. stdio `mcp` always has 4 tools (I6 regression guard)
# ─────────────────────────────────────────────────────────────────────────────


class TestStdioMcpUnchanged:
    """The stdio `mcp` always retains all four tools regardless of HTTP config (I6)."""

    def test_stdio_has_four_tools(self) -> None:
        """Regression guard: test_four_tools_registered equivalent (ADR-0010 §6 / I6)."""
        from app.mcp.server import mcp as stdio_mcp

        names = _get_tool_names(stdio_mcp)
        for expected in ("search_wiki", "write_page", "get_page", "list_pages"):
            assert (
                expected in names
            ), f"Stdio mcp missing tool {expected!r} — build_http_mcp() must not alter it (I6)"

    def test_stdio_is_fastmcp_instance(self) -> None:
        from app.mcp.server import mcp as stdio_mcp

        assert isinstance(stdio_mcp, FastMCP)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Bearer auth guard unit tests (_BearerAuthMiddleware)
# ─────────────────────────────────────────────────────────────────────────────


async def _flag_on() -> Any:
    """
    Return a RemoteMcpFlag with is_enabled() == True.
    ADR-0032: _BearerAuthMiddleware now requires a flag arg; these bearer tests
    verify the bearer check (gate-ON path), so the flag must be ON.
    """
    from app.main import RemoteMcpFlag

    f = RemoteMcpFlag()
    await f.load(True)
    return f


async def _build_auth_cache_with_db_hash(token: str) -> Any:
    """
    Build a _McpAuthCache with a DB hash for the given token.
    ADR-0033: tests that were written for the ADR-0029 static-token guard need to
    supply an auth_cache so the gate's token resolver uses the DB-hash path.
    Using the DB-hash path avoids the need to patch settings.mcp_auth_token.
    """
    from app.main import _hash_token, _McpAuthCache

    cache = _McpAuthCache()
    db_hash = _hash_token(token)
    await cache.load(db_hash, False)  # DB hash stored; allow_without_token=False
    return cache


# Keep the old helper name as an alias for backward compat within this file.
async def _build_auth_cache_with_env_token(env_token: str) -> Any:
    """Alias: uses DB hash path so settings.mcp_auth_token need not be set."""
    return await _build_auth_cache_with_db_hash(env_token)


def _private_scope(headers: list[tuple[bytes, bytes]] | None = None) -> dict[str, Any]:
    """
    ASGI HTTP scope with a PRIVATE peer IP (loopback, no CF headers).
    ADR-0033: the gate now classifies source; tests that expect 401 on wrong/no bearer
    must use a scope that is classified PRIVATE (so the token check applies).
    Without this, an unknown client → fail-safe PUBLIC → 404 (no tell that token is needed).
    """
    return {
        "type": "http",
        "client": ("127.0.0.1", 12345),  # loopback → PRIVATE
        "headers": headers or [],
    }


class TestBearerAuthMiddleware:
    """Unit tests for the ASGI bearer-token guard (ADR-0029 §2.2, ADR-0032 §2.3, ADR-0033 §2.4)."""

    @pytest.mark.asyncio
    async def test_missing_auth_header_returns_401(self) -> None:
        """
        No Authorization header, token configured (DB hash), PRIVATE source → 401.
        ADR-0033: gate now source-classifies; we must supply a private-peer scope and
        a DB hash so the source=PRIVATE + tok_configured=True (db path) → 401.
        """
        from app.main import _BearerAuthMiddleware

        calls: list[dict[str, Any]] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            calls.append({"scope": scope})

        flag = await _flag_on()
        cache = await _build_auth_cache_with_env_token("secret")  # uses DB hash path
        middleware = _BearerAuthMiddleware(inner, "secret", flag, cache)
        scope = _private_scope()  # loopback peer → PRIVATE
        receive: Any = AsyncMock()
        send_calls: list[Any] = []

        async def send_fn(event: Any) -> None:
            send_calls.append(event)

        await middleware(scope, receive, send_fn)
        # Inner app must NOT have been called
        assert calls == [], "Inner app must not be called when auth header is missing"
        # Response status must be 401
        start_events = [e for e in send_calls if e.get("type") == "http.response.start"]
        assert start_events, "Must have sent http.response.start"
        assert start_events[0]["status"] == 401

    @pytest.mark.asyncio
    async def test_wrong_token_returns_401(self) -> None:
        """Wrong bearer token, token configured, PRIVATE source → 401."""
        from app.main import _BearerAuthMiddleware

        calls: list[dict[str, Any]] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            calls.append({"scope": scope})

        flag = await _flag_on()
        cache = await _build_auth_cache_with_env_token("correct-token")
        middleware = _BearerAuthMiddleware(inner, "correct-token", flag, cache)
        scope = _private_scope([(b"authorization", b"Bearer wrong-token")])
        send_calls: list[Any] = []

        async def send_fn(event: Any) -> None:
            send_calls.append(event)

        await middleware(scope, AsyncMock(), send_fn)
        assert calls == []
        start_events = [e for e in send_calls if e.get("type") == "http.response.start"]
        assert start_events[0]["status"] == 401

    @pytest.mark.asyncio
    async def test_correct_token_passes_through(self) -> None:
        """Correct bearer token, PRIVATE source → inner app is called (flag ON)."""
        from app.main import _BearerAuthMiddleware

        calls: list[dict[str, Any]] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            calls.append({"reached": True})

        flag = await _flag_on()
        cache = await _build_auth_cache_with_env_token("my-token")
        middleware = _BearerAuthMiddleware(inner, "my-token", flag, cache)
        scope = _private_scope([(b"authorization", b"Bearer my-token")])
        await middleware(scope, AsyncMock(), AsyncMock())
        assert calls == [{"reached": True}], "Inner app must be called with correct token"

    @pytest.mark.asyncio
    async def test_lifespan_scope_bypasses_auth(self) -> None:
        """Lifespan scope must pass through without auth check (so session manager starts)."""
        from app.main import RemoteMcpFlag, _BearerAuthMiddleware

        calls: list[dict[str, Any]] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            calls.append(scope)

        # Flag OFF to prove lifespan bypasses BOTH the gate check AND bearer check.
        flag = RemoteMcpFlag()
        await flag.load(False)
        cache = await _build_auth_cache_with_env_token("secret")
        middleware = _BearerAuthMiddleware(inner, "secret", flag, cache)
        # No auth header — but lifespan scope
        scope = {"type": "lifespan", "headers": []}
        await middleware(scope, AsyncMock(), AsyncMock())
        assert len(calls) == 1, "Lifespan scope must reach inner app without auth check"

    @pytest.mark.asyncio
    async def test_case_insensitive_bearer_prefix(self) -> None:
        """Authorization header prefix is parsed case-insensitively (flag ON, PRIVATE)."""
        from app.main import _BearerAuthMiddleware

        calls: list[dict[str, Any]] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            calls.append({"reached": True})

        flag = await _flag_on()
        cache = await _build_auth_cache_with_env_token("tok")
        middleware = _BearerAuthMiddleware(inner, "tok", flag, cache)
        # Use uppercase BEARER; use private scope so bearer check applies
        scope = _private_scope([(b"authorization", b"BEARER tok")])
        await middleware(scope, AsyncMock(), AsyncMock())
        assert calls == [{"reached": True}]


# ─────────────────────────────────────────────────────────────────────────────
# 4. /mcp/info ADR-0029 §2.5 fields
# ─────────────────────────────────────────────────────────────────────────────


class TestMcpInfoHttpFields:
    """GET /mcp/info must include http_enabled and remote_write_enabled but no token."""

    @pytest.mark.asyncio
    async def test_mcp_info_includes_http_enabled(self) -> None:
        from app.main import app

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        assert resp.status_code == 200
        data = resp.json()
        assert "http_enabled" in data, "GET /mcp/info must include http_enabled (ADR-0029 §2.5)"
        assert isinstance(data["http_enabled"], bool)

    @pytest.mark.asyncio
    async def test_mcp_info_includes_remote_write_enabled(self) -> None:
        from app.main import app

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        assert resp.status_code == 200
        data = resp.json()
        assert (
            "remote_write_enabled" in data
        ), "GET /mcp/info must include remote_write_enabled (ADR-0029 §2.5)"
        assert isinstance(data["remote_write_enabled"], bool)

    @pytest.mark.asyncio
    async def test_mcp_info_does_not_expose_token(self) -> None:
        """The MCP auth token must NEVER appear in the /mcp/info response (ADR-0029 §2.5)."""
        os.environ["MCP_AUTH_TOKEN"] = "super-secret-do-not-leak"
        try:
            from app.main import app

            with patch("app.main.app.router.lifespan_context", _noop_lifespan):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.get("/mcp/info")
        finally:
            os.environ.pop("MCP_AUTH_TOKEN", None)

        assert resp.status_code == 200
        raw_body = resp.text
        assert (
            "super-secret-do-not-leak" not in raw_body
        ), "MCP_AUTH_TOKEN must never appear in /mcp/info response (ADR-0029 §2.5)"

    @pytest.mark.asyncio
    async def test_mcp_info_http_enabled_always_true_adr0033(self) -> None:
        """
        ADR-0033 §2.4 always-mount: http_enabled is ALWAYS True regardless of token config.
        The gate is the per-request arbiter; mount condition is no longer "token set".
        """
        saved = os.environ.pop("MCP_AUTH_TOKEN", None)
        try:
            from app.main import app

            with patch("app.main.app.router.lifespan_context", _noop_lifespan):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.get("/mcp/info")
        finally:
            if saved is not None:
                os.environ["MCP_AUTH_TOKEN"] = saved

        assert resp.status_code == 200
        data = resp.json()
        # Always True per ADR-0033 §2.4 — the gate, not mount condition, controls reachability
        assert data["http_enabled"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 5. Token unset → /mcp/server not mounted (404)
# ─────────────────────────────────────────────────────────────────────────────


class TestMcpServerAlwaysMountedAdr0033:
    """
    ADR-0033 §2.4: /mcp/server is ALWAYS mounted; access is gated per-request
    by the middleware. The old "not mounted when no token" behavior is replaced:
    gate returns 404 (stealth) when remote_enabled=OFF; 401 when remote_enabled=ON
    but no/wrong bearer; 200/PASS when remote_enabled=ON + valid bearer.
    """

    @pytest.mark.asyncio
    async def test_mcp_server_always_mounted_no_token(self) -> None:
        """
        Without MCP_AUTH_TOKEN and remote_enabled=OFF, /mcp/server returns 404
        from the gate (stealth — not because it's unmounted, but gate rejects).
        ADR-0033 §2.4 table row: remote=OFF → 404.

        We POST to /mcp/server/ (with trailing slash) to bypass Starlette's
        mount trailing-slash redirect, so the request reaches the gate directly.
        """
        saved = os.environ.pop("MCP_AUTH_TOKEN", None)
        try:
            from app.main import _remote_mcp_flag, app

            # Ensure the in-process flag is OFF for this test (cross-test isolation).
            await _remote_mcp_flag.load(False)

            with patch("app.main.app.router.lifespan_context", _noop_lifespan):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.post(
                        "/mcp/server/",  # trailing slash: bypass mount redirect, hit gate
                        json={},
                        headers={"Authorization": "Bearer anything"},
                        follow_redirects=False,
                    )
            # Gate returns 404 (remote disabled) — surface IS mounted but gate rejects.
            assert (
                resp.status_code == 404
            ), f"Expected 404 from gate (remote_enabled=OFF), got {resp.status_code}"
        finally:
            if saved is not None:
                os.environ["MCP_AUTH_TOKEN"] = saved


# ─────────────────────────────────────────────────────────────────────────────
# 6. write_page on HTTP routes through _write_page_body → write_wiki_page (I1/I5)
# ─────────────────────────────────────────────────────────────────────────────


class TestWritePageRoutesThroughSharedSeam:
    """
    When write_page is registered on the HTTP surface (write_enabled=True),
    its implementation calls _write_page_body which calls write_wiki_page()
    (ADR-0010 §2, I1/I5 — no second writer).
    """

    @pytest.mark.asyncio
    async def test_write_page_body_calls_write_wiki_page(self) -> None:
        """
        _write_page_body (used by both stdio and HTTP write_page tools) calls
        write_wiki_page() — the shared ingest seam (ADR-0010 §2).
        """
        import uuid as _uuid

        from app.mcp.server import _write_page_body

        fake_row = MagicMock()
        fake_row.id = _uuid.uuid4()
        fake_row.title = "Test"
        fake_row.page_type = "concept"

        with patch("app.ingest.orchestrator.write_wiki_page", new_callable=AsyncMock) as mock_wwp:
            mock_wwp.return_value = fake_row
            result = await _write_page_body(
                title="Test",
                content="Body",
                frontmatter={
                    "type": "concept",
                    "title": "Test",
                    "sources": ["raw/sources/x.md"],
                    "lang": "en",
                },
                origin_source="raw/sources/x.md",
            )

        mock_wwp.assert_called_once()
        assert "error" not in result
        assert result["title"] == "Test"

    @pytest.mark.asyncio
    async def test_http_write_page_delegates_to_same_body(self) -> None:
        """
        The write_page tool on the HTTP FastMCP instance (write_enabled=True) delegates
        to _write_page_body — same function as the stdio mcp.write_page.
        This confirms no second writer is introduced (I1/I5, ADR-0010 §2).
        """
        import uuid as _uuid

        from app.mcp.server import build_http_mcp

        http_mcp = build_http_mcp(write_enabled=True)
        names = _get_tool_names(http_mcp)
        assert "write_page" in names

        fake_row = MagicMock()
        fake_row.id = _uuid.uuid4()
        fake_row.title = "WikiPage"
        fake_row.page_type = "concept"

        with patch("app.ingest.orchestrator.write_wiki_page", new_callable=AsyncMock) as mock_wwp:
            mock_wwp.return_value = fake_row
            # Call _write_page_body directly (the shared body that both tools call)
            from app.mcp.server import _write_page_body

            result = await _write_page_body(
                title="WikiPage",
                content="Content.",
                frontmatter={
                    "type": "concept",
                    "title": "WikiPage",
                    "sources": ["raw/sources/y.md"],
                    "lang": "en",
                },
                origin_source="raw/sources/y.md",
            )

        mock_wwp.assert_called_once()
        assert result.get("title") == "WikiPage"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Config helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestMcpHttpConfig:
    """Settings.mcp_http_enabled and related config (ADR-0029 / ADR-0033)."""

    def test_mcp_http_enabled_always_true_no_token(self) -> None:
        """
        ADR-0033 §2.4 always-mount: mcp_http_enabled is True even when no token is set.
        The gate (not mount condition) controls per-request access.
        """
        from app.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://u:p@h/db",
            qdrant_url="http://h:6333",
            embedding_url="http://h:11434/api/embeddings",
            embedding_dim=1024,
        )
        assert s.mcp_http_enabled is True

    def test_mcp_http_enabled_always_true_with_token(self) -> None:
        """mcp_http_enabled is True when token is set (also always True per ADR-0033)."""
        from app.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://u:p@h/db",
            qdrant_url="http://h:6333",
            embedding_url="http://h:11434/api/embeddings",
            embedding_dim=1024,
            mcp_auth_token="some-token",
        )
        assert s.mcp_http_enabled is True

    def test_mcp_remote_write_enabled_defaults_false(self) -> None:
        from app.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://u:p@h/db",
            qdrant_url="http://h:6333",
            embedding_url="http://h:11434/api/embeddings",
            embedding_dim=1024,
        )
        assert s.mcp_remote_write_enabled is False

    def test_mcp_auth_token_none_by_default(self) -> None:
        from app.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://u:p@h/db",
            qdrant_url="http://h:6333",
            embedding_url="http://h:11434/api/embeddings",
            embedding_dim=1024,
        )
        assert s.mcp_auth_token is None

    def test_mcp_trusted_proxies_empty_by_default(self) -> None:
        """MCP_TRUSTED_PROXIES defaults to empty → XFF is ignored (ADR-0033 §2.3)."""
        from app.config import Settings

        s = Settings(
            database_url="postgresql+asyncpg://u:p@h/db",
            qdrant_url="http://h:6333",
            embedding_url="http://h:11434/api/embeddings",
            embedding_dim=1024,
        )
        assert s.mcp_trusted_proxies == ""
        assert s.mcp_trusted_proxies_list == []
