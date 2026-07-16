"""Per-domain config router: /clip/config (ADR-0040).

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
from app.models import VaultState
from app.schemas.config import (
    ClipConfigRequest,
    ClipConfigResponse,
    ClipConfigStateResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/clip/config",
    response_model=ClipConfigResponse,
    summary="Read-only web clipper ingress posture (ADR-0040)",
    description=(
        "Returns the current posture of the POST /clip ingress: enabled state, "
        "token_configured (bool, never the value), token_source (db|env|none), "
        "allowed_origins list, and max_body_bytes. "
        "Mirrors GET /mcp/info: no sensitive values ever returned. "
        "F11-clip-config (ADR-0040)."
    ),
)
async def get_clip_config() -> ClipConfigResponse:
    """
    GET /clip/config — read-only web clipper ingress posture (ADR-0040).

    All values derived from the in-process runtime_state.clip_config_cache (loaded from vault_state
    at startup and refreshed on PUT /clip/config writes). No DB query on each GET.
    NEVER returns the token value, only token_configured + token_source.
    """
    return ClipConfigResponse(
        enabled=runtime_state.clip_config_cache.resolved_enabled(),
        token_configured=runtime_state.clip_config_cache.token_configured(),
        token_source=runtime_state.clip_config_cache.token_source(),
        allowed_origins=runtime_state.clip_config_cache.resolved_allowed_origins_list(),
        max_body_bytes=settings.clip_max_body_bytes,
    )


@router.put(
    "/clip/config",
    response_model=ClipConfigStateResponse,
    summary="Set, rotate, or clear the clip ingress token + enabled/origins (ADR-0040)",
    description=(
        "ADR-0040 §2.4 — runtime web clipper configuration. "
        "rotate_token=true: generate a new token (secrets.token_urlsafe(32)), store its "
        "PBKDF2-SHA256 hash in vault_state.clip_access_token, return plaintext ONCE in "
        "generated_token (never stored). "
        "clear_token=true: set DB token to NULL (falls back to CLIP_TOKEN env or none). "
        "set_enabled: set clip_enabled_db (DB wins over CLIP_ENABLED env when set). "
        'set_allowed_origins: replace DB origins (empty string "" clears to env fallback). '
        "Same-origin / unauthenticated — consistent with PUT /mcp/auth (ADR-0033 §2.5). "
        "NEVER returns or stores the token plaintext (except the one-time generated_token). "
        "F11-clip-config (ADR-0040)."
    ),
)
async def put_clip_config(body: ClipConfigRequest) -> ClipConfigStateResponse:
    """
    PUT /clip/config — runtime web clipper configuration (ADR-0040 §2.4).

    Applies changes in this order:
      1. clear_token (if true) → set clip_access_token = NULL.
      2. rotate_token (if true) → generate plaintext, hash with PBKDF2, store hash,
         capture plaintext for one-time response (never persisted).
      3. set_enabled (if set) → persist clip_enabled_db.
      4. set_allowed_origins (if set) → persist clip_allowed_origins_db
         (empty string → NULL = env-fallback).
      5. Refresh in-process runtime_state.clip_config_cache.
      6. Return ClipConfigStateResponse (no token plaintext except one-time generated_token).

    Mirrors PUT /mcp/auth (ADR-0033 §2.5).
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
                clip_enabled_db=None,
                clip_access_token=None,
                clip_allowed_origins_db=None,
                updated_at=datetime.now(UTC),
            )
            session.add(state)

        # 1. clear_token
        if body.clear_token:
            state.clip_access_token = None

        # 2. rotate_token (takes precedence over clear if both are set)
        if body.rotate_token:
            new_plaintext = secrets.token_urlsafe(32)
            # Hash for storage (mirrors MCP ADR-0033 §2.1 — never store plaintext in DB).
            # The PBKDF2 hash is safe even if the DB is compromised.
            state.clip_access_token = runtime_state.hash_token(new_plaintext)
            # Capture plaintext for the one-time response ONLY (never persisted).
            generated_token = new_plaintext
            # new_plaintext out of scope after assignment to generated_token.

        # 3. set_enabled
        if body.set_enabled is not None:
            state.clip_enabled_db = body.set_enabled

        # 4. set_allowed_origins (empty string → NULL = env-fallback)
        if body.set_allowed_origins is not None:
            state.clip_allowed_origins_db = (
                body.set_allowed_origins if body.set_allowed_origins else None
            )

        state.updated_at = datetime.now(UTC)

        # Capture final values for cache update (inside session scope — will be committed).
        # clip_access_token is now a PBKDF2 hash (or None); store hash in cache.
        final_hash: str | None = state.clip_access_token
        final_enabled_db: bool | None = state.clip_enabled_db
        final_origins_db: str | None = state.clip_allowed_origins_db

    # 5. Refresh in-process caches (outside session — DB write committed).
    await runtime_state.clip_config_cache.set_hash(final_hash)
    await runtime_state.clip_config_cache.set_enabled_db(final_enabled_db)
    await runtime_state.clip_config_cache.set_allowed_origins_db(final_origins_db)

    tok_source = runtime_state.clip_config_cache.token_source()
    tok_configured = runtime_state.clip_config_cache.token_configured()
    resolved_enabled = runtime_state.clip_config_cache.resolved_enabled()
    resolved_origins = runtime_state.clip_config_cache.resolved_allowed_origins_list()

    logger.info(
        "PUT /clip/config: enabled=%s token_source=%s origins_source=%s (ADR-0040)",
        resolved_enabled,
        tok_source,
        runtime_state.clip_config_cache.origins_source(),
        # NEVER log the token value
    )

    # 6. Return posture (no plaintext except the one-time generated_token).
    return ClipConfigStateResponse(
        enabled=resolved_enabled,
        token_configured=tok_configured,
        token_source=tok_source,
        allowed_origins=resolved_origins,
        max_body_bytes=settings.clip_max_body_bytes,
        generated_token=generated_token,
    )
