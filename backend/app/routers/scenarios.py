"""
Per-domain APIRouter: GET /scenarios + POST /scenarios/{id}/apply (R7).

Also re-exports _SCENARIOS and _SCENARIO_INDEX for backward-compatible test imports.
"""

from __future__ import annotations

import logging
import sys as _sys
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.scenarios_data import SCENARIO_INDEX as _SCENARIO_INDEX
from app.scenarios_data import SCENARIOS as _SCENARIOS

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

# ── GET /scenarios + POST /scenarios/{id}/apply  (R7-1, [F1, K1]) ─────────────
# 5 vault-bootstrap presets: Research, Reading, PersonalGrowth, Business, General.
# Each preset overwrites vault/purpose.md + vault/schema.md on explicit user action.


class ScenarioItem(BaseModel):
    """One scenario preset descriptor (R7-1 list response)."""

    id: str
    name: str
    description: str


class ScenarioListResponse(BaseModel):
    """Response for GET /scenarios (R7-1)."""

    items: list[ScenarioItem]


class ScenarioApplyResponse(BaseModel):
    """Response for POST /scenarios/{scenario_id}/apply (R7-1)."""

    applied: bool


@router.get(
    "/scenarios",
    response_model=ScenarioListResponse,
    summary="List available vault scenario templates",
    description=(
        "Return the 5 built-in vault preset templates (R7-1, AC-R7-1-1, [F1, K1]). "
        "Each preset provides a purpose.md body and schema.md stub appropriate to the domain. "
        "Apply a preset via POST /scenarios/{id}/apply."
    ),
)
async def list_scenarios() -> ScenarioListResponse:
    """GET /scenarios — R7-1 preset list [F1, K1]."""
    return ScenarioListResponse(
        items=[
            ScenarioItem(id=s["id"], name=s["name"], description=s["description"])
            for s in _SCENARIOS
        ]
    )


@router.post(
    "/scenarios/{scenario_id}/apply",
    response_model=ScenarioApplyResponse,
    summary="Apply a vault scenario template",
    description=(
        "Write vault/purpose.md and vault/schema.md for the chosen preset "
        "(R7-1, AC-R7-1-2, [F1, K1]). "
        "This is an explicit user action — both files are OVERWRITTEN with preset content. "
        "Bumps data_version. 404 for unknown scenario_id."
    ),
    responses={
        200: {"description": "Preset applied; purpose.md and schema.md written"},
        404: {"description": "Unknown scenario_id"},
    },
)
async def apply_scenario(scenario_id: str) -> ScenarioApplyResponse:
    """
    POST /scenarios/{scenario_id}/apply — R7-1 [F1, K1].

    Overwrites vault/purpose.md and vault/schema.md with the preset content for the
    chosen scenario. This is an explicit user action — existing files are replaced.
    Bumps data_version so the watcher / graph engine pick up the change.
    """
    from app.ingest.orchestrator import bump_version

    scenario = _SCENARIO_INDEX.get(scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario_id {scenario_id!r}")

    vault = settings.vault_root
    purpose_path = vault / "purpose.md"
    schema_path = vault / "schema.md"

    try:
        purpose_path.write_text(scenario["purpose_md"], encoding="utf-8")
        schema_path.write_text(scenario["schema_md"], encoding="utf-8")
    except OSError as exc:
        logger.error("apply_scenario: failed to write preset files: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to write preset files: {exc}") from exc

    # Create scenario-specific extra wiki/ subdirectories (idempotent — WS-E, v1.7.0).
    extra_dirs: list[str] = scenario["extra_dirs"]
    for extra in extra_dirs:
        try:
            (vault / extra).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("apply_scenario: could not create %r: %s", extra, exc)

    await bump_version()
    logger.info(
        "apply_scenario: applied preset %r → purpose.md + schema.md written; extra_dirs=%s",
        scenario_id,
        extra_dirs,
    )
    return ScenarioApplyResponse(applied=True)
