"""
Tests for GET/PUT /provider/cli-auth — CLI subscription OAuth token (ADR-0043).

Acceptance checks per ADR-0043 §4 (mirrors test_clip_config.py):
  1.  GET /provider/cli-auth posture — nothing set (none source, unconfigured).
  2.  GET /provider/cli-auth posture — env ANTHROPIC_API_KEY only (env source, api-key mode).
  3.  GET /provider/cli-auth posture — env CLAUDE_CODE_OAUTH_TOKEN only (env source, subscription).
  4.  GET /provider/cli-auth posture — env CLAUDE_CODE_USE_SUBSCRIPTION only (env, subscription).
  5.  GET /provider/cli-auth posture — DB token set (db source, subscription, value NOT returned).
  6.  PUT /provider/cli-auth token=<value> — stores to DB and refreshes cache; posture = db/sub.
  7.  PUT /provider/cli-auth clear=true — nulls DB token; posture falls back to env/none.
  8.  Token value NEVER in GET response (grep-equivalent assertion).
  9.  Token value NEVER in PUT response.
  10. clear wins over token when both sent.
  11. Empty/whitespace token → 422.
  12. Too-short token (< 20 chars) → 422.
  13. Too-long token (> 500 chars) → 422.
  14. Plausible token (no sk-ant-oat01 prefix) → 200 (soft check, not hard-reject).
  15. Neither token nor clear → 400.
  16. DB token wins over env API key for auth_mode (crux — ADR-0043 §2.3 tier 1).
  17. Cache token_source/auth_mode unit matrix (db/env/none × all env combos).
  18. set_token refreshes cache; resolve_subscription_token returns the token.
  19. Empty DB token treated as unset (None) — no empty-string leakage.
  20. Response shape: required fields present; no token/value field ever exposed.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────


async def _noop_lifespan(app_: Any) -> AsyncGenerator[None, None]:
    """No-op lifespan — suppresses real startup/shutdown in tests."""
    yield


def _make_vault_state_row(
    cli_oauth_token: str | None = None,
    cli_oauth_token_encrypted: bytes | None = None,
) -> MagicMock:
    """Return a mock VaultState row with the ADR-0043 + W7 columns (plus pre-existing)."""
    row = MagicMock()
    row.vault_id = "test-cli-auth-config"
    row.data_version = 0
    row.remote_mcp_enabled = False
    row.mcp_access_token_hash = None
    row.mcp_allow_without_token = False
    row.clip_enabled_db = None
    row.clip_access_token = None
    row.clip_allowed_origins_db = None
    row.searxng_url_db = None
    row.searxng_categories_db = None
    row.searxng_max_queries_db = None
    row.cli_oauth_token = cli_oauth_token  # ADR-0043 column (legacy plaintext)
    row.cli_oauth_token_encrypted = cli_oauth_token_encrypted  # W7 column (Fernet ciphertext)
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


async def _make_client() -> AsyncClient:
    """Build an AsyncClient against the FastAPI app with a no-op lifespan."""
    from contextlib import asynccontextmanager as acm

    from app.main import app
    from fastapi import FastAPI

    @acm
    async def test_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield

    app.router.lifespan_context = test_lifespan
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ─────────────────────────────────────────────────────────────────────────────
# 1. GET /provider/cli-auth — token_source = 'none' (no DB, no env)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_cli_auth_config_token_source_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-CA-01: No DB token, no env signals → token_source='none', token_configured=False."""
    import app.cli_auth as cli_auth_mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        async with await _make_client() as client:
            resp = await client.get("/provider/cli-auth")
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_source"] == "none"
    assert body["token_configured"] is False
    assert body["auth_mode"] == "unconfigured"
    # CRITICAL: token value NEVER in response
    assert "token" not in body or body.get("token") is None
    assert "cli_oauth_token" not in body
    assert "value" not in body


# ─────────────────────────────────────────────────────────────────────────────
# 2–4. GET /provider/cli-auth — env-only postures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_cli_auth_config_token_source_env_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CA-02: No DB token, ANTHROPIC_API_KEY set → token_source='env', auth_mode='api-key'."""
    import app.cli_auth as cli_auth_mod

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-env-key")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)  # no DB token
        async with await _make_client() as client:
            resp = await client.get("/provider/cli-auth")
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_source"] == "env"
    assert body["token_configured"] is True
    assert body["auth_mode"] == "api-key"
    # CRITICAL: env token value NEVER in response
    assert "sk-test-env-key" not in resp.text, "ANTHROPIC_API_KEY value leaked into response!"


@pytest.mark.asyncio
async def test_get_cli_auth_config_token_source_env_oauth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CA-03: No DB token, CLAUDE_CODE_OAUTH_TOKEN set.

    Expected: token_source='env', auth_mode='subscription'.
    """
    import app.cli_auth as cli_auth_mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-env-test")
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        async with await _make_client() as client:
            resp = await client.get("/provider/cli-auth")
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_source"] == "env"
    assert body["token_configured"] is True
    assert body["auth_mode"] == "subscription"
    # Env token value NEVER in response
    assert "sk-ant-oat-env-test" not in resp.text


@pytest.mark.asyncio
async def test_get_cli_auth_config_token_source_env_use_subscription(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CA-04: No DB token, CLAUDE_CODE_USE_SUBSCRIPTION=true → env, subscription."""
    import app.cli_auth as cli_auth_mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", "true")

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        async with await _make_client() as client:
            resp = await client.get("/provider/cli-auth")
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_source"] == "env"
    assert body["token_configured"] is True
    assert body["auth_mode"] == "subscription"


# ─────────────────────────────────────────────────────────────────────────────
# 5. GET /provider/cli-auth — DB token set (db source, value NOT returned)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_cli_auth_config_token_source_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-CA-05: DB token set → token_source='db', auth_mode='subscription', value NOT returned."""
    import app.cli_auth as cli_auth_mod

    db_token_value = "sk-ant-oat01-SENTINEL-TOKEN-FOR-TEST-12345"
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        await cli_auth_mod._cli_auth_config_cache.load(db_token_value)
        async with await _make_client() as client:
            resp = await client.get("/provider/cli-auth")
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_source"] == "db"
    assert body["token_configured"] is True
    assert body["auth_mode"] == "subscription"
    # CRITICAL: DB token value NEVER in response (key invariant — ADR-0043 §2.1)
    response_str = resp.text
    assert db_token_value not in response_str, (
        f"SECURITY VIOLATION: DB token value leaked into GET /provider/cli-auth response: "
        f"{response_str!r}"
    )
    assert "cli_oauth_token" not in body
    assert "value" not in body


# ─────────────────────────────────────────────────────────────────────────────
# 6. PUT /provider/cli-auth token=<value> — stores to DB + refreshes cache
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_cli_auth_config_set_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-CA-06 (W7 updated): PUT {token} stores Fernet ciphertext to cli_oauth_token_encrypted
    (not plaintext) and refreshes cache → db/subscription.

    W7 amendment: the write path now uses cli_oauth_token_encrypted (BYTEA) rather than the
    legacy cli_oauth_token (TEXT). SYNAPSE_SECRET_KEY must be set. The CLI cache still holds
    the decrypted plaintext in-memory for outbound injection.
    """
    import app.cli_auth as cli_auth_mod
    from cryptography.fernet import Fernet as _Fernet

    master_key = _Fernet.generate_key().decode()
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", master_key)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    pasted_token = "sk-ant-oat01-TEST-SET-TOKEN-FOR-UNIT-TEST-9999"
    original_token = cli_auth_mod._cli_auth_config_cache.get_token()

    # Capture what the PUT handler writes into both DB columns (W7).
    captured_encrypted: list[bytes | None] = []
    captured_legacy: list[str | None] = []

    class _CapturingStateV2:
        vault_id = "test-cli-auth-set"
        data_version = 0
        remote_mcp_enabled = False
        mcp_access_token_hash = None
        mcp_allow_without_token = False
        clip_enabled_db = None
        clip_access_token = None
        clip_allowed_origins_db = None
        cli_oauth_token: str | None = None
        cli_oauth_token_encrypted: bytes | None = None
        updated_at = None

    capturing_state = _CapturingStateV2()

    def make_session() -> Any:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = capturing_state

        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)

        def _capture(*_args: Any) -> None:
            captured_encrypted.append(capturing_state.cli_oauth_token_encrypted)
            captured_legacy.append(capturing_state.cli_oauth_token)

        ctx.__aexit__ = AsyncMock(side_effect=_capture)
        return ctx

    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)

        with patch("app.main.get_session", side_effect=make_session):
            async with await _make_client() as client:
                resp = await client.put("/provider/cli-auth", json={"token": pasted_token})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Posture should now be db/subscription
    assert body["token_source"] == "db"
    assert body["token_configured"] is True
    assert body["auth_mode"] == "subscription"

    # W7: the encrypted column holds Fernet ciphertext (not plaintext).
    assert len(captured_encrypted) == 1, f"Expected 1 DB write, got {captured_encrypted}"
    stored_enc = captured_encrypted[0]
    assert stored_enc is not None, "cli_oauth_token_encrypted must be set after PUT"
    assert isinstance(stored_enc, bytes), f"Expected bytes for ciphertext, got {type(stored_enc)}"
    assert (
        stored_enc != pasted_token.encode()
    ), "SECURITY VIOLATION: stored bytes equal raw token — not encrypted"
    # W7: legacy cli_oauth_token must be NULL (write path migrated to encrypted column).
    assert (
        captured_legacy[0] is None
    ), f"cli_oauth_token (legacy) must be NULL after W7 PUT, got {captured_legacy[0]!r}"
    # W7: round-trip: the stored ciphertext decrypts to the original token.
    from cryptography.fernet import Fernet as _F2

    decrypted = _F2(master_key.encode()).decrypt(stored_enc).decode()
    assert decrypted == pasted_token, f"Round-trip failed: {decrypted!r} != {pasted_token!r}"

    # CRITICAL: token value NEVER in response.
    assert pasted_token not in resp.text, (
        f"SECURITY VIOLATION: token value leaked into PUT /provider/cli-auth response: "
        f"{resp.text!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. PUT /provider/cli-auth clear=true — nulls DB token
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_cli_auth_config_clear_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-CA-07: clear=true → DB token cleared; source falls back to env/none."""
    import app.cli_auth as cli_auth_mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    old_token = "sk-ant-oat01-OLD-TOKEN-CLEAR-TEST"
    original_token = cli_auth_mod._cli_auth_config_cache.get_token()

    state_row = _make_vault_state_row(cli_oauth_token=old_token)
    mock_session = _make_db_session_mock(state_row)

    try:
        await cli_auth_mod._cli_auth_config_cache.load(old_token)

        with patch("app.main.get_session", return_value=mock_session):
            async with await _make_client() as client:
                resp = await client.put("/provider/cli-auth", json={"clear": True})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # After clear, no DB token; no env → none/unconfigured
    assert body["token_source"] == "none"
    assert body["token_configured"] is False
    assert body["auth_mode"] == "unconfigured"
    # CRITICAL: old token value NEVER in response
    assert old_token not in resp.text


# ─────────────────────────────────────────────────────────────────────────────
# 8. Token value NEVER in GET response — grep-equivalent
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cli_auth_config_response_never_contains_token_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CA-08: SECURITY — No token value appears anywhere in GET /provider/cli-auth response."""
    import app.cli_auth as cli_auth_mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    sentinel_tokens = [
        "sk-ant-oat01-SENTINEL-ALPHA-7391-MUST-NOT-APPEAR",
        "sk-ant-oat01-SENTINEL-BRAVO-0042-MUST-NOT-APPEAR",
        "sk-ant-oat01-SENTINEL-CHARLIE-9999-MUST-NOT-APPEAR",
    ]

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    results: list[tuple[str, str]] = []

    try:
        for sentinel in sentinel_tokens:
            await cli_auth_mod._cli_auth_config_cache.load(sentinel)
            async with await _make_client() as client:
                resp = await client.get("/provider/cli-auth")
            results.append((sentinel, resp.text))
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    for sentinel, response_text in results:
        assert sentinel not in response_text, (
            f"SECURITY VIOLATION: Token sentinel {sentinel!r} appeared in GET "
            f"/provider/cli-auth response: {response_text!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 9. Token value NEVER in PUT response
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cli_auth_put_response_never_contains_token_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CA-09 (W7 updated): PUT /provider/cli-auth response never returns the token value.
    SYNAPSE_SECRET_KEY required for the PUT to succeed (W7).
    """
    import app.cli_auth as cli_auth_mod
    from cryptography.fernet import Fernet as _Fernet

    master_key = _Fernet.generate_key().decode()
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", master_key)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    pasted = "sk-ant-oat01-PUT-RESPONSE-NO-LEAK-SENTINEL-1234"
    original_token = cli_auth_mod._cli_auth_config_cache.get_token()

    class _CapturingStateNoLeak:
        vault_id = "test-cli-auth-no-leak"
        data_version = 0
        remote_mcp_enabled = False
        mcp_access_token_hash = None
        mcp_allow_without_token = False
        clip_enabled_db = None
        clip_access_token = None
        clip_allowed_origins_db = None
        cli_oauth_token: str | None = None
        cli_oauth_token_encrypted: bytes | None = None
        updated_at = None

    def make_session() -> Any:
        cs = _CapturingStateNoLeak()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = cs
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        with patch("app.main.get_session", side_effect=make_session):
            async with await _make_client() as client:
                resp = await client.put("/provider/cli-auth", json={"token": pasted})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    assert pasted not in resp.text, (
        f"SECURITY VIOLATION: token value appeared in PUT /provider/cli-auth response: "
        f"{resp.text!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 10. clear wins over token when both sent
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_cli_auth_config_clear_wins_over_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CA-10 (W7 updated): clear=true wins when both clear and token are sent.
    Both cli_oauth_token_encrypted and cli_oauth_token (legacy) are set to NULL.
    """
    import app.cli_auth as cli_auth_mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    # Key can be set or unset — clear must always work without the master key.
    monkeypatch.delenv("SYNAPSE_SECRET_KEY", raising=False)

    old_token = "sk-ant-oat01-OLD-CLEAR-WINS-TEST"
    original_token = cli_auth_mod._cli_auth_config_cache.get_token()

    class _CapturingStateClear:
        vault_id = "test-cli-auth-clear-wins"
        data_version = 0
        remote_mcp_enabled = False
        mcp_access_token_hash = None
        mcp_allow_without_token = False
        clip_enabled_db = None
        clip_access_token = None
        clip_allowed_origins_db = None
        cli_oauth_token: str | None = old_token
        cli_oauth_token_encrypted: bytes | None = b"fakeciphertext"
        updated_at = None

    captured_enc: list[bytes | None] = []
    captured_leg: list[str | None] = []

    def make_session() -> Any:
        cs = _CapturingStateClear()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = cs
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)

        def _capture(*_args: Any) -> None:
            captured_enc.append(cs.cli_oauth_token_encrypted)
            captured_leg.append(cs.cli_oauth_token)

        ctx.__aexit__ = AsyncMock(side_effect=_capture)
        return ctx

    try:
        await cli_auth_mod._cli_auth_config_cache.load(old_token)
        with patch("app.main.get_session", side_effect=make_session):
            async with await _make_client() as client:
                resp = await client.put(
                    "/provider/cli-auth",
                    json={"token": "sk-ant-oat01-NEW-TOKEN-SHOULD-NOT-WIN", "clear": True},
                )
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # clear wins → none/unconfigured
    assert body["token_source"] == "none"
    # W7: both columns must be NULL after clear.
    assert (
        captured_enc[0] is None
    ), f"clear should have set cli_oauth_token_encrypted=None, got {captured_enc[0]!r}"
    assert (
        captured_leg[0] is None
    ), f"clear should have set cli_oauth_token=None, got {captured_leg[0]!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 11–14. Validation tests
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_cli_auth_config_empty_token_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-CA-11: Empty/whitespace-only token → 422."""
    import app.cli_auth as cli_auth_mod

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        async with await _make_client() as client:
            resp_empty = await client.put("/provider/cli-auth", json={"token": ""})
            resp_ws = await client.put("/provider/cli-auth", json={"token": "   "})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp_empty.status_code == 422, f"Empty token should be 422, got {resp_empty.status_code}"
    assert (
        resp_ws.status_code == 422
    ), f"Whitespace-only token should be 422, got {resp_ws.status_code}"


@pytest.mark.asyncio
async def test_put_cli_auth_config_too_short_token_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-CA-12: Token shorter than 20 chars → 422."""
    import app.cli_auth as cli_auth_mod

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    short_token = "sk-ant-oat01-short"  # 18 chars
    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        async with await _make_client() as client:
            resp = await client.put("/provider/cli-auth", json={"token": short_token})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert (
        resp.status_code == 422
    ), f"Short token should be 422, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_put_cli_auth_config_too_long_token_422(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-CA-13: Token longer than 500 chars → 422."""
    import app.cli_auth as cli_auth_mod

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    long_token = "sk-ant-oat01-" + "x" * 490  # 503 chars
    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        async with await _make_client() as client:
            resp = await client.put("/provider/cli-auth", json={"token": long_token})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert (
        resp.status_code == 422
    ), f"Too-long token should be 422, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_put_cli_auth_config_no_prefix_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-CA-14 (W7 updated): Token without sk-ant-oat01- prefix is accepted (soft check).
    Requires SYNAPSE_SECRET_KEY (W7) — set a dummy key so encryption can succeed.
    """
    import app.cli_auth as cli_auth_mod
    from cryptography.fernet import Fernet as _Fernet

    master_key = _Fernet.generate_key().decode()
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", master_key)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    # A plausible token without the canonical prefix (future-proofing).
    no_prefix_token = "eyJhbGciOiJSUzI1NiIsImtpZCI6ImZha2Vfa2V5In0.some_valid_looking_payload"
    original_token = cli_auth_mod._cli_auth_config_cache.get_token()

    class _CapturingStateNoPrefix:
        vault_id = "test-cli-auth-no-prefix"
        data_version = 0
        remote_mcp_enabled = False
        mcp_access_token_hash = None
        mcp_allow_without_token = False
        clip_enabled_db = None
        clip_access_token = None
        clip_allowed_origins_db = None
        cli_oauth_token: str | None = None
        cli_oauth_token_encrypted: bytes | None = None
        updated_at = None

    def make_session() -> Any:
        cs = _CapturingStateNoPrefix()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = cs
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        with patch("app.main.get_session", side_effect=make_session):
            async with await _make_client() as client:
                resp = await client.put("/provider/cli-auth", json={"token": no_prefix_token})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    # Soft check: accepted (200), not rejected (422).
    assert (
        resp.status_code == 200
    ), f"Token without prefix should be accepted (soft check), got {resp.status_code}: {resp.text}"
    assert no_prefix_token not in resp.text, "Soft-check token value must not appear in response"


# ─────────────────────────────────────────────────────────────────────────────
# 15. Neither token nor clear → 400
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_cli_auth_config_empty_body_400() -> None:
    """TC-CA-15: Empty body (neither token nor clear) → 400."""
    import app.cli_auth as cli_auth_mod

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        async with await _make_client() as client:
            resp = await client.put("/provider/cli-auth", json={})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert (
        resp.status_code == 400
    ), f"Empty body should return 400, got {resp.status_code}: {resp.text}"


# ─────────────────────────────────────────────────────────────────────────────
# 16. DB token wins over env API key for auth_mode (the ADR-0043 crux)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_cli_auth_config_db_wins_over_env_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CA-16: DB token + ANTHROPIC_API_KEY in env → auth_mode='subscription' (DB wins).

    This is the crux of ADR-0043 §2.3 tier 1: a DB-set subscription token outranks
    the env API key. The response should show token_source='db' and auth_mode='subscription',
    NOT 'api-key'.
    """
    import app.cli_auth as cli_auth_mod

    db_token = "sk-ant-oat01-DB-WINS-CRUX-TEST-9999999"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-api-key-should-not-win")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        # Load DB token into cache (tier 1 — above API key tier 2).
        await cli_auth_mod._cli_auth_config_cache.load(db_token)
        async with await _make_client() as client:
            resp = await client.get("/provider/cli-auth")
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (
        body["token_source"] == "db"
    ), f"DB token must outrank env API key (ADR-0043 §2.3 tier 1). Got: {body}"
    assert (
        body["auth_mode"] == "subscription"
    ), f"DB token → auth_mode='subscription'; ANTHROPIC_API_KEY must not win. Got: {body}"
    assert db_token not in resp.text, "DB token value must never appear in response"


# ─────────────────────────────────────────────────────────────────────────────
# 17. _CliAuthConfigCache unit tests — token_source / auth_mode matrix
# ─────────────────────────────────────────────────────────────────────────────


class TestCliAuthCacheTokenSourceResolution:
    """Unit tests for _CliAuthConfigCache.token_source() / auth_mode() (ADR-0043 §2.5)."""

    @pytest.mark.asyncio
    async def test_db_token_is_db_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.cli_auth import _CliAuthConfigCache

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

        cache = _CliAuthConfigCache()
        await cache.load("sk-ant-oat01-some-db-token-of-adequate-length")
        assert cache.token_source() == "db"
        assert cache.token_configured() is True
        assert cache.auth_mode() == "subscription"

    @pytest.mark.asyncio
    async def test_no_db_api_key_env_is_env_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.cli_auth import _CliAuthConfigCache

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-key")
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

        cache = _CliAuthConfigCache()
        await cache.load(None)
        assert cache.token_source() == "env"
        assert cache.token_configured() is True
        assert cache.auth_mode() == "api-key"

    @pytest.mark.asyncio
    async def test_no_db_oauth_token_env_is_env_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.cli_auth import _CliAuthConfigCache

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-env-tok")
        monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

        cache = _CliAuthConfigCache()
        await cache.load(None)
        assert cache.token_source() == "env"
        assert cache.auth_mode() == "subscription"

    @pytest.mark.asyncio
    async def test_no_db_use_sub_env_is_env_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.cli_auth import _CliAuthConfigCache

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", "yes")

        cache = _CliAuthConfigCache()
        await cache.load(None)
        assert cache.token_source() == "env"
        assert cache.auth_mode() == "subscription"

    @pytest.mark.asyncio
    async def test_no_db_no_env_is_none_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.cli_auth import _CliAuthConfigCache

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

        cache = _CliAuthConfigCache()
        await cache.load(None)
        assert cache.token_source() == "none"
        assert cache.token_configured() is False
        assert cache.auth_mode() == "unconfigured"

    @pytest.mark.asyncio
    async def test_db_token_overrides_env_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DB token (tier 1) outranks ANTHROPIC_API_KEY (tier 2) — ADR-0043 §2.3 crux."""
        from app.cli_auth import _CliAuthConfigCache

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-api-env-key-should-lose")
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

        cache = _CliAuthConfigCache()
        await cache.load("sk-ant-oat01-DB-TOKEN-WINS-CRUX-12345")
        assert cache.token_source() == "db"
        assert cache.auth_mode() == "subscription"
        # get_token() must return the DB token (for injection by cli.py).
        tok = cache.get_token()
        assert tok is not None and tok.startswith("sk-ant-oat01-")

    @pytest.mark.asyncio
    async def test_empty_string_token_treated_as_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty DB token (empty string) is treated as None — ADR-0043 §2.3 / ADR-0042 rule."""
        from app.cli_auth import _CliAuthConfigCache

        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

        cache = _CliAuthConfigCache()
        await cache.load("")  # empty string = unset
        assert cache.get_token() is None
        assert cache.token_source() == "none"


# ─────────────────────────────────────────────────────────────────────────────
# 18. set_token refreshes cache; resolve_subscription_token returns token
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_token_and_resolve_subscription_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CA-18: set_token updates singleton; resolve_subscription_token returns it."""
    import app.cli_auth as cli_auth_mod

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    test_token = "sk-ant-oat01-RESOLVE-TEST-TOKEN-12345"
    try:
        await cli_auth_mod._cli_auth_config_cache.set_token(test_token)
        resolved = cli_auth_mod.resolve_subscription_token()
        assert (
            resolved == test_token
        ), f"resolve_subscription_token() must return the set token; got {resolved!r}"
    finally:
        await cli_auth_mod._cli_auth_config_cache.set_token(original_token)


@pytest.mark.asyncio
async def test_resolve_subscription_token_none_when_no_db_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CA-18b: resolve_subscription_token() returns None when no DB token is set."""
    import app.cli_auth as cli_auth_mod

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        resolved = cli_auth_mod.resolve_subscription_token()
        assert resolved is None
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)


# ─────────────────────────────────────────────────────────────────────────────
# 19. Empty DB token treated as unset
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_db_token_treated_as_none() -> None:
    """TC-CA-19: Empty string in vault_state.cli_oauth_token → cache treats as None."""
    import app.cli_auth as cli_auth_mod

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        await cli_auth_mod._cli_auth_config_cache.load("")
        assert cli_auth_mod._cli_auth_config_cache.get_token() is None
        assert cli_auth_mod.resolve_subscription_token() is None
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)


# ─────────────────────────────────────────────────────────────────────────────
# 20. Response shape
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_cli_auth_config_response_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """TC-CA-20: GET /provider/cli-auth returns all required fields; no token/value field."""
    import app.cli_auth as cli_auth_mod

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        async with await _make_client() as client:
            resp = await client.get("/provider/cli-auth")
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    required_fields = {"token_configured", "token_source", "auth_mode"}
    missing = required_fields - set(body.keys())
    assert not missing, f"Missing fields in GET /provider/cli-auth response: {missing}"
    assert isinstance(body["token_configured"], bool)
    assert isinstance(body["token_source"], str)
    assert body["token_source"] in ("db", "env", "none")
    assert isinstance(body["auth_mode"], str)
    assert body["auth_mode"] in ("api-key", "subscription", "unconfigured")
    # No sensitive fields
    assert "token" not in body
    assert "cli_oauth_token" not in body
    assert "value" not in body
    assert "generated_token" not in body
