"""
Per-domain APIRouter: GET /search (4-phase RAG retrieval, F5).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()


# ── GET /search Pydantic models (F5, ADR-0022 §2.5) ──────────────────────────


class SearchResultItem(BaseModel):
    """
    One citation entry in the GET /search response (ADR-0022 §2.5, AC-F5-6).

    Maps to Citation.{n, ref.id, ref.title, ref.slug, score, phase}.
    """

    n: int = Field(..., description="1-based citation index, contiguous from 1")
    id: str = Field(..., description="UUID of the pages row (== Qdrant point id, ADR-0002)")
    title: str = Field(..., description="Frontmatter title or filename stem (never empty, §2.6)")
    slug: str = Field(..., description="slugify(title) — derived, not a DB column (§2.6)")
    score: float = Field(..., description="Cosine similarity (vector) or edge weight (expansion)")
    phase: str = Field(..., description='"vector" | "expansion"')

    model_config = {
        "json_schema_extra": {
            "example": {
                "n": 1,
                "id": "00000000-0000-0000-0000-000000000001",
                "title": "Homelab Setup",
                "slug": "homelab-setup",
                "score": 0.87,
                "phase": "vector",
            }
        }
    }


class SearchResponse(BaseModel):
    """
    GET /search response (ADR-0022 §2.5, AC-F5-6).

    read-only — never bumps data_version (AC-F5-5).
    0-hit → 200 with empty results + empty context (AC-F5-7a).
    """

    query: str
    context: str = Field(
        ...,
        description="Assembled context string with inline [n] markers (≤ token_budget, ADR-0022)",
    )
    results: list[SearchResultItem] = Field(
        ...,
        description="Citations in rank order (vector seeds first, then expansions by edge weight)",
    )
    data_version: int = Field(
        ...,
        description="Snapshot read BEFORE assembly — proves the call is read-only (AC-F5-5)",
    )
    approx_tokens: int = Field(..., description="char/4 estimate of context length")
    token_budget: int = Field(..., description="20% of context_window used as the retrieval slice")

    model_config = {
        "json_schema_extra": {
            "example": {
                "query": "homelab docker services",
                "context": "[1] Homelab Setup\nDocker Compose ...\n",
                "results": [
                    {
                        "n": 1,
                        "id": "00000000-0000-0000-0000-000000000001",
                        "title": "Homelab Setup",
                        "slug": "homelab-setup",
                        "score": 0.87,
                        "phase": "vector",
                    }
                ],
                "data_version": 5,
                "approx_tokens": 512,
                "token_budget": 6553,
            }
        }
    }


# ── GET /search ───────────────────────────────────────────────────────────────


# ── R8-5: valid page types for GET /search ?type= param (AC-R8-5-1) ───────────
# Mirrors VALID_PAGE_TYPES in retrieval.py; kept here so FastAPI can build the 422
# description from the same frozenset without importing retrieval at module import time.
_SEARCH_VALID_TYPES: frozenset[str] = frozenset(
    {"entity", "concept", "source", "synthesis", "comparison", "query"}
)

# R8-5: valid sort options for GET /search ?sort= param (AC-R8-5-1).
_SEARCH_VALID_SORTS: frozenset[str] = frozenset({"relevance", "date_desc", "date_asc"})


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="4-phase RAG retrieval (F5, ADR-0022)",
    description=(
        "Run the F5 4-phase retrieval pipeline (ADR-0022 §2.2, AC-F5-6) and return a grounded "
        "context string + citation list. "
        "Phase 1: dense vector search via bge-m3 (Qdrant, top-k). "
        "Phase 2: BFS graph-expansion over the `edges` table (depth ≤ 2). "
        "Phase 3: token-budget allocation (20% of context_window, F14). "
        "Phase 4: context assembly with inline [n] markers. "
        "0-hit query → 200 with empty results + empty context (AC-F5-7a). "
        "READ-ONLY — never bumps data_version (AC-F5-5). "
        "R8-5: optional `type` (comma-separated page types) and "
        "`sort` (relevance|date_desc|date_asc) "
        "params — 422 on unknown type or sort value (AC-R8-5-1). "
        "Documented in openapi.json (I8, AC-F5-6)."
    ),
    responses={
        200: {"description": "Retrieval result (0-hit → empty results array)"},
        422: {"description": "Validation error (k out of range, missing q, or unknown type/sort)"},
    },
)
async def search(
    q: str = Query(..., min_length=1, description="The query string to retrieve context for"),
    vault_id: str | None = Query(
        default=None,
        description="Vault scope; defaults to settings.vault_id",
    ),
    k: int = Query(
        default=8,
        ge=1,
        le=50,
        description="Dense top-k for the vector phase (1..50); default 8 (ADR-0022 §2.1)",
    ),
    context_window: int | None = Query(
        default=None,
        ge=4096,
        le=1_000_000,
        description="Context window override (4096..1_000_000); null → 32 768 default (F14)",
    ),
    type: str | None = Query(  # noqa: A002 — shadowing built-in is intentional for API param name
        default=None,
        description=(
            "R8-5: Comma-separated page types to filter results. "
            "Valid values: entity, concept, source, synthesis, comparison, query. "
            "Multiple values: type=entity,concept. "
            "Omit to return all types (AC-R8-5-1). "
            "Unknown value → 422."
        ),
        alias="type",
    ),
    sort: str | None = Query(
        default=None,
        description=(
            "R8-5: Sort order for results. "
            "relevance (default) = cosine/edge-weight ranking unchanged. "
            "date_desc = newest first (updated_at DESC). "
            "date_asc = oldest first (updated_at ASC). "
            "Phase internals (budgets, BFS) are never changed (I7). "
            "Unknown value → 422 (AC-R8-5-1)."
        ),
    ),
) -> SearchResponse:
    """
    GET /search — F5 4-phase retrieval (ADR-0022, AC-F5-6).

    Single bounded pass (I7): Qdrant bge-m3 dense search → edges BFS expansion → budget
    allocation → context assembly. Zero inference calls, zero vault walk (I1). Read-only
    — data_version is unchanged (AC-F5-5).

    R8-5: optional `type` and `sort` params (AC-R8-5-1). 422 on unknown values.
    """
    from app.chat.context import DEFAULT_CONTEXT_WINDOW as _DEFAULT_WINDOW
    from app.rag.retrieval import SearchSortOption, retrieve

    # ── R8-5: validate and parse `type` param (AC-R8-5-1) ─────────────────────
    type_filter: list[str] = []
    if type is not None:
        raw_types = [t.strip() for t in type.split(",") if t.strip()]
        unknown = [t for t in raw_types if t not in _SEARCH_VALID_TYPES]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown page type(s): {unknown!r}. "
                    f"Valid values: {sorted(_SEARCH_VALID_TYPES)}"
                ),
            )
        type_filter = raw_types

    # ── R8-5: validate `sort` param (AC-R8-5-1) ───────────────────────────────
    effective_sort: SearchSortOption = "relevance"
    if sort is not None:
        if sort not in _SEARCH_VALID_SORTS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown sort value: {sort!r}. " f"Valid values: {sorted(_SEARCH_VALID_SORTS)}"
                ),
            )
        effective_sort = sort  # type: ignore[assignment]  # validated above

    effective_vault_id = vault_id or settings.vault_id
    window = context_window or _DEFAULT_WINDOW

    rctx = await retrieve(
        query=q,
        vault_id=effective_vault_id,
        context_window=window,
        k=k,
        type_filter=type_filter or None,
        sort=effective_sort,
    )

    results: list[SearchResultItem] = [
        SearchResultItem(
            n=c.n,
            id=c.ref.id,
            title=c.ref.title,
            slug=c.ref.slug,
            score=c.score,
            phase=c.phase,
        )
        for c in rctx.citations
    ]

    return SearchResponse(
        query=rctx.query,
        context=rctx.text,
        results=results,
        data_version=rctx.data_version,
        approx_tokens=rctx.approx_tokens,
        token_budget=rctx.token_budget,
    )
