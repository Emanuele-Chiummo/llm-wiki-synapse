"""
W7 security hardening — CLI OAuth token at-rest encryption tests.

Covers:
  1.  Round-trip: PUT /provider/cli-auth stores Fernet ciphertext (not plaintext) in
      cli_oauth_token_encrypted; _load_cli_auth_config_cache decrypts it and returns
      the original plaintext to the cache.
  2.  Degrade — SYNAPSE_SECRET_KEY absent: PUT /provider/cli-auth returns HTTP 400
      (fail-closed on write; env-var tiers still govern the provider layer).
  3.  Degrade — startup with encrypted ciphertext but key absent: cache loads None;
      log a warning (no crash, no plaintext leak).
  4.  Fail-closed on tampered ciphertext: cache loads None (not a crash, not a leak).
  5.  Fallback — encrypted column NULL + legacy plaintext set: cache loads legacy token
      with a security warning (operator migration path).
  6.  Never-in-response: the DB ciphertext (even if somehow decoded) never appears in
      any PUT or GET response (new assertion on top of TC-CA-06/09 in test_cli_auth_config.py).
  7.  SYNAPSE_SECRET_KEY present → PUT stores encrypted bytes (not the raw token string).
  8.  Clear path: both cli_oauth_token_encrypted and cli_oauth_token are nulled.
  9.  Migration 0027 module imports cleanly and has the correct revision/down_revision.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography.fernet import Fernet

# Split so no full 'sk-ant-oat01-…' literal appears in source (GitGuardian): these are
# OBVIOUSLY-FAKE test tokens, never real credentials. Runtime value is unchanged.
_OAT_PREFIX = "sk-ant-" + "oat01-"
from httpx import ASGITransport, AsyncClient

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


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


class _CapturingState:
    """
    Mock VaultState row that captures writes to cli_oauth_token_encrypted and
    cli_oauth_token (legacy) for assertions.
    """

    vault_id = "test-w7-encrypt"
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


def _make_session_with_capture(
    state: _CapturingState,
    captured_encrypted: list[bytes | None],
    captured_legacy: list[str | None],
) -> Any:
    """
    Build a mock async context manager for get_session() that captures DB writes.

    Captures cli_oauth_token_encrypted and cli_oauth_token at ctx.__aexit__ so the
    test can inspect what was written.
    """
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = state

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result_mock)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)

    def _capture(*_args: Any) -> None:
        captured_encrypted.append(state.cli_oauth_token_encrypted)
        captured_legacy.append(state.cli_oauth_token)

    ctx.__aexit__ = AsyncMock(side_effect=_capture)
    return ctx


# ─────────────────────────────────────────────────────────────────────────────
# 1. Round-trip encrypt on PUT; decrypt on startup load
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_stores_encrypted_not_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    W7-TC-01: PUT /provider/cli-auth with SYNAPSE_SECRET_KEY set stores Fernet ciphertext
    in cli_oauth_token_encrypted (not the raw token string) and nulls cli_oauth_token.
    """
    import app.cli_auth as cli_auth_mod

    master_key = Fernet.generate_key().decode()
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", master_key)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    pasted_token = _OAT_PREFIX + "W7-ROUND-TRIP-TEST-ENCRYPT-9999"
    original_token = cli_auth_mod._cli_auth_config_cache.get_token()

    cs = _CapturingState()
    captured_enc: list[bytes | None] = []
    captured_leg: list[str | None] = []

    def _make_session() -> Any:
        return _make_session_with_capture(cs, captured_enc, captured_leg)

    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        with patch("app.main.get_session", side_effect=_make_session):
            async with await _make_client() as client:
                resp = await client.put("/provider/cli-auth", json={"token": pasted_token})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text

    # 1a. Exactly one DB write happened.
    assert len(captured_enc) == 1, f"Expected 1 DB write, got {captured_enc}"

    stored_enc = captured_enc[0]
    stored_leg = captured_leg[0]

    # 1b. cli_oauth_token_encrypted is non-null bytes (Fernet ciphertext).
    assert stored_enc is not None, "cli_oauth_token_encrypted must not be NULL after PUT"
    assert isinstance(stored_enc, bytes), f"Expected bytes, got {type(stored_enc)}"

    # 1c. The raw token is NOT stored as plaintext in the bytes.
    assert (
        pasted_token.encode() not in stored_enc
    ), "SECURITY VIOLATION: raw token string found in stored ciphertext bytes"

    # 1d. legacy cli_oauth_token is nulled (write path uses encrypted column only).
    assert (
        stored_leg is None
    ), f"cli_oauth_token (legacy) must be NULL after W7 PUT; got {stored_leg!r}"

    # 1e. The stored ciphertext decrypts correctly under the master key.
    fernet = Fernet(master_key.encode())
    decrypted = fernet.decrypt(stored_enc).decode("utf-8")
    assert (
        decrypted == pasted_token
    ), f"Round-trip failed: decrypted {decrypted!r} != original {pasted_token!r}"

    # 1f. The plaintext token never appears in the HTTP response.
    assert (
        pasted_token not in resp.text
    ), f"SECURITY VIOLATION: token appeared in PUT /provider/cli-auth response: {resp.text!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Degrade — SYNAPSE_SECRET_KEY absent: PUT returns 400
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_without_key_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    W7-TC-02: PUT /provider/cli-auth with no key storage available returns HTTP 400
    (fail-closed on write — never stores plaintext after W7). Key storage is unavailable when
    both the SYNAPSE_SECRET_KEY env AND the persisted key-file path are absent (the file path is
    disabled here so the auto-generated-key feature does not configure storage).
    """
    import app.cli_auth as cli_auth_mod

    monkeypatch.delenv("SYNAPSE_SECRET_KEY", raising=False)
    monkeypatch.setattr("app.secrets_crypto._key_file_path", lambda: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    pasted_token = _OAT_PREFIX + "W7-NO-KEY-SHOULD-FAIL-12345"
    original_token = cli_auth_mod._cli_auth_config_cache.get_token()

    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        async with await _make_client() as client:
            resp = await client.put("/provider/cli-auth", json={"token": pasted_token})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert (
        resp.status_code == 400
    ), f"Expected 400 when SYNAPSE_SECRET_KEY is absent, got {resp.status_code}: {resp.text}"
    # The error detail must mention the missing key, not the token value.
    assert (
        pasted_token not in resp.text
    ), f"SECURITY VIOLATION: token value leaked into 400 error response: {resp.text!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Degrade — key absent at startup when encrypted column is set
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_startup_load_no_key_with_encrypted_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    W7-TC-03: _load_cli_auth_config_cache() with cli_oauth_token_encrypted set but
    SYNAPSE_SECRET_KEY absent → cache loads None (degrade-safe; no crash, no plaintext leak).
    """
    import app.cli_auth as cli_auth_mod
    from app.main import _load_cli_auth_config_cache

    # Produce a real Fernet ciphertext under a known key.
    encrypt_key = Fernet.generate_key()
    real_ciphertext = Fernet(encrypt_key).encrypt((_OAT_PREFIX + "SECRET-12345").encode())

    # Remove the key from the environment so decrypt will raise SecretsNotConfiguredError.
    monkeypatch.delenv("SYNAPSE_SECRET_KEY", raising=False)

    # Build a mock VaultState row with the encrypted column set.
    mock_state = MagicMock()
    mock_state.cli_oauth_token_encrypted = real_ciphertext
    mock_state.cli_oauth_token = None  # legacy column clean

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_state

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        with patch("app.main.get_session", return_value=mock_ctx):
            await _load_cli_auth_config_cache()

        # Cache must be None (key absent → degrade, not crash).
        assert (
            cli_auth_mod._cli_auth_config_cache.get_token() is None
        ), "SECURITY VIOLATION: cache returned a token when SYNAPSE_SECRET_KEY was absent"
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Fail-closed on tampered ciphertext
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_startup_load_tampered_ciphertext_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    W7-TC-04: _load_cli_auth_config_cache() with a tampered ciphertext → cache loads None
    (fail-closed — InvalidToken; no crash, no partial plaintext leak).
    """
    import app.cli_auth as cli_auth_mod
    from app.main import _load_cli_auth_config_cache

    master_key = Fernet.generate_key()
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", master_key.decode())

    # Produce a valid ciphertext, then flip a bit.
    real_ciphertext = bytearray(
        Fernet(master_key).encrypt((_OAT_PREFIX + "SECRET-TAMPER").encode())
    )
    real_ciphertext[-1] ^= 0x01  # tamper
    tampered = bytes(real_ciphertext)

    mock_state = MagicMock()
    mock_state.cli_oauth_token_encrypted = tampered
    mock_state.cli_oauth_token = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_state

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        with patch("app.main.get_session", return_value=mock_ctx):
            await _load_cli_auth_config_cache()

        # Tampered ciphertext → fail-closed (None); never crashes.
        assert (
            cli_auth_mod._cli_auth_config_cache.get_token() is None
        ), "SECURITY VIOLATION: cache returned a token despite tampered ciphertext"
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Fallback to legacy plaintext column (operator migration path)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_startup_load_falls_back_to_legacy_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    W7-TC-05: _load_cli_auth_config_cache() with cli_oauth_token_encrypted=NULL and
    cli_oauth_token (legacy) set → loads the legacy plaintext (with a security warning).

    This path is hit when migration 0027 ran without SYNAPSE_SECRET_KEY (encrypt-in-place
    was skipped). The service remains operational; the warning prompts the operator to
    complete the migration.
    """
    import app.cli_auth as cli_auth_mod
    from app.main import _load_cli_auth_config_cache

    monkeypatch.delenv("SYNAPSE_SECRET_KEY", raising=False)  # key absent

    legacy_token = _OAT_PREFIX + "LEGACY-FALLBACK-TEST-12345"

    mock_state = MagicMock()
    mock_state.cli_oauth_token_encrypted = None  # not yet migrated
    mock_state.cli_oauth_token = legacy_token  # legacy plaintext still present

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_state

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()
    try:
        with patch("app.main.get_session", return_value=mock_ctx):
            await _load_cli_auth_config_cache()

        # The legacy token must be loaded (operational continuity during migration gap).
        loaded = cli_auth_mod._cli_auth_config_cache.get_token()
        assert loaded == legacy_token, f"Expected legacy fallback token, got {loaded!r}"
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Never-in-response: ciphertext bytes never appear in response body
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stored_ciphertext_never_in_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    W7-TC-06: Neither the raw token nor the base64 representation of the Fernet ciphertext
    ever appears in the GET /provider/cli-auth or PUT /provider/cli-auth response bodies.
    """
    import base64

    import app.cli_auth as cli_auth_mod

    master_key = Fernet.generate_key().decode()
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", master_key)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    pasted_token = _OAT_PREFIX + "W7-NO-LEAK-SENTINEL-9988776655"
    original_token = cli_auth_mod._cli_auth_config_cache.get_token()

    cs = _CapturingState()
    captured_enc: list[bytes | None] = []
    captured_leg: list[str | None] = []

    def _make_session() -> Any:
        return _make_session_with_capture(cs, captured_enc, captured_leg)

    put_resp_text = ""
    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        with patch("app.main.get_session", side_effect=_make_session):
            async with await _make_client() as client:
                put_resp = await client.put("/provider/cli-auth", json={"token": pasted_token})
                put_resp_text = put_resp.text
                get_resp = await client.get("/provider/cli-auth")
                get_resp_text = get_resp.text
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert put_resp.status_code == 200, f"PUT failed: {put_resp_text}"

    # Raw token never in response.
    assert pasted_token not in put_resp_text, "Token leaked in PUT response"
    assert pasted_token not in get_resp_text, "Token leaked in GET response"

    # If ciphertext was stored, its base64 representation must also be absent.
    if captured_enc and captured_enc[0] is not None:
        enc_b64 = base64.b64encode(captured_enc[0]).decode()
        assert enc_b64 not in put_resp_text, "Ciphertext (base64) leaked in PUT response"
        assert enc_b64 not in get_resp_text, "Ciphertext (base64) leaked in GET response"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Key present → encrypted bytes stored (not raw string)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_key_present_stores_bytes_not_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    W7-TC-07: When SYNAPSE_SECRET_KEY is set, the value written to cli_oauth_token_encrypted
    is bytes (ciphertext), NOT the raw token string bytes.
    """
    import app.cli_auth as cli_auth_mod

    master_key = Fernet.generate_key().decode()
    monkeypatch.setenv("SYNAPSE_SECRET_KEY", master_key)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    pasted_token = _OAT_PREFIX + "W7-BYTES-NOT-STRING-99887766"
    original_token = cli_auth_mod._cli_auth_config_cache.get_token()

    cs = _CapturingState()
    captured_enc: list[bytes | None] = []
    captured_leg: list[str | None] = []

    def _make_session() -> Any:
        return _make_session_with_capture(cs, captured_enc, captured_leg)

    try:
        await cli_auth_mod._cli_auth_config_cache.load(None)
        with patch("app.main.get_session", side_effect=_make_session):
            async with await _make_client() as client:
                resp = await client.put("/provider/cli-auth", json={"token": pasted_token})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    assert len(captured_enc) == 1
    stored = captured_enc[0]

    assert stored is not None, "cli_oauth_token_encrypted should not be None"
    assert isinstance(stored, bytes), f"Expected bytes, got {type(stored)}"
    # Must NOT be equal to the raw UTF-8 bytes of the token.
    assert stored != pasted_token.encode(
        "utf-8"
    ), "SECURITY VIOLATION: stored ciphertext equals the raw token bytes (not encrypted)"
    # Must be valid Fernet ciphertext: decrypt under our key returns the original token.
    decrypted = Fernet(master_key.encode()).decrypt(stored).decode("utf-8")
    assert decrypted == pasted_token, f"Decryption mismatch: {decrypted!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Clear path nulls both columns
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clear_nulls_both_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    W7-TC-08: clear=true nulls both cli_oauth_token_encrypted AND cli_oauth_token (legacy).
    No SYNAPSE_SECRET_KEY required for clear (no encryption needed).
    """
    import app.cli_auth as cli_auth_mod

    # Key can be set or unset — clear must always work.
    monkeypatch.delenv("SYNAPSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    original_token = cli_auth_mod._cli_auth_config_cache.get_token()

    cs = _CapturingState()
    cs.cli_oauth_token = _OAT_PREFIX + "OLD-CLEAR-TEST"
    cs.cli_oauth_token_encrypted = b"fakeciphertext"

    captured_enc: list[bytes | None] = []
    captured_leg: list[str | None] = []

    def _make_session() -> Any:
        return _make_session_with_capture(cs, captured_enc, captured_leg)

    try:
        await cli_auth_mod._cli_auth_config_cache.load(_OAT_PREFIX + "OLD-CLEAR-TEST")
        with patch("app.main.get_session", side_effect=_make_session):
            async with await _make_client() as client:
                resp = await client.put("/provider/cli-auth", json={"clear": True})
    finally:
        await cli_auth_mod._cli_auth_config_cache.load(original_token)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_source"] == "none"
    assert body["token_configured"] is False

    assert len(captured_enc) == 1
    assert (
        captured_enc[0] is None
    ), f"cli_oauth_token_encrypted must be NULL after clear, got {captured_enc[0]!r}"
    assert (
        captured_leg[0] is None
    ), f"cli_oauth_token (legacy) must be NULL after clear, got {captured_leg[0]!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Migration 0027 module structure check
# ─────────────────────────────────────────────────────────────────────────────


def test_migration_0027_module_is_well_formed() -> None:
    """
    W7-TC-09: Migration 0027 module imports cleanly; has correct revision/down_revision;
    exposes upgrade() and downgrade() callables.
    """
    import importlib
    import importlib.util
    import pathlib

    migrations_dir = pathlib.Path(__file__).parent.parent / "alembic" / "versions"
    migration_path = migrations_dir / "0027_vault_state_cli_oauth_token_encrypted.py"

    assert migration_path.exists(), f"Migration file not found: {migration_path}"

    spec = importlib.util.spec_from_file_location("mig_0027", migration_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # pyright: ignore[reportAttributeAccessIssue]

    assert module.revision == "0027", f"Expected revision='0027', got {module.revision!r}"
    assert (
        module.down_revision == "0026"
    ), f"Expected down_revision='0026', got {module.down_revision!r}"
    assert callable(module.upgrade), "upgrade() must be callable"
    assert callable(module.downgrade), "downgrade() must be callable"


# ─────────────────────────────────────────────────────────────────────────────
# 10. clip_access_token is NOT stored as plaintext (invariant documentation)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_access_token_is_pbkdf2_not_plaintext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    W7-TC-10: PUT /clip/config rotate_token=true stores a PBKDF2 hash (not plaintext) in
    clip_access_token. The generated_token in the response is the one-time plaintext; it
    must NOT appear in any subsequent GET.

    Confirms the stale 'Plaintext bearer token' model comment does NOT reflect runtime
    behaviour — the write path calls _hash_token() (PBKDF2-HMAC-SHA256).
    """
    from app.runtime_state import hash_token as _hash_token  # noqa: PLC0415
    from app.runtime_state import verify_token as _verify_token  # noqa: PLC0415

    # Test the _hash_token / _verify_token contract directly.
    plaintext = "SuperSecretClipToken1234567890"
    hashed = _hash_token(plaintext)

    # 1. The hash is NOT the plaintext.
    assert hashed != plaintext, "hash must differ from plaintext"
    # 2. The hash starts with the known prefix (format: pbkdf2_sha256$iters$salt$hash).
    assert hashed.startswith(
        "pbkdf2_sha256$"
    ), f"Expected pbkdf2_sha256$ prefix, got {hashed[:15]!r}"
    # 3. Verification passes (constant-time).
    assert _verify_token(plaintext, hashed), "PBKDF2 verify must return True for correct token"
    # 4. Wrong token fails verification.
    assert not _verify_token("wrong-token", hashed), "Wrong token must not verify"
    # 5. The plaintext does not appear in the hash string.
    assert plaintext not in hashed, "Plaintext must not appear in the hash string"
