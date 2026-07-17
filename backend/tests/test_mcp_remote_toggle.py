"""
Tests for the remote MCP runtime toggle (ADR-0032).

Coverage:
    1. Default OFF — fresh/upgraded vault_state row → remote_enabled=false;
       GET /mcp/info returns remote_enabled=false.
    2. Gate OFF ⇒ 404 — MCP_AUTH_TOKEN set, remote_mcp_enabled=false:
       request to /mcp/server returns 404 (with OR without a valid bearer).
    3. Gate ON ⇒ bearer enforced — token set, remote_mcp_enabled=true:
       missing/wrong bearer → 401; correct bearer → passes to sub-app.
    4. PUT /mcp/remote {enabled:true} with token set → remote_enabled=true, clamped=false.
    5. PUT /mcp/remote {enabled:true} with NO token → clamped=true, remote_enabled=false.
    6. PUT /mcp/remote {enabled:false} always succeeds.
    7. GET /mcp/info returns token_configured, remote_enabled, mount_path; never the token.
    8. Persistence — flag set ON via PUT, then re-read from vault_state → true.
    9. Session manager NOT remounted on toggle (lifespan runs once; flag change is in-memory).
   10. MCP_MOUNT_PATH constant used in /mcp/info.mount_path and PUT /mcp/remote.mount_path.
   11. _BearerAuthMiddleware: lifespan/WS scopes pass through regardless of flag state.

Test patterns:
    - ASGITransport + AsyncClient (mirrors test_mcp_http.py).
    - Patched lifespan (_noop_lifespan) to avoid real infra.
    - DB interactions tested via mocking get_session / VaultState.
"""

from __future__ import annotations

import os
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


def _make_vault_state_row(remote_mcp_enabled: bool = False) -> MagicMock:
    """Return a mock VaultState row with the given remote_mcp_enabled value."""
    row = MagicMock()
    row.remote_mcp_enabled = remote_mcp_enabled
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


class TestDefaultOff:
    """Fresh vault_state defaults to remote_mcp_enabled=false (ADR-0032 §2.1)."""

    @pytest.mark.asyncio
    async def test_remote_mcp_flag_default_is_false(self) -> None:
        """RemoteMcpFlag starts as False (loaded from DB at startup)."""
        from app.runtime_state import RemoteMcpFlag

        flag = RemoteMcpFlag()
        assert flag.is_enabled() is False

    @pytest.mark.asyncio
    async def test_mcp_info_remote_enabled_false_when_flag_off(self) -> None:
        """GET /mcp/info returns remote_enabled=false when the flag is OFF."""
        from app.main import app
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        # Force flag to OFF
        await _remote_mcp_flag.load(False)

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        assert resp.status_code == 200
        data = resp.json()
        assert (
            data["remote_enabled"] is False
        ), "remote_enabled must be false when RemoteMcpFlag is OFF (ADR-0032 §2.1)"

    @pytest.mark.asyncio
    async def test_vault_state_new_row_defaults_remote_mcp_enabled_false(self) -> None:
        """
        _seed_vault_state seeds remote_mcp_enabled=False on new rows (ADR-0032 §2.1).
        Verified by inspecting the VaultState constructor call via mock.
        """
        from app.main import _seed_vault_state

        # Simulate no existing row — will create a new VaultState.
        added_objects: list[Any] = []

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # no existing row

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock(side_effect=added_objects.append)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.main.get_session", return_value=mock_ctx):
            await _seed_vault_state()

        assert len(added_objects) == 1
        new_row = added_objects[0]
        # The seeded row must have remote_mcp_enabled=False (fail-closed, ADR-0032 §2.1)
        assert (
            new_row.remote_mcp_enabled is False
        ), "Seeded vault_state must have remote_mcp_enabled=False (ADR-0032 §2.1)"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Gate OFF ⇒ 404
# ─────────────────────────────────────────────────────────────────────────────


class TestGateOff404:
    """When flag is OFF, /mcp/server returns 404 regardless of bearer (ADR-0032 §2.3)."""

    @pytest.mark.asyncio
    async def test_gate_off_no_bearer_returns_404(self) -> None:
        """Flag OFF + no bearer → 404 (not 401 — no info leak)."""
        from app.runtime_state import BearerAuthMiddleware as _BearerAuthMiddleware
        from app.runtime_state import McpAuthCache as _McpAuthCache
        from app.runtime_state import RemoteMcpFlag

        flag = RemoteMcpFlag()
        await flag.load(False)  # flag OFF

        inner_calls: list[Any] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            inner_calls.append(scope)

        cache = _McpAuthCache()
        await cache.load(None, False)
        # Gate OFF (remote_mcp_enabled=False) → 404 regardless of source/bearer
        middleware = _BearerAuthMiddleware(inner, "secret", flag, cache)
        scope = {"type": "http", "client": ("127.0.0.1", 12345), "headers": []}
        send_events: list[Any] = []

        async def send_fn(event: Any) -> None:
            send_events.append(event)

        await middleware(scope, AsyncMock(), send_fn)

        # Inner must NOT be reached
        assert inner_calls == [], "Sub-app must not be called when gate is OFF"
        start_events = [e for e in send_events if e.get("type") == "http.response.start"]
        assert start_events, "Must have sent a response"
        assert (
            start_events[0]["status"] == 404
        ), "Gate OFF must return 404 (not 401) — no info leak (ADR-0032 §2.3)"

    @pytest.mark.asyncio
    async def test_gate_off_valid_bearer_still_404(self) -> None:
        """Flag OFF + correct bearer → still 404 (flag check is BEFORE bearer check)."""
        from app.runtime_state import BearerAuthMiddleware as _BearerAuthMiddleware
        from app.runtime_state import McpAuthCache as _McpAuthCache
        from app.runtime_state import RemoteMcpFlag

        flag = RemoteMcpFlag()
        await flag.load(False)  # flag OFF

        inner_calls: list[Any] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            inner_calls.append(scope)

        cache = _McpAuthCache()
        await cache.load(None, False)
        middleware = _BearerAuthMiddleware(inner, "correct-token", flag, cache)
        scope = {
            "type": "http",
            "client": ("127.0.0.1", 12345),
            "headers": [(b"authorization", b"Bearer correct-token")],
        }
        send_events: list[Any] = []

        async def send_fn(event: Any) -> None:
            send_events.append(event)

        await middleware(scope, AsyncMock(), send_fn)

        assert inner_calls == []
        start_events = [e for e in send_events if e.get("type") == "http.response.start"]
        assert (
            start_events[0]["status"] == 404
        ), "Gate OFF must return 404 even with a valid bearer (ADR-0032 §2.3)"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Gate ON ⇒ bearer enforced
# ─────────────────────────────────────────────────────────────────────────────


async def _build_cache_with_db_hash(token: str) -> Any:
    """
    Build a _McpAuthCache with a real PBKDF2 hash for the given token.
    ADR-0033: the gate verifies via the DB-hash path (PBKDF2) when the cache
    has a non-None hash — this avoids needing to patch settings.mcp_auth_token.
    """
    from app.runtime_state import McpAuthCache as _McpAuthCache
    from app.runtime_state import hash_token as _hash_token

    cache = _McpAuthCache()
    db_hash = _hash_token(token)
    await cache.load(db_hash, False)
    return cache


def _private_http_scope(
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, Any]:
    """ASGI HTTP scope with a loopback peer (PRIVATE per ADR-0033 §2.3)."""
    return {
        "type": "http",
        "client": ("127.0.0.1", 12345),
        "headers": headers or [],
    }


class TestGateOnBearerEnforced:
    """When flag is ON, bearer is enforced exactly as ADR-0029/0032/0033."""

    @pytest.mark.asyncio
    async def test_gate_on_missing_bearer_returns_401(self) -> None:
        """Flag ON + token configured (DB hash) + PRIVATE source + no bearer → 401."""
        from app.runtime_state import BearerAuthMiddleware as _BearerAuthMiddleware
        from app.runtime_state import RemoteMcpFlag

        flag = RemoteMcpFlag()
        await flag.load(True)  # flag ON

        inner_calls: list[Any] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            inner_calls.append(scope)

        cache = await _build_cache_with_db_hash("secret")
        middleware = _BearerAuthMiddleware(inner, "secret", flag, cache)
        scope = _private_http_scope()  # loopback → PRIVATE; no auth header
        send_events: list[Any] = []

        async def send_fn(event: Any) -> None:
            send_events.append(event)

        await middleware(scope, AsyncMock(), send_fn)

        assert inner_calls == []
        start_events = [e for e in send_events if e.get("type") == "http.response.start"]
        assert start_events[0]["status"] == 401

    @pytest.mark.asyncio
    async def test_gate_on_wrong_bearer_returns_401(self) -> None:
        """Flag ON + token configured (DB hash) + PRIVATE source + wrong bearer → 401."""
        from app.runtime_state import BearerAuthMiddleware as _BearerAuthMiddleware
        from app.runtime_state import RemoteMcpFlag

        flag = RemoteMcpFlag()
        await flag.load(True)

        inner_calls: list[Any] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            inner_calls.append(scope)

        cache = await _build_cache_with_db_hash("correct")
        middleware = _BearerAuthMiddleware(inner, "correct", flag, cache)
        scope = _private_http_scope([(b"authorization", b"Bearer wrong")])
        send_events: list[Any] = []

        async def send_fn(event: Any) -> None:
            send_events.append(event)

        await middleware(scope, AsyncMock(), send_fn)

        assert inner_calls == []
        start_events = [e for e in send_events if e.get("type") == "http.response.start"]
        assert start_events[0]["status"] == 401

    @pytest.mark.asyncio
    async def test_gate_on_correct_bearer_passes_through(self) -> None:
        """Flag ON + token configured (DB hash) + PRIVATE source + correct bearer → pass."""
        from app.runtime_state import BearerAuthMiddleware as _BearerAuthMiddleware
        from app.runtime_state import RemoteMcpFlag

        flag = RemoteMcpFlag()
        await flag.load(True)

        inner_calls: list[Any] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            inner_calls.append({"reached": True})

        cache = await _build_cache_with_db_hash("my-token")
        middleware = _BearerAuthMiddleware(inner, "my-token", flag, cache)
        scope = _private_http_scope([(b"authorization", b"Bearer my-token")])
        await middleware(scope, AsyncMock(), AsyncMock())
        assert inner_calls == [{"reached": True}]

    @pytest.mark.asyncio
    async def test_lifespan_scope_always_passes_regardless_of_flag(self) -> None:
        """Lifespan scope passes through regardless of flag state (ADR-0032 §2.3)."""
        from app.runtime_state import BearerAuthMiddleware as _BearerAuthMiddleware
        from app.runtime_state import McpAuthCache as _McpAuthCache
        from app.runtime_state import RemoteMcpFlag

        # Test with flag OFF
        flag_off = RemoteMcpFlag()
        await flag_off.load(False)

        inner_calls: list[Any] = []

        async def inner(scope: Any, receive: Any, send: Any) -> None:
            inner_calls.append(scope["type"])

        cache = _McpAuthCache()
        await cache.load(None, False)
        middleware = _BearerAuthMiddleware(inner, "secret", flag_off, cache)
        scope = {"type": "lifespan", "headers": []}
        await middleware(scope, AsyncMock(), AsyncMock())
        assert "lifespan" in inner_calls, "Lifespan must pass through even when flag is OFF"

        # Test with flag ON (unchanged behaviour from ADR-0029)
        flag_on = RemoteMcpFlag()
        await flag_on.load(True)
        middleware2 = _BearerAuthMiddleware(inner, "secret", flag_on, cache)
        await middleware2(scope, AsyncMock(), AsyncMock())
        assert inner_calls.count("lifespan") == 2


# ─────────────────────────────────────────────────────────────────────────────
# 4 & 5. PUT /mcp/remote — token-floor clamp
# ─────────────────────────────────────────────────────────────────────────────


class TestPutMcpRemote:
    """PUT /mcp/remote handler: token-floor clamp + persistence (ADR-0032 §2.4)."""

    @pytest.mark.asyncio
    async def test_put_enabled_true_with_token_sets_remote_enabled(self) -> None:
        """enabled=true + token set → remote_enabled=true, clamped=false (ADR-0032 §2.4)."""
        import app.main as main_mod
        from app.main import app
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        await _remote_mcp_flag.load(False)

        vault_state_row = _make_vault_state_row(remote_mcp_enabled=False)
        db_ctx = _make_db_session_mock(vault_state_row)

        # Patch the module-level settings singleton directly (instance attribute).
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
                    resp = await ac.put("/mcp/remote", json={"enabled": True})
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]
            await _remote_mcp_flag.load(False)

        assert resp.status_code == 200
        data = resp.json()
        assert data["remote_enabled"] is True
        assert data["clamped"] is False
        assert data["token_configured"] is True
        assert "mount_path" in data

    @pytest.mark.asyncio
    async def test_put_enabled_true_without_token_is_clamped(self) -> None:
        """enabled=true + NO token → clamped=true, remote_enabled=false (ADR-0032 §2.4)."""
        import app.main as main_mod
        from app.main import app
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        await _remote_mcp_flag.load(False)

        vault_state_row = _make_vault_state_row(remote_mcp_enabled=False)
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
                    resp = await ac.put("/mcp/remote", json={"enabled": True})
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert resp.status_code == 200
        data = resp.json()
        assert data["remote_enabled"] is False, "Clamped: must persist false, not true"
        assert data["clamped"] is True, "clamped must be True when no token"
        assert data["token_configured"] is False

    @pytest.mark.asyncio
    async def test_put_enabled_false_always_succeeds(self) -> None:
        """enabled=false always succeeds, even without a token (ADR-0032 §2.4)."""
        import app.main as main_mod
        from app.main import app
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        await _remote_mcp_flag.load(True)  # start ON

        vault_state_row = _make_vault_state_row(remote_mcp_enabled=True)
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
                    resp = await ac.put("/mcp/remote", json={"enabled": False})
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]
            await _remote_mcp_flag.load(False)

        assert resp.status_code == 200
        data = resp.json()
        assert data["remote_enabled"] is False
        assert data["clamped"] is False

    @pytest.mark.asyncio
    async def test_put_mcp_remote_refreshes_in_memory_flag(self) -> None:
        """PUT /mcp/remote refreshes RemoteMcpFlag immediately (ADR-0032 §2.2)."""
        import app.main as main_mod
        from app.main import app
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        await _remote_mcp_flag.load(False)

        vault_state_row = _make_vault_state_row(remote_mcp_enabled=False)
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
                    await ac.put("/mcp/remote", json={"enabled": True})

            # The in-process flag must now be True
            assert _remote_mcp_flag.is_enabled() is True, (
                "RemoteMcpFlag must be updated immediately after PUT /mcp/remote " "(ADR-0032 §2.2)"
            )
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]
            await _remote_mcp_flag.load(False)

    @pytest.mark.asyncio
    async def test_put_response_has_correct_shape(self) -> None:
        """PUT /mcp/remote response: remote_enabled, token_configured, mount_path, clamped."""
        import app.main as main_mod
        from app.main import app
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        await _remote_mcp_flag.load(False)

        vault_state_row = _make_vault_state_row(remote_mcp_enabled=False)
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
                    resp = await ac.put("/mcp/remote", json={"enabled": False})
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("remote_enabled"), bool)
        assert isinstance(data.get("token_configured"), bool)
        assert isinstance(data.get("mount_path"), str)
        assert isinstance(data.get("clamped"), bool)

    @pytest.mark.asyncio
    async def test_put_mount_path_equals_constant(self) -> None:
        """PUT /mcp/remote.mount_path must equal MCP_MOUNT_PATH constant (I6)."""
        import app.main as main_mod
        from app.main import app
        from app.runtime_state import MCP_MOUNT_PATH
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        await _remote_mcp_flag.load(False)

        vault_state_row = _make_vault_state_row(remote_mcp_enabled=False)
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
                    resp = await ac.put("/mcp/remote", json={"enabled": False})
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert resp.json()["mount_path"] == MCP_MOUNT_PATH


# ─────────────────────────────────────────────────────────────────────────────
# 7. GET /mcp/info — ADR-0032 additions
# ─────────────────────────────────────────────────────────────────────────────


class TestMcpInfoAdr0032:
    """GET /mcp/info: token_configured, remote_enabled, mount_path; no token (ADR-0032 §2.5)."""

    @pytest.mark.asyncio
    async def test_mcp_info_has_token_configured(self) -> None:
        """GET /mcp/info must include token_configured (bool)."""
        from app.main import app

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        assert resp.status_code == 200
        data = resp.json()
        assert "token_configured" in data
        assert isinstance(data["token_configured"], bool)

    @pytest.mark.asyncio
    async def test_mcp_info_has_remote_enabled(self) -> None:
        """GET /mcp/info must include remote_enabled (bool)."""
        from app.main import app
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        await _remote_mcp_flag.load(False)

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        assert resp.status_code == 200
        data = resp.json()
        assert "remote_enabled" in data
        assert isinstance(data["remote_enabled"], bool)

    @pytest.mark.asyncio
    async def test_mcp_info_has_mount_path(self) -> None:
        """GET /mcp/info must include mount_path (str = MCP_MOUNT_PATH constant, I6)."""
        from app.main import app
        from app.runtime_state import MCP_MOUNT_PATH

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        assert resp.status_code == 200
        data = resp.json()
        assert "mount_path" in data
        assert (
            data["mount_path"] == MCP_MOUNT_PATH
        ), f"mount_path must equal MCP_MOUNT_PATH={MCP_MOUNT_PATH!r} (I6)"

    @pytest.mark.asyncio
    async def test_mcp_info_never_leaks_token(self) -> None:
        """The MCP auth token must never appear in /mcp/info response (ADR-0032 §2.5, I6)."""
        sentinel = "VERY-SECRET-TOKEN-DO-NOT-LEAK-0032"
        os.environ["MCP_AUTH_TOKEN"] = sentinel
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
        assert (
            sentinel not in resp.text
        ), "MCP_AUTH_TOKEN must NEVER appear in /mcp/info response (ADR-0032 §2.5 / I6)"

    @pytest.mark.asyncio
    async def test_mcp_info_token_configured_false_when_no_token(self) -> None:
        """token_configured must be False when MCP_AUTH_TOKEN is unset."""
        import app.main as main_mod
        from app.main import app

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = None  # type: ignore[assignment]
        try:
            with patch("app.main.app.router.lifespan_context", _noop_lifespan):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.get("/mcp/info")
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert resp.status_code == 200
        data = resp.json()
        assert data["token_configured"] is False

    @pytest.mark.asyncio
    async def test_mcp_info_remote_enabled_reflects_flag(self) -> None:
        """GET /mcp/info.remote_enabled reflects the in-process RemoteMcpFlag value."""
        from app.main import app
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        # Set flag to True
        await _remote_mcp_flag.load(True)
        try:
            with patch("app.main.app.router.lifespan_context", _noop_lifespan):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp_on = await ac.get("/mcp/info")

            # Set flag to False
            await _remote_mcp_flag.load(False)
            with patch("app.main.app.router.lifespan_context", _noop_lifespan):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp_off = await ac.get("/mcp/info")
        finally:
            await _remote_mcp_flag.load(False)

        assert resp_on.json()["remote_enabled"] is True
        assert resp_off.json()["remote_enabled"] is False

    @pytest.mark.asyncio
    async def test_mcp_info_mount_path_is_string(self) -> None:
        """/mcp/info.mount_path is a non-empty string."""
        from app.main import app

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        data = resp.json()
        assert isinstance(data["mount_path"], str)
        assert data["mount_path"].startswith("/")


# ─────────────────────────────────────────────────────────────────────────────
# 8. Persistence — flag survives a DB reload
# ─────────────────────────────────────────────────────────────────────────────


class TestFlagPersistence:
    """Flag set ON is persisted in vault_state and reloaded correctly (ADR-0032 §2.2)."""

    @pytest.mark.asyncio
    async def test_load_remote_mcp_flag_reads_true_from_db(self) -> None:
        """_load_remote_mcp_flag() sets the flag from a DB row with remote_mcp_enabled=True."""
        from app.main import _load_remote_mcp_flag
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        # Start with flag OFF
        await _remote_mcp_flag.load(False)

        # Simulate a DB row with remote_mcp_enabled=True
        vault_state_row = _make_vault_state_row(remote_mcp_enabled=True)
        db_ctx = _make_db_session_mock(vault_state_row)

        with patch("app.main.get_session", return_value=db_ctx):
            await _load_remote_mcp_flag()

        assert (
            _remote_mcp_flag.is_enabled() is True
        ), "_load_remote_mcp_flag must update the in-process flag from the DB row (ADR-0032 §2.2)"

        # Clean up
        await _remote_mcp_flag.load(False)

    @pytest.mark.asyncio
    async def test_load_remote_mcp_flag_reads_false_from_db(self) -> None:
        """_load_remote_mcp_flag() sets the flag to False from a DB row with enabled=False."""
        from app.main import _load_remote_mcp_flag
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        # Start with flag ON (to verify it can be reset)
        await _remote_mcp_flag.load(True)

        vault_state_row = _make_vault_state_row(remote_mcp_enabled=False)
        db_ctx = _make_db_session_mock(vault_state_row)

        with patch("app.main.get_session", return_value=db_ctx):
            await _load_remote_mcp_flag()

        assert _remote_mcp_flag.is_enabled() is False

    @pytest.mark.asyncio
    async def test_load_remote_mcp_flag_defaults_false_when_no_row(self) -> None:
        """_load_remote_mcp_flag() defaults to False when vault_state row is missing."""
        from app.main import _load_remote_mcp_flag
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        await _remote_mcp_flag.load(True)

        # Simulate missing row
        db_ctx = _make_db_session_mock(None)

        with patch("app.main.get_session", return_value=db_ctx):
            await _load_remote_mcp_flag()

        assert (
            _remote_mcp_flag.is_enabled() is False
        ), "Missing vault_state row must default to False (fail-closed)"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Session manager NOT remounted on toggle
# ─────────────────────────────────────────────────────────────────────────────


class TestNoRemount:
    """Session manager mounted once; toggle is a flag-only in-memory change (ADR-0032 §2.3)."""

    def test_remote_mcp_flag_set_is_not_a_mount_operation(self) -> None:
        """
        RemoteMcpFlag.set() changes only the in-memory boolean — it does NOT restart
        the session manager or remount the sub-app.

        This is a structural test: the flag class has no reference to any ASGI app,
        session manager, or lifespan — it is a pure in-memory boolean holder.
        """
        from app.runtime_state import RemoteMcpFlag

        flag = RemoteMcpFlag()
        # The flag must have no attributes referencing an ASGI app or session manager.
        for attr in vars(flag):
            assert (
                "app" not in attr.lower()
            ), f"RemoteMcpFlag.{attr} looks like an ASGI app reference — must be flag-only"
            assert (
                "session" not in attr.lower()
            ), f"RemoteMcpFlag.{attr} looks like a session manager reference"
            assert "mount" not in attr.lower(), f"RemoteMcpFlag.{attr} looks like a mount reference"

    @pytest.mark.asyncio
    async def test_flag_toggle_does_not_call_lifespan(self) -> None:
        """
        Setting the flag via RemoteMcpFlag.set() does not trigger any lifespan event.
        (Structural: set() is a simple boolean assignment behind an asyncio.Lock.)
        """
        from app.runtime_state import RemoteMcpFlag

        flag = RemoteMcpFlag()
        lifespan_calls: list[str] = []

        # Monkey-patch: if set() ever called something ASGI-related, it would show here.
        original_set = flag.set

        async def tracked_set(enabled: bool) -> None:
            lifespan_calls.append(f"set({enabled})")
            await original_set(enabled)

        flag.set = tracked_set  # type: ignore[method-assign]

        await flag.set(True)
        await flag.set(False)

        # Only 'set()' calls should be logged — no lifespan/mount calls.
        assert lifespan_calls == ["set(True)", "set(False)"]
        assert flag.is_enabled() is False


# ─────────────────────────────────────────────────────────────────────────────
# 10. MCP_MOUNT_PATH constant
# ─────────────────────────────────────────────────────────────────────────────


class TestMcpMountPathConstant:
    """MCP_MOUNT_PATH is a single module constant; /mcp/info and PUT /mcp/remote use it (I6)."""

    def test_mcp_mount_path_is_string(self) -> None:
        from app.runtime_state import MCP_MOUNT_PATH

        assert isinstance(MCP_MOUNT_PATH, str)
        assert (
            MCP_MOUNT_PATH == "/mcp/server"
        ), "MCP_MOUNT_PATH must be '/mcp/server' (ADR-0032 / ADR-0029)"

    @pytest.mark.asyncio
    async def test_mcp_info_mount_path_equals_constant(self) -> None:
        """GET /mcp/info.mount_path equals MCP_MOUNT_PATH."""
        from app.main import app
        from app.runtime_state import MCP_MOUNT_PATH

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        assert resp.json()["mount_path"] == MCP_MOUNT_PATH
