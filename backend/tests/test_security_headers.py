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


# ── ADR-0087: CSP header assertions ───────────────────────────────────────────


def test_csp_header_present_on_every_response() -> None:
    """CSP header is stamped on every response (ADR-0087 / SEC-CSP-1)."""
    resp = _client().get("/")
    assert "content-security-policy" in resp.headers


def test_csp_script_src_self_without_unsafe_inline() -> None:
    """script-src must be 'self' only — no unsafe-inline or unsafe-eval (AC-CSP-2)."""
    resp = _client().get("/")
    csp = resp.headers["content-security-policy"]
    assert "script-src 'self'" in csp
    # Extract the script-src directive value to confirm no unsafe-inline in script-src.
    import re

    m = re.search(r"script-src([^;]*)", csp)
    assert m is not None, "script-src directive must be present in CSP"
    script_src_value = m.group(1)
    assert "'unsafe-inline'" not in script_src_value, "script-src must NOT contain 'unsafe-inline'"
    assert "'unsafe-eval'" not in script_src_value, "script-src must NOT contain 'unsafe-eval'"


def test_csp_style_src_has_unsafe_inline() -> None:
    """style-src 'unsafe-inline' is present — required by KaTeX, React inline styles,
    and the index.html inline <style> block (ADR-0087 finding)."""
    resp = _client().get("/")
    csp = resp.headers["content-security-policy"]
    assert "style-src" in csp
    # unsafe-inline must be present in the CSP (for style-src specifically)
    assert "'unsafe-inline'" in csp


def test_csp_frame_ancestors_none() -> None:
    """frame-ancestors 'none' prevents clickjacking via iframe embedding (AC-CSP-4)."""
    resp = _client().get("/")
    csp = resp.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp


def test_csp_object_src_none_and_base_uri_self() -> None:
    """object-src 'none' blocks plugins; base-uri 'self' blocks base-tag injection."""
    resp = _client().get("/")
    csp = resp.headers["content-security-policy"]
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
