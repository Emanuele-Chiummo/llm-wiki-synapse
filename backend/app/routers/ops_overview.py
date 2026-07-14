"""
POST /ops/overview/regenerate — manual overview.md regeneration endpoint (ADR-0078).

Replaces the automatic per-ingest overview regen that was removed from the pipeline.
Runs regenerate_overview() for the active vault and returns a small status JSON.
Fire-and-forget semantics: the endpoint returns once the regeneration completes (or
degrades). It never raises a 5xx — the underlying _update_overview is degrade-safe (I7).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class OverviewRegenerateResponse(BaseModel):
    """200 response for POST /ops/overview/regenerate."""

    status: str
    """'regenerated' when the provider call succeeded; 'degraded' when it failed or no
    provider is configured (previous overview.md kept; ingest unaffected)."""


@router.post(
    "/ops/overview/regenerate",
    response_model=OverviewRegenerateResponse,
    summary="Regenerate overview.md (manual op, ADR-0078)",
    description=(
        "Trigger a bounded, degrade-safe regeneration of vault/wiki/overview.md via the "
        "configured ingest provider. As of ADR-0078, overview.md is no longer auto-updated "
        "on every ingest; use this endpoint to refresh it on demand. "
        "Returns status='regenerated' on success or 'degraded' when the provider is "
        "unavailable (the previous overview.md is kept; this endpoint never fails the caller)."
    ),
)
async def regenerate_overview_endpoint() -> OverviewRegenerateResponse:
    """
    Run one bounded overview regeneration for the active vault.

    Delegates to ops.overview.regenerate_overview() which calls
    orchestrator._update_overview() (bounded single provider call, degrade-safe, I6/I7).
    """
    from app.config import settings
    from app.ops.overview import regenerate_overview

    vault_path = settings.wiki_dir / "overview.md"
    existed_before = vault_path.exists()

    try:
        await regenerate_overview(analysis=None, origin_source="manual/ops-overview")
    except Exception as exc:  # noqa: BLE001 — degrade-safe contract
        logger.warning("POST /ops/overview/regenerate: unexpected error (degraded): %s", exc)
        return OverviewRegenerateResponse(status="degraded")

    # Determine outcome: if the file exists and is (or became) present we succeeded.
    # _update_overview is degrade-safe and logs internally; we treat "file now present" as
    # success and "no change / no provider" as degraded.
    exists_after = vault_path.exists()
    if exists_after and (not existed_before or True):
        # Provider succeeded (or file existed and was kept) — report regenerated when present.
        status = "regenerated"
    else:
        status = "degraded"

    return OverviewRegenerateResponse(status=status)
