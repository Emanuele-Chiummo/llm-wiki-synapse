"""
Per-domain APIRouter: /review/queue/* endpoints (F9 HITL Review Queue).

Covers:
  GET  /review/queue                      — paginated review items
  POST /review/queue/{id}/approve         — create page from review item
  POST /review/queue/{id}/create          — alias for approve
  POST /review/queue/{id}/skip            — set status=skipped
  POST /review/queue/{id}/deep-research   — delegate to F10
  POST /review/queue/bulk                 — bulk-process review items
  DELETE /review/queue/resolved           — delete resolved items
  POST /review/queue/sweep                — manual auto-resolution sweep
"""

from __future__ import annotations

import logging
import sys as _sys
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import Page, ReviewItem

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

# ── F9 Review Queue REST (ADR-0034 §7 — proposal model redesign) ─────────────

# Maximum page size for GET /review/queue (I7 — bounded list)
_REVIEW_QUEUE_MAX_LIMIT: int = 200


class ReferencedPage(BaseModel):
    """Convenience join for a referenced_page_ids entry (ADR-0044 §6.1)."""

    id: uuid.UUID
    title: str | None = None
    type: str | None = None


class ReviewItemResponse(BaseModel):
    """
    API response shape for one review_items row (ADR-0034 §7.1; ADR-0044 §6.1 additions).

    Projection carries the full proposal model: type, proposed_title, proposed_page_type,
    proposed_dir, rationale, and the three page FK fields (page_id/source_page_id/created_page_id).
    page_title is a convenience join from pages.title for the page_id FK (UI display).
    resolution records how the item was closed (null while pending).

    ADR-0044 §6.1 adds: content_key (opaque dedup handle), referenced_page_ids (array),
    referenced_pages (convenience join, stale ids filtered), search_queries (Deep-Research seeds).
    """

    id: uuid.UUID
    vault_id: str
    item_type: str = Field(
        description=(
            "missing-page | suggestion | contradiction | duplicate | confirm | "
            "purpose-suggestion"
        )
    )
    status: str = Field(
        description="pending | created | skipped | dismissed | deep_researched | auto_resolved"
    )
    proposed_title: str | None = Field(
        default=None,
        description="Title the LLM proposes to create; drives lazy skeleton (ADR-0034 §5.2)",
    )
    proposed_page_type: str | None = Field(
        default=None,
        description="entity|concept|source|synthesis|comparison; NULL → heuristic at Create",
    )
    proposed_dir: str | None = Field(
        default=None,
        description="Target wiki/ subdir (display only; recomputed at Create — ADR-0034 §5.2)",
    )
    rationale: str | None = Field(
        default=None,
        description="Why this matters; used as topic hint for Deep Research (ADR-0034 §3.1)",
    )
    page_id: uuid.UUID | None = Field(
        default=None,
        description="Review TARGET: conflicting/context existing page FK (ADR-0034 §3.1)",
    )
    page_title: str | None = Field(
        default=None,
        description="Convenience join from pages.title for page_id (UI display)",
    )
    source_page_id: uuid.UUID | None = Field(
        default=None,
        description="Provenance: page whose ingest produced this proposal (ADR-0034 §3.1)",
    )
    created_page_id: uuid.UUID | None = Field(
        default=None,
        description="Page produced by a successful Create action (ADR-0034 §5); null otherwise",
    )
    resolution: str | None = Field(
        default=None,
        description=(
            "created|skipped|dismissed|researched|rule_resolved|llm_resolved; null while pending"
        ),
    )
    deep_research_run_id: uuid.UUID | None = Field(
        default=None,
        description="FK → deep_research_runs.id; set when Deep-Research fires (AC-F10-5)",
    )
    # ── ADR-0044 §6.1: contextual depth + stable idempotency (additions) ──────────
    content_key: str | None = Field(
        default=None,
        description="Stable FNV-1a dedup handle (opaque to UI); NULL for confirm (ADR-0044 §3.2)",
    )
    referenced_page_ids: list[str] | None = Field(
        default=None,
        description="Array of page-id strings this proposal is contextually about (ADR-0044 §2)",
    )
    referenced_pages: list[ReferencedPage] | None = Field(
        default=None,
        description=(
            "Convenience join [{id,title,type}] for referenced_page_ids; stale ids filtered at "
            "render (ADR-0044 §6.1/§9.2) so the card renders [[title]] links without a round-trip"
        ),
    )
    search_queries: list[str] | None = Field(
        default=None,
        description="≤3 pre-generated search queries; search_queries[0] seeds Deep Research",
    )
    created_at: datetime
    reviewed_at: datetime | None = None

    model_config = {"from_attributes": True, "populate_by_name": True}


class ReviewQueueResponse(BaseModel):
    """Paginated response for GET /review/queue (ADR-0034 §7)."""

    items: list[ReviewItemResponse]
    total: int
    limit: int
    offset: int


class ReviewDeepResearchResponse(BaseModel):
    """202 response for POST /review/queue/{id}/deep-research (ADR-0034 §7)."""

    review_item_id: uuid.UUID
    run_id: uuid.UUID

    model_config = {
        "json_schema_extra": {
            "example": {
                "review_item_id": "00000000-0000-0000-0000-000000000001",
                "run_id": "00000000-0000-0000-0000-000000000002",
            }
        }
    }


class ReviewSweepResponse(BaseModel):
    """200 response for POST /review/queue/sweep (ADR-0034 §7)."""

    rule_resolved: int = Field(description="Items closed by rule-based Pass-1")
    llm_resolved: int = Field(description="Items closed by conservative LLM Pass-2")
    kept: int = Field(description="Items that remain pending after the sweep")


class ReviewBulkRequest(BaseModel):
    """Request body for POST /review/queue/bulk (ADR-0044 §6)."""

    vault_id: str = Field(..., description="Vault scope (required)")
    action: str = Field(
        ...,
        description="skip | dismiss | mark-resolved (ADR-0044 §6)",
    )
    ids: list[uuid.UUID] = Field(
        ...,
        description="Review item ids to act on; capped at REVIEW_BULK_MAX_IDS (I7 — 400 over)",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "vault_id": "default",
                "action": "dismiss",
                "ids": ["00000000-0000-0000-0000-000000000001"],
            }
        }
    }


class ReviewBulkResponse(BaseModel):
    """200 response for POST /review/queue/bulk (ADR-0044 §6)."""

    updated: int = Field(description="Pending ids mutated to the new terminal status")
    skipped_terminal: int = Field(
        description="Ids that were already terminal (or confirm under mark-resolved); never mutated"
    )


class ReviewClearResolvedResponse(BaseModel):
    """200 response for DELETE /review/queue/resolved (ADR-0044 §6)."""

    deleted: int = Field(description="Terminal rows hard-deleted for the vault (pending untouched)")


def _review_item_to_response(
    item: ReviewItem,
    page_title: str | None = None,
    *,
    referenced_pages: list[ReferencedPage] | None = None,
) -> ReviewItemResponse:
    """Convert ReviewItem ORM row to ReviewItemResponse (handles str/UUID for id fields).

    ADR-0044 §6.1: content_key, referenced_page_ids, search_queries pass through; referenced_pages
    is the caller-supplied convenience join (stale ids already filtered — §9.2)."""

    # UUID fields stored as str in SQLite, UUID in Postgres — normalise to UUID
    def _to_uuid(val: Any) -> uuid.UUID | None:
        if val is None:
            return None
        try:
            return uuid.UUID(str(val))
        except (ValueError, AttributeError):
            return None

    def _str_list(val: Any) -> list[str] | None:
        if not isinstance(val, list):
            return None
        out = [str(x) for x in val if isinstance(x, str) and x.strip()]
        return out or None

    return ReviewItemResponse(
        id=_to_uuid(item.id) or uuid.UUID(int=0),
        vault_id=item.vault_id,
        item_type=item.item_type,
        status=item.status,
        proposed_title=item.proposed_title,
        proposed_page_type=item.proposed_page_type,
        proposed_dir=item.proposed_dir,
        rationale=item.rationale,
        page_id=_to_uuid(item.page_id),
        page_title=page_title,
        source_page_id=_to_uuid(item.source_page_id),
        created_page_id=_to_uuid(item.created_page_id),
        resolution=item.resolution,
        deep_research_run_id=_to_uuid(item.deep_research_run_id),
        content_key=getattr(item, "content_key", None),
        referenced_page_ids=_str_list(getattr(item, "referenced_page_ids", None)),
        referenced_pages=referenced_pages,
        search_queries=_str_list(getattr(item, "search_queries", None)),
        created_at=item.created_at,
        reviewed_at=item.reviewed_at,
    )


@router.get(
    "/review/queue",
    response_model=ReviewQueueResponse,
    summary="List HITL review queue proposals",
    description=(
        "F9 HITL Review Queue (ADR-0034 §7; ADR-0044 §6 status filter + contextual projection). "
        "Returns paginated review_items for a vault, ordered created_at ASC. "
        "Each item is a PROPOSAL (missing-page|suggestion|contradiction|duplicate|confirm). "
        "status filter (ADR-0044 §6): pending (default) | resolved | dismissed | all. "
        "limit: default 50, max 200 (I7 — bounded page size). offset: >=0. "
        "vault_id: required filter. "
        "page_title is a convenience join from pages.title for the page_id FK (UI display). "
        "referenced_pages joins referenced_page_ids to [{id,title,type}] (stale ids filtered)."
    ),
    responses={
        200: {"description": "Paginated review proposals"},
        422: {"description": "Validation error (limit out of range, missing vault_id)"},
    },
)
async def list_review_queue(
    vault_id: str = Query(..., description="Vault scope (required)"),
    status: str = Query(
        default="pending",
        description="Status filter (ADR-0044 §6): pending | resolved | dismissed | all",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=_REVIEW_QUEUE_MAX_LIMIT,
        description=f"Max rows to return (1..{_REVIEW_QUEUE_MAX_LIMIT}); I7 cap",
    ),
    offset: int = Query(default=0, ge=0, description="Row offset for pagination"),
) -> ReviewQueueResponse:
    """
    GET /review/queue — paginated HITL review proposals (ADR-0034 §7, ADR-0044 §6 filter).

    READ-ONLY — no data_version bump, no ingest triggered.
    limit capped at 200 (I7 — bounded page size). page_title + referenced_pages are convenience
    joins on pages; referenced_pages drops ids that no longer resolve to a live page (§9.2).
    """
    from app.ops.review import list_queue

    queue_page = await list_queue(vault_id, limit=limit, offset=offset, status=status)

    # Load page_title for page_id + referenced_pages for referenced_page_ids in ONE bounded
    # pages read across all items on the page (convenience joins — ADR-0044 §6.1).
    def _ids_of(val: Any) -> list[str]:
        if not isinstance(val, list):
            return []
        return [str(x) for x in val if isinstance(x, str) and x.strip()]

    all_page_ids: set[str] = set()
    for it in queue_page.items:
        if it.page_id is not None:
            all_page_ids.add(str(it.page_id))
        for rid in _ids_of(getattr(it, "referenced_page_ids", None)):
            all_page_ids.add(rid)

    page_info: dict[str, tuple[str | None, str | None]] = {}
    if all_page_ids:
        from sqlalchemy import String as _SAString
        from sqlalchemy import cast as _sa_cast

        async with _m.get_session() as session:
            rows = await session.execute(
                select(Page.id, Page.title, Page.page_type).where(
                    # CAST for SQLite/Postgres id portability (mirrors retrieval.py / sweep).
                    _sa_cast(Page.id, _SAString).in_(list(all_page_ids)),
                    Page.deleted_at.is_(None),
                )
            )
            for row in rows:
                page_info[str(row[0])] = (row[1], row[2])

    items: list[ReviewItemResponse] = []
    for it in queue_page.items:
        page_title = page_info.get(str(it.page_id), (None, None))[0] if it.page_id else None
        # referenced_pages: resolve + DROP stale ids (§9.2 render-time filter, I9).
        referenced_pages: list[ReferencedPage] = []
        for rid in _ids_of(getattr(it, "referenced_page_ids", None)):
            info = page_info.get(rid)
            if info is None:
                continue  # stale id → filtered out
            try:
                referenced_pages.append(
                    ReferencedPage(id=uuid.UUID(rid), title=info[0], type=info[1])
                )
            except (ValueError, AttributeError):
                continue
        items.append(
            _review_item_to_response(
                it,
                page_title=page_title,
                referenced_pages=referenced_pages or None,
            )
        )

    return ReviewQueueResponse(
        items=items,
        total=queue_page.total,
        limit=queue_page.limit,
        offset=queue_page.offset,
    )


async def _create_review_item_handler(item_id: uuid.UUID) -> ReviewItemResponse:
    """
    Shared Create handler for both /approve and /create routes (ADR-0034 §5).

    Runs the bounded orchestrated loop to generate the page on-demand (lazy — ADR-0034 §2),
    writes it through write_wiki_page (I1 — one data_version bump), and returns 201.

    409 if item not pending or no ingest provider configured (I6).
    502 if generation fails; item left pending (§5.3).
    404 if item not found.
    """
    from app.ops.review import create_page_from_review

    item = await create_page_from_review(item_id)
    return _review_item_to_response(item)


@router.post(
    "/review/queue/{item_id}/approve",
    response_model=ReviewItemResponse,
    status_code=201,
    summary="Create: lazy on-demand page generation from a proposal",
    description=(
        "F9 HITL Review Queue — Create action (ADR-0034 §5; path kept for backward stability). "
        "Runs the bounded orchestrated loop targeting the proposed page, writes it through "
        "write_wiki_page (I1 — one data_version bump), sets status=created + created_page_id. "
        "409 if item is not pending or no ingest provider is configured (I6 — never hardcode). "
        "502 if generation fails; item is left pending — retry or skip. "
        "404 if item_id is unknown. "
        "Prefer the /create alias (explicit verb) for new clients (ADR-0034 §9 risk 6)."
    ),
    responses={
        201: {"description": "Page created; item status=created"},
        404: {"description": "Review item not found"},
        409: {"description": "Item not pending, or no ingest provider configured (I6)"},
        502: {"description": "Generation failed; item left pending"},
    },
)
async def approve_review_item(item_id: uuid.UUID) -> ReviewItemResponse:
    """POST /review/queue/{id}/approve — Create alias for backward compatibility (ADR-0034 §5)."""
    return await _create_review_item_handler(item_id)


@router.post(
    "/review/queue/{item_id}/create",
    response_model=ReviewItemResponse,
    status_code=201,
    summary="Create: lazy on-demand page generation from a proposal (explicit verb)",
    description=(
        "F9 HITL Review Queue — Create action (ADR-0034 §5 — preferred explicit alias). "
        "Identical to POST /review/queue/{id}/approve. "
        "Runs the bounded orchestrated loop targeting the proposed page, writes it through "
        "write_wiki_page (I1 — one data_version bump), sets status=created + created_page_id. "
        "409 if item is not pending or no ingest provider is configured (I6). "
        "502 if generation fails; item is left pending. "
        "404 if item_id is unknown."
    ),
    responses={
        201: {"description": "Page created; item status=created"},
        404: {"description": "Review item not found"},
        409: {"description": "Item not pending, or no ingest provider configured (I6)"},
        502: {"description": "Generation failed; item left pending"},
    },
)
async def create_review_item(item_id: uuid.UUID) -> ReviewItemResponse:
    """POST /review/queue/{id}/create — lazy on-demand Create (ADR-0034 §5 preferred verb)."""
    return await _create_review_item_handler(item_id)


@router.post(
    "/review/queue/{item_id}/skip",
    response_model=ReviewItemResponse,
    summary="Skip a review proposal",
    description=(
        "F9 HITL Review Queue — skip action (ADR-0034 §7). "
        "Sets status=skipped, resolution=skipped, reviewed_at=now(). "
        "404 if item_id is unknown."
    ),
    responses={
        200: {"description": "Item skipped"},
        404: {"description": "Review item not found"},
    },
)
async def skip_review_item(item_id: uuid.UUID) -> ReviewItemResponse:
    """POST /review/queue/{id}/skip — status write (ADR-0034 §7)."""
    from app.ops.review import skip

    item = await skip(item_id)
    return _review_item_to_response(item)


@router.post(
    "/review/queue/{item_id}/dismiss",
    response_model=ReviewItemResponse,
    summary="Dismiss a review proposal",
    description=(
        "F9 HITL Review Queue — dismiss action (ADR-0044 §6). "
        "Sets status=dismissed, resolution=dismissed, reviewed_at=now(). Terminal. "
        "Distinct from skip: 'hide this, I'm not acting' vs skip's 'considered and declined'. "
        "404 if item_id is unknown."
    ),
    responses={
        200: {"description": "Item dismissed"},
        404: {"description": "Review item not found"},
    },
)
async def dismiss_review_item(item_id: uuid.UUID) -> ReviewItemResponse:
    """POST /review/queue/{id}/dismiss — status write (ADR-0044 §6)."""
    from app.ops.review import dismiss

    item = await dismiss(item_id)
    return _review_item_to_response(item)


@router.post(
    "/review/queue/{item_id}/deep-research",
    response_model=ReviewDeepResearchResponse,
    status_code=202,
    summary="Trigger deep research for a review proposal",
    description=(
        "F9 HITL Review Queue — deep-research action (ADR-0034 §7, AC-F9-3, AC-F10-5). "
        "Sets status=deep_researched, resolution=researched; delegates to F10 with the item's "
        "proposed_title → rationale (first line) → page.title as the research topic. "
        "(pre_generated_query is DROPPED in ADR-0034; topic derivation updated.) "
        "Stores the returned run_id in review_items.deep_research_run_id (AC-F10-5). "
        "Returns 202 {review_item_id, run_id} immediately (fire-and-poll). "
        "503 if SEARXNG_URL is unset (inherits F10's guard, I9). "
        "404 if item_id is unknown."
    ),
    responses={
        202: {
            "description": "Deep research started; poll GET /research/runs/{run_id} for progress"
        },
        404: {"description": "Review item not found"},
        503: {"description": "SEARXNG_URL is not configured (I9)"},
    },
)
async def deep_research_review_item(item_id: uuid.UUID) -> ReviewDeepResearchResponse:
    """POST /review/queue/{id}/deep-research — delegate to F10 (ADR-0034 §7, AC-F10-5)."""
    from app.ops.review import deep_research as _deep_research_op

    result = await _deep_research_op(item_id)
    return ReviewDeepResearchResponse(
        review_item_id=result.review_item_id,
        run_id=result.run_id,
    )


@router.post(
    "/review/queue/sweep",
    response_model=ReviewSweepResponse,
    summary="Manual auto-resolution sweep of pending review proposals",
    description=(
        "F9 HITL Review Queue — manual sweep trigger (ADR-0034 §6). "
        "Runs Pass-1 (rule-based title-match for missing-page/duplicate) and "
        "Pass-2 (conservative bounded LLM judgment). "
        "Bounded; idempotent; never fails (returns partial results on error). "
        "vault_id: required. "
        "Auto-triggered after each orchestrated ingest run and after a successful Create. "
        "confirm items are NEVER auto-resolved (Do-NOT #7, ADR-0034 §10)."
    ),
    responses={
        200: {"description": "Sweep complete; counts of resolved and kept items"},
        422: {"description": "Validation error (missing vault_id)"},
    },
)
async def sweep_review_queue(
    vault_id: str = Query(..., description="Vault scope (required)"),
) -> ReviewSweepResponse:
    """POST /review/queue/sweep — manual auto-resolution sweep (ADR-0034 §6)."""
    from app.ops.review import sweep_reviews

    result = await sweep_reviews(vault_id)
    return ReviewSweepResponse(
        rule_resolved=result.rule_resolved,
        llm_resolved=result.llm_resolved,
        kept=result.kept,
    )


@router.post(
    "/review/queue/bulk",
    response_model=ReviewBulkResponse,
    summary="Bulk status action on review proposals",
    description=(
        "F9 HITL Review Queue — bounded bulk status write (ADR-0044 §6, I7). "
        "action: skip | dismiss | mark-resolved. "
        "Only PENDING ids (scoped to vault_id) are mutated; already-terminal ids are counted in "
        "skipped_terminal and NEVER re-mutated. mark-resolved NEVER auto-resolves a `confirm` "
        "item (Do-NOT #6/#10 — it is counted as skipped_terminal). No provider call. "
        "len(ids) is capped at REVIEW_BULK_MAX_IDS (400 over cap — I7)."
    ),
    responses={
        200: {"description": "Bulk action applied; {updated, skipped_terminal}"},
        400: {"description": "ids exceed REVIEW_BULK_MAX_IDS, or unknown action (I7)"},
    },
)
async def bulk_review_queue(body: ReviewBulkRequest) -> ReviewBulkResponse:
    """POST /review/queue/bulk — bounded bulk status write (ADR-0044 §6)."""
    from app.config import settings as _settings
    from app.ops.review import bulk_update_reviews

    max_ids = int(getattr(_settings, "review_bulk_max_ids", 200))
    if len(body.ids) > max_ids:
        raise HTTPException(
            status_code=400,
            detail=(
                f"bulk ids ({len(body.ids)}) exceed REVIEW_BULK_MAX_IDS ({max_ids}) — "
                "split into smaller batches (I7 — bounded bulk write)."
            ),
        )
    if body.action not in ("skip", "dismiss", "mark-resolved"):
        raise HTTPException(
            status_code=400,
            detail=f"Unknown bulk action {body.action!r}; expected skip|dismiss|mark-resolved.",
        )

    result = await bulk_update_reviews(
        vault_id=body.vault_id,
        action=body.action,
        ids=body.ids,
    )
    return ReviewBulkResponse(updated=result.updated, skipped_terminal=result.skipped_terminal)


@router.delete(
    "/review/queue/resolved",
    response_model=ReviewClearResolvedResponse,
    summary="Clear (hard-delete) terminal review proposals",
    description=(
        "F9 HITL Review Queue — 'Clear resolved' (ADR-0044 §6, I7). "
        "Hard-deletes terminal rows (skipped/dismissed/created/auto_resolved/deep_researched) for "
        "the vault in ONE bounded vault-scoped statement. PENDING rows are NEVER touched. "
        "Idempotent. These rows are advisory metadata (not vault content); created_page_id points "
        "at a page that persists independently (ADR-0044 §9.5). "
        "vault_id: required."
    ),
    responses={
        200: {"description": "Terminal rows deleted; {deleted}"},
        422: {"description": "Validation error (missing vault_id)"},
    },
)
async def clear_resolved_review_queue(
    vault_id: str = Query(..., description="Vault scope (required)"),
) -> ReviewClearResolvedResponse:
    """DELETE /review/queue/resolved — bounded hard-delete of terminal rows (ADR-0044 §6)."""
    from app.ops.review import clear_resolved_reviews

    deleted = await clear_resolved_reviews(vault_id)
    return ReviewClearResolvedResponse(deleted=deleted)


# ── B5/D2: bulk-resolve + PATCH single item ───────────────────────────────────
# These are DISTINCT from POST /review/queue/bulk (ADR-0044 §6, UI bulk-select actions):
#   /bulk          — existing: vault-scoped bulk skip/dismiss/mark-resolved (action-typed)
#   /bulk-resolve  — NEW: id-list + action (llm_wiki parity: resolves each via per-item funcs)
#   PATCH /{id}    — NEW: single-item open/close toggle (llm_wiki parity)


class BulkResolveRequest(BaseModel):
    """Request body for POST /review/queue/bulk-resolve (B5/D2 — llm_wiki parity)."""

    ids: list[str] = Field(
        ...,
        description=(
            "Review item id strings (UUID) to resolve; "
            "capped at 200 (I7 — 422 beyond cap)."
        ),
    )
    action: str = Field(
        ...,
        description=(
            "Resolution action: skip | dismiss "
            "(exact tokens from ops/review.py; 422 on unknown)."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "ids": ["00000000-0000-0000-0000-000000000001"],
                "action": "skip",
            }
        }
    }


class BulkResolveResponse(BaseModel):
    """Response for POST /review/queue/bulk-resolve (B5/D2)."""

    resolved: int = Field(description="Items successfully resolved via the per-item action")
    not_found: int = Field(description="ids that had no matching review item")
    count: int = Field(description="Total ids supplied (= resolved + not_found + terminal_skipped)")


# B5/D2 bulk-resolve: cap on ids (I7)
_BULK_RESOLVE_MAX_IDS: int = 200


@router.post(
    "/review/queue/bulk-resolve",
    response_model=BulkResolveResponse,
    summary="Bulk-resolve review items by id list (B5/D2 — llm_wiki parity)",
    description=(
        "F9 HITL Review Queue — per-item bulk resolve (B5/D2). "
        "Distinct from POST /review/queue/bulk (vault-scoped action-typed bulk): "
        "this endpoint accepts a flat id list and applies the action (skip|dismiss) "
        "to each via the EXACT per-item ops.review function (same seam as the REST /{id}/skip "
        "and /{id}/dismiss endpoints — I9, no second writer). "
        "len(ids) capped at 200 (I7 — 422 beyond). "
        "action must be skip or dismiss (exact tokens from ops/review.py — 422 on unknown). "
        "Already-terminal or not-found ids are counted and silently skipped."
    ),
    responses={
        200: {"description": "Bulk resolve complete; {resolved, not_found, count}"},
        422: {"description": "ids exceed 200 cap, or unknown action (I7)"},
    },
)
async def bulk_resolve_review_queue(body: BulkResolveRequest) -> BulkResolveResponse:
    """POST /review/queue/bulk-resolve — per-item bulk resolve (B5/D2 llm_wiki parity)."""
    import uuid as _uuid

    from app.ops.review import dismiss as _dismiss
    from app.ops.review import skip as _skip

    # I7 cap
    if len(body.ids) > _BULK_RESOLVE_MAX_IDS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"bulk-resolve ids ({len(body.ids)}) exceed cap ({_BULK_RESOLVE_MAX_IDS}) — "
                "split into smaller batches (I7 — bounded bulk resolve)."
            ),
        )

    # Validate action (exact tokens from ops/review.py)
    _ALLOWED = frozenset({"skip", "dismiss"})
    if body.action not in _ALLOWED:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown action {body.action!r}; bulk-resolve accepts: "
                f"{sorted(_ALLOWED)} "
                "(for lazy page generation use POST /review/queue/{id}/create)."
            ),
        )

    resolved = 0
    not_found = 0

    for id_str in body.ids:
        try:
            item_uuid = _uuid.UUID(id_str)
        except (ValueError, AttributeError):
            not_found += 1
            continue

        try:
            if body.action == "skip":
                await _skip(item_uuid)
            else:
                await _dismiss(item_uuid)
            resolved += 1
        except HTTPException as exc:
            if exc.status_code == 404:
                not_found += 1
            else:
                logger.warning("bulk-resolve: item %s action=%s → %s", id_str, body.action, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("bulk-resolve: item %s action=%s failed: %s", id_str, body.action, exc)

    return BulkResolveResponse(
        resolved=resolved,
        not_found=not_found,
        count=len(body.ids),
    )


class PatchReviewRequest(BaseModel):
    """Request body for PATCH /review/queue/{id} (B5/D2 — llm_wiki parity)."""

    resolved: bool = Field(
        default=True,
        description=(
            "True → resolve (action required); False → reopen to pending. "
            "Default True."
        ),
    )
    action: str = Field(
        default="skip",
        description=(
            "Resolution action when resolved=True: skip | dismiss "
            "(exact tokens from ops/review.py; 422 on unknown). "
            "Ignored when resolved=False (reopen)."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {"resolved": True, "action": "skip"}
        }
    }


@router.patch(
    "/review/queue/{item_id}",
    response_model=ReviewItemResponse,
    summary="Resolve or reopen a single review item (B5/D2 — llm_wiki parity)",
    description=(
        "F9 HITL Review Queue — single-item PATCH (B5/D2 llm_wiki parity). "
        "resolved=true (default): apply action (skip|dismiss) via the per-item ops.review seam. "
        "resolved=false: reopen the item to pending (clears status, resolution, reviewed_at). "
        "action must be skip|dismiss (422 on unknown); ignored when resolved=false. "
        "Routes through the exact ops.review._set_status primitive — no second writer (I9). "
        "404 if item_id is unknown."
    ),
    responses={
        200: {"description": "Item patched; returns updated ReviewItemResponse"},
        404: {"description": "Review item not found"},
        422: {"description": "Unknown action"},
    },
)
async def patch_review_item(item_id: uuid.UUID, body: PatchReviewRequest) -> ReviewItemResponse:
    """PATCH /review/queue/{id} — resolve or reopen a review item (B5/D2 llm_wiki parity)."""
    from app.ops.review import dismiss as _dismiss
    from app.ops.review import skip as _skip

    if body.resolved:
        # Validate action (exact tokens from ops/review.py)
        _ALLOWED = frozenset({"skip", "dismiss"})
        if body.action not in _ALLOWED:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unknown action {body.action!r}; PATCH accepts: {sorted(_ALLOWED)}."
                ),
            )
        if body.action == "skip":
            item = await _skip(item_id)
        else:
            item = await _dismiss(item_id)
    else:
        # Reopen: set status back to pending, explicitly clear resolution + reviewed fields.
        # _set_status only writes resolution when non-None, so we need a direct DB write here
        # to NULL resolution + reviewed_at (reopening is the inverse of skip/dismiss).
        from sqlalchemy import select as _select

        from app.db import get_session as _get_session
        from app.models import ReviewItem as _ReviewItem

        item_id_str = str(item_id)
        async with _get_session() as session:
            row = await session.execute(
                _select(_ReviewItem).where(_ReviewItem.id == item_id_str)
            )
            item_orm = row.scalar_one_or_none()
            if item_orm is None:
                raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")
            item_orm.status = "pending"
            item_orm.resolution = None
            item_orm.reviewed_at = None
            item_orm.reviewed_by = None
            await session.flush()
            await session.refresh(item_orm)
            session.expunge(item_orm)
        item = item_orm

    return _review_item_to_response(item)
