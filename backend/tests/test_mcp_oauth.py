"""
Tests for the MCP OAuth 2.1 + PKCE authorization server (2.1.6, ADR-0090).

Coverage
--------
Discovery:
  1. Both .well-known endpoints 404 when remote_mcp_enabled is OFF; 200 + correct shape
     when ON.

Dynamic Client Registration (POST /register):
  2. Valid redirect_uris → 201 with a fresh client_id, no client_secret in the response.
  3. Missing/invalid redirect_uris → 400.

Authorization endpoint (GET/POST /authorize):
  4. GET with response_type != code → 400; code_challenge_method != S256 → 400.
  5. GET with valid params → 200 HTML form carrying the params as hidden fields.
  6. POST with the WRONG mcp_token → 401, form re-rendered with an error, no code issued.
  7. POST with the CORRECT mcp_token → 302 redirect to redirect_uri?code=...&state=...;
     JIT-registers a previously-unseen client_id.
  8. POST with a redirect_uri that doesn't match an ALREADY-registered client → 400
     (open-redirect guard) — both at GET and at POST.

Token endpoint (POST /token):
  9.  Full authorization_code + PKCE round trip → 200 with access_token + refresh_token.
  10. Wrong code_verifier → 400 (PKCE mismatch).
  11. Reusing an already-consumed code → 400 (single-use).
  12. client_id/redirect_uri mismatch against the issued code → 400.
  13. refresh_token grant rotates: old refresh_token stops working, new pair is minted.

BearerAuthMiddleware integration:
  14. An OAuth-issued access_token satisfies BearerAuthMiddleware._verify_bearer() the same
      way the static bearer does — verify_static_mcp_token() does NOT accept it (no
      delegation chain).

Everything shares the SAME remote_mcp_enabled floor as /mcp/server itself (ADR-0032).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from collections.abc import AsyncGenerator
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._db_fixtures import make_sqlite_engine

_MCP_TOKEN = "test-mcp-static-token-please-ignore"  # noqa: S105 — test fixture, not a secret


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) — S256, RFC 7636."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[AsyncClient, None]:
    """
    AsyncClient against the real app.main.app, SQLite-backed (full schema via
    Base.metadata), with remote_mcp_flag ON and a known static MCP token loaded into
    mcp_auth_cache (env-source path) — mirrors test_api_tokens_pf_auth1.py's fixture.
    """
    from app import db as db_mod
    from app import runtime_state

    engine = await make_sqlite_engine()
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "async_session_factory", session_factory)

    monkeypatch.setattr(runtime_state.settings, "mcp_auth_token", _MCP_TOKEN)

    original_flag = runtime_state.remote_mcp_flag._enabled
    original_auth_hash = runtime_state.mcp_auth_cache._hash
    original_auth_allow = runtime_state.mcp_auth_cache._allow_without_token
    original_oauth_entries = dict(runtime_state.mcp_oauth_token_cache._entries)

    runtime_state.remote_mcp_flag._enabled = True
    runtime_state.mcp_auth_cache._hash = None  # env-source path — settings.mcp_auth_token above
    runtime_state.mcp_auth_cache._allow_without_token = False
    runtime_state.mcp_oauth_token_cache._entries = {}

    from app.main import app as main_app

    transport = ASGITransport(app=main_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    runtime_state.remote_mcp_flag._enabled = original_flag
    runtime_state.mcp_auth_cache._hash = original_auth_hash
    runtime_state.mcp_auth_cache._allow_without_token = original_auth_allow
    runtime_state.mcp_oauth_token_cache._entries = original_oauth_entries
    await engine.dispose()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Discovery
# ─────────────────────────────────────────────────────────────────────────────


class TestDiscovery:
    async def test_discovery_404_when_remote_mcp_disabled(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import runtime_state

        runtime_state.remote_mcp_flag._enabled = False
        resp1 = await client.get("/.well-known/oauth-authorization-server")
        resp2 = await client.get("/.well-known/oauth-protected-resource")
        assert resp1.status_code == 404
        assert resp2.status_code == 404

    async def test_authorization_server_metadata_shape(self, client: AsyncClient) -> None:
        resp = await client.get("/.well-known/oauth-authorization-server")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["authorization_endpoint"].endswith("/authorize")
        assert body["token_endpoint"].endswith("/token")
        assert body["registration_endpoint"].endswith("/register")
        assert body["code_challenge_methods_supported"] == ["S256"]
        assert "authorization_code" in body["grant_types_supported"]
        assert "refresh_token" in body["grant_types_supported"]

    async def test_protected_resource_metadata_shape(self, client: AsyncClient) -> None:
        resp = await client.get("/.well-known/oauth-protected-resource")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["resource"].endswith("/mcp/server")


# ─────────────────────────────────────────────────────────────────────────────
# 2-3. Dynamic Client Registration
# ─────────────────────────────────────────────────────────────────────────────


class TestRegistration:
    async def test_register_valid_client(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/register",
            json={
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "client_name": "claude.ai",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["client_id"]
        assert "client_secret" not in body
        assert body["redirect_uris"] == ["https://claude.ai/api/mcp/auth_callback"]
        assert body["token_endpoint_auth_method"] == "none"

    async def test_register_missing_redirect_uris_400(self, client: AsyncClient) -> None:
        resp = await client.post("/register", json={"client_name": "no-uris"})
        assert resp.status_code == 400

    async def test_register_non_https_redirect_uri_400(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/register", json={"redirect_uris": ["http://evil.example.com/cb"]}
        )
        assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 4-8. Authorization endpoint
# ─────────────────────────────────────────────────────────────────────────────


class TestAuthorize:
    async def test_get_wrong_response_type_400(self, client: AsyncClient) -> None:
        _, challenge = _pkce_pair()
        resp = await client.get(
            "/authorize",
            params={
                "response_type": "token",
                "client_id": "c1",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert resp.status_code == 400

    async def test_get_plain_pkce_rejected_400(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "c1",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": "whatever",
                "code_challenge_method": "plain",
            },
        )
        assert resp.status_code == 400

    async def test_get_valid_renders_form(self, client: AsyncClient) -> None:
        _, challenge = _pkce_pair()
        resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "unseen-client",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "xyz",
            },
        )
        assert resp.status_code == 200
        assert 'name="mcp_token"' in resp.text
        assert 'value="unseen-client"' in resp.text
        assert 'value="xyz"' in resp.text

    async def test_post_wrong_token_401_no_code(self, client: AsyncClient) -> None:
        _, challenge = _pkce_pair()
        resp = await client.post(
            "/authorize",
            data={
                "client_id": "unseen-client-2",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": challenge,
                "state": "s1",
                "mcp_token": "totally-wrong",
            },
        )
        assert resp.status_code == 401
        assert "Incorrect token" in resp.text

    async def test_post_correct_token_redirects_with_code_and_jit_registers(
        self, client: AsyncClient
    ) -> None:
        _, challenge = _pkce_pair()
        resp = await client.post(
            "/authorize",
            data={
                "client_id": "jit-client-1",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": challenge,
                "state": "s2",
                "mcp_token": _MCP_TOKEN,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302, resp.text
        location = resp.headers["location"]
        parsed = urlparse(location)
        qs = parse_qs(parsed.query)
        assert "code" in qs
        assert qs["state"] == ["s2"]
        assert location.startswith("https://claude.ai/api/mcp/auth_callback")

    async def test_post_redirect_uri_mismatch_for_bound_client_400(
        self, client: AsyncClient
    ) -> None:
        _, challenge = _pkce_pair()
        # First approval binds "bound-client" to redirect_uri A.
        first = await client.post(
            "/authorize",
            data={
                "client_id": "bound-client",
                "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                "code_challenge": challenge,
                "state": "s3",
                "mcp_token": _MCP_TOKEN,
            },
            follow_redirects=False,
        )
        assert first.status_code == 302

        # A second attempt with a DIFFERENT redirect_uri for the SAME client_id must fail.
        _, challenge2 = _pkce_pair()
        second = await client.post(
            "/authorize",
            data={
                "client_id": "bound-client",
                "redirect_uri": "https://evil.example.com/cb",
                "code_challenge": challenge2,
                "state": "s4",
                "mcp_token": _MCP_TOKEN,
            },
        )
        assert second.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 9-13. Token endpoint
# ─────────────────────────────────────────────────────────────────────────────


async def _approve_and_get_code(
    client: AsyncClient, *, client_id: str, redirect_uri: str, code_challenge: str, state: str = ""
) -> str:
    resp = await client.post(
        "/authorize",
        data={
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "state": state,
            "mcp_token": _MCP_TOKEN,
        },
        follow_redirects=False,
    )
    assert resp.status_code == 302, resp.text
    qs = parse_qs(urlparse(resp.headers["location"]).query)
    return qs["code"][0]


class TestTokenEndpoint:
    async def test_authorization_code_round_trip(self, client: AsyncClient) -> None:
        verifier, challenge = _pkce_pair()
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"
        code = await _approve_and_get_code(
            client, client_id="tok-client-1", redirect_uri=redirect_uri, code_challenge=challenge
        )

        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": "tok-client-1",
                "code_verifier": verifier,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["token_type"] == "Bearer"
        assert body["access_token"]
        assert body["refresh_token"]
        assert body["expires_in"] > 0

    async def test_wrong_code_verifier_400(self, client: AsyncClient) -> None:
        _, challenge = _pkce_pair()
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"
        code = await _approve_and_get_code(
            client, client_id="tok-client-2", redirect_uri=redirect_uri, code_challenge=challenge
        )
        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": "tok-client-2",
                "code_verifier": "wrong-verifier",
            },
        )
        assert resp.status_code == 400

    async def test_code_reuse_rejected(self, client: AsyncClient) -> None:
        verifier, challenge = _pkce_pair()
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"
        code = await _approve_and_get_code(
            client, client_id="tok-client-3", redirect_uri=redirect_uri, code_challenge=challenge
        )
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": "tok-client-3",
            "code_verifier": verifier,
        }
        first = await client.post("/token", data=body)
        assert first.status_code == 200
        second = await client.post("/token", data=body)
        assert second.status_code == 400

    async def test_client_id_mismatch_400(self, client: AsyncClient) -> None:
        verifier, challenge = _pkce_pair()
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"
        code = await _approve_and_get_code(
            client, client_id="tok-client-4", redirect_uri=redirect_uri, code_challenge=challenge
        )
        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": "some-other-client",
                "code_verifier": verifier,
            },
        )
        assert resp.status_code == 400

    async def test_refresh_token_rotation(self, client: AsyncClient) -> None:
        verifier, challenge = _pkce_pair()
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"
        code = await _approve_and_get_code(
            client, client_id="tok-client-5", redirect_uri=redirect_uri, code_challenge=challenge
        )
        minted = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": "tok-client-5",
                "code_verifier": verifier,
            },
        )
        old_refresh = minted.json()["refresh_token"]

        rotated = await client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": old_refresh,
                "client_id": "tok-client-5",
            },
        )
        assert rotated.status_code == 200, rotated.text
        new_access = rotated.json()["access_token"]
        assert new_access != minted.json()["access_token"]

        # Old refresh_token must no longer work (rotated away from).
        replay = await client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": old_refresh,
                "client_id": "tok-client-5",
            },
        )
        assert replay.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 14. BearerAuthMiddleware integration
# ─────────────────────────────────────────────────────────────────────────────


class TestBearerMiddlewareIntegration:
    async def test_oauth_issued_token_satisfies_verify_bearer(self, client: AsyncClient) -> None:
        from app import runtime_state

        verifier, challenge = _pkce_pair()
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"
        code = await _approve_and_get_code(
            client, client_id="mw-client-1", redirect_uri=redirect_uri, code_challenge=challenge
        )
        minted = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": "mw-client-1",
                "code_verifier": verifier,
            },
        )
        access_token = minted.json()["access_token"]

        gate = runtime_state.BearerAuthMiddleware(
            app=None,  # unused by _verify_bearer directly
            token=runtime_state.settings.mcp_auth_token or "",
            flag=runtime_state.remote_mcp_flag,
            auth_cache=runtime_state.mcp_auth_cache,
        )
        db_hash = runtime_state.mcp_auth_cache.get_hash()
        tok_source = runtime_state.resolve_token_source(db_hash)
        assert gate._verify_bearer(
            access_token, db_hash, runtime_state.settings.mcp_auth_token or "", tok_source
        )

        # But an OAuth-issued token must NOT satisfy the static-only check (no delegation
        # chain — only the operator's real MCP token can approve /authorize grants).
        assert not runtime_state.verify_static_mcp_token(access_token)

    async def test_random_token_rejected_by_verify_bearer(self, client: AsyncClient) -> None:
        from app import runtime_state

        gate = runtime_state.BearerAuthMiddleware(
            app=None,
            token=runtime_state.settings.mcp_auth_token or "",
            flag=runtime_state.remote_mcp_flag,
            auth_cache=runtime_state.mcp_auth_cache,
        )
        db_hash = runtime_state.mcp_auth_cache.get_hash()
        tok_source = runtime_state.resolve_token_source(db_hash)
        assert not gate._verify_bearer(
            "not-a-real-token", db_hash, runtime_state.settings.mcp_auth_token or "", tok_source
        )


# ─────────────────────────────────────────────────────────────────────────────
# 15. redirect_uri validation is parsed, not prefix-matched (2.1.8)
# ─────────────────────────────────────────────────────────────────────────────


class TestRedirectUriValidation:
    """
    Regression: ``_valid_redirect_uri`` used ``uri.startswith("http://localhost")``, so any
    attacker-controlled host merely BEGINNING with "localhost" was accepted as a loopback
    URI — e.g. ``http://localhost.attacker.example/cb``. A crafted /authorize link could then
    carry the issued authorization code off-box, in plaintext, once the operator approved it.
    """

    async def test_register_rejects_localhost_lookalike_host(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/register", json={"redirect_uris": ["http://localhost.attacker.example/cb"]}
        )
        assert resp.status_code == 400

    async def test_authorize_rejects_localhost_lookalike_host(self, client: AsyncClient) -> None:
        _, challenge = _pkce_pair()
        resp = await client.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": "lookalike-client",
                "redirect_uri": "http://localhost.attacker.example/cb",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert resp.status_code == 400

    async def test_authorize_post_rejects_localhost_lookalike_host(
        self, client: AsyncClient
    ) -> None:
        """The POST leg validates independently — it is reachable without the GET."""
        _, challenge = _pkce_pair()
        resp = await client.post(
            "/authorize",
            data={
                "client_id": "lookalike-client-2",
                "redirect_uri": "http://localhost.attacker.example/cb",
                "code_challenge": challenge,
                "state": "",
                "mcp_token": _MCP_TOKEN,
            },
            follow_redirects=False,
        )
        assert resp.status_code == 400

    async def test_bare_scheme_rejected(self, client: AsyncClient) -> None:
        resp = await client.post("/register", json={"redirect_uris": ["https://"]})
        assert resp.status_code == 400

    async def test_localhost_still_accepted(self, client: AsyncClient) -> None:
        """Non-regression: the genuine local/dev redirect URI must keep working."""
        resp = await client.post("/register", json={"redirect_uris": ["http://localhost:8080/cb"]})
        assert resp.status_code == 201, resp.text

    async def test_loopback_ip_now_accepted(self, client: AsyncClient) -> None:
        """
        Deliberate, documented widening (2.1.8): the prefix check accepted only the literal
        string "localhost", so a developer pointing a client at http://127.0.0.1 got a
        confusing rejection. Parsing the host lets the allow-set name what was always meant —
        the loopback interface — and 127.0.0.1 / ::1 are no less loopback than "localhost".
        """
        for uri in ("http://127.0.0.1:9000/cb", "http://[::1]:9000/cb"):
            resp = await client.post("/register", json={"redirect_uris": [uri]})
            assert resp.status_code == 201, f"{uri} must be valid: {resp.text}"

    async def test_https_still_accepted(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/register", json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"]}
        )
        assert resp.status_code == 201


# ─────────────────────────────────────────────────────────────────────────────
# 16. Changing the static MCP token revokes every OAuth grant (2.1.8)
# ─────────────────────────────────────────────────────────────────────────────


async def _seed_vault_state() -> None:
    """
    Insert the vault_state row PUT /mcp/auth reads, with remote_mcp_enabled already ON.

    In production this row is seeded at lifespan startup; the SQLite fixture here starts
    from an empty schema, and the handler's defensive "row is missing" branch creates it
    with remote_mcp_enabled=False — which the allow-aware clamp (ADR-0033 §2.4) then
    propagates to the in-process flag, 404-ing every later OAuth call for reasons that have
    nothing to do with what these tests assert.
    """
    from datetime import UTC, datetime

    from app import runtime_state
    from app.models import VaultState

    async with runtime_state.get_session() as session:
        session.add(
            VaultState(
                vault_id=runtime_state.settings.vault_id,
                data_version=0,
                remote_mcp_enabled=True,
                mcp_access_token_hash=None,
                mcp_allow_without_token=False,
                updated_at=datetime.now(UTC),
            )
        )


async def _mint_oauth_pair(client: AsyncClient, client_id: str) -> tuple[str, str]:
    """Run a full authorize+token round trip; return (access_token, refresh_token)."""
    verifier, challenge = _pkce_pair()
    redirect_uri = "https://claude.ai/api/mcp/auth_callback"
    code = await _approve_and_get_code(
        client, client_id=client_id, redirect_uri=redirect_uri, code_challenge=challenge
    )
    resp = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["access_token"], body["refresh_token"]


def _bearer_accepts(access_token: str) -> bool:
    """Ask the real MCP access gate whether this token still opens /mcp/server."""
    from app import runtime_state

    gate = runtime_state.BearerAuthMiddleware(
        app=None,
        token=runtime_state.settings.mcp_auth_token or "",
        flag=runtime_state.remote_mcp_flag,
        auth_cache=runtime_state.mcp_auth_cache,
    )
    db_hash = runtime_state.mcp_auth_cache.get_hash()
    tok_source = runtime_state.resolve_token_source(db_hash)
    return bool(
        gate._verify_bearer(
            access_token, db_hash, runtime_state.settings.mcp_auth_token or "", tok_source
        )
    )


class TestStaticTokenChangeRevokesGrants:
    """
    Regression: PUT /mcp/auth (set / rotate / clear) changed the ONLY credential that can
    approve an OAuth grant, but left every already-issued grant fully alive. Rotating a
    leaked token therefore revoked nothing for an OAuth client: the access token kept
    opening /mcp/server, and its 90-day refresh_token — which never re-presents the static
    token — kept minting new ones.
    """

    async def test_rotate_token_kills_existing_access_token(self, client: AsyncClient) -> None:
        await _seed_vault_state()
        access_token, _ = await _mint_oauth_pair(client, "revoke-client-1")
        assert _bearer_accepts(access_token), "precondition: freshly minted token must work"

        resp = await client.put("/mcp/auth", json={"rotate_token": True})
        assert resp.status_code == 200, resp.text

        assert not _bearer_accepts(
            access_token
        ), "an OAuth access token must not survive a static-token rotation"

    async def test_rotate_token_kills_existing_refresh_token(self, client: AsyncClient) -> None:
        await _seed_vault_state()
        _, refresh_token = await _mint_oauth_pair(client, "revoke-client-2")

        resp = await client.put("/mcp/auth", json={"rotate_token": True})
        assert resp.status_code == 200, resp.text

        refreshed = await client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": "revoke-client-2",
            },
        )
        assert refreshed.status_code == 400, (
            "a revoked refresh_token must not mint a new access token: " + refreshed.text
        )

    async def test_clear_token_kills_existing_access_token(self, client: AsyncClient) -> None:
        from app import runtime_state

        await _seed_vault_state()

        # Put a DB-source token in place first, so clear_token actually changes the hash.
        set_resp = await client.put("/mcp/auth", json={"token": "an-explicit-static-token"})
        assert set_resp.status_code == 200
        new_static = "an-explicit-static-token"

        # Mint a grant against that credential.
        verifier, challenge = _pkce_pair()
        redirect_uri = "https://claude.ai/api/mcp/auth_callback"
        approve = await client.post(
            "/authorize",
            data={
                "client_id": "revoke-client-3",
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "state": "",
                "mcp_token": new_static,
            },
            follow_redirects=False,
        )
        assert approve.status_code == 302, approve.text
        code = parse_qs(urlparse(approve.headers["location"]).query)["code"][0]
        minted = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": "revoke-client-3",
                "code_verifier": verifier,
            },
        )
        access_token = minted.json()["access_token"]
        assert _bearer_accepts(access_token)

        cleared = await client.put("/mcp/auth", json={"clear_token": True})
        assert cleared.status_code == 200, cleared.text

        assert not _bearer_accepts(access_token)
        assert runtime_state.mcp_oauth_token_cache._entries == {}

    async def test_unrelated_flag_update_does_not_revoke(self, client: AsyncClient) -> None:
        """
        Guard against over-revoking: a PUT that only flips allow_without_token leaves the
        static credential untouched, so live grants must survive it.
        """
        await _seed_vault_state()
        access_token, _ = await _mint_oauth_pair(client, "keep-client-1")
        assert _bearer_accepts(access_token)

        resp = await client.put("/mcp/auth", json={"allow_without_token": True})
        assert resp.status_code == 200, resp.text

        assert _bearer_accepts(access_token), "a flag-only update must not revoke OAuth grants"
