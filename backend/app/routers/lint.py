"""
Per-domain APIRouter: /lint/* endpoints (K2 Lint-fix loop).

Covers:
  POST /lint/scan                         — bounded lint scan → run + findings
  GET  /lint/runs                         — lint run history
  GET  /lint/runs/{id}                    — run detail
  GET  /lint/findings                     — paginated findings (L10: category+severity filters)
  POST /lint/findings/{id}/apply          — HUMAN GATE: apply a fix
  POST /lint/findings/{id}/dismiss        — dismiss a finding
  POST /lint/findings/{id}/send-to-review — bridge to F9 review queue (L6)
  POST /lint/findings/batch               — batch apply|dismiss|send-to-review (L5, cap 200)
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app import runtime_state
from app.models import LintFinding, LintRun

logger = logging.getLogger(__name__)

router = APIRouter()


# ── K2 Lint-fix loop REST (ADR-0037) ─────────────────────────────────────────

# Maximum page size for GET /lint/findings (I7 — bounded list)
_LINT_FINDINGS_MAX_LIMIT: int = 200


class LintScanRequest(BaseModel):
    """
    Request body for POST /lint/scan (ADR-0037 §6).

    max_iter and token_budget are optional — env defaults (LINT_MAX_ITER / LINT_TOKEN_BUDGET)
    apply when omitted. Both are FROZEN onto the lint_runs row before the scan runs (I7).
    Server-side validators cap the range so callers cannot request an unbounded run (I7).
    semantic: when False, skip the provider pass entirely (deterministic findings only;
    run records iterations_used=0, cost=0). L8 / ADR-0037 B1.
    """

    vault_id: str = Field(..., description="Vault scope for the scan")
    max_iter: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description="Max semantic rounds (1..10); null → LINT_MAX_ITER default",
    )
    token_budget: int | None = Field(
        default=None,
        ge=1_000,
        le=1_000_000,
        description="Token budget (1_000..1_000_000); null → LINT_TOKEN_BUDGET default",
    )
    semantic: bool = Field(
        default=True,
        description=(
            "When False, skip the provider pass entirely (deterministic findings only; "
            "zero LLM cost). L8 / ADR-0037 B1."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {"vault_id": "default", "max_iter": 3, "token_budget": 20000}
        }
    }


class LintFindingResponse(BaseModel):
    """API response shape for one lint_findings row (ADR-0037 §6)."""

    id: uuid.UUID
    lint_run_id: uuid.UUID
    vault_id: str
    category: str = Field(
        description=(
            "orphan-page | broken-wikilink | missing-xref | contradiction | "
            "stale-claim | missing-page"
        )
    )
    severity: str = Field(description="info | warning | error")
    target_page_id: uuid.UUID | None = None
    target_title: str | None = None
    description: str
    proposed_action: str | None = Field(
        default=None,
        description="Fix apply_lint_fix would attempt; null for flag-only findings",
    )
    status: str = Field(description="open | applied | dismissed")
    resolution_note: str | None = None
    # L2 — suggested target (broken-wikilink only; NULL for all other categories)
    suggested_target: str | None = Field(
        default=None,
        description=(
            "Title of the best-matching existing page (broken-wikilink only). "
            "Displayed as the green 'Suggested target:' strip in the lint UI (L2)."
        ),
    )
    suggested_page_id: uuid.UUID | None = Field(
        default=None,
        description="FK → pages.id for the suggested_target page (L2).",
    )
    created_at: datetime
    reviewed_at: datetime | None = None

    model_config = {"from_attributes": True}


class LintRunResponse(BaseModel):
    """API response shape for one lint_runs row (ADR-0037 §6)."""

    id: uuid.UUID
    vault_id: str
    status: str = Field(description="running | completed | error")
    max_iter: int
    token_budget: int
    iterations_used: int
    findings_count: int
    total_cost_usd: float
    started_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    created_at: datetime

    @field_validator("total_cost_usd", mode="before")
    @classmethod
    def _decimal_to_float(cls, v: Any) -> float:
        return float(v) if v is not None else 0.0

    model_config = {"from_attributes": True}


class LintScanResponse(BaseModel):
    """200 response for POST /lint/scan (ADR-0037 §6): the run + its findings."""

    run: LintRunResponse
    findings: list[LintFindingResponse]


class LintRunListResponse(BaseModel):
    """Paginated list response for GET /lint/runs (ADR-0037 §6)."""

    items: list[LintRunResponse]
    total: int
    limit: int
    offset: int


class LintFindingListResponse(BaseModel):
    """Paginated list response for GET /lint/findings (ADR-0037 §6)."""

    items: list[LintFindingResponse]
    total: int
    limit: int
    offset: int
    severity_totals: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "COUNT(*) per severity (info/warning/error) over the same vault + status + "
            "category predicate as 'total', but IGNORING the severity filter and pagination. "
            "Use this to render accurate group-header counts (e.g. 'Warnings (N)') even when "
            "a severity filter is active. Keys present only for severities with at least one "
            "matching finding."
        ),
    )


def _lint_finding_to_response(f: LintFinding) -> LintFindingResponse:
    """Convert a LintFinding ORM row to LintFindingResponse (str/UUID normalisation)."""

    def _to_uuid(val: Any) -> uuid.UUID | None:
        if val is None:
            return None
        try:
            return uuid.UUID(str(val))
        except (ValueError, AttributeError):
            return None

    return LintFindingResponse(
        id=_to_uuid(f.id) or uuid.UUID(int=0),
        lint_run_id=_to_uuid(f.lint_run_id) or uuid.UUID(int=0),
        vault_id=f.vault_id,
        category=f.category,
        severity=f.severity,
        target_page_id=_to_uuid(f.target_page_id),
        target_title=f.target_title,
        description=f.description,
        proposed_action=f.proposed_action,
        status=f.status,
        resolution_note=f.resolution_note,
        suggested_target=getattr(f, "suggested_target", None),
        suggested_page_id=_to_uuid(getattr(f, "suggested_page_id", None)),
        created_at=f.created_at,
        reviewed_at=f.reviewed_at,
    )


def _lint_run_to_response(r: LintRun) -> LintRunResponse:
    """Convert a LintRun ORM row to LintRunResponse (str/UUID normalisation)."""

    def _to_uuid(val: Any) -> uuid.UUID:
        try:
            return uuid.UUID(str(val))
        except (ValueError, AttributeError):
            return uuid.UUID(int=0)

    return LintRunResponse(
        id=_to_uuid(r.id),
        vault_id=r.vault_id,
        status=r.status,
        max_iter=r.max_iter,
        token_budget=r.token_budget,
        iterations_used=r.iterations_used,
        findings_count=r.findings_count,
        total_cost_usd=float(r.total_cost_usd),
        started_at=r.started_at,
        completed_at=r.completed_at,
        error_message=r.error_message,
        created_at=r.created_at,
    )


@router.post(
    "/lint/scan",
    response_model=LintScanResponse,
    summary="Run a bounded lint scan (K2 — produces findings, never auto-fixes)",
    description=(
        "K2 Lint-fix loop (ADR-0037). Runs a BOUNDED, HUMAN-GATED health check of the wiki: "
        "deterministic structural checks (orphan-page via the graph/links read, no LLM) plus a "
        "bounded semantic pass (missing-xref | contradiction | stale-claim | missing-page) that "
        "rides the resolved ingest provider (I6 — never hardcoded). "
        "Bounds: max_iter (1..10) + token_budget (1k..1M) FROZEN on the lint_runs row (I7); "
        "findings capped at LINT_MAX_FINDINGS; total_cost_usd logged. "
        "Produces FINDINGS only — applying a fix requires the explicit human gate "
        "(POST /lint/findings/{id}/apply). Returns the run row + its findings."
    ),
    responses={
        200: {"description": "Scan complete; run + findings returned"},
        422: {"description": "Validation error (max_iter/token_budget out of range)"},
    },
)
async def lint_scan(body: LintScanRequest) -> LintScanResponse:
    """POST /lint/scan — run a bounded lint scan synchronously (ADR-0037 §6)."""
    from app.ops.lint import run_lint_scan

    result = await run_lint_scan(
        body.vault_id,
        max_iter=body.max_iter,
        token_budget=body.token_budget,
        semantic=body.semantic,
    )

    # Load the run row + its findings for the response.
    run_id_str = str(result.run_id)
    async with runtime_state.get_session() as session:
        run = (await session.execute(select(LintRun).where(LintRun.id == run_id_str))).scalar_one()
        finding_rows = list(
            (
                await session.execute(
                    select(LintFinding)
                    .where(LintFinding.lint_run_id == run_id_str)
                    .order_by(LintFinding.created_at.asc())
                )
            ).scalars()
        )
        session.expunge(run)
        for fr in finding_rows:
            session.expunge(fr)

    return LintScanResponse(
        run=_lint_run_to_response(run),
        findings=[_lint_finding_to_response(f) for f in finding_rows],
    )


@router.get(
    "/lint/runs",
    response_model=LintRunListResponse,
    summary="List lint scan run history",
    description=(
        "Paginated, created_at DESC list of lint_runs rows (ADR-0037 §6). "
        "limit: 1..100 default 20; offset: >=0 default 0; vault_id: optional filter. "
        "Mirrors GET /research/runs."
    ),
    responses={
        200: {"description": "Paginated lint run list"},
        422: {"description": "Validation error (limit/offset out of range)"},
    },
)
async def list_lint_runs_endpoint(
    limit: int = Query(default=20, ge=1, le=100, description="Max rows (1..100)"),
    offset: int = Query(default=0, ge=0, description="Row offset (>=0)"),
    vault_id: str | None = Query(default=None, description="Optional vault_id filter"),
) -> LintRunListResponse:
    """GET /lint/runs — paginated lint run list (ADR-0037 §6)."""
    from app.ops.lint import list_lint_runs

    page = await list_lint_runs(vault_id, limit=limit, offset=offset)
    return LintRunListResponse(
        items=[_lint_run_to_response(r) for r in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/lint/runs/{run_id}",
    response_model=LintRunResponse,
    summary="Get a lint scan run by id",
    description="Returns one lint_runs row (ADR-0037 §6). 404 if unknown run_id.",
    responses={
        200: {"description": "Lint run detail"},
        404: {"description": "No lint run with this id"},
    },
)
async def get_lint_run(run_id: uuid.UUID) -> LintRunResponse:
    """GET /lint/runs/{id} — lint run detail (ADR-0037 §6)."""
    run_id_str = str(run_id)
    async with runtime_state.get_session() as session:
        run = (
            await session.execute(select(LintRun).where(LintRun.id == run_id_str))
        ).scalar_one_or_none()
        if run is None:
            raise HTTPException(status_code=404, detail=f"Lint run {run_id} not found")
        session.expunge(run)
    return _lint_run_to_response(run)


@router.get(
    "/lint/findings",
    response_model=LintFindingListResponse,
    summary="List lint findings",
    description=(
        "Paginated, created_at ASC list of lint_findings rows (ADR-0037 §6). "
        "vault_id: required. status: optional filter (open|applied|dismissed; default open). "
        "category: optional filter (orphan-page|broken-wikilink|missing-xref|contradiction|"
        "stale-claim|missing-page|no-outlinks|suggestion; 422 on invalid). "
        "severity: optional filter (info|warning|error; 422 on invalid). L10 / ADR-0037 B1. "
        "limit: default 50, max 200 (I7 — bounded page size). offset: >=0."
    ),
    responses={
        200: {"description": "Paginated lint findings"},
        422: {"description": "Validation error (limit out of range, invalid category/severity)"},
    },
)
async def list_lint_findings_endpoint(
    vault_id: str = Query(..., description="Vault scope (required)"),
    status: str | None = Query(
        default="open",
        description="open | applied | dismissed; null/omit for all statuses",
    ),
    category: str | None = Query(
        default=None,
        description=(
            "Filter by category: orphan-page | broken-wikilink | missing-xref | "
            "contradiction | stale-claim | missing-page | no-outlinks | suggestion. "
            "422 on invalid value. L10."
        ),
    ),
    severity: str | None = Query(
        default=None,
        description="Filter by severity: info | warning | error. 422 on invalid value. L10.",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=_LINT_FINDINGS_MAX_LIMIT,
        description=f"Max rows (1..{_LINT_FINDINGS_MAX_LIMIT}); I7 cap",
    ),
    offset: int = Query(default=0, ge=0, description="Row offset for pagination"),
) -> LintFindingListResponse:
    """GET /lint/findings — paginated lint findings (ADR-0037 §6)."""
    from app.ops.lint import _VALID_CATEGORIES, _VALID_SEVERITIES, list_lint_findings

    # Treat the literal string "all" (or empty) as "no status filter".
    status_filter = None if status in (None, "", "all") else status

    # L10: validate category + severity (422 on invalid — mirrors _VALID_CATEGORIES contract).
    if category is not None and category not in _VALID_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=(f"Invalid category {category!r}; must be one of {sorted(_VALID_CATEGORIES)}"),
        )
    if severity is not None and severity not in _VALID_SEVERITIES:
        raise HTTPException(
            status_code=422,
            detail=(f"Invalid severity {severity!r}; must be one of {sorted(_VALID_SEVERITIES)}"),
        )

    page = await list_lint_findings(
        vault_id,
        status=status_filter,
        category=category,
        severity=severity,
        limit=limit,
        offset=offset,
    )
    return LintFindingListResponse(
        items=[_lint_finding_to_response(f) for f in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        severity_totals=page.severity_totals,
    )


@router.post(
    "/lint/findings/{finding_id}/apply",
    response_model=LintFindingResponse,
    summary="Apply a lint fix (HUMAN GATE)",
    description=(
        "K2 Lint-fix loop — human-gated apply (ADR-0037 §5). Applies ONLY safe/bounded fixes; "
        "bumps data_version at most ONCE per applied fix (I1); NEVER full-rescans. "
        "missing-xref → reuses the wikilink-enrichment seam (adds the [[link]] into the page "
        "body, I5). missing-page → delegates to the lazy-generation seam (bounded orchestrated "
        "loop, one data_version bump). orphan-page / contradiction / stale-claim are FLAG-ONLY: "
        "apply records acknowledgement (status=applied) but performs no automatic edit. "
        "409 if the finding is not open or no ingest provider is configured (I6). "
        "502 if a bounded fix fails; finding left open. 404 if finding_id is unknown."
    ),
    responses={
        200: {"description": "Fix applied (or finding acknowledged for flag-only categories)"},
        404: {"description": "Lint finding not found"},
        409: {"description": "Finding not open, or no ingest provider configured (I6)"},
        502: {"description": "Bounded fix failed; finding left open"},
    },
)
async def apply_lint_finding(finding_id: uuid.UUID) -> LintFindingResponse:
    """POST /lint/findings/{id}/apply — human-gated apply (ADR-0037 §5)."""
    from app.ops.lint import apply_lint_fix

    finding = await apply_lint_fix(finding_id)
    return _lint_finding_to_response(finding)


@router.post(
    "/lint/findings/{finding_id}/dismiss",
    response_model=LintFindingResponse,
    summary="Dismiss a lint finding",
    description=(
        "K2 Lint-fix loop — dismiss action (ADR-0037 §5). Sets status=dismissed, "
        "reviewed_at=now(). No edit, no data_version bump. 404 if finding_id is unknown."
    ),
    responses={
        200: {"description": "Finding dismissed"},
        404: {"description": "Lint finding not found"},
    },
)
async def dismiss_lint_finding_endpoint(finding_id: uuid.UUID) -> LintFindingResponse:
    """POST /lint/findings/{id}/dismiss — status write (ADR-0037 §5)."""
    from app.ops.lint import dismiss_lint_finding

    finding = await dismiss_lint_finding(finding_id)
    return _lint_finding_to_response(finding)


# ── L6 — POST /lint/findings/{id}/send-to-review ─────────────────────────────────


@router.post(
    "/lint/findings/{finding_id}/send-to-review",
    response_model=LintFindingResponse,
    summary="Bridge a lint finding to the F9 HITL review queue",
    description=(
        "L6 / ADR-0037 B1. Maps finding category → review item_type and calls "
        "ops/review.enqueue_review(). Category mapping: "
        "broken-wikilink → missing-page; missing-page → missing-page; "
        "contradiction → contradiction; stale-claim → suggestion; "
        "orphan-page → suggestion; missing-xref → suggestion. "
        "proposed_title = suggested_target when present (broken-wikilink), else target_title. "
        "On success: finding status → 'applied', resolution_note = 'sent to review (<id>)'. "
        "409 if the finding is not open. 404 if finding_id is unknown."
    ),
    responses={
        200: {"description": "Finding sent to review; status → applied"},
        404: {"description": "Lint finding not found"},
        409: {"description": "Finding not open"},
    },
)
async def send_to_review_endpoint(finding_id: uuid.UUID) -> LintFindingResponse:
    """POST /lint/findings/{id}/send-to-review — bridge to F9 review queue (L6)."""
    from app.ops.lint import send_finding_to_review

    finding = await send_finding_to_review(finding_id)
    return _lint_finding_to_response(finding)


# ── L5 — POST /lint/findings/batch ────────────────────────────────────────────────

# NOTE: this route MUST be declared BEFORE /lint/findings/{finding_id}/... routes to avoid
# FastAPI routing the literal segment "batch" as a UUID. Reorder is handled at the router
# registration level — the route is declared last here but must be mounted before the
# parameterised routes in app/main.py (or the router include order resolves it since FastAPI
# matches literal paths before parameterised ones when declared earlier).


class BatchFindingsRequest(BaseModel):
    """Request body for POST /lint/findings/batch (L5 / ADR-0037 B1)."""

    ids: list[uuid.UUID] = Field(
        ...,
        description=("List of finding UUIDs to act on. " "Cap: <= 200 (I7 — bounded; 422 beyond)."),
    )
    action: str = Field(
        ...,
        description="Action to apply: apply | dismiss | send-to-review",
    )


class BatchItemResult(BaseModel):
    """Per-id result within POST /lint/findings/batch response (L5)."""

    id: uuid.UUID
    status: str = Field(description="ok | error")
    detail: str | None = None


class BatchFindingsResponse(BaseModel):
    """Response for POST /lint/findings/batch (L5 / ADR-0037 B1)."""

    results: list[BatchItemResult]
    ok_count: int
    error_count: int


_BATCH_VALID_ACTIONS = frozenset({"apply", "dismiss", "send-to-review"})
_BATCH_MAX_IDS = 200  # I7 — matches ops/lint._BATCH_MAX_IDS


@router.post(
    "/lint/findings/batch",
    response_model=BatchFindingsResponse,
    summary="Batch apply|dismiss|send-to-review for multiple lint findings",
    description=(
        "L5 / ADR-0037 B1. Apply a single action to multiple findings sequentially. "
        "Actions: apply | dismiss | send-to-review. "
        f"Cap: ids ≤ {_BATCH_MAX_IDS} (I7 — 422 beyond). "
        "Per-id failure does NOT abort the batch — all ids are attempted. "
        "Response: {results: [{id, status: ok|error, detail}], ok_count, error_count}."
    ),
    responses={
        200: {"description": "Batch processed; see results for per-id status"},
        422: {"description": (f"ids exceeds cap of {_BATCH_MAX_IDS}, or invalid action")},
    },
)
async def batch_findings_endpoint(body: BatchFindingsRequest) -> BatchFindingsResponse:
    """POST /lint/findings/batch — sequential batch action (L5 / ADR-0037 B1)."""
    # I7 cap: reject oversized batches before any DB work.
    if len(body.ids) > _BATCH_MAX_IDS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Batch size {len(body.ids)} exceeds the cap of {_BATCH_MAX_IDS} (I7). "
                "Split into smaller batches."
            ),
        )
    if body.action not in _BATCH_VALID_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid action {body.action!r}; must be one of {sorted(_BATCH_VALID_ACTIONS)}",
        )

    from app.ops.lint import apply_batch

    batch_resp = await apply_batch(body.ids, body.action)
    return BatchFindingsResponse(
        results=[
            BatchItemResult(id=uuid.UUID(r.id), status=r.status, detail=r.detail)
            for r in batch_resp.results
        ],
        ok_count=batch_resp.ok_count,
        error_count=batch_resp.error_count,
    )
