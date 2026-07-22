"""
Synapse shared Bearer token middleware (ADR-0052, R10-1; extended PF-AUTH-1, 1.9.4 W4).

Credential model
----------------
``SYNAPSE_AUTH_TOKEN`` (env var, read once at startup via ``app/config.py``).

* Empty / absent → authentication DISABLED; every route behaves as v0.9 (backward-
  compatible default, EC-M10-11).
* Non-empty → every non-exempt HTTP request MUST present
  ``Authorization: Bearer <token>``, compared constant-time via
  ``secrets.compare_digest`` (I3 — no KDF, no DB round-trip).

Scoped API tokens (PF-AUTH-1, 1.9.4 W4)
----------------------------------------
In ADDITION to the single bootstrap ``SYNAPSE_AUTH_TOKEN`` above, a presented bearer is
also checked against the in-process ``ApiTokenCache`` (``app.runtime_state.api_token_cache``),
which mirrors the active (non-revoked) rows of the ``api_tokens`` table (PBKDF2 hashes,
never plaintext — see ``app.models.ApiToken`` / ``app.runtime_state.hash_token``).

This is purely additive: the bootstrap token check runs first and is completely unchanged;
the DB-token check only runs when the bootstrap check did not match (or auth is disabled —
see below). A request that presents a valid ``api_tokens`` bearer is subject to two
extra, token-specific gates that the bootstrap token has never had:

* **Vault scope** — if the matched token's ``vault_id`` is not NULL, it must equal the
  running instance's ``settings.vault_id`` (single-vault-per-backend architecture). A
  mismatch is indistinguishable from "no token matched" to the caller (401) — it is NOT
  treated as a 403, because the token simply does not apply to this backend.
* **Read-only** — if the matched token's ``read_only`` flag is set, any HTTP method other
  than GET/HEAD/OPTIONS is rejected with 403 (the token authenticated fine; it just isn't
  allowed to write).

NOTE: when ``SYNAPSE_AUTH_TOKEN`` is unset (auth "disabled"), the middleware is STILL a
transparent pass-through — scoped API tokens are an ADDITIONAL credential type layered on
top of the same gate, not a replacement for it. Operators who want scoped tokens enforced
must set ``SYNAPSE_AUTH_TOKEN`` (the perimeter must be closed for a token to mean anything).

Exempt set (bypass_auth predicate — authoritative per ADR-0052 §2.3)
---------------------------------------------------------------------
* ``OPTIONS`` (any path) — CORS preflights cannot carry a bearer header.
* ``GET /status``          — liveness probe; no vault data exposed.
* ``GET /health/live``     — minimal liveness response with no component details.
* ``GET /docs``            — Swagger UI (schema is already public in git).
* ``GET /redoc``           — ReDoc UI (same rationale).
* ``GET /openapi.json``    — raw OpenAPI schema.
* Path prefix ``/mcp/server`` — mounted FastMCP sub-app; uses ADR-0033 own token.
* Exact path ``POST /clip``  — uses ADR-0038 CLIP_TOKEN; the browser extension
  cannot know the API token.

Exemptions are (path, methods) pairs (R13-9, B11): a path is exempt only for the
explicitly listed HTTP methods.  A future mutating route on an otherwise-exempt path
(e.g. a hypothetical ``POST /status``) will NOT be silently open — it will require
the API Bearer token like any other route.  Current-route behaviour is unchanged.

The ``/mcp/*`` management routes (``/mcp/info``, ``/mcp/auth``, ``/mcp/remote``)
and the clip config routes (``/clip/config``) are ordinary REST routes and ARE
gated by this middleware (not in the exempt set).

CORS ordering (ADR-0052 §2.4)
------------------------------
Auth middleware MUST be registered BEFORE ``CORSMiddleware`` in
``app.add_middleware(...)`` source order.  In Starlette, the last-registered
middleware is the outermost layer.  Therefore:

    app.add_middleware(SynapseAuthMiddleware)   # inner — runs auth check
    app.add_middleware(CORSMiddleware, ...)      # outer — wraps every response

This ensures 401 responses carry ``Access-Control-Allow-Origin`` so the browser
can read the status code and display the token prompt (AC-R10-2-2 / ADR-0052 §2.4).

Do-NOTs (ADR-0052 §6)
----------------------
* DO NOT log the token value, a prefix, or any derived form.
* DO NOT compare with ``==`` — use ``secrets.compare_digest`` (constant-time).
* DO NOT put the token in a URL, query string, path, or redirect.
* DO NOT hash or store ``SYNAPSE_AUTH_TOKEN`` in the DB.
* DO NOT enforce via per-route ``Depends`` — use this middleware so new routes
  are gated by construction.
* DO NOT double-gate ``/mcp/server`` or ``POST /clip`` — they keep their own auth.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# ── Exempt-set constants (ADR-0052 §2.3 — named constants, never scattered literals) ─

# MCP_MOUNT_PATH is the sub-app prefix defined in main.py (single source of truth).
# Re-declared here so auth.py is importable without importing main.py (circular).
# MUST match the value in main.py exactly.
MCP_MOUNT_PATH: str = "/mcp/server"

# Method-aware exempt set (R13-9 / B11).
#
# Each entry is (exact_path, allowed_methods).  A request matches this set only when
# BOTH the path AND the HTTP method are in the entry — a future mutating route on an
# otherwise-probe path (e.g. POST /status) will NOT be silently exempt.
#
# HEAD is included alongside GET because HTTP clients and health-check tools send HEAD
# for liveness probes; HEAD exposes the same (empty) response body as GET.
#
# Rationale for each entry:
#   /status, /health/live   — connection/liveness probes (no diagnostics, safe public)
#   /docs, /redoc             — Swagger/ReDoc UIs (schema is public in git)
#   /openapi.json             — raw OpenAPI schema (same rationale as docs)
#   /clip                     — POST only; uses ADR-0038 CLIP_TOKEN (extension token)
#   /authorize, /token, /register, /.well-known/oauth-* — the MCP OAuth 2.1/PKCE
#     authorization server (2.1.6, ADR-0090, app.mcp.oauth). MUST be reachable WITHOUT
#     SYNAPSE_AUTH_TOKEN: these are called server-to-server by the OAuth client (e.g.
#     claude.ai's own backend for /token and /register — it can send no Synapse credential
#     at all, that is the whole reason this surface exists) and via top-level browser
#     navigation for /authorize (no XHR, so no bearer header either). Each of these routes
#     has its OWN independent gate: the SAME remote_mcp_enabled floor as /mcp/server itself
#     (404 when off), and /authorize additionally requires the operator's real MCP token to
#     approve any grant (app.runtime_state.verify_static_mcp_token) — this exemption does
#     NOT make the surface unauthenticated, it only moves the credential check inside the
#     handler instead of the middleware.
_EXEMPT_EXACT: tuple[tuple[str, frozenset[str]], ...] = (
    ("/status", frozenset({"GET", "HEAD"})),
    ("/health/live", frozenset({"GET", "HEAD"})),
    ("/docs", frozenset({"GET", "HEAD"})),
    ("/redoc", frozenset({"GET", "HEAD"})),
    ("/openapi.json", frozenset({"GET", "HEAD"})),
    ("/clip", frozenset({"POST"})),  # ADR-0038 CLIP_TOKEN; extension auth
    ("/authorize", frozenset({"GET", "POST"})),
    ("/token", frozenset({"POST"})),
    ("/register", frozenset({"POST"})),
    ("/.well-known/oauth-authorization-server", frozenset({"GET"})),
    ("/.well-known/oauth-protected-resource", frozenset({"GET"})),
)

# 401 response body (PM-locked contract per SPRINT-v1.0-SCOPE §R10-1 and ADR-0052 §2.4).
_UNAUTHORIZED_BODY: dict[str, str] = {
    "error": "unauthorized",
    "hint": "Set Authorization: Bearer <token>",
}


def _bypass_auth(method: str, path: str) -> bool:
    """
    Return True when this request MUST bypass the token check.

    Predicate (authoritative summary — ADR-0052 §2.3, amended R13-9/B11):
        method == "OPTIONS"                         (CORS preflights)
        or (path, method) matches _EXEMPT_EXACT     (method-aware pairs)
        or path == MCP_MOUNT_PATH / starts with it  (FastMCP sub-app, all methods)

    The _EXEMPT_EXACT check is now METHOD-AWARE (R13-9/B11): only the listed methods
    are exempt for each path. A POST to /status, for example, is no longer silently
    open — it will require a valid Bearer token when auth is enabled.
    """
    if method == "OPTIONS":
        return True
    # Method-aware exempt-set check (R13-9 / B11).
    for exempt_path, exempt_methods in _EXEMPT_EXACT:
        if path == exempt_path and method in exempt_methods:
            return True
    # Mount exclusion: the FastMCP sub-app at /mcp/server and all sub-paths.
    # Management routes (/mcp/info, /mcp/auth, /mcp/remote) are on the main
    # router and are NOT prefixed with /mcp/server — they are gated normally.
    if path == MCP_MOUNT_PATH or path.startswith(MCP_MOUNT_PATH + "/"):
        return True
    return False


class SynapseAuthMiddleware:
    """
    Single shared Bearer token gate (ADR-0052 §2.2).

    Registered via ``app.add_middleware(SynapseAuthMiddleware)`` in ``main.py``
    BEFORE the ``CORSMiddleware`` call so CORS is the outermost layer and stamps
    CORS headers onto every response — including 401s (§2.4 ordering contract).

    When ``token`` is empty (the default), the middleware is a transparent
    pass-through: it calls ``app(scope, receive, send)`` immediately without
    inspecting any header.  This is the backward-compatible disabled path
    (EC-M10-11 / ADR-0052 §2.1).

    Parameters
    ----------
    app:
        The wrapped ASGI application.
    token:
        The ``SYNAPSE_AUTH_TOKEN`` value, read once at import time from
        ``app.config.settings``.  Injected here rather than re-read per request
        so the comparison cost is truly O(len(token)) with no I/O.
    token_cache:
        Optional ``app.runtime_state.ApiTokenCache`` — the in-process cache of active
        ``api_tokens`` rows (PF-AUTH-1, 1.9.4 W4). ``None`` (the default) reproduces the
        pre-1.9.4 behaviour exactly: only the bootstrap ``token`` is checked.
    vault_id:
        This backend instance's ``settings.vault_id``, used to enforce a matched token's
        optional vault scope. Ignored when ``token_cache`` is ``None``.
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str = "",
        token_cache: Any | None = None,
        vault_id: str = "",
    ) -> None:
        self._app = app
        # Read once at startup — never per-request (I3: no DB round-trip).
        # Token is stored as bytes for secrets.compare_digest (constant-time requires
        # both operands to be the same type; str works too but bytes is canonical).
        self._token: str = token
        # PF-AUTH-1 (1.9.4 W4): optional scoped-token cache + this instance's vault_id.
        self._token_cache: Any | None = token_cache
        self._vault_id: str = vault_id

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Only enforce on HTTP requests; let lifespan / WebSocket scopes pass through.
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Auth disabled (empty bootstrap token) → transparent pass-through (EC-M10-11).
        # PF-AUTH-1: scoped api_tokens are an ADDITIONAL credential layered on top of the
        # bootstrap gate, never a replacement for it — this branch (and its back-compat
        # guarantee) is completely unchanged.
        if not self._token:
            await self._app(scope, receive, send)
            return

        method: str = scope.get("method", "GET")
        path: str = scope.get("path", "/")

        # Exempt set: OPTIONS + probe/docs paths + MCP mount + clip ingress.
        if _bypass_auth(method, path):
            await self._app(scope, receive, send)
            return

        # Extract the bearer token from the Authorization header.
        # Header names in ASGI scope are lower-cased bytes.
        presented_token: str | None = None
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                raw: str = value.decode("latin-1")
                if raw.startswith("Bearer ") or raw.startswith("bearer "):
                    presented_token = raw[7:]
                break

        if presented_token is not None:
            # Constant-time comparison (ADR-0052 §2.1, I3 — closes timing side-channel).
            # secrets.compare_digest requires both operands to be the same type (str here).
            # NEVER compare with == (Do-NOT §9).
            # NEVER log the token value or the presented token (Do-NOT §2).
            if secrets.compare_digest(self._token, presented_token):
                await self._app(scope, receive, send)
                return

            # PF-AUTH-1 (1.9.4 W4): bootstrap token didn't match — try the scoped
            # api_tokens cache. NEVER logs presented_token or any stored hash.
            if self._token_cache is not None:
                entry = self._token_cache.find_match(presented_token)
                if entry is not None:
                    # Vault scope: a mismatch is treated identically to "no token
                    # matched" (401) — the token simply does not apply to this backend.
                    if entry.vault_id is not None and entry.vault_id != self._vault_id:
                        await self._respond_401(scope, receive, send)
                        return
                    # Read-only: any method other than GET/HEAD/OPTIONS is rejected.
                    if entry.read_only and method not in ("GET", "HEAD", "OPTIONS"):
                        await self._respond_403_read_only(scope, receive, send)
                        return
                    # Match — best-effort last_used_at bump. Awaited inline (not a
                    # detached background task — StaticPool/SQLite test sessions do not
                    # tolerate concurrent fire-and-forget writes); swallows any DB error
                    # so a persistence hiccup never turns into a request failure.
                    await self._touch_last_used(entry.id)
                    await self._app(scope, receive, send)
                    return

        await self._respond_401(scope, receive, send)

    @staticmethod
    async def _respond_401(scope: Scope, receive: Receive, send: Send) -> None:
        """Reject: 401 with RFC 6750 WWW-Authenticate: Bearer header."""
        response = JSONResponse(
            content=_UNAUTHORIZED_BODY,
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)

    @staticmethod
    async def _respond_403_read_only(scope: Scope, receive: Receive, send: Send) -> None:
        """PF-AUTH-1: a read_only api_tokens row attempted a non-read HTTP method."""
        response = JSONResponse(
            content={
                "error": "read_only_token",
                "hint": "This API token is read-only and cannot perform write requests.",
            },
            status_code=403,
        )
        await response(scope, receive, send)

    @staticmethod
    async def _touch_last_used(token_id: str) -> None:
        """
        Best-effort ``last_used_at`` bump for a matched scoped token.

        Swallows any DB error — a persistence hiccup on the audit timestamp must never
        turn into a request failure for an otherwise-valid token. NEVER logs the token
        secret (only the row id, which is not sensitive).
        """
        try:
            from app.runtime_state import bump_api_token_last_used  # noqa: PLC0415

            await bump_api_token_last_used(token_id)
        except Exception:  # noqa: BLE001 — best-effort audit write, never fatal
            logger.debug(
                "SynapseAuthMiddleware: last_used_at bump failed for token id=%s (non-fatal)",
                token_id,
            )
