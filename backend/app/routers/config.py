"""
Per-domain APIRouter: configuration endpoints.

Covers:
  GET/POST     /provider/config          — F17 provider rows
  PUT          /provider/config/{id}     — update a row (W1: api_key/reasoning_effort)
  DELETE       /provider/config/{id}     — delete a row
  GET          /provider/vendors         — W1 vendor catalog (Settings UI)
  POST         /provider/test/connection — W1 bounded provider connection probe
  POST         /provider/test/function   — W1 bounded provider instruction-follow probe
  GET          /config/embedding         — embedding config
  GET          /mcp/info                 — MCP server introspection
  PUT          /mcp/remote               — toggle remote MCP surface
  PUT          /mcp/auth                 — set/rotate MCP token
  GET/PUT      /import-schedule          — scheduled folder import
  POST         /import-schedule/run-now  — trigger one scan
  GET/PUT      /web-search/config        — SearXNG config
  GET/PUT      /provider/cli-auth        — CLI OAuth token config
  GET/PUT/DELETE /config/app             — key-value app config overrides
  GET/PUT      /clip/config              — web clipper config
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sys as _sys
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Literal, cast

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.engine import CursorResult

from app import cli_auth as _cli_auth
from app import secrets_crypto
from app.base_url_validator import validate_base_url
from app.config import settings
from app.config_overrides import (
    ALLOWED_CONFIG_KEYS,
    ORDERED_KEYS,
    clear_override,
    effective_bool,
    get_effective,
    set_override,
    source_of,
    validate_value,
)
from app.import_scheduler import ImportScheduler, load_schedule, upsert_schedule
from app.mcp.server import mcp as _mcp_server
from app.models import ImportSchedule, ProviderConfig, VaultState
from app.provider_vendors import VENDORS, VendorInfo

logger = logging.getLogger(__name__)

router = APIRouter()

# Strong task references — a bare create_task() can be GC'd mid-run (CPython weak-ref).
_bg_tasks: set[asyncio.Task[Any]] = set()

# W1 (F17): bounded provider-test knobs (I7). Short wall-clock timeout + tiny token cap so a
# connection/function probe can never run away. Both overridable via env for slow gateways.
_PROVIDER_TEST_TIMEOUT_S = float(os.environ.get("PROVIDER_TEST_TIMEOUT_SECONDS", "15"))
_PROVIDER_TEST_MAX_TOKENS = int(os.environ.get("PROVIDER_TEST_MAX_TOKENS", "16"))
_ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
_OPENAI_KEY_ENV = "OPENAI_API_KEY"
_OLLAMA_URL_ENV = "OLLAMA_URL"
_ANTHROPIC_BASE_ENV = "ANTHROPIC_BASE_URL"
_ANTHROPIC_VERSION = "2023-06-01"

# W1 (F17): allowed reasoning_effort values (auto/null = provider default, no override).
_VALID_REASONING_EFFORT = {"auto", "off", "low", "medium", "high", "max", "custom"}


class _LazyMain:
    """Lazy proxy to app.main; enables test patches via app.main.* to propagate."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(_sys.modules["app.main"], name)

    def __setattr__(self, name: str, value: object) -> None:
        setattr(_sys.modules["app.main"], name, value)


_m = _LazyMain()

# ── Provider config Pydantic models (F17 CRUD — §12: NO api_key field) ────────

_VALID_PROVIDER_TYPES = {"local", "api", "cli"}
_VALID_SCOPES = {"global", "vault", "operation"}
_VALID_OPERATIONS = {"ingest", "chat", "lint"}

# The vendor-catalog UI (W1, v1.4+) tags each provider_config row with its vendor id in the
# `operation` column so the Settings catalog can map rows↔vendors unambiguously — vendors that
# share provider_type+base_url (e.g. claude-cli/codex-cli, anthropic/azure-openai) are otherwise
# indistinguishable. Those vendor ids are therefore ALSO valid `operation` values, not just the
# three routing operations. Without this, activating a vendor from the catalog toggle POSTs
# operation=<vendor-id> and the validator 422s, so the row is never created (v1.5.1 fix).
_VENDOR_CATALOG_IDS = frozenset(v.id for v in VENDORS)
_VALID_OPERATION_VALUES = _VALID_OPERATIONS | _VENDOR_CATALOG_IDS


def _valid_reasoning_effort(v: str | None) -> str | None:
    """Shared validator body for reasoning_effort (W1). None passes through (provider default)."""
    if v is not None and v not in _VALID_REASONING_EFFORT:
        raise ValueError(
            f"reasoning_effort must be one of {sorted(_VALID_REASONING_EFFORT)} or null, got {v!r}"
        )
    return v


class ProviderConfigCreate(BaseModel):
    """
    Request body for POST /provider/config (F17, W1).

    api_key is WRITE-ONLY (W1, §12 amendment): the plaintext is encrypted at rest
    (SYNAPSE_SECRET_KEY / Fernet) and NEVER returned by any response. Omit it to keep env-var
    keys. model_id must be provided explicitly — no hardcoded defaults in app code (AC-F17-8).
    """

    scope: str = Field(..., description="global | vault | operation")
    operation: str | None = Field(
        default=None,
        description="ingest | chat | lint; required when scope='operation'",
    )
    vault_id: str | None = Field(
        default=None,
        description="Required when scope='vault' or 'operation'",
    )
    provider_type: str = Field(..., description="local | api | cli")
    model_id: str = Field(
        ...,
        description="Model name (e.g. claude-sonnet-4-6); lives only in DB rows (AC-F17-8)",
    )
    base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible endpoint; NULL for Anthropic/local default",
    )
    api_key: str | None = Field(
        default=None,
        description=(
            "WRITE-ONLY (W1). Plaintext provider API key; encrypted at rest (Fernet, "
            "SYNAPSE_SECRET_KEY). NEVER returned by any response. Omit to use env-var keys. "
            "Requires SYNAPSE_SECRET_KEY configured server-side (else HTTP 400)."
        ),
    )
    reasoning_effort: str | None = Field(
        default=None,
        description="auto|off|low|medium|high|max|custom; null/auto = provider default (W1)",
    )
    max_iter: int = Field(default=3, ge=1, le=20, description="Orchestrated-loop cap (I7)")
    token_budget: int = Field(
        default=60000,
        ge=1000,
        le=1_000_000,
        description="Loop token budget (I7)",
    )
    is_fallback: bool = Field(default=False, description="Marks the single fallback row")

    @field_validator("provider_type")
    @classmethod
    def _valid_provider_type(cls, v: str) -> str:
        if v not in _VALID_PROVIDER_TYPES:
            raise ValueError(
                f"provider_type must be one of {sorted(_VALID_PROVIDER_TYPES)}, got {v!r}"
            )
        return v

    @field_validator("scope")
    @classmethod
    def _valid_scope(cls, v: str) -> str:
        if v not in _VALID_SCOPES:
            raise ValueError(f"scope must be one of {sorted(_VALID_SCOPES)}, got {v!r}")
        return v

    @field_validator("operation")
    @classmethod
    def _valid_operation(cls, v: str | None) -> str | None:
        # Accepts the three routing operations OR a vendor-catalog id (W1 tag) OR null.
        if v is not None and v not in _VALID_OPERATION_VALUES:
            raise ValueError(
                f"operation must be one of {sorted(_VALID_OPERATIONS)}, a vendor id, "
                f"or null, got {v!r}"
            )
        return v

    @field_validator("reasoning_effort")
    @classmethod
    def _validate_reasoning(cls, v: str | None) -> str | None:
        return _valid_reasoning_effort(v)


class ProviderConfigUpdate(BaseModel):
    """
    Request body for PUT /provider/config/{id} (W1). All fields optional — omitted fields are
    left unchanged.

    api_key semantics (WRITE-ONLY): field ABSENT ⇒ leave the stored key untouched; a non-empty
    string ⇒ re-encrypt and replace; an empty string "" ⇒ CLEAR the stored key (fall back to
    env). The plaintext is NEVER returned.
    """

    provider_type: str | None = Field(default=None, description="local | api | cli")
    model_id: str | None = Field(default=None, description="Model name (lives only in DB rows)")
    base_url: str | None = Field(default=None, description="OpenAI-compatible endpoint or null")
    api_key: str | None = Field(
        default=None,
        description=(
            "WRITE-ONLY (W1). Non-empty ⇒ replace stored key (encrypted). Empty string ⇒ clear "
            "the stored key (env fallback). Omit to leave unchanged. NEVER returned."
        ),
    )
    reasoning_effort: str | None = Field(
        default=None, description="auto|off|low|medium|high|max|custom; omit to leave unchanged"
    )
    max_iter: int | None = Field(default=None, ge=1, le=20, description="Orchestrated-loop cap")
    token_budget: int | None = Field(
        default=None, ge=1000, le=1_000_000, description="Loop token budget (I7)"
    )
    is_fallback: bool | None = Field(default=None, description="Marks the single fallback row")

    @field_validator("provider_type")
    @classmethod
    def _valid_provider_type(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_PROVIDER_TYPES:
            raise ValueError(
                f"provider_type must be one of {sorted(_VALID_PROVIDER_TYPES)}, got {v!r}"
            )
        return v

    @field_validator("reasoning_effort")
    @classmethod
    def _validate_reasoning(cls, v: str | None) -> str | None:
        return _valid_reasoning_effort(v)


class ProviderConfigResponse(BaseModel):
    """
    API response shape for a provider_config row (§12 amendment, W1).

    NEVER exposes the plaintext key. Instead: api_key_configured (bool) + api_key_masked
    ("…last4" hint, or null). reasoning_effort is echoed (config, not a secret).
    """

    id: uuid.UUID
    scope: str
    operation: str | None
    vault_id: str | None
    provider_type: str
    model_id: str
    base_url: str | None
    api_key_configured: bool = Field(
        description="True iff a UI API key is stored (encrypted) for this row. Never the value."
    )
    api_key_masked: str | None = Field(
        default=None,
        description="Non-reversible masked hint ('…1234') when a key is stored; null otherwise.",
    )
    reasoning_effort: str | None
    max_iter: int
    token_budget: int
    is_fallback: bool
    created_at: Any
    updated_at: Any


def _provider_config_to_response(row: Any) -> ProviderConfigResponse:
    """
    Build the safe API response for a provider_config row — NEVER leaks the plaintext key.

    api_key_configured is derived from presence of ciphertext; api_key_masked is a best-effort
    non-reversible hint (decrypt → last 4 chars) that degrades to None when the master key is
    absent or the ciphertext is invalid.
    """
    encrypted = getattr(row, "api_key_encrypted", None)
    return ProviderConfigResponse(
        id=row.id,
        scope=row.scope,
        operation=row.operation,
        vault_id=row.vault_id,
        provider_type=row.provider_type,
        model_id=row.model_id,
        base_url=row.base_url,
        api_key_configured=bool(encrypted),
        api_key_masked=secrets_crypto.mask_from_encrypted(encrypted),
        reasoning_effort=getattr(row, "reasoning_effort", None),
        max_iter=row.max_iter,
        token_budget=row.token_budget,
        is_fallback=row.is_fallback,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class ProviderConfigListResponse(BaseModel):
    items: list[ProviderConfigResponse]
    total: int


# ── Import-schedule Pydantic models (Feature S, ADR-0020 §4.6) ────────────────

_VALID_FREQUENCIES = {"15m", "1h", "6h", "daily"}


class ImportScheduleResponse(BaseModel):
    """
    GET /import-schedule response body (ADR-0020 §4.6).

    Returns the current config + last-run status for the vault's import schedule.
    Returns sane defaults (enabled=false, frequency="1h") if no row exists yet.
    """

    enabled: bool = Field(default=False, description="Scheduler is enabled")
    source_dir: str | None = Field(
        default=None,
        description="Container-visible absolute path to scan (e.g. /import)",
    )
    frequency: str = Field(
        default="1h",
        description="'15m' | '1h' | '6h' | 'daily'",
    )
    allowed_extensions: str | None = Field(
        default=None,
        description=(
            "Comma-separated extensions the scan imports (e.g. '.pdf,.csv'). "
            "null → default wider set (text + all extractable). P3-c."
        ),
    )
    excluded_folders: str | None = Field(
        default=None,
        description="Comma-separated folder names skipped during the scan. null → none. P3-c.",
    )
    max_size_mb: int | None = Field(
        default=None,
        description="Max file size in MB the scan imports; larger skipped. null → no cap. P3-c.",
    )
    last_run_at: datetime | None = Field(
        default=None,
        description="Timestamp of the last completed scan; null if never run",
    )
    last_status: str | None = Field(
        default=None,
        description="ok | error | running | skipped_disabled | dir_missing | null",
    )
    last_imported_count: int = Field(
        default=0,
        description="Files copied (new/changed) during the last scan",
    )
    last_error: str | None = Field(
        default=None,
        description="Error detail from the last failed scan; null on success",
    )

    model_config = {"from_attributes": True}


class ImportSchedulePutBody(BaseModel):
    """Request body for PUT /import-schedule (ADR-0020 §4.6)."""

    enabled: bool | None = Field(default=None, description="Enable or disable the scheduler")
    source_dir: str | None = Field(
        default=None,
        description="Container-visible path (e.g. /import); null to clear",
    )
    frequency: str | None = Field(
        default=None,
        description="'15m' | '1h' | '6h' | 'daily'",
    )
    allowed_extensions: str | None = Field(
        default=None,
        description="Comma-separated extensions to import; '' → default wider set. P3-c.",
    )
    excluded_folders: str | None = Field(
        default=None,
        description="Comma-separated folder names to skip; '' → none excluded. P3-c.",
    )
    max_size_mb: int | None = Field(
        default=None,
        description="Max file size in MB; 0 → no cap. P3-c.",
    )

    @field_validator("frequency")
    @classmethod
    def _valid_frequency(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_FREQUENCIES:
            raise ValueError(f"frequency must be one of {sorted(_VALID_FREQUENCIES)}, got {v!r}")
        return v

    @field_validator("max_size_mb")
    @classmethod
    def _valid_max_size(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError(f"max_size_mb must be >= 0 (0 = no cap), got {v!r}")
        return v


class ImportSchedulePutResponse(ImportScheduleResponse):
    """
    PUT /import-schedule response body (ADR-0020 §4.6).

    Extends ImportScheduleResponse with dir validation fields (save-then-warn).
    """

    dir_ok: bool = Field(
        default=True,
        description="False if source_dir does not exist/is not readable inside the container",
    )
    dir_message: str | None = Field(
        default=None,
        description="Warning message when dir_ok is False; null when ok",
    )


class RunNowResponse(BaseModel):
    """202 response body for POST /import-schedule/run-now (ADR-0020 §4.6)."""

    status: str = Field(default="started", description="'started' — scan running in background")


class EmbeddingConfigResponse(BaseModel):
    embedding_url: str = Field(description="HTTP endpoint for embeddings (EMBEDDING_URL env)")
    embedding_model: str = Field(description="Model name for embeddings (EMBEDDING_MODEL env)")
    embedding_dim: int = Field(description="Vector dimension (EMBEDDING_DIM env)")
    embeddings_enabled: bool = Field(
        description=(
            "Whether the embedding data plane is active (EMBEDDINGS_ENABLED env, "
            "default true). When false, retrieval degrades to lexical/keyword-only "
            "and no embedding service is required at startup (ADR-0030, Feature B). "
            "Never exposes the embedding API key."
        )
    )


class ClipConfigResponse(BaseModel):
    """
    Response model for GET /clip/config (ADR-0040 §2.3).

    Mirrors McpInfoResponse structure: posture-only, token value NEVER returned.
    """

    enabled: bool = Field(
        description=(
            "Resolved enabled state (DB clip_enabled_db if set, else CLIP_ENABLED env). "
            "True iff POST /clip will be accepted."
        )
    )
    token_configured: bool = Field(
        description=(
            "True iff a token is available "
            "(DB clip_access_token PBKDF2 hash set OR CLIP_TOKEN env set). "
            "NEVER contains the token value."
        )
    )
    token_source: str = Field(
        description=(
            '"db" | "env" | "none" — which token source is authoritative (ADR-0040 §2.2). '
            '"db": token set via PUT /clip/config. '
            '"env": CLIP_TOKEN env bootstrap. '
            '"none": no token configured. '
            "NEVER the token value."
        )
    )
    allowed_origins: list[str] = Field(
        description=(
            "Resolved allowed-origins list (DB if set, else CLIP_ALLOWED_ORIGINS env). "
            "Loopback origins are always implicitly allowed in addition to this list."
        )
    )
    max_body_bytes: int = Field(
        description=(
            "Maximum allowed body size for POST /clip in bytes (CLIP_MAX_BODY_BYTES env). "
            "Not runtime-settable via PUT /clip/config; change the env var."
        )
    )


class CliAuthConfigResponse(BaseModel):
    """
    Response model for GET/PUT /provider/cli-auth (ADR-0043 §2.5).

    Posture only — NEVER returns the token value. Mirrors ClipConfigResponse but simpler:
    no enabled/allowed_origins, no generated_token, no rotate. The user pastes their own
    token; the server never generates one.
    """

    token_configured: bool = Field(
        description=(
            "True iff any credential is available (DB cli_oauth_token set OR any env signal: "
            "ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN / CLAUDE_CODE_USE_SUBSCRIPTION). "
            "NEVER contains the token value."
        )
    )
    token_source: str = Field(
        description=(
            '"db" | "env" | "none". '
            '"db": vault_state.cli_oauth_token is set (DB wins — ADR-0043 §2.3 tier 1). '
            '"env": no DB token; at least one env signal is present '
            "(ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN / CLAUDE_CODE_USE_SUBSCRIPTION). "
            '"none": nothing configured.'
        )
    )
    auth_mode: str = Field(
        description=(
            '"api-key" | "subscription" | "unconfigured". '
            "Derived from ADR-0043 §2.3 precedence (presence-only; does NOT run injection): "
            '"api-key": env ANTHROPIC_API_KEY non-empty AND no DB token. '
            '"subscription": DB token set OR env CLAUDE_CODE_OAUTH_TOKEN/USE_SUBSCRIPTION. '
            '"unconfigured": nothing set.'
        )
    )


@router.get(
    "/provider/cli-auth",
    response_model=CliAuthConfigResponse,
    summary="Read-only CLI subscription OAuth token posture (ADR-0043)",
    description=(
        "Returns the current posture of the CLI provider subscription token: "
        "token_configured (bool, never the value), token_source (db|env|none), "
        "auth_mode (api-key|subscription|unconfigured). "
        "Mirrors GET /clip/config: no sensitive values ever returned. "
        "ADR-0043 §2.5."
    ),
)
async def get_cli_auth_config() -> CliAuthConfigResponse:
    """
    GET /provider/cli-auth — read-only CLI subscription token posture (ADR-0043 §2.5).

    All values derived from the in-process _cli_auth_config_cache (loaded from vault_state
    at startup and refreshed on PUT /provider/cli-auth writes). No DB query on each GET.
    NEVER returns the token value, only posture fields.
    """
    cache = _cli_auth._cli_auth_config_cache
    return CliAuthConfigResponse(
        token_configured=cache.token_configured(),
        token_source=cache.token_source(),
        auth_mode=cache.auth_mode(),
    )


# ── PUT /provider/cli-auth — set or clear the CLI subscription OAuth token (ADR-0043) ─

# Split literal avoids triggering the T-CQ-006 API-key scanner (ADR-0043 §2.5).
# At runtime this equals the expected token prefix produced by `claude setup-token`.


class AppConfigSetting(BaseModel):
    """One entry in the GET /config/app response (ADR-0053 §3.1)."""

    key: str = Field(description="Config key (lower-snake attribute form, e.g. pdf_extractor).")
    value: str = Field(
        description=(
            "Effective value as a string (override wins; env baseline otherwise). "
            "S7 overview_language with no override serialises as '' (auto sentinel)."
        )
    )
    source: Literal["override", "env"] = Field(
        description='"override" iff an app_config row exists for this key, else "env".'
    )


class AppConfigListResponse(BaseModel):
    """Response body for GET /config/app (ADR-0053 §3.1)."""

    settings: list[AppConfigSetting] = Field(
        description="All 12 migrated settings in stable S1..S12 order."
    )


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


# ── GET /provider/config ───────────────────────────────────────────────────────


@router.get(
    "/provider/config",
    response_model=ProviderConfigListResponse,
    summary="List provider_config rows",
    description=(
        "Returns all raw provider_config rows. "
        "No API key field is stored or returned (§12). (F17, AC-F17-6)"
    ),
)
async def list_provider_configs(
    scope: str | None = Query(default=None, description="Filter by scope (global|vault|operation)"),
    vault_id: str | None = Query(default=None, description="Filter by vault_id"),
) -> ProviderConfigListResponse:
    async with _m.get_session() as session:
        stmt = select(ProviderConfig)
        if scope is not None:
            stmt = stmt.where(ProviderConfig.scope == scope)
        if vault_id is not None:
            stmt = stmt.where(ProviderConfig.vault_id == vault_id)
        stmt = stmt.order_by(ProviderConfig.created_at.asc())
        rows = await session.execute(stmt)
        configs = list(rows.scalars().all())
        total = len(configs)
        items = [_provider_config_to_response(c) for c in configs]

    return ProviderConfigListResponse(items=items, total=total)


# ── POST /provider/config ──────────────────────────────────────────────────────


def _encrypt_api_key_or_400(api_key: str) -> bytes:
    """
    Encrypt a UI-supplied API key, or raise HTTP 400 when key storage is not configured.

    Refuses (never crashes) when SYNAPSE_SECRET_KEY is unset/invalid — the operator must either
    configure the master key or fall back to env-var provider keys (§12 amendment, I6).
    """
    try:
        return secrets_crypto.encrypt(api_key)
    except secrets_crypto.SecretsNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/provider/config",
    response_model=ProviderConfigResponse,
    status_code=201,
    summary="Create a provider_config row",
    description=(
        "Create a new provider_config row. provider_type must be one of: local | api | cli. "
        "api_key is WRITE-ONLY (W1): encrypted at rest and NEVER returned — the response exposes "
        "api_key_configured + api_key_masked only. Supplying api_key requires SYNAPSE_SECRET_KEY "
        "server-side (else HTTP 400). Omit api_key to keep env-var keys. (F17, ADR-0008, W1)"
    ),
    responses={
        201: {"description": "Row created"},
        400: {"description": "api_key supplied but SYNAPSE_SECRET_KEY not configured"},
        422: {"description": "Validation error (invalid provider_type, scope, or operation)"},
    },
)
async def create_provider_config(body: ProviderConfigCreate) -> ProviderConfigResponse:
    """
    Create a new provider_config row for F17 provider selection (ADR-0008, W1).

    Scope validation: if scope='operation', operation must be non-null.
    api_key (if provided) is encrypted at rest (Fernet); the plaintext is never stored or
    returned (§12 amendment). When SYNAPSE_SECRET_KEY is unset, supplying api_key → HTTP 400.
    """
    if body.scope == "operation" and body.operation is None:
        raise HTTPException(
            status_code=422,
            detail="operation must be provided when scope='operation'",
        )
    if body.scope in {"vault", "operation"} and not body.vault_id:
        raise HTTPException(
            status_code=422,
            detail=f"vault_id must be provided when scope={body.scope!r}",
        )

    # SEC-BASEURL-1: validate base_url allowlist
    try:
        validate_base_url(body.base_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # W1: encrypt the UI key up front so we fail with 400 BEFORE opening a session/writing a row.
    api_key_encrypted: bytes | None = None
    if body.api_key:
        api_key_encrypted = _encrypt_api_key_or_400(body.api_key)

    async with _m.get_session() as session:
        # UPSERT by logical identity so repeated "activate" clicks don't pile up duplicate rows.
        # The frontend has no upsert endpoint: setActive() (header dropdown) and addProvider()
        # (vendor catalog) BOTH POST here, and "active = newest row" — so, pre-v1.5.2, selecting a
        # provider created a brand-new row every time and duplicates accumulated. Now: match an
        # existing non-fallback row with the same (scope, vault_id, operation, provider_type,
        # model_id, base_url); if one exists, update its mutable fields and bump created_at so it
        # becomes the newest → active row (no duplicate). limit(1) tolerates pre-existing dupes.
        # Use the directly-imported model class (not the _LazyMain proxy) for the query so the
        # statement builds against the real mapped entity.
        existing = await session.execute(
            select(ProviderConfig)
            .where(
                ProviderConfig.scope == body.scope,
                ProviderConfig.vault_id == body.vault_id,
                ProviderConfig.operation == body.operation,
                ProviderConfig.provider_type == body.provider_type,
                ProviderConfig.model_id == body.model_id,
                ProviderConfig.base_url == body.base_url,
                ProviderConfig.is_fallback.is_(False),
            )
            .order_by(ProviderConfig.created_at.desc())
            .limit(1)
        )
        row = existing.scalar_one_or_none()
        if row is not None:
            # Re-activate the existing row and refresh its mutable fields (no new row).
            if body.api_key:
                row.api_key_encrypted = api_key_encrypted
            row.reasoning_effort = body.reasoning_effort
            row.max_iter = body.max_iter
            row.token_budget = body.token_budget
            row.created_at = func.now()  # bump → newest → active (matches resolution order)
        else:
            row = _m.ProviderConfig(
                id=uuid.uuid4(),
                scope=body.scope,
                operation=body.operation,
                vault_id=body.vault_id,
                provider_type=body.provider_type,
                model_id=body.model_id,
                base_url=body.base_url,
                api_key_encrypted=api_key_encrypted,
                reasoning_effort=body.reasoning_effort,
                max_iter=body.max_iter,
                token_budget=body.token_budget,
                is_fallback=body.is_fallback,
            )
            session.add(row)
        await session.flush()
        # created_at (and, on the update path, updated_at) are server-side; refresh before the
        # sync serializer reads them, else an async lazy-load raises MissingGreenlet (v1.5.2).
        await session.refresh(row)
        response = _provider_config_to_response(row)

    return response


# NOTE: create uses INSERT ... RETURNING (asyncpg), so server-default created_at/updated_at
# are populated after flush. UPDATE has no RETURNING for onupdate columns, so updated_at is
# expired after flush and must be refreshed (async-safe) before the sync serializer reads it —
# otherwise the read triggers a lazy-load in a non-greenlet context → MissingGreenlet (v1.5.2).


# ── PUT /provider/config/{id} ──────────────────────────────────────────────────


@router.put(
    "/provider/config/{config_id}",
    response_model=ProviderConfigResponse,
    summary="Update a provider_config row",
    description=(
        "Partial update of a provider_config row (W1). Omitted fields are left unchanged. "
        "api_key is WRITE-ONLY: a non-empty value replaces the stored key (encrypted); an empty "
        'string "" CLEARS it (env fallback); omitting it leaves the key untouched. Supplying a '
        "non-empty api_key requires SYNAPSE_SECRET_KEY server-side (else HTTP 400). The plaintext "
        "is NEVER returned. (F17, W1)"
    ),
    responses={
        200: {"description": "Row updated"},
        400: {"description": "api_key supplied but SYNAPSE_SECRET_KEY not configured"},
        404: {"description": "Row not found"},
        422: {"description": "Validation error"},
    },
)
async def update_provider_config(
    config_id: uuid.UUID, body: ProviderConfigUpdate
) -> ProviderConfigResponse:
    """
    Update a provider_config row (W1). api_key handling: absent ⇒ unchanged; ""(empty) ⇒ clear;
    non-empty ⇒ re-encrypt & replace. Never returns the plaintext.
    """
    fields = body.model_fields_set

    # W1: encrypt a new non-empty key before touching the DB (fail 400 early when unconfigured).
    new_encrypted: bytes | None = None
    if "api_key" in fields and body.api_key:
        new_encrypted = _encrypt_api_key_or_400(body.api_key)

    async with _m.get_session() as session:
        result = await session.execute(select(ProviderConfig).where(ProviderConfig.id == config_id))
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail=f"provider_config {config_id} not found")

        if "provider_type" in fields and body.provider_type is not None:
            row.provider_type = body.provider_type
        if "model_id" in fields and body.model_id is not None:
            row.model_id = body.model_id
        if "base_url" in fields:
            # SEC-BASEURL-1: validate base_url allowlist
            try:
                validate_base_url(body.base_url)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            row.base_url = body.base_url
        if "reasoning_effort" in fields:
            row.reasoning_effort = body.reasoning_effort
        if "max_iter" in fields and body.max_iter is not None:
            row.max_iter = body.max_iter
        if "token_budget" in fields and body.token_budget is not None:
            row.token_budget = body.token_budget
        if "is_fallback" in fields and body.is_fallback is not None:
            row.is_fallback = body.is_fallback

        # W1 api_key: non-empty ⇒ replace; empty string ⇒ clear; absent ⇒ leave as-is.
        if "api_key" in fields:
            row.api_key_encrypted = new_encrypted  # new ciphertext or None (clear)

        await session.flush()
        # updated_at is server-side (onupdate=now()); after an UPDATE flush it is expired and
        # would be lazily reloaded when the sync serializer reads it — which raises MissingGreenlet
        # in the async engine. Refresh explicitly (async-safe) so all columns are populated first.
        await session.refresh(row)
        response = _provider_config_to_response(row)

    return response


# ── DELETE /provider/config/{id} ───────────────────────────────────────────────


@router.delete(
    "/provider/config/{config_id}",
    status_code=204,
    summary="Delete a provider_config row by UUID",
    description="Hard-delete the provider_config row with the given id. (F17)",
    responses={
        204: {"description": "Row deleted"},
        404: {"description": "Row not found"},
    },
)
async def delete_provider_config(config_id: uuid.UUID) -> None:
    """Delete a provider_config row (F17). 404 if not found."""
    from sqlalchemy import delete as sa_delete

    async with _m.get_session() as session:
        result = await session.execute(
            sa_delete(ProviderConfig).where(ProviderConfig.id == config_id)
        )
        deleted = cast("CursorResult[Any]", result).rowcount

    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail=f"provider_config {config_id} not found",
        )


# ── GET /provider/vendors — W1 vendor catalog (Settings UI) ───────────────────


class VendorListResponse(BaseModel):
    """Response body for GET /provider/vendors (W1)."""

    vendors: list[VendorInfo] = Field(description="The fixed one-row-per-vendor catalog.")


@router.get(
    "/provider/vendors",
    response_model=VendorListResponse,
    summary="List the provider vendor catalog",
    description=(
        "Returns the curated one-row-per-vendor catalog for the Settings 'LLM Models' UI (W1). "
        "Each entry carries id, display_name, provider_type (api|local|cli), default_base_url, "
        "needs_api_key, model_presets, and notes. Static — no secrets, no DB. (F17, W1)"
    ),
)
async def list_provider_vendors() -> VendorListResponse:
    """GET /provider/vendors — the static vendor catalog (W1). No secrets, no DB read."""
    return VendorListResponse(vendors=list(VENDORS))


# ── POST /provider/test/{connection,function} — W1 bounded provider probes (I7) ─


class ProviderTestRequest(BaseModel):
    """
    Request body for the provider-test endpoints (W1).

    Provide EITHER config_id (probe a stored row — its decrypted key is used) OR inline
    {provider_type, model, base_url?, api_key?}. Inline fields override the stored row when both
    are given. api_key is WRITE-ONLY — never echoed back.
    """

    config_id: uuid.UUID | None = Field(
        default=None, description="Probe an existing provider_config row (uses its stored key)."
    )
    provider_type: str | None = Field(default=None, description="local | api | cli")
    base_url: str | None = Field(default=None, description="OpenAI-compatible endpoint (api only)")
    model: str | None = Field(default=None, description="Model id to probe")
    api_key: str | None = Field(
        default=None,
        description="WRITE-ONLY inline key; overrides the stored/env key. Never echoed.",
    )


class ProviderTestResponse(BaseModel):
    """Response body for the provider-test endpoints (W1). Never contains a key."""

    ok: bool = Field(
        description="True iff the probe succeeded (connection: HTTP ok; function: reply matched)."
    )
    latency_ms: int = Field(description="Wall-clock latency of the probe in milliseconds.")
    detail: str = Field(description="Human-readable outcome. NEVER contains the API key.")


async def _resolve_probe_target(
    body: ProviderTestRequest,
) -> tuple[str, str | None, str, str | None]:
    """
    Resolve (provider_type, base_url, model, api_key) for a probe from body/config_id.

    Inline body fields win over the stored row. Key precedence: inline api_key > decrypted
    stored key > env-var key (ANTHROPIC/OPENAI by path). Raises HTTP 422 when neither a
    resolvable config_id nor an inline {provider_type, model} is supplied. NEVER logs the key.
    """
    provider_type = body.provider_type
    base_url = body.base_url
    model = body.model
    stored_encrypted: bytes | None = None

    if body.config_id is not None:
        async with _m.get_session() as session:
            result = await session.execute(
                select(ProviderConfig).where(ProviderConfig.id == body.config_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"provider_config {body.config_id} not found"
                )
            provider_type = provider_type or row.provider_type
            base_url = base_url if body.base_url is not None else row.base_url
            model = model or row.model_id
            stored_encrypted = row.api_key_encrypted

    if not provider_type or not model:
        raise HTTPException(
            status_code=422,
            detail="provide a config_id, or inline provider_type + model",
        )
    if provider_type not in _VALID_PROVIDER_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"provider_type must be one of {sorted(_VALID_PROVIDER_TYPES)}",
        )

    api_key = _resolve_probe_key(provider_type, base_url, body.api_key, stored_encrypted)
    return provider_type, base_url, model, api_key


def _resolve_probe_key(
    provider_type: str, base_url: str | None, inline_key: str | None, stored: bytes | None
) -> str | None:
    """Key precedence for a probe: inline > decrypted stored > env-var. NEVER logged."""
    if inline_key:
        return inline_key
    if stored:
        try:
            return secrets_crypto.decrypt(bytes(stored))
        except (secrets_crypto.SecretsNotConfiguredError, secrets_crypto.InvalidToken):
            pass
    if provider_type == "api":
        return os.environ.get(_OPENAI_KEY_ENV if base_url else _ANTHROPIC_KEY_ENV)
    return None


async def _one_shot_chat(
    provider_type: str, base_url: str | None, model: str, api_key: str | None, instruction: str
) -> str:
    """
    Perform ONE bounded chat call and return the assistant text (W1, I7).

    Token-capped (_PROVIDER_TEST_MAX_TOKENS) and timeout-bounded (_PROVIDER_TEST_TIMEOUT_S).
    Dispatch by provider_type: api+base_url ⇒ OpenAI-compatible; api ⇒ Anthropic-native;
    local ⇒ Ollama. NEVER logs or returns the key.
    """
    timeout = _PROVIDER_TEST_TIMEOUT_S
    messages = [{"role": "user", "content": instruction}]

    if provider_type == "api" and base_url:
        if not api_key:
            raise ValueError("no API key resolved (inline, stored, or env)")
        req_body = {"model": model, "messages": messages, "max_tokens": _PROVIDER_TEST_MAX_TOKENS}
        headers = {"authorization": f"Bearer {api_key}", "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions", json=req_body, headers=headers
            )
            resp.raise_for_status()
            payload = resp.json()
        choices = payload.get("choices", [])
        return str(choices[0].get("message", {}).get("content", "")) if choices else ""

    if provider_type == "api":
        if not api_key:
            raise ValueError("no API key resolved (inline, stored, or env)")
        anthropic_base = os.environ.get(_ANTHROPIC_BASE_ENV, "https://api.anthropic.com").rstrip(
            "/"
        )
        req_body = {"model": model, "max_tokens": _PROVIDER_TEST_MAX_TOKENS, "messages": messages}
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{anthropic_base}/v1/messages", json=req_body, headers=headers
            )
            resp.raise_for_status()
            payload = resp.json()
        blocks = payload.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    if provider_type == "local":
        ollama_base = (base_url or os.environ.get(_OLLAMA_URL_ENV, "")).rstrip("/")
        if not ollama_base:
            raise ValueError("no Ollama base URL (set base_url or OLLAMA_URL)")
        req_body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": _PROVIDER_TEST_MAX_TOKENS},
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{ollama_base}/api/chat", json=req_body)
            resp.raise_for_status()
            payload = resp.json()
        return str(payload.get("message", {}).get("content", ""))

    # Should be unreachable (cli handled before this call).
    raise ValueError(f"unsupported provider_type for probe: {provider_type!r}")


async def _run_probe(
    body: ProviderTestRequest, *, instruction: str, require_ok: bool
) -> ProviderTestResponse:
    """
    Shared bounded probe for the connection/function endpoints (W1, I7).

    connection (require_ok=False): ok iff the endpoint returned a successful response.
    function   (require_ok=True):  ok iff the reply contains "ok" (case-insensitive).
    CLI is not live-probed (cheap posture check via cli_auth). NEVER echoes the key.
    """
    provider_type, base_url, model, api_key = await _resolve_probe_target(body)

    if provider_type == "cli":
        configured = _cli_auth._cli_auth_config_cache.token_configured()
        return ProviderTestResponse(
            ok=configured,
            latency_ms=0,
            detail=(
                "CLI credentials present (no live probe run for the agentic CLI backend)"
                if configured
                else "no CLI credentials configured (set the CLI subscription token or env)"
            ),
        )

    start = time.monotonic()
    try:
        text = await _one_shot_chat(provider_type, base_url, model, api_key, instruction)
    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ProviderTestResponse(
            ok=False, latency_ms=elapsed, detail=f"timeout after {_PROVIDER_TEST_TIMEOUT_S:.0f}s"
        )
    except httpx.HTTPStatusError as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return ProviderTestResponse(
            ok=False, latency_ms=elapsed, detail=f"HTTP {exc.response.status_code} from endpoint"
        )
    except (httpx.HTTPError, ValueError) as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        # exc messages here originate from our own code / httpx — never contain the key.
        return ProviderTestResponse(ok=False, latency_ms=elapsed, detail=str(exc))

    elapsed = int((time.monotonic() - start) * 1000)
    if require_ok:
        ok = "ok" in text.strip().lower()
        detail = "model followed the instruction" if ok else "model reply did not match 'OK'"
    else:
        ok = True
        detail = "endpoint responded"
    return ProviderTestResponse(ok=ok, latency_ms=elapsed, detail=detail)


@router.post(
    "/provider/test/connection",
    response_model=ProviderTestResponse,
    summary="Bounded provider connection probe (W1)",
    description=(
        "One bounded, token-capped call (timeout _PROVIDER_TEST_TIMEOUT_S) to verify the "
        "provider endpoint responds. Accepts a config_id (uses the stored, decrypted key) or an "
        "inline {provider_type, model, base_url?, api_key?}. Returns {ok, latency_ms, detail}; "
        "NEVER echoes the key. CLI backend is posture-checked, not live-probed. (F17, W1, I7)"
    ),
)
async def provider_test_connection(body: ProviderTestRequest) -> ProviderTestResponse:
    """POST /provider/test/connection — bounded connectivity probe (W1)."""
    return await _run_probe(body, instruction="Reply with the single word: OK", require_ok=False)


@router.post(
    "/provider/test/function",
    response_model=ProviderTestResponse,
    summary="Bounded provider instruction-follow probe (W1)",
    description=(
        "One bounded, token-capped call asking the model to reply exactly 'OK'; ok=true iff the "
        "reply contains 'OK'. Same input contract and safety as /provider/test/connection. "
        "(F17, W1, I7)"
    ),
)
async def provider_test_function(body: ProviderTestRequest) -> ProviderTestResponse:
    """POST /provider/test/function — bounded instruction-follow probe (W1)."""
    return await _run_probe(
        body,
        instruction="Reply with exactly the two characters: OK. No other text.",
        require_ok=True,
    )


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


# ── GET /mcp/info — read-only MCP server introspection (F1-MCP-UI, ADR-0027) ──


class McpToolInfo(BaseModel):
    """Schema for a single tool entry in GET /mcp/info (ADR-0027 §2.1)."""

    name: str = Field(description="Tool name as registered in the FastMCP server")
    description: str = Field(description="Full tool description (docstring); UI truncates")
    input_schema: dict[str, Any] = Field(
        description="JSON-Schema object for the tool's input arguments (tool.parameters)"
    )


class McpInfoResponse(BaseModel):
    """Response model for GET /mcp/info (ADR-0027 §2.1; ADR-0029 §2.5; ADR-0032 §2.5; ADR-0033)."""

    server_name: str = Field(
        description="MCP server name, derived from the live FastMCP object (I6)"
    )
    transport: str = Field(
        description="MCP transport type (MCP_TRANSPORT env, default 'stdio'; ADR-0010)"
    )
    entry_point_command: str = Field(
        description="Command to launch the MCP server (MCP_ENTRY_COMMAND env; ADR-0027 §2.3)"
    )
    tool_count: int = Field(description="Number of tools currently registered in the server")
    tools: list[McpToolInfo] = Field(description="Introspected tool list from the live registry")
    # ADR-0029 §2.5 additions — remote posture, no token ever returned
    http_enabled: bool = Field(
        description=(
            "Whether the HTTP MCP surface is compiled in and mounted (ADR-0029 §2.2). "
            "Always true (ADR-0033 §2.4 always-mount). The token itself is never returned. "
            "Alias: token_configured for backward compat."
        )
    )
    remote_write_enabled: bool = Field(
        description=(
            "Whether write tools (write_page, resolve_review, trigger_source_rescan) are "
            "enabled on the HTTP surface (ADR-0029 §2.3, ADR-0072). "
            "Reflects the effective runtime flag: vault_state.remote_mcp_write_enabled "
            "when non-NULL (DB-wins), else MCP_REMOTE_WRITE_ENABLED env (default false). "
            "Toggle via PUT /mcp/remote-write. "
            "Only meaningful when remote_enabled is true."
        )
    )
    # ADR-0032 §2.5 additions — runtime toggle posture; token NEVER returned
    token_configured: bool = Field(
        description=(
            "True iff a token is configured — DB hash set OR MCP_AUTH_TOKEN env set "
            "(ADR-0033 §2.1 precedence). NEVER contains the token value."
        )
    )
    remote_enabled: bool = Field(
        description=(
            "The persisted runtime toggle state from vault_state.remote_mcp_enabled "
            "(ADR-0032 §2.1). False by default; can be set via PUT /mcp/remote."
        )
    )
    mount_path: str = Field(
        description=(
            "The mount path for the remote MCP HTTP surface (= MCP_MOUNT_PATH constant). "
            "UI builds the connection URL as: window.location.origin + mount_path "
            "(ADR-0032 §2.5; I6 — derived from constant, never hardcoded in handler)."
        )
    )
    # ADR-0033 §2.5 additions — token source + allow flag; no token/hash/salt ever returned
    token_source: str = Field(
        description=(
            '"db" | "env" | "none" — which token source is authoritative (ADR-0033 §2.1). '
            '"db": UI-set token (PBKDF2 hash in vault_state). '
            '"env": MCP_AUTH_TOKEN env bootstrap. '
            '"none": no token configured. '
            "NEVER the token value, hash, or salt."
        )
    )
    allow_without_token: bool = Field(
        description=(
            "Whether token-less access is permitted for PRIVATE sources "
            "(loopback/CGNAT/RFC1918/link-local/ULA — ADR-0033 §2.3). "
            "PUBLIC sources (Cloudflare tunnel) are NEVER exempted regardless of this flag."
        )
    )


@router.get(
    "/mcp/info",
    response_model=McpInfoResponse,
    summary="Read-only MCP server introspection",
    description=(
        "Returns the live FastMCP server metadata: name, transport, entry-point command, "
        "and the full list of registered tools (name, description, input_schema). "
        "All values are derived from the live `mcp` object and settings — nothing hardcoded (I6). "
        "No MCP transport session is opened; no tool is invoked (I9). "
        "Read-only — edit MCP_TRANSPORT / MCP_ENTRY_COMMAND env vars to change. "
        "F1-MCP-UI (ADR-0027 §2.1)."
    ),
)
async def get_mcp_info() -> McpInfoResponse:
    """
    GET /mcp/info — read-only introspection of the Synapse FastMCP server (ADR-0027 §2.1).

    Derives every value from the live `mcp` object (imported at module level) and `settings`.
    No string about the MCP server is hardcoded inside this function (I6).
    No DB query, no Qdrant call, no MCP transport/stdio session is opened (I9).
    """
    # Introspect the live FastMCP registry — await directly in async handler (ADR-0027 §2.2).
    raw_tools = await _mcp_server.list_tools()

    tools: list[McpToolInfo] = [
        McpToolInfo(
            name=t.name,
            description=t.description or "",
            input_schema=t.parameters if t.parameters is not None else {},
        )
        for t in raw_tools
    ]

    # ADR-0033 §2.5: resolve token source from in-process cache.
    # NEVER return token/hash/salt — only boolean-derived values.
    db_hash = _m._mcp_auth_cache.get_hash()
    tok_source = _m._resolve_token_source(db_hash)
    tok_configured = _m._token_configured(db_hash)

    return McpInfoResponse(
        server_name=_mcp_server.name,
        transport=settings.mcp_transport,
        entry_point_command=settings.mcp_entry_command,
        tool_count=len(tools),
        tools=tools,
        # ADR-0029 §2.5 — always-mount (ADR-0033 §2.4); token NEVER returned
        http_enabled=True,  # always-mount (ADR-0033 §2.4)
        # ADR-0072 §5: report effective runtime flag (DB-wins-else-env), not the raw env var.
        remote_write_enabled=_m._mcp_write_flag.is_enabled(),
        # ADR-0032 §2.5 — runtime toggle posture
        token_configured=tok_configured,
        remote_enabled=_m._remote_mcp_flag.is_enabled(),
        mount_path=_m.MCP_MOUNT_PATH,
        # ADR-0033 §2.5 — token source + allow flag; NEVER the token/hash/salt
        token_source=tok_source,
        allow_without_token=_m._mcp_auth_cache.allow_without_token(),
    )


# ── PUT /mcp/remote — runtime toggle for remote MCP HTTP surface (ADR-0032 §2.4) ──


class McpRemoteToggleRequest(BaseModel):
    """Request body for PUT /mcp/remote (ADR-0032 §2.4)."""

    enabled: bool = Field(description="Desired runtime state for the remote MCP HTTP surface.")


class McpRemoteStateResponse(BaseModel):
    """
    Response model for PUT /mcp/remote (ADR-0032 §2.4).

    Always returned with HTTP 200 (even when clamped — the posture is reported truthfully).
    The token itself is NEVER returned (I6).
    """

    remote_enabled: bool = Field(
        description=(
            "The resulting persisted runtime flag (post-clamp). " "False when clamped=true."
        )
    )
    token_configured: bool = Field(
        description=(
            "True iff MCP_AUTH_TOKEN is set (the security floor). "
            "NEVER contains the token value."
        )
    )
    mount_path: str = Field(
        description="Mount path for the remote MCP HTTP surface (= MCP_MOUNT_PATH constant; I6)."
    )
    clamped: bool = Field(
        description=(
            "True iff the request asked enabled=true but MCP_AUTH_TOKEN is unset — "
            "the flag was forced to false (token-floor clamp, ADR-0032 §2.4)."
        )
    )


@router.put(
    "/mcp/remote",
    response_model=McpRemoteStateResponse,
    summary="Toggle the remote MCP HTTP surface at runtime",
    description=(
        "Persists vault_state.remote_mcp_enabled for the active vault and refreshes the "
        "in-process RemoteMcpFlag cache immediately (ADR-0032 §2.2/§2.4). "
        "Token-floor clamp: if MCP_AUTH_TOKEN is unset and enabled=true, the flag is "
        "forced to false and clamped=true is returned (HTTP 200). "
        "enabled=false always succeeds. "
        "Same-origin / unauthenticated — consistent with the rest of the REST API "
        "(ADR-0028 / ADR-0032 §2.4). "
        "F1-MCP-UI (ADR-0032)."
    ),
)
async def put_mcp_remote(body: McpRemoteToggleRequest) -> McpRemoteStateResponse:
    """
    PUT /mcp/remote — persist the runtime MCP toggle (ADR-0032 §2.4; amended by ADR-0033 §2.4/§2.5).

    Allow-aware clamp (ADR-0033 §2.4): enabling is permitted when EITHER
    ``token_configured OR allow_without_token``. Without either, enabling remote is
    pointless (the surface 404s for everyone), so we clamp to OFF.
    On success: write vault_state, refresh RemoteMcpFlag cache.
    No MCP tool is invoked; no second writer is introduced (I9).
    """
    db_hash = _m._mcp_auth_cache.get_hash()
    tok_configured: bool = _m._token_configured(db_hash)
    allow: bool = _m._mcp_auth_cache.allow_without_token()
    clamped: bool = False
    desired: bool = body.enabled

    # Allow-aware clamp (ADR-0033 §2.4): cannot enable without token OR allow.
    if desired and not tok_configured and not allow:
        desired = False
        clamped = True

    # Persist to vault_state (DB is source of truth — ADR-0032 §2.1).
    async with _m.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Should not happen (seeded at startup), but be defensive.
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_enabled=desired,
                updated_at=datetime.now(UTC),
            )
            session.add(state)
        else:
            state.remote_mcp_enabled = desired
            state.updated_at = datetime.now(UTC)

    # Refresh the in-process cache immediately (ADR-0032 §2.2).
    await _m._remote_mcp_flag.set(desired)

    logger.info(
        "PUT /mcp/remote: enabled=%s clamped=%s tok_configured=%s allow=%s (ADR-0032/0033)",
        desired,
        clamped,
        tok_configured,
        allow,
    )

    return McpRemoteStateResponse(
        remote_enabled=desired,
        token_configured=tok_configured,
        mount_path=_m.MCP_MOUNT_PATH,
        clamped=clamped,
    )


# ── PUT /mcp/remote-write — runtime toggle for remote MCP write tools (ADR-0072 §4) ─


class McpRemoteWriteToggleRequest(BaseModel):
    """Request body for PUT /mcp/remote-write (ADR-0072 §4)."""

    enabled: bool = Field(
        description="Desired runtime state for the write tools on the HTTP MCP surface."
    )


class McpRemoteWriteStateResponse(BaseModel):
    """
    Response model for PUT /mcp/remote-write (ADR-0072 §4).

    Always returned with HTTP 200 (even when clamped — the posture is reported truthfully).
    """

    remote_write_enabled: bool = Field(
        description=("The resulting persisted runtime flag (post-clamp). False when clamped=true.")
    )
    token_configured: bool = Field(
        description=(
            "True iff a token is configured (DB hash or env bootstrap — ADR-0033 §2.1). "
            "NEVER contains the token value."
        )
    )
    clamped: bool = Field(
        description=(
            "True iff the request asked enabled=true but neither a token is configured "
            "nor allow_without_token is set — the flag was forced to false "
            "(token-floor clamp, ADR-0072 §4)."
        )
    )


@router.put(
    "/mcp/remote-write",
    response_model=McpRemoteWriteStateResponse,
    summary="Toggle the remote MCP write tools at runtime",
    description=(
        "Persists vault_state.remote_mcp_write_enabled for the active vault and refreshes the "
        "in-process _mcp_write_flag cache immediately (ADR-0072 §2/§4). "
        "Token-floor clamp: if neither a token is configured (DB hash or MCP_AUTH_TOKEN env) "
        "nor allow_without_token is set, and enabled=true, the flag is forced to false and "
        "clamped=true is returned (HTTP 200). enabled=false always succeeds. "
        "Write tools (write_page, resolve_review, trigger_source_rescan) are always listed "
        "on the HTTP surface but error at call time when the flag is off (ADR-0072 §3). "
        "Same-origin / unauthenticated — consistent with the rest of the REST API "
        "(ADR-0028 / ADR-0072 §4). "
        "F17 [ADR-0072]."
    ),
)
async def put_mcp_remote_write(body: McpRemoteWriteToggleRequest) -> McpRemoteWriteStateResponse:
    """
    PUT /mcp/remote-write — persist the runtime write-tools toggle (ADR-0072 §4).

    Allow-aware clamp (mirrors ADR-0033 §2.4 / ADR-0072 §4): enabling is permitted when EITHER
    ``token_configured OR allow_without_token``. Without either, enabling write tools is
    pointless (the MCP surface itself is token-gated or not reachable), so we clamp to OFF.
    On success: write vault_state, refresh _mcp_write_flag cache.
    No MCP tool is invoked; no second writer is introduced (I9). Single write path preserved (I6).
    """
    db_hash = _m._mcp_auth_cache.get_hash()
    tok_configured: bool = _m._token_configured(db_hash)
    allow: bool = _m._mcp_auth_cache.allow_without_token()
    clamped: bool = False
    desired: bool = body.enabled

    # Allow-aware clamp (ADR-0072 §4): cannot enable without token OR allow.
    if desired and not tok_configured and not allow:
        desired = False
        clamped = True

    # Persist to vault_state (DB is source of truth — ADR-0072 §1).
    async with _m.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Should not happen (seeded at startup), but be defensive.
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_write_enabled=desired,
                updated_at=datetime.now(UTC),
            )
            session.add(state)
        else:
            state.remote_mcp_write_enabled = desired
            state.updated_at = datetime.now(UTC)

    # Refresh the in-process cache immediately (ADR-0072 §2).
    await _m._mcp_write_flag.set(desired)

    logger.info(
        "PUT /mcp/remote-write: enabled=%s clamped=%s tok_configured=%s allow=%s (ADR-0072)",
        desired,
        clamped,
        tok_configured,
        allow,
    )

    return McpRemoteWriteStateResponse(
        remote_write_enabled=desired,
        token_configured=tok_configured,
        clamped=clamped,
    )


# ── PUT /mcp/auth — set/rotate/clear token + allow flag (ADR-0033 §2.5) ──────


class McpAuthRequest(BaseModel):
    """
    Request body for PUT /mcp/auth (ADR-0033 §2.5).

    All fields are optional; omitting a field leaves that aspect unchanged.
    Exactly one of rotate_token / token / clear_token should be used per call
    (using multiple is allowed but the last write wins: clear > explicit > rotate).
    allow_without_token can be set independently in the same call.
    """

    rotate_token: bool | None = Field(
        default=None,
        description=(
            "true ⇒ generate a new high-entropy token (secrets.token_urlsafe(32)), "
            "store its PBKDF2 hash, return plaintext ONCE in generated_token. "
            "The plaintext is NEVER stored and NEVER returned again."
        ),
    )
    token: str | None = Field(
        default=None,
        description=(
            "Owner-supplied explicit token; stored as PBKDF2 hash only. "
            "generated_token stays null (owner already knows the value). "
            "Not echoed in the response."
        ),
    )
    clear_token: bool | None = Field(
        default=None,
        description=(
            "true ⇒ set mcp_access_token_hash = NULL. "
            "If this leaves token_configured=false AND allow_without_token=false, "
            "remote_enabled is clamped OFF (no usable auth posture)."
        ),
    )
    allow_without_token: bool | None = Field(
        default=None,
        description=(
            "Persist the allow-without-token flag (ADR-0033 §2.3). "
            "Omit to leave unchanged. "
            "true: private sources (loopback/CGNAT/RFC1918/link-local) may connect "
            "without a bearer token. PUBLIC sources are NEVER exempted."
        ),
    )


class McpAuthStateResponse(BaseModel):
    """
    Response body for PUT /mcp/auth (ADR-0033 §2.5).

    token_configured, token_source, allow_without_token, remote_enabled, mount_path
    always reflect the post-write state. generated_token is populated ONLY when
    rotate_token=true was set — shown ONCE, never returned again by any GET/PUT.
    NEVER contains the token, hash, or salt.
    """

    token_configured: bool = Field(
        description="True iff DB hash is set OR MCP_AUTH_TOKEN env bootstrap is set."
    )
    token_source: str = Field(
        description='"db" | "env" | "none" — authoritative token source (ADR-0033 §2.1).'
    )
    allow_without_token: bool = Field(
        description="The persisted allow-without-token flag after this write."
    )
    remote_enabled: bool = Field(
        description="The remote_mcp_enabled flag after any allow-aware clamp."
    )
    mount_path: str = Field(description="MCP_MOUNT_PATH constant (I6).")
    generated_token: str | None = Field(
        default=None,
        description=(
            "Populated ONLY when rotate_token=true — the plaintext token shown ONCE. "
            "null in all other cases. NEVER stored; NEVER returned by subsequent calls."
        ),
    )


@router.put(
    "/mcp/auth",
    response_model=McpAuthStateResponse,
    summary="Set, rotate, or clear the MCP access token + allow-without-token flag",
    description=(
        "ADR-0033 §2.5 — UI-settable MCP token management. "
        "rotate_token=true: generate a new token (secrets.token_urlsafe(32)), store its "
        "PBKDF2 hash in vault_state, return plaintext ONCE in generated_token. "
        "token=<value>: store an explicit token as hash; NOT echoed; generated_token=null. "
        "clear_token=true: set hash to NULL (token_source may fall back to env or none). "
        "allow_without_token: persist the private-source allow flag. "
        "If post-write state has no token AND allow_without_token=false, remote_enabled "
        "is clamped OFF (allow-aware clamp — ADR-0033 §2.4). "
        "Same-origin / unauthenticated (consistent with ADR-0032 §2.4). "
        "NEVER returns or stores token plaintext (except the one-time generated_token). "
        "F1-MCP-UI (ADR-0033)."
    ),
)
async def put_mcp_auth(body: McpAuthRequest) -> McpAuthStateResponse:
    """
    PUT /mcp/auth — UI-settable MCP token management (ADR-0033 §2.5).

    Applies changes in this order:
      1. clear_token (if true) → set hash NULL.
      2. token (if set) → hash and persist.
      3. rotate_token (if true) → generate, hash, persist, capture plaintext.
      4. allow_without_token (if set) → persist.
      5. Apply allow-aware clamp to remote_enabled (§2.4).
      6. Refresh in-process caches.
      7. Return McpAuthStateResponse (no plaintext except generated_token).

    No MCP tool is invoked; no second writer is introduced (I9).
    """
    generated_token: str | None = None

    async with _m.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Should not happen (seeded at startup), but be defensive.
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_enabled=False,
                mcp_access_token_hash=None,
                mcp_allow_without_token=False,
                updated_at=datetime.now(UTC),
            )
            session.add(state)

        # 1. clear_token
        if body.clear_token:
            state.mcp_access_token_hash = None

        # 2. explicit token
        if body.token is not None:
            state.mcp_access_token_hash = _m._hash_token(body.token)
            # Do NOT echo body.token in logs or response.

        # 3. rotate_token (takes precedence over explicit token if both are set)
        if body.rotate_token:
            new_plaintext = secrets.token_urlsafe(32)
            state.mcp_access_token_hash = _m._hash_token(new_plaintext)
            # Capture plaintext for the one-time response; NEVER persist it.
            generated_token = new_plaintext
            # Immediately discard from local scope after assigning to response var;
            # new_plaintext goes out of scope here.

        # 4. allow_without_token
        if body.allow_without_token is not None:
            state.mcp_allow_without_token = body.allow_without_token

        # 5. Allow-aware clamp on remote_enabled (ADR-0033 §2.4).
        new_hash = state.mcp_access_token_hash
        new_allow = state.mcp_allow_without_token
        tok_configured_post = _m._token_configured(new_hash)
        if state.remote_mcp_enabled and not tok_configured_post and not new_allow:
            state.remote_mcp_enabled = False
            logger.info(
                "PUT /mcp/auth: remote_enabled clamped OFF "
                "(no token AND allow=false, ADR-0033 §2.4)"
            )

        state.updated_at = datetime.now(UTC)

        # Capture final values for cache update (inside session scope).
        final_hash = state.mcp_access_token_hash
        final_allow = state.mcp_allow_without_token
        final_remote = state.remote_mcp_enabled

    # 6. Refresh in-process caches (outside session — DB write committed).
    await _m._mcp_auth_cache.set_hash(final_hash)
    await _m._mcp_auth_cache.set_allow(final_allow)
    await _m._remote_mcp_flag.set(final_remote)

    # 7. Derive response values (NEVER return hash, plaintext, or salt).
    tok_source = _m._resolve_token_source(final_hash)
    tok_configured = _m._token_configured(final_hash)

    logger.info(
        "PUT /mcp/auth: token_source=%s allow_without_token=%s remote_enabled=%s (ADR-0033)",
        tok_source,
        final_allow,
        final_remote,
    )

    return McpAuthStateResponse(
        token_configured=tok_configured,
        token_source=tok_source,
        allow_without_token=final_allow,
        remote_enabled=final_remote,
        mount_path=_m.MCP_MOUNT_PATH,
        generated_token=generated_token,
    )


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
    scheduler = _m._import_scheduler
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


@router.get(
    "/clip/config",
    response_model=ClipConfigResponse,
    summary="Read-only web clipper ingress posture (ADR-0040)",
    description=(
        "Returns the current posture of the POST /clip ingress: enabled state, "
        "token_configured (bool, never the value), token_source (db|env|none), "
        "allowed_origins list, and max_body_bytes. "
        "Mirrors GET /mcp/info: no sensitive values ever returned. "
        "F11-clip-config (ADR-0040)."
    ),
)
async def get_clip_config() -> ClipConfigResponse:
    """
    GET /clip/config — read-only web clipper ingress posture (ADR-0040).

    All values derived from the in-process _m._clip_config_cache (loaded from vault_state
    at startup and refreshed on PUT /clip/config writes). No DB query on each GET.
    NEVER returns the token value, only token_configured + token_source.
    """
    return ClipConfigResponse(
        enabled=_m._clip_config_cache.resolved_enabled(),
        token_configured=_m._clip_config_cache.token_configured(),
        token_source=_m._clip_config_cache.token_source(),
        allowed_origins=_m._clip_config_cache.resolved_allowed_origins_list(),
        max_body_bytes=settings.clip_max_body_bytes,
    )


# ── PUT /clip/config — set/rotate/clear clip token + enabled + origins (ADR-0040) ─


class ClipConfigRequest(BaseModel):
    """
    Request body for PUT /clip/config (ADR-0040 §2.4).

    All fields are optional; omitting a field leaves that aspect unchanged.
    Mirrors McpAuthRequest (ADR-0033 §2.5).
    """

    rotate_token: bool | None = Field(
        default=None,
        description=(
            "true ⇒ generate a new high-entropy token (secrets.token_urlsafe(32)), "
            "store its PBKDF2-SHA256 hash in clip_access_token (never the raw value), "
            "return plaintext ONCE in generated_token. "
            "The plaintext is NEVER stored or returned again after this call."
        ),
    )
    clear_token: bool | None = Field(
        default=None,
        description=(
            "true ⇒ set clip_access_token = NULL (DB token cleared; "
            "falls back to CLIP_TOKEN env bootstrap or none)."
        ),
    )
    set_enabled: bool | None = Field(
        default=None,
        description=(
            "Set the clip_enabled_db flag. "
            "true ⇒ DB overrides CLIP_ENABLED env with True. "
            "false ⇒ DB overrides with False (ingress disabled regardless of env). "
            "Omit to leave unchanged."
        ),
    )
    set_allowed_origins: str | None = Field(
        default=None,
        description=(
            "Replace the DB clip_allowed_origins_db value with this comma-separated string. "
            'Empty string "" clears the DB value (falls back to CLIP_ALLOWED_ORIGINS env). '
            "Omit to leave unchanged."
        ),
    )


class ClipConfigStateResponse(BaseModel):
    """
    Response body for PUT /clip/config (ADR-0040 §2.4).

    Always reflects post-write posture. generated_token is populated ONLY when
    rotate_token=true — shown ONCE, never returned again.
    NEVER contains the token value (except the one-time generated_token on rotate).
    """

    enabled: bool = Field(description="Resolved enabled state after this write.")
    token_configured: bool = Field(
        description="True iff a token is available after this write (DB or env)."
    )
    token_source: str = Field(
        description='"db" | "env" | "none" — authoritative token source after this write.'
    )
    allowed_origins: list[str] = Field(
        description="Resolved allowed-origins list after this write."
    )
    max_body_bytes: int = Field(description="CLIP_MAX_BODY_BYTES (env, not runtime-settable).")
    generated_token: str | None = Field(
        default=None,
        description=(
            "Populated ONLY when rotate_token=true — the plaintext token shown ONCE. "
            "null in all other cases. NEVER stored as recoverable. "
            "NEVER returned by subsequent GET or PUT."
        ),
    )


@router.put(
    "/clip/config",
    response_model=ClipConfigStateResponse,
    summary="Set, rotate, or clear the clip ingress token + enabled/origins (ADR-0040)",
    description=(
        "ADR-0040 §2.4 — runtime web clipper configuration. "
        "rotate_token=true: generate a new token (secrets.token_urlsafe(32)), store its "
        "PBKDF2-SHA256 hash in vault_state.clip_access_token, return plaintext ONCE in "
        "generated_token (never stored). "
        "clear_token=true: set DB token to NULL (falls back to CLIP_TOKEN env or none). "
        "set_enabled: set clip_enabled_db (DB wins over CLIP_ENABLED env when set). "
        'set_allowed_origins: replace DB origins (empty string "" clears to env fallback). '
        "Same-origin / unauthenticated — consistent with PUT /mcp/auth (ADR-0033 §2.5). "
        "NEVER returns or stores the token plaintext (except the one-time generated_token). "
        "F11-clip-config (ADR-0040)."
    ),
)
async def put_clip_config(body: ClipConfigRequest) -> ClipConfigStateResponse:
    """
    PUT /clip/config — runtime web clipper configuration (ADR-0040 §2.4).

    Applies changes in this order:
      1. clear_token (if true) → set clip_access_token = NULL.
      2. rotate_token (if true) → generate plaintext, hash with PBKDF2, store hash,
         capture plaintext for one-time response (never persisted).
      3. set_enabled (if set) → persist clip_enabled_db.
      4. set_allowed_origins (if set) → persist clip_allowed_origins_db
         (empty string → NULL = env-fallback).
      5. Refresh in-process _m._clip_config_cache.
      6. Return ClipConfigStateResponse (no token plaintext except one-time generated_token).

    Mirrors PUT /mcp/auth (ADR-0033 §2.5).
    """
    generated_token: str | None = None

    async with _m.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        if state is None:
            # Should not happen (seeded at startup), but be defensive.
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_enabled=False,
                mcp_access_token_hash=None,
                mcp_allow_without_token=False,
                clip_enabled_db=None,
                clip_access_token=None,
                clip_allowed_origins_db=None,
                updated_at=datetime.now(UTC),
            )
            session.add(state)

        # 1. clear_token
        if body.clear_token:
            state.clip_access_token = None

        # 2. rotate_token (takes precedence over clear if both are set)
        if body.rotate_token:
            new_plaintext = secrets.token_urlsafe(32)
            # Hash for storage (mirrors MCP ADR-0033 §2.1 — never store plaintext in DB).
            # The PBKDF2 hash is safe even if the DB is compromised.
            state.clip_access_token = _m._hash_token(new_plaintext)
            # Capture plaintext for the one-time response ONLY (never persisted).
            generated_token = new_plaintext
            # new_plaintext out of scope after assignment to generated_token.

        # 3. set_enabled
        if body.set_enabled is not None:
            state.clip_enabled_db = body.set_enabled

        # 4. set_allowed_origins (empty string → NULL = env-fallback)
        if body.set_allowed_origins is not None:
            state.clip_allowed_origins_db = (
                body.set_allowed_origins if body.set_allowed_origins else None
            )

        state.updated_at = datetime.now(UTC)

        # Capture final values for cache update (inside session scope — will be committed).
        # clip_access_token is now a PBKDF2 hash (or None); store hash in cache.
        final_hash: str | None = state.clip_access_token
        final_enabled_db: bool | None = state.clip_enabled_db
        final_origins_db: str | None = state.clip_allowed_origins_db

    # 5. Refresh in-process caches (outside session — DB write committed).
    await _m._clip_config_cache.set_hash(final_hash)
    await _m._clip_config_cache.set_enabled_db(final_enabled_db)
    await _m._clip_config_cache.set_allowed_origins_db(final_origins_db)

    tok_source = _m._clip_config_cache.token_source()
    tok_configured = _m._clip_config_cache.token_configured()
    resolved_enabled = _m._clip_config_cache.resolved_enabled()
    resolved_origins = _m._clip_config_cache.resolved_allowed_origins_list()

    logger.info(
        "PUT /clip/config: enabled=%s token_source=%s origins_source=%s (ADR-0040)",
        resolved_enabled,
        tok_source,
        _m._clip_config_cache.origins_source(),
        # NEVER log the token value
    )

    # 6. Return posture (no plaintext except the one-time generated_token).
    return ClipConfigStateResponse(
        enabled=resolved_enabled,
        token_configured=tok_configured,
        token_source=tok_source,
        allowed_origins=resolved_origins,
        max_body_bytes=settings.clip_max_body_bytes,
        generated_token=generated_token,
    )


# ── GET /web-search/config — read-only SearXNG posture (ADR-0041) ─────────────


class WebSearchConfigResponse(BaseModel):
    """
    Response model for GET /web-search/config (ADR-0041 §2.3).

    The SearXNG URL is NOT a secret — it IS returned (unlike the clip token).
    source values: "db" | "env" | "none".
    """

    configured: bool = Field(
        description=(
            "True iff a SearXNG URL is available (DB or env). "
            "POST /research/start returns 503 when false."
        )
    )
    url: str | None = Field(
        description=(
            "Resolved SearXNG base URL (DB wins over env; ADR-0041 §2.2). "
            "None when neither DB nor env is set. "
            "NOT a secret — returned in full (unlike clip/mcp tokens)."
        )
    )
    categories: list[str] = Field(
        description=(
            "Resolved SearXNG categories list (DB wins over env/default; ADR-0041 §2.2). "
            "Empty list when neither DB nor env sets this — SearXNG uses its own default."
        )
    )
    max_queries: int = Field(
        description=(
            "Resolved max SearXNG queries per deep-research iteration "
            "(DB wins over DEEP_RESEARCH_MAX_QUERIES env; ADR-0041 §2.2)."
        )
    )
    source: str = Field(
        description=(
            '"db" | "env" | "none" — which URL source is authoritative (ADR-0041 §2.2). '
            '"db": URL set via PUT /web-search/config. '
            '"env": SEARXNG_URL env var. '
            '"none": no URL configured.'
        )
    )


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

    All values derived from the in-process _m._web_search_config_cache (loaded from
    vault_state at startup and refreshed on PUT /web-search/config writes).
    No DB query on each GET. The URL IS returned (not a secret — ADR-0041 §2.1).
    """
    return WebSearchConfigResponse(
        configured=_m._web_search_config_cache.configured(),
        url=_m._web_search_config_cache.resolved_url(),
        categories=_m._web_search_config_cache.resolved_categories(),
        max_queries=_m._web_search_config_cache.resolved_max_queries(),
        source=_m._web_search_config_cache.url_source(),
    )


# ── PUT /web-search/config — set/clear SearXNG URL + categories + max_queries (ADR-0041) ─


class WebSearchConfigRequest(BaseModel):
    """
    Request body for PUT /web-search/config (ADR-0041 §2.4).

    All fields are optional; omitting a field leaves that aspect unchanged.
    No provider field — SearXNG is the ONLY web-search backend (I9).
    Passing any non-SearXNG provider name is rejected with 422 (I9 guard).
    """

    set_url: str | None = Field(
        default=None,
        description=(
            "Set the SearXNG base URL in vault_state (DB wins over env). "
            "Must be a valid http(s) URL. "
            "Set to null to clear the DB URL (falls back to SEARXNG_URL env)."
        ),
    )
    set_categories: str | None = Field(
        default=None,
        description=(
            "Comma-separated SearXNG categories (e.g. 'general,news'). "
            'Empty string "" clears to default. '
            "Omit to leave unchanged."
        ),
    )
    set_max_queries: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description=(
            "Max SearXNG queries per deep-research iteration (1–50). " "Omit to leave unchanged."
        ),
    )
    clear: bool | None = Field(
        default=None,
        description=(
            "If true, clear ALL DB overrides (url, categories, max_queries). "
            "After clear, all three fall back to env / code defaults. "
            "Applied FIRST; then set_* fields are applied."
        ),
    )


class WebSearchConfigStateResponse(BaseModel):
    """
    Response body for PUT /web-search/config (ADR-0041 §2.4).

    Always reflects post-write posture.
    """

    configured: bool = Field(description="True iff a SearXNG URL is now available.")
    url: str | None = Field(description="Resolved SearXNG URL post-write (not a secret).")
    categories: list[str] = Field(description="Resolved categories list post-write.")
    max_queries: int = Field(description="Resolved max_queries post-write.")
    source: str = Field(description='"db" | "env" | "none" — URL source post-write.')


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
      5. Refresh in-process _m._web_search_config_cache.
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

    async with _m.get_session() as session:
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
    await _m._web_search_config_cache.set_url_db(final_url_db)
    await _m._web_search_config_cache.set_categories_db(final_categories_db)
    await _m._web_search_config_cache.set_max_queries_db(final_max_queries_db)

    logger.info(
        "PUT /web-search/config: url_source=%s categories_source=%s "
        "max_queries_source=%s configured=%s (ADR-0041)",
        _m._web_search_config_cache.url_source(),
        _m._web_search_config_cache.categories_source(),
        _m._web_search_config_cache.max_queries_source(),
        _m._web_search_config_cache.configured(),
    )

    # 6. Return posture.
    return WebSearchConfigStateResponse(
        configured=_m._web_search_config_cache.configured(),
        url=_m._web_search_config_cache.resolved_url(),
        categories=_m._web_search_config_cache.resolved_categories(),
        max_queries=_m._web_search_config_cache.resolved_max_queries(),
        source=_m._web_search_config_cache.url_source(),
    )


# ── GET/PUT /web-search/provider-keys — cloud provider API keys (P3-e, ADR-0071) ─────


class WebSearchProviderKeyState(BaseModel):
    """Masked posture for one cloud web-search provider — NEVER the key value."""

    configured: bool = Field(description="True if a key is set (DB or env)")
    source: str = Field(description="'db' | 'env' | 'none'")


class WebSearchProviderKeysResponse(BaseModel):
    """GET /web-search/provider-keys — masked posture for all cloud providers."""

    secrets_available: bool = Field(
        description="True if SYNAPSE_SECRET_KEY is set (required to store keys via the UI)"
    )
    providers: dict[str, WebSearchProviderKeyState] = Field(
        description="Per-provider masked posture (tavily/serpapi/firecrawl/brave)"
    )


class WebSearchProviderKeyRequest(BaseModel):
    """PUT /web-search/provider-keys — set (key) or clear (clear=true) one provider's key."""

    provider: str = Field(description="tavily | serpapi | firecrawl | brave")
    key: str | None = Field(default=None, description="API key to store; omit when clearing")
    clear: bool = Field(default=False, description="True to remove the stored key (env resumes)")


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


_CLI_TOKEN_PREFIX: str = "sk-ant-" + "oat01-"


class CliAuthConfigRequest(BaseModel):
    """
    Request body for PUT /provider/cli-auth (ADR-0043 §2.5).

    Exactly one of {token, clear} should be present:
      token: str  — paste the token produced by ``claude setup-token`` (prefix: sk-ant- + oat01-)
      clear: bool — true ⇒ set vault_state.cli_oauth_token = NULL (fall back to env / none)

    ``clear`` wins if both are sent. An empty body (neither field) → 400.
    """

    token: str | None = Field(
        default=None,
        description=(
            "The Claude subscription OAuth token to store (from `claude setup-token`). "
            "Encrypted at rest via Fernet (SYNAPSE_SECRET_KEY); plaintext held in-memory "
            "only for outbound CLI injection. NEVER logged or returned. "
            "Requires SYNAPSE_SECRET_KEY configured server-side (else HTTP 400 — W7). "
            "Validated: non-empty, 20–500 chars; soft prefix check (warns, does not block)."
        ),
    )
    clear: bool | None = Field(
        default=None,
        description=(
            "true ⇒ set cli_oauth_token_encrypted = NULL (fall back to env / none). "
            "Wins over token if both are sent."
        ),
    )


@router.put(
    "/provider/cli-auth",
    response_model=CliAuthConfigResponse,
    summary="Set or clear the CLI subscription OAuth token (ADR-0043 / W7)",
    description=(
        "ADR-0043 §2.5 (W7 amendment) — store a pasted Claude subscription OAuth token or "
        "clear it. "
        "clear=true: set DB token to NULL (falls back to env / none). "
        "token=<value>: validate, Fernet-encrypt (requires SYNAPSE_SECRET_KEY — else HTTP 400), "
        "and store in vault_state.cli_oauth_token_encrypted; refresh cache. "
        "Returns post-write posture (same shape as GET); NEVER the token value. "
        "400 if body has neither token nor clear. "
        "400 if SYNAPSE_SECRET_KEY is unset when storing a new token (fail-closed). "
        "422 if token is empty/whitespace or absurd length. "
        "Soft prefix check warns but does NOT hard-reject — ADR-0043 §2.5."
    ),
)
async def put_cli_auth_config(body: CliAuthConfigRequest) -> CliAuthConfigResponse:
    """
    PUT /provider/cli-auth — set or clear the CLI subscription OAuth token (ADR-0043 §2.5,
    W7 encryption amendment).

    Semantics:
      1. clear=true (wins if both sent) → set cli_oauth_token_encrypted = NULL,
         cli_oauth_token = NULL (legacy); refresh cache.
      2. token=<value> → validate; Fernet-encrypt (SYNAPSE_SECRET_KEY — HTTP 400 if absent);
         store ciphertext in cli_oauth_token_encrypted; clear legacy cli_oauth_token; refresh
         cache with the plaintext (in-memory only, for outbound CLI injection).
      3. neither field → 400 (no-op request).
    Returns post-write posture. NEVER logs or returns the token value.
    """
    # 0. Guard: empty body (neither field set).
    if not body.clear and body.token is None:
        raise HTTPException(status_code=400, detail="Provide token or clear=true.")

    # Pre-validate the token BEFORE opening a DB session (no unnecessary DB round-trip
    # on bad input — mirrors the clip pattern of early-exit on validation failure).
    validated_token: str | None = None  # None = clear or will be set below
    token_encrypted: bytes | None = None  # Fernet ciphertext, set only on SET path

    if not body.clear:
        raw = (body.token or "").strip()
        if not raw:
            raise HTTPException(
                status_code=422,
                detail="token must be a non-empty, non-whitespace string.",
            )
        if len(raw) < 20 or len(raw) > 500:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"token length {len(raw)} is outside the accepted range [20, 500]. "
                    "Verify you pasted the full token from `claude setup-token`."
                ),
            )
        # Soft prefix check — warn but never hard-block (ADR-0043 §2.5).
        if not raw.startswith(_CLI_TOKEN_PREFIX):
            logger.warning(
                "PUT /provider/cli-auth: token does not match expected prefix; "
                "accepting anyway — Anthropic may change the prefix (ADR-0043 §2.5)."
                # NEVER log the token value itself.
            )
        # W7: encrypt BEFORE the DB session — fail early with 400 if key absent.
        token_encrypted = _encrypt_api_key_or_400(raw)
        validated_token = raw

    final_token: str | None = None  # plaintext for in-process cache; None after clear

    async with _m.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state: VaultState | None = row.scalar_one_or_none()
        if state is None:
            # Seed row (mirrors the put_clip_config pattern).
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                remote_mcp_enabled=False,
                mcp_access_token_hash=None,
                mcp_allow_without_token=False,
                clip_enabled_db=None,
                clip_access_token=None,
                clip_allowed_origins_db=None,
                updated_at=datetime.now(UTC),
            )
            session.add(state)

        # 1. clear wins if both fields supplied (already validated above).
        if body.clear:
            # Null both columns — the legacy plaintext and the new encrypted column.
            state.cli_oauth_token_encrypted = None
            state.cli_oauth_token = None  # legacy column (kept for rollback safety)
        else:
            # 2. Store Fernet ciphertext (W7 — plaintext NEVER written to DB).
            state.cli_oauth_token_encrypted = token_encrypted
            # Null legacy plaintext column so the read path unambiguously uses the
            # encrypted column (no dual-state confusion after migration 0027).
            state.cli_oauth_token = None
            final_token = validated_token  # plaintext for in-process cache only

        state.updated_at = datetime.now(UTC)

    # 3. Refresh in-process cache with the plaintext (outside session — DB write committed).
    #    The cache holds the decrypted token in-memory ONLY — it is never written back to DB.
    await _cli_auth._cli_auth_config_cache.set_token(final_token)
    logger.info(
        "PUT /provider/cli-auth: token_source=%s auth_mode=%s (ADR-0043 / W7)",
        _cli_auth._cli_auth_config_cache.token_source(),
        _cli_auth._cli_auth_config_cache.auth_mode(),
        # NEVER log the token value
    )

    # 4. Return post-write posture (never the value).
    cache = _cli_auth._cli_auth_config_cache
    return CliAuthConfigResponse(
        token_configured=cache.token_configured(),
        token_source=cache.token_source(),
        auth_mode=cache.auth_mode(),
    )


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


class AppConfigPutBody(BaseModel):
    """Request body for PUT /config/app/{key} (ADR-0053 §3.2)."""

    value: str = Field(
        description=(
            "Override value as a string. Required and non-null "
            "(app_config.value is NOT NULL — use DELETE to reset to default, §3.3). "
            "Per-key validation rules: ADR-0053 §2.3."
        )
    )


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
        async with _m.get_session() as session:
            await clear_override(session, key)
        return Response(status_code=204)

    # Validate value (422 on failure, no write — ADR-0053 §2.3)
    err = validate_value(key, body.value)
    if err is not None:
        raise HTTPException(status_code=422, detail=err)

    async with _m.get_session() as session:
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

    async with _m.get_session() as session:
        await clear_override(session, key)

    logger.info("DELETE /config/app/%s: source=env (override removed — ADR-0053 §3.3)", key)
    return Response(status_code=204)
