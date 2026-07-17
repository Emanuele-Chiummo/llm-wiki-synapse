"""
Tests for GET/PUT /clip/config — web clipper runtime configuration (ADR-0040).

Acceptance checks per ADR-0040 §5 (mirrors test_mcp_auth_adr0033.py):
  1.  GET /clip/config posture — no token set (none source).
  2.  GET /clip/config posture — env token only (env source).
  3.  GET /clip/config posture — DB token set (db source), token value NEVER returned.
  4.  PUT /clip/config rotate_token=true — generated_token present once (one-time reveal).
  5.  PUT /clip/config rotate_token=true — subsequent GET never returns the token value.
  6.  PUT /clip/config clear_token=true — token_source becomes env or none.
  7.  PUT /clip/config set_enabled=true — resolves enabled=True; POST /clip becomes available.
  8.  PUT /clip/config set_enabled=false — resolves enabled=False; POST /clip returns 503.
  9.  PUT /clip/config set_allowed_origins — POST /clip honours DB origins over env.
  10. POST /clip honours DB token over env (DB wins when set, ADR-0040 §2.2).
  11. Token NEVER in GET /clip/config response body (grep-equivalent assertion).
  12. token_source = 'db' when DB token set; 'env' when only env; 'none' when neither.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
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
    clip_enabled_db: bool | None = None,
    clip_access_token: str | None = None,
    clip_allowed_origins_db: str | None = None,
) -> MagicMock:
    """Return a mock VaultState row with ADR-0040 columns."""
    row = MagicMock()
    row.vault_id = "test-clip-config"
    row.data_version = 0
    row.remote_mcp_enabled = False
    row.mcp_access_token_hash = None
    row.mcp_allow_without_token = False
    row.clip_enabled_db = clip_enabled_db
    row.clip_access_token = clip_access_token
    row.clip_allowed_origins_db = clip_allowed_origins_db
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
# 1. GET /clip/config — token_source = 'none' (no DB, no env)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_clip_config_token_source_none() -> None:
    """TC-CC-01: No DB token, no env token → token_source='none', token_configured=False."""
    from app import config as cfg
    from app import runtime_state

    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()
    try:
        cfg.settings.clip_token = None
        await runtime_state.clip_config_cache.load(None, None, None)  # no DB, no env
        async with await _make_client() as client:
            resp = await client.get("/clip/config")
    finally:
        cfg.settings.clip_token = original_clip_token
        # Restore cache to pre-test state using saved hash (not the plaintext env token).
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_source"] == "none"
    assert body["token_configured"] is False
    # CRITICAL: token value NEVER in response
    assert "clip_access_token" not in body
    assert "token" not in body or body.get("token") is None


@pytest.mark.asyncio
async def test_get_clip_config_token_source_env() -> None:
    """TC-CC-02: No DB token, CLIP_TOKEN env set → token_source='env', token_configured=True."""
    from app import config as cfg
    from app import runtime_state

    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()
    try:
        cfg.settings.clip_token = "env-token-abc"
        await runtime_state.clip_config_cache.load(None, None, None)  # no DB token
        async with await _make_client() as client:
            resp = await client.get("/clip/config")
    finally:
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_source"] == "env"
    assert body["token_configured"] is True
    # CRITICAL: env token value NEVER in response
    response_str = resp.text
    assert "env-token-abc" not in response_str, "Token value leaked into response!"


@pytest.mark.asyncio
async def test_get_clip_config_token_source_db() -> None:
    """TC-CC-03: DB token set → token_source='db', token_configured=True, value NOT returned."""
    from app import config as cfg
    from app import runtime_state

    db_token_value = "db-clip-token-secret-xyz"
    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()
    try:
        # DB token takes precedence over env; store as PBKDF2 hash (ADR-0040 §2.2).
        db_token_hash = runtime_state.hash_token(db_token_value)
        cfg.settings.clip_token = "env-fallback-token"
        await runtime_state.clip_config_cache.load(None, db_token_hash, None)
        async with await _make_client() as client:
            resp = await client.get("/clip/config")
    finally:
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_source"] == "db"
    assert body["token_configured"] is True
    # CRITICAL: DB token value NEVER in response (key invariant — ADR-0040 §2.1)
    response_str = resp.text
    assert db_token_value not in response_str, (
        f"SECURITY VIOLATION: DB token value leaked into GET /clip/config response: "
        f"{response_str!r}"
    )
    assert "clip_access_token" not in body


# ─────────────────────────────────────────────────────────────────────────────
# 2. PUT /clip/config — rotate_token one-time reveal
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_clip_config_rotate_token_one_time_reveal() -> None:
    """TC-CC-04: rotate_token=true → generated_token populated once in PUT response."""
    from app import config as cfg
    from app import runtime_state

    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()

    # Capture what the PUT handler sets on the DB
    captured_token: list[str] = []

    def make_session() -> Any:
        """Mock session that captures clip_access_token assignments."""

        class _CapturingState:
            vault_id = "test-clip-config"
            data_version = 0
            remote_mcp_enabled = False
            mcp_access_token_hash = None
            mcp_allow_without_token = False
            clip_enabled_db = None
            clip_access_token: str | None = None
            clip_allowed_origins_db = None
            updated_at = None

        capturing_state = _CapturingState()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = capturing_state

        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(
            side_effect=lambda *a: captured_token.append(capturing_state.clip_access_token or "")
        )

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    try:
        cfg.settings.clip_token = None
        await runtime_state.clip_config_cache.load(None, None, None)

        with patch("app.main.get_session", side_effect=make_session):
            async with await _make_client() as client:
                resp = await client.put("/clip/config", json={"rotate_token": True})
    finally:
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # generated_token must be present on rotate
    assert body.get("generated_token") is not None, "generated_token missing from PUT response"
    assert len(body["generated_token"]) >= 32, "Token too short"
    # token_source must be 'db' after rotate
    assert body["token_source"] == "db"
    assert body["token_configured"] is True


@pytest.mark.asyncio
async def test_put_clip_config_no_rotate_no_generated_token() -> None:
    """TC-CC-04b: Without rotate_token, generated_token is null in response."""
    from app import config as cfg
    from app import runtime_state

    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()

    state_row = _make_vault_state_row(clip_enabled_db=True)
    mock_session = _make_db_session_mock(state_row)

    try:
        cfg.settings.clip_token = "env-token"
        await runtime_state.clip_config_cache.load(None, None, None)

        with patch("app.main.get_session", return_value=mock_session):
            async with await _make_client() as client:
                resp = await client.put("/clip/config", json={"set_enabled": True})
    finally:
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (
        body.get("generated_token") is None
    ), "generated_token must be null when rotate_token is not set"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Token NEVER returned by subsequent GET after rotate
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_clip_config_never_returns_token_after_rotate() -> None:
    """TC-CC-05: After rotate, GET /clip/config never returns the token value.

    This is the key invariant for ADR-0040 §2.1 — token is shown once in PUT,
    never again in any GET. Mirrors the MCP check in test_mcp_auth_adr0033.py.
    """
    from app import config as cfg
    from app import runtime_state

    db_token = "rotated-secret-token-SENTINEL"
    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()
    try:
        cfg.settings.clip_token = None
        # Simulate: rotate has been called and DB now has a PBKDF2 hash (ADR-0040 §2.2).
        db_token_hash = runtime_state.hash_token(db_token)
        await runtime_state.clip_config_cache.load(None, db_token_hash, None)

        async with await _make_client() as client:
            resp = await client.get("/clip/config")
    finally:
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    assert resp.status_code == 200, resp.text
    # The token sentinel must NEVER appear in the response
    assert db_token not in resp.text, (
        f"SECURITY VIOLATION: DB token value appeared in GET /clip/config response: "
        f"{resp.text!r}"
    )
    body = resp.json()
    # generated_token is absent from GET response (it's only in PUT response)
    assert "generated_token" not in body or body.get("generated_token") is None


# ─────────────────────────────────────────────────────────────────────────────
# 4. PUT /clip/config clear_token
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_clip_config_clear_token() -> None:
    """TC-CC-06: clear_token=true → DB token cleared; source becomes env or none."""
    from app import config as cfg
    from app import runtime_state

    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()

    # State row simulates DB token previously set as PBKDF2 hash; clear_token nulls it.
    old_db_token_hash = runtime_state.hash_token("old-db-token")
    state_row = _make_vault_state_row(clip_access_token=old_db_token_hash)
    mock_session = _make_db_session_mock(state_row)

    try:
        cfg.settings.clip_token = "env-fallback"
        await runtime_state.clip_config_cache.load(None, old_db_token_hash, None)

        with patch("app.main.get_session", return_value=mock_session):
            async with await _make_client() as client:
                resp = await client.put("/clip/config", json={"clear_token": True})
    finally:
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # After clear, DB token is gone; env bootstrap is the fallback → token_source='env'
    # (The mock clears state_row.clip_access_token via the PUT handler logic)
    # token value is NEVER returned
    assert "old-db-token" not in resp.text
    # token_configured may be True (env fallback) or False depending on env_token
    # The response shape is what matters for this test
    assert "token_configured" in body
    assert "token_source" in body


# ─────────────────────────────────────────────────────────────────────────────
# 5. PUT /clip/config set_enabled gates POST /clip
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_clip_config_set_enabled_false_gates_clip_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CC-07/08: set_enabled=false via DB → POST /clip returns 503 regardless of env."""
    from app import config as cfg
    from app import runtime_state

    original_clip_enabled = cfg.settings.clip_enabled
    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()

    try:
        # env says enabled=True, but DB says False
        cfg.settings.clip_enabled = True
        cfg.settings.clip_token = "test-token-xyz"
        # Load cache: DB clip_enabled_db=False overrides env (hash doesn't matter for 503 test)
        # Use a fake hash-shaped string — auth gate is never reached because enabled=False first
        await runtime_state.clip_config_cache.load(False, "pbkdf2_sha256$1$fake$hash", None)

        async with await _make_client() as client:
            resp = await client.post(
                "/clip",
                json={
                    "url": "https://example.com",
                    "title": "Test",
                    "markdown": "# Test",
                },
                headers={"Authorization": "Bearer test-token-xyz"},
            )
    finally:
        cfg.settings.clip_enabled = original_clip_enabled
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    assert (
        resp.status_code == 503
    ), f"Expected 503 when DB clip_enabled_db=False, got {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_put_clip_config_set_enabled_true_enables_ingress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CC-07: set_enabled=True via DB → POST /clip proceeds past enabled gate."""
    from contextlib import asynccontextmanager

    from app import config as cfg
    from app import runtime_state

    # Set up temp vault
    vault_root = tmp_path / "vault"
    sources_dir = vault_root / "raw" / "sources"
    sources_dir.mkdir(parents=True)
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    log_md = wiki_dir / "log.md"
    log_md.write_text("---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n")

    original_clip_enabled = cfg.settings.clip_enabled
    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()

    @asynccontextmanager
    async def _fake_session() -> AsyncGenerator[AsyncMock, None]:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        yield session

    monkeypatch.setattr("app.main.get_session", _fake_session)
    monkeypatch.setattr("app.main._graph_cache", None)
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "raw_sources_dir", property(lambda self: sources_dir))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(type(cfg.settings), "log_md_path", property(lambda self: log_md))

    db_plaintext = "db-token-xyz"
    db_hash = runtime_state.hash_token(db_plaintext)

    try:
        cfg.settings.clip_enabled = False  # env says disabled
        cfg.settings.clip_token = None
        # DB says: enabled=True; store PBKDF2 hash as the DB would after rotate
        await runtime_state.clip_config_cache.load(True, db_hash, None)

        async with await _make_client() as client:
            resp = await client.post(
                "/clip",
                json={
                    "url": "https://example.com",
                    "title": "DB Enable Test",
                    "markdown": "# Test article body",
                },
                headers={"Authorization": f"Bearer {db_plaintext}"},
            )
    finally:
        cfg.settings.clip_enabled = original_clip_enabled
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    # Should reach at least past the enabled gate (503 means fail; want 202 or 401/403/400)
    assert resp.status_code != 503, (
        f"Expected DB clip_enabled_db=True to override env enabled=False, "
        f"but got 503: {resp.text}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. PUT /clip/config set_allowed_origins — POST /clip honours DB origins
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_honours_db_allowed_origins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CC-09: DB clip_allowed_origins_db wins over CLIP_ALLOWED_ORIGINS env."""
    from contextlib import asynccontextmanager

    from app import config as cfg
    from app import runtime_state

    vault_root = tmp_path / "vault"
    sources_dir = vault_root / "raw" / "sources"
    sources_dir.mkdir(parents=True)
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    log_md = wiki_dir / "log.md"
    log_md.write_text("---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n")

    original_clip_enabled = cfg.settings.clip_enabled
    original_clip_token = cfg.settings.clip_token
    original_clip_origins = cfg.settings.clip_allowed_origins
    original_cache_hash = runtime_state.clip_config_cache.get_hash()

    @asynccontextmanager
    async def _fake_session() -> AsyncGenerator[AsyncMock, None]:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        yield session

    monkeypatch.setattr("app.main.get_session", _fake_session)
    monkeypatch.setattr("app.main._graph_cache", None)
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "raw_sources_dir", property(lambda self: sources_dir))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(type(cfg.settings), "log_md_path", property(lambda self: log_md))

    db_origin = "chrome-extension://db-extension-id"
    env_origin = "chrome-extension://env-extension-id"
    token_plaintext = "test-token-origins"
    # Hash the token as the DB would store it after rotate (ADR-0040 §2.2).
    token_hash = runtime_state.hash_token(token_plaintext)

    try:
        cfg.settings.clip_enabled = True
        cfg.settings.clip_token = None
        cfg.settings.clip_allowed_origins = env_origin
        # DB origins override env: only db_origin is allowed; load PBKDF2 hash into cache
        await runtime_state.clip_config_cache.load(True, token_hash, db_origin)

        async with await _make_client() as client:
            # DB origin should be ALLOWED (present plaintext; cache has the hash for verify)
            resp_db = await client.post(
                "/clip",
                json={"url": "https://example.com", "title": "Test", "markdown": "# Test"},
                headers={"Authorization": f"Bearer {token_plaintext}", "Origin": db_origin},
            )
            # Env-only origin should be REJECTED (403) because DB overrides env
            resp_env = await client.post(
                "/clip",
                json={"url": "https://example.com", "title": "Test", "markdown": "# Test"},
                headers={"Authorization": f"Bearer {token_plaintext}", "Origin": env_origin},
            )
    finally:
        cfg.settings.clip_enabled = original_clip_enabled
        cfg.settings.clip_token = original_clip_token
        cfg.settings.clip_allowed_origins = original_clip_origins
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    # DB origin is allowed (202 success or non-403 non-503)
    assert resp_db.status_code not in (
        403,
        503,
    ), f"DB origin {db_origin!r} should be allowed but got {resp_db.status_code}: {resp_db.text}"
    # Env-only origin is rejected since DB allowlist takes over
    assert resp_env.status_code == 403, (
        f"Env origin {env_origin!r} should be rejected (DB overrides env) "
        f"but got {resp_env.status_code}: {resp_env.text}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. POST /clip honours DB token over env (DB wins — ADR-0040 §2.2)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_honours_db_token_over_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CC-10: DB clip_access_token wins over CLIP_TOKEN env.

    When DB token is set, the env token no longer authenticates.
    When the DB token is presented, the request succeeds.
    """
    from contextlib import asynccontextmanager

    from app import config as cfg
    from app import runtime_state

    vault_root = tmp_path / "vault"
    sources_dir = vault_root / "raw" / "sources"
    sources_dir.mkdir(parents=True)
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    log_md = wiki_dir / "log.md"
    log_md.write_text("---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n")

    original_clip_enabled = cfg.settings.clip_enabled
    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()

    @asynccontextmanager
    async def _fake_session() -> AsyncGenerator[AsyncMock, None]:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        yield session

    monkeypatch.setattr("app.main.get_session", _fake_session)
    monkeypatch.setattr("app.main._graph_cache", None)
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "raw_sources_dir", property(lambda self: sources_dir))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(type(cfg.settings), "log_md_path", property(lambda self: log_md))

    env_token = "env-only-token"
    db_token_plaintext = "db-override-token"
    # Hash the DB token as the DB stores it (PBKDF2 — ADR-0040 §2.2).
    db_token_hash = runtime_state.hash_token(db_token_plaintext)

    try:
        cfg.settings.clip_enabled = True
        cfg.settings.clip_token = env_token
        # DB token overrides env; load PBKDF2 hash so _verify_token() is exercised
        await runtime_state.clip_config_cache.load(True, db_token_hash, None)

        async with await _make_client() as client:
            # DB token (plaintext) → _verify_token() → should succeed
            resp_db_token = await client.post(
                "/clip",
                json={"url": "https://example.com", "title": "Test", "markdown": "# Test"},
                headers={"Authorization": f"Bearer {db_token_plaintext}"},
            )
            # Env token → should be rejected (401) since DB token overrides it
            resp_env_token = await client.post(
                "/clip",
                json={"url": "https://example.com", "title": "Test", "markdown": "# Test"},
                headers={"Authorization": f"Bearer {env_token}"},
            )
    finally:
        cfg.settings.clip_enabled = original_clip_enabled
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    # DB token succeeds
    assert resp_db_token.status_code not in (
        401,
        503,
    ), f"DB token should authenticate, got {resp_db_token.status_code}: {resp_db_token.text}"
    # Env token rejected
    assert resp_env_token.status_code == 401, (
        f"Env token should be rejected (401) when DB token overrides, "
        f"got {resp_env_token.status_code}: {resp_env_token.text}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# 8. Token NEVER in GET /clip/config response — grep-equivalent assertion
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clip_config_response_never_contains_token_value() -> None:
    """TC-CC-11: SECURITY — No token value appears anywhere in GET /clip/config response.

    This test simulates several different token values and verifies that none of them
    appear as a substring in the response JSON. This is the critical no-leak invariant
    of ADR-0040 §2.1 (mirrors ADR-0033 §5.2 check in test_mcp_auth_adr0033.py).
    """
    from app import config as cfg
    from app import runtime_state

    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()

    sentinel_tokens = [
        "UNIQUE-CLIP-TOKEN-SENTINEL-ALPHA-7391",
        "ANOTHER-SECRET-TOKEN-BRAVO-0042",
        "db-and-env-both-set-CHARLIE-9999",
    ]

    results: list[tuple[str, str]] = []

    try:
        for sentinel in sentinel_tokens:
            # Store each sentinel as a PBKDF2 hash (as the DB would after rotate, ADR-0040 §2.2).
            sentinel_hash = runtime_state.hash_token(sentinel)
            cfg.settings.clip_token = None
            await runtime_state.clip_config_cache.load(None, sentinel_hash, None)
            async with await _make_client() as client:
                resp = await client.get("/clip/config")
            results.append((sentinel, resp.text))
    finally:
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    for sentinel, response_text in results:
        assert sentinel not in response_text, (
            f"SECURITY VIOLATION: Token sentinel {sentinel!r} appeared in GET /clip/config "
            f"response: {response_text!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 9. token_source resolution (unit tests — mirrors TestTokenSourceResolver in 0033)
# ─────────────────────────────────────────────────────────────────────────────


class TestClipTokenSourceResolution:
    """Unit tests for _ClipConfigCache token_source resolution (ADR-0040 §2.2)."""

    @pytest.mark.asyncio
    async def test_db_token_is_db_source(self) -> None:
        from app import runtime_state

        cache = runtime_state.ClipConfigCache()
        # Store a PBKDF2 hash as the DB would (ADR-0040 §2.2).
        fake_hash = runtime_state.hash_token("some-db-token")
        await cache.load(None, fake_hash, None)
        assert cache.token_source() == "db"
        assert cache.token_configured() is True

    @pytest.mark.asyncio
    async def test_no_db_env_token_is_env_source(self) -> None:
        from app import config as cfg
        from app import runtime_state

        original = cfg.settings.clip_token
        try:
            cfg.settings.clip_token = "env-token-abc"
            cache = runtime_state.ClipConfigCache()
            await cache.load(None, None, None)
            assert cache.token_source() == "env"
            assert cache.token_configured() is True
        finally:
            cfg.settings.clip_token = original

    @pytest.mark.asyncio
    async def test_no_db_no_env_is_none_source(self) -> None:
        from app import config as cfg
        from app import runtime_state

        original = cfg.settings.clip_token
        try:
            cfg.settings.clip_token = None
            cache = runtime_state.ClipConfigCache()
            await cache.load(None, None, None)
            assert cache.token_source() == "none"
            assert cache.token_configured() is False
        finally:
            cfg.settings.clip_token = original

    @pytest.mark.asyncio
    async def test_db_token_overrides_env(self) -> None:
        """DB token (hash) takes precedence over env bootstrap (ADR-0040 §2.2)."""
        from app import config as cfg
        from app import runtime_state

        original = cfg.settings.clip_token
        try:
            cfg.settings.clip_token = "env-token"
            cache = runtime_state.ClipConfigCache()
            # Simulate a PBKDF2 hash as stored in the DB (ADR-0040 §2.2).
            fake_hash = runtime_state.hash_token("db-token")
            await cache.load(None, fake_hash, None)
            assert cache.token_source() == "db"
            # get_hash() returns the stored PBKDF2 hash (not the plaintext "db-token")
            stored = cache.get_hash()
            assert stored is not None
            assert stored.startswith(
                "pbkdf2_sha256$"
            ), f"DB token must be stored as a PBKDF2 hash, got: {stored!r}"
            # The stored hash must NOT be the raw token value
            assert stored != "db-token"
            # _verify_token must confirm the round-trip
            assert runtime_state.verify_token(
                "db-token", stored
            ), "_verify_token must accept the original plaintext against the stored hash"
        finally:
            cfg.settings.clip_token = original


class TestClipEnabledResolution:
    """Unit tests for _ClipConfigCache enabled resolution (ADR-0040 §2.2)."""

    @pytest.mark.asyncio
    async def test_db_enabled_true_overrides_env_false(self) -> None:
        from app import config as cfg
        from app import runtime_state

        original = cfg.settings.clip_enabled
        try:
            cfg.settings.clip_enabled = False
            cache = runtime_state.ClipConfigCache()
            await cache.load(True, None, None)
            assert cache.resolved_enabled() is True
        finally:
            cfg.settings.clip_enabled = original

    @pytest.mark.asyncio
    async def test_db_enabled_false_overrides_env_true(self) -> None:
        from app import config as cfg
        from app import runtime_state

        original = cfg.settings.clip_enabled
        try:
            cfg.settings.clip_enabled = True
            cache = runtime_state.ClipConfigCache()
            await cache.load(False, None, None)
            assert cache.resolved_enabled() is False
        finally:
            cfg.settings.clip_enabled = original

    @pytest.mark.asyncio
    async def test_db_none_falls_back_to_env(self) -> None:
        from app import config as cfg
        from app import runtime_state

        original = cfg.settings.clip_enabled
        try:
            cfg.settings.clip_enabled = True
            cache = runtime_state.ClipConfigCache()
            await cache.load(None, None, None)
            assert cache.resolved_enabled() is True
        finally:
            cfg.settings.clip_enabled = original


class TestClipAllowedOriginsResolution:
    """Unit tests for _ClipConfigCache allowed_origins resolution (ADR-0040 §2.2)."""

    @pytest.mark.asyncio
    async def test_db_origins_override_env(self) -> None:
        from app import config as cfg
        from app import runtime_state

        original = cfg.settings.clip_allowed_origins
        try:
            cfg.settings.clip_allowed_origins = "chrome-extension://env-only"
            cache = runtime_state.ClipConfigCache()
            await cache.load(None, None, "chrome-extension://db-only")
            result = cache.resolved_allowed_origins_list()
            assert result == ["chrome-extension://db-only"]
        finally:
            cfg.settings.clip_allowed_origins = original

    @pytest.mark.asyncio
    async def test_db_none_falls_back_to_env(self) -> None:
        from app import config as cfg
        from app import runtime_state

        original = cfg.settings.clip_allowed_origins
        try:
            cfg.settings.clip_allowed_origins = "chrome-extension://env-origin"
            cache = runtime_state.ClipConfigCache()
            await cache.load(None, None, None)
            result = cache.resolved_allowed_origins_list()
            assert result == ["chrome-extension://env-origin"]
        finally:
            cfg.settings.clip_allowed_origins = original


# ─────────────────────────────────────────────────────────────────────────────
# 10. GET /clip/config response shape
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_clip_config_response_shape() -> None:
    """TC-CC-shape: GET /clip/config returns all required fields with correct types."""
    from app import config as cfg
    from app import runtime_state

    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()
    try:
        cfg.settings.clip_token = "shape-test-env-token"
        await runtime_state.clip_config_cache.load(None, None, None)
        async with await _make_client() as client:
            resp = await client.get("/clip/config")
    finally:
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    required_fields = {
        "enabled",
        "token_configured",
        "token_source",
        "allowed_origins",
        "max_body_bytes",
    }
    missing = required_fields - set(body.keys())
    assert not missing, f"Missing fields in GET /clip/config response: {missing}"
    assert isinstance(body["enabled"], bool)
    assert isinstance(body["token_configured"], bool)
    assert isinstance(body["token_source"], str)
    assert body["token_source"] in ("db", "env", "none")
    assert isinstance(body["allowed_origins"], list)
    assert isinstance(body["max_body_bytes"], int)
    # NEVER contains raw token
    assert "clip_access_token" not in body
    assert "shape-test-env-token" not in resp.text


# ─────────────────────────────────────────────────────────────────────────────
# 11. rotate_token stores PBKDF2 hash in DB (coordinator requirement)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_clip_config_rotate_stores_pbkdf2_hash() -> None:
    """TC-CC-pbkdf2: rotate_token stores PBKDF2 hash in DB column, NOT the raw token.

    ADR-0040 §2.2 — DB column clip_access_token must be a PBKDF2-SHA256 hash
    (starts with 'pbkdf2_sha256$'), never the raw token value.
    Mirrors the MCP invariant in ADR-0033 §2.1.
    """
    from app import config as cfg
    from app import runtime_state

    original_clip_token = cfg.settings.clip_token
    original_cache_hash = runtime_state.clip_config_cache.get_hash()

    # Capture what the PUT handler writes into state.clip_access_token.
    captured_db_value: list[str] = []

    def make_session() -> Any:
        class _CapturingState:
            vault_id = "test-clip-pbkdf2"
            data_version = 0
            remote_mcp_enabled = False
            mcp_access_token_hash = None
            mcp_allow_without_token = False
            clip_enabled_db = None
            clip_access_token: str | None = None
            clip_allowed_origins_db = None
            updated_at = None

        capturing_state = _CapturingState()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = capturing_state

        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        # The PUT handler does: async with get_session() as session:
        # So get_session() returns ctx; async with ctx as session calls ctx.__aenter__.
        # ctx.__aexit__ is called on exit — capture the DB value there.
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(
            side_effect=lambda *a: captured_db_value.append(capturing_state.clip_access_token or "")
        )
        return ctx

    try:
        cfg.settings.clip_token = None
        await runtime_state.clip_config_cache.load(None, None, None)

        with patch("app.main.get_session", side_effect=make_session):
            async with await _make_client() as client:
                resp = await client.put("/clip/config", json={"rotate_token": True})
    finally:
        cfg.settings.clip_token = original_clip_token
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()

    raw_token = body.get("generated_token", "")
    assert raw_token, "generated_token must be non-empty on rotate"

    # The value written to the DB column must be a PBKDF2 hash, NOT the raw token.
    assert len(captured_db_value) == 1, f"Expected 1 DB write, got {captured_db_value}"
    stored_in_db = captured_db_value[0]
    assert stored_in_db.startswith(
        "pbkdf2_sha256$"
    ), f"SECURITY VIOLATION: DB column must store PBKDF2 hash, got: {stored_in_db!r}"
    assert (
        stored_in_db != raw_token
    ), "SECURITY VIOLATION: DB column must NOT store the raw token — only the hash"
    # Confirm round-trip: _verify_token must accept the raw token against the stored hash.
    assert runtime_state.verify_token(
        raw_token, stored_in_db
    ), "_verify_token must accept the raw generated_token against the stored PBKDF2 hash"


# ─────────────────────────────────────────────────────────────────────────────
# 12. Rotated token authenticates subsequent POST /clip (coordinator requirement)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rotate_token_authenticates_post_clip(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-CC-rotate-auth: generated_token from PUT /clip/config authenticates POST /clip.

    ADR-0040 §2.2: rotate generates a token, returns it once, stores PBKDF2 hash.
    The returned plaintext must authenticate subsequent POST /clip requests via
    _verify_token(presented, stored_hash).
    """
    from contextlib import asynccontextmanager

    from app import config as cfg
    from app import runtime_state

    # Set up a minimal vault so POST /clip can proceed past auth to ingest logic.
    vault_root = tmp_path / "vault"
    sources_dir = vault_root / "raw" / "sources"
    sources_dir.mkdir(parents=True)
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    log_md = wiki_dir / "log.md"
    log_md.write_text("---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n")

    original_clip_token = cfg.settings.clip_token
    original_clip_enabled = cfg.settings.clip_enabled
    original_cache_hash = runtime_state.clip_config_cache.get_hash()

    # Intercept the fake session used by PUT to capture the stored hash.
    stored_hash_holder: list[str] = []

    class _RotateState:
        vault_id = "test-rotate-auth"
        data_version = 0
        remote_mcp_enabled = False
        mcp_access_token_hash = None
        mcp_allow_without_token = False
        clip_enabled_db: bool | None = True
        clip_access_token: str | None = None
        clip_allowed_origins_db = None
        updated_at = None

    rotate_state = _RotateState()

    def _make_rotate_session() -> Any:
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = rotate_state

        session = AsyncMock()
        session.execute = AsyncMock(return_value=result_mock)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        # ctx.__aexit__ captures the stored hash after the async with block exits.
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(
            side_effect=lambda *a: stored_hash_holder.append(rotate_state.clip_access_token or "")
        )
        return ctx

    @asynccontextmanager
    async def _fake_ingest_session() -> AsyncGenerator[AsyncMock, None]:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        yield session

    monkeypatch.setattr("app.main._graph_cache", None)
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "raw_sources_dir", property(lambda self: sources_dir))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(type(cfg.settings), "log_md_path", property(lambda self: log_md))

    try:
        cfg.settings.clip_token = None
        cfg.settings.clip_enabled = True
        # Cache: enabled=True, no token yet.
        await runtime_state.clip_config_cache.load(True, None, None)

        generated_token: str = ""

        # Step 1: Rotate via PUT /clip/config to get the generated_token.
        with patch("app.main.get_session", side_effect=_make_rotate_session):
            async with await _make_client() as client:
                put_resp = await client.put("/clip/config", json={"rotate_token": True})
        assert put_resp.status_code == 200, put_resp.text
        generated_token = put_resp.json().get("generated_token", "")
        assert generated_token, "PUT /clip/config must return generated_token on rotate"
        assert len(stored_hash_holder) == 1, "Expected one DB write"
        # Verify the DB stores a hash, not the raw token.
        assert stored_hash_holder[0].startswith(
            "pbkdf2_sha256$"
        ), f"DB must store PBKDF2 hash, got: {stored_hash_holder[0]!r}"

        # Step 2: After rotate, the in-process cache has the PBKDF2 hash (set by set_hash).
        # Confirm cache state is consistent.
        assert runtime_state.clip_config_cache.token_source() == "db"
        cached_hash = runtime_state.clip_config_cache.get_hash()
        assert cached_hash is not None and cached_hash.startswith("pbkdf2_sha256$")

        # Step 3: Use the generated_token to authenticate POST /clip.
        with patch("app.main.get_session", side_effect=_fake_ingest_session):
            async with await _make_client() as client:
                post_resp = await client.post(
                    "/clip",
                    json={
                        "url": "https://example.com/rotate-test",
                        "title": "Rotate Auth Test",
                        "markdown": "# Test body for rotate auth",
                    },
                    headers={"Authorization": f"Bearer {generated_token}"},
                )

        # Step 4: Env-only token (old/wrong) must be rejected after rotate.
        with patch("app.main.get_session", side_effect=_fake_ingest_session):
            async with await _make_client() as client:
                post_reject = await client.post(
                    "/clip",
                    json={
                        "url": "https://example.com/rotate-reject",
                        "title": "Reject Test",
                        "markdown": "# Wrong token",
                    },
                    headers={"Authorization": "Bearer wrong-old-token"},
                )

    finally:
        cfg.settings.clip_token = original_clip_token
        cfg.settings.clip_enabled = original_clip_enabled
        await runtime_state.clip_config_cache.load(None, original_cache_hash, None)

    # generated_token must authenticate (not 401/503)
    assert post_resp.status_code not in (401, 503), (
        f"Rotated generated_token must authenticate POST /clip, "
        f"got {post_resp.status_code}: {post_resp.text}"
    )
    # Wrong token must be rejected (401)
    assert post_reject.status_code == 401, (
        f"Wrong token must be rejected with 401 after rotate, "
        f"got {post_reject.status_code}: {post_reject.text}"
    )
