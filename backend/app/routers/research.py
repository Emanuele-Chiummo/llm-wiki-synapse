"""
Per-domain APIRouter: /research/* endpoints (F10 Deep Research).

Covers:
  POST   /research/start            — start a bounded deep-research run
  GET    /research/runs             — paginated run history
  GET    /research/runs/{id}        — run detail + sources
  DELETE /research/runs/{id}        — delete one run from history (v1.5.4)
"""

from __future__ import annotations

import asyncio
import logging
import sys as _sys
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select

from app.config import settings
from app.models import DeepResearchRun, DeepResearchSource
from app.rate_limit import rate_limit

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
    queries: list[str] | None = Field(
        default=None,
        max_length=10,
        description=(
            "Optional caller-supplied SearXNG queries (B5/D3). When present (non-empty) the FIRST "
            "iteration uses these VERBATIM instead of the provider query-gen round-trip — the "
            "same seed_queries seam a review item uses (bounded to DEEP_RESEARCH_MAX_QUERIES). "
            "This is how the 'optimize + confirm' dialog passes the user-edited queries. "
            "null/[] → generate queries from scratch (default, unchanged behavior)."
        ),
    )

    @field_validator("queries")
    @classmethod
    def _strip_queries(cls, v: list[str] | None) -> list[str] | None:
        """Drop empty/whitespace-only queries; collapse to None when nothing remains (I7)."""
        if v is None:
            return None
        cleaned = [q.strip() for q in v if q and q.strip()]
        return cleaned or None

    model_config = {
        "json_schema_extra": {
            "example": {
                "vault_id": "default",
                "topic": "Kubernetes networking with Calico",
                "max_iter": 3,
                "token_budget": 100000,
                "queries": ["Calico CNI BGP mode", "Kubernetes NetworkPolicy Calico"],
            }
        }
    }


class ResearchStartResponse(BaseModel):
    """202 response for POST /research/start (ADR-0024 §8.1)."""

    run_id: uuid.UUID = Field(..., description="UUID of the created deep_research_runs row")

    model_config = {
        "json_schema_extra": {"example": {"run_id": "00000000-0000-0000-0000-000000000001"}}
    }


class ResearchOptimizeTopicRequest(BaseModel):
    """
    Request body for POST /research/optimize-topic (B5/D3).

    A seed topic (typically taken from a Graph Insight) to be rephrased into a domain-specific
    research topic + web-search-optimized queries before the user confirms a Deep Research run.
    """

    topic: str = Field(..., min_length=1, description="Seed research topic (non-empty)")
    vault_id: str | None = Field(
        default=None,
        description=(
            "Vault scope used to resolve the provider AND to load overview.md/purpose.md context. "
            "null → the server's default vault_id (settings.vault_id)."
        ),
    )

    model_config = {
        "json_schema_extra": {"example": {"topic": "container networking", "vault_id": "default"}}
    }


class ResearchOptimizeTopicResponse(BaseModel):
    """
    200 response for POST /research/optimize-topic (B5/D3).

    optimized_topic prefills the editable topic field; queries prefill the editable query list.
    On the no-provider / degraded path optimized_topic echoes the seed topic and queries is
    [topic] — the dialog still opens (never a 500).
    """

    optimized_topic: str = Field(..., description="Domain-specific rephrasing of the seed topic")
    queries: list[str] = Field(
        default_factory=list,
        description="3..5 web-search-optimized SearXNG queries (or [topic] on the fallback path)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "optimized_topic": "Kubernetes container networking (CNI) — Calico vs Cilium",
                "queries": [
                    "Kubernetes CNI comparison Calico Cilium",
                    "Calico BGP networking Kubernetes",
                    "Cilium eBPF networking Kubernetes",
                ],
            }
        }
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


class ResearchDeleteResponse(BaseModel):
    """200 response for DELETE /research/runs/{run_id} (v1.5.4)."""

    id: uuid.UUID = Field(..., description="Deleted run id")
    raw_source_deleted: bool = Field(
        description=(
            "True if the run's raw/sources/deep-research-<id>.md file was also removed. "
            "Only happens when no wiki page was ever created from it (synthesis_page_id was "
            "NULL) — pure orphan cleanup. A run whose synthesis WAS ingested into a page keeps "
            "its raw file in place (that page still documents it as its source, I1/I5)."
        )
    )


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
        "503 if SEARXNG_URL is unset (I9 — no fake run, no fallback engine). "
        "429 if per-IP rate limit exceeded (R13-9)."
    ),
    responses={
        202: {"description": "Run accepted; poll GET /research/runs/{id} for progress"},
        422: {"description": "Validation error (empty topic, max_iter out of range, etc.)"},
        429: {"description": "Per-IP rate limit exceeded (R13-9)"},
        503: {"description": "SEARXNG_URL is not configured (I9)"},
    },
    dependencies=[Depends(rate_limit)],
)
async def research_start(body: ResearchStartRequest) -> ResearchStartResponse:
    """
    POST /research/start — fire-and-poll deep research (ADR-0024 §8.1, I7/I9).

    1. 503 if SEARXNG_URL is unset (I9 — never a fake run, never a fallback engine).
    2. INSERT deep_research_runs row with status='running' + frozen bounds.
    3. Schedule run_deep_research(...) as asyncio background task.
    4. Return 202 {run_id} immediately.
    """
    # ── I9: the SELECTED web-search provider must be configured before creating a run row ────
    # (ADR-0024 §8.1, ADR-0041, ADR-0070). SearXNG is the default (DB searxng_url_db wins over
    # SEARXNG_URL env — ADR-0041 §2.2); the opt-in cloud/local backends require their own key/URL.
    from app.ops.web_search import get_web_search_provider

    _provider = get_web_search_provider()
    if not _provider.configured():
        raise HTTPException(
            status_code=503,
            detail=(
                f"The selected web-search provider {_provider.name!r} is not configured. "
                "For 'searxng' set SEARXNG_URL (or PUT /web-search/config); for the opt-in "
                "cloud backends set the matching env key (TAVILY_API_KEY / SERPAPI_API_KEY / "
                "FIRECRAWL_API_KEY / BRAVE_API_KEY); for 'ollama_web' set OLLAMA_URL. Or switch "
                "the backend via PUT /config/app/web_search_provider (I9, ADR-0070)."
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
            # B5/D3: caller-edited queries seed the FIRST iteration verbatim (bounded to
            # DEEP_RESEARCH_MAX_QUERIES inside run_deep_research). None → generate from scratch.
            seed_queries=body.queries,
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


@router.post(
    "/research/optimize-topic",
    response_model=ResearchOptimizeTopicResponse,
    summary="Optimize a seed topic + propose search queries before a Deep Research run",
    description=(
        "B5/D3 pre-run optimization (llm_wiki parity). ONE bounded provider.chat() call "
        "(I6 provider-neutral, I7 single call + timeout + token budget + cost logged) that reads "
        "the vault overview.md + purpose.md and rephrases the seed `topic` into a domain-specific "
        "research topic plus 3-5 web-search-optimized queries. LLM-only — no web/SearXNG call here "
        "(I9 not engaged; the actual run happens later via POST /research/start with the edited "
        "topic/queries). "
        "GRACEFUL: when NO provider is configured (or the call times out / errors) it returns 200 "
        "with {optimized_topic: <topic>, queries: [<topic>]} so the confirm dialog still works "
        "offline — it does NOT 503/500. 429 if per-IP rate limit exceeded (R13-9)."
    ),
    responses={
        200: {"description": "Optimized topic + queries (or graceful echo fallback)"},
        422: {"description": "Validation error (empty topic)"},
        429: {"description": "Per-IP rate limit exceeded (R13-9)"},
    },
    dependencies=[Depends(rate_limit)],
)
async def research_optimize_topic(
    body: ResearchOptimizeTopicRequest,
) -> ResearchOptimizeTopicResponse:
    """
    POST /research/optimize-topic — B5/D3 pre-run optimize + confirm surface.

    Delegates to ops.deep_research.optimize_topic (single bounded provider call, I6/I7). The
    endpoint NEVER 500s on provider issues: optimize_topic swallows provider errors and returns
    the naive echo fallback so the UI dialog always prefills. This handler only maps the domain
    OptimizedTopic to the response model.
    """
    from app.ops.deep_research import optimize_topic

    vault_id = body.vault_id or settings.vault_id
    result = await optimize_topic(vault_id=vault_id, topic=body.topic)

    logger.info(
        "research_optimize_topic: vault=%s topic=%r → optimized=%r queries=%d",
        vault_id,
        body.topic,
        result.optimized_topic,
        len(result.queries),
    )
    return ResearchOptimizeTopicResponse(
        optimized_topic=result.optimized_topic,
        queries=result.queries,
    )


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


@router.delete(
    "/research/runs/{run_id}",
    response_model=ResearchDeleteResponse,
    summary="Delete a deep-research run from history",
    description=(
        "Removes one deep_research_runs row — and its deep_research_sources child rows (ORM "
        "cascade, ADR-0024 §7.2) — from the run history list (GET /research/runs). History "
        "cleanup only: this does NOT touch a wiki page the run may have produced "
        "(synthesis_page_id) — that page is ordinary wiki content and must be removed via "
        "DELETE /pages/{page_id} (F13 cascade-delete) if desired, independently. The run's "
        "raw/sources/deep-research-<id>.md file is best-effort removed ONLY when no page was "
        "ever created from it (synthesis_page_id IS NULL) — pure orphan cleanup; otherwise the "
        "file is left in place. Makes ZERO inference calls. 404 if unknown run_id. 409 while "
        "the run is still 'running' (deleting an in-flight row would race the background task's "
        "writes)."
    ),
    responses={
        200: {"description": "Run deleted from history"},
        404: {"description": "No run with this id"},
        409: {"description": "Run is still running — cannot delete an in-flight run"},
    },
)
async def delete_research_run(run_id: uuid.UUID) -> ResearchDeleteResponse:
    """DELETE /research/runs/{run_id} — remove one run from history (v1.5.4, not F13)."""
    run_id_str = str(run_id)

    async with _m.get_session() as session:
        run_result = await session.execute(
            select(DeepResearchRun).where(DeepResearchRun.id == run_id_str)
        )
        run = run_result.scalar_one_or_none()

        if run is None:
            raise HTTPException(status_code=404, detail=f"Deep research run {run_id} not found")

        if run.status == "running":
            raise HTTPException(
                status_code=409,
                detail="Run is still running — cannot delete an in-flight run",
            )

        synthesis_page_id = run.synthesis_page_id

        # Explicit two-step delete (children before parent) rather than ORM
        # session.delete(run): DeepResearchRun.sources is cascade="all, delete-orphan"
        # BUT lazy="raise" (ADR-0024 §7.2) — relying on the ORM cascade would try to
        # lazy-load run.sources to process the cascade and raise. This also avoids
        # depending on the DB-level ON DELETE CASCADE FK behaving identically across
        # backends (SQLite test schema vs Postgres prod).
        await session.execute(
            sa_delete(DeepResearchSource).where(DeepResearchSource.run_id == run_id_str)
        )
        await session.execute(sa_delete(DeepResearchRun).where(DeepResearchRun.id == run_id_str))

        raw_source_deleted = False
        if synthesis_page_id is None:
            # Orphan cleanup only — a page that documents this raw file as its source keeps it.
            rel = f"raw/sources/deep-research-{run_id_str}.md"
            abs_path = settings.vault_root / rel
            try:
                if abs_path.exists():
                    abs_path.unlink()
                    raw_source_deleted = True
            except OSError as exc:  # noqa: BLE001 — best-effort, never fails the delete
                logger.warning(
                    "DELETE /research/runs/%s: failed to remove raw source %s: %s",
                    run_id,
                    rel,
                    exc,
                )

    logger.info(
        "DELETE /research/runs/%s: deleted (raw_source_deleted=%s)", run_id, raw_source_deleted
    )
    return ResearchDeleteResponse(id=run_id, raw_source_deleted=raw_source_deleted)
