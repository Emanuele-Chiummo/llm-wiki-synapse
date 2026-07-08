"""
Security response headers (H4 — defense-in-depth, ADR-0052 companion).

A tiny ASGI middleware that stamps a conservative set of hardening headers on every
HTTP response:

* ``X-Content-Type-Options: nosniff``  — stop MIME sniffing of served content.
* ``X-Frame-Options: SAMEORIGIN``      — clickjacking guard (SAMEORIGIN, not DENY, so
                                          any same-origin embedding still works).
* ``Referrer-Policy: strict-origin-when-cross-origin`` — don't leak full URLs cross-site.
* ``Strict-Transport-Security``        — ONLY when the request arrived over HTTPS
                                          (``x-forwarded-proto: https`` from the tunnel, or
                                          an https scope). Never emitted over plain HTTP, so
                                          local ``http://`` dev and loopback are unaffected.

Intentionally NO Content-Security-Policy here: a correct CSP for the SPA (inline styles,
KaTeX, workers) needs its own tested policy and would risk breaking the UI — that is a
separate, deliberate change, not a zero-risk header.

Headers already present on a response are left untouched (never overwritten).
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_STATIC_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
)

_HSTS_HEADER: tuple[bytes, bytes] = (
    b"strict-transport-security",
    b"max-age=31536000; includeSubDomains",
)


class SecurityHeadersMiddleware:
    """Stamp conservative hardening headers on every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        is_https = self._request_is_https(scope)

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start":
                raw = list(message.get("headers", []))
                present = {name.lower() for name, _ in raw}
                for name, value in _STATIC_HEADERS:
                    if name not in present:
                        raw.append((name, value))
                if is_https and _HSTS_HEADER[0] not in present:
                    raw.append(_HSTS_HEADER)
                message["headers"] = raw
            await send(message)

        await self._app(scope, receive, _send)

    @staticmethod
    def _request_is_https(scope: Scope) -> bool:
        """True when the effective request scheme is https (proxy-attested or direct)."""
        headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
        for name, value in headers:
            if name == b"x-forwarded-proto":
                proto = value.decode("latin-1").split(",")[0].strip().lower()
                return proto == "https"
        return str(scope.get("scheme") or "") == "https"
