"""Shared Pydantic DTOs + validation constants for the config/provider/mcp/
clip/web-search/import-schedule domains.

Extracted verbatim from app.routers.config during the BE-REFAC-1 split so the
per-domain router modules import their request/response models from one place.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.provider_vendors import VENDORS, VendorInfo

# W1 (F17): allowed reasoning_effort values (auto/null = provider default, no override).
_VALID_REASONING_EFFORT = {"auto", "off", "low", "medium", "high", "max", "custom"}

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


# ── GET /provider/vendors — W1 vendor catalog (Settings UI) ───────────────────


class VendorListResponse(BaseModel):
    """Response body for GET /provider/vendors (W1)."""

    vendors: list[VendorInfo] = Field(description="The fixed one-row-per-vendor catalog.")


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


class AppConfigPutBody(BaseModel):
    """Request body for PUT /config/app/{key} (ADR-0053 §3.2)."""

    value: str = Field(
        description=(
            "Override value as a string. Required and non-null "
            "(app_config.value is NOT NULL — use DELETE to reset to default, §3.3). "
            "Per-key validation rules: ADR-0053 §2.3."
        )
    )


# ── PF-AUTH-1 (1.9.4 W4): scoped API tokens ───────────────────────────────────


class ApiTokenCreateRequest(BaseModel):
    """Request body for POST /config/api-tokens (PF-AUTH-1)."""

    label: str = Field(
        min_length=1,
        max_length=200,
        description="Human-readable description of what this token is used for.",
    )
    vault_id: str | None = Field(
        default=None,
        description=(
            "NULL (default) = global token, valid for any vault this backend serves. "
            "Non-NULL = the token is only accepted when it equals this backend instance's "
            "settings.vault_id at request time; a mismatch is rejected as an invalid token."
        ),
    )
    read_only: bool = Field(
        default=False,
        description=(
            "True = the token may only be used for GET/HEAD/OPTIONS requests; any other "
            "HTTP method is rejected with 403."
        ),
    )


class ApiTokenCreateResponse(BaseModel):
    """
    Response body for POST /config/api-tokens (PF-AUTH-1).

    ``token`` is the PLAINTEXT secret — shown exactly once, here, and never again. The
    caller MUST copy it now; only its PBKDF2 hash is persisted (app.models.ApiToken).
    """

    id: uuid.UUID = Field(description="Row id — pass this to DELETE /config/api-tokens/{id}.")
    label: str
    vault_id: str | None
    read_only: bool
    created_at: datetime
    token: str = Field(
        description=(
            "The plaintext bearer secret. Shown ONE TIME ONLY — copy it now. "
            "NEVER returned again by any endpoint, NEVER logged."
        )
    )


class ApiTokenListItem(BaseModel):
    """One row in GET /config/api-tokens (PF-AUTH-1). NEVER includes the secret/hash."""

    id: uuid.UUID
    label: str
    vault_id: str | None
    read_only: bool
    created_at: datetime
    last_used_at: datetime | None


class ApiTokenListResponse(BaseModel):
    """Response body for GET /config/api-tokens (PF-AUTH-1)."""

    tokens: list[ApiTokenListItem]
