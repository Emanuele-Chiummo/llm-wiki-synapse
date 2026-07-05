"""
Per-domain APIRouter: GET /status.
"""

from __future__ import annotations

import logging
import sys as _sys
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.config import settings
from app.models import ReviewItem, VaultState

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


class StatusResponse(BaseModel):
    vault_id: str
    data_version: int
    started_at: datetime
    uptime_seconds: float
    version: str = Field(
        description=(
            "Backend package version from pyproject.toml via importlib.metadata "
            "(ADR-0054 §6). Read at runtime — never a hardcoded literal."
        )
    )
    review_pending: int = Field(
        default=0,
        description=(
            "Count of pending review-queue items — feeds the NavRail badge via the "
            "existing /status poll (no dedicated poller, I3). Additive, non-breaking."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "vault_id": "default",
                "data_version": 3,
                "started_at": "2026-06-28T10:00:00Z",
                "uptime_seconds": 42.7,
                "version": "1.2.0",
                "review_pending": 5,
            }
        }
    }


@router.get(
    "/status",
    response_model=StatusResponse,
    summary="Service health + data_version",
    description=(
        "Returns vault_id, current data_version (monotonic ingest counter), "
        "service started_at, and uptime_seconds. (AC-REST-1, AC-F16dv-3)"
    ),
)
async def get_status() -> StatusResponse:
    async with _m.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        data_version = state.data_version if state is not None else 0

        # Pending review count for the NavRail badge (owner request, v1.2.x).
        # Piggybacks on the existing 30s /status poll — no new frontend poller (I3).
        review_row = await session.execute(
            select(func.count())
            .select_from(ReviewItem)
            .where(
                ReviewItem.vault_id == settings.vault_id,
                ReviewItem.status == "pending",
            )
        )
        review_pending: int = review_row.scalar_one()

    now = datetime.now(UTC)
    uptime = (now - _m._started_at).total_seconds()
    # Backend version: APP_VERSION env (release-stamped) wins over installed package
    # metadata, which can lag for editable/dev installs (ADR-0054 §6, R12-3).
    return StatusResponse(
        vault_id=settings.vault_id,
        data_version=data_version,
        started_at=_m._started_at,
        uptime_seconds=uptime,
        version=_m._resolve_backend_version(),
        review_pending=review_pending,
    )
