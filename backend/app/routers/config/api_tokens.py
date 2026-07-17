"""Per-domain config router: /config/api-tokens (PF-AUTH-1, 1.9.4 W4).

Scoped, revocable API tokens layered on top of the single SYNAPSE_AUTH_TOKEN bootstrap
bearer (app/auth.py, ADR-0052). See app.models.ApiToken and app.runtime_state.ApiTokenCache
for the storage/verification design.
"""

from __future__ import annotations

import logging
import secrets as _secrets
import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app import runtime_state
from app.models import ApiToken
from app.runtime_state import ApiTokenEntry
from app.schemas.config import (
    ApiTokenCreateRequest,
    ApiTokenCreateResponse,
    ApiTokenListItem,
    ApiTokenListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post(
    "/config/api-tokens",
    response_model=ApiTokenCreateResponse,
    status_code=201,
    summary="Create a scoped API token",
    description=(
        "Generates a new bearer secret (secrets.token_urlsafe), persists only its "
        "PBKDF2-SHA256 hash (app.runtime_state.hash_token), and returns the PLAINTEXT "
        "secret exactly once in this response. It is unrecoverable after that — losing it "
        "means creating a new token and revoking this one. "
        "vault_id=null (default) creates a global token; a non-null vault_id scopes the "
        "token to that vault (rejected as an invalid token on any other vault_id). "
        "read_only=true restricts the token to GET/HEAD/OPTIONS requests. "
        "PF-AUTH-1 (1.9.4 W4)."
    ),
)
async def create_api_token(body: ApiTokenCreateRequest) -> ApiTokenCreateResponse:
    """POST /config/api-tokens — create + persist a scoped API token (PF-AUTH-1)."""
    plaintext = _secrets.token_urlsafe(32)
    secret_hash = runtime_state.hash_token(plaintext)

    async with runtime_state.get_session() as session:
        row = ApiToken(
            label=body.label,
            secret_hash=secret_hash,
            vault_id=body.vault_id,
            read_only=body.read_only,
        )
        session.add(row)
        await session.flush()
        row_id = row.id
        created_at = row.created_at

    # Refresh the in-process cache immediately so the token is usable on the very next
    # request (mirrors mcp_auth_cache / clip_config_cache — I2 no debounce needed here).
    await runtime_state.api_token_cache.add(
        ApiTokenEntry(
            id=str(row_id),
            label=body.label,
            secret_hash=secret_hash,
            vault_id=body.vault_id,
            read_only=body.read_only,
        )
    )

    logger.info(
        "POST /config/api-tokens: created id=%s label=%r vault_id=%r read_only=%s "
        "(PF-AUTH-1; plaintext NEVER logged)",
        row_id,
        body.label,
        body.vault_id,
        body.read_only,
    )

    return ApiTokenCreateResponse(
        id=row_id,
        label=body.label,
        vault_id=body.vault_id,
        read_only=body.read_only,
        created_at=created_at,
        token=plaintext,
    )


@router.get(
    "/config/api-tokens",
    response_model=ApiTokenListResponse,
    summary="List active API tokens",
    description=(
        "Lists non-revoked api_tokens rows — label, scope, read_only, last_used_at, "
        "created_at. NEVER includes the secret or its hash. PF-AUTH-1 (1.9.4 W4)."
    ),
)
async def list_api_tokens() -> ApiTokenListResponse:
    """GET /config/api-tokens — list active (non-revoked) tokens, secret never included."""
    async with runtime_state.get_session() as session:
        result = await session.execute(
            select(ApiToken)
            .where(ApiToken.revoked_at.is_(None))
            .order_by(ApiToken.created_at.desc())
        )
        rows = result.scalars().all()

    return ApiTokenListResponse(
        tokens=[
            ApiTokenListItem(
                id=row.id,
                label=row.label,
                vault_id=row.vault_id,
                read_only=row.read_only,
                created_at=row.created_at,
                last_used_at=row.last_used_at,
            )
            for row in rows
        ]
    )


@router.delete(
    "/config/api-tokens/{token_id}",
    status_code=204,
    summary="Revoke an API token",
    description=(
        "Soft-deletes the row (revoked_at = now()) and removes it from the in-process "
        "ApiTokenCache immediately — the token stops working on the very next request. "
        "404 if the id does not exist or is already revoked. PF-AUTH-1 (1.9.4 W4)."
    ),
)
async def revoke_api_token(token_id: uuid.UUID) -> None:
    """DELETE /config/api-tokens/{id} — revoke (soft-delete) a token."""
    from datetime import UTC, datetime  # noqa: PLC0415

    async with runtime_state.get_session() as session:
        row = await session.get(ApiToken, token_id)
        if row is None or row.revoked_at is not None:
            raise HTTPException(status_code=404, detail="API token not found")
        row.revoked_at = datetime.now(UTC)

    await runtime_state.api_token_cache.revoke(str(token_id))

    logger.info("DELETE /config/api-tokens/%s: revoked (PF-AUTH-1)", token_id)
