"""Per-domain config router: /provider/config CRUD (F17).

Split out of the monolithic app.routers.config (BE-REFAC-1). Same paths/contract.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.engine import CursorResult

from app import runtime_state, secrets_crypto
from app.base_url_validator import validate_base_url
from app.models import ProviderConfig
from app.provider_config_service import bump_config_version
from app.schemas.config import (
    ProviderConfigCreate,
    ProviderConfigListResponse,
    ProviderConfigResponse,
    ProviderConfigUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _provider_config_to_response(row: Any) -> ProviderConfigResponse:
    """
    Build the safe API response for a provider_config row — NEVER leaks the plaintext key.

    api_key_configured is derived from presence of ciphertext; api_key_masked is a best-effort
    non-reversible hint (decrypt → last 4 chars) that degrades to None when the master key is
    absent or the ciphertext is invalid.
    """
    encrypted = getattr(row, "api_key_encrypted", None)
    return ProviderConfigResponse(
        id=row.id,
        scope=row.scope,
        operation=row.operation,
        vault_id=row.vault_id,
        provider_type=row.provider_type,
        model_id=row.model_id,
        base_url=row.base_url,
        api_key_configured=bool(encrypted),
        api_key_masked=secrets_crypto.mask_from_encrypted(encrypted),
        reasoning_effort=getattr(row, "reasoning_effort", None),
        max_iter=row.max_iter,
        token_budget=row.token_budget,
        is_fallback=row.is_fallback,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ── GET /provider/config ───────────────────────────────────────────────────────


@router.get(
    "/provider/config",
    response_model=ProviderConfigListResponse,
    summary="List provider_config rows",
    description=(
        "Returns all raw provider_config rows. "
        "No API key field is stored or returned (§12). (F17, AC-F17-6)"
    ),
)
async def list_provider_configs(
    scope: str | None = Query(default=None, description="Filter by scope (global|vault|operation)"),
    vault_id: str | None = Query(default=None, description="Filter by vault_id"),
) -> ProviderConfigListResponse:
    async with runtime_state.get_session() as session:
        stmt = select(ProviderConfig)
        if scope is not None:
            stmt = stmt.where(ProviderConfig.scope == scope)
        if vault_id is not None:
            stmt = stmt.where(ProviderConfig.vault_id == vault_id)
        stmt = stmt.order_by(ProviderConfig.created_at.asc())
        rows = await session.execute(stmt)
        configs = list(rows.scalars().all())
        total = len(configs)
        items = [_provider_config_to_response(c) for c in configs]

    return ProviderConfigListResponse(items=items, total=total)


# ── POST /provider/config ──────────────────────────────────────────────────────


def _encrypt_api_key_or_400(api_key: str) -> bytes:
    """
    Encrypt a UI-supplied API key, or raise HTTP 400 when key storage is not configured.

    Refuses (never crashes) when SYNAPSE_SECRET_KEY is unset/invalid — the operator must either
    configure the master key or fall back to env-var provider keys (§12 amendment, I6).
    """
    try:
        return secrets_crypto.encrypt(api_key)
    except secrets_crypto.SecretsNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/provider/config",
    response_model=ProviderConfigResponse,
    status_code=201,
    summary="Create a provider_config row",
    description=(
        "Create a new provider_config row. provider_type must be one of: local | api | cli. "
        "api_key is WRITE-ONLY (W1): encrypted at rest and NEVER returned — the response exposes "
        "api_key_configured + api_key_masked only. Supplying api_key requires SYNAPSE_SECRET_KEY "
        "server-side (else HTTP 400). Omit api_key to keep env-var keys. (F17, ADR-0008, W1)"
    ),
    responses={
        201: {"description": "Row created"},
        400: {"description": "api_key supplied but SYNAPSE_SECRET_KEY not configured"},
        422: {"description": "Validation error (invalid provider_type, scope, or operation)"},
    },
)
async def create_provider_config(body: ProviderConfigCreate) -> ProviderConfigResponse:
    """
    Create a new provider_config row for F17 provider selection (ADR-0008, W1).

    Scope validation: if scope='operation', operation must be non-null.
    api_key (if provided) is encrypted at rest (Fernet); the plaintext is never stored or
    returned (§12 amendment). When SYNAPSE_SECRET_KEY is unset, supplying api_key → HTTP 400.
    """
    if body.scope == "operation" and body.operation is None:
        raise HTTPException(
            status_code=422,
            detail="operation must be provided when scope='operation'",
        )
    if body.scope in {"vault", "operation"} and not body.vault_id:
        raise HTTPException(
            status_code=422,
            detail=f"vault_id must be provided when scope={body.scope!r}",
        )

    # SEC-BASEURL-1: validate base_url allowlist
    try:
        validate_base_url(body.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # W1: encrypt the UI key up front so we fail with 400 BEFORE opening a session/writing a row.
    api_key_encrypted: bytes | None = None
    if body.api_key:
        api_key_encrypted = _encrypt_api_key_or_400(body.api_key)

    async with runtime_state.get_session() as session:
        # UPSERT by logical identity so repeated "activate" clicks don't pile up duplicate rows.
        # The frontend has no upsert endpoint: setActive() (header dropdown) and addProvider()
        # (vendor catalog) BOTH POST here, and "active = newest row" — so, pre-v1.5.2, selecting a
        # provider created a brand-new row every time and duplicates accumulated. Now: match an
        # existing non-fallback row with the same (scope, vault_id, operation, provider_type,
        # model_id, base_url); if one exists, update its mutable fields and bump created_at so it
        # becomes the newest → active row (no duplicate). limit(1) tolerates pre-existing dupes.
        # Use the directly-imported model class (not the _LazyMain proxy) for the query so the
        # statement builds against the real mapped entity.
        existing = await session.execute(
            select(ProviderConfig)
            .where(
                ProviderConfig.scope == body.scope,
                ProviderConfig.vault_id == body.vault_id,
                ProviderConfig.operation == body.operation,
                ProviderConfig.provider_type == body.provider_type,
                ProviderConfig.model_id == body.model_id,
                ProviderConfig.base_url == body.base_url,
                ProviderConfig.is_fallback.is_(False),
            )
            .order_by(ProviderConfig.created_at.desc())
            .limit(1)
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            # Re-activate the existing row and refresh its mutable fields (no new row).
            if body.api_key:
                row.api_key_encrypted = api_key_encrypted
            row.reasoning_effort = body.reasoning_effort
            row.max_iter = body.max_iter
            row.token_budget = body.token_budget
            row.created_at = func.now()  # bump → newest → active (matches resolution order)
        else:
            row = runtime_state.provider_config_model()(
                id=uuid.uuid4(),
                scope=body.scope,
                operation=body.operation,
                vault_id=body.vault_id,
                provider_type=body.provider_type,
                model_id=body.model_id,
                base_url=body.base_url,
                api_key_encrypted=api_key_encrypted,
                reasoning_effort=body.reasoning_effort,
                max_iter=body.max_iter,
                token_budget=body.token_budget,
                is_fallback=body.is_fallback,
            )
            session.add(row)
        await session.flush()
        # created_at (and, on the update path, updated_at) are server-side; refresh before the
        # sync serializer reads them, else an async lazy-load raises MissingGreenlet (v1.5.2).
        await session.refresh(row)
        response = _provider_config_to_response(row)

    # BE-PERF-10: invalidate GET /status's memoized supports_vision (and any other
    # provider_config-derived memo) now that a row was created/re-activated.
    bump_config_version()
    return response


# NOTE: create uses INSERT ... RETURNING (asyncpg), so server-default created_at/updated_at
# are populated after flush. UPDATE has no RETURNING for onupdate columns, so updated_at is
# expired after flush and must be refreshed (async-safe) before the sync serializer reads it —
# otherwise the read triggers a lazy-load in a non-greenlet context → MissingGreenlet (v1.5.2).


# ── PUT /provider/config/{id} ──────────────────────────────────────────────────


@router.put(
    "/provider/config/{config_id}",
    response_model=ProviderConfigResponse,
    summary="Update a provider_config row",
    description=(
        "Partial update of a provider_config row (W1). Omitted fields are left unchanged. "
        "api_key is WRITE-ONLY: a non-empty value replaces the stored key (encrypted); an empty "
        'string "" CLEARS it (env fallback); omitting it leaves the key untouched. Supplying a '
        "non-empty api_key requires SYNAPSE_SECRET_KEY server-side (else HTTP 400). The plaintext "
        "is NEVER returned. (F17, W1)"
    ),
    responses={
        200: {"description": "Row updated"},
        400: {"description": "api_key supplied but SYNAPSE_SECRET_KEY not configured"},
        404: {"description": "Row not found"},
        422: {"description": "Validation error"},
    },
)
async def update_provider_config(
    config_id: uuid.UUID, body: ProviderConfigUpdate
) -> ProviderConfigResponse:
    """
    Update a provider_config row (W1). api_key handling: absent ⇒ unchanged; ""(empty) ⇒ clear;
    non-empty ⇒ re-encrypt & replace. Never returns the plaintext.
    """
    fields = body.model_fields_set

    # W1: encrypt a new non-empty key before touching the DB (fail 400 early when unconfigured).
    new_encrypted: bytes | None = None
    if "api_key" in fields and body.api_key:
        new_encrypted = _encrypt_api_key_or_400(body.api_key)

    async with runtime_state.get_session() as session:
        result = await session.execute(select(ProviderConfig).where(ProviderConfig.id == config_id))
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"provider_config {config_id} not found")

        if "provider_type" in fields and body.provider_type is not None:
            row.provider_type = body.provider_type
        if "model_id" in fields and body.model_id is not None:
            row.model_id = body.model_id
        if "base_url" in fields:
            # SEC-BASEURL-1: validate base_url allowlist
            try:
                validate_base_url(body.base_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            row.base_url = body.base_url
        if "reasoning_effort" in fields:
            row.reasoning_effort = body.reasoning_effort
        if "max_iter" in fields and body.max_iter is not None:
            row.max_iter = body.max_iter
        if "token_budget" in fields and body.token_budget is not None:
            row.token_budget = body.token_budget
        if "is_fallback" in fields and body.is_fallback is not None:
            row.is_fallback = body.is_fallback

        # W1 api_key: non-empty ⇒ replace; empty string ⇒ clear; absent ⇒ leave as-is.
        if "api_key" in fields:
            row.api_key_encrypted = new_encrypted  # new ciphertext or None (clear)

        await session.flush()
        # updated_at is server-side (onupdate=now()); after an UPDATE flush it is expired and
        # would be lazily reloaded when the sync serializer reads it — which raises MissingGreenlet
        # in the async engine. Refresh explicitly (async-safe) so all columns are populated first.
        await session.refresh(row)
        response = _provider_config_to_response(row)

    # BE-PERF-10: invalidate GET /status's memoized supports_vision (and any other
    # provider_config-derived memo) now that this row was updated.
    bump_config_version()
    return response


# ── DELETE /provider/config/{id} ───────────────────────────────────────────────


@router.delete(
    "/provider/config/{config_id}",
    status_code=204,
    summary="Delete a provider_config row by UUID",
    description="Hard-delete the provider_config row with the given id. (F17)",
    responses={
        204: {"description": "Row deleted"},
        404: {"description": "Row not found"},
    },
)
async def delete_provider_config(config_id: uuid.UUID) -> None:
    """Delete a provider_config row (F17). 404 if not found."""
    from sqlalchemy import delete as sa_delete

    async with runtime_state.get_session() as session:
        result = await session.execute(
            sa_delete(ProviderConfig).where(ProviderConfig.id == config_id)
        )
        deleted = cast("CursorResult[Any]", result).rowcount

    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail=f"provider_config {config_id} not found",
        )

    # BE-PERF-10: invalidate GET /status's memoized supports_vision (and any other
    # provider_config-derived memo) now that this row was deleted.
    bump_config_version()
