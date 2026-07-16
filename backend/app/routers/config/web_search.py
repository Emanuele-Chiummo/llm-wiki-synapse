"""Per-domain config router: /web-search/config + /web-search/provider-keys (ADR-0041/0071).

Split out of the monolithic app.routers.config (BE-REFAC-1). Same paths/contract.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app import runtime_state, secrets_crypto
from app.config import settings
from app.models import VaultState
from app.schemas.config import (
    WebSearchConfigRequest,
    WebSearchConfigResponse,
    WebSearchConfigStateResponse,
    WebSearchProviderKeyRequest,
    WebSearchProviderKeysResponse,
    WebSearchProviderKeyState,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/web-search/config",
    response_model=WebSearchConfigResponse,
    summary="Read-only SearXNG web-search posture (ADR-0041)",
    description=(
        "Returns the current SearXNG configuration: configured flag, resolved URL, "
        "categories, max_queries, and source (db|env|none). "
        "DB value wins over env when set (ADR-0041 §2.2). "
        "The URL is NOT a secret and IS returned in full. "
        "F10-web-search-config (ADR-0041)."
    ),
)
async def get_web_search_config() -> WebSearchConfigResponse:
    """
    GET /web-search/config — read-only SearXNG web-search posture (ADR-0041).

    All values derived from the in-process runtime_state.web_search_config_cache (loaded from
    vault_state at startup and refreshed on PUT /web-search/config writes).
    No DB query on each GET. The URL IS returned (not a secret — ADR-0041 §2.1).
    """
    return WebSearchConfigResponse(
        configured=runtime_state.web_search_config_cache.configured(),
        url=runtime_state.web_search_config_cache.resolved_url(),
        categories=runtime_state.web_search_config_cache.resolved_categories(),
        max_queries=runtime_state.web_search_config_cache.resolved_max_queries(),
        source=runtime_state.web_search_config_cache.url_source(),
    )


@router.put(
    "/web-search/config",
    response_model=WebSearchConfigStateResponse,
    summary="Set or clear the SearXNG web-search configuration (ADR-0041)",
    description=(
        "ADR-0041 §2.4 — runtime SearXNG configuration. "
        "set_url: set searxng_url_db (validates http/https; DB wins over SEARXNG_URL env). "
        "set_categories: set searxng_categories_db (comma-separated; empty string clears). "
        "set_max_queries: set searxng_max_queries_db (1–50; DB wins over env). "
        "clear=true: clear ALL three DB columns (falls back to env / code defaults). "
        "I9 invariant: SearXNG is the ONLY web-search backend. "
        "No provider field accepted — any attempt to configure a non-SearXNG provider is rejected. "
        "F10-web-search-config (ADR-0041)."
    ),
)
async def put_web_search_config(body: WebSearchConfigRequest) -> WebSearchConfigStateResponse:
    """
    PUT /web-search/config — runtime SearXNG configuration (ADR-0041 §2.4).

    Applies changes in this order:
      1. clear=true (if set) → set all three DB columns to NULL.
      2. set_url (if set) → validate + persist searxng_url_db.
      3. set_categories (if set) → persist searxng_categories_db (empty = NULL).
      4. set_max_queries (if set) → persist searxng_max_queries_db.
      5. Refresh in-process runtime_state.web_search_config_cache.
      6. Return WebSearchConfigStateResponse.

    I9: SearXNG is the ONLY web-search backend. No provider routing here.
    """
    import re

    def _validate_url(url: str) -> str:
        """Validate that the URL is a plausible http(s) URL."""
        url = url.strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid SearXNG URL {url!r}: must start with http:// or https://. "
                    "SearXNG is the ONLY web-search backend (I9 — ADR-0041)."
                ),
            )
        return url

    async with runtime_state.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            raise HTTPException(status_code=500, detail="vault_state row not found")

        # 1. clear=true → null all three DB columns
        if body.clear:
            state.searxng_url_db = None
            state.searxng_categories_db = None
            state.searxng_max_queries_db = None

        # 2. set_url (if provided)
        if body.set_url is not None:
            state.searxng_url_db = _validate_url(body.set_url)

        # 3. set_categories (if provided)
        if body.set_categories is not None:
            # Empty string → NULL (falls back to default)
            stripped = body.set_categories.strip()
            state.searxng_categories_db = stripped if stripped else None

        # 4. set_max_queries (if provided)
        if body.set_max_queries is not None:
            state.searxng_max_queries_db = body.set_max_queries

        final_url_db: str | None = state.searxng_url_db
        final_categories_db: str | None = state.searxng_categories_db
        final_max_queries_db: int | None = state.searxng_max_queries_db

    # 5. Refresh in-process cache (outside session — DB write committed).
    await runtime_state.web_search_config_cache.set_url_db(final_url_db)
    await runtime_state.web_search_config_cache.set_categories_db(final_categories_db)
    await runtime_state.web_search_config_cache.set_max_queries_db(final_max_queries_db)

    logger.info(
        "PUT /web-search/config: url_source=%s categories_source=%s "
        "max_queries_source=%s configured=%s (ADR-0041)",
        runtime_state.web_search_config_cache.url_source(),
        runtime_state.web_search_config_cache.categories_source(),
        runtime_state.web_search_config_cache.max_queries_source(),
        runtime_state.web_search_config_cache.configured(),
    )

    # 6. Return posture.
    return WebSearchConfigStateResponse(
        configured=runtime_state.web_search_config_cache.configured(),
        url=runtime_state.web_search_config_cache.resolved_url(),
        categories=runtime_state.web_search_config_cache.resolved_categories(),
        max_queries=runtime_state.web_search_config_cache.resolved_max_queries(),
        source=runtime_state.web_search_config_cache.url_source(),
    )


@router.get(
    "/web-search/provider-keys",
    response_model=WebSearchProviderKeysResponse,
    summary="Masked posture of cloud web-search provider API keys (P3-e)",
    description=(
        "Read-only masked posture for the opt-in cloud web-search providers. NEVER returns the "
        "key value — only whether one is set and its source (db | env | none). Keys are stored "
        "Fernet-encrypted at rest and require SYNAPSE_SECRET_KEY to set via the UI (ADR-0071)."
    ),
)
async def get_web_search_provider_keys() -> WebSearchProviderKeysResponse:
    """GET /web-search/provider-keys — masked posture (ADR-0071)."""
    from app.ops.web_search.keys import get_key_posture

    posture = get_key_posture()
    return WebSearchProviderKeysResponse(
        secrets_available=secrets_crypto.is_configured(),
        providers={
            p: WebSearchProviderKeyState(configured=bool(v["configured"]), source=str(v["source"]))
            for p, v in posture.items()
        },
    )


@router.put(
    "/web-search/provider-keys",
    response_model=WebSearchProviderKeysResponse,
    summary="Set or clear a cloud web-search provider API key (P3-e)",
    description=(
        "Store (encrypted at rest) or clear one cloud provider's API key. Setting a key requires "
        "SYNAPSE_SECRET_KEY (400 when absent) — mirrors the CLI-auth token contract (ADR-0043/W7). "
        "The stored key wins over the env `{PROVIDER}_API_KEY` fallback. The plaintext is never "
        "logged or returned. ADR-0071."
    ),
    responses={
        200: {"description": "Key stored/cleared; returns the refreshed masked posture"},
        400: {"description": "SYNAPSE_SECRET_KEY not set (cannot encrypt) or invalid provider/key"},
    },
)
async def put_web_search_provider_key(
    body: WebSearchProviderKeyRequest,
) -> WebSearchProviderKeysResponse:
    """PUT /web-search/provider-keys — set/clear one provider's key (ADR-0071)."""
    from app.ops.web_search.keys import (
        CLOUD_KEY_PROVIDERS,
        clear_web_search_api_key,
        set_web_search_api_key,
    )

    if body.provider not in CLOUD_KEY_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"provider must be one of {sorted(CLOUD_KEY_PROVIDERS)}, got {body.provider!r}",
        )
    if body.clear:
        await clear_web_search_api_key(body.provider)
    else:
        if not body.key or not body.key.strip():
            raise HTTPException(status_code=400, detail="key must be a non-empty string")
        if not secrets_crypto.is_configured():
            raise HTTPException(
                status_code=400,
                detail=(
                    "SYNAPSE_SECRET_KEY is not set — cannot encrypt the key at rest. Set it in the "
                    "server environment, or provide the key via the {PROVIDER}_API_KEY env var."
                ),
            )
        try:
            await set_web_search_api_key(body.provider, body.key)
        except secrets_crypto.SecretsNotConfiguredError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return await get_web_search_provider_keys()
