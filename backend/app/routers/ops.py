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
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app import runtime_state
from app.config import settings
from app.ops_scheduler import OpsScheduler

logger = logging.getLogger(__name__)

router = APIRouter()


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


# ── POST/GET /ops/synthesize — bounded corpus-level synthesis/comparison generator ──
# ADR-0067 D3 / audit P0-3: seeds candidate clusters from the 4-signal graph, then AUTO-WRITES a
# synthesis (thesis+integration) / comparison (table) page per high-confidence cluster and
# PROPOSES borderline clusters to the F9 review queue. Background asyncio task + 202, single-flight
# (409 while running). No dormant-400 — it runs whenever called (no-provider vault → clean no-op).
# Strong task reference kept in a module-level set — a bare create_task can be GC'd mid-run.

_synthesize_tasks: set[asyncio.Task[Any]] = set()


class SynthesizeRequest(BaseModel):
    """Request body for POST /ops/synthesize (ADR-0074)."""

    max_pages: int | None = Field(
        default=None,
        ge=1,
        description="Cap on pages auto-written this run (synthesis+comparison; clamped).",
    )
    max_candidates: int | None = Field(
        default=None,
        ge=1,
        description="Cap on all evaluated clusters, including Review/skip paths (I7).",
    )
    token_budget: int | None = Field(
        default=None, ge=1, description="Token budget for the run (clamped server-side, I7)."
    )
    force: bool = Field(
        default=False,
        description="Regenerate/update the same keyed corpus pages; never create duplicates.",
    )
    mode: Literal["auto", "review-only"] = Field(
        default="auto",
        description="auto writes high-confidence pages; review-only enqueues eligible clusters.",
    )


class SynthesizeStartResponse(BaseModel):
    """202 response for POST /ops/synthesize."""

    status: str = Field(default="started", description="'started' — synthesize runs in background")
    max_pages: int = Field(description="Effective (clamped) page cap for this run")
    max_candidates: int = Field(description="Effective total candidate-evaluation cap")
    token_budget: int = Field(description="Effective (clamped) token budget for this run")
    force: bool = Field(description="Echo of the force flag")
    mode: Literal["auto", "review-only"] = Field(description="Effective run mode")


class SynthesizeStatusResponse(BaseModel):
    """GET /ops/synthesize — single-flight state + last completed summary."""

    running: bool = Field(description="True while a synthesize run is in flight")
    current: dict[str, Any] | None = Field(
        default=None, description="Effective bounds/mode of the active run; null while idle"
    )
    last_summary: dict[str, Any] | None = Field(
        default=None, description="Summary of the most recent completed run (null if never ran)"
    )


@router.post(
    "/ops/synthesize",
    status_code=202,
    response_model=SynthesizeStartResponse,
    responses={409: {"description": "A synthesize run is already in flight"}},
)
async def start_synthesize(body: SynthesizeRequest | None = None) -> SynthesizeStartResponse:
    """Start ONE bounded corpus-level synthesis/comparison pass (ADR-0067 D3, P0-3, I6/I7)."""
    from app.ops import synthesize as _sy

    body = body or SynthesizeRequest()

    if not _sy.reserve_run():
        raise HTTPException(
            status_code=409,
            detail="A synthesize run is already running. Poll GET /ops/synthesize.",
        )

    mp, tb = _sy.clamp_bounds(body.max_pages, body.token_budget)
    mc = _sy.clamp_max_candidates(body.max_candidates)

    async def _run() -> None:
        try:
            await _sy.run_synthesize(
                vault_id=settings.vault_id,
                max_pages=body.max_pages,
                max_candidates=body.max_candidates,
                token_budget=body.token_budget,
                force=body.force,
                mode=body.mode,
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 — run_synthesize never raises by contract; belt+braces
            logger.error("synthesize: unhandled error in background run: %s", exc)

    try:
        task = asyncio.create_task(_run())
    except Exception:
        _sy.release_reservation()
        raise
    _synthesize_tasks.add(task)
    task.add_done_callback(_synthesize_tasks.discard)

    return SynthesizeStartResponse(
        status="started",
        max_pages=mp,
        max_candidates=mc,
        token_budget=tb,
        force=body.force,
        mode=body.mode,
    )


@router.get("/ops/synthesize", response_model=SynthesizeStatusResponse)
async def get_synthesize_status() -> SynthesizeStatusResponse:
    """Single-flight state + last summary of the corpus synthesize pass (ADR-0067 D3)."""
    from app.ops import synthesize as _sy

    last = _sy.get_last_summary()
    return SynthesizeStatusResponse(
        running=_sy.is_running(),
        current=_sy.get_current(),
        last_summary=last.as_dict() if last is not None else None,
    )


@router.get(
    "/ops/synthesize/audit",
    response_model=dict[str, Any],
    summary="Dry-run audit of possible legacy corpus-page duplicates",
)
async def audit_synthesize_duplicates(
    max_pages: int = Query(default=500, ge=1, le=2_000),
) -> dict[str, Any]:
    """Read-only ADR-0074 audit; never tags, merges, deletes or rewrites legacy pages."""
    from app.ops import synthesize as _sy

    return await _sy.audit_legacy_duplicates(settings.vault_id, max_pages=max_pages)


# ── POST/GET /ops/backfill-related — ADR-0067 D2 related: + slug-link conversion ──
# Brings EXISTING wiki pages up to ADR-0067 D2 conventions (P2-1 related: backfill and
# P2-2 title→slug link rewrite) WITHOUT re-ingesting. Zero LLM cost; DRY-RUN by default.
# Background asyncio task + 202, single-flight (409 while running). Strong task reference
# kept in a module-level set — a bare create_task can be GC'd mid-run.

_backfill_related_tasks: set[asyncio.Task[Any]] = set()


class BackfillRelatedRequest(BaseModel):
    """Request body for POST /ops/backfill-related (ADR-0067 D2 P2-1+P2-2)."""

    max_pages: int | None = Field(
        default=None,
        ge=1,
        description="Cap on wiki pages scanned per run (clamped server-side, I7).",
    )
    apply: bool = Field(
        default=False,
        description=(
            "False (default) = dry-run only — returns planned counts + samples with no file "
            "writes. True = perform actual writes + incremental re-index + data_version bump."
        ),
    )


class BackfillRelatedStartResponse(BaseModel):
    """202 response for POST /ops/backfill-related."""

    status: str = Field(default="started", description="'started' — backfill runs in background")
    max_pages: int = Field(description="Effective (clamped) page cap for this run")
    apply: bool = Field(description="Whether file writes are performed (False = dry-run)")


class BackfillRelatedStatusResponse(BaseModel):
    """GET /ops/backfill-related — single-flight state + last completed summary."""

    running: bool = Field(description="True while a backfill-related run is in flight")
    last_summary: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Summary of the most recent completed run (null if never ran). "
            "Includes 'samples' (first 5 changed pages) in dry-run mode."
        ),
    )


@router.post(
    "/ops/backfill-related",
    status_code=202,
    response_model=BackfillRelatedStartResponse,
    responses={409: {"description": "A backfill-related run is already in flight"}},
)
async def start_backfill_related(
    body: BackfillRelatedRequest,
) -> BackfillRelatedStartResponse:
    """
    Start ONE bounded ADR-0067 D2 backfill pass (apply=False → dry-run) [P2-1,P2-2,I1,I7].

    Brings existing wiki pages up to D2 conventions:
      P2-1 — sets/replaces ``related:`` from resolved outbound wikilinks (cap 8, slugs only).
      P2-2 — rewrites ``[[Title]]`` / ``[[Title|alias]]`` to ``[[slug|Title]]`` /
              ``[[slug|alias]]`` in page bodies (rendering unchanged).

    Zero LLM cost.  Dry-run by default; pass ``apply=true`` to commit changes.
    """
    from app.ops import backfill_related as _br  # noqa: PLC0415

    if _br.is_running():
        raise HTTPException(
            status_code=409,
            detail=(
                "A backfill-related run is already in flight. " "Poll GET /ops/backfill-related."
            ),
        )

    mp = _br.clamp_bounds(body.max_pages)

    async def _run() -> None:
        try:
            await _br.run_backfill_related(
                vault_id=settings.vault_id,
                apply=body.apply,
                max_pages=body.max_pages,
            )
        except Exception as exc:  # noqa: BLE001 — run_backfill_related never raises; belt+braces
            logger.error("backfill-related: unhandled error in background run: %s", exc)

    task = asyncio.create_task(_run())
    _backfill_related_tasks.add(task)
    task.add_done_callback(_backfill_related_tasks.discard)

    return BackfillRelatedStartResponse(status="started", max_pages=mp, apply=body.apply)


@router.get("/ops/backfill-related", response_model=BackfillRelatedStatusResponse)
async def get_backfill_related_status() -> BackfillRelatedStatusResponse:
    """Single-flight state + last summary of the ADR-0067 D2 backfill [P2-1,P2-2,I1]."""
    from app.ops import backfill_related as _br  # noqa: PLC0415

    last = _br.get_last_summary()
    return BackfillRelatedStatusResponse(
        running=_br.is_running(),
        last_summary=last.as_dict() if last is not None else None,
    )


# ── POST/GET /ops/reconcile-folders — bounded folder-vs-type reconcile sweep ──
# Physically moves wiki pages whose filesystem folder does not match the folder
# implied by their ``type`` (e.g. an entity living under concepts/ → entities/).
# Background asyncio task + 202, single-flight (409 while running), DRY-RUN by default.
# Zero LLM cost (deterministic folder routing via type_subdir). Strong task reference
# kept in a module-level set — a bare create_task can be GC'd mid-run.

_reconcile_tasks: set[asyncio.Task[Any]] = set()


class ReconcileFoldersRequest(BaseModel):
    """Request body for POST /ops/reconcile-folders."""

    max_pages: int | None = Field(
        default=None,
        ge=1,
        description="Cap on wiki pages scanned per run (clamped server-side, I7).",
    )
    apply: bool = Field(
        default=False,
        description=(
            "False (default) = dry-run only — returns a plan with no file writes. "
            "True = perform actual moves + DB + Qdrant updates."
        ),
    )


class ReconcileFoldersStartResponse(BaseModel):
    """202 response for POST /ops/reconcile-folders."""

    status: str = Field(default="started", description="'started' — reconcile runs in background")
    max_pages: int = Field(description="Effective (clamped) page cap for this run")
    apply: bool = Field(description="Whether moves are applied (False = dry-run)")


class ReconcileFoldersStatusResponse(BaseModel):
    """GET /ops/reconcile-folders — single-flight state + last completed summary."""

    running: bool = Field(description="True while a reconcile run is in flight")
    last_summary: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Summary of the most recent completed run (null if never ran). "
            "Includes 'plan' (list of proposed moves) in dry-run mode."
        ),
    )


@router.post(
    "/ops/reconcile-folders",
    status_code=202,
    response_model=ReconcileFoldersStartResponse,
    responses={409: {"description": "A reconcile-folders run is already in flight"}},
)
async def start_reconcile_folders(
    body: ReconcileFoldersRequest,
) -> ReconcileFoldersStartResponse:
    """
    Start ONE bounded folder-reconcile sweep (apply=False → dry-run; apply=True → moves) [K1,I1].

    Finds wiki pages whose physical folder (e.g. ``concepts/``) does not match the folder
    implied by their ``type`` frontmatter (e.g. ``entity`` → ``entities/``) and — when
    ``apply=True`` — moves them, updating Postgres + Qdrant. Zero LLM cost; purely
    deterministic. The plan is visible via GET /ops/reconcile-folders after the run
    completes (dry-run returns plan without writing; apply returns counts + by_folder).
    """
    from app.ops import reconcile_folders as _rf  # noqa: PLC0415

    if _rf.is_running():
        raise HTTPException(
            status_code=409,
            detail="A reconcile-folders run is already in flight. Poll GET /ops/reconcile-folders.",
        )

    mp = _rf.clamp_bounds(body.max_pages)

    async def _run() -> None:
        try:
            await _rf.run_reconcile(
                vault_id=settings.vault_id,
                apply=body.apply,
                max_pages=body.max_pages,
            )
        except Exception as exc:  # noqa: BLE001 — run_reconcile never raises by contract
            logger.error("reconcile-folders: unhandled error in background run: %s", exc)

    task = asyncio.create_task(_run())
    _reconcile_tasks.add(task)
    task.add_done_callback(_reconcile_tasks.discard)

    return ReconcileFoldersStartResponse(status="started", max_pages=mp, apply=body.apply)


@router.get("/ops/reconcile-folders", response_model=ReconcileFoldersStatusResponse)
async def get_reconcile_folders_status() -> ReconcileFoldersStatusResponse:
    """Single-flight state + last summary of the folder-reconcile sweep [K1,I1]."""
    from app.ops import reconcile_folders as _rf  # noqa: PLC0415

    last = _rf.get_last_summary()
    return ReconcileFoldersStatusResponse(
        running=_rf.is_running(),
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

    scheduler = runtime_state.ops_scheduler()

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

    scheduler = runtime_state.ops_scheduler()
    if scheduler is None:
        # Fallback: create a temporary scheduler (test environments that bypass lifespan).
        scheduler = OpsScheduler()

    try:
        await scheduler.run_now(op_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {"status": "triggered", "op": op}


# ── System self-update (R12-3, B1: Watchtower HTTP API) ───────────────────────────


class UpdateStatusResponse(BaseModel):
    """Deployment update availability (read-only; safe to poll)."""

    current_version: str = Field(..., description="Running backend version.")
    latest_version: str | None = Field(
        None, description="Latest published GitHub Release tag (X.Y.Z), or null if unknown."
    )
    update_available: bool = Field(
        ..., description="True when latest_version is semver-newer than current_version."
    )
    update_supported: bool = Field(
        ...,
        description=(
            "True when the Watchtower HTTP API is configured, i.e. POST /ops/system-update can "
            "actually trigger an update. When false the UI hides the 'update' button."
        ),
    )


class SystemUpdateResponse(BaseModel):
    """Result of poking Watchtower to pull+recreate the labelled containers."""

    triggered: bool = Field(..., description="True when Watchtower accepted the update request.")
    message: str = Field(..., description="Human-facing status line.")


@router.get(
    "/ops/update-status",
    response_model=UpdateStatusResponse,
    summary="Deployment update availability (R12-3)",
    description=(
        "Compares the running backend version with the latest published GitHub Release. Read-only "
        "and cached ~1h. `update_supported` reflects whether the Watchtower HTTP API is wired up."
    ),
)
async def get_update_status_endpoint() -> UpdateStatusResponse:
    from app.ops.system_update import get_update_status  # noqa: PLC0415

    st = await get_update_status()
    return UpdateStatusResponse(
        current_version=st.current_version,
        latest_version=st.latest_version,
        update_available=st.update_available,
        update_supported=st.update_supported,
    )


@router.post(
    "/ops/system-update",
    response_model=SystemUpdateResponse,
    status_code=202,
    summary="Trigger a Watchtower pull + recreate (R12-3, B1)",
    description=(
        "Pokes Watchtower's /v1/update (fire-and-forget) to pull the latest images and recreate "
        "every container labelled com.centurylinklabs.watchtower.enable=true on the host. The "
        "backend itself is recreated, so the connection drops — the UI shows an indeterminate "
        "'update in progress' state and reconnects when the new backend is up. Auth-gated in "
        "server mode (ADR-0052)."
    ),
    responses={
        202: {"description": "Update triggered."},
        501: {"description": "Update mechanism not configured (no Watchtower URL/token)."},
        502: {"description": "Watchtower request failed."},
    },
)
async def trigger_system_update_endpoint() -> SystemUpdateResponse:
    import httpx  # noqa: PLC0415

    from app.ops.system_update import (  # noqa: PLC0415
        UpdateNotConfiguredError,
        trigger_system_update,
    )

    try:
        message = await trigger_system_update()
    except UpdateNotConfiguredError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Watchtower request failed: {exc}") from exc
    return SystemUpdateResponse(triggered=True, message=message)
