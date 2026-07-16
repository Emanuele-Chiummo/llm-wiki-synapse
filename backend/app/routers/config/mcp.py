"""Per-domain config router: /mcp/info + /mcp/remote + /mcp/remote-write + /mcp/auth.

Split out of the monolithic app.routers.config (BE-REFAC-1). Same paths/contract.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter
from sqlalchemy import select

from app import runtime_state
from app.config import settings
from app.mcp.server import mcp as _mcp_server
from app.models import VaultState
from app.schemas.config import (
    McpAuthRequest,
    McpAuthStateResponse,
    McpInfoResponse,
    McpRemoteStateResponse,
    McpRemoteToggleRequest,
    McpRemoteWriteStateResponse,
    McpRemoteWriteToggleRequest,
    McpToolInfo,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/mcp/info",
    response_model=McpInfoResponse,
    summary="Read-only MCP server introspection",
    description=(
        "Returns the live FastMCP server metadata: name, transport, entry-point command, "
        "and the full list of registered tools (name, description, input_schema). "
        "All values are derived from the live `mcp` object and settings — nothing hardcoded (I6). "
        "No MCP transport session is opened; no tool is invoked (I9). "
        "Read-only — edit MCP_TRANSPORT / MCP_ENTRY_COMMAND env vars to change. "
        "F1-MCP-UI (ADR-0027 §2.1)."
    ),
)
async def get_mcp_info() -> McpInfoResponse:
    """
    GET /mcp/info — read-only introspection of the Synapse FastMCP server (ADR-0027 §2.1).

    Derives every value from the live `mcp` object (imported at module level) and `settings`.
    No string about the MCP server is hardcoded inside this function (I6).
    No DB query, no Qdrant call, no MCP transport/stdio session is opened (I9).
    """
    # Introspect the live FastMCP registry — await directly in async handler (ADR-0027 §2.2).
    raw_tools = await _mcp_server.list_tools()

    tools: list[McpToolInfo] = [
        McpToolInfo(
            name=t.name,
            description=t.description or "",
            input_schema=t.parameters if t.parameters is not None else {},
        )
        for t in raw_tools
    ]

    # ADR-0033 §2.5: resolve token source from in-process cache.
    # NEVER return token/hash/salt — only boolean-derived values.
    db_hash = runtime_state.mcp_auth_cache.get_hash()
    tok_source = runtime_state.resolve_token_source(db_hash)
    tok_configured = runtime_state.token_configured(db_hash)

    return McpInfoResponse(
        server_name=_mcp_server.name,
        transport=settings.mcp_transport,
        entry_point_command=settings.mcp_entry_command,
        tool_count=len(tools),
        tools=tools,
        # ADR-0029 §2.5 — always-mount (ADR-0033 §2.4); token NEVER returned
        http_enabled=True,  # always-mount (ADR-0033 §2.4)
        # ADR-0072 §5: report effective runtime flag (DB-wins-else-env), not the raw env var.
        remote_write_enabled=runtime_state.mcp_write_flag.is_enabled(),
        # ADR-0032 §2.5 — runtime toggle posture
        token_configured=tok_configured,
        remote_enabled=runtime_state.remote_mcp_flag.is_enabled(),
        mount_path=runtime_state.MCP_MOUNT_PATH,
        # ADR-0033 §2.5 — token source + allow flag; NEVER the token/hash/salt
        token_source=tok_source,
        allow_without_token=runtime_state.mcp_auth_cache.allow_without_token(),
    )


@router.put(
    "/mcp/remote",
    response_model=McpRemoteStateResponse,
    summary="Toggle the remote MCP HTTP surface at runtime",
    description=(
        "Persists vault_state.remote_mcp_enabled for the active vault and refreshes the "
        "in-process RemoteMcpFlag cache immediately (ADR-0032 §2.2/§2.4). "
        "Token-floor clamp: if MCP_AUTH_TOKEN is unset and enabled=true, the flag is "
        "forced to false and clamped=true is returned (HTTP 200). "
        "enabled=false always succeeds. "
        "Same-origin / unauthenticated — consistent with the rest of the REST API "
        "(ADR-0028 / ADR-0032 §2.4). "
        "F1-MCP-UI (ADR-0032)."
    ),
)
async def put_mcp_remote(body: McpRemoteToggleRequest) -> McpRemoteStateResponse:
    """
    PUT /mcp/remote — persist the runtime MCP toggle (ADR-0032 §2.4; amended by ADR-0033 §2.4/§2.5).

    Allow-aware clamp (ADR-0033 §2.4): enabling is permitted when EITHER
    ``token_configured OR allow_without_token``. Without either, enabling remote is
    pointless (the surface 404s for everyone), so we clamp to OFF.
    On success: write vault_state, refresh RemoteMcpFlag cache.
    No MCP tool is invoked; no second writer is introduced (I9).
    """
    db_hash = runtime_state.mcp_auth_cache.get_hash()
    tok_configured: bool = runtime_state.token_configured(db_hash)
    allow: bool = runtime_state.mcp_auth_cache.allow_without_token()
    clamped: bool = False
    desired: bool = body.enabled

    # Allow-aware clamp (ADR-0033 §2.4): cannot enable without token OR allow.
    if desired and not tok_configured and not allow:
        desired = False
        clamped = True

    # Persist to vault_state (DB is source of truth — ADR-0032 §2.1).
    async with runtime_state.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Should not happen (seeded at startup), but be defensive.
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_enabled=desired,
                updated_at=datetime.now(UTC),
            )
            session.add(state)
        else:
            state.remote_mcp_enabled = desired
            state.updated_at = datetime.now(UTC)

    # Refresh the in-process cache immediately (ADR-0032 §2.2).
    await runtime_state.remote_mcp_flag.set(desired)

    logger.info(
        "PUT /mcp/remote: enabled=%s clamped=%s tok_configured=%s allow=%s (ADR-0032/0033)",
        desired,
        clamped,
        tok_configured,
        allow,
    )

    return McpRemoteStateResponse(
        remote_enabled=desired,
        token_configured=tok_configured,
        mount_path=runtime_state.MCP_MOUNT_PATH,
        clamped=clamped,
    )


@router.put(
    "/mcp/remote-write",
    response_model=McpRemoteWriteStateResponse,
    summary="Toggle the remote MCP write tools at runtime",
    description=(
        "Persists vault_state.remote_mcp_write_enabled for the active vault and refreshes the "
        "in-process _mcp_write_flag cache immediately (ADR-0072 §2/§4). "
        "Token-floor clamp: if neither a token is configured (DB hash or MCP_AUTH_TOKEN env) "
        "nor allow_without_token is set, and enabled=true, the flag is forced to false and "
        "clamped=true is returned (HTTP 200). enabled=false always succeeds. "
        "Write tools (write_page, resolve_review, trigger_source_rescan) are always listed "
        "on the HTTP surface but error at call time when the flag is off (ADR-0072 §3). "
        "Same-origin / unauthenticated — consistent with the rest of the REST API "
        "(ADR-0028 / ADR-0072 §4). "
        "F17 [ADR-0072]."
    ),
)
async def put_mcp_remote_write(body: McpRemoteWriteToggleRequest) -> McpRemoteWriteStateResponse:
    """
    PUT /mcp/remote-write — persist the runtime write-tools toggle (ADR-0072 §4).

    Allow-aware clamp (mirrors ADR-0033 §2.4 / ADR-0072 §4): enabling is permitted when EITHER
    ``token_configured OR allow_without_token``. Without either, enabling write tools is
    pointless (the MCP surface itself is token-gated or not reachable), so we clamp to OFF.
    On success: write vault_state, refresh _mcp_write_flag cache.
    No MCP tool is invoked; no second writer is introduced (I9). Single write path preserved (I6).
    """
    db_hash = runtime_state.mcp_auth_cache.get_hash()
    tok_configured: bool = runtime_state.token_configured(db_hash)
    allow: bool = runtime_state.mcp_auth_cache.allow_without_token()
    clamped: bool = False
    desired: bool = body.enabled

    # Allow-aware clamp (ADR-0072 §4): cannot enable without token OR allow.
    if desired and not tok_configured and not allow:
        desired = False
        clamped = True

    # Persist to vault_state (DB is source of truth — ADR-0072 §1).
    async with runtime_state.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Should not happen (seeded at startup), but be defensive.
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_write_enabled=desired,
                updated_at=datetime.now(UTC),
            )
            session.add(state)
        else:
            state.remote_mcp_write_enabled = desired
            state.updated_at = datetime.now(UTC)

    # Refresh the in-process cache immediately (ADR-0072 §2).
    await runtime_state.mcp_write_flag.set(desired)

    logger.info(
        "PUT /mcp/remote-write: enabled=%s clamped=%s tok_configured=%s allow=%s (ADR-0072)",
        desired,
        clamped,
        tok_configured,
        allow,
    )

    return McpRemoteWriteStateResponse(
        remote_write_enabled=desired,
        token_configured=tok_configured,
        clamped=clamped,
    )


@router.put(
    "/mcp/auth",
    response_model=McpAuthStateResponse,
    summary="Set, rotate, or clear the MCP access token + allow-without-token flag",
    description=(
        "ADR-0033 §2.5 — UI-settable MCP token management. "
        "rotate_token=true: generate a new token (secrets.token_urlsafe(32)), store its "
        "PBKDF2 hash in vault_state, return plaintext ONCE in generated_token. "
        "token=<value>: store an explicit token as hash; NOT echoed; generated_token=null. "
        "clear_token=true: set hash to NULL (token_source may fall back to env or none). "
        "allow_without_token: persist the private-source allow flag. "
        "If post-write state has no token AND allow_without_token=false, remote_enabled "
        "is clamped OFF (allow-aware clamp — ADR-0033 §2.4). "
        "Same-origin / unauthenticated (consistent with ADR-0032 §2.4). "
        "NEVER returns or stores token plaintext (except the one-time generated_token). "
        "F1-MCP-UI (ADR-0033)."
    ),
)
async def put_mcp_auth(body: McpAuthRequest) -> McpAuthStateResponse:
    """
    PUT /mcp/auth — UI-settable MCP token management (ADR-0033 §2.5).

    Applies changes in this order:
      1. clear_token (if true) → set hash NULL.
      2. token (if set) → hash and persist.
      3. rotate_token (if true) → generate, hash, persist, capture plaintext.
      4. allow_without_token (if set) → persist.
      5. Apply allow-aware clamp to remote_enabled (§2.4).
      6. Refresh in-process caches.
      7. Return McpAuthStateResponse (no plaintext except generated_token).

    No MCP tool is invoked; no second writer is introduced (I9).
    """
    generated_token: str | None = None

    async with runtime_state.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Should not happen (seeded at startup), but be defensive.
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_enabled=False,
                mcp_access_token_hash=None,
                mcp_allow_without_token=False,
                updated_at=datetime.now(UTC),
            )
            session.add(state)

        # 1. clear_token
        if body.clear_token:
            state.mcp_access_token_hash = None

        # 2. explicit token
        if body.token is not None:
            state.mcp_access_token_hash = runtime_state.hash_token(body.token)
            # Do NOT echo body.token in logs or response.

        # 3. rotate_token (takes precedence over explicit token if both are set)
        if body.rotate_token:
            new_plaintext = secrets.token_urlsafe(32)
            state.mcp_access_token_hash = runtime_state.hash_token(new_plaintext)
            # Capture plaintext for the one-time response; NEVER persist it.
            generated_token = new_plaintext
            # Immediately discard from local scope after assigning to response var;
            # new_plaintext goes out of scope here.

        # 4. allow_without_token
        if body.allow_without_token is not None:
            state.mcp_allow_without_token = body.allow_without_token

        # 5. Allow-aware clamp on remote_enabled (ADR-0033 §2.4).
        new_hash = state.mcp_access_token_hash
        new_allow = state.mcp_allow_without_token
        tok_configured_post = runtime_state.token_configured(new_hash)
        if state.remote_mcp_enabled and not tok_configured_post and not new_allow:
            state.remote_mcp_enabled = False
            logger.info(
                "PUT /mcp/auth: remote_enabled clamped OFF "
                "(no token AND allow=false, ADR-0033 §2.4)"
            )

        state.updated_at = datetime.now(UTC)

        # Capture final values for cache update (inside session scope).
        final_hash = state.mcp_access_token_hash
        final_allow = state.mcp_allow_without_token
        final_remote = state.remote_mcp_enabled

    # 6. Refresh in-process caches (outside session — DB write committed).
    await runtime_state.mcp_auth_cache.set_hash(final_hash)
    await runtime_state.mcp_auth_cache.set_allow(final_allow)
    await runtime_state.remote_mcp_flag.set(final_remote)

    # 7. Derive response values (NEVER return hash, plaintext, or salt).
    tok_source = runtime_state.resolve_token_source(final_hash)
    tok_configured = runtime_state.token_configured(final_hash)

    logger.info(
        "PUT /mcp/auth: token_source=%s allow_without_token=%s remote_enabled=%s (ADR-0033)",
        tok_source,
        final_allow,
        final_remote,
    )

    return McpAuthStateResponse(
        token_configured=tok_configured,
        token_source=tok_source,
        allow_without_token=final_allow,
        remote_enabled=final_remote,
        mount_path=runtime_state.MCP_MOUNT_PATH,
        generated_token=generated_token,
    )
