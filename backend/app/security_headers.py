"""
Security response headers (H4 — defense-in-depth, ADR-0052 companion, ADR-0087).

A tiny ASGI middleware that stamps a hardening header set on every HTTP response:

* ``X-Content-Type-Options: nosniff``  — stop MIME sniffing of served content.
* ``X-Frame-Options: SAMEORIGIN``      — clickjacking guard (SAMEORIGIN, not DENY, so
                                          any same-origin embedding still works).
* ``Referrer-Policy: strict-origin-when-cross-origin`` — don't leak full URLs cross-site.
* ``Content-Security-Policy``          — see ADR-0087 for the full policy rationale.
                                          ``script-src 'self'``: the production Vite build
                                          emits only external hashed JS chunks — no inline
                                          scripts.
                                          ``style-src 'self' 'unsafe-inline'``: required by
                                          three independent sources in the current codebase —
                                          (a) the inline <style> block in index.html, (b)
                                          KaTeX's HTML+MathML output mode which generates
                                          inline style attributes on every rendered span, and
                                          (c) React's inline style={{}} props used extensively
                                          (e.g. react-resizable-panels, type-color badges).
                                          ``unsafe-inline`` for styles is a deliberate,
                                          documented tradeoff: it weakens CSS-injection
                                          protection but does NOT enable JavaScript execution.
                                          ``connect-src 'self'``: the primary enforcement layer
                                          (nginx) proxies the API on the same origin; for dev /
                                          CI the Vite server/preview headers add
                                          ``http://localhost:*`` — see vite.config.ts.
* ``Strict-Transport-Security``        — ONLY when the request arrived over HTTPS
                                          (``x-forwarded-proto: https`` from the tunnel, or
                                          an https scope). Never emitted over plain HTTP, so
                                          local ``http://`` dev and loopback are unaffected.

Note: this middleware applies to ALL FastAPI responses (API JSON, errors, …).  The SPA HTML
document is served by nginx (production) or ``vite preview`` (dev/CI), which also stamp the
CSP independently — those layers are the primary CSP enforcement path for the browser.
Adding the header here provides defense-in-depth and consistency.

Headers already present on a response are left untouched (never overwritten).
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

# ADR-0087: Content Security Policy.
# directive breakdown:
#   default-src 'self'          — catch-all; everything falls back to same-origin.
#   script-src 'self'           — production Vite build: external hashed chunks only.
#   style-src 'self' 'unsafe-inline' — required by KaTeX HTML output, React inline styles,
#                                      and the inline <style> block in index.html (ADR-0087).
#   font-src 'self' data:       — KaTeX fonts bundled by Vite (same-origin); data: as
#                                 a safety net for any CSS-embedded font data URIs.
#   img-src 'self' data: blob:  — favicon, potential data-URI images in wiki content,
#                                 blob: for canvas/sigma.js snapshots.
#   connect-src 'self'          — API is same-origin in production (nginx proxies); dev/CI
#                                 broaden this in vite.config.ts preview/server headers.
#   worker-src 'self' blob:     — PWA service worker (same-origin); blob: future-proofs
#                                 for any blob: worker creation.
#   frame-ancestors 'none'      — complements X-Frame-Options: SAMEORIGIN; prevents framing.
#   object-src 'none'           — block plugins (Flash, Java applets, etc.).
#   base-uri 'self'             — prevent <base href="..."> injection attacks.
_CSP_VALUE: bytes = (
    b"default-src 'self'; "
    b"script-src 'self'; "
    b"style-src 'self' 'unsafe-inline'; "
    b"font-src 'self' data:; "
    b"img-src 'self' data: blob:; "
    b"connect-src 'self'; "
    b"worker-src 'self' blob:; "
    b"frame-ancestors 'none'; "
    b"object-src 'none'; "
    b"base-uri 'self'"
)

_STATIC_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"content-security-policy", _CSP_VALUE),
)

_HSTS_HEADER: tuple[bytes, bytes] = (
    b"strict-transport-security",
    b"max-age=31536000; includeSubDomains",
)


class SecurityHeadersMiddleware:
    """Stamp hardening headers (including CSP — ADR-0087) on every HTTP response."""

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
