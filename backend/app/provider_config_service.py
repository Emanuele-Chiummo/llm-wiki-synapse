"""
ConfigResolver — provider_config resolution service (ADR-0008 §2, F17, I6).

Resolution order (most specific wins):
    1. scope='operation' AND vault_id=? AND operation=?   (operation+vault)
    2. scope='vault'     AND vault_id=?                   (vault default)
    3. scope='global'                                      (global default)

A missing global row is a hard configuration error — never a silent default backend (I6).
Returns the first matching `ProviderConfig` ORM row, which the factory in
`app.ingest.provider` then converts to a `ProviderSettings` instance.

Separate function `resolve_fallback_provider_config` returns the `is_fallback=True`
row at the narrowest matching scope — used by the orchestrator's single-fallback path
(ADR-0009 §fallback, I7 bound: exactly one fallback attempt).

No API key column exists on ProviderConfig — keys are env-only (§12, ADR-0008 §3).

This module is imported by:
    - `backend/app/main.py`  (provider_config CRUD REST endpoints)
    - `backend/app/ingest/orchestrator.py`  (seam wiring for ingest/fallback resolution)
"""

from __future__ import annotations

import logging
from typing import Literal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import ProviderConfig

logger = logging.getLogger(__name__)

# Valid literal types — mirrored from models.py for validation without an import cycle.
VALID_PROVIDER_TYPES: frozenset[str] = frozenset({"local", "api", "cli"})
VALID_SCOPES: frozenset[str] = frozenset({"global", "vault", "operation"})
VALID_OPERATIONS: frozenset[str] = frozenset({"ingest", "chat", "lint"})

OperationT = Literal["ingest", "chat", "lint"]


async def resolve_provider_config(
    operation: OperationT,
    vault_id: str | None = None,
    *,
    session: AsyncSession | None = None,
) -> ProviderConfig:
    """
    Return the most-specific ProviderConfig row for (operation, vault_id).

    Resolution order per ADR-0008 §2:
        operation+vault > vault > global

    Raises ConfigNotFoundError if no row resolves (a hard config error per I6 —
    the caller must surface it; do NOT silently default a backend).

    Args:
        operation: "ingest" | "chat" | "lint"
        vault_id:  logical vault identifier (defaults to settings.vault_id when None)
        session:   optional existing AsyncSession; if None a new session is opened
    """
    effective_vault_id = vault_id or settings.vault_id

    async def _resolve(sess: AsyncSession) -> ProviderConfig | None:
        # 1. operation+vault (most specific)
        row = await _query_one(
            sess,
            and_(
                ProviderConfig.scope == "operation",
                ProviderConfig.vault_id == effective_vault_id,
                ProviderConfig.operation == operation,
                ProviderConfig.is_fallback.is_(False),
            ),
        )
        if row is not None:
            logger.debug(
                "provider_config resolved: operation+vault scope (op=%s vault=%s)",
                operation,
                effective_vault_id,
            )
            return row

        # 2. vault (vault default)
        row = await _query_one(
            sess,
            and_(
                ProviderConfig.scope == "vault",
                ProviderConfig.vault_id == effective_vault_id,
                ProviderConfig.is_fallback.is_(False),
            ),
        )
        if row is not None:
            logger.debug("provider_config resolved: vault scope (vault=%s)", effective_vault_id)
            return row

        # 3. global (catch-all default)
        row = await _query_one(
            sess,
            and_(
                ProviderConfig.scope == "global",
                ProviderConfig.is_fallback.is_(False),
            ),
        )
        if row is not None:
            logger.debug("provider_config resolved: global scope")
            return row

        return None

    if session is not None:
        result = await _resolve(session)
    else:
        async with get_session() as sess:
            result = await _resolve(sess)
            if result is not None:
                sess.expunge(result)

    if result is None:
        raise ConfigNotFoundError(
            f"No provider_config row found for operation={operation!r} "
            f"vault_id={effective_vault_id!r}. "
            "Seed a global row in provider_config (ADR-0008 §2, I6)."
        )
    return result


async def resolve_fallback_provider_config(
    vault_id: str | None = None,
    *,
    session: AsyncSession | None = None,
) -> ProviderConfig | None:
    """
    Return the fallback ProviderConfig row (is_fallback=True) at the narrowest matching
    scope, or None if no fallback is configured (ADR-0009 §fallback).

    The orchestrator's single-fallback path calls this exactly once on primary failure (I7).
    Resolution order: vault-scoped fallback first, then global fallback.

    Args:
        vault_id: logical vault identifier (defaults to settings.vault_id when None)
        session:  optional existing AsyncSession
    """
    effective_vault_id = vault_id or settings.vault_id

    async def _resolve(sess: AsyncSession) -> ProviderConfig | None:
        # Vault-scoped fallback first (more specific)
        row = await _query_one(
            sess,
            and_(
                ProviderConfig.vault_id == effective_vault_id,
                ProviderConfig.is_fallback.is_(True),
            ),
        )
        if row is not None:
            return row
        # Global fallback
        return await _query_one(
            sess,
            and_(
                ProviderConfig.scope == "global",
                ProviderConfig.is_fallback.is_(True),
            ),
        )

    if session is not None:
        return await _resolve(session)

    async with get_session() as sess:
        result = await _resolve(sess)
        if result is not None:
            sess.expunge(result)
        return result


# ── Internal helpers ───────────────────────────────────────────────────────────


async def _query_one(
    session: AsyncSession,
    where_clause: object,
) -> ProviderConfig | None:
    """Execute a SELECT with *where_clause* and return the first matching row or None."""
    stmt = select(ProviderConfig).where(where_clause).limit(1)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


# ── Exception type ─────────────────────────────────────────────────────────────


class ConfigNotFoundError(RuntimeError):
    """
    Raised when no provider_config row can be resolved for a given (operation, vault_id).

    This is a HARD configuration error — never silently default a backend (I6, ADR-0008 §2).
    The caller should surface this as an HTTP 500 / IngestError rather than choosing a backend.
    """
