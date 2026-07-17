"""Per-domain config router: /import-schedule + /import-schedule/run-now (Feature S).

Split out of the monolithic app.routers.config (BE-REFAC-1). Same paths/contract.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException

from app import runtime_state
from app.config import settings
from app.import_scheduler import ImportScheduler, load_schedule, upsert_schedule
from app.models import ImportSchedule
from app.schemas.config import (
    ImportSchedulePutBody,
    ImportSchedulePutResponse,
    ImportScheduleResponse,
    RunNowResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# Strong task references — a bare create_task() can be GC'd mid-run (CPython weak-ref).
_bg_tasks: set[asyncio.Task[Any]] = set()

# ── Import schedule REST (Feature S, ADR-0020 §4.6) ───────────────────────────


def _schedule_to_response(schedule: ImportSchedule | None) -> ImportScheduleResponse:
    """Convert an ImportSchedule ORM row to the API response shape (or return defaults)."""
    if schedule is None:
        return ImportScheduleResponse()
    return ImportScheduleResponse(
        enabled=schedule.enabled,
        source_dir=schedule.source_dir,
        frequency=schedule.frequency,
        allowed_extensions=schedule.allowed_extensions,
        excluded_folders=schedule.excluded_folders,
        max_size_mb=schedule.max_size_mb,
        last_run_at=schedule.last_run_at,
        last_status=schedule.last_status,
        last_imported_count=schedule.last_imported_count,
        last_error=schedule.last_error,
    )


@router.get(
    "/import-schedule",
    response_model=ImportScheduleResponse,
    summary="Get scheduled folder import config + last-run status",
    description=(
        "Returns the current import schedule configuration and last-run status for the vault. "
        "Returns sane defaults (enabled=false, frequency='1h') if no row has been configured yet. "
        "Feature S (ADR-0020 §4.6)."
    ),
)
async def get_import_schedule() -> ImportScheduleResponse:
    """GET /import-schedule — current config + last-run status (ADR-0020 §4.6)."""
    schedule = await load_schedule(settings.vault_id)
    return _schedule_to_response(schedule)  # type: ignore[arg-type]


@router.put(
    "/import-schedule",
    response_model=ImportSchedulePutResponse,
    summary="Upsert import schedule configuration",
    description=(
        "Create or update the import schedule for the vault. "
        "Validates source_dir: if the directory does not exist inside the container, "
        "the row is still saved but dir_ok=false + dir_message is returned (save-then-warn). "
        "frequency must be one of '15m' | '1h' | '6h' | 'daily'. "
        "Config changes take effect on the next scheduler tick without a restart. "
        "Feature S (ADR-0020 §4.6)."
    ),
    responses={
        200: {"description": "Config saved (dir_ok may be false if mount is missing)"},
        422: {"description": "Invalid frequency value"},
    },
)
async def put_import_schedule(body: ImportSchedulePutBody) -> ImportSchedulePutResponse:
    """
    PUT /import-schedule — upsert schedule config with save-then-warn dir validation.

    If body.source_dir is provided, validate it exists & is readable inside the container.
    Persist regardless of dir_ok (operator may add the mount later; next tick picks it up).
    """
    # Build update kwargs
    update_kwargs: dict[str, object] = {}
    if body.enabled is not None:
        update_kwargs["enabled"] = body.enabled
    if body.source_dir is not None:
        update_kwargs["source_dir"] = body.source_dir
    if body.frequency is not None:
        update_kwargs["frequency"] = body.frequency
    if body.allowed_extensions is not None:
        update_kwargs["allowed_extensions"] = body.allowed_extensions or None
    if body.excluded_folders is not None:
        update_kwargs["excluded_folders"] = body.excluded_folders or None
    if body.max_size_mb is not None:
        update_kwargs["max_size_mb"] = body.max_size_mb or None
    update_kwargs["updated_at"] = datetime.now(UTC)

    await upsert_schedule(settings.vault_id, **update_kwargs)

    # Reload the freshly persisted row
    schedule = await load_schedule(settings.vault_id)

    # Dir validation (save-then-warn — ADR-0020 §4.6)
    dir_ok = True
    dir_message: str | None = None
    source_dir_val: str | None = getattr(schedule, "source_dir", None) if schedule else None
    if source_dir_val is not None:
        import os as _os

        if not _os.path.isdir(source_dir_val):
            dir_ok = False
            dir_message = (
                f"Directory '{source_dir_val}' is not visible inside the backend container. "
                "Add a mount (e.g. - ./import:/import:ro in docker-compose.yml) and set "
                "source_dir to the CONTAINER path — see DEPLOY.md."
            )

    base = _schedule_to_response(schedule)  # type: ignore[arg-type]
    return ImportSchedulePutResponse(
        enabled=base.enabled,
        source_dir=base.source_dir,
        frequency=base.frequency,
        allowed_extensions=base.allowed_extensions,
        excluded_folders=base.excluded_folders,
        max_size_mb=base.max_size_mb,
        last_run_at=base.last_run_at,
        last_status=base.last_status,
        last_imported_count=base.last_imported_count,
        last_error=base.last_error,
        dir_ok=dir_ok,
        dir_message=dir_message,
    )


@router.post(
    "/import-schedule/run-now",
    response_model=RunNowResponse,
    status_code=202,
    summary="Trigger one bounded import scan immediately",
    description=(
        "Trigger one bounded scan of source_dir immediately (same bounds as the scheduler: "
        "IMPORT_SCAN_MAX_FILES + IMPORT_SCAN_MAX_SECONDS, I7). "
        "The scan runs in the background; poll GET /import-schedule for the result. "
        "409 if a scan is already in-flight. 400 if disabled or source_dir unset/missing. "
        "Feature S (ADR-0020 §4.6)."
    ),
    responses={
        202: {"description": "Scan started in the background"},
        400: {"description": "Schedule is disabled, source_dir not set, or directory missing"},
        409: {"description": "A scan is already in-flight (I7 — no overlap)"},
    },
)
async def run_import_now() -> RunNowResponse:
    """
    POST /import-schedule/run-now — trigger one bounded scan immediately (ADR-0020 §4.6).

    Uses the module-level ImportScheduler singleton started in the lifespan.
    Falls back to creating a temporary scheduler if the lifespan singleton is absent
    (e.g. test environments that bypass lifespan).
    """
    scheduler = runtime_state.import_scheduler()
    if scheduler is None:
        # Graceful degradation: create an ephemeral scheduler (test / direct-startup scenario)
        scheduler = ImportScheduler()

    if scheduler.scan_in_flight:
        raise HTTPException(
            status_code=409,
            detail=(
                "A scan is already in-flight. "
                "Wait for it to finish or poll GET /import-schedule."
            ),
        )

    # Kick off the scan as a background task
    async def _run() -> None:
        try:
            await scheduler.run_now()
        except (ValueError, RuntimeError) as exc:
            logger.warning("run_import_now: scan failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.error("run_import_now: unhandled error in background scan: %s", exc)

    try:
        # Validate preconditions before starting the background task (so we get 400 synchronously)
        cfg = await load_schedule(settings.vault_id)
        if cfg is None or not getattr(cfg, "enabled", False):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Schedule is disabled or not configured. " "Enable it and set source_dir first."
                ),
            )
        source_dir = getattr(cfg, "source_dir", None)
        if not source_dir:
            raise HTTPException(
                status_code=400,
                detail="source_dir is not set. Configure a container-visible path first.",
            )
        import os as _os

        if not _os.path.isdir(str(source_dir)):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Directory '{source_dir}' is not accessible inside the container. "
                    "Add a mount (e.g. - ./import:/import:ro) and set "
                    "source_dir to the container path."
                ),
            )
    except HTTPException:
        raise

    _t = asyncio.create_task(_run())
    _bg_tasks.add(_t)
    _t.add_done_callback(_bg_tasks.discard)
    return RunNowResponse(status="started")
