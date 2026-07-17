"""Per-domain config router: /config/embedding.

Split out of the monolithic app.routers.config (BE-REFAC-1). Same paths/contract.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.config import settings
from app.config_overrides import (
    effective_bool,
)
from app.schemas.config import (
    EmbeddingConfigResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ── GET /config/embedding ─────────────────────────────────────────────────────


@router.get(
    "/config/embedding",
    response_model=EmbeddingConfigResponse,
    summary="Get current embedding configuration",
    description=(
        "Returns the active embedding config read from environment variables "
        "(EMBEDDING_URL, EMBEDDING_MODEL, EMBEDDING_DIM, EMBEDDINGS_ENABLED). "
        "Read-only — edit .env to change. (I9, ADR-0030)"
    ),
)
async def get_embedding_config() -> EmbeddingConfigResponse:
    """Return current embedding settings including enabled/disabled state (F17 / I9 / ADR-0030)."""
    return EmbeddingConfigResponse(
        embedding_url=settings.embedding_url,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
        embeddings_enabled=effective_bool("embeddings_enabled", settings.embeddings_enabled),
    )
