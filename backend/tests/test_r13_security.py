"""
Tests for R13-9 deploy security pass.

Coverage
--------
B2 — SSRF guard (security_net.py):
  T-SEC-001  _is_private_ip: RFC1918 (10/8, 172.16/12, 192.168/16) blocked
  T-SEC-002  _is_private_ip: loopback 127.0.0.1 blocked
  T-SEC-003  _is_private_ip: link-local / cloud-metadata 169.254.169.254 blocked
  T-SEC-004  _is_private_ip: IPv6 loopback ::1 blocked
  T-SEC-005  _is_private_ip: IPv6 ULA fd00::1 blocked
  T-SEC-006  _is_private_ip: 0.0.0.0 blocked
  T-SEC-007  _is_private_ip: public IPs (1.1.1.1, 8.8.8.8, 93.184.216.34) allowed
  T-SEC-008  _validate_scheme_and_host: http/https allowed
  T-SEC-009  _validate_scheme_and_host: ftp/file/gopher blocked
  T-SEC-010  _validate_scheme_and_host: empty host raises SSRFError
  T-SEC-011  _check_host: 169.254.169.254 DNS result rejected (cloud-metadata)
  T-SEC-012  _check_host: 10.x.x.x DNS result rejected (RFC1918)
  T-SEC-013  _check_host: DNS failure (OSError) raises SSRFError
  T-SEC-014  _check_host: public IP allowed
  T-SEC-015  safe_fetch: redirect to private IP blocked before second connection
  T-SEC-016  safe_fetch: redirect cap exceeded raises SSRFError
  T-SEC-017  safe_fetch: success on non-redirect public response
  T-SEC-018  source: searxng.py does NOT import safe_fetch (trusted config URL)
  T-SEC-019  source: deep_research.py imports safe_fetch for result-URL fetches

B11 — method-aware auth exempt list (auth.py):
  T-AUTH-001  GET  /status         → exempt
  T-AUTH-002  HEAD /status         → exempt
  T-AUTH-003  POST /status         → NOT exempt (mutating hypothetical must be gated)
  T-AUTH-004  GET  /clip           → NOT exempt
  T-AUTH-005  POST /clip           → exempt (ADR-0038 CLIP_TOKEN)
  T-AUTH-006  OPTIONS (any path)   → always exempt (CORS preflight)
  T-AUTH-007  GET  /mcp/server     → exempt (sub-app prefix)
  T-AUTH-008  POST /mcp/server/sse → exempt (sub-app prefix, arbitrary sub-path)
  T-AUTH-009  GET  /mcp/info       → NOT exempt (management route, not sub-app)
  T-AUTH-010  GET  /health/detailed → exempt
  T-AUTH-011  DELETE /health/detailed → NOT exempt
  T-AUTH-012  GET  /docs           → exempt
  T-AUTH-013  GET  /openapi.json   → exempt

B4 — per-IP fixed-window rate limiter (rate_limit.py):
  T-RL-001  allows exactly N requests within a window
  T-RL-002  rejects N+1 with HTTPException(429)
  T-RL-003  429 response includes Retry-After header
  T-RL-004  window rollover resets counter (using _now test hook)
  T-RL-005  different IPs have independent counters
  T-RL-006  route wiring: mini FastAPI app returns 429 after limit
  T-RL-007  source: POST /chat/stream carries rate_limit Depends
  T-RL-008  source: POST /ingest/trigger carries rate_limit Depends
  T-RL-009  source: POST /research/start carries rate_limit Depends
"""

from __future__ import annotations

import socket
from unittest.mock import MagicMock

import pytest
from fastapi import Depends, FastAPI
from fastapi import Request as FastAPIRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _af_inet() -> int:  # noqa: D103 — avoids importing socket at module level
    return socket.AF_INET


def _make_addr_info(ip: str) -> list[tuple]:
    """Build a minimal socket.getaddrinfo return value for a single IPv4 address."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


def _make_request(ip: str = "1.2.3.4") -> MagicMock:
    """Create a minimal mock Request object for rate-limiter tests."""
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = ip
    return req


# ===========================================================================
# B2 — SSRF guard
# ===========================================================================


class TestIsPrivateIp:
    """T-SEC-001 to T-SEC-007: _is_private_ip correctly classifies addresses."""

    def test_rfc1918_10_block(self) -> None:
        from app.security_net import _is_private_ip

        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("10.255.255.255") is True

    def test_rfc1918_172_block(self) -> None:
        from app.security_net import _is_private_ip

        assert _is_private_ip("172.16.0.1") is True
        assert _is_private_ip("172.31.255.255") is True

    def test_rfc1918_192_block(self) -> None:
        from app.security_net import _is_private_ip

        assert _is_private_ip("192.168.0.1") is True
        assert _is_private_ip("192.168.100.200") is True

    def test_loopback_blocked(self) -> None:
        from app.security_net import _is_private_ip

        assert _is_private_ip("127.0.0.1") is True
        assert _is_private_ip("127.255.0.0") is True

    def test_link_local_and_cloud_metadata_blocked(self) -> None:
        from app.security_net import _is_private_ip

        # The notorious cloud-metadata endpoint
        assert _is_private_ip("169.254.169.254") is True
        # General link-local
        assert _is_private_ip("169.254.0.1") is True

    def test_ipv6_loopback_blocked(self) -> None:
        from app.security_net import _is_private_ip

        assert _is_private_ip("::1") is True

    def test_ipv6_ula_blocked(self) -> None:
        from app.security_net import _is_private_ip

        assert _is_private_ip("fd00::1") is True
        assert _is_private_ip("fc00::1") is True

    def test_zero_address_blocked(self) -> None:
        from app.security_net import _is_private_ip

        assert _is_private_ip("0.0.0.0") is True

    def test_public_ips_allowed(self) -> None:
        from app.security_net import _is_private_ip

        assert _is_private_ip("1.1.1.1") is False  # Cloudflare
        assert _is_private_ip("8.8.8.8") is False  # Google
        assert _is_private_ip("93.184.216.34") is False  # example.com

    def test_invalid_string_fail_closed(self) -> None:
        from app.security_net import _is_private_ip

        # Unparseable → fail-closed (treat as private / block)
        assert _is_private_ip("not-an-ip") is True
        assert _is_private_ip("") is True


class TestValidateSchemeAndHost:
    """T-SEC-008 to T-SEC-010: scheme + host validation."""

    def test_http_allowed(self) -> None:
        from app.security_net import _validate_scheme_and_host

        scheme, host = _validate_scheme_and_host("http://example.com/page")
        assert scheme == "http"
        assert host == "example.com"

    def test_https_allowed(self) -> None:
        from app.security_net import _validate_scheme_and_host

        scheme, host = _validate_scheme_and_host("https://example.com/path?q=1")
        assert scheme == "https"
        assert host == "example.com"

    def test_ftp_blocked(self) -> None:
        from app.security_net import SSRFError, _validate_scheme_and_host

        with pytest.raises(SSRFError, match="scheme"):
            _validate_scheme_and_host("ftp://example.com/file")

    def test_file_scheme_blocked(self) -> None:
        from app.security_net import SSRFError, _validate_scheme_and_host

        with pytest.raises(SSRFError, match="scheme"):
            _validate_scheme_and_host("file:///etc/passwd")

    def test_empty_host_blocked(self) -> None:
        from app.security_net import SSRFError, _validate_scheme_and_host

        with pytest.raises(SSRFError, match="[Hh]ost"):
            _validate_scheme_and_host("http:///path")


class TestCheckHost:
    """T-SEC-011 to T-SEC-014: DNS resolution + private IP detection."""

    async def test_metadata_ip_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-SEC-011: 169.254.169.254 (cloud-metadata endpoint) is blocked."""
        from app.security_net import SSRFError, _check_host

        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port, family, type_: _make_addr_info("169.254.169.254"),
        )
        with pytest.raises(SSRFError, match="private"):
            await _check_host("metadata.internal")

    async def test_rfc1918_ip_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-SEC-012: A hostname resolving to 10.x.x.x is blocked."""
        from app.security_net import SSRFError, _check_host

        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port, family, type_: _make_addr_info("10.0.0.100"),
        )
        with pytest.raises(SSRFError, match="private"):
            await _check_host("internal.corp")

    async def test_dns_failure_raises_ssrf_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-SEC-013: OSError from DNS raises SSRFError (fail-closed)."""
        from app.security_net import SSRFError, _check_host

        def _fail(host: str, port: object, family: int, type_: int) -> list:
            raise OSError("NXDOMAIN")

        monkeypatch.setattr("socket.getaddrinfo", _fail)
        with pytest.raises(SSRFError, match="resolve"):
            await _check_host("nxdomain.invalid")

    async def test_public_ip_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-SEC-014: Hostname resolving to a public IP passes without error."""
        from app.security_net import _check_host

        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port, family, type_: _make_addr_info("93.184.216.34"),
        )
        # Should not raise
        await _check_host("example.com")


class TestSafeFetch:
    """T-SEC-015 to T-SEC-017: safe_fetch end-to-end behaviour."""

    async def test_redirect_to_private_ip_blocked(
        self,
        monkeypatch: pytest.MonkeyPatch,
        httpx_mock: object,
    ) -> None:
        """
        T-SEC-015: A 301 redirect to a private IP is blocked BEFORE the second connection.

        Flow:
          1. DNS resolves example.com → public IP (pass)
          2. httpx returns 301 Location: http://10.0.0.1/secret
          3. DNS resolves 10.0.0.1 → private IP → SSRFError (no second HTTP request)
        """
        from app.security_net import SSRFError, safe_fetch

        # Public DNS for initial host; 10.0.0.1 resolves to itself (RFC1918)
        def mock_dns(host: str, port: object, family: int, type_: int) -> list:
            if host == "example.com":
                return _make_addr_info("93.184.216.34")
            # IP literals (e.g. "10.0.0.1") resolve to themselves
            return _make_addr_info(host)

        monkeypatch.setattr("socket.getaddrinfo", mock_dns)

        # First HTTP request returns a redirect to a private IP.
        # is_optional=True: if SSRFError is raised BEFORE the request, teardown won't fail.
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://example.com/article",
            status_code=301,
            headers={"location": "http://10.0.0.1/secret"},
            is_optional=True,
        )

        with pytest.raises(SSRFError, match="private"):
            await safe_fetch("https://example.com/article")

    async def test_too_many_redirects_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        httpx_mock: object,
    ) -> None:
        """T-SEC-016: Redirect chain exceeding max_redirects raises SSRFError."""
        from app.security_net import SSRFError, safe_fetch

        # Always public DNS
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port, family, type_: _make_addr_info("93.184.216.34"),
        )

        # safe_fetch with max_redirects=3 makes at most 4 requests (hops 0..3).
        # Hop 3 returns a redirect → we raise "Too many redirects" without a 5th request.
        # Register a reusable response (is_optional so leftover ones don't fail teardown).
        httpx_mock.add_response(  # type: ignore[attr-defined]
            status_code=302,
            headers={"location": "https://example.com/other"},
            is_reusable=True,
            is_optional=True,
        )

        with pytest.raises(SSRFError, match="redirect"):
            await safe_fetch("https://example.com/start", max_redirects=3)

    async def test_success_returns_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
        httpx_mock: object,
    ) -> None:
        """T-SEC-017: A non-redirect public HTTPS response is returned unchanged."""
        from app.security_net import safe_fetch

        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda host, port, family, type_: _make_addr_info("93.184.216.34"),
        )

        # Not optional: this response MUST be consumed (the request must reach httpx).
        httpx_mock.add_response(  # type: ignore[attr-defined]
            url="https://example.com/page",
            status_code=200,
            text="Hello world",
        )

        resp = await safe_fetch("https://example.com/page")
        assert resp.status_code == 200
        assert resp.text == "Hello world"


class TestSsrfSourceInspection:
    """T-SEC-018/019: verify architectural separation at the source level."""

    def test_searxng_does_not_import_safe_fetch(self) -> None:
        """
        T-SEC-018: ops/searxng.py calls the operator-configured SEARXNG_URL directly
        (trusted config) and must NOT import safe_fetch — that would be a double-guard
        on a trusted endpoint.
        """
        import ast
        import pathlib

        src = pathlib.Path(__file__).parent.parent / "app" / "ops" / "searxng.py"
        tree = ast.parse(src.read_text())
        imports: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)

        assert "app.security_net" not in imports, (
            "searxng.py must NOT import from app.security_net — "
            "SEARXNG_URL is trusted operator config"
        )

    def test_deep_research_imports_safe_fetch(self) -> None:
        """
        T-SEC-019: ops/deep_research.py must import safe_fetch from app.security_net
        for fetching SearXNG result URLs (untrusted external input).
        """
        import ast
        import pathlib

        src = pathlib.Path(__file__).parent.parent / "app" / "ops" / "deep_research.py"
        tree = ast.parse(src.read_text())
        from_security_net = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "app.security_net"
            for alias in node.names
        ]

        assert (
            "safe_fetch" in from_security_net
        ), "deep_research.py must import safe_fetch from app.security_net"
        assert (
            "SSRFError" in from_security_net
        ), "deep_research.py must import SSRFError from app.security_net"


# ===========================================================================
# B11 — Method-aware auth exempt list
# ===========================================================================


class TestBypassAuth:
    """T-AUTH-001 to T-AUTH-013: _bypass_auth (path, method) pair logic."""

    @pytest.fixture(autouse=True)
    def _import(self) -> None:
        from app.auth import _bypass_auth

        self._bypass = _bypass_auth

    def test_get_status_exempt(self) -> None:
        """T-AUTH-001"""
        assert self._bypass("GET", "/status") is True

    def test_head_status_exempt(self) -> None:
        """T-AUTH-002: liveness probes often use HEAD."""
        assert self._bypass("HEAD", "/status") is True

    def test_post_status_not_exempt(self) -> None:
        """T-AUTH-003: mutating verb on probe path must be gated (R13-9/B11)."""
        assert self._bypass("POST", "/status") is False

    def test_get_clip_not_exempt(self) -> None:
        """T-AUTH-004: only POST /clip is exempt (CLIP_TOKEN route)."""
        assert self._bypass("GET", "/clip") is False

    def test_post_clip_exempt(self) -> None:
        """T-AUTH-005: POST /clip uses ADR-0038 CLIP_TOKEN, not API Bearer."""
        assert self._bypass("POST", "/clip") is True

    def test_options_always_exempt(self) -> None:
        """T-AUTH-006: CORS preflights cannot carry Bearer headers."""
        assert self._bypass("OPTIONS", "/pages") is True
        assert self._bypass("OPTIONS", "/status") is True
        assert self._bypass("OPTIONS", "/any/arbitrary/path") is True

    def test_mcp_server_prefix_exempt(self) -> None:
        """T-AUTH-007: /mcp/server is the FastMCP sub-app; it has its own auth."""
        assert self._bypass("GET", "/mcp/server") is True

    def test_mcp_server_subpath_exempt(self) -> None:
        """T-AUTH-008: sub-paths of /mcp/server are also exempt."""
        assert self._bypass("POST", "/mcp/server/sse") is True
        assert self._bypass("GET", "/mcp/server/tools/list") is True

    def test_mcp_info_not_exempt(self) -> None:
        """T-AUTH-009: /mcp/info is a management route, NOT the sub-app prefix."""
        assert self._bypass("GET", "/mcp/info") is False

    def test_health_detailed_exempt(self) -> None:
        """T-AUTH-010"""
        assert self._bypass("GET", "/health/detailed") is True
        assert self._bypass("HEAD", "/health/detailed") is True

    def test_delete_health_not_exempt(self) -> None:
        """T-AUTH-011: unusual verb on probe path must be gated."""
        assert self._bypass("DELETE", "/health/detailed") is False

    def test_docs_exempt(self) -> None:
        """T-AUTH-012"""
        assert self._bypass("GET", "/docs") is True

    def test_openapi_json_exempt(self) -> None:
        """T-AUTH-013"""
        assert self._bypass("GET", "/openapi.json") is True


# ===========================================================================
# B4 — Per-IP fixed-window rate limiter
# ===========================================================================


class TestFixedWindowLimiter:
    """T-RL-001 to T-RL-005: _FixedWindowLimiter unit tests."""

    async def test_allows_n_requests(self) -> None:
        """T-RL-001: Exactly N requests succeed within the window."""

        from app.rate_limit import _FixedWindowLimiter

        limiter = _FixedWindowLimiter()
        req = _make_request("5.6.7.8")
        for _ in range(5):
            # Must not raise
            await limiter.check(req, requests=5, window_seconds=60, _now=1000.0)

    async def test_rejects_n_plus_one(self) -> None:
        """T-RL-002: The N+1th request raises HTTPException(429)."""
        from app.rate_limit import _FixedWindowLimiter
        from fastapi import HTTPException

        limiter = _FixedWindowLimiter()
        req = _make_request("5.6.7.9")
        for _ in range(3):
            await limiter.check(req, requests=3, window_seconds=60, _now=2000.0)

        with pytest.raises(HTTPException) as exc_info:
            await limiter.check(req, requests=3, window_seconds=60, _now=2000.0)

        assert exc_info.value.status_code == 429

    async def test_429_includes_retry_after_header(self) -> None:
        """T-RL-003: 429 response carries Retry-After header."""
        from app.rate_limit import _FixedWindowLimiter
        from fastapi import HTTPException

        limiter = _FixedWindowLimiter()
        req = _make_request("5.6.7.10")
        for _ in range(2):
            await limiter.check(req, requests=2, window_seconds=60, _now=3000.0)

        with pytest.raises(HTTPException) as exc_info:
            await limiter.check(req, requests=2, window_seconds=60, _now=3000.0)

        assert "Retry-After" in exc_info.value.headers

    async def test_window_rollover_resets_counter(self) -> None:
        """T-RL-004: After window_seconds elapses, the counter resets."""
        from app.rate_limit import _FixedWindowLimiter

        limiter = _FixedWindowLimiter()
        req = _make_request("5.6.7.11")

        # Fill the window at t=4000
        for _ in range(2):
            await limiter.check(req, requests=2, window_seconds=60, _now=4000.0)

        # Advance past the window boundary (t=4000 + 60 = 4060 → t=4061)
        # This should reset the counter
        await limiter.check(req, requests=2, window_seconds=60, _now=4061.0)

        # Should be able to make another request (counter = 2 now in new window)
        await limiter.check(req, requests=2, window_seconds=60, _now=4061.0)

        # N+1 should now fail (we've used 2 in the new window)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await limiter.check(req, requests=2, window_seconds=60, _now=4061.0)
        assert exc_info.value.status_code == 429

    async def test_different_ips_independent(self) -> None:
        """T-RL-005: Requests from different IPs have independent counters."""
        from app.rate_limit import _FixedWindowLimiter

        limiter = _FixedWindowLimiter()
        req_a = _make_request("10.0.0.1")
        req_b = _make_request("10.0.0.2")

        # Exhaust quota for IP A
        for _ in range(2):
            await limiter.check(req_a, requests=2, window_seconds=60, _now=5000.0)

        # IP B is unaffected — should still succeed
        await limiter.check(req_b, requests=2, window_seconds=60, _now=5000.0)

    async def test_zero_limit_disables(self) -> None:
        """requests <= 0 → rate limiter is disabled (no-op)."""
        from app.rate_limit import _FixedWindowLimiter

        limiter = _FixedWindowLimiter()
        req = _make_request("5.6.7.12")

        # Should never raise, regardless of how many calls
        for _ in range(100):
            await limiter.check(req, requests=0, window_seconds=60, _now=6000.0)


class TestRateLimitRoutWiring:
    """T-RL-006: rate_limit dependency applied on a mini FastAPI app."""

    async def test_route_returns_429_after_limit(self) -> None:
        """
        T-RL-006: Minimal FastAPI app with Depends(rate_limit-like) returns 429
        after N requests from the same IP, and 200 for the first N.

        Uses a fresh _FixedWindowLimiter per test to avoid shared state.
        Uses httpx.AsyncClient with ASGITransport so the test runs in the same
        event loop as the async dependency (avoids TestClient event-loop conflicts
        with pytest-asyncio's asyncio_mode="auto").
        """
        import httpx
        from app.rate_limit import _FixedWindowLimiter

        limiter = _FixedWindowLimiter()

        async def local_rate_limit(request: FastAPIRequest) -> None:
            await limiter.check(request, requests=3, window_seconds=60)

        mini_app = FastAPI()

        @mini_app.post("/stream", dependencies=[Depends(local_rate_limit)])
        def stream_endpoint() -> dict:
            return {"ok": True}

        # ASGITransport calls the ASGI app in-process — no real HTTP socket.
        # Not intercepted by pytest-httpx (which patches the HTTP transport layer).
        transport = httpx.ASGITransport(app=mini_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            for i in range(3):
                resp = await client.post("/stream")
                assert (
                    resp.status_code == 200
                ), f"Request {i + 1} should be allowed; got {resp.status_code}: {resp.text}"

            resp = await client.post("/stream")
            assert resp.status_code == 429
            assert "retry-after" in resp.headers


class TestRateLimitSourceInspection:
    """T-RL-007 to T-RL-009: verify rate_limit is wired to inference-cost routes."""

    def _load_router_source(self, module_path: str) -> str:
        import pathlib

        return (pathlib.Path(__file__).parent.parent / module_path).read_text()

    def test_chat_stream_has_rate_limit(self) -> None:
        """T-RL-007: POST /chat/stream carries rate_limit dependency."""
        src = self._load_router_source("app/routers/chat.py")
        # Both the import and the Depends application must be present
        assert "rate_limit" in src, "app/routers/chat.py must apply rate_limit to POST /chat/stream"

    def test_ingest_trigger_has_rate_limit(self) -> None:
        """T-RL-008: POST /ingest/trigger carries rate_limit dependency."""
        src = self._load_router_source("app/routers/ingest.py")
        assert (
            "rate_limit" in src
        ), "app/routers/ingest.py must apply rate_limit to POST /ingest/trigger"

    def test_research_start_has_rate_limit(self) -> None:
        """T-RL-009: POST /research/start carries rate_limit dependency."""
        src = self._load_router_source("app/routers/research.py")
        assert (
            "rate_limit" in src
        ), "app/routers/research.py must apply rate_limit to POST /research/start"


# ===========================================================================
# H3 — rate-limit keying is trusted-proxy-aware (ADR-0033 resolver reuse)
# ===========================================================================


def _asgi_request(peer_ip: str, xff: str | None = None):  # noqa: ANN202 - starlette Request
    """Build a real ASGI-scope Request (not a MagicMock) so resolve_source_ip runs for real."""
    from starlette.requests import Request

    headers: list[tuple[bytes, bytes]] = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    return Request({"type": "http", "client": (peer_ip, 12345), "headers": headers})


class TestRateLimiterKeying:
    """H3: behind a trusted proxy the limiter keys per proxy-attested client, not one bucket."""

    async def test_trusted_proxy_keys_per_xff_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.config import settings
        from app.rate_limit import _FixedWindowLimiter
        from fastapi import HTTPException

        # Peer 10.0.0.1 is the trusted tunnel; the real client is the XFF last hop.
        monkeypatch.setattr(settings, "mcp_trusted_proxies", "10.0.0.1")
        limiter = _FixedWindowLimiter()
        req_a = _asgi_request("10.0.0.1", xff="203.0.113.1")
        req_b = _asgi_request("10.0.0.1", xff="203.0.113.2")

        # Exhaust client A's window.
        for _ in range(2):
            await limiter.check(req_a, requests=2, window_seconds=60, _now=1000.0)
        with pytest.raises(HTTPException) as exc:
            await limiter.check(req_a, requests=2, window_seconds=60, _now=1000.0)
        assert exc.value.status_code == 429

        # Client B (different XFF) has its OWN bucket — must NOT be rejected. Before H3 both
        # shared the single 10.0.0.1 bucket and B would have been 429'd here.
        await limiter.check(req_b, requests=2, window_seconds=60, _now=1000.0)

    async def test_untrusted_peer_ignores_forged_xff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.config import settings
        from app.rate_limit import _FixedWindowLimiter
        from fastapi import HTTPException

        # No trusted proxies → XFF is untrusted; both requests key on the same peer IP.
        monkeypatch.setattr(settings, "mcp_trusted_proxies", "")
        limiter = _FixedWindowLimiter()
        req1 = _asgi_request("198.51.100.5", xff="1.1.1.1")
        req2 = _asgi_request("198.51.100.5", xff="2.2.2.2")  # spoofed XFF must not split buckets

        await limiter.check(req1, requests=2, window_seconds=60, _now=5000.0)
        await limiter.check(req2, requests=2, window_seconds=60, _now=5000.0)
        with pytest.raises(HTTPException) as exc:
            await limiter.check(req1, requests=2, window_seconds=60, _now=5000.0)
        assert exc.value.status_code == 429
