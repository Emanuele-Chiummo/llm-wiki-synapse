"""
Per-domain APIRouter: /ops/* endpoints.

Covers:
  POST/GET /ops/backfill-domains   — bounded domain backfill (ADR-0054 §6)
  POST/GET /ops/reclassify-types   — bounded type re-classification (SPRINT-v1.2)
  GET      /ops/schedules          — list registered OpsScheduler jobs
  POST     /ops/schedules/{op}/run-now — trigger a job immediately
"""

from __future__ import annotations

import asyncio
import logging
import sys as _sys
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.ops_scheduler import OpsScheduler

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

# ── POST/GET /ops/backfill-domains — one-time bounded domain backfill (ADR-0054 §6) ──
# Background asyncio task + 202, mirroring POST /research/start. Single-flight (409 while
# running), 400 when the vocabulary is dormant (no provider calls ever fire dormant, I6/I7).
# Strong task reference kept in a module-level set — a bare create_task can be GC'd mid-run.

_backfill_tasks: set[asyncio.Task[Any]] = set()


class BackfillDomainsRequest(BaseModel):
    """Request body for POST /ops/backfill-domains (ADR-0054 §6)."""

    max_pages: int | None = Field(
        default=None, ge=1, description="Cap on pages processed this run (clamped server-side)."
    )
    token_budget: int | None = Field(
        default=None, ge=1, description="Token budget for the run (clamped server-side, I7)."
    )
    force: bool = Field(
        default=False,
        description="Re-classify pages that already carry a domain/ tag (default: skip them).",
    )


class BackfillDomainsStartResponse(BaseModel):
    """202 response for POST /ops/backfill-domains."""

    status: str = Field(default="started", description="'started' — backfill runs in background")
    max_pages: int = Field(description="Effective (clamped) page cap for this run")
    token_budget: int = Field(description="Effective (clamped) token budget for this run")
    force: bool = Field(description="Whether already-tagged pages are re-classified")


class BackfillDomainsStatusResponse(BaseModel):
    """GET /ops/backfill-domains — single-flight state + last completed summary."""

    running: bool = Field(description="True while a backfill run is in flight")
    last_summary: dict[str, Any] | None = Field(
        default=None, description="Summary of the most recent completed run (null if never ran)"
    )


@router.post(
    "/ops/backfill-domains",
    status_code=202,
    response_model=BackfillDomainsStartResponse,
    responses={
        400: {"description": "Domain vocabulary is empty (feature dormant)"},
        409: {"description": "A backfill run is already in flight"},
    },
)
async def start_backfill_domains(body: BackfillDomainsRequest) -> BackfillDomainsStartResponse:
    """Start ONE bounded domain backfill over the existing vault (R12-2, ADR-0054 §6)."""
    from app.config_overrides import effective_domain_vocabulary
    from app.ops import backfill_domains as _bd

    if _bd.is_running():
        raise HTTPException(
            status_code=409,
            detail="A domain backfill is already running. Poll GET /ops/backfill-domains.",
        )
    if not effective_domain_vocabulary():
        raise HTTPException(
            status_code=400,
            detail=(
                "Domain vocabulary is empty — configure Settings > Advanced > "
                "domain_vocabulary first (the feature is dormant without it)."
            ),
        )

    mp, tb = _bd.clamp_bounds(body.max_pages, body.token_budget)

    async def _run() -> None:
        try:
            await _bd.run_backfill(
                vault_id=settings.vault_id,
                max_pages=body.max_pages,
                token_budget=body.token_budget,
                force=body.force,
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 — run_backfill never raises by contract; belt+braces
            logger.error("backfill-domains: unhandled error in background run: %s", exc)

    task = asyncio.create_task(_run())
    _backfill_tasks.add(task)
    task.add_done_callback(_backfill_tasks.discard)

    return BackfillDomainsStartResponse(
        status="started", max_pages=mp, token_budget=tb, force=body.force
    )


@router.get("/ops/backfill-domains", response_model=BackfillDomainsStatusResponse)
async def get_backfill_domains_status() -> BackfillDomainsStatusResponse:
    """Single-flight state + last summary of the domain backfill (ADR-0054 §6)."""
    from dataclasses import asdict

    from app.ops import backfill_domains as _bd

    last = _bd.get_last_summary()
    return BackfillDomainsStatusResponse(
        running=_bd.is_running(),
        last_summary=asdict(last) if last is not None else None,
    )


# ── POST/GET /ops/reclassify-types — bounded page-type re-classification (SPRINT-v1.2 tail) ──
# The TYPE twin of /ops/backfill-domains: re-assigns each page's `type` frontmatter per the
# curated schema.md rules (K8). Background asyncio task + 202, single-flight (409 while running).
# NO dormant-400 — schema.md always exists (the vault-context loader is tolerant). Strong task
# reference kept in a module-level set — a bare create_task can be GC'd mid-run.

_reclassify_tasks: set[asyncio.Task[Any]] = set()


class ReclassifyTypesRequest(BaseModel):
    """Request body for POST /ops/reclassify-types (SPRINT-v1.2 tail)."""

    max_pages: int | None = Field(
        default=None, ge=1, description="Cap on pages processed this run (clamped server-side)."
    )
    token_budget: int | None = Field(
        default=None, ge=1, description="Token budget for the run (clamped server-side, I7)."
    )
    force: bool = Field(
        default=False,
        description=(
            "Widen candidates from the suspicious set (NULL/untyped/concept) to ALL non-reserved "
            "wiki pages (overview/index are never touched in either mode)."
        ),
    )


class ReclassifyTypesStartResponse(BaseModel):
    """202 response for POST /ops/reclassify-types."""

    status: str = Field(default="started", description="'started' — reclassify runs in background")
    max_pages: int = Field(description="Effective (clamped) page cap for this run")
    token_budget: int = Field(description="Effective (clamped) token budget for this run")
    force: bool = Field(description="Whether ALL non-reserved pages are candidates")


class ReclassifyTypesStatusResponse(BaseModel):
    """GET /ops/reclassify-types — single-flight state + last completed summary."""

    running: bool = Field(description="True while a reclassify run is in flight")
    last_summary: dict[str, Any] | None = Field(
        default=None, description="Summary of the most recent completed run (null if never ran)"
    )


@router.post(
    "/ops/reclassify-types",
    status_code=202,
    response_model=ReclassifyTypesStartResponse,
    responses={409: {"description": "A reclassify run is already in flight"}},
)
async def start_reclassify_types(body: ReclassifyTypesRequest) -> ReclassifyTypesStartResponse:
    """Start ONE bounded page-type re-classification over the vault (SPRINT-v1.2 tail, K8/I7)."""
    from app.ops import reclassify_types as _rt

    if _rt.is_running():
        raise HTTPException(
            status_code=409,
            detail="A type re-classification is already running. Poll GET /ops/reclassify-types.",
        )

    mp, tb = _rt.clamp_bounds(body.max_pages, body.token_budget)

    async def _run() -> None:
        try:
            await _rt.run_reclassify(
                vault_id=settings.vault_id,
                max_pages=body.max_pages,
                token_budget=body.token_budget,
                force=body.force,
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 — run_reclassify never raises by contract; belt+braces
            logger.error("reclassify-types: unhandled error in background run: %s", exc)

    task = asyncio.create_task(_run())
    _reclassify_tasks.add(task)
    task.add_done_callback(_reclassify_tasks.discard)

    return ReclassifyTypesStartResponse(
        status="started", max_pages=mp, token_budget=tb, force=body.force
    )


@router.get("/ops/reclassify-types", response_model=ReclassifyTypesStatusResponse)
async def get_reclassify_types_status() -> ReclassifyTypesStatusResponse:
    """Single-flight state + last summary of the type re-classification (SPRINT-v1.2 tail)."""
    from app.ops import reclassify_types as _rt

    last = _rt.get_last_summary()
    return ReclassifyTypesStatusResponse(
        running=_rt.is_running(),
        last_summary=last.as_dict() if last is not None else None,
    )


# ── GET /ops/schedules + POST /ops/schedules/{op}/run-now (R12-7/A5) ─────────
# OpsScheduler status + manual trigger. Schedule FREQUENCIES are set via the existing
# PUT /config/app/{key} (S10 lint_schedule / S11 backfill_schedule — no new write endpoint).


class OpsScheduleEntry(BaseModel):
    """One row in the GET /ops/schedules response — state of one schedulable op."""

    op: str = Field(
        description="Operation name: 'lint', 'backfill', 'schema_review', or 'reclassify'"
    )
    schedule: str = Field(description="Effective schedule: off|hourly|daily|weekly")
    last_run_at: str | None = Field(
        default=None, description="ISO-8601 timestamp of the last completed run, or null."
    )
    last_status: str | None = Field(
        default=None, description="'ok' | 'dormant' | 'error:<msg>' | null (never run)."
    )
    last_detail: str | None = Field(
        default=None,
        description=(
            "Short human outcome of the last run ('12 tagged / 30 processed', "
            "'dormant: no domain vocabulary configured', 'error: ...'), or null. R13-12."
        ),
    )
    in_flight: bool = Field(description="True while this op is currently executing.")


class OpsSchedulesResponse(BaseModel):
    """Response body for GET /ops/schedules."""

    ops: list[OpsScheduleEntry]


@router.get(
    "/ops/schedules",
    response_model=OpsSchedulesResponse,
    summary="List schedulable ops with schedule and last-run state (R12-7/A5/R12-8/R12-9)",
    description=(
        "Returns the in-memory state of the OpsScheduler for each schedulable op "
        "(lint, backfill, schema_review, reclassify). Schedule frequencies are set via "
        "PUT /config/app/lint_schedule, PUT /config/app/backfill_schedule, "
        "PUT /config/app/schema_review_schedule, and PUT /config/app/reclassify_schedule "
        "(S10/S11/S12/S13 — allowed values: off|hourly|daily|weekly). "
        "State is in-memory and resets on container restart. "
        "Auth: SynapseAuthMiddleware (ADR-0052)."
    ),
)
async def get_ops_schedules() -> OpsSchedulesResponse:
    """GET /ops/schedules — OpsScheduler state for lint, backfill, schema_review, reclassify."""
    from app.config_overrides import get_effective  # noqa: PLC0415
    from app.ops_scheduler import _OP_NAMES  # noqa: PLC0415

    scheduler = _m._ops_scheduler

    entries: list[OpsScheduleEntry] = []
    for op in _OP_NAMES:
        schedule_key = f"{op}_schedule"
        schedule = get_effective(schedule_key, "off")
        if scheduler is not None:
            state = scheduler.get_state(op)
            last_run_at = state.last_run_at.isoformat() if state.last_run_at is not None else None
            last_status = state.last_status
            last_detail = state.last_detail
            in_flight = state.in_flight
        else:
            last_run_at = None
            last_status = None
            last_detail = None
            in_flight = False
        entries.append(
            OpsScheduleEntry(
                op=op,
                schedule=schedule,
                last_run_at=last_run_at,
                last_status=last_status,
                last_detail=last_detail,
                in_flight=in_flight,
            )
        )
    return OpsSchedulesResponse(ops=entries)


@router.post(
    "/ops/schedules/{op}/run-now",
    status_code=202,
    summary="Trigger a schedulable op immediately (R12-7/A5/R12-8/R12-9)",
    description=(
        "Manually trigger one scheduled op ('lint', 'backfill', 'schema_review', or "
        "'reclassify') regardless of the configured schedule. Returns 202 if triggered, "
        "409 if already in-flight, 400 if the op is 'backfill' and the domain vocabulary "
        "is empty (dormant). schema_review and reclassify have no dormant state — they run "
        "whenever called (anti-spam / single-flight dedup is inside each op). "
        "Auth: SynapseAuthMiddleware (ADR-0052)."
    ),
    responses={
        202: {"description": "Op triggered successfully."},
        400: {"description": "backfill: vocabulary dormant (feature off)."},
        404: {"description": "Unknown op name."},
        409: {"description": "Op is already in-flight."},
    },
)
async def run_ops_schedule_now(op: str) -> dict[str, str]:
    """POST /ops/schedules/{op}/run-now — trigger lint/backfill/schema_review/reclassify."""
    from app.ops_scheduler import _OP_NAMES, OpName  # noqa: PLC0415

    if op not in _OP_NAMES:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown op {op!r}. Valid ops: {list(_OP_NAMES)}",
        )
    op_name: OpName = op

    # For backfill: check vocabulary dormant state upfront (reuse the same check
    # as POST /ops/backfill-domains to avoid duplicating the dormant vocabulary logic).
    if op_name == "backfill":
        from app.config_overrides import effective_domain_vocabulary  # noqa: PLC0415

        if not effective_domain_vocabulary():
            raise HTTPException(
                status_code=400,
                detail=(
                    "Domain vocabulary is empty — configure Settings > Advanced > "
                    "domain_vocabulary first (backfill is dormant without it)."
                ),
            )

    # For reclassify: check single-flight guard from the module directly (409 when running).
    # No dormant-400 — reclassify operates on pages that exist regardless of vocabulary.
    if op_name == "reclassify":
        from app.ops.reclassify_types import is_running as _reclassify_is_running  # noqa: PLC0415

        if _reclassify_is_running():
            raise HTTPException(
                status_code=409,
                detail=(
                    "A type re-classification is already running. "
                    "Poll GET /ops/reclassify-types."
                ),
            )

    scheduler = _m._ops_scheduler
    if scheduler is None:
        # Fallback: create a temporary scheduler (test environments that bypass lifespan).
        scheduler = OpsScheduler()

    try:
        await scheduler.run_now(op_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"status": "triggered", "op": op}
