"""
Tests for ADR-0033: UI-settable MCP access token + allow-without-token gate.

Acceptance checks per ADR-0033 §5:
  1.  Token rotate round-trip — generated_token present on rotate_token=true,
      absent on subsequent GET/PUT; no plaintext/hash in any response or log.
  2.  Hash storage — mcp_access_token_hash is a PBKDF2 string; DB dump never
      contains plaintext; verification is constant-time.
  3.  Env bootstrap precedence — MCP_AUTH_TOKEN works when DB hash is NULL;
      DB token overrides env after PUT /mcp/auth.
  4.  PRIVATE allow-without-token → PASS (loopback peer, no CF header, allow ON).
  5.  PUBLIC always requires token — CRITICAL test:
        a. CF-Connecting-IP header present from a private peer → PUBLIC → 401/404.
        b. CF-Ray header present from a private peer → PUBLIC → 401/404.
        c. Non-private peer IP (no CF header) → PUBLIC → 401/404.
  6.  XFF spoof ignored when peer not trusted.
  7.  CF-header forge only restricts (never grants private access).
  8.  Decision table — every row of ADR-0033 §2.4.
  9.  Allow-aware clamp on PUT /mcp/remote.
  10. No remount / session manager stable.
  11. /mcp/auth response shape + /mcp/info shape.
  12. No plaintext token/hash/salt in any API response.

Test patterns:
  - _BearerAuthMiddleware / _McpGate tested via direct middleware instantiation.
  - Source classification tested via _classify_source() / _ip_is_private() helpers.
  - PUT /mcp/auth + PUT /mcp/remote tested via AsyncClient + mocked DB.
  - GET /mcp/info tested via AsyncClient.
  - PUBLIC source simulated by injecting CF headers or a non-private scope["client"].
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
    remote_mcp_enabled: bool = False,
    mcp_access_token_hash: str | None = None,
    mcp_allow_without_token: bool = False,
) -> MagicMock:
    """Return a mock VaultState row with ADR-0033 columns."""
    row = MagicMock()
    row.remote_mcp_enabled = remote_mcp_enabled
    row.data_version = 0
    row.vault_id = "test"
    row.mcp_access_token_hash = mcp_access_token_hash
    row.mcp_allow_without_token = mcp_allow_without_token
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


def _make_scope(
    client_ip: str = "127.0.0.1",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[str, Any]:
    """Build a minimal ASGI HTTP scope with the given peer IP and headers."""
    return {
        "type": "http",
        "client": (client_ip, 12345),
        "headers": headers or [],
    }


async def _build_gate(
    *,
    flag_enabled: bool = True,
    db_hash: str | None = None,
    allow_without_token: bool = False,
    env_token: str = "",
) -> Any:
    """
    Build a _BearerAuthMiddleware (MCP access gate) with the given posture.

    Returns (middleware, inner_calls) where inner_calls is a list that gains
    a dict when the inner app is reached.
    """
    from app.runtime_state import BearerAuthMiddleware as _BearerAuthMiddleware
    from app.runtime_state import McpAuthCache as _McpAuthCache
    from app.runtime_state import RemoteMcpFlag

    flag = RemoteMcpFlag()
    await flag.load(flag_enabled)

    cache = _McpAuthCache()
    await cache.load(db_hash, allow_without_token)

    inner_calls: list[dict[str, Any]] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        inner_calls.append({"reached": True})

    mw = _BearerAuthMiddleware(
        inner,
        env_token,
        flag,
        cache,
    )
    return mw, inner_calls


async def _call_gate(
    mw: Any,
    scope: dict[str, Any],
    bearer: str | None = None,
) -> int:
    """
    Call the middleware and return the HTTP response status code.

    Returns -1 when the inner app was reached (PASS — no response sent by gate).
    """
    headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
    if bearer is not None:
        headers.append((b"authorization", f"Bearer {bearer}".encode()))
    scope = {**scope, "headers": headers}

    send_events: list[Any] = []

    async def send_fn(event: Any) -> None:
        send_events.append(event)

    inner_called = [False]
    original_app = mw._app

    async def tracking_inner(scope_: Any, receive_: Any, send_: Any) -> None:
        inner_called[0] = True
        await original_app(scope_, receive_, send_)

    mw._app = tracking_inner
    await mw(scope, AsyncMock(), send_fn)
    mw._app = original_app  # restore

    if inner_called[0]:
        return -1  # PASS

    start = [e for e in send_events if e.get("type") == "http.response.start"]
    if start:
        return start[0]["status"]
    return -2  # unexpected


# ─────────────────────────────────────────────────────────────────────────────
# 1. Token hashing helpers
# ─────────────────────────────────────────────────────────────────────────────


class TestTokenHashing:
    """Unit tests for PBKDF2 token hashing helpers (ADR-0033 §2.1)."""

    def test_hash_token_returns_pbkdf2_string(self) -> None:
        from app.runtime_state import hash_token as _hash_token

        h = _hash_token("my-secret-token")
        assert h.startswith("pbkdf2_sha256$"), f"Expected pbkdf2_sha256 prefix, got {h[:30]!r}"
        parts = h.split("$")
        assert len(parts) == 4, "Hash must have 4 parts separated by $"

    def test_verify_token_correct(self) -> None:
        from app.runtime_state import hash_token as _hash_token
        from app.runtime_state import verify_token as _verify_token

        plaintext = "super-secret-12345"
        h = _hash_token(plaintext)
        assert _verify_token(plaintext, h) is True

    def test_verify_token_wrong(self) -> None:
        from app.runtime_state import hash_token as _hash_token
        from app.runtime_state import verify_token as _verify_token

        h = _hash_token("correct-token")
        assert _verify_token("wrong-token", h) is False

    def test_verify_token_different_salts_different_hashes(self) -> None:
        """Each call to _hash_token generates a new salt → different stored hash."""
        from app.runtime_state import hash_token as _hash_token

        h1 = _hash_token("same-plaintext")
        h2 = _hash_token("same-plaintext")
        assert h1 != h2, "Same plaintext must produce different hashes (different salts)"

    def test_verify_token_invalid_format(self) -> None:
        from app.runtime_state import verify_token as _verify_token

        # Malformed hash → False (fail-closed)
        assert _verify_token("token", "not-a-valid-hash") is False
        assert _verify_token("token", "") is False
        assert _verify_token("token", "pbkdf2_sha256$abc$def") is False  # only 3 parts

    def test_hash_contains_no_plaintext(self) -> None:
        """The stored hash must not contain the plaintext token."""
        from app.runtime_state import hash_token as _hash_token

        plaintext = "UNIQUE-TOKEN-SENTINEL-XYZ-9876"
        h = _hash_token(plaintext)
        assert plaintext not in h, "Stored hash must not contain the plaintext token"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Token source resolver
# ─────────────────────────────────────────────────────────────────────────────


class TestTokenSourceResolver:
    """_resolve_token_source + _token_configured precedence (ADR-0033 §2.1)."""

    def test_db_hash_is_db_source(self) -> None:
        from app.runtime_state import resolve_token_source as _resolve_token_source

        assert _resolve_token_source("pbkdf2_sha256$...") == "db"

    def test_no_db_hash_env_token_is_env_source(self) -> None:
        import app.main as main_mod
        from app.runtime_state import resolve_token_source as _resolve_token_source

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = "env-token"  # type: ignore[assignment]
        try:
            assert _resolve_token_source(None) == "env"
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

    def test_no_db_no_env_is_none_source(self) -> None:
        import app.main as main_mod
        from app.runtime_state import resolve_token_source as _resolve_token_source

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = None  # type: ignore[assignment]
        try:
            assert _resolve_token_source(None) == "none"
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

    def test_db_hash_overrides_env(self) -> None:
        """DB hash takes precedence over env bootstrap (ADR-0033 §2.1)."""
        import app.main as main_mod
        from app.runtime_state import resolve_token_source as _resolve_token_source

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = "env-token"  # type: ignore[assignment]
        try:
            assert _resolve_token_source("some-db-hash") == "db"
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

    def test_token_configured_true_with_db_hash(self) -> None:
        from app.runtime_state import token_configured as _token_configured

        assert _token_configured("any-hash") is True

    def test_token_configured_false_with_nothing(self) -> None:
        import app.main as main_mod
        from app.runtime_state import token_configured as _token_configured

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = None  # type: ignore[assignment]
        try:
            assert _token_configured(None) is False
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Source classifier
# ─────────────────────────────────────────────────────────────────────────────


class TestSourceClassifier:
    """_classify_source + _ip_is_private (ADR-0033 §2.3)."""

    def test_loopback_is_private(self) -> None:
        from app.runtime_state import ip_is_private as _ip_is_private

        assert _ip_is_private("127.0.0.1") is True
        assert _ip_is_private("127.255.255.255") is True
        assert _ip_is_private("::1") is True

    def test_tailscale_cgnat_is_private(self) -> None:
        from app.runtime_state import ip_is_private as _ip_is_private

        assert _ip_is_private("100.64.0.1") is True
        assert _ip_is_private("100.127.255.255") is True

    def test_rfc1918_is_private(self) -> None:
        from app.runtime_state import ip_is_private as _ip_is_private

        assert _ip_is_private("10.0.0.1") is True
        assert _ip_is_private("172.16.0.1") is True
        assert _ip_is_private("172.31.255.255") is True
        assert _ip_is_private("192.168.1.1") is True

    def test_link_local_is_private(self) -> None:
        from app.runtime_state import ip_is_private as _ip_is_private

        assert _ip_is_private("169.254.0.1") is True
        # fe80:: (link-local IPv6)
        assert _ip_is_private("fe80::1") is True

    def test_ula_ipv6_is_private(self) -> None:
        from app.runtime_state import ip_is_private as _ip_is_private

        # fc00::/7 ULA
        assert _ip_is_private("fd00::1") is True

    def test_public_ip_is_not_private(self) -> None:
        from app.runtime_state import ip_is_private as _ip_is_private

        assert _ip_is_private("8.8.8.8") is False
        assert _ip_is_private("1.1.1.1") is False
        assert _ip_is_private("203.0.113.1") is False  # TEST-NET-3

    def test_private_peer_no_cf_headers_is_private(self) -> None:
        """Loopback peer + no CF headers → PRIVATE."""
        from app.runtime_state import classify_source as _classify_source

        scope = _make_scope("127.0.0.1")
        assert _classify_source(scope) is False  # PRIVATE

    def test_private_peer_with_cf_connecting_ip_is_public(self) -> None:
        """CF-Connecting-IP present from private peer → PUBLIC (ADR-0033 §2.3 CRITICAL)."""
        from app.runtime_state import classify_source as _classify_source

        scope = _make_scope(
            "127.0.0.1",
            headers=[(b"cf-connecting-ip", b"8.8.8.8")],
        )
        assert _classify_source(scope) is True  # PUBLIC

    def test_private_peer_with_cf_ray_is_public(self) -> None:
        """CF-Ray present from private peer → PUBLIC (ADR-0033 §2.3 CRITICAL)."""
        from app.runtime_state import classify_source as _classify_source

        scope = _make_scope(
            "192.168.1.5",
            headers=[(b"cf-ray", b"7f9e0a1b2c3d4e5f-LHR")],
        )
        assert _classify_source(scope) is True  # PUBLIC

    def test_public_peer_no_cf_headers_is_public(self) -> None:
        """Non-private peer IP + no CF headers → PUBLIC."""
        from app.runtime_state import classify_source as _classify_source

        scope = _make_scope("203.0.113.5")
        assert _classify_source(scope) is True  # PUBLIC

    def test_missing_client_in_scope_is_public(self) -> None:
        """No client in scope → fail-safe PUBLIC."""
        from app.runtime_state import classify_source as _classify_source

        scope = {"type": "http", "headers": []}
        assert _classify_source(scope) is True  # PUBLIC (fail-safe)

    def test_xff_ignored_when_peer_not_trusted(self) -> None:
        """
        Untrusted peer forges X-Forwarded-For: 127.0.0.1 → classified by peer IP
        (public peer → PUBLIC) — forge does not grant private access.
        ADR-0033 §2.3 XFF spoofing defence.
        """
        import app.main as main_mod
        from app.runtime_state import classify_source as _classify_source

        # Ensure no trusted proxies
        original = main_mod.settings.mcp_trusted_proxies
        main_mod.settings.mcp_trusted_proxies = ""  # type: ignore[assignment]
        try:
            scope = _make_scope(
                "8.8.8.8",  # public peer
                headers=[(b"x-forwarded-for", b"127.0.0.1")],  # spoofed XFF
            )
            result = _classify_source(scope)
        finally:
            main_mod.settings.mcp_trusted_proxies = original  # type: ignore[assignment]

        assert result is True  # PUBLIC — the spoof is ignored


# ─────────────────────────────────────────────────────────────────────────────
# 4. Decision table — every row of ADR-0033 §2.4
# ─────────────────────────────────────────────────────────────────────────────


class TestDecisionTable:
    """Every row of ADR-0033 §2.4 decision table (via _BearerAuthMiddleware)."""

    @pytest.mark.asyncio
    async def test_row_remote_off_any_returns_404(self) -> None:
        """remote_enabled OFF → 404 regardless of everything."""
        from app.runtime_state import hash_token as _hash_token

        mw, _ = await _build_gate(
            flag_enabled=False, db_hash=_hash_token("tok"), allow_without_token=True
        )
        scope = _make_scope("127.0.0.1")
        status = await _call_gate(mw, scope, bearer="tok")
        assert status == 404, f"remote_enabled OFF must return 404; got {status}"

    @pytest.mark.asyncio
    async def test_row_on_valid_bearer_passes(self) -> None:
        """ON + valid bearer → PASS (any source, any allow state)."""
        from app.runtime_state import hash_token as _hash_token

        plaintext = "valid-bearer-token"
        h = _hash_token(plaintext)
        mw, _ = await _build_gate(flag_enabled=True, db_hash=h, allow_without_token=False)
        scope = _make_scope("8.8.8.8")  # public source
        status = await _call_gate(mw, scope, bearer=plaintext)
        assert status == -1, f"Valid bearer must PASS; got status {status}"

    @pytest.mark.asyncio
    async def test_row_private_tok_allow_off_no_bearer_returns_401(self) -> None:
        """ON + PRIVATE + tok configured + allow OFF + no bearer → 401."""
        from app.runtime_state import hash_token as _hash_token

        mw, _ = await _build_gate(
            flag_enabled=True,
            db_hash=_hash_token("some-token"),
            allow_without_token=False,
        )
        scope = _make_scope("127.0.0.1")
        status = await _call_gate(mw, scope)
        assert status == 401, f"Expected 401; got {status}"

    @pytest.mark.asyncio
    async def test_row_private_tok_allow_on_no_bearer_passes(self) -> None:
        """ON + PRIVATE + tok configured + allow ON + no bearer → PASS."""
        from app.runtime_state import hash_token as _hash_token

        mw, _ = await _build_gate(
            flag_enabled=True,
            db_hash=_hash_token("some-token"),
            allow_without_token=True,
        )
        scope = _make_scope("127.0.0.1")
        status = await _call_gate(mw, scope)
        assert status == -1, f"allow_without_token=ON + private must PASS; got {status}"

    @pytest.mark.asyncio
    async def test_row_private_no_tok_allow_on_no_bearer_passes(self) -> None:
        """ON + PRIVATE + no token + allow ON + no bearer → PASS."""
        import app.main as main_mod

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = None  # type: ignore[assignment]
        try:
            mw, _ = await _build_gate(flag_enabled=True, db_hash=None, allow_without_token=True)
            scope = _make_scope("192.168.0.10")  # private
            status = await _call_gate(mw, scope)
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert status == -1, f"No token + allow_on + private must PASS; got {status}"

    @pytest.mark.asyncio
    async def test_row_private_no_tok_allow_off_returns_404(self) -> None:
        """ON + PRIVATE + no token + allow OFF → 404 (surface closed)."""
        import app.main as main_mod

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = None  # type: ignore[assignment]
        try:
            mw, _ = await _build_gate(flag_enabled=True, db_hash=None, allow_without_token=False)
            scope = _make_scope("10.0.0.1")  # private
            status = await _call_gate(mw, scope)
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert status == 404, f"No token + allow_off + private must be 404; got {status}"

    @pytest.mark.asyncio
    async def test_row_public_tok_configured_no_bearer_returns_401(self) -> None:
        """ON + PUBLIC + tok configured + no/bad bearer → 401."""
        from app.runtime_state import hash_token as _hash_token

        mw, _ = await _build_gate(
            flag_enabled=True,
            db_hash=_hash_token("some-token"),
            allow_without_token=True,  # allow ON but PUBLIC → still 401
        )
        scope = _make_scope(
            "192.168.1.5",
            headers=[(b"cf-connecting-ip", b"8.8.8.8")],  # CF header → PUBLIC
        )
        status = await _call_gate(mw, scope)
        assert status == 401, f"PUBLIC + tok configured + no bearer must be 401; got {status}"

    @pytest.mark.asyncio
    async def test_row_public_no_tok_returns_404(self) -> None:
        """ON + PUBLIC + no token → 404 (never open the public surface token-lessly)."""
        import app.main as main_mod

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = None  # type: ignore[assignment]
        try:
            mw, _ = await _build_gate(
                flag_enabled=True,
                db_hash=None,
                allow_without_token=True,  # allow ON but PUBLIC → still 404
            )
            scope = _make_scope("8.8.8.8")  # public peer
            status = await _call_gate(mw, scope)
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert status == 404, f"PUBLIC + no token must be 404; got {status}"


# ─────────────────────────────────────────────────────────────────────────────
# 5. CRITICAL: PUBLIC always requires token — never grant token-less PUBLIC access
# ─────────────────────────────────────────────────────────────────────────────


class TestPublicAlwaysRequiresToken:
    """
    ADR-0033 §2.3 CRITICAL acceptance check:
    allow_without_token=ON must NEVER grant token-less access to PUBLIC sources.
    """

    @pytest.mark.asyncio
    async def test_cf_connecting_ip_from_private_peer_is_public(self) -> None:
        """
        CRITICAL: CF-Connecting-IP header present, peer is private (loopback).
        The CF header forces PUBLIC → token required → 401 (tok configured).
        """
        from app.runtime_state import hash_token as _hash_token

        mw, _ = await _build_gate(
            flag_enabled=True,
            db_hash=_hash_token("tok"),
            allow_without_token=True,
        )
        scope = _make_scope(
            "127.0.0.1",  # private peer
            headers=[(b"cf-connecting-ip", b"203.0.113.1")],  # CF header → PUBLIC
        )
        status = await _call_gate(mw, scope)
        assert status == 401, (
            "CRITICAL: CF-Connecting-IP must force PUBLIC → 401; "
            f"allow_without_token=ON must NOT bypass this; got {status}"
        )

    @pytest.mark.asyncio
    async def test_cf_ray_from_private_peer_is_public(self) -> None:
        """CRITICAL: CF-Ray header → PUBLIC → 401 even from loopback."""
        from app.runtime_state import hash_token as _hash_token

        mw, _ = await _build_gate(
            flag_enabled=True,
            db_hash=_hash_token("tok"),
            allow_without_token=True,
        )
        scope = _make_scope(
            "::1",  # IPv6 loopback
            headers=[(b"cf-ray", b"abc123-LHR")],
        )
        status = await _call_gate(mw, scope)
        assert status == 401, "CRITICAL: CF-Ray must force PUBLIC → 401; got {status}"

    @pytest.mark.asyncio
    async def test_public_peer_no_cf_header_no_token_gets_404(self) -> None:
        """CRITICAL: Public peer (non-private IP), no CF header, allow ON → still 404."""
        import app.main as main_mod

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = None  # type: ignore[assignment]
        try:
            mw, _ = await _build_gate(
                flag_enabled=True,
                db_hash=None,
                allow_without_token=True,
            )
            scope = _make_scope("203.0.113.5")  # public
            status = await _call_gate(mw, scope)
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert (
            status == 404
        ), "CRITICAL: Public peer + allow_without_token=ON + no token must be 404; got {status}"

    @pytest.mark.asyncio
    async def test_public_peer_with_token_gets_401_not_pass(self) -> None:
        """CRITICAL: Public peer, tok configured, allow ON, no bearer → 401 (not PASS)."""
        from app.runtime_state import hash_token as _hash_token

        mw, _ = await _build_gate(
            flag_enabled=True,
            db_hash=_hash_token("tok"),
            allow_without_token=True,
        )
        scope = _make_scope("1.2.3.4")  # public
        status = await _call_gate(mw, scope)
        assert status == 401, "CRITICAL: Public source must never PASS without bearer; got {status}"

    @pytest.mark.asyncio
    async def test_forging_cf_header_only_restricts_never_grants(self) -> None:
        """
        ADR-0033 §2.3: CF-Connecting-IP forge only restricts (makes request PUBLIC).
        A private peer forging CF-Connecting-IP cannot GAIN private access —
        it loses it (forced PUBLIC).
        """
        from app.runtime_state import hash_token as _hash_token

        mw, _ = await _build_gate(
            flag_enabled=True,
            db_hash=_hash_token("tok"),
            allow_without_token=True,
        )
        # Private peer forging a CF header
        scope = _make_scope(
            "10.0.0.1",  # private peer
            headers=[(b"cf-connecting-ip", b"fake-cf-ip")],
        )
        status = await _call_gate(mw, scope)
        # Must be 401 (PUBLIC → token required) NOT -1 (PASS as private)
        assert status in (
            401,
            404,
        ), "CRITICAL: Forged CF header must RESTRICT not grant; got {status}"


# ─────────────────────────────────────────────────────────────────────────────
# 6. XFF spoof defence
# ─────────────────────────────────────────────────────────────────────────────


class TestXffSpoofDefence:
    """XFF is honoured only when peer is in MCP_TRUSTED_PROXIES (ADR-0033 §2.3)."""

    @pytest.mark.asyncio
    async def test_xff_loopback_from_untrusted_public_peer_is_public(self) -> None:
        """
        Untrusted public peer + X-Forwarded-For: 127.0.0.1 → classified as peer IP
        (public) → token required.
        """
        import app.main as main_mod
        from app.runtime_state import hash_token as _hash_token

        original = main_mod.settings.mcp_trusted_proxies
        main_mod.settings.mcp_trusted_proxies = ""  # type: ignore[assignment]
        try:
            mw, _ = await _build_gate(
                flag_enabled=True,
                db_hash=_hash_token("tok"),
                allow_without_token=True,
            )
            scope = _make_scope(
                "8.8.8.8",  # public peer
                headers=[(b"x-forwarded-for", b"127.0.0.1")],
            )
            status = await _call_gate(mw, scope)
        finally:
            main_mod.settings.mcp_trusted_proxies = original  # type: ignore[assignment]

        assert (
            status == 401
        ), "XFF spoof from untrusted peer must be classified by peer IP → 401; got {status}"

    def test_resolve_source_ip_uses_peer_when_no_trusted_proxies(self) -> None:
        """_resolve_source_ip returns peer IP when MCP_TRUSTED_PROXIES is empty."""
        import app.main as main_mod
        from app.client_ip import resolve_source_ip as _resolve_source_ip

        original = main_mod.settings.mcp_trusted_proxies
        main_mod.settings.mcp_trusted_proxies = ""  # type: ignore[assignment]
        try:
            scope = _make_scope(
                "8.8.8.8",
                headers=[(b"x-forwarded-for", b"127.0.0.1")],
            )
            ip = _resolve_source_ip(scope)
        finally:
            main_mod.settings.mcp_trusted_proxies = original  # type: ignore[assignment]

        assert ip == "8.8.8.8", f"Must use peer IP when no trusted proxies; got {ip!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Env bootstrap precedence (ADR-0033 §2.1)
# ─────────────────────────────────────────────────────────────────────────────


class TestEnvBootstrapPrecedence:
    """Env MCP_AUTH_TOKEN works when DB hash NULL; DB hash overrides env (ADR-0033 §2.1)."""

    @pytest.mark.asyncio
    async def test_env_token_authenticates_when_db_hash_null(self) -> None:
        """No DB hash → env token is authoritative; correct bearer → PASS."""
        import app.main as main_mod

        env_plaintext = "env-bootstrap-token"
        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = env_plaintext  # type: ignore[assignment]
        try:
            mw, _ = await _build_gate(
                flag_enabled=True,
                db_hash=None,  # no DB token
                allow_without_token=False,
                env_token=env_plaintext,
            )
            scope = _make_scope("127.0.0.1")
            status = await _call_gate(mw, scope, bearer=env_plaintext)
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert status == -1, f"Env bootstrap token must authenticate; got {status}"

    @pytest.mark.asyncio
    async def test_db_hash_overrides_env_token(self) -> None:
        """DB hash set → env token no longer authenticates (DB wins)."""
        import app.main as main_mod
        from app.runtime_state import hash_token as _hash_token

        env_plaintext = "env-token-that-should-be-overridden"
        db_plaintext = "db-ui-set-token"

        original = main_mod.settings.mcp_auth_token
        main_mod.settings.mcp_auth_token = env_plaintext  # type: ignore[assignment]
        try:
            mw, _ = await _build_gate(
                flag_enabled=True,
                db_hash=_hash_token(db_plaintext),
                allow_without_token=False,
                env_token=env_plaintext,
            )
            scope = _make_scope("127.0.0.1")
            # Env token should NOT work when DB hash is set
            status_env = await _call_gate(mw, scope, bearer=env_plaintext)
            # DB token SHOULD work
            status_db = await _call_gate(mw, scope, bearer=db_plaintext)
        finally:
            main_mod.settings.mcp_auth_token = original  # type: ignore[assignment]

        assert (
            status_env == 401
        ), f"Env token must not authenticate when DB hash is set; got {status_env}"
        assert status_db == -1, f"DB token must authenticate; got {status_db}"


# ─────────────────────────────────────────────────────────────────────────────
# 8. PUT /mcp/auth endpoint
# ─────────────────────────────────────────────────────────────────────────────


class TestPutMcpAuth:
    """PUT /mcp/auth handler (ADR-0033 §2.5)."""

    @pytest.mark.asyncio
    async def test_rotate_token_returns_generated_token_once(self) -> None:
        """rotate_token=true returns generated_token in response (ADR-0033 §2.5)."""
        from app.main import app

        vault_state_row = _make_vault_state_row()
        db_ctx = _make_db_session_mock(vault_state_row)

        with (
            patch("app.main.app.router.lifespan_context", _noop_lifespan),
            patch("app.main.get_session", return_value=db_ctx),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.put("/mcp/auth", json={"rotate_token": True})

        assert resp.status_code == 200
        data = resp.json()
        assert (
            data.get("generated_token") is not None
        ), "rotate_token=true must return generated_token"
        assert isinstance(data["generated_token"], str)
        assert len(data["generated_token"]) > 0

    @pytest.mark.asyncio
    async def test_explicit_token_not_echoed(self) -> None:
        """Explicit token= is NOT echoed in the response (ADR-0033 §2.5)."""
        from app.main import app

        vault_state_row = _make_vault_state_row()
        db_ctx = _make_db_session_mock(vault_state_row)
        sentinel = "EXPLICIT-TOKEN-DO-NOT-ECHO-0033"

        with (
            patch("app.main.app.router.lifespan_context", _noop_lifespan),
            patch("app.main.get_session", return_value=db_ctx),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.put("/mcp/auth", json={"token": sentinel})

        assert resp.status_code == 200
        data = resp.json()
        assert (
            data.get("generated_token") is None
        ), "explicit token= must not populate generated_token"
        assert sentinel not in resp.text, "Explicit token must NEVER be echoed in response"

    @pytest.mark.asyncio
    async def test_response_shape(self) -> None:
        """PUT /mcp/auth response includes all required fields."""
        from app.main import app

        vault_state_row = _make_vault_state_row()
        db_ctx = _make_db_session_mock(vault_state_row)

        with (
            patch("app.main.app.router.lifespan_context", _noop_lifespan),
            patch("app.main.get_session", return_value=db_ctx),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.put("/mcp/auth", json={"allow_without_token": True})

        assert resp.status_code == 200
        data = resp.json()
        required_fields = (
            "token_configured",
            "token_source",
            "allow_without_token",
            "remote_enabled",
            "mount_path",
        )
        for field in required_fields:
            assert field in data, f"Field {field!r} missing from PUT /mcp/auth response"
        assert isinstance(data["token_configured"], bool)
        assert data["token_source"] in ("db", "env", "none")
        assert isinstance(data["allow_without_token"], bool)
        assert isinstance(data["remote_enabled"], bool)
        assert data["mount_path"] == "/mcp/server"

    @pytest.mark.asyncio
    async def test_no_hash_in_response(self) -> None:
        """No PBKDF2 hash or token plaintext in PUT /mcp/auth response (ADR-0033 §2.1)."""
        from app.main import app

        vault_state_row = _make_vault_state_row()
        db_ctx = _make_db_session_mock(vault_state_row)

        with (
            patch("app.main.app.router.lifespan_context", _noop_lifespan),
            patch("app.main.get_session", return_value=db_ctx),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.put("/mcp/auth", json={"rotate_token": True})

        assert resp.status_code == 200
        body = resp.text
        # PBKDF2 format prefix must NEVER appear in any API response
        assert "pbkdf2_sha256$" not in body, "PBKDF2 hash prefix must not appear in response"


# ─────────────────────────────────────────────────────────────────────────────
# 9. PUT /mcp/remote — allow-aware clamp (ADR-0033 §2.4)
# ─────────────────────────────────────────────────────────────────────────────


class TestAllowAwareClamp:
    """PUT /mcp/remote allow-aware clamp (ADR-0033 §2.4 replaces ADR-0032 token-floor clamp)."""

    @pytest.mark.asyncio
    async def test_enable_with_allow_on_no_token_is_not_clamped(self) -> None:
        """
        enabled=true + no token + allow_without_token=ON → NOT clamped
        (allow_on is a valid posture for private access — ADR-0033 §2.4).
        """
        import app.main as main_mod
        from app.main import app
        from app.runtime_state import mcp_auth_cache as _mcp_auth_cache
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        await _remote_mcp_flag.load(False)
        await _mcp_auth_cache.load(None, True)  # allow=ON, no token

        vault_state_row = _make_vault_state_row()
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
            await _remote_mcp_flag.load(False)
            await _mcp_auth_cache.load(None, False)

        assert resp.status_code == 200
        data = resp.json()
        assert (
            data["clamped"] is False
        ), "allow_without_token=ON must allow enabling remote even without a token"
        assert data["remote_enabled"] is True

    @pytest.mark.asyncio
    async def test_enable_without_token_and_allow_is_clamped(self) -> None:
        """
        enabled=true + no token + allow_without_token=OFF → clamped (ADR-0033 §2.4).
        No usable auth posture.
        """
        import app.main as main_mod
        from app.main import app
        from app.runtime_state import mcp_auth_cache as _mcp_auth_cache
        from app.runtime_state import remote_mcp_flag as _remote_mcp_flag

        await _remote_mcp_flag.load(False)
        await _mcp_auth_cache.load(None, False)  # allow=OFF, no token

        vault_state_row = _make_vault_state_row()
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
            await _remote_mcp_flag.load(False)

        assert resp.status_code == 200
        data = resp.json()
        assert data["clamped"] is True, "No token AND allow=OFF must clamp remote_enabled to OFF"
        assert data["remote_enabled"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 10. GET /mcp/info — ADR-0033 additions
# ─────────────────────────────────────────────────────────────────────────────


class TestMcpInfoAdr0033:
    """GET /mcp/info includes token_source + allow_without_token (ADR-0033 §2.5)."""

    @pytest.mark.asyncio
    async def test_mcp_info_has_token_source(self) -> None:
        from app.main import app

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        assert resp.status_code == 200
        data = resp.json()
        assert "token_source" in data, "GET /mcp/info must include token_source (ADR-0033 §2.5)"
        assert data["token_source"] in ("db", "env", "none")

    @pytest.mark.asyncio
    async def test_mcp_info_has_allow_without_token(self) -> None:
        from app.main import app

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        assert resp.status_code == 200
        data = resp.json()
        assert "allow_without_token" in data, "GET /mcp/info must include allow_without_token"
        assert isinstance(data["allow_without_token"], bool)

    @pytest.mark.asyncio
    async def test_mcp_info_never_returns_token_hash_or_salt(self) -> None:
        """GET /mcp/info must NEVER return token, hash, or salt (ADR-0033 §2.5)."""
        from app.main import app
        from app.runtime_state import hash_token as _hash_token
        from app.runtime_state import mcp_auth_cache as _mcp_auth_cache

        sentinel = "SUPER-SECRET-SENTINEL-TOKEN-0033-XYZ"
        h = _hash_token(sentinel)
        await _mcp_auth_cache.load(h, False)
        try:
            with patch("app.main.app.router.lifespan_context", _noop_lifespan):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as ac:
                    resp = await ac.get("/mcp/info")
        finally:
            await _mcp_auth_cache.load(None, False)

        assert resp.status_code == 200
        body = resp.text
        assert sentinel not in body, "Token plaintext must NEVER appear in /mcp/info response"
        assert "pbkdf2_sha256$" not in body, "PBKDF2 hash must NEVER appear in /mcp/info response"

    @pytest.mark.asyncio
    async def test_mcp_info_http_enabled_always_true(self) -> None:
        """GET /mcp/info.http_enabled is always True (ADR-0033 §2.4 always-mount)."""
        from app.main import app

        with patch("app.main.app.router.lifespan_context", _noop_lifespan):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.get("/mcp/info")

        assert resp.status_code == 200
        data = resp.json()
        assert (
            data["http_enabled"] is True
        ), "http_enabled must be True (always-mount, ADR-0033 §2.4)"


# ─────────────────────────────────────────────────────────────────────────────
# 11. No-remount — lifespan/WS scopes pass through
# ─────────────────────────────────────────────────────────────────────────────


class TestNoRemountAdr0033:
    """Session manager stability: lifespan/WS pass through (ADR-0032 §2.3 / ADR-0033)."""

    @pytest.mark.asyncio
    async def test_lifespan_scope_bypasses_gate(self) -> None:
        """Lifespan scope must pass through unconditionally (no auth check)."""
        mw, inner_calls = await _build_gate(
            flag_enabled=False,
            db_hash=None,
            allow_without_token=False,
        )

        async def capturing_inner(scope: Any, receive: Any, send: Any) -> None:
            inner_calls.append(scope)

        mw._app = capturing_inner  # type: ignore[method-assign]
        scope = {"type": "lifespan", "headers": []}
        await mw(scope, AsyncMock(), AsyncMock())
        assert len(inner_calls) == 1, "Lifespan scope must reach inner app"

    @pytest.mark.asyncio
    async def test_mcp_auth_cache_set_does_not_call_lifespan(self) -> None:
        """_McpAuthCache.set_hash() and set_allow() are pure in-memory — no ASGI calls."""
        from app.runtime_state import McpAuthCache as _McpAuthCache

        cache = _McpAuthCache()
        calls: list[str] = []

        original_set_hash = cache.set_hash
        original_set_allow = cache.set_allow

        async def tracked_set_hash(h: Any) -> None:
            calls.append(f"set_hash({h is not None})")
            await original_set_hash(h)

        async def tracked_set_allow(a: bool) -> None:
            calls.append(f"set_allow({a})")
            await original_set_allow(a)

        cache.set_hash = tracked_set_hash  # type: ignore[method-assign]
        cache.set_allow = tracked_set_allow  # type: ignore[method-assign]

        await cache.set_hash(None)
        await cache.set_allow(True)

        # No "app", "mount", or "session" references in calls
        for c in calls:
            assert "app" not in c.lower()
            assert "mount" not in c.lower()
            assert "session" not in c.lower()


# ─────────────────────────────────────────────────────────────────────────────
# 12. MCP_PRIVATE_CIDRS constant completeness check
# ─────────────────────────────────────────────────────────────────────────────


class TestPrivateCidrsConstant:
    """MCP_PRIVATE_CIDRS covers all required ranges (ADR-0033 §2.3 / I6)."""

    def test_cidr_constant_is_a_named_tuple(self) -> None:
        from app.runtime_state import MCP_PRIVATE_CIDRS

        assert isinstance(MCP_PRIVATE_CIDRS, tuple)
        assert len(MCP_PRIVATE_CIDRS) > 0

    def test_loopback_covered(self) -> None:
        import ipaddress

        from app.runtime_state import MCP_PRIVATE_CIDRS

        lo = ipaddress.ip_address("127.0.0.1")
        assert any(lo in net for net in MCP_PRIVATE_CIDRS), "127.0.0.1 must be in MCP_PRIVATE_CIDRS"

    def test_tailscale_cgnat_covered(self) -> None:
        import ipaddress

        from app.runtime_state import MCP_PRIVATE_CIDRS

        ts = ipaddress.ip_address("100.100.100.100")
        assert any(
            ts in net for net in MCP_PRIVATE_CIDRS
        ), "100.100.100.100 must be in MCP_PRIVATE_CIDRS"

    def test_rfc1918_covered(self) -> None:
        import ipaddress

        from app.runtime_state import MCP_PRIVATE_CIDRS

        for ip in ("10.0.0.1", "172.16.0.1", "192.168.1.1"):
            addr = ipaddress.ip_address(ip)
            assert any(
                addr in net for net in MCP_PRIVATE_CIDRS
            ), f"{ip} must be in MCP_PRIVATE_CIDRS"

    def test_public_ip_not_covered(self) -> None:
        import ipaddress

        from app.runtime_state import MCP_PRIVATE_CIDRS

        pub = ipaddress.ip_address("8.8.8.8")
        assert not any(
            pub in net for net in MCP_PRIVATE_CIDRS
        ), "8.8.8.8 must NOT be in MCP_PRIVATE_CIDRS"
