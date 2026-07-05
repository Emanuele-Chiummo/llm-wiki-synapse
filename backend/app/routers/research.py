"""
Per-domain APIRouter: /research/* endpoints (F10 Deep Research).

Covers:
  POST /research/start            — start a bounded deep-research run
  GET  /research/runs             — paginated run history
  GET  /research/runs/{id}        — run detail + sources
"""

from __future__ import annotations

import asyncio
import logging
import sys as _sys
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select

from app.config import settings
from app.models import DeepResearchRun, DeepResearchSource

logger = logging.getLogger(__name__)

router = APIRouter()


class _LazyMain:
    """Lazy proxy to app.main; enables test patches via app.main.* to propagate."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(_sys.modules["app.main"], name)

    def __setattr__(self, name: str, value: object) -> None:
        setattr(_sys.modules["app.main"], name, value)


_m = _LazyMain()

# ── Deep Research REST (F10, ADR-0024 §8) ─────────────────────────────────────


class ResearchStartRequest(BaseModel):
    """
    Request body for POST /research/start (ADR-0024 §8.1, AC-F10-4).

    max_iter and token_budget are optional — env defaults apply when omitted.
    Both are FROZEN onto the deep_research_runs row before the background task starts
    (AQ-v0.5-4, I7). Server-side validators cap the range so callers cannot request an
    unbounded run (I7 / Do-NOT #1/#2).
    """

    vault_id: str = Field(..., description="Vault scope for the run")
    topic: str = Field(..., min_length=1, description="Research topic (non-empty)")
    max_iter: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Max refinement rounds (1..10); null → DEEP_RESEARCH_MAX_ITER default",
    )
    token_budget: int | None = Field(
        default=None,
        ge=1_000,
        le=1_000_000,
        description="Token budget (1_000..1_000_000); null → DEEP_RESEARCH_TOKEN_BUDGET default",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "vault_id": "default",
                "topic": "Kubernetes networking with Calico",
                "max_iter": 3,
                "token_budget": 100000,
            }
        }
    }


class ResearchStartResponse(BaseModel):
    """202 response for POST /research/start (ADR-0024 §8.1)."""

    run_id: uuid.UUID = Field(..., description="UUID of the created deep_research_runs row")

    model_config = {
        "json_schema_extra": {"example": {"run_id": "00000000-0000-0000-0000-000000000001"}}
    }


class ResearchRunSummary(BaseModel):
    """
    One item in GET /research/runs (ADR-0024 §8.2, AC-F10-4b).

    Mirrors the ingest_runs list shape: id, topic, status, cost, timing.
    """

    id: uuid.UUID
    vault_id: str
    topic: str
    status: str = Field(
        description="running | converged | max_iter_reached | budget_exhausted | error"
    )
    iterations_used: int
    sources_fetched: int
    total_cost_usd: float
    started_at: datetime
    completed_at: datetime | None = None

    @field_validator("total_cost_usd", mode="before")
    @classmethod
    def _decimal_to_float(cls, v: Any) -> float:
        return float(v) if v is not None else 0.0

    model_config = {"from_attributes": True}


class ResearchRunListResponse(BaseModel):
    """Paginated list response for GET /research/runs (ADR-0024 §8.2)."""

    items: list[ResearchRunSummary]
    total: int
    limit: int
    offset: int


class ResearchSourceSummary(BaseModel):
    """One source row in GET /research/runs/{id} (ADR-0024 §8.3, AC-F10-6b)."""

    url: str
    title: str | None
    relevance_score: float | None = None
    iteration: int

    @field_validator("relevance_score", mode="before")
    @classmethod
    def _decimal_to_float(cls, v: Any) -> float | None:
        return float(v) if v is not None else None

    model_config = {"from_attributes": True}


class ResearchRunDetail(BaseModel):
    """
    GET /research/runs/{id} response (ADR-0024 §8.3, AC-F10-4c).

    Includes the full queries_used array and per-source summaries.
    synthesis_text is null until step 5 completes (AC-F10-4c).
    sources array excludes fetched_content_md blobs by default (size guard, ADR-0024 §8.3).
    """

    id: uuid.UUID
    vault_id: str
    topic: str
    status: str
    max_iter: int
    token_budget: int
    iterations_used: int
    queries_used: list[str]
    sources_fetched: int
    total_cost_usd: float
    synthesis_text: str | None = None
    synthesis_page_id: uuid.UUID | None = None
    sources: list[ResearchSourceSummary] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None

    @field_validator("total_cost_usd", mode="before")
    @classmethod
    def _decimal_to_float(cls, v: Any) -> float:
        return float(v) if v is not None else 0.0

    model_config = {"from_attributes": True}


@router.post(
    "/research/start",
    response_model=ResearchStartResponse,
    status_code=202,
    summary="Start a bounded deep-research run",
    description=(
        "F10 Deep Research loop (ADR-0024 §8.1, AC-F10-4a). "
        "Validates topic/vault_id; bounds max_iter (1..10) and token_budget (1k..1M) so the "
        "caller cannot request an unbounded run (I7). "
        "Freezes bounds onto the deep_research_runs row before the background task starts "
        "(AQ-v0.5-4). Schedules run_deep_research as a background asyncio task (fire-and-poll). "
        "Returns 202 {run_id} immediately — poll GET /research/runs/{id} for progress. "
        "503 if SEARXNG_URL is unset (I9 — no fake run, no fallback engine)."
    ),
    responses={
        202: {"description": "Run accepted; poll GET /research/runs/{id} for progress"},
        422: {"description": "Validation error (empty topic, max_iter out of range, etc.)"},
        503: {"description": "SEARXNG_URL is not configured (I9)"},
    },
)
async def research_start(body: ResearchStartRequest) -> ResearchStartResponse:
    """
    POST /research/start — fire-and-poll deep research (ADR-0024 §8.1, I7/I9).

    1. 503 if SEARXNG_URL is unset (I9 — never a fake run, never a fallback engine).
    2. INSERT deep_research_runs row with status='running' + frozen bounds.
    3. Schedule run_deep_research(...) as asyncio background task.
    4. Return 202 {run_id} immediately.
    """
    # ── I9: SearXNG URL required before creating a run row (ADR-0024 §8.1, ADR-0041) ────
    # Resolution: DB vault_state.searxng_url_db wins over SEARXNG_URL env (ADR-0041 §2.2).
    if not _m._web_search_config_cache.configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "SEARXNG_URL is not configured. Set SEARXNG_URL env var or use "
                "PUT /web-search/config to set the SearXNG instance URL at runtime "
                "(e.g. http://searxng:8080) to enable deep research (I9, ADR-0041)."
            ),
        )

    from app.ops.deep_research import run_deep_research

    run_id = uuid.uuid4()
    # Use str(run_id) so the ORM INSERT works with both Postgres (UUID col)
    # and SQLite in-memory tests (String(36) variant via with_variant).
    # UUID(as_uuid=True) on Postgres can accept a string UUID value.
    run_id_str = str(run_id)

    # Freeze bounds (AQ-v0.5-4): resolve env defaults NOW, INSERT row, schedule task.
    frozen_max_iter = (
        body.max_iter if body.max_iter is not None else settings.deep_research_max_iter
    )
    frozen_token_budget = (
        body.token_budget if body.token_budget is not None else settings.deep_research_token_budget
    )

    # Pre-INSERT the row so the caller can poll immediately after 202
    async with _m.get_session() as session:
        run = DeepResearchRun(
            id=run_id_str,
            vault_id=body.vault_id,
            topic=body.topic,
            status="running",
            max_iter=frozen_max_iter,
            token_budget=frozen_token_budget,
            iterations_used=0,
            queries_used=[],
            sources_fetched=0,
            converged=False,
            total_cost_usd=0,
            synthesis_text=None,
            synthesis_page_id=None,
            started_at=datetime.now(UTC),
            completed_at=None,
            error_message=None,
        )
        session.add(run)

    # Schedule the bounded loop as a background task (ADR-0020 fire-and-poll pattern).
    # Pass the SAME run_id so the loop updates the row we just inserted — not a new one
    # (C1: without this the client polls a row the loop never touches → stuck "running").
    asyncio.create_task(
        run_deep_research(
            vault_id=body.vault_id,
            topic=body.topic,
            max_iter=frozen_max_iter,
            token_budget=frozen_token_budget,
            run_id=run_id,
        )
    )

    logger.info(
        "research_start: run_id=%s vault=%s topic=%r max_iter=%d budget=%d",
        run_id,
        body.vault_id,
        body.topic,
        frozen_max_iter,
        frozen_token_budget,
    )
    return ResearchStartResponse(run_id=run_id)


@router.get(
    "/research/runs",
    response_model=ResearchRunListResponse,
    summary="List deep-research run history",
    description=(
        "Paginated, started_at DESC list of deep_research_runs rows (ADR-0024 §8.2, AC-F10-4b). "
        "limit: 1..100 default 20; offset: >=0 default 0; vault_id: optional filter. "
        "Mirrors GET /ingest/runs contract."
    ),
    responses={
        200: {"description": "Paginated run list"},
        422: {"description": "Validation error (limit/offset out of range)"},
    },
)
async def list_research_runs(
    limit: int = Query(default=20, ge=1, le=100, description="Max rows (1..100)"),
    offset: int = Query(default=0, ge=0, description="Row offset (>=0)"),
    vault_id: str | None = Query(default=None, description="Optional vault_id filter"),
) -> ResearchRunListResponse:
    """GET /research/runs — paginated deep-research run list (ADR-0024 §8.2)."""
    async with _m.get_session() as session:
        count_stmt = select(func.count()).select_from(DeepResearchRun)
        if vault_id is not None:
            count_stmt = count_stmt.where(DeepResearchRun.vault_id == vault_id)
        total: int = (await session.execute(count_stmt)).scalar_one()

        data_stmt = select(DeepResearchRun)
        if vault_id is not None:
            data_stmt = data_stmt.where(DeepResearchRun.vault_id == vault_id)
        data_stmt = (
            data_stmt.order_by(DeepResearchRun.started_at.desc()).offset(offset).limit(limit)
        )
        runs = list((await session.execute(data_stmt)).scalars().all())

    items = [
        ResearchRunSummary(
            id=r.id,
            vault_id=r.vault_id,
            topic=r.topic,
            status=r.status,
            iterations_used=r.iterations_used,
            sources_fetched=r.sources_fetched,
            total_cost_usd=float(r.total_cost_usd),
            started_at=r.started_at,
            completed_at=r.completed_at,
        )
        for r in runs
    ]
    return ResearchRunListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/research/runs/{run_id}",
    response_model=ResearchRunDetail,
    summary="Get deep-research run detail + sources",
    description=(
        "Returns full run detail including queries_used, synthesis_text, and per-source summaries "
        "(ADR-0024 §8.3, AC-F10-4c). synthesis_text is null until step 5 completes. "
        "sources array excludes fetched_content_md blobs (size guard). 404 if unknown run_id."
    ),
    responses={
        200: {"description": "Run detail with sources"},
        404: {"description": "No run with this id"},
    },
)
async def get_research_run(run_id: uuid.UUID) -> ResearchRunDetail:
    """GET /research/runs/{id} — deep-research run detail (ADR-0024 §8.3)."""
    # Use str(run_id) so the query works with both Postgres (UUID col) and SQLite (String col).
    # UUID(as_uuid=True).with_variant(String(36), "sqlite") handles the conversion when given
    # a str, but aiosqlite cannot bind a native uuid.UUID Python object.
    run_id_str = str(run_id)

    async with _m.get_session() as session:
        # Load the run row
        run_result = await session.execute(
            select(DeepResearchRun).where(DeepResearchRun.id == run_id_str)
        )
        run = run_result.scalar_one_or_none()

        if run is None:
            raise HTTPException(status_code=404, detail=f"Deep research run {run_id} not found")

        # Load sources in a separate query (avoids lazy-load raise on relationship)
        sources_result = await session.execute(
            select(DeepResearchSource).where(DeepResearchSource.run_id == run_id_str)
        )
        source_rows = list(sources_result.scalars().all())

    sources = [
        ResearchSourceSummary(
            url=s.url,
            title=s.title,
            relevance_score=float(s.relevance_score) if s.relevance_score is not None else None,
            iteration=s.iteration,
        )
        for s in source_rows
    ]

    return ResearchRunDetail(
        id=run.id,
        vault_id=run.vault_id,
        topic=run.topic,
        status=run.status,
        max_iter=run.max_iter,
        token_budget=run.token_budget,
        iterations_used=run.iterations_used,
        queries_used=run.queries_used or [],
        sources_fetched=run.sources_fetched,
        total_cost_usd=float(run.total_cost_usd),
        synthesis_text=run.synthesis_text,
        synthesis_page_id=run.synthesis_page_id,
        sources=sources,
        started_at=run.started_at,
        completed_at=run.completed_at,
        error_message=run.error_message,
    )
