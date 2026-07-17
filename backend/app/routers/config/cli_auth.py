"""Per-domain config router: /provider/cli-auth (ADR-0043).

Split out of the monolithic app.routers.config (BE-REFAC-1). Same paths/contract.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app import cli_auth as _cli_auth
from app import runtime_state
from app.config import settings
from app.models import VaultState
from app.routers.config.provider import _encrypt_api_key_or_400
from app.schemas.config import (
    CliAuthConfigRequest,
    CliAuthConfigResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/provider/cli-auth",
    response_model=CliAuthConfigResponse,
    summary="Read-only CLI subscription OAuth token posture (ADR-0043)",
    description=(
        "Returns the current posture of the CLI provider subscription token: "
        "token_configured (bool, never the value), token_source (db|env|none), "
        "auth_mode (api-key|subscription|unconfigured). "
        "Mirrors GET /clip/config: no sensitive values ever returned. "
        "ADR-0043 §2.5."
    ),
)
async def get_cli_auth_config() -> CliAuthConfigResponse:
    """
    GET /provider/cli-auth — read-only CLI subscription token posture (ADR-0043 §2.5).

    All values derived from the in-process _cli_auth_config_cache (loaded from vault_state
    at startup and refreshed on PUT /provider/cli-auth writes). No DB query on each GET.
    NEVER returns the token value, only posture fields.
    """
    cache = _cli_auth._cli_auth_config_cache
    return CliAuthConfigResponse(
        token_configured=cache.token_configured(),
        token_source=cache.token_source(),
        auth_mode=cache.auth_mode(),
    )


_CLI_TOKEN_PREFIX: str = "sk-ant-" + "oat01-"


@router.put(
    "/provider/cli-auth",
    response_model=CliAuthConfigResponse,
    summary="Set or clear the CLI subscription OAuth token (ADR-0043 / W7)",
    description=(
        "ADR-0043 §2.5 (W7 amendment) — store a pasted Claude subscription OAuth token or "
        "clear it. "
        "clear=true: set DB token to NULL (falls back to env / none). "
        "token=<value>: validate, Fernet-encrypt (requires SYNAPSE_SECRET_KEY — else HTTP 400), "
        "and store in vault_state.cli_oauth_token_encrypted; refresh cache. "
        "Returns post-write posture (same shape as GET); NEVER the token value. "
        "400 if body has neither token nor clear. "
        "400 if SYNAPSE_SECRET_KEY is unset when storing a new token (fail-closed). "
        "422 if token is empty/whitespace or absurd length. "
        "Soft prefix check warns but does NOT hard-reject — ADR-0043 §2.5."
    ),
)
async def put_cli_auth_config(body: CliAuthConfigRequest) -> CliAuthConfigResponse:
    """
    PUT /provider/cli-auth — set or clear the CLI subscription OAuth token (ADR-0043 §2.5,
    W7 encryption amendment).

    Semantics:
      1. clear=true (wins if both sent) → set cli_oauth_token_encrypted = NULL,
         cli_oauth_token = NULL (legacy); refresh cache.
      2. token=<value> → validate; Fernet-encrypt (SYNAPSE_SECRET_KEY — HTTP 400 if absent);
         store ciphertext in cli_oauth_token_encrypted; clear legacy cli_oauth_token; refresh
         cache with the plaintext (in-memory only, for outbound CLI injection).
      3. neither field → 400 (no-op request).
    Returns post-write posture. NEVER logs or returns the token value.
    """
    # 0. Guard: empty body (neither field set).
    if not body.clear and body.token is None:
        raise HTTPException(status_code=400, detail="Provide token or clear=true.")

    # Pre-validate the token BEFORE opening a DB session (no unnecessary DB round-trip
    # on bad input — mirrors the clip pattern of early-exit on validation failure).
    validated_token: str | None = None  # None = clear or will be set below
    token_encrypted: bytes | None = None  # Fernet ciphertext, set only on SET path

    if not body.clear:
        raw = (body.token or "").strip()
        if not raw:
            raise HTTPException(
                status_code=422,
                detail="token must be a non-empty, non-whitespace string.",
            )
        if len(raw) < 20 or len(raw) > 500:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"token length {len(raw)} is outside the accepted range [20, 500]. "
                    "Verify you pasted the full token from `claude setup-token`."
                ),
            )
        # Soft prefix check — warn but never hard-block (ADR-0043 §2.5).
        if not raw.startswith(_CLI_TOKEN_PREFIX):
            logger.warning(
                "PUT /provider/cli-auth: token does not match expected prefix; "
                "accepting anyway — Anthropic may change the prefix (ADR-0043 §2.5)."
                # NEVER log the token value itself.
            )
        # W7: encrypt BEFORE the DB session — fail early with 400 if key absent.
        token_encrypted = _encrypt_api_key_or_400(raw)
        validated_token = raw

    final_token: str | None = None  # plaintext for in-process cache; None after clear

    async with runtime_state.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state: VaultState | None = row.scalar_one_or_none()
        if state is None:
            # Seed row (mirrors the put_clip_config pattern).
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

        # 1. clear wins if both fields supplied (already validated above).
        if body.clear:
            # Null both columns — the legacy plaintext and the new encrypted column.
            state.cli_oauth_token_encrypted = None
            state.cli_oauth_token = None  # legacy column (kept for rollback safety)
        else:
            # 2. Store Fernet ciphertext (W7 — plaintext NEVER written to DB).
            state.cli_oauth_token_encrypted = token_encrypted
            # Null legacy plaintext column so the read path unambiguously uses the
            # encrypted column (no dual-state confusion after migration 0027).
            state.cli_oauth_token = None
            final_token = validated_token  # plaintext for in-process cache only

        state.updated_at = datetime.now(UTC)

    # 3. Refresh in-process cache with the plaintext (outside session — DB write committed).
    #    The cache holds the decrypted token in-memory ONLY — it is never written back to DB.
    await _cli_auth._cli_auth_config_cache.set_token(final_token)
    logger.info(
        "PUT /provider/cli-auth: token_source=%s auth_mode=%s (ADR-0043 / W7)",
        _cli_auth._cli_auth_config_cache.token_source(),
        _cli_auth._cli_auth_config_cache.auth_mode(),
        # NEVER log the token value
    )

    # 4. Return post-write posture (never the value).
    cache = _cli_auth._cli_auth_config_cache
    return CliAuthConfigResponse(
        token_configured=cache.token_configured(),
        token_source=cache.token_source(),
        auth_mode=cache.auth_mode(),
    )
