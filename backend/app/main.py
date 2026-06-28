"""
Synapse FastAPI service — v0.2 (M2).

Endpoints:
  GET  /status                — vault_id, data_version, started_at, uptime
  GET  /pages                 — paginated list of live pages
  GET  /pages/{id}            — single page by UUID
  POST /ingest/trigger        — sync ingest; HTTP 202 (typed IngestTriggerResponse, AC-D4u)
  GET  /provider/config       — list effective + raw provider_config rows (F17)
  POST /provider/config       — create/update a provider_config row (F17, §12 — no api key)
  DELETE /provider/config/{id} — delete a provider_config row by UUID

Startup sequence (ordered, per v0.1-architecture §2.5):
  1. Vault skeleton bootstrap (vault.py) — AC-K7-1, I5
  2. Seed vault_state (idempotent) — ADR-0005, AC-F16dv-1
  3. Validate EMBEDDING_DIM vs live bge-m3 + ensure synapse_pages collection — ADR-0004
  4. Start watchdog observer — watcher.py
  5. Emit AQ-3 INFO line if raw/sources/ is non-empty — ADR-0006

OpenAPI: auto-served at /openapi.json; `make openapi` snapshots to docs/api/openapi.json (D4).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.config import settings
from app.db import dispose_engine, get_session
from app.embeddings import EmbeddingError, get_embedding_client
from app.ingest.orchestrator import IngestResult, ingest_file
from app.models import Page, ProviderConfig, VaultState
from app.qdrant_client import ensure_collection
from app.vault import bootstrap_vault
from app.watcher import start_watcher, stop_watcher

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Startup timestamp ──────────────────────────────────────────────────────────
_started_at: datetime = datetime.now(UTC)


# ── Lifespan ───────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """
    FastAPI lifespan: startup → yield → shutdown.

    Ordered startup sequence per v0.1-architecture §2.5.
    """
    global _started_at
    _started_at = datetime.now(UTC)

    # 1. Vault skeleton (K1, I5, AC-K7-1)
    bootstrap_vault()

    # 2. Seed vault_state (ADR-0005, AC-F16dv-1)
    await _seed_vault_state()

    # 3. Validate EMBEDDING_DIM vs live bge-m3 + ensure collection (ADR-0004)
    await _validate_embedding_and_collection()

    # 4. Start watcher (I1)
    loop = asyncio.get_running_loop()
    start_watcher(loop)

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    stop_watcher()
    await dispose_engine()


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Synapse",
    version="0.1.0",
    description=(
        "Self-organising wiki backend — walking skeleton (M1). "
        "Karpathy LLM Wiki pattern with pluggable inference (F17)."
    ),
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


# ── Pydantic response models ───────────────────────────────────────────────────


class StatusResponse(BaseModel):
    vault_id: str
    data_version: int
    started_at: datetime
    uptime_seconds: float

    model_config = {
        "json_schema_extra": {
            "example": {
                "vault_id": "default",
                "data_version": 3,
                "started_at": "2026-06-28T10:00:00Z",
                "uptime_seconds": 42.7,
            }
        }
    }


class PageResponse(BaseModel):
    id: uuid.UUID
    vault_id: str
    file_path: str
    title: str | None
    page_type: str | None = Field(None, serialization_alias="type")
    sources: list[str] | None
    content_hash: str
    qdrant_point_id: uuid.UUID | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"populate_by_name": True, "from_attributes": True}


class PageListResponse(BaseModel):
    items: list[PageResponse]
    total: int
    limit: int
    offset: int


class IngestTriggerRequest(BaseModel):
    file_path: str = Field(..., description="Relative path under vault/raw/sources/")


class IngestTriggerResponse(BaseModel):
    """
    Typed 202 body for POST /ingest/trigger (AC-D4u — task_id appears in OpenAPI schema).

    task_id is None in v0.2 (synchronous path); v0.3 fills it with a real async task UUID.
    status: "completed" | "skipped" (I1 fast-path) | "queued"/"running" (async, v0.3+).
    """

    task_id: uuid.UUID | None = Field(
        default=None,
        description="Async task UUID (None in v0.2 synchronous mode; filled in v0.3+)",
    )
    status: str = Field(
        ...,
        description='"completed" or "skipped" (I1 mtime/hash fast-path)',
    )
    page_id: uuid.UUID = Field(..., description="UUID of the ingested page row")

    model_config = {
        "json_schema_extra": {
            "example": {
                "task_id": None,
                "status": "completed",
                "page_id": "00000000-0000-0000-0000-000000000001",
            }
        }
    }


# ── Provider config Pydantic models (F17 CRUD — §12: NO api_key field) ────────

_VALID_PROVIDER_TYPES = {"local", "api", "cli"}
_VALID_SCOPES = {"global", "vault", "operation"}
_VALID_OPERATIONS = {"ingest", "chat", "lint"}


class ProviderConfigCreate(BaseModel):
    """
    Request body for POST /provider/config (F17).

    Stores NO API key (§12 / ADR-0008 §3). Keys are env-only inside provider/.
    model_id must be provided explicitly — no hardcoded defaults in app code (AC-F17-8).
    """

    scope: str = Field(..., description="global | vault | operation")
    operation: str | None = Field(
        default=None,
        description="ingest | chat | lint; required when scope='operation'",
    )
    vault_id: str | None = Field(
        default=None,
        description="Required when scope='vault' or 'operation'",
    )
    provider_type: str = Field(..., description="local | api | cli")
    model_id: str = Field(
        ...,
        description="Model name (e.g. claude-sonnet-4-6); lives only in DB rows (AC-F17-8)",
    )
    base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible endpoint; NULL for Anthropic/local default",
    )
    max_iter: int = Field(default=3, ge=1, le=20, description="Orchestrated-loop cap (I7)")
    token_budget: int = Field(
        default=60000,
        ge=1000,
        le=1_000_000,
        description="Loop token budget (I7)",
    )
    is_fallback: bool = Field(default=False, description="Marks the single fallback row")

    @field_validator("provider_type")
    @classmethod
    def _valid_provider_type(cls, v: str) -> str:
        if v not in _VALID_PROVIDER_TYPES:
            raise ValueError(
                f"provider_type must be one of {sorted(_VALID_PROVIDER_TYPES)}, got {v!r}"
            )
        return v

    @field_validator("scope")
    @classmethod
    def _valid_scope(cls, v: str) -> str:
        if v not in _VALID_SCOPES:
            raise ValueError(f"scope must be one of {sorted(_VALID_SCOPES)}, got {v!r}")
        return v

    @field_validator("operation")
    @classmethod
    def _valid_operation(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_OPERATIONS:
            raise ValueError(
                f"operation must be one of {sorted(_VALID_OPERATIONS)} or null, got {v!r}"
            )
        return v


class ProviderConfigResponse(BaseModel):
    """API response shape for a provider_config row (§12: no api_key field)."""

    id: uuid.UUID
    scope: str
    operation: str | None
    vault_id: str | None
    provider_type: str
    model_id: str
    base_url: str | None
    max_iter: int
    token_budget: int
    is_fallback: bool
    created_at: Any
    updated_at: Any

    model_config = {"from_attributes": True}


class ProviderConfigListResponse(BaseModel):
    items: list[ProviderConfigResponse]
    total: int


# ── GET /status ────────────────────────────────────────────────────────────────


@app.get(
    "/status",
    response_model=StatusResponse,
    summary="Service health + data_version",
    description=(
        "Returns vault_id, current data_version (monotonic ingest counter), "
        "service started_at, and uptime_seconds. (AC-REST-1, AC-F16dv-3)"
    ),
)
async def get_status() -> StatusResponse:
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        data_version = state.data_version if state is not None else 0

    now = datetime.now(UTC)
    uptime = (now - _started_at).total_seconds()
    return StatusResponse(
        vault_id=settings.vault_id,
        data_version=data_version,
        started_at=_started_at,
        uptime_seconds=uptime,
    )


# ── GET /pages ─────────────────────────────────────────────────────────────────


@app.get(
    "/pages",
    response_model=PageListResponse,
    summary="List live pages",
    description=(
        "Paginated list of pages where deleted_at IS NULL. " "Supports limit/offset. (AC-REST-2)"
    ),
)
async def list_pages(
    limit: int = Query(default=50, ge=1, le=500, description="Max rows to return"),
    offset: int = Query(default=0, ge=0, description="Row offset for pagination"),
) -> PageListResponse:
    async with get_session() as session:
        total_row = await session.execute(
            select(func.count())
            .select_from(Page)
            .where(
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
        )
        total: int = total_row.scalar_one()

        rows = await session.execute(
            select(Page)
            .where(
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
            .order_by(Page.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        pages = rows.scalars().all()

    return PageListResponse(
        items=[_page_to_response(p) for p in pages],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── GET /pages/{id} ────────────────────────────────────────────────────────────


@app.get(
    "/pages/{page_id}",
    response_model=PageResponse,
    summary="Get a single page by UUID",
    description=(
        "Returns full page metadata; 404 if unknown or deleted; 422 on invalid UUID. "
        "(AC-REST-3, AC-REST-6)"
    ),
)
async def get_page(page_id: uuid.UUID) -> PageResponse:
    async with get_session() as session:
        row = await session.execute(
            select(Page).where(
                Page.id == page_id,
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
        )
        page = row.scalar_one_or_none()

    if page is None:
        raise HTTPException(status_code=404, detail=f"Page {page_id} not found")

    return _page_to_response(page)


# ── POST /ingest/trigger ───────────────────────────────────────────────────────


@app.post(
    "/ingest/trigger",
    response_model=IngestTriggerResponse,
    status_code=202,
    summary="Manually trigger ingest of a single file",
    description=(
        "Synchronously ingests the file at file_path through the seam. "
        "Returns HTTP 202 with typed {task_id, status, page_id} (ADR-0006, AC-REST-4, AC-D4u). "
        "status is 'completed' or 'skipped' (I1 fast-path)."
    ),
    responses={
        202: {"description": "Ingest accepted and completed"},
        422: {"description": "Validation error (missing file_path, bad format, or file not found)"},
    },
)
async def trigger_ingest(body: IngestTriggerRequest) -> IngestTriggerResponse:
    """
    Trigger incremental ingest of a single file (K2 partial, ADR-0006, AC-D4u).

    Resolves the file path under vault_root if relative.
    Runs ingest_file through the seam (ADR-0003); never touches DB/Qdrant directly.
    Returns 202 per ADR-0006 contract with a typed schema so task_id appears in OpenAPI (AC-D4u).
    """
    from pathlib import Path

    # Resolve relative or absolute path
    path = Path(body.file_path)
    if not path.is_absolute():
        path = settings.vault_root / path

    if not path.exists():
        raise HTTPException(
            status_code=422,
            detail=f"File not found: {body.file_path}",
        )

    result: IngestResult = await ingest_file(path)

    return IngestTriggerResponse(
        task_id=None,
        status=result.status,
        page_id=result.page_id,
    )


# ── GET /provider/config ───────────────────────────────────────────────────────


@app.get(
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
    async with get_session() as session:
        stmt = select(ProviderConfig)
        if scope is not None:
            stmt = stmt.where(ProviderConfig.scope == scope)
        if vault_id is not None:
            stmt = stmt.where(ProviderConfig.vault_id == vault_id)
        stmt = stmt.order_by(ProviderConfig.created_at.asc())
        rows = await session.execute(stmt)
        configs = list(rows.scalars().all())
        total = len(configs)
        items = [ProviderConfigResponse.model_validate(c) for c in configs]

    return ProviderConfigListResponse(items=items, total=total)


# ── POST /provider/config ──────────────────────────────────────────────────────


@app.post(
    "/provider/config",
    response_model=ProviderConfigResponse,
    status_code=201,
    summary="Create or update a provider_config row",
    description=(
        "Create a new provider_config row. "
        "provider_type must be one of: local | api | cli. "
        "NO api_key field is accepted or stored — keys are env-only (§12). (F17, ADR-0008)"
    ),
    responses={
        201: {"description": "Row created"},
        422: {"description": "Validation error (invalid provider_type, scope, or operation)"},
    },
)
async def create_provider_config(body: ProviderConfigCreate) -> ProviderConfigResponse:
    """
    Create a new provider_config row for F17 provider selection (ADR-0008).

    Scope validation: if scope='operation', operation must be non-null.
    No API key field: keys live in environment only (§12, ADR-0008 §3).
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

    async with get_session() as session:
        row = ProviderConfig(
            id=uuid.uuid4(),
            scope=body.scope,
            operation=body.operation,
            vault_id=body.vault_id,
            provider_type=body.provider_type,
            model_id=body.model_id,
            base_url=body.base_url,
            max_iter=body.max_iter,
            token_budget=body.token_budget,
            is_fallback=body.is_fallback,
        )
        session.add(row)
        await session.flush()
        response = ProviderConfigResponse.model_validate(row)

    return response


# ── DELETE /provider/config/{id} ───────────────────────────────────────────────


@app.delete(
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

    async with get_session() as session:
        result = await session.execute(
            sa_delete(ProviderConfig).where(ProviderConfig.id == config_id)
        )
        deleted = result.rowcount  # type: ignore[union-attr]

    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail=f"provider_config {config_id} not found",
        )


# ── Startup helpers ────────────────────────────────────────────────────────────


async def _seed_vault_state() -> None:
    """
    Insert vault_state row for VAULT_ID with data_version=0 if absent (ADR-0005, AQ-4).

    Idempotent — safe to call on every restart.
    """
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        if row.scalar_one_or_none() is None:
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                updated_at=datetime.now(UTC),
            )
            session.add(state)
            logger.info("vault_state seeded for vault_id=%r", settings.vault_id)
        else:
            logger.info("vault_state already exists for vault_id=%r — no change", settings.vault_id)


async def _validate_embedding_and_collection() -> None:
    """
    Validate EMBEDDING_DIM against the live bge-m3 service and ensure the
    synapse_pages Qdrant collection (ADR-0004, AC-QD-1).

    Fails fast on mismatch (ADR-0004 — the running service is the authority).
    Allows a FakeEmbeddingClient to be injected in CI without TrueNAS (GAP-4).
    """
    client = get_embedding_client()
    try:
        live_dim = await client.probe_dimension()
    except EmbeddingError as exc:
        logger.error("Cannot reach embedding service: %s", exc)
        raise RuntimeError(
            f"Embedding service at {settings.embedding_url} is unreachable at startup. "
            "Set EMBEDDING_URL to a reachable endpoint or inject a FakeEmbeddingClient "
            "for CI (GAP-4)."
        ) from exc

    if live_dim != settings.embedding_dim:
        raise RuntimeError(
            f"EMBEDDING_DIM={settings.embedding_dim} but the live bge-m3 service "
            f"returned vectors of length {live_dim}. Update EMBEDDING_DIM to match "
            "the running service (ADR-0004)."
        )

    logger.info("Embedding dimension validated: %d", live_dim)
    await ensure_collection(dim=live_dim)


# ── Model serialisation helper ─────────────────────────────────────────────────


def _page_to_response(page: Page) -> PageResponse:
    return PageResponse(
        id=page.id,
        vault_id=page.vault_id,
        file_path=page.file_path,
        title=page.title,
        page_type=page.page_type,
        sources=page.sources,
        content_hash=page.content_hash,
        qdrant_point_id=page.qdrant_point_id,
        deleted_at=page.deleted_at,
        created_at=page.created_at,
        updated_at=page.updated_at,
    )
