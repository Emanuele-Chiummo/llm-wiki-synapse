"""
Configuration router package (BE-REFAC-1 split of the former app/routers/config.py).

Aggregates the per-domain sub-routers into a single ``router`` with the SAME paths and
contract as before. Domain modules:
  provider          — /provider/config CRUD (F17)
  provider_test     — /provider/vendors, /provider/test/{connection,function} (W1)
  embedding         — /config/embedding
  mcp               — /mcp/info, /mcp/remote, /mcp/remote-write, /mcp/auth
  import_schedule   — /import-schedule, /import-schedule/run-now (Feature S)
  clip              — /clip/config (ADR-0040)
  web_search        — /web-search/config, /web-search/provider-keys (ADR-0041/0071)
  cli_auth          — /provider/cli-auth (ADR-0043)
  app_config        — /config/app (ADR-0053)
  api_tokens        — /config/api-tokens CRUD (PF-AUTH-1, 1.9.4 W4)

Shared Pydantic DTOs live in app.schemas.config.

Sub-routers are included in original first-appearance order to keep the generated
OpenAPI as close as possible to the pre-split schema (path/schema set unchanged).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.routers.config import (
    api_tokens,
    app_config,
    cli_auth,
    clip,
    embedding,
    import_schedule,
    mcp,
    provider,
    provider_test,
    web_search,
)

router = APIRouter()
router.include_router(cli_auth.router)
router.include_router(provider.router)
router.include_router(provider_test.router)
router.include_router(embedding.router)
router.include_router(mcp.router)
router.include_router(import_schedule.router)
router.include_router(clip.router)
router.include_router(web_search.router)
router.include_router(app_config.router)
router.include_router(api_tokens.router)

# ── Backward-compatible re-exports (import seam preserved for one release) ─────
# app.main re-exports EmbeddingConfigResponse + get_embedding_config from here; the
# test-suite imports _resolve_probe_key and patches provider_test._one_shot_chat.
from app.routers.config.embedding import get_embedding_config  # noqa: E402,F401
from app.routers.config.provider_test import (  # noqa: E402,F401
    _one_shot_chat,
    _resolve_probe_key,
)
from app.schemas.config import EmbeddingConfigResponse  # noqa: E402,F401

__all__ = [
    "EmbeddingConfigResponse",
    "get_embedding_config",
    "router",
]
