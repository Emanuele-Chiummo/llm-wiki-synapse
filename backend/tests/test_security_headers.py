"""
H4 — SecurityHeadersMiddleware behaviour (infra-free).

Mounts the middleware on a minimal Starlette app so no DB/Qdrant is needed. Verifies the
conservative hardening headers are stamped on every response, that HSTS is emitted ONLY
over HTTPS (so http/localhost dev is unaffected), and that pre-existing headers are never
overwritten.
"""

from __future__ import annotations

from app.security_headers import SecurityHeadersMiddleware
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient


def _client() -> TestClient:
    async def _ok(_request: object) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def _preset(_request: object) -> PlainTextResponse:
        # Route that already sets X-Frame-Options — the middleware must NOT overwrite it.
        return PlainTextResponse("ok", headers={"X-Frame-Options": "DENY"})

    app = Starlette(routes=[Route("/", _ok), Route("/preset", _preset)])
    app.add_middleware(SecurityHeadersMiddleware)
    return TestClient(app)


def test_static_hardening_headers_present_on_every_response() -> None:
    resp = _client().get("/")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "SAMEORIGIN"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_hsts_absent_over_plain_http() -> None:
    # Default TestClient scheme is http → HSTS must NOT be sent (never break local http dev).
    resp = _client().get("/")
    assert "strict-transport-security" not in resp.headers


def test_hsts_present_when_forwarded_proto_https() -> None:
    resp = _client().get("/", headers={"X-Forwarded-Proto": "https"})
    assert "strict-transport-security" in resp.headers
    assert "max-age=" in resp.headers["strict-transport-security"]


def test_existing_header_not_overwritten() -> None:
    # The route sets X-Frame-Options: DENY; the middleware must leave it untouched.
    resp = _client().get("/preset")
    assert resp.headers["x-frame-options"] == "DENY"
