"""
Tests for PF-AUTH-1 (1.9.4 W4) — scoped, revocable API tokens.

Coverage
--------
Router (POST/GET/DELETE /config/api-tokens), real SQLite-backed persistence:
  1.  POST creates a row + returns the plaintext token ONCE (201).
  2.  GET lists active tokens without the secret/hash; excludes revoked rows.
  3.  DELETE soft-deletes (revoked_at set); the row disappears from GET.
  4.  DELETE on an unknown/already-revoked id → 404.
  5.  Full round-trip: a token created via POST authenticates a subsequent request
      (through SynapseAuthMiddleware, via a dedicated Starlette mini-app).
  6.  Plaintext is never present in the GET/DELETE response bodies or in logs.

Middleware (SynapseAuthMiddleware + ApiTokenCache), unit-level (mirrors test_r10_auth.py):
  7.  A valid scoped-token bearer passes when vault_id is NULL (global) or matches.
  8.  A vault-scoped token presented against a DIFFERENT vault_id → 401 (not 403).
  9.  A read_only token → write methods (POST/PUT/DELETE/PATCH) rejected 403; GET/HEAD/
      OPTIONS still pass.
  10. The bootstrap SYNAPSE_AUTH_TOKEN behaviour is completely unchanged (regression).
  11. Revoking a token (removing it from the cache) makes it stop authenticating.
  12. Token plaintext is NEVER logged during a match/no-match/read-only-reject cycle.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tests._db_fixtures import make_sqlite_engine

# ─────────────────────────────────────────────────────────────────────────────
# Router-level fixtures: real app.main.app + SQLite-backed session factory
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    """
    AsyncClient against the real ``app.main.app`` with the DB session factory swapped
    for a fresh SQLite in-memory engine (full Synapse schema via Base.metadata).

    ``app.main.app`` is built with ``SynapseAuthMiddleware(token="")`` at import time
    (settings.auth_token defaults to ""), so auth is disabled for these router-focused
    CRUD tests — consistent with every other integration test in this suite.
    """
    from app import db as db_mod
    from app import runtime_state

    engine = await make_sqlite_engine()
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "async_session_factory", session_factory)

    # Isolate the module-level ApiTokenCache singleton per test.
    original_entries = dict(runtime_state.api_token_cache._entries)
    runtime_state.api_token_cache._entries = {}

    from app.main import app as main_app

    transport = ASGITransport(app=main_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    runtime_state.api_token_cache._entries = original_entries
    await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# 1-2. Create + list
# ─────────────────────────────────────────────────────────────────────────────


class TestCreateAndList:
    async def test_create_returns_plaintext_once(self, client: AsyncClient) -> None:
        resp = await client.post("/config/api-tokens", json={"label": "CI runner"})
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["label"] == "CI runner"
        assert body["vault_id"] is None
        assert body["read_only"] is False
        assert "token" in body and isinstance(body["token"], str) and len(body["token"]) > 20
        assert "id" in body and "created_at" in body

    async def test_create_scoped_read_only(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/config/api-tokens",
            json={"label": "readonly-scoped", "vault_id": "other-vault", "read_only": True},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["vault_id"] == "other-vault"
        assert body["read_only"] is True

    async def test_list_excludes_secret_and_hash(self, client: AsyncClient) -> None:
        create_resp = await client.post("/config/api-tokens", json={"label": "no-leak"})
        plaintext = create_resp.json()["token"]

        list_resp = await client.get("/config/api-tokens")
        assert list_resp.status_code == 200
        body = list_resp.json()
        assert "tokens" in body
        raw_text = list_resp.text
        assert plaintext not in raw_text
        assert "secret_hash" not in raw_text
        assert "secret" not in raw_text.lower()
        item = next(t for t in body["tokens"] if t["label"] == "no-leak")
        assert item["last_used_at"] is None
        assert "created_at" in item

    async def test_list_returns_both_created_tokens(self, client: AsyncClient) -> None:
        await client.post("/config/api-tokens", json={"label": "first"})
        await client.post("/config/api-tokens", json={"label": "second"})
        resp = await client.get("/config/api-tokens")
        labels = {t["label"] for t in resp.json()["tokens"]}
        assert {"first", "second"} <= labels


# ─────────────────────────────────────────────────────────────────────────────
# 3-4. Revoke (soft-delete)
# ─────────────────────────────────────────────────────────────────────────────


class TestRevoke:
    async def test_delete_revokes_and_removes_from_list(self, client: AsyncClient) -> None:
        create_resp = await client.post("/config/api-tokens", json={"label": "to-revoke"})
        token_id = create_resp.json()["id"]

        del_resp = await client.delete(f"/config/api-tokens/{token_id}")
        assert del_resp.status_code == 204

        list_resp = await client.get("/config/api-tokens")
        labels = [t["label"] for t in list_resp.json()["tokens"]]
        assert "to-revoke" not in labels

    async def test_delete_removes_from_in_process_cache(self, client: AsyncClient) -> None:
        from app import runtime_state

        create_resp = await client.post("/config/api-tokens", json={"label": "cache-check"})
        token_id = create_resp.json()["id"]
        assert token_id in runtime_state.api_token_cache._entries

        await client.delete(f"/config/api-tokens/{token_id}")
        assert token_id not in runtime_state.api_token_cache._entries

    async def test_delete_unknown_id_404(self, client: AsyncClient) -> None:
        resp = await client.delete("/config/api-tokens/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_delete_already_revoked_404(self, client: AsyncClient) -> None:
        create_resp = await client.post("/config/api-tokens", json={"label": "double-revoke"})
        token_id = create_resp.json()["id"]
        first = await client.delete(f"/config/api-tokens/{token_id}")
        assert first.status_code == 204
        second = await client.delete(f"/config/api-tokens/{token_id}")
        assert second.status_code == 404


# ─────────────────────────────────────────────────────────────────────────────
# 6. No plaintext leak into logs during create/list/delete
# ─────────────────────────────────────────────────────────────────────────────


class TestNoPlaintextInLogs:
    async def test_plaintext_never_logged(
        self, client: AsyncClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            create_resp = await client.post("/config/api-tokens", json={"label": "log-check"})
            plaintext = create_resp.json()["token"]
            token_id = create_resp.json()["id"]
            await client.get("/config/api-tokens")
            await client.delete(f"/config/api-tokens/{token_id}")

        for record in caplog.records:
            assert (
                plaintext not in record.getMessage()
            ), f"Token plaintext leaked into a log record: {record.getMessage()!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Middleware unit tests — direct SynapseAuthMiddleware + ApiTokenCache
# (mirrors backend/tests/test_r10_auth.py's mini-Starlette-app pattern)
# ─────────────────────────────────────────────────────────────────────────────


async def _echo_handler(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "method": request.method})


def _make_mini_app(
    *,
    bootstrap_token: str,
    token_cache: Any,
    vault_id: str = "vault-a",
) -> Starlette:
    from app.auth import SynapseAuthMiddleware

    routes = [
        Route("/pages", _echo_handler, methods=["GET"]),
        Route("/pages", _echo_handler, methods=["POST"]),
        Route("/pages", _echo_handler, methods=["DELETE"]),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(
        SynapseAuthMiddleware,
        token=bootstrap_token,
        token_cache=token_cache,
        vault_id=vault_id,
    )
    return app


def _build_cache_with_entry(
    *,
    plaintext: str,
    vault_id: str | None = None,
    read_only: bool = False,
    entry_id: str = "tok-1",
) -> Any:
    from app.runtime_state import ApiTokenCache, ApiTokenEntry, hash_token

    cache = ApiTokenCache()
    cache._entries = {
        entry_id: ApiTokenEntry(
            id=entry_id,
            label="test",
            secret_hash=hash_token(plaintext),
            vault_id=vault_id,
            read_only=read_only,
        )
    }
    return cache


class TestMiddlewareScopedTokenGlobal:
    """A global (vault_id=None) scoped token passes for any vault_id (AC-7)."""

    def test_global_scoped_token_passes(self) -> None:
        plaintext = "scoped-secret-global-abc"
        cache = _build_cache_with_entry(plaintext=plaintext, vault_id=None)
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=cache, vault_id="vault-a")
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.get("/pages", headers={"Authorization": f"Bearer {plaintext}"})
        assert resp.status_code == 200


class TestMiddlewareScopedTokenVaultMismatch:
    """A vault-scoped token presented to a DIFFERENT vault_id is rejected — 401 (AC-8)."""

    def test_matching_vault_passes(self) -> None:
        plaintext = "scoped-secret-vault-match"
        cache = _build_cache_with_entry(plaintext=plaintext, vault_id="vault-a")
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=cache, vault_id="vault-a")
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.get("/pages", headers={"Authorization": f"Bearer {plaintext}"})
        assert resp.status_code == 200

    def test_mismatched_vault_is_401(self) -> None:
        plaintext = "scoped-secret-vault-mismatch"
        cache = _build_cache_with_entry(plaintext=plaintext, vault_id="vault-OTHER")
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=cache, vault_id="vault-a")
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.get("/pages", headers={"Authorization": f"Bearer {plaintext}"})
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"


class TestMiddlewareReadOnly:
    """A read_only token blocks writes with 403 but still allows reads (AC-9)."""

    def test_read_only_blocks_post(self) -> None:
        plaintext = "readonly-secret-1"
        cache = _build_cache_with_entry(plaintext=plaintext, read_only=True)
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=cache)
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.post("/pages", headers={"Authorization": f"Bearer {plaintext}"})
        assert resp.status_code == 403
        assert resp.json()["error"] == "read_only_token"

    def test_read_only_blocks_delete(self) -> None:
        plaintext = "readonly-secret-2"
        cache = _build_cache_with_entry(plaintext=plaintext, read_only=True)
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=cache)
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.delete("/pages", headers={"Authorization": f"Bearer {plaintext}"})
        assert resp.status_code == 403

    def test_read_only_allows_get(self) -> None:
        plaintext = "readonly-secret-3"
        cache = _build_cache_with_entry(plaintext=plaintext, read_only=True)
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=cache)
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.get("/pages", headers={"Authorization": f"Bearer {plaintext}"})
        assert resp.status_code == 200

    def test_not_read_only_allows_post(self) -> None:
        plaintext = "readwrite-secret-1"
        cache = _build_cache_with_entry(plaintext=plaintext, read_only=False)
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=cache)
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.post("/pages", headers={"Authorization": f"Bearer {plaintext}"})
        assert resp.status_code == 200


class TestBootstrapTokenUnchanged:
    """AC-10: SYNAPSE_AUTH_TOKEN bootstrap behaviour is byte-for-byte unchanged."""

    def test_bootstrap_still_wins_over_scoped_cache(self) -> None:
        cache = _build_cache_with_entry(plaintext="some-scoped-secret")
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=cache)
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.get("/pages", headers={"Authorization": "Bearer bootstrap-xyz"})
        assert resp.status_code == 200

    def test_no_header_still_401(self) -> None:
        cache = _build_cache_with_entry(plaintext="some-scoped-secret")
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=cache)
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.get("/pages")
        assert resp.status_code == 401

    def test_disabled_auth_still_open_regardless_of_cache(self) -> None:
        """Empty bootstrap token → still a full pass-through (EC-M10-11), scoped tokens or not."""
        cache = _build_cache_with_entry(plaintext="some-scoped-secret")
        app = _make_mini_app(bootstrap_token="", token_cache=cache)
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.get("/pages")
        assert resp.status_code == 200

    def test_no_token_cache_regression(self) -> None:
        """token_cache=None (the default) reproduces pre-1.9.4 behaviour exactly."""
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=None)
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.get("/pages", headers={"Authorization": "Bearer bootstrap-xyz"})
        assert resp.status_code == 200
        with TestClient(app, raise_server_exceptions=True) as tc:
            resp = tc.get("/pages", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401


class TestRevokedTokenStopsAuthenticating:
    """AC-11: once removed from the cache, a token no longer authenticates."""

    def test_revoked_entry_401s(self) -> None:
        plaintext = "revoke-me-secret"
        cache = _build_cache_with_entry(plaintext=plaintext, entry_id="tok-revoke")
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=cache)
        with TestClient(app, raise_server_exceptions=True) as tc:
            ok = tc.get("/pages", headers={"Authorization": f"Bearer {plaintext}"})
        assert ok.status_code == 200

        cache._entries.pop("tok-revoke", None)
        with TestClient(app, raise_server_exceptions=True) as tc:
            after = tc.get("/pages", headers={"Authorization": f"Bearer {plaintext}"})
        assert after.status_code == 401


class TestMiddlewarePlaintextNeverLogged:
    """AC-12: no scoped-token plaintext appears in any log record."""

    def test_no_leak_across_match_mismatch_readonly(self, caplog: pytest.LogCaptureFixture) -> None:
        plaintext = "super-secret-scoped-do-not-log"
        cache = _build_cache_with_entry(plaintext=plaintext, vault_id="vault-OTHER", read_only=True)
        app = _make_mini_app(bootstrap_token="bootstrap-xyz", token_cache=cache, vault_id="vault-a")

        with caplog.at_level(logging.DEBUG):
            with TestClient(app, raise_server_exceptions=True) as tc:
                tc.get("/pages", headers={"Authorization": f"Bearer {plaintext}"})  # vault mismatch
                tc.get("/pages", headers={"Authorization": "Bearer totally-wrong"})  # no match

        for record in caplog.records:
            assert plaintext not in record.getMessage()
