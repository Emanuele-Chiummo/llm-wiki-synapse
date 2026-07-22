"""
MCP OAuth 2.1 + PKCE authorization server (2.1.6, ADR-0090).

claude.ai's web "Custom connector" UI speaks ONLY OAuth 2.1 authorization_code + PKCE — it
cannot send a static bearer header the way Claude Desktop's JSON ``mcpServers`` config can
(that gap is what surfaced live: clicking "Connect" redirected the browser to
``<origin>/authorize?response_type=code&client_id=...&code_challenge=...`` and silently
landed on the frontend SPA, because neither ``/authorize`` nor the discovery/registration
paths existed). This module adds a minimal, single-operator-oriented authorization server so
that flow can complete, WITHOUT replacing the existing static bearer token
(``app.runtime_state.BearerAuthMiddleware`` / ADR-0033) — an OAuth-issued access token is
simply an ADDITIONAL way to satisfy the SAME ``/mcp/server`` gate (see
``runtime_state.McpOAuthTokenCache`` / ``_verify_bearer``).

Endpoints (mounted at the APP ROOT, not under ``/mcp/server`` — see ADR-0090 §2 for why):
    GET  /.well-known/oauth-authorization-server  — RFC 8414 discovery
    GET  /.well-known/oauth-protected-resource    — RFC 9728 discovery
    POST /register                                — RFC 7591 Dynamic Client Registration
    GET  /authorize                                — renders the consent form
    POST /authorize                                — verifies the STATIC MCP token, issues a
                                                      single-use code, redirects to redirect_uri
    POST /token                                    — authorization_code / refresh_token grants

Security model (single-operator, personal-vault deployment — see ADR-0090 §3):
  - Public clients only (PKCE S256 is the confidentiality mechanism; no client_secret is
    ever issued or stored — RFC 6749 §2.1: a public client's client_id is an identifier,
    not a secret).
  - The ONLY real credential in this whole flow is the SAME static MCP token that already
    gates ``/mcp/server`` directly (DB hash via ``PUT /mcp/auth``, or ``MCP_AUTH_TOKEN``
    env bootstrap) — the operator must type it once into the ``/authorize`` consent form to
    approve a grant. An OAuth-issued access token can NEVER be used to approve ANOTHER OAuth
    grant (no delegation chain) — ``verify_static_mcp_token`` deliberately excludes it.
  - Authorization codes are short-lived (120s), single-use, held in an in-process dict —
    NOT persisted — matching the single-process-deployment assumption already documented
    for ``RemoteMcpFlag``/``McpAuthCache`` in app.runtime_state.
  - Access/refresh tokens ARE persisted (PBKDF2-hashed, never plaintext — same helpers as
    ``api_tokens.secret_hash`` / ``vault_state.mcp_access_token_hash``) so they survive a
    backend restart; refresh is rotate-on-use (OAuth 2.1 best practice).
  - Every route here shares the SAME floor as ``/mcp/server`` itself: when
    ``remote_mcp_enabled`` (ADR-0032) is OFF, all of these 404 — the whole OAuth surface is
    closed exactly when the MCP surface it grants access to is closed.
  - JIT (just-in-time) client registration at ``/authorize``: some MCP clients (observed
    live with claude.ai) self-assign a client_id and skip ``POST /register`` entirely when
    no ``registration_endpoint`` was discoverable at the time the connector was first added.
    Requiring strict pre-registration would break that already-configured connector. Once a
    client_id is bound to a redirect_uri (whether via explicit registration or JIT), it can
    NEVER be silently rebound to a different redirect_uri (open-redirect guard).
"""

from __future__ import annotations

import base64
import hashlib
import html as _html
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app import runtime_state
from app.models import McpOAuthClient, McpOAuthToken

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Bounds (I7-style — every stateful thing here is bounded) ─────────────────
_AUTH_CODE_TTL_SECONDS = 120
_ACCESS_TOKEN_TTL_SECONDS = 3600  # 1 hour
_REFRESH_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 90  # 90 days
_MAX_REGISTERED_CLIENTS = 200  # DCR abuse ceiling — see register_client()


def _gate_open() -> bool:
    """OAuth endpoints share the SAME floor as /mcp/server (ADR-0032, amended by ADR-0090)."""
    return runtime_state.remote_mcp_flag.is_enabled()


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


def _issuer(request: Request) -> str:
    """Build the issuer URL from the incoming request — no hardcoded domain (I6)."""
    return f"{request.url.scheme}://{request.url.netloc}"


# ── In-process authorization-code store (ephemeral — see module docstring) ───


@dataclass
class _PendingCode:
    client_id: str
    redirect_uri: str
    code_challenge: str
    expires_at: float  # time.monotonic()-based


_codes: dict[str, _PendingCode] = {}


def _prune_expired_codes() -> None:
    now = time.monotonic()
    for code in [c for c, pc in _codes.items() if pc.expires_at <= now]:
        _codes.pop(code, None)


def _issue_code(client_id: str, redirect_uri: str, code_challenge: str) -> str:
    _prune_expired_codes()
    code = secrets.token_urlsafe(32)
    _codes[code] = _PendingCode(
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        expires_at=time.monotonic() + _AUTH_CODE_TTL_SECONDS,
    )
    return code


def _consume_code(code: str) -> _PendingCode | None:
    """Pop (single-use) and return the pending code, or None if unknown/expired."""
    _prune_expired_codes()
    return _codes.pop(code, None)


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """S256 PKCE check (RFC 7636 §4.6): BASE64URL(SHA256(code_verifier)) == code_challenge."""
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, code_challenge)


def _valid_redirect_uri(uri: str) -> bool:
    """https:// always allowed; http://localhost allowed for local/dev testing only."""
    return uri.startswith("https://") or uri.startswith("http://localhost")


# ── Discovery (RFC 8414 / RFC 9728) ───────────────────────────────────────────


@router.get("/.well-known/oauth-authorization-server", include_in_schema=False)
async def oauth_authorization_server_metadata(request: Request) -> dict[str, Any]:
    if not _gate_open():
        raise _not_found()
    issuer = _issuer(request)
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "registration_endpoint": f"{issuer}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }


@router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
async def oauth_protected_resource_metadata(request: Request) -> dict[str, Any]:
    if not _gate_open():
        raise _not_found()
    issuer = _issuer(request)
    return {
        "resource": f"{issuer}{runtime_state.MCP_MOUNT_PATH}",
        "authorization_servers": [issuer],
    }


# ── Dynamic Client Registration (RFC 7591) ────────────────────────────────────


class ClientRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    redirect_uris: list[str] = Field(default_factory=list)
    client_name: str | None = None


class ClientRegistrationResponse(BaseModel):
    client_id: str
    client_id_issued_at: int
    redirect_uris: list[str]
    client_name: str | None = None
    token_endpoint_auth_method: str = "none"  # noqa: S105 — OAuth metadata value, not a secret
    grant_types: list[str] = Field(default_factory=lambda: ["authorization_code", "refresh_token"])
    response_types: list[str] = Field(default_factory=lambda: ["code"])


@router.post(
    "/register",
    status_code=201,
    response_model=ClientRegistrationResponse,
    include_in_schema=False,
)
async def register_client(body: ClientRegistrationRequest) -> ClientRegistrationResponse:
    if not _gate_open():
        raise _not_found()
    if not body.redirect_uris:
        raise HTTPException(status_code=400, detail="redirect_uris is required")
    for uri in body.redirect_uris:
        if not _valid_redirect_uri(uri):
            raise HTTPException(
                status_code=400,
                detail=f"redirect_uri must be https:// (or http://localhost for testing): {uri!r}",
            )

    async with runtime_state.get_session() as session:
        count = (
            await session.execute(select(func.count()).select_from(McpOAuthClient))
        ).scalar_one()
        if count >= _MAX_REGISTERED_CLIENTS:
            raise HTTPException(
                status_code=429,
                detail="Too many registered MCP OAuth clients — contact the operator",
            )
        client_id = secrets.token_urlsafe(24)
        row = McpOAuthClient(
            client_id=client_id, client_name=body.client_name, redirect_uris=body.redirect_uris
        )
        session.add(row)
        await session.flush()
        created_at = row.created_at

    logger.info(
        "POST /register: registered MCP OAuth client_id=%s name=%r", client_id, body.client_name
    )

    return ClientRegistrationResponse(
        client_id=client_id,
        client_id_issued_at=int(created_at.timestamp()),
        redirect_uris=body.redirect_uris,
        client_name=body.client_name,
    )


# ── Authorization endpoint (consent form) ─────────────────────────────────────


def _render_consent_form(
    *, client_id: str, redirect_uri: str, code_challenge: str, state: str, error: str | None = None
) -> str:
    """Minimal, dependency-free HTML consent page. Every interpolated value originates from
    an untrusted query string / prior form post — html.escape() everywhere (reflected-XSS
    guard); values are carried through as hidden fields, never rendered as raw HTML/JS."""
    error_html = (
        f'<p style="color:#b00020;font-weight:600">{_html.escape(error)}</p>' if error else ""
    )
    body_style = (
        "font-family: system-ui, -apple-system, sans-serif; max-width: 440px; "
        "margin: 4rem auto; padding: 0 1.5rem; color: #1a1a1a;"
    )
    label_style = "display:block;margin-top:1rem;font-weight:600;"
    input_style = (
        "width:100%;box-sizing:border-box;padding:.6rem;margin:.4rem 0 1rem;font-size:1rem;"
    )
    button_style = "padding:.6rem 1.5rem;font-size:1rem;cursor:pointer;"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Authorize MCP access — Synapse</title>
</head>
<body style="{body_style}">
<h1 style="font-size: 1.25rem;">Authorize MCP access</h1>
<p>An application is requesting access to your Synapse MCP server
(<code>{_html.escape(runtime_state.MCP_MOUNT_PATH)}</code>).</p>
{error_html}
<form method="post" action="/authorize">
  <input type="hidden" name="client_id" value="{_html.escape(client_id)}">
  <input type="hidden" name="redirect_uri" value="{_html.escape(redirect_uri)}">
  <input type="hidden" name="code_challenge" value="{_html.escape(code_challenge)}">
  <input type="hidden" name="state" value="{_html.escape(state)}">
  <label for="mcp_token" style="{label_style}">Synapse MCP token</label>
  <input type="password" id="mcp_token" name="mcp_token" required autofocus
         style="{input_style}">
  <button type="submit" style="{button_style}">Authorize</button>
</form>
</body>
</html>"""


@router.get("/authorize", include_in_schema=False)
async def authorize_form(
    request: Request,
    response_type: str = "",
    client_id: str = "",
    redirect_uri: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "",
    state: str = "",
) -> HTMLResponse:
    if not _gate_open():
        raise _not_found()
    if response_type != "code":
        raise HTTPException(status_code=400, detail="response_type must be 'code'")
    if code_challenge_method != "S256":
        raise HTTPException(
            status_code=400, detail="code_challenge_method must be 'S256' (PKCE required)"
        )
    if not client_id or not code_challenge:
        raise HTTPException(status_code=400, detail="client_id and code_challenge are required")
    if not redirect_uri or not _valid_redirect_uri(redirect_uri):
        raise HTTPException(
            status_code=400,
            detail="redirect_uri must be https:// (or http://localhost for local testing)",
        )

    # If the client_id is already bound to a client, redirect_uri must match one of its
    # registered URIs BEFORE rendering the form (open-redirect guard — never approve, or
    # even solicit approval, against an unbound URI switch for an established client_id).
    async with runtime_state.get_session() as session:
        existing = await session.get(McpOAuthClient, client_id)
    if existing is not None and redirect_uri not in existing.redirect_uris:
        raise HTTPException(
            status_code=400, detail="redirect_uri does not match the registered client"
        )

    return HTMLResponse(
        _render_consent_form(
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            state=state,
        )
    )


@router.post("/authorize", include_in_schema=False, response_model=None)
async def authorize_submit(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    code_challenge: str = Form(...),
    state: str = Form(""),
    mcp_token: str = Form(...),
) -> HTMLResponse | RedirectResponse:
    if not _gate_open():
        raise _not_found()
    if not redirect_uri or not _valid_redirect_uri(redirect_uri):
        raise HTTPException(status_code=400, detail="invalid redirect_uri")

    if not runtime_state.verify_static_mcp_token(mcp_token):
        return HTMLResponse(
            _render_consent_form(
                client_id=client_id,
                redirect_uri=redirect_uri,
                code_challenge=code_challenge,
                state=state,
                error="Incorrect token — try again.",
            ),
            status_code=401,
        )

    # JIT-register an unseen client_id (see module docstring); reject a redirect_uri switch
    # for an already-bound one.
    async with runtime_state.get_session() as session:
        existing = await session.get(McpOAuthClient, client_id)
        if existing is None:
            session.add(
                McpOAuthClient(client_id=client_id, client_name=None, redirect_uris=[redirect_uri])
            )
            logger.info(
                "POST /authorize: JIT-registered previously-unseen client_id=%s redirect_uri=%s",
                client_id,
                redirect_uri,
            )
        elif redirect_uri not in existing.redirect_uris:
            raise HTTPException(
                status_code=400, detail="redirect_uri does not match the registered client"
            )

    code = _issue_code(client_id, redirect_uri, code_challenge)
    separator = "&" if "?" in redirect_uri else "?"
    location = f"{redirect_uri}{separator}code={quote(code)}"
    if state:
        location += f"&state={quote(state)}"

    logger.info("POST /authorize: approved, issued code for client_id=%s", client_id)
    return RedirectResponse(location, status_code=302)


# ── Token endpoint ─────────────────────────────────────────────────────────────


async def _mint_tokens(client_id: str) -> dict[str, Any]:
    access_plain = secrets.token_urlsafe(32)
    refresh_plain = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=_ACCESS_TOKEN_TTL_SECONDS)
    refresh_expires_at = now + timedelta(seconds=_REFRESH_TOKEN_TTL_SECONDS)

    access_hash = runtime_state.hash_token(access_plain)
    refresh_hash = runtime_state.hash_token(refresh_plain)

    async with runtime_state.get_session() as session:
        row = McpOAuthToken(
            client_id=client_id,
            access_token_hash=access_hash,
            refresh_token_hash=refresh_hash,
            expires_at=expires_at,
            refresh_expires_at=refresh_expires_at,
        )
        session.add(row)
        await session.flush()
        row_id = row.id

    await runtime_state.mcp_oauth_token_cache.add(str(row_id), access_hash, expires_at)

    logger.info(
        "POST /token: minted access/refresh token pair for client_id=%s (row id=%s)",
        client_id,
        row_id,
    )

    return {
        "access_token": access_plain,
        "token_type": "Bearer",
        "expires_in": _ACCESS_TOKEN_TTL_SECONDS,
        "refresh_token": refresh_plain,
    }


async def _rotate_tokens(client_id: str, refresh_plain: str) -> dict[str, Any]:
    """Rotate-on-use (OAuth 2.1 best practice): revoke the presented refresh_token's row and
    mint a brand-new access/refresh pair. Any reuse of an already-rotated refresh_token fails
    (revoked_at is set), which is the standard replay-detection signal."""
    now = datetime.now(UTC)
    async with runtime_state.get_session() as session:
        result = await session.execute(
            select(McpOAuthToken).where(
                McpOAuthToken.client_id == client_id,
                McpOAuthToken.revoked_at.is_(None),
            )
        )
        matched: McpOAuthToken | None = None
        for row in result.scalars():
            if runtime_state.as_aware_utc(row.refresh_expires_at) <= now:
                continue
            if runtime_state.verify_token(refresh_plain, row.refresh_token_hash):
                matched = row
                break
        if matched is None:
            raise HTTPException(
                status_code=400,
                detail="invalid_grant: unknown, expired, or already-rotated refresh_token",
            )
        matched.revoked_at = now
        revoked_id = matched.id

    await runtime_state.mcp_oauth_token_cache.remove(str(revoked_id))
    return await _mint_tokens(client_id)


@router.post("/token", include_in_schema=False)
async def token_endpoint(
    grant_type: str = Form(...),
    code: str | None = Form(None),
    redirect_uri: str | None = Form(None),
    client_id: str | None = Form(None),
    code_verifier: str | None = Form(None),
    refresh_token: str | None = Form(None),
) -> dict[str, Any]:
    if not _gate_open():
        raise _not_found()

    if grant_type == "authorization_code":
        if not (code and redirect_uri and client_id and code_verifier):
            raise HTTPException(
                status_code=400,
                detail="code, redirect_uri, client_id, code_verifier are required",
            )
        pending = _consume_code(code)
        if pending is None:
            raise HTTPException(status_code=400, detail="invalid_grant: unknown or expired code")
        if pending.client_id != client_id or pending.redirect_uri != redirect_uri:
            raise HTTPException(
                status_code=400, detail="invalid_grant: client_id/redirect_uri mismatch"
            )
        if not _verify_pkce(code_verifier, pending.code_challenge):
            raise HTTPException(status_code=400, detail="invalid_grant: PKCE verification failed")
        return await _mint_tokens(client_id)

    if grant_type == "refresh_token":
        if not (refresh_token and client_id):
            raise HTTPException(status_code=400, detail="refresh_token and client_id are required")
        return await _rotate_tokens(client_id, refresh_token)

    raise HTTPException(status_code=400, detail=f"unsupported grant_type: {grant_type!r}")


__all__ = ["router"]
