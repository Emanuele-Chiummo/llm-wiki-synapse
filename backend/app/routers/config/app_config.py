"""Per-domain config router: /config/app key-value overrides (ADR-0053).

Split out of the monolithic app.routers.config (BE-REFAC-1). Same paths/contract.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app import runtime_state
from app.config import settings
from app.config_overrides import (
    ALLOWED_CONFIG_KEYS,
    ORDERED_KEYS,
    clear_override,
    get_effective,
    set_override,
    source_of,
    validate_value,
)
from app.schemas.config import (
    AppConfigListResponse,
    AppConfigPutBody,
    AppConfigSetting,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _build_app_config_response() -> AppConfigListResponse:
    """
    Build the GET /config/app response from the in-process cache (I7 — no DB round-trip).

    Value resolution per key (ADR-0053 §3.1):
      • S7 overview_language: None env-default → serialised as "" (auto sentinel).
      • S9 domain_vocabulary: no env var; env-default is "[]" (empty JSON array — dormant).
      • S13 reclassify_schedule: no env-var baseline; default is "off" (R12-9).
      • S14–S18: loop-bound int keys; env-var baseline from settings (I7).
      • All others: str env-default passed directly.
    """
    env_defaults: dict[str, str] = {
        "pdf_extractor": settings.pdf_extractor,
        "marker_service_url": settings.marker_service_url,
        "marker_timeout_seconds": str(settings.marker_timeout_seconds),
        "cost_alert_threshold_usd": str(settings.cost_alert_threshold_usd),
        "embeddings_enabled": str(settings.embeddings_enabled).lower(),
        "embedding_format": settings.embedding_format,
        "overview_language": settings.overview_language or "",
        "wikilink_enrich_enabled": str(settings.wikilink_enrich_enabled).lower(),
        # S9: domain_vocabulary has no env var; default is dormant empty list (ADR-0054 §2.1)
        "domain_vocabulary": "[]",
        # S10/S11: schedule keys have no env-var baseline; default is "off" (R12-7/A5).
        "lint_schedule": "off",
        "backfill_schedule": "off",
        # S12: schema_review_schedule has no env-var baseline; default is "off" (R12-8).
        "schema_review_schedule": "off",
        # S13: reclassify_schedule has no env-var baseline; default is "off" (R12-9).
        "reclassify_schedule": "off",
        # S14–S18: loop-bound keys — env-var baseline from settings (I7).
        "deep_research_max_iter": str(settings.deep_research_max_iter),
        "deep_research_token_budget": str(settings.deep_research_token_budget),
        "deep_research_max_queries": str(settings.deep_research_max_queries),
        "lint_max_iter": str(settings.lint_max_iter),
        "lint_token_budget": str(settings.lint_token_budget),
        # S19/S20: Image Captioning (v1.5 P3-a) — env-var baseline from settings.
        "vision_captions_enabled": str(settings.vision_captions_enabled).lower(),
        "vision_max_images_per_run": str(settings.vision_max_images_per_run),
        # S21/S22: MinerU cloud PDF (v1.5 P3-d, ADR-0069) — non-secret keys only.
        "mineru_api_url": settings.mineru_api_url,
        "mineru_timeout_seconds": str(settings.mineru_timeout_seconds),
        # S23: web-search provider selector (v1.5 P3-e, ADR-0070) — non-secret; keys are env-only.
        "web_search_provider": settings.web_search_provider,
        # S24: backup_schedule has no env-var baseline; default is "off" (1.9.1 W4, SEC-OPS-2).
        "backup_schedule": "off",
    }

    result: list[AppConfigSetting] = []
    for key in ORDERED_KEYS:
        env_default = env_defaults[key]
        effective = get_effective(key, env_default)
        result.append(
            AppConfigSetting(
                key=key,
                value=effective,
                source=source_of(key),
            )
        )
    return AppConfigListResponse(settings=result)


@router.get(
    "/config/app",
    response_model=AppConfigListResponse,
    summary="List all 13 runtime config overrides with effective value + source (ADR-0053)",
    description=(
        "Returns the 13 migrated settings (S1..S13) in stable order. "
        "Each entry has key, effective value (override wins over env), and source "
        "('override' iff a DB row exists, else 'env'). "
        "All values from in-process cache — no DB round-trip (I7). "
        "Never returns infra/secret keys (allow-list boundary — ADR-0053 §2.2/§2.4). "
        "Auth: SynapseAuthMiddleware (ADR-0052 / ADR-0053 §3 — BearerAuth in OpenAPI)."
    ),
)
async def get_app_config() -> AppConfigListResponse:
    """GET /config/app — list effective values + source for all 13 settings (ADR-0053 §3.1)."""
    return _build_app_config_response()


@router.put(
    "/config/app/{key}",
    status_code=204,
    summary="Upsert one config override (ADR-0053)",
    description=(
        "Sets or updates the DB override for one of the 13 allowed keys. "
        "Returns 204 on success. "
        "Returns 400 {'error':'invalid_key','allowed':[...]} for a non-allowed key. "
        "Returns 422 on value validation failure (per-key rules — ADR-0053 §2.3). "
        "Auth: SynapseAuthMiddleware (ADR-0052). "
        "After write, the in-process cache is refreshed immediately so a subsequent "
        "GET /config/app reflects source='override' (ADR-0053 §3.2)."
    ),
    responses={
        204: {"description": "Override stored; effective value updated in cache."},
        400: {"description": "Key not in allow-list."},
        422: {"description": "Value fails per-key validation rule (ADR-0053 §2.3)."},
    },
)
async def put_app_config(key: str, body: AppConfigPutBody) -> Response:
    """PUT /config/app/{key} — upsert one config override (ADR-0053 §3.2)."""
    if key not in ALLOWED_CONFIG_KEYS:
        import json as _json  # noqa: PLC0415

        return Response(
            content=_json.dumps({"error": "invalid_key", "allowed": sorted(ALLOWED_CONFIG_KEYS)}),
            status_code=400,
            media_type="application/json",
        )

    # S7 "(auto)" sentinel → redirect to DELETE (ADR-0053 §2.3)
    if key == "overview_language" and body.value.strip() == "(auto)":
        async with runtime_state.get_session() as session:
            await clear_override(session, key)
        return Response(status_code=204)

    # Validate value (422 on failure, no write — ADR-0053 §2.3)
    err = validate_value(key, body.value)
    if err is not None:
        raise HTTPException(status_code=422, detail=err)

    async with runtime_state.get_session() as session:
        await set_override(session, key, body.value)

    logger.info("PUT /config/app/%s: source=override (ADR-0053)", key)
    return Response(status_code=204)


@router.delete(
    "/config/app/{key}",
    status_code=204,
    summary="Remove a config override → revert to env default (ADR-0053 §3.3)",
    description=(
        "Deletes the app_config row for *key*, reverting the setting to its env baseline. "
        "Returns 204 (idempotent — no-op if no row existed). "
        "Returns 400 {'error':'invalid_key','allowed':[...]} for a non-allowed key. "
        "Auth: SynapseAuthMiddleware (ADR-0052). "
        "Reset is DELETE, not PUT null: app_config.value is NOT NULL by design (ADR-0053 §3.3). "
        "S7 overview_language: frontend sends DELETE for the '(auto)' choice (§2.3)."
    ),
    responses={
        204: {"description": "Override removed; setting reverts to env default."},
        400: {"description": "Key not in allow-list."},
    },
)
async def delete_app_config(key: str) -> Response:
    """DELETE /config/app/{key} — remove override, revert to env default (ADR-0053 §3.3)."""
    if key not in ALLOWED_CONFIG_KEYS:
        import json as _json  # noqa: PLC0415

        return Response(
            content=_json.dumps({"error": "invalid_key", "allowed": sorted(ALLOWED_CONFIG_KEYS)}),
            status_code=400,
            media_type="application/json",
        )

    async with runtime_state.get_session() as session:
        await clear_override(session, key)

    logger.info("DELETE /config/app/%s: source=env (override removed — ADR-0053 §3.3)", key)
    return Response(status_code=204)
