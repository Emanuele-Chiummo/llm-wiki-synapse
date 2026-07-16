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

# BE-PERF-10: memoize the resolved supports_vision capability, keyed on the provider_config
# generation counter (app.provider_config_service.get_config_version — bumped by every
# provider_config create/update/delete). /status is polled ~every 30s (I3, NavRail badge);
# re-resolving provider_config (up to 3 queries) and instantiating a provider on every poll
# just to read one boolean is unnecessary once the config hasn't changed. None means "not yet
# computed" so the first poll after a (re)start still resolves fresh.
_supports_vision_cache: tuple[int, bool] | None = None


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
    supports_vision: bool = Field(
        default=False,
        description=(
            "B2-C1: True when the active chat provider reports capabilities().supports_vision. "
            "The frontend uses this to gate the attach-image button. Additive, non-breaking — "
            "defaults False so existing clients without the field read as vision-disabled."
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
                "supports_vision": False,
            }
        }
    }


@router.get(
    "/status",
    response_model=StatusResponse,
    summary="Service health + data_version",
    description=(
        "Returns vault_id, current data_version (monotonic ingest counter), "
        "service started_at, uptime_seconds, and supports_vision (B2-C1). (AC-REST-1, AC-F16dv-3)"
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

    # B2-C1: probe active chat provider for supports_vision capability.
    # Resolved via the normal provider abstraction (I6). Failure → False (safe default).
    # BE-PERF-10: memoized on the provider_config generation counter — skips the up-to-3
    # resolver queries + provider instantiation on every /status poll when nothing changed.
    global _supports_vision_cache  # noqa: PLW0603
    from app.provider_config_service import get_config_version

    current_cfg_version = get_config_version()
    if _supports_vision_cache is not None and _supports_vision_cache[0] == current_cfg_version:
        supports_vision = _supports_vision_cache[1]
    else:
        supports_vision = False
        try:
            from app.ingest.provider import resolve_provider
            from app.provider_config_service import resolve_provider_config

            config_row = await resolve_provider_config("chat", settings.vault_id)
            provider = resolve_provider(config_row)
            supports_vision = bool(provider.capabilities().supports_vision)
        except Exception:  # noqa: BLE001  — ConfigNotFoundError, ImportError, any startup lag
            supports_vision = False
        _supports_vision_cache = (current_cfg_version, supports_vision)

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
        supports_vision=supports_vision,
    )
