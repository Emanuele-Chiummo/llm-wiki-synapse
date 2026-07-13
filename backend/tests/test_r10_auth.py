"""
Tests for R10-1 — Shared Bearer token authentication (ADR-0052).

AC coverage
-----------
AC-R10-1-1a  Disabled default: empty SYNAPSE_AUTH_TOKEN → all routes return 200 with
             no Authorization header required (EC-M10-11 backward-compat).

AC-R10-1-1b  Enabled + reject: set token → GET /pages no header → 401 with
             {"error":"unauthorized","hint":...} + WWW-Authenticate: Bearer.

AC-R10-1-1c  Enabled + accept: correct Bearer → 200.

AC-R10-1-2   Exempt set: with token set, /status and the minimal /health/live return
             200 without an Authorization header. /health/detailed is protected.

AC-R10-1-3   WebSocket: N/A — the codebase uses NDJSON-over-POST for chat streaming
             (ADR-0019 "not SSE/WebSocket"); there is NO WebSocket route.  This test
             file documents that fact instead of implementing a W/S test.
             Per ADR-0052 §2.2: "There is no WebSocket route in the codebase.
             POST /chat/stream is an ordinary POST and is gated by the HTTP middleware
             like any other route."  AC-R10-1-3 (W/S close-code-4401) is therefore
             not applicable to the current architecture and is recorded here as a
             FORWARD CONSTRAINT: if a WebSocket route is ever added, that work will
             require an ADR amendment before implementation (ADR-0052 §2.2 paragraph
             "WebSocket note").

AC-R10-1-5   ruff + black + mypy clean — verified by CI gate (test runs are part of
             the same suite; code-quality tests are in test_code_quality.py).

Security invariants tested here
--------------------------------
- Token NEVER in any log entry (caplog assertion).
- Constant-time compare used (source import assertion — ``secrets.compare_digest``).
- CORS-on-401 (the ordering test): a cross-origin 401 carries
  Access-Control-Allow-Origin (ADR-0052 §2.4 risk-1).
- OPTIONS exempt (CORS preflight answered, not 401'd).
- /mcp/server prefix NOT double-gated by SynapseAuthMiddleware.
- POST /clip NOT double-gated (but GET /clip/config IS gated).
"""

from __future__ import annotations

import inspect
import logging
import secrets
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

# ─────────────────────────────────────────────────────────────────────────────
# Module-level no-op lifespan helper
# ─────────────────────────────────────────────────────────────────────────────


async def _noop_lifespan(app_: Any) -> AsyncGenerator[None, None]:
    """Suppress real startup/shutdown so tests don't need live Postgres/Qdrant."""
    yield


# ─────────────────────────────────────────────────────────────────────────────
# Minimal Starlette app with SynapseAuthMiddleware for unit-level tests
# (avoids spinning up the full FastAPI app for simple middleware logic tests)
# ─────────────────────────────────────────────────────────────────────────────


async def _echo_handler(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _make_mini_app(token: str, cors_origins: list[str] | None = None) -> Starlette:
    """
    Minimal Starlette app with the auth middleware (and optionally CORS) wired in
    the same order as main.py (auth inner, CORS outer).
    """
    from app.auth import SynapseAuthMiddleware
    from fastapi.middleware.cors import CORSMiddleware

    routes = [
        Route("/pages", _echo_handler),
        Route("/status", _echo_handler),
        Route("/health/live", _echo_handler),
        Route("/health/detailed", _echo_handler),
        Route("/docs", _echo_handler),
        Route("/openapi.json", _echo_handler),
        Route("/clip", _echo_handler, methods=["POST"]),
        Route("/clip/config", _echo_handler),
        Route("/mcp/server/sse", _echo_handler),
        Route("/mcp/info", _echo_handler),
    ]
    app = Starlette(routes=routes)
    # Mirror main.py registration order: auth BEFORE CORS → auth is inner.
    app.add_middleware(SynapseAuthMiddleware, token=token)
    if cors_origins is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    return app


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests: SynapseAuthMiddleware in isolation (Starlette mini-app)
# ─────────────────────────────────────────────────────────────────────────────


class TestAuthMiddlewareDisabled:
    """AC-R10-1-1a: empty token → auth disabled, all routes open (EC-M10-11)."""

    def test_pages_open_when_token_empty(self) -> None:
        app = _make_mini_app(token="")
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/pages")
        assert resp.status_code == 200

    def test_pages_open_no_auth_header(self) -> None:
        """No Authorization header required when auth is disabled."""
        app = _make_mini_app(token="")
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/pages")
        assert "www-authenticate" not in resp.headers

    def test_status_open_when_token_empty(self) -> None:
        app = _make_mini_app(token="")
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/status")
        assert resp.status_code == 200


class TestAuthMiddlewareEnabled:
    """AC-R10-1-1b/c: set token → gated routes require correct Bearer."""

    TOKEN = "test-secret-token-abc123"

    def test_reject_no_header(self) -> None:
        """AC-R10-1-1b: no Authorization header → 401."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/pages")
        assert resp.status_code == 401

    def test_reject_wrong_token(self) -> None:
        """AC-R10-1-1b: wrong Bearer value → 401."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/pages", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401

    def test_reject_401_body_shape(self) -> None:
        """AC-R10-1-1b: 401 body matches PM-locked contract {"error","hint"}."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/pages")
        data = resp.json()
        assert data["error"] == "unauthorized"
        assert "hint" in data
        assert "Bearer" in data["hint"]

    def test_reject_401_www_authenticate_header(self) -> None:
        """RFC 6750: 401 must carry WWW-Authenticate: Bearer."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/pages")
        assert resp.headers.get("www-authenticate") == "Bearer"

    def test_accept_correct_bearer(self) -> None:
        """AC-R10-1-1c: correct Bearer → 200."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/pages", headers={"Authorization": f"Bearer {self.TOKEN}"})
        assert resp.status_code == 200

    def test_accept_case_insensitive_bearer_prefix(self) -> None:
        """'bearer' (lowercase) prefix is also accepted per RFC 6750."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/pages", headers={"Authorization": f"bearer {self.TOKEN}"})
        assert resp.status_code == 200


class TestExemptPaths:
    """AC-R10-1-2: exempt set passes through without a token."""

    TOKEN = "test-secret-token-xyz789"

    def _assert_open(self, path: str, method: str = "GET") -> None:
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.request(method, path)
        assert (
            resp.status_code == 200
        ), f"{method} {path} should be exempt but got {resp.status_code}"

    def test_status_exempt(self) -> None:
        """GET /status is always reachable (liveness probe)."""
        self._assert_open("/status")

    def test_health_live_exempt(self) -> None:
        """GET /health/live is always reachable and contains no diagnostics."""
        self._assert_open("/health/live")

    def test_health_detailed_gated(self) -> None:
        """GET /health/detailed requires the shared token because it exposes diagnostics."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            response = client.get("/health/detailed")
        assert response.status_code == 401

    def test_docs_exempt(self) -> None:
        """GET /docs is exempt (schema is public, avoids confusing gated docs UX)."""
        self._assert_open("/docs")

    def test_openapi_json_exempt(self) -> None:
        """GET /openapi.json is exempt (same rationale as /docs)."""
        self._assert_open("/openapi.json")

    def test_clip_post_exempt(self) -> None:
        """POST /clip bypasses this middleware (uses ADR-0038 CLIP_TOKEN)."""
        self._assert_open("/clip", method="POST")

    def test_clip_config_gated(self) -> None:
        """GET /clip/config is NOT exempt — ordinary REST route, gated (ADR-0052 §2.3-D)."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/clip/config")
        assert resp.status_code == 401

    def test_mcp_server_prefix_exempt(self) -> None:
        """Requests to /mcp/server/... bypass SynapseAuthMiddleware (ADR-0033 keeps its own gate)."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/mcp/server/sse")
        # The mini-app has a route at /mcp/server/sse that returns 200 if auth passes it.
        # Since /mcp/server/* is exempt, auth passes it through and the route returns 200.
        assert resp.status_code == 200

    def test_mcp_info_is_gated(self) -> None:
        """GET /mcp/info is on the main router, NOT the mount prefix — IS gated (ADR-0052 §2.3-D)."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/mcp/info")
        assert resp.status_code == 401


class TestOptionsExempt:
    """AC: OPTIONS (CORS preflight) is never 401'd regardless of auth state."""

    TOKEN = "test-options-token-pqr456"

    def test_options_always_passes_through(self) -> None:
        """OPTIONS to any path bypasses the token check."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.options("/pages")
        # The mini-app does not have an explicit OPTIONS handler, so Starlette
        # returns 405 Method Not Allowed — but it does NOT return 401 (which is
        # the key assertion: auth middleware passed it through).
        assert resp.status_code != 401

    def test_options_with_wrong_bearer_still_not_401(self) -> None:
        """Even with a wrong Bearer header, OPTIONS is passed through (never 401'd)."""
        app = _make_mini_app(token=self.TOKEN)
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.options("/pages", headers={"Authorization": "Bearer wrong-value"})
        assert resp.status_code != 401


# ─────────────────────────────────────────────────────────────────────────────
# CORS-on-401 test (ADR-0052 §2.4 — the ordering invariant)
# ─────────────────────────────────────────────────────────────────────────────


class TestCorsOn401:
    """
    ADR-0052 §2.4 Risk 1: a 401 from a cross-origin request MUST carry
    Access-Control-Allow-Origin so the browser can read the status code.

    This test verifies the middleware ORDER is correct:
      auth (inner) → 401 → passes up through CORS (outer) → CORS stamps the header.
    """

    TOKEN = "test-cors-order-token"
    ORIGIN = "http://localhost:5173"

    def test_401_carries_cors_header(self) -> None:
        """
        A cross-origin request with no/wrong token → 401 with Access-Control-Allow-Origin.

        This is the definitive ordering proof: if CORS were INNER (registered after auth),
        the 401 would exit before CORS processes it and the header would be absent —
        making the browser hide the 401 behind an opaque CORS error.
        """
        app = _make_mini_app(token=self.TOKEN, cors_origins=[self.ORIGIN])
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.get("/pages", headers={"Origin": self.ORIGIN})
        assert resp.status_code == 401
        acao = resp.headers.get("access-control-allow-origin")
        assert acao is not None, (
            "401 response MUST carry Access-Control-Allow-Origin when the request has an "
            "Origin header — CORS must be outermost (ADR-0052 §2.4). "
            f"Got headers: {dict(resp.headers)}"
        )

    def test_options_preflight_answered_not_401(self) -> None:
        """OPTIONS preflight is answered by CORS (outermost), not 401'd by auth (inner)."""
        app = _make_mini_app(token=self.TOKEN, cors_origins=[self.ORIGIN])
        with TestClient(app, raise_server_exceptions=True) as client:
            resp = client.options(
                "/pages",
                headers={
                    "Origin": self.ORIGIN,
                    "Access-Control-Request-Method": "GET",
                },
            )
        assert resp.status_code != 401


# ─────────────────────────────────────────────────────────────────────────────
# Security invariants: no token leak in logs, constant-time compare
# ─────────────────────────────────────────────────────────────────────────────


class TestSecurityInvariants:
    """Do-NOT §2 (no token in logs) and Do-NOT §9 (constant-time compare)."""

    TOKEN = "super-secret-do-not-log-me"

    def test_token_never_in_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        """
        ADR-0052 Do-NOT §2: the token value MUST NEVER appear in any log entry.

        Checks both a rejected request (auth failure) and an accepted request.
        """
        app = _make_mini_app(token=self.TOKEN)

        with caplog.at_level(logging.DEBUG):
            with TestClient(app, raise_server_exceptions=True) as client:
                # Rejected request (wrong token)
                client.get("/pages", headers={"Authorization": "Bearer wrong-value"})
                # Accepted request (correct token)
                client.get("/pages", headers={"Authorization": f"Bearer {self.TOKEN}"})

        for record in caplog.records:
            assert self.TOKEN not in record.getMessage(), (
                f"Token value found in log record: {record.getMessage()!r}. "
                "ADR-0052 Do-NOT §2: never log the token."
            )

    def test_constant_time_compare_used(self) -> None:
        """
        ADR-0052 §2.1 / Do-NOT §9: comparison MUST use secrets.compare_digest,
        not == (constant-time, closes timing side-channel).

        Source assertion: verify auth.py imports and calls secrets.compare_digest.
        """
        import app.auth as auth_module

        # 1. The module must import 'secrets'.
        assert hasattr(
            auth_module, "secrets"
        ), "app/auth.py must import the 'secrets' module (ADR-0052 Do-NOT §9)"

        # 2. The source code must reference secrets.compare_digest (not ==).
        source = inspect.getsource(auth_module)
        assert "secrets.compare_digest" in source, (
            "app/auth.py must use secrets.compare_digest for token comparison "
            "(ADR-0052 §2.1, Do-NOT §9 — constant-time compare, no == operator)"
        )

        # 3. The function itself must be callable (sanity check the import is live).
        assert callable(secrets.compare_digest), "secrets.compare_digest must be callable"


# ─────────────────────────────────────────────────────────────────────────────
# Bypass_auth predicate unit tests (auth.py internal logic)
# ─────────────────────────────────────────────────────────────────────────────


class TestBypassAuthPredicate:
    """Unit tests for the _bypass_auth(method, path) predicate in auth.py."""

    def test_options_bypasses_any_path(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("OPTIONS", "/pages") is True
        assert _bypass_auth("OPTIONS", "/secret-route") is True

    def test_status_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("GET", "/status") is True

    def test_health_live_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("GET", "/health/live") is True

    def test_health_detailed_not_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("GET", "/health/detailed") is False

    def test_docs_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("GET", "/docs") is True

    def test_openapi_json_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("GET", "/openapi.json") is True

    def test_clip_exact_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("POST", "/clip") is True

    def test_clip_subpath_not_bypassed(self) -> None:
        """GET /clip/config is NOT exempt — it is gated by this middleware."""
        from app.auth import _bypass_auth

        assert _bypass_auth("GET", "/clip/config") is False

    def test_mcp_server_exact_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("GET", "/mcp/server") is True

    def test_mcp_server_subpath_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("GET", "/mcp/server/sse") is True
        assert _bypass_auth("POST", "/mcp/server/messages") is True

    def test_mcp_info_not_bypassed(self) -> None:
        """GET /mcp/info is a main-router route, NOT the mount — IS gated."""
        from app.auth import _bypass_auth

        assert _bypass_auth("GET", "/mcp/info") is False

    def test_mcp_auth_not_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("PUT", "/mcp/auth") is False

    def test_mcp_remote_not_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("PUT", "/mcp/remote") is False

    def test_pages_not_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("GET", "/pages") is False

    def test_search_not_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("GET", "/search") is False

    def test_chat_not_bypassed(self) -> None:
        from app.auth import _bypass_auth

        assert _bypass_auth("POST", "/chat/stream") is False


# ─────────────────────────────────────────────────────────────────────────────
# Config field tests (AC-R10-1-1: auth_token field exists and defaults to "")
# ─────────────────────────────────────────────────────────────────────────────


class TestConfigField:
    """Verify config.py addition: auth_token field, env var SYNAPSE_AUTH_TOKEN."""

    def test_auth_token_default_is_empty_string(self) -> None:
        """auth_token defaults to '' — auth disabled by default (EC-M10-11)."""
        from app.config import Settings
        from pydantic.fields import FieldInfo

        field: FieldInfo = Settings.model_fields["auth_token"]
        assert field.default == "", (
            "auth_token must default to '' (empty) so auth is disabled by default "
            "(ADR-0052 §2.1, EC-M10-11)"
        )

    def test_auth_token_is_string_type(self) -> None:
        from app.config import Settings

        field = Settings.model_fields["auth_token"]
        # annotation is str (not Optional[str])
        assert field.annotation is str, (
            "auth_token must be typed as str (not Optional[str]) — the empty string "
            "is the disabled sentinel, not None (ADR-0052 §2.1)"
        )


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket N/A documentation (AC-R10-1-3)
# ─────────────────────────────────────────────────────────────────────────────


class TestWebSocketNA:
    """
    AC-R10-1-3 documentation test.

    The codebase uses NDJSON-over-POST for chat streaming (ADR-0019).
    There is NO WebSocket route.  The middleware covers 100% of the live surface.

    This test exists to:
    1. Confirm there is no WebSocket route registered in the FastAPI app.
    2. Record the forward constraint from ADR-0052 §2.2 for future implementers.
    """

    def test_no_websocket_routes_registered(self) -> None:
        """
        ADR-0052 §2.2 forward constraint: there is no WebSocket route.

        If this test fails, a WebSocket route was added.  The implementer MUST:
        1. Write an ADR amendment to ADR-0052 §2.2 specifying the W/S auth mechanism
           (query param ?token= or first-frame handshake, close code 4401).
        2. Add a ASGI ``websocket`` scope handler to SynapseAuthMiddleware.
        3. Add the W/S auth test that AC-R10-1-3 originally required.
        """
        from app.main import app

        for route in app.routes:
            route_type = type(route).__name__
            assert "WebSocket" not in route_type, (
                f"WebSocket route found: {route!r}. "
                "ADR-0052 §2.2 forward constraint triggered — see test docstring."
            )
