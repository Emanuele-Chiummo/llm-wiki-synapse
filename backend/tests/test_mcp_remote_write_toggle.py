"""
Tests for the remote MCP write-tools runtime toggle (ADR-0072).

Coverage:
    1. _mcp_write_flag default is False (fresh singleton).
    2. GET /mcp/info.remote_write_enabled reflects _mcp_write_flag (not the env var).
    3. PUT /mcp/remote-write {enabled:true} with token configured → remote_write_enabled=true,
       clamped=false; _mcp_write_flag flipped.
    4. PUT /mcp/remote-write {enabled:true} with NO token → clamped=true,
       remote_write_enabled=false (token-floor clamp, ADR-0072 §4).
    5. PUT /mcp/remote-write {enabled:false} always succeeds even without a token.
    6. Guard returns error dict when flag is OFF (write_page, resolve_review,
       trigger_source_rescan each return {"error": "..."}).
    7. write_page delegates to _write_page_body when flag is ON (no error dict).
    8. Response shape: remote_write_enabled (bool), token_configured (bool), clamped (bool).
    9. Persistence: flag set ON via PUT, vault_state column updated.
   10. _load_mcp_write_flag DB-wins-else-env precedence (non-NULL DB overrides env).
   11. _load_mcp_write_flag falls back to env when DB value is NULL.

Test patterns:
    - ASGITransport + AsyncClient (mirrors test_mcp_remote_toggle.py).
    - Patched lifespan (_noop_lifespan) to avoid real infra.
    - DB interactions tested via mocking get_session / VaultState.
    - Guard tests use build_http_mcp(write_enabled_getter=...) directly (no HTTP layer needed).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


async def _noop_lifespan(app_: Any) -> AsyncGenerator[None, None]:
    """No-op lifespan — suppresses real startup/shutdown in tests."""
    yield


def _make_vault_state_row(
    remote_mcp_write_enabled: bool | None = None,
    mcp_access_token_hash: str | None = None,
    mcp_allow_without_token: bool = False,
) -> MagicMock:
    """Return a mock VaultState row with the given field values."""
    row = MagicMock()
    row.remote_mcp_write_enabled = remote_mcp_write_enabled
    row.mcp_access_token_hash = mcp_access_token_hash
    row.mcp_allow_without_token = mcp_allow_without_token
    row.data_version = 0
    row.vault_id = "test"
    row.updated_at = None
    return row


def _make_db_session_mock(vault_state_row: Any) -> MagicMock:
    """
    Build a mock async context manager for get_session() that returns a session
    whose execute().scalar_one_or_none() returns vault_state_row.
    """
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
# 1. Default OFF
# ─────────────────────────────────────────────────────────────────────────────


class TestWriteFlagDefault:
    """_mcp_write_flag starts as False (ADR-0072 §2)."""

    @pytest.mark.asyncio
    async def test_mcp_write_flag_default_is_false(self) -> None:
        """RemoteMcpFlag() for write starts as False (fail-closed, ADR-0072 §2)."""
        from app.main import RemoteMcpFlag

        flag = RemoteMcpFlag()
        assert flag.is_enabled() is False

    @pytest.mark.asyncio
    async def test_mcp_info_remote_write_enabled_reflects_flag(self) -> None:
        """GET /mcp/info.remote_write_enabled reflects _mcp_write_flag, not env (ADR-0072 §5)."""
        from app.main import _mcp_write_flag, app

        # Explicitly force flag OFF (in case env MCP_REMOTE_WRITE_ENABLED is set somewhere)
        await _mcp_write_flag.load(False)

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        assert resp.status_code == 200
        data = resp.json()
        assert (
            data["remote_write_enabled"] is False
        ), "remote_write_enabled must reflect _mcp_write_flag (ADR-0072 §5)"

    @pytest.mark.asyncio
    async def test_mcp_info_remote_write_enabled_true_when_flag_on(self) -> None:
        """GET /mcp/info.remote_write_enabled=true when _mcp_write_flag is ON."""
        from app.main import _mcp_write_flag, app

        await _mcp_write_flag.load(True)
        try:
            with patch("app.main.app.router.lifespan_context", _noop_lifespan):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.get("/mcp/info")
        finally:
            await _mcp_write_flag.load(False)

        assert resp.status_code == 200
        assert resp.json()["remote_write_enabled"] is True


# ─────────────────────────────────────────────────────────────────────────────
# 3 & 4 & 5. PUT /mcp/remote-write — token-floor clamp
# ─────────────────────────────────────────────────────────────────────────────


class TestPutMcpRemoteWrite:
    """PUT /mcp/remote-write handler: token-floor clamp + persistence (ADR-0072 §4)."""

    @pytest.mark.asyncio
    async def test_put_enabled_true_with_token_sets_write_enabled(self) -> None:
        """enabled=true + token set → remote_write_enabled=true, clamped=false (ADR-0072 §4)."""
        import app.main as main_mod
        from app.main import _mcp_write_flag, app

        await _mcp_write_flag.load(False)

        vault_state_row = _make_vault_state_row(remote_mcp_write_enabled=False)
        db_ctx = _make_db_session_mock(vault_state_row)

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = "test-secret-token"  # type: ignore[assignment]
        try:
            with (
                patch("app.main.app.router.lifespan_context", _noop_lifespan),
                patch("app.main.get_session", return_value=db_ctx),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.put("/mcp/remote-write", json={"enabled": True})
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]
            await _mcp_write_flag.load(False)

        assert resp.status_code == 200
        data = resp.json()
        assert data["remote_write_enabled"] is True
        assert data["clamped"] is False
        assert data["token_configured"] is True

    @pytest.mark.asyncio
    async def test_put_enabled_true_without_token_is_clamped(self) -> None:
        """enabled=true + NO token → clamped=true, remote_write_enabled=false (ADR-0072 §4)."""
        import app.main as main_mod
        from app.main import _mcp_write_flag, app

        await _mcp_write_flag.load(False)

        vault_state_row = _make_vault_state_row(remote_mcp_write_enabled=False)
        db_ctx = _make_db_session_mock(vault_state_row)

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = None  # type: ignore[assignment]
        try:
            with (
                patch("app.main.app.router.lifespan_context", _noop_lifespan),
                patch("app.main.get_session", return_value=db_ctx),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.put("/mcp/remote-write", json={"enabled": True})
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert resp.status_code == 200
        data = resp.json()
        assert data["remote_write_enabled"] is False, "Clamped: must persist false, not true"
        assert data["clamped"] is True, "clamped must be True when no token and no allow"
        assert data["token_configured"] is False

    @pytest.mark.asyncio
    async def test_put_enabled_false_always_succeeds(self) -> None:
        """enabled=false always succeeds, even without a token (ADR-0072 §4)."""
        import app.main as main_mod
        from app.main import _mcp_write_flag, app

        await _mcp_write_flag.load(True)

        vault_state_row = _make_vault_state_row(remote_mcp_write_enabled=True)
        db_ctx = _make_db_session_mock(vault_state_row)

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = None  # type: ignore[assignment]
        try:
            with (
                patch("app.main.app.router.lifespan_context", _noop_lifespan),
                patch("app.main.get_session", return_value=db_ctx),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.put("/mcp/remote-write", json={"enabled": False})
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]
            await _mcp_write_flag.load(False)

        assert resp.status_code == 200
        data = resp.json()
        assert data["remote_write_enabled"] is False
        assert data["clamped"] is False

    @pytest.mark.asyncio
    async def test_put_refreshes_in_memory_flag(self) -> None:
        """PUT /mcp/remote-write refreshes _mcp_write_flag immediately (ADR-0072 §2)."""
        import app.main as main_mod
        from app.main import _mcp_write_flag, app

        await _mcp_write_flag.load(False)

        vault_state_row = _make_vault_state_row(remote_mcp_write_enabled=False)
        db_ctx = _make_db_session_mock(vault_state_row)

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = "tok"  # type: ignore[assignment]
        try:
            with (
                patch("app.main.app.router.lifespan_context", _noop_lifespan),
                patch("app.main.get_session", return_value=db_ctx),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    await ac.put("/mcp/remote-write", json={"enabled": True})

            assert _mcp_write_flag.is_enabled() is True, (
                "_mcp_write_flag must be updated immediately after PUT /mcp/remote-write "
                "(ADR-0072 §2)"
            )
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]
            await _mcp_write_flag.load(False)

    @pytest.mark.asyncio
    async def test_put_response_shape(self) -> None:
        """PUT /mcp/remote-write response has remote_write_enabled, token_configured, clamped."""
        import app.main as main_mod
        from app.main import _mcp_write_flag, app

        await _mcp_write_flag.load(False)

        vault_state_row = _make_vault_state_row(remote_mcp_write_enabled=False)
        db_ctx = _make_db_session_mock(vault_state_row)

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = "tok"  # type: ignore[assignment]
        try:
            with (
                patch("app.main.app.router.lifespan_context", _noop_lifespan),
                patch("app.main.get_session", return_value=db_ctx),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.put("/mcp/remote-write", json={"enabled": False})
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("remote_write_enabled"), bool)
        assert isinstance(data.get("token_configured"), bool)
        assert isinstance(data.get("clamped"), bool)

    @pytest.mark.asyncio
    async def test_put_enabled_true_with_allow_without_token_not_clamped(self) -> None:
        """enabled=true + allow_without_token=true (no token) → not clamped (ADR-0072 §4)."""
        import app.main as main_mod
        from app.main import _mcp_auth_cache, _mcp_write_flag, app

        await _mcp_write_flag.load(False)
        # Simulate allow_without_token=true in the auth cache
        original_allow = _mcp_auth_cache.allow_without_token()
        await _mcp_auth_cache.set_allow(True)

        vault_state_row = _make_vault_state_row(remote_mcp_write_enabled=False)
        db_ctx = _make_db_session_mock(vault_state_row)

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = None  # type: ignore[assignment]
        try:
            with (
                patch("app.main.app.router.lifespan_context", _noop_lifespan),
                patch("app.main.get_session", return_value=db_ctx),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.put("/mcp/remote-write", json={"enabled": True})
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]
            await _mcp_auth_cache.set_allow(original_allow)
            await _mcp_write_flag.load(False)

        assert resp.status_code == 200
        data = resp.json()
        assert data["remote_write_enabled"] is True, "allow_without_token=true must bypass clamp"
        assert data["clamped"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 6 & 7. Guard: write tools return error dict when flag OFF, delegate when ON
# ─────────────────────────────────────────────────────────────────────────────


class TestWriteToolGuard:
    """write_page / resolve_review / trigger_source_rescan guard logic (ADR-0072 §3)."""

    @pytest.mark.asyncio
    async def test_write_page_returns_error_dict_when_flag_off(self) -> None:
        """
        write_page returns {"error": "..."} when write_enabled_getter() is False (ADR-0072 §3).
        The guard sits IN FRONT OF _write_page_body; the body is never called (I6/I9).
        """
        from app.mcp.server import build_http_mcp

        flag_value = False

        http_mcp = build_http_mcp(write_enabled_getter=lambda: flag_value)

        # Introspect: write_page must be registered (always-register model, ADR-0072 §3).
        tools = await http_mcp.list_tools()
        tool_names = {t.name for t in tools}
        assert (
            "write_page" in tool_names
        ), "write_page must be registered even when flag is OFF (ADR-0072 §3 always-register)"

        # Call the tool — should get the guard error, not reach _write_page_body.
        body_called = False

        async def _fake_write_page(*args: Any, **kwargs: Any) -> dict[str, Any]:
            nonlocal body_called
            body_called = True
            return {"id": "x", "title": "x", "type": "entity", "relevance_score": 0.0}

        with patch("app.mcp.server._write_page_body", _fake_write_page):
            http_mcp2 = build_http_mcp(write_enabled_getter=lambda: False)
            # Call via the registered tool directly (unwrap the decorated function).
            # We need to find the tool and call its underlying body:
            tool_map = {t.name: t for t in await http_mcp2.list_tools()}
            assert "write_page" in tool_map

            # Use call_tool to exercise the full tool body.
            result = await http_mcp2.call_tool(
                "write_page",
                {
                    "title": "Test",
                    "content": "body",
                    "frontmatter": {"type": "entity", "title": "Test", "sources": [], "lang": "en"},
                    "origin_source": "",
                },
            )

        # result is a list of TextContent objects from FastMCP.
        result_dict = result.structured_content or {}
        assert (
            "error" in result_dict
        ), "write_page must return {'error': '...'} when flag is OFF (ADR-0072 §3)"
        assert "remote writes are disabled" in result_dict["error"]
        assert not body_called, "_write_page_body must NOT be called when flag is OFF (I9)"

    @pytest.mark.asyncio
    async def test_resolve_review_returns_error_dict_when_flag_off(self) -> None:
        """resolve_review returns {"error": "..."} when flag is OFF (ADR-0072 §3)."""
        from app.mcp.server import build_http_mcp

        http_mcp = build_http_mcp(write_enabled_getter=lambda: False)

        tools = await http_mcp.list_tools()
        tool_names = {t.name for t in tools}
        assert "resolve_review" in tool_names, "resolve_review must be registered when getter used"

        result = await http_mcp.call_tool(
            "resolve_review",
            {"review_id": "00000000-0000-0000-0000-000000000001", "action": "skip"},
        )
        result_dict = result.structured_content or {}
        assert "error" in result_dict
        assert "remote writes are disabled" in result_dict["error"]

    @pytest.mark.asyncio
    async def test_trigger_source_rescan_returns_error_dict_when_flag_off(self) -> None:
        """trigger_source_rescan returns {"error": "..."} when flag is OFF (ADR-0072 §3)."""
        from app.mcp.server import build_http_mcp

        http_mcp = build_http_mcp(write_enabled_getter=lambda: False)

        tools = await http_mcp.list_tools()
        tool_names = {t.name for t in tools}
        assert (
            "trigger_source_rescan" in tool_names
        ), "trigger_source_rescan must be registered when getter used"

        result = await http_mcp.call_tool("trigger_source_rescan", {})
        result_dict = result.structured_content or {}
        assert "error" in result_dict
        assert "remote writes are disabled" in result_dict["error"]

    @pytest.mark.asyncio
    async def test_write_page_delegates_when_flag_on(self) -> None:
        """write_page delegates to _write_page_body when flag is ON (ADR-0072 §3)."""
        from app.mcp.server import build_http_mcp

        body_called = False

        async def _fake_write_page(
            title: str,
            content: str,
            frontmatter: dict[str, Any],
            origin_source: str = "",
            vault: str | None = None,
        ) -> dict[str, Any]:
            nonlocal body_called
            body_called = True
            return {"id": "abc", "title": title, "type": "entity", "relevance_score": 0.0}

        with patch("app.mcp.server._write_page_body", _fake_write_page):
            http_mcp = build_http_mcp(write_enabled_getter=lambda: True)
            result = await http_mcp.call_tool(
                "write_page",
                {
                    "title": "MyPage",
                    "content": "body",
                    "frontmatter": {
                        "type": "entity",
                        "title": "MyPage",
                        "sources": [],
                        "lang": "en",
                    },
                    "origin_source": "",
                },
            )

        result_dict = result.structured_content or {}
        assert body_called, "_write_page_body must be called when flag is ON"
        assert "error" not in result_dict, "No error dict when flag is ON"
        assert result_dict.get("title") == "MyPage"

    @pytest.mark.asyncio
    async def test_static_write_enabled_false_does_not_register_write_tools(self) -> None:
        """Legacy static path: write_enabled=False → write tools not registered (backward compat)."""
        from app.mcp.server import build_http_mcp

        http_mcp = build_http_mcp(write_enabled=False)
        tools = await http_mcp.list_tools()
        tool_names = {t.name for t in tools}
        assert (
            "write_page" not in tool_names
        ), "Static write_enabled=False must NOT register write tools (backward compat)"

    @pytest.mark.asyncio
    async def test_static_write_enabled_true_registers_write_tools(self) -> None:
        """Legacy static path: write_enabled=True → write tools registered (backward compat)."""
        from app.mcp.server import build_http_mcp

        http_mcp = build_http_mcp(write_enabled=True)
        tools = await http_mcp.list_tools()
        tool_names = {t.name for t in tools}
        assert "write_page" in tool_names, "Static write_enabled=True must register write_page"
        assert "resolve_review" in tool_names
        assert "trigger_source_rescan" in tool_names

    @pytest.mark.asyncio
    async def test_getter_mode_always_registers_write_tools_regardless_of_flag_value(self) -> None:
        """Getter model: write tools registered regardless of current flag value (ADR-0072 §3)."""
        from app.mcp.server import build_http_mcp

        # Even with getter returning False, all write tools must be registered.
        http_mcp = build_http_mcp(write_enabled_getter=lambda: False)
        tools = await http_mcp.list_tools()
        tool_names = {t.name for t in tools}
        assert "write_page" in tool_names, (
            "Getter model must always register write tools even when getter returns False "
            "(ADR-0072 §3 always-register)"
        )
        assert "resolve_review" in tool_names
        assert "trigger_source_rescan" in tool_names


# ─────────────────────────────────────────────────────────────────────────────
# 9. Persistence — flag set ON persists to vault_state column
# ─────────────────────────────────────────────────────────────────────────────


class TestWriteFlagPersistence:
    """Persistence: PUT /mcp/remote-write writes vault_state.remote_mcp_write_enabled (ADR-0072 §1)."""

    @pytest.mark.asyncio
    async def test_put_updates_vault_state_column(self) -> None:
        """PUT /mcp/remote-write sets vault_state.remote_mcp_write_enabled in DB."""
        import app.main as main_mod
        from app.main import _mcp_write_flag, app

        await _mcp_write_flag.load(False)

        vault_state_row = _make_vault_state_row(remote_mcp_write_enabled=False)
        db_ctx = _make_db_session_mock(vault_state_row)

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = "tok"  # type: ignore[assignment]
        try:
            with (
                patch("app.main.app.router.lifespan_context", _noop_lifespan),
                patch("app.main.get_session", return_value=db_ctx),
            ):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    await ac.put("/mcp/remote-write", json={"enabled": True})
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]
            await _mcp_write_flag.load(False)

        # The mock row should have been updated.
        assert (
            vault_state_row.remote_mcp_write_enabled is True
        ), "vault_state.remote_mcp_write_enabled must be set to True on PUT (ADR-0072 §1)"


# ─────────────────────────────────────────────────────────────────────────────
# 10 & 11. _load_mcp_write_flag: DB-wins-else-env precedence
# ─────────────────────────────────────────────────────────────────────────────


class TestLoadMcpWriteFlag:
    """_load_mcp_write_flag() DB-wins-else-env precedence (ADR-0072 §1/§2)."""

    @pytest.mark.asyncio
    async def test_load_uses_db_value_when_non_null(self) -> None:
        """DB non-NULL value wins over env bootstrap (ADR-0072 §1)."""
        import app.main as main_mod
        from app.main import _load_mcp_write_flag, _mcp_write_flag

        await _mcp_write_flag.load(False)

        # DB says True; env says False (default)
        vault_state_row = _make_vault_state_row(remote_mcp_write_enabled=True)
        db_ctx = _make_db_session_mock(vault_state_row)

        original = main_mod.settings.mcp_remote_write_enabled
        main_mod.settings.mcp_remote_write_enabled = False  # type: ignore[assignment]
        try:
            with patch("app.main.get_session", return_value=db_ctx):
                await _load_mcp_write_flag()
        finally:
            main_mod.settings.mcp_remote_write_enabled = original  # type: ignore[assignment]

        assert (
            _mcp_write_flag.is_enabled() is True
        ), "DB non-NULL value must win over env (ADR-0072 §1 DB-wins-else-env)"
        await _mcp_write_flag.load(False)

    @pytest.mark.asyncio
    async def test_load_falls_back_to_env_when_db_null(self) -> None:
        """DB NULL falls back to MCP_REMOTE_WRITE_ENABLED env (ADR-0072 §1)."""
        import app.main as main_mod
        from app.main import _load_mcp_write_flag, _mcp_write_flag

        await _mcp_write_flag.load(False)

        # DB says NULL; env says True
        vault_state_row = _make_vault_state_row(remote_mcp_write_enabled=None)
        db_ctx = _make_db_session_mock(vault_state_row)

        original = main_mod.settings.mcp_remote_write_enabled
        main_mod.settings.mcp_remote_write_enabled = True  # type: ignore[assignment]
        try:
            with patch("app.main.get_session", return_value=db_ctx):
                await _load_mcp_write_flag()
        finally:
            main_mod.settings.mcp_remote_write_enabled = original  # type: ignore[assignment]

        assert (
            _mcp_write_flag.is_enabled() is True
        ), "DB NULL must fall back to env MCP_REMOTE_WRITE_ENABLED (ADR-0072 §1)"
        await _mcp_write_flag.load(False)

    @pytest.mark.asyncio
    async def test_load_defaults_false_when_no_row(self) -> None:
        """Missing vault_state row falls back to env default (False by default)."""
        import app.main as main_mod
        from app.main import _load_mcp_write_flag, _mcp_write_flag

        await _mcp_write_flag.load(True)

        # No DB row; env is False (default)
        db_ctx = _make_db_session_mock(None)

        original = main_mod.settings.mcp_remote_write_enabled
        main_mod.settings.mcp_remote_write_enabled = False  # type: ignore[assignment]
        try:
            with patch("app.main.get_session", return_value=db_ctx):
                await _load_mcp_write_flag()
        finally:
            main_mod.settings.mcp_remote_write_enabled = original  # type: ignore[assignment]

        assert (
            _mcp_write_flag.is_enabled() is False
        ), "Missing vault_state row must fall back to env default (fail-closed)"
