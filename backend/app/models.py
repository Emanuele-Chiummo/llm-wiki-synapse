"""
SQLAlchemy 2 ORM models — single source of truth for D2 ER diagram (I8 / AC-PG-3).

Tables defined here:
  - pages             : one row per source file; soft-deletable (ADR-0005).
                        v0.3: adds pages.x / pages.y (FA2 layout coords, ADR-0013 / AQ-6).
                        v0.6: adds pages.tags (K6 navigation tags; JSONB, mirrors sources;
                        Alembic migration 0018).
  - vault_state       : one row per vault; holds the monotonic data_version (ADR-0005).
                        v0.5-ADR-0032: adds remote_mcp_enabled (Alembic migration 0011).
                        v0.5-ADR-0033: adds mcp_access_token_hash + mcp_allow_without_token
                        (Alembic migration 0012).
                        v0.6-ADR-0040: adds clip_enabled_db + clip_access_token +
                        clip_allowed_origins_db (Alembic migration 0015).
  - provider_config   : F17 backend selection per scope (global|vault|operation) (ADR-0008).
  - ingest_runs       : per-run cost/convergence audit ledger (I7, ADR-0008 §4).
  - links             : K5 wikilink edges; source_page_id → target_title (dangling until resolved).
  - edges             : v0.3 graph edges; 4-signal weighted pairs (ADR-0012 / AQ-5).
  - conversations     : v0.4 F6 chat threads; soft-deletable (ADR-0019 §2.5).
  - messages          : v0.4 F6 chat messages; per-message token/cost columns (I7, ADR-0019).
  - import_schedules  : M4-EXT scheduled folder import config + last-run status (ADR-0020 §4.1).
  - deep_research_runs    : v0.5 F10 per-run audit ledger for deep research (ADR-0024 §7.1).
  - deep_research_sources : v0.5 F10 per-source child rows (ADR-0024 §7.2).
  - review_items      : v0.5 F9 HITL review queue; redesigned in ADR-0034.
                        Alembic migration 0010 (original); migration 0013 (redesign).
                        Now stores PROPOSALS (5 types) with lazy on-demand Create.
  - lint_runs         : v0.6 K2 lint-fix loop per-run audit ledger (ADR-0037 §3).
  - lint_findings     : v0.6 K2 lint-fix proposals (orphan/missing-xref/contradiction/
                        stale-claim/missing-page/broken-wikilink); human-gated apply (ADR-0037 §3).
                        Alembic 0024: adds suggested_target (Text) + suggested_page_id (UUID FK).
                        (L2 / ADR-0037 B1)

provider_config + ingest_runs added in v0.2 (ADR-0008). links added in v0.2 (ADR-0008 §5).
All three new tables ship in a single Alembic migration 0002 (one schema-change event).

v0.3: edges table + pages.x/y columns ship in Alembic migration 0003 (one schema-change
event, ADR-0012 / ADR-0013).

v0.4: ingest_runs.status / pages_created / error_message added in Alembic migration 0006
(ADR-0018 §7); max_iter_used and finished_at are aliased in the API response layer as
iterations_used and completed_at respectively.

M4-EXT: import_schedules table added in Alembic migration 0008 (ADR-0020 §4.1).

v0.5-F10: deep_research_runs + deep_research_sources added in Alembic migration 0009
(ADR-0024 §7 — F10 Deep Research loop).

v0.5-F9: review_items added in Alembic migration 0010 (ADR-0025 §3.1 — F9 HITL review queue).

v0.6-K2: lint_runs + lint_findings added in Alembic migration 0014 (ADR-0037 — K2 lint-fix
    loop). lint_runs mirrors deep_research_runs (id, vault_id, status, total_cost_usd,
    error_message, created_at + bounds frozen at INSERT); lint_findings mirrors review_items
    (id, lint_run_id FK, category, severity, target_page_id FK → pages, description,
    proposed_action, status[open|applied|dismissed], created_at).

v0.5-ADR-0034: review_items redesigned in Alembic migration 0013 (ADR-0034 — proposal model).
    Added: source_page_id, proposed_title, proposed_page_type, proposed_dir, rationale,
    resolution, created_page_id. Dropped: pre_generated_query. Extended item_type to 5 values
    (missing-page|suggestion|contradiction|duplicate|confirm); extended status to include
    created|auto_resolved. Added ix_review_items_vault_proposed_title index.

v0.5-ADR-0032: vault_state.remote_mcp_enabled added in Alembic migration 0011 (ADR-0032 §2.1 —
    persisted runtime toggle for the remote MCP HTTP surface; default OFF).

v0.5-ADR-0033: vault_state.mcp_access_token_hash + vault_state.mcp_allow_without_token added
    in Alembic migration 0012 (ADR-0033 §2.1/§2.3 — UI-settable token as salted PBKDF2 hash;
    allow-without-token flag for private-source access; both default fail-closed).

v0.6-ADR-0040: vault_state.clip_enabled_db + vault_state.clip_access_token +
    vault_state.clip_allowed_origins_db added in Alembic migration 0015 (ADR-0040 §3 —
    runtime configuration for the web clipper ingress; DB wins over CLIP_* env when set).

v0.6-ADR-0041: vault_state.searxng_url_db + vault_state.searxng_categories_db +
    vault_state.searxng_max_queries_db added in Alembic migration 0016 (ADR-0041 §3 —
    runtime configuration for the SearXNG web-search backend; DB wins over SEARXNG_URL /
    DEEP_RESEARCH_* env when set; URL is NOT a secret and IS returned by GET /web-search/config).

v0.6-ADR-0043: vault_state.cli_oauth_token added in Alembic migration 0017 (ADR-0043 §2.2 —
    plaintext Claude subscription OAuth token for the CLI provider; DB wins over env when set;
    stored plaintext because it is replayed outbound to the spawned CLI — a hash cannot be
    replayed; §12 narrowly amended for this one credential; NEVER logged or returned).

W7 (security hardening): vault_state.cli_oauth_token_encrypted (BYTEA) added in Alembic
    migration 0027. Fernet-encrypted at rest (SYNAPSE_SECRET_KEY). The legacy plaintext
    column (cli_oauth_token) is kept for one migration cycle for rollback safety; the write
    path now stores exclusively in cli_oauth_token_encrypted; the read path prefers the
    encrypted column and falls back to cli_oauth_token with a security warning (operator
    migration path). cli_oauth_token will be dropped in a future cleanup migration.

    clip_access_token (migration 0015) stores a PBKDF2-SHA256 hash (ADR-0040 §2.2 amendment
    — NOT plaintext as the original migration comment states). The column type remains TEXT
    for the hash string; no ciphertext column is needed because the hash is one-way and
    sufficient for bearer-token verification (compare-then-verify; never replay).

Run `make er` to regenerate docs/er/schema.mmd from this file (I8).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Double,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Shared declarative base for all Synapse models."""


class Page(Base):
    """
    One row per source file under vault/raw/sources/.

    Identity: id == qdrant_point_id (ADR-0002) — the UUID used as the Qdrant point id
    so the two stores are joined by a stable, O(1) key.

    Soft-delete: deleted_at IS NULL means the page is live; setting deleted_at
    tombstones the row while retaining metadata for cascade-delete (F13) and audit.
    The Qdrant point is hard-deleted on soft-delete (ADR-0002 / ADR-0005).

    Change detection: source_mtime_ns is the cheap fast-path gate (ADR-0001);
    content_hash is the authoritative equality signal.  Both are required by the
    mtime-then-hash policy.
    """

    __tablename__ = "pages"

    # ── Primary key ───────────────────────────────────────────────────────────
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Page identity; also the Qdrant point id (ADR-0002)",
    )

    # ── Vault ─────────────────────────────────────────────────────────────────
    vault_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Logical vault identifier — from VAULT_ID env var",
    )

    # ── Filesystem ────────────────────────────────────────────────────────────
    file_path: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Relative path under vault/raw/sources/; join key to filesystem",
    )

    # ── K6 frontmatter (tolerant: missing → NULL, no exception) ──────────────
    title: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="YAML frontmatter 'title'; NULL if absent (K6, AC-K6-2/3)",
    )

    # 'type' is a Python keyword; map to column 'type' explicitly
    page_type: Mapped[str | None] = mapped_column(
        "type",
        Text,
        nullable=True,
        comment="YAML frontmatter 'type'; NULL if absent (K6)",
    )

    sources: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="YAML frontmatter 'sources[]' as JSONB array; NULL if absent (K6)",
    )

    tags: Mapped[list[str] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment=(
            "YAML frontmatter 'tags[]' as JSONB array; NULL if absent (K6 navigation, "
            "nashsu/llm_wiki parity). Mirrors `sources` storage. Migration 0018."
        ),
    )

    generation_key: Mapped[str | None] = mapped_column(
        String(96),
        nullable=True,
        comment=(
            "Stable identity for corpus-derived comparison/synthesis pages; mirrored as "
            "synapse_generation_key in YAML. NULL for ordinary/legacy pages (ADR-0074, 0031)."
        ),
    )

    # ── Change-detection ──────────────────────────────────────────────────────
    content_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="sha256 hex of raw file bytes — authoritative change signal (ADR-0001)",
    )

    source_mtime_ns: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="st_mtime_ns at last confirmed index — cheap fast-path gate (ADR-0001)",
    )

    # ── Qdrant join ───────────────────────────────────────────────────────────
    qdrant_point_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        comment="Qdrant point id == pages.id; explicit to allow deliberate divergence later",
    )

    # ── FA2 layout coordinates (v0.3, ADR-0013 / AQ-6) ──────────────────────
    x: Mapped[float | None] = mapped_column(
        Double,
        nullable=True,
        comment="FR x-coordinate (DOUBLE PRECISION); NULL until first layout (ADR-0013)",
    )

    y: Mapped[float | None] = mapped_column(
        Double,
        nullable=True,
        comment="FR y-coordinate (DOUBLE PRECISION); NULL until first layout (ADR-0013)",
    )

    # ── Louvain community id (G-P0-2, migration 0020) ────────────────────────
    community: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=(
            "Louvain community id, re-numbered by size (largest=0). "
            "NULL until first GraphEngine.recompute() after migration 0020 (G-P0-2). "
            "Persisted alongside x/y; exposed in GET /graph nodes (I2)."
        ),
    )

    # ── Manual position pin (Feature A) ──────────────────────────────────────
    pinned: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("false"),
        default=False,
        comment=(
            "True when the user manually positioned this node via PATCH /pages/{id}/position. "
            "Engine preserves pinned coords across FR recomputes (Feature A)."
        ),
    )

    # ── Soft delete ───────────────────────────────────────────────────────────
    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment="NULL = live; set = soft-deleted (ADR-0005); Qdrant point hard-deleted",
    )

    # ── Audit timestamps ──────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time",
    )

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Updated on every upsert (AC-WATCH-3)",
    )

    # ── Constraints & indexes ─────────────────────────────────────────────────
    __table_args__ = (
        # Enforce one live row per (vault, path) — partial unique index (ADR-0005)
        Index(
            "uix_pages_vault_file_path_live",
            "vault_id",
            "file_path",
            unique=True,
            postgresql_where=sa_text("deleted_at IS NULL"),
        ),
        Index(
            "uix_pages_vault_generation_key_live",
            "vault_id",
            "generation_key",
            unique=True,
            postgresql_where=sa_text("deleted_at IS NULL AND generation_key IS NOT NULL"),
            sqlite_where=sa_text("deleted_at IS NULL AND generation_key IS NOT NULL"),
        ),
    )

    def __repr__(self) -> str:
        return f"<Page id={self.id} path={self.file_path!r} deleted={self.deleted_at is not None}>"


class VaultState(Base):
    """
    One row per vault; holds the monotonic data_version debounce signal (I2, ADR-0005).

    Seeded on startup (idempotent — one row per vault_id) with data_version = 0.
    Incremented +1 only on a successful content-changing upsert ingest.
    Never decremented: startup, restart, deletion, duplicate-skip, and GET requests
    leave it unchanged (AC-F16dv-4).

    Column notes (ADR cross-references):
      remote_mcp_enabled         — ADR-0032, migration 0011. Runtime ON/OFF for HTTP MCP surface.
      mcp_access_token_hash      — ADR-0033, migration 0012. PBKDF2 hash; never plaintext.
      mcp_allow_without_token    — ADR-0033, migration 0012. Private-source token-less exemption.
      remote_mcp_write_enabled   — ADR-0072, migration 0030. Nullable runtime toggle for write
                                   tools on the HTTP surface. NULL = use MCP_REMOTE_WRITE_ENABLED
                                   env; non-NULL DB value is authoritative (DB-wins-else-env).
    """

    __tablename__ = "vault_state"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Row identity",
    )

    vault_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="One row per vault; from VAULT_ID env var",
    )

    data_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="Monotonic; +1 per successful upsert ingest (AC-F16dv-2/4); FA2 debounce signal",
    )

    remote_mcp_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa_text("false"),
        comment=(
            "Runtime toggle for the remote (HTTP) MCP surface (ADR-0032 §2.1). "
            "Default OFF; requires MCP_AUTH_TOKEN to be set before enabling. "
            "Persisted here; read into RemoteMcpFlag cache in main.py at startup."
        ),
    )

    # ── ADR-0033: UI-settable MCP access token (hashed) + allow-without-token flag ─
    mcp_access_token_hash: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment=(
            "Salted PBKDF2-HMAC-SHA256 hash of the UI-set MCP access token (ADR-0033 §2.1). "
            "Format: pbkdf2_sha256$<iters>$<salt_b64>$<hash_b64>. "
            "NULL = no UI token; env MCP_AUTH_TOKEN is the bootstrap fallback. "
            "NEVER stores plaintext. Token shown once at generation time (one-time reveal). "
            "DB-hash takes precedence over env bootstrap when set. "
            "Migration 0012."
        ),
    )

    mcp_allow_without_token: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa_text("false"),
        comment=(
            "When ON: PRIVATE sources (loopback/CGNAT/RFC1918/link-local/ULA) may reach "
            "/mcp/server without a bearer token (ADR-0033 §2.3). "
            "PUBLIC sources (Cloudflare tunnel — CF-Connecting-IP/CF-Ray) are NEVER "
            "exempted regardless of this flag (fail-safe by construction). "
            "Default false — fail-closed. Migration 0012."
        ),
    )

    # ── ADR-0072: Remote MCP write tools runtime toggle ───────────────────────────
    remote_mcp_write_enabled: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        default=None,
        comment=(
            "Runtime toggle for write tools (write_page, resolve_review, "
            "trigger_source_rescan) on the HTTP MCP surface (ADR-0072 §1). "
            "NULL = not set in DB; env MCP_REMOTE_WRITE_ENABLED is the fallback. "
            "When NOT NULL, DB value is authoritative (DB-wins-else-env). "
            "Always-register-guard model: write tools are always listed on the HTTP "
            "surface but each body checks the effective flag at call time (ADR-0072 §3). "
            "Migration 0030."
        ),
    )

    # ── ADR-0040: Web clipper runtime configuration ────────────────────────────────
    clip_enabled_db: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        default=None,
        comment=(
            "Runtime enabled-gate for POST /clip ingress (ADR-0040 §3). "
            "NULL = not set in DB; env CLIP_ENABLED is the fallback. "
            "When NOT NULL, DB value is authoritative (overrides CLIP_ENABLED env). "
            "Migration 0015."
        ),
    )

    clip_access_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment=(
            "PBKDF2-SHA256 hash of the bearer token for POST /clip (ADR-0040 §2.2 amendment). "
            "NOTE: the original migration 0015 comment says 'plaintext' — that is stale. "
            "The write path (PUT /clip/config rotate_token) stores the PBKDF2 hash (never the "
            "raw token). The raw token is shown exactly once at generation time in "
            "generated_token (never stored). Verification uses constant-time _verify_token(). "
            "NULL = no DB token; fall back to CLIP_TOKEN env bootstrap or none. "
            "When set, DB value wins over CLIP_TOKEN env. "
            "NEVER logged. Migration 0015."
        ),
    )

    clip_allowed_origins_db: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment=(
            "Comma-separated Origin allowlist for POST /clip (ADR-0040 §3). "
            "NULL = fall back to CLIP_ALLOWED_ORIGINS env var. "
            "When set, DB value wins over env. Migration 0015."
        ),
    )

    # ── ADR-0043 + W7: CLI subscription OAuth token ──────────────────────────────
    cli_oauth_token: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment=(
            "DEPRECATED write path (W7 migration 0027). "
            "Legacy plaintext CLI subscription OAuth token column (ADR-0043 §2.1 original). "
            "Kept for rollback safety — migration 0027 nulls this column after encrypting the "
            "value into cli_oauth_token_encrypted. The read path falls back to this column "
            "with a security warning if cli_oauth_token_encrypted is NULL (operator migration "
            "path when SYNAPSE_SECRET_KEY was absent at migration time). "
            "Will be dropped in a future cleanup migration. Migration 0017."
        ),
    )

    cli_oauth_token_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
        default=None,
        comment=(
            "W7 (ADR-0043 amendment). Fernet-encrypted CLI subscription OAuth token "
            "(master key from SYNAPSE_SECRET_KEY env; app/secrets_crypto.py). "
            "Produced on the host by `claude setup-token` (prefix: sk-ant- + oat01-). "
            "NULL = no UI token; env `CLAUDE_CODE_OAUTH_TOKEN` / `CLAUDE_CODE_USE_SUBSCRIPTION` "
            "govern. "
            "When NOT NULL the DB value is authoritative: it is decrypted at startup and "
            "injected into the spawned `claude` CLI env as CLAUDE_CODE_OAUTH_TOKEN. "
            "Fernet AES-128-CBC + HMAC-SHA256. Plaintext NEVER stored/logged. "
            "Requires SYNAPSE_SECRET_KEY to store (PUT /provider/cli-auth returns 400 when "
            "key absent). Fail-closed on tampered ciphertext (decrypt returns None, cache "
            "loads None). Migration 0027."
        ),
    )

    # ── P3-e: web-search cloud provider API keys (ADR-0071, encrypted at rest) ───
    web_search_api_keys_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
        default=None,
        comment=(
            "P3-e (ADR-0071). Fernet-encrypted JSON map {provider: api_key} for the opt-in cloud "
            "web-search providers (tavily/serpapi/firecrawl/brave). Master key from "
            "SYNAPSE_SECRET_KEY env (app/secrets_crypto.py). NULL = no UI keys; env "
            "`{PROVIDER}_API_KEY` govern. When a provider's key is present here the DB value wins "
            "over env. Plaintext NEVER stored/logged/returned (GET exposes only a masked posture). "
            "Requires SYNAPSE_SECRET_KEY to store (PUT /web-search/provider-keys → 400 when "
            "absent). Fail-closed on tampered ciphertext. Migration 0029."
        ),
    )

    # ── ADR-0041: SearXNG web-search runtime configuration ───────────────────────
    searxng_url_db: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment=(
            "Runtime SearXNG base URL (ADR-0041 §3). "
            "NULL = not set in DB; fall back to SEARXNG_URL env var. "
            "When set, DB value wins over env. "
            "NOT a secret — returned by GET /web-search/config (no masking). "
            "Migration 0016."
        ),
    )

    searxng_categories_db: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        default=None,
        comment=(
            "Comma-separated SearXNG categories (ADR-0041 §3). "
            "NULL = not set in DB; fall back to env / code defaults. "
            "When set, DB value wins over env. "
            "Migration 0016."
        ),
    )

    searxng_max_queries_db: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=None,
        comment=(
            "Max SearXNG queries per deep-research iteration (ADR-0041 §3). "
            "NULL = not set in DB; fall back to DEEP_RESEARCH_MAX_QUERIES env. "
            "When set, DB value wins over env. "
            "Migration 0016."
        ),
    )

    # ── ADR-0081: per-vault AI output language (llm_wiki onboarding parity) ────────
    output_language: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        default=None,
        comment=(
            "Mandatory AI output language chosen at vault creation (ADR-0081, "
            "llm_wiki create-dialog parity). ISO-639-1 code (e.g. 'en', 'it'). "
            "NULL = auto (detect from source content, pre-1.7.0 vaults). "
            "Read by ingest prompts (MANDATORY OUTPUT LANGUAGE directive) and by "
            "the writer language guard. Migration 0032."
        ),
    )

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last bump time",
    )

    __table_args__ = (UniqueConstraint("vault_id", name="uq_vault_state_vault_id"),)

    def __repr__(self) -> str:
        return f"<VaultState vault_id={self.vault_id!r} data_version={self.data_version}>"


class ProviderConfig(Base):
    """
    F17 inference-provider selection per scope (ADR-0008 §2). Resolution precedence (most
    specific wins, done by the ConfigResolver — backend-engineer): operation+vault > vault >
    global. A missing global row is a HARD configuration error, never a silent default
    backend (I6 — "never hardcode a provider").

    Secrets: env-var keys (ANTHROPIC_API_KEY / OPENAI_API_KEY) remain the default (§12,
    ADR-0008 §3). W1 (F17, §12 amendment) additionally allows a UI-supplied per-vendor key,
    stored in `api_key_encrypted` ENCRYPTED AT REST (Fernet, master key from SYNAPSE_SECRET_KEY;
    app/secrets_crypto.py). The plaintext is NEVER stored and NEVER returned by any endpoint —
    GET exposes only `api_key_configured` + a masked hint. `model_id` values live ONLY in DB
    rows (seeded by the Alembic data migration), never as literals in app code (AC-F17-8).
    `provider_name`/`model_id` are config, not routing inputs (I6 routing is by
    capabilities().supports_agentic_loop).
    """

    __tablename__ = "provider_config"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Row identity",
    )

    scope: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="global | vault | operation (ADR-0008 §2)",
    )

    operation: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="ingest | chat | lint; NULL unless scope='operation' (AQ-v0.2-5)",
    )

    vault_id: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        comment="NULL at global scope; required at vault/operation scope",
    )

    provider_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="local | api | cli — selects the InferenceProvider backend (I6)",
    )

    model_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Model name (e.g. claude-sonnet-4-6); value lives ONLY in DB rows (AC-F17-8)",
    )

    base_url: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="OpenAI-compatible endpoint for ApiProvider; NULL for Anthropic/local default",
    )

    api_key_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary,
        nullable=True,
        comment=(
            "W1 (F17, §12 amendment). Fernet-encrypted UI-supplied provider API key "
            "(master key from SYNAPSE_SECRET_KEY env; app/secrets_crypto.py). "
            "NULL = no UI key; the provider layer falls back to env-var keys "
            "(ANTHROPIC_API_KEY / OPENAI_API_KEY). The plaintext is NEVER stored and NEVER "
            "returned by any endpoint (GET exposes only api_key_configured + a masked hint). "
            "Migration 0026."
        ),
    )

    reasoning_effort: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "W1 (F17). Per-provider reasoning/thinking effort: "
            "auto | off | low | medium | high | max | custom. "
            "NULL/auto = provider default (no reasoning override). Threaded into the ApiProvider "
            "where the backend supports it (Anthropic extended thinking / OpenAI-compatible "
            "reasoning_effort); degrade-safe/ignored when unsupported. Migration 0026."
        ),
    )

    max_iter: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
        server_default=sa_text("3"),
        comment="Orchestrated-loop iteration cap (I7, ADR-0009)",
    )

    token_budget: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60000,
        server_default=sa_text("60000"),
        comment="Loop token budget (I7); 60000 orchestrated / 100000 cli (ADR-0009)",
    )

    is_fallback: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa_text("false"),
        comment="Marks the single fallback row for a scope (ADR-0009 §fallback)",
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time",
    )

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Updated on every change",
    )

    def __repr__(self) -> str:
        return (
            f"<ProviderConfig scope={self.scope!r} op={self.operation!r} "
            f"type={self.provider_type!r} model={self.model_id!r}>"
        )


class IngestRun(Base):
    """
    Per-run cost/convergence audit ledger (I7, ADR-0008 §4). System of record for cost
    auditing ("flag anomalies", "log total_cost_usd for every run"). `provider_name`/`model_id`
    are AUDIT METADATA ONLY — never read back into a routing decision (I6).
    """

    __tablename__ = "ingest_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Row identity",
    )

    vault_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Vault this run belongs to",
    )

    page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id"),
        nullable=True,
        comment="Originating source page; NULL on a pre-write failure",
    )

    provider_name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Provider class name (e.g. OllamaProvider) — AUDIT ONLY, never routed on (I6)",
    )

    provider_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="local | api | cli (audit)",
    )

    model_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Resolved model used (audit)",
    )

    route: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="orchestrated | delegated (capability-aware routing outcome)",
    )

    max_iter_used: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="Iterations actually consumed (1..max_iter); 0 for delegated",
    )

    total_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="input+output tokens across all iterations (I7)",
    )

    total_cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="0.0000 for local/cli (ADR-0009); logged per run (I7)",
    )

    converged: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa_text("false"),
        comment="True if a valid batch was produced within max_iter",
    )

    cost_anomaly: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa_text("false"),
        comment="True if total_cost_usd > 1.00 (ADR-0009 §3)",
    )

    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Run start time",
    )

    finished_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Run finish time",
    )

    # ── v0.4 view fields (ADR-0018 §7, migration 0006) ────────────────────────

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="completed",
        server_default=sa_text("'completed'"),
        comment=(
            "Run lifecycle state: running | completed | failed | converged_false. "
            "Backfilled from converged for historical rows (ADR-0018 §7)."
        ),
    )

    pages_created: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment=(
            "Number of wiki pages persisted during this run. "
            "0 for historical rows; set by orchestrator on new runs (ADR-0018 §7)."
        ),
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Human-readable error description for failed runs; "
            "NULL for completed/running/converged_false rows (ADR-0018 §7)."
        ),
    )

    page_type_counts: Mapped[dict[str, int] | None] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"),
        nullable=True,
        comment=(
            "Per-PageType pages created by this run for generation diagnostics; "
            "NULL for legacy rows (ADR-0073, migration 0031)."
        ),
    )

    # ── v0.6 queue fields (ADR-0046, migration 0021) ──────────────────────────

    source_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Relative raw source path (raw/sources/…) the run is ingesting. "
            "NULL for historical rows written before migration 0021 (ADR-0046 §1)."
        ),
    )

    retry_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment=(
            "Times this source has been retried. Enforces MAX_INGEST_RETRIES=3 (I7, ADR-0046 §5). "
            "Incremented by the queue manager on each re-dispatch; 0 for the initial attempt."
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<IngestRun provider={self.provider_name!r} route={self.route!r} "
            f"converged={self.converged} status={self.status!r} cost=${self.total_cost_usd}>"
        )


class Conversation(Base):
    """
    F6 chat conversation — one row per persistent multi-turn chat thread (ADR-0019 §2.5).

    Persisted in Postgres (system of record, ADR-0002) so AC-F6-1 ("a page refresh restores
    the last active conversation") holds across devices/LiveSync. Soft-deletable (ADR-0005
    pattern): deleted_at IS NULL means live. Ordered for the conversation list by
    updated_at DESC (bumped on each turn — drives the "last active" restore).

    Cost (I7) is NOT held here: per-message token/cost columns on `messages` are the durable
    chat-cost record. There is intentionally NO chat_runs table (ADR-0019 §2.2 / Do-NOT #9).
    """

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Conversation identity",
    )

    vault_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Logical vault scope (matches pages/edges pattern); from VAULT_ID env var",
    )

    title: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="User-set or first-prompt-derived title; NULL until set (ADR-0019 §2.5)",
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time",
    )

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Bumped on each chat turn; drives last-active-conversation restore (AC-F6-1)",
    )

    deleted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment="NULL = live; set = soft-deleted (ADR-0005 / ADR-0019 §2.5)",
    )

    __table_args__ = (
        # List query: live conversations for a vault, newest activity first (ADR-0019 §2.5).
        Index(
            "ix_conversations_vault_updated_live",
            "vault_id",
            "updated_at",
            postgresql_where=sa_text("deleted_at IS NULL"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<Conversation id={self.id} vault={self.vault_id!r} "
            f"title={self.title!r} deleted={self.deleted_at is not None}>"
        )


class ChatMessage(Base):
    """
    F6 chat message — one row per user/assistant/system message (ADR-0019 §2.5).

    `content` is stored RAW and UN-MUTATED, including any literal <think>…</think> span
    (AC-F7-2 / Do-NOT #7) — the streaming token/think split is a transport convenience only,
    re-derivable from this string at render time. `citations` is reserved for M5 (always []
    in M4). The per-message token/cost columns are the durable I7 chat-cost record (ADR-0019
    §2.2): 0.0000 for local/cli (ADR-0009).
    """

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Message identity",
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id"),
        nullable=False,
        comment="FK → conversations.id (ADR-0019 §2.5)",
    )

    role: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="'user' | 'assistant' | 'system' (ADR-0019 §2.5)",
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="RAW message content, incl. literal <think>…</think> un-mutated (AC-F7-2)",
    )

    citations: Mapped[list[Any] | None] = mapped_column(
        # JSONB on Postgres; plain JSON on SQLite (test in-memory engine) — same column on
        # Postgres, just renders portably for the unit-test SQLite path.
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment="RESERVED for M5 [n] citations; always [] in M4 (ADR-0019 §2.3)",
    )

    images: Mapped[list[Any] | None] = mapped_column(
        # JSONB on Postgres; plain JSON on SQLite (test in-memory engine).
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=True,
        comment=(
            "B2-C1 image attachments: [{mime, data_base64}] for user messages. "
            "NULL when no images attached. Stored so regenerate/history preserves images. "
            "Migration: 0024_messages_images."
        ),
    )

    provider_type: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Backend that produced an assistant msg (audit); NULL for user/system",
    )

    model_id: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Resolved model that produced an assistant msg (audit); NULL otherwise",
    )

    input_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="Prompt tokens for an assistant turn (I7 persistent cost record)",
    )

    output_tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="Completion tokens for an assistant turn (I7)",
    )

    total_cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="0.0000 for local/cli (ADR-0009); logged + returned in done event (I7)",
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time; messages ordered created_at ASC (ADR-0019 §2.5)",
    )

    __table_args__ = (
        # History read: ordered messages for one conversation (ADR-0019 §2.5).
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<ChatMessage id={self.id} conv={self.conversation_id} "
            f"role={self.role!r} tokens={self.input_tokens}/{self.output_tokens}>"
        )


class Link(Base):
    """
    K5 wikilink edge — one row per [[Target]] or [[Target|alias]] occurrence in a page.

    Parsed by app.wiki.links.parse_wikilinks() and persisted by persist_links() after each
    write_wiki_page() call (I1 — incremental, not a full-rescan). target_page_id is nullable
    and resolved lazily: it is NULL while the target page does not yet exist (dangling=True),
    and filled in once the target page is created (v0.3 graph resolution, ADR-0008 §5).

    The dangling flag is a denormalised convenience so the v0.2 warn-not-error path (AQ-v0.2-7)
    can be checked without a join. A dangling link does NOT invalidate a batch (K5 / ADR-0007 §5).
    """

    __tablename__ = "links"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Row identity",
    )

    source_page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id"),
        nullable=False,
        comment="FK → pages.id; the page that contains the wikilink (K5)",
    )

    target_title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="The [[Target]] title string as written (K5)",
    )

    target_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id"),
        nullable=True,
        comment="Resolved FK → pages.id; NULL while the target page does not exist (K5, v0.3)",
    )

    alias: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="The |alias part of [[Target|alias]], if present (K5)",
    )

    dangling: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa_text("false"),
        comment="True when target_page_id is unresolved (AC-K5-5); warn-not-error path",
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time",
    )

    def __repr__(self) -> str:
        alias_part = f"|{self.alias}" if self.alias else ""
        return (
            f"<Link [[{self.target_title}{alias_part}]] "
            f"from={self.source_page_id} dangling={self.dangling}>"
        )


class Edge(Base):
    """
    v0.3 graph edge — one row per weighted undirected page pair (ADR-0012, AQ-5).

    Computed by GraphEngine.recompute() from the 4-signal additive formula:
      weight = 3·direct_link_count + 4·shared_source_count + 1.5·adamic_adar + 1·same_type
    (ADR-0012 / v0.3-architecture §2).

    Persisted iff weight > 0 (sparse table). Replaced as a whole on each recompute via
    delete-then-insert inside a single transaction (AQ-5, ADR-0013 §algorithm step 6).

    The pair is stored **canonically** (smaller UUID first by string comparison) so the
    unique constraint on (vault_id, source_page_id, target_page_id) is always effective.

    signals JSONB holds the per-signal breakdown {direct, source, aa, type} for audit /
    independent-signal QA assertions (AC-F4-1(e)).
    """

    __tablename__ = "edges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Row identity",
    )

    vault_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Scope edges per vault (matches pages/vault_state pattern)",
    )

    source_page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id"),
        nullable=False,
        comment=(
            "Unordered pair stored canonically (smaller UUID first by string sort). "
            "FK → pages.id (ADR-0012 / AQ-5)"
        ),
    )

    target_page_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pages.id"),
        nullable=False,
        comment="FK → pages.id; target of the undirected pair (ADR-0012)",
    )

    weight: Mapped[float] = mapped_column(
        Double,
        nullable=False,
        comment=(
            "Additive 4-signal weight > 0 (ADR-0012): "
            "3·direct + 4·source_overlap + 1.5·adamic_adar + 1·same_type"
        ),
    )

    signals: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment='Per-signal breakdown {"direct","source","aa","type"} for audit (AC-F4-1(e))',
    )

    kind: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        comment=(
            'Structural edge discriminator: "link" (direct wikilink present) | '
            '"source" (shared provenance only). ADR-0016 §4. '
            "NULL for rows written before migration 0004 (treated as link)."
        ),
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time (set on each recompute batch)",
    )

    # Relationships (for ORM convenience; not required by the graph engine)
    source_page: Mapped[Page] = relationship("Page", foreign_keys=[source_page_id], lazy="raise")
    target_page: Mapped[Page] = relationship("Page", foreign_keys=[target_page_id], lazy="raise")

    # Constraints & indexes
    __table_args__ = (
        # Unique on the canonicalised undirected pair within a vault
        UniqueConstraint(
            "vault_id",
            "source_page_id",
            "target_page_id",
            name="uq_edges_vault_pair",
        ),
        # Indexes both endpoints for GET /graph reads and cascade cleanup (F13, v0.5)
        Index("ix_edges_source_page_id", "source_page_id"),
        Index("ix_edges_target_page_id", "target_page_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<Edge {self.source_page_id}↔{self.target_page_id} "
            f"w={self.weight:.3f} vault={self.vault_id!r}>"
        )


class ImportSchedule(Base):
    """
    M4-EXT scheduled folder import — one row per vault (ADR-0020 §4.1).

    Holds the configuration (enabled, source_dir, frequency) and the last-run status
    (last_run_at, last_status, last_imported_count, last_error) so the UI shows
    "last scan: 5 min ago, 3 imported" across restarts.

    `source_dir` is a **container-visible** absolute path (e.g. /import); the backend
    validates it with an os.path.isdir check. The scheduler re-reads this row each tick
    so a PUT /import-schedule change takes effect on the next tick without a restart.

    frequency enum values map to seconds server-side (I7 — no runaway interval):
      15m → 900 | 1h → 3600 | 6h → 21600 | daily → 86400
    """

    __tablename__ = "import_schedules"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Row identity",
    )

    vault_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Logical vault identifier — one schedule row per vault (UNIQUE)",
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa_text("false"),
        comment="Scheduler is a no-op while false (ADR-0020 §4.1)",
    )

    source_dir: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Container-visible absolute path to scan (e.g. /import). "
            "NULL until set by the user. Must be mounted into the container (ADR-0020 §7)."
        ),
    )

    frequency: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="1h",
        server_default=sa_text("'1h'"),
        comment="Scan interval enum: '15m' | '1h' | '6h' | 'daily' (I7 — bounded, ADR-0020 §4.1)",
    )

    # ── P3-c: wider Source-Watch file types (v1.5 LLM Wiki parity) ──────────────
    allowed_extensions: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Comma-separated file extensions the scheduled scan imports (e.g. '.pdf,.csv'). "
            "NULL → default wider set (text + all extractable). P3-c (v1.5, ADR-0068)."
        ),
    )

    excluded_folders: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Comma-separated folder names skipped during the scan (matched against path parts). "
            "NULL → nothing excluded. P3-c (v1.5, ADR-0068)."
        ),
    )

    max_size_mb: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=(
            "Max file size in MB the scan will import; larger files are skipped (I7). "
            "NULL → no size cap. P3-c (v1.5, ADR-0068)."
        ),
    )

    last_run_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment="Timestamp of the last completed scan; NULL if never run",
    )

    last_status: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Outcome of the last scan: 'ok' | 'error' | 'running' | "
            "'skipped_disabled' | 'dir_missing' | NULL (never run)"
        ),
    )

    last_imported_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="Number of files copied (new/changed) during the last scan (ADR-0020 §4.3)",
    )

    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Human-readable error from the last failed scan; NULL on success",
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time",
    )

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Updated on every PUT or scan completion",
    )

    __table_args__ = (UniqueConstraint("vault_id", name="uq_import_schedules_vault_id"),)

    def __repr__(self) -> str:
        return (
            f"<ImportSchedule vault={self.vault_id!r} enabled={self.enabled} "
            f"freq={self.frequency!r} status={self.last_status!r}>"
        )


class DeepResearchRun(Base):
    """
    v0.5 F10 deep-research run — one row per run_deep_research() call (ADR-0024 §7.1).

    Bounds (max_iter, token_budget) are FROZEN at INSERT and never re-read mid-loop (I7).
    status defaults to 'running'; terminal values: converged | max_iter_reached |
    budget_exhausted | error. Never left 'running' on loop fall-through (Do-NOT #7).
    total_cost_usd: 0.0000 for local/cli (ADR-0009 convention).
    synthesis_page_id: FK → pages.id; NULL until _ingest_synthesis completes.

    Index: (vault_id, started_at DESC) mirrors ingest_runs (ADR-0024 §7.1).
    """

    __tablename__ = "deep_research_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Run identity",
    )

    vault_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Scope — string, no vaults table (AQ-v0.5-6, ADR-0024 §7.1)",
    )

    topic: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="The research topic provided by the caller",
    )

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="running",
        server_default=sa_text("'running'"),
        comment=(
            "running | converged | max_iter_reached | budget_exhausted | error. "
            "Defaults 'running'; terminal write always in finally (Do-NOT #7, ADR-0024 §3.2)"
        ),
    )

    max_iter: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Iteration cap FROZEN at INSERT from POST body → env default (AQ-v0.5-4)",
    )

    token_budget: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Token budget FROZEN at INSERT (AQ-v0.5-4, I7)",
    )

    iterations_used: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="Rounds consumed (1..max_iter); 0 until first round completes",
    )

    queries_used: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
        server_default=sa_text("'[]'"),
        comment="Array of every query issued, per round (AC-F10-4c)",
    )

    sources_fetched: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="Count of fetched candidate sources across all iterations",
    )

    converged: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=sa_text("false"),
        comment="True iff status == 'converged' (audit convenience, ADR-0024 §7.1)",
    )

    total_cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="I7 cost ledger; 0.0000 for local/cli (ADR-0009); $1 anomaly threshold",
    )

    synthesis_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="The synthesized markdown; NULL until step 5 completes (AC-F10-4c)",
    )

    synthesis_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        ForeignKey("pages.id"),
        nullable=True,
        comment="FK → pages.id created by the re-entrant ingest_file; NULL until done",
    )

    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Run start time",
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment="NULL while running; set in finally block (mirrors ingest_runs alias rule)",
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Populated only on status='error'; NULL otherwise",
    )

    # Relationships
    sources: Mapped[list[DeepResearchSource]] = relationship(
        "DeepResearchSource",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        # Paginated list query: (vault_id, started_at DESC) mirrors ingest_runs
        Index("ix_deep_research_runs_vault_started", "vault_id", "started_at"),
    )

    def __repr__(self) -> str:
        topic_short = self.topic[:40] if self.topic else ""
        return (
            f"<DeepResearchRun id={self.id} status={self.status!r} "
            f"vault={self.vault_id!r} topic={topic_short!r}>"
        )


class DeepResearchSource(Base):
    """
    v0.5 F10 per-source child row — one per fetched URL within a run (ADR-0024 §7.2).

    ON DELETE CASCADE from deep_research_runs (run.sources is the ORM relationship).
    relevance_score is optional/best-effort in Phase 2 (NULL allowed, ADR-0024 §11).
    fetched_content_md is capped at DEEP_RESEARCH_FETCH_MAX_CHARS (ADR-0024 §4).
    """

    __tablename__ = "deep_research_sources"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Row identity",
    )

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        ForeignKey("deep_research_runs.id", ondelete="CASCADE"),
        nullable=False,
        comment="FK → deep_research_runs.id (ON DELETE CASCADE)",
    )

    url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="The fetched source URL (from SearXNG hit)",
    )

    title: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Hit title from SearXNG; may be URL if no title available",
    )

    fetched_content_md: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Extracted markdown (capped at DEEP_RESEARCH_FETCH_MAX_CHARS); "
            "NULL on fetch failure (ADR-0024 §4 / Do-NOT #9)"
        ),
    )

    relevance_score: Mapped[float | None] = mapped_column(
        Numeric(6, 4),
        nullable=True,
        comment="Optional model/heuristic relevance; NULL in Phase 2 (ADR-0024 §11)",
    )

    iteration: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default=sa_text("1"),
        comment="Which round produced this source (1..max_iter); audit trail",
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time",
    )

    # Relationship back to the run
    run: Mapped[DeepResearchRun] = relationship(
        "DeepResearchRun",
        back_populates="sources",
        lazy="raise",
    )

    __table_args__ = (Index("ix_deep_research_sources_run_id", "run_id"),)

    def __repr__(self) -> str:
        return f"<DeepResearchSource run={self.run_id} url={self.url!r} iter={self.iteration}>"


class ReviewItem(Base):
    """
    v0.5 F9 HITL review queue — redesigned in ADR-0034 (Alembic migration 0013).

    PROPOSAL MODEL (ADR-0034 §3):
    Rows are PROPOSALS for follow-up work, not confirmations of auto-created pages.
    Pages are created on-demand only when the human takes the Create action (§5).

    vault_id: String identifier (no FK, no vaults table — AQ-v0.5-6).

    item_type enum-by-convention (5 values, ADR-0034 §3.1):
      missing-page   — a referenced-but-absent page the LLM found via dangling wikilink
      suggestion     — a research gap / follow-up the LLM identified
      contradiction  — a conflict with existing wiki content
      duplicate      — a possible name-collision with an existing page
      confirm        — the LLM wants human confirmation before proceeding

    status lifecycle (ADR-0034 §3.1; ADR-0044 §3.1 adds `dismissed`):
      pending        — awaiting human action (initial state)
      created        — Create action ran; page written via write_wiki_page (§5)
      skipped        — human chose Skip (considered and declined)
      dismissed      — human hid the item without acting (ADR-0044; distinct from skipped)
      deep_researched— human chose Deep Research; deep_research_run_id is set
      auto_resolved  — sweep auto-closed the item (Pass-1 or Pass-2), or human bulk mark-resolved

    ADR-0044 idempotency (§3): content_key is a stable FNV-1a digest; enqueue_review upserts
    on (vault_id, content_key) for the live (pending) set so re-ingest does not resurrect a
    skipped/dismissed item nor accumulate duplicates. `confirm` items carry content_key=NULL
    (never deduped). referenced_page_ids + search_queries carry contextual depth.

    page_id (RE-DOCUMENTED, same column):
      The review TARGET: existing page in conflict (contradiction/duplicate) or
      the source-context page (missing-page/suggestion). NULL when none applies.

    source_page_id:
      The page WHOSE INGEST produced this proposal (provenance). Distinct from page_id.

    proposed_title:
      The title the LLM proposes to create. Required for missing-page; advisory for others.
      Drives the lazy skeleton (§5.2) and rule-based sweep title match (§6.2).

    proposed_page_type:
      Inferred PageType (entity|concept|source|synthesis|comparison). NULL → heuristic at
      Create time (§5.2). `source` is never a valid Create target.

    proposed_dir:
      Target wiki/ subdir derived from proposed_page_type (display only; recomputed at Create).

    rationale:
      Short human-readable "why this matters". Replaces the old per-page questions.
      For `suggestion`: the gap/follow-up; for `contradiction`: conflict description.

    resolution:
      How the item closed: created|skipped|researched|rule_resolved|llm_resolved.
      NULL while pending.

    created_page_id:
      FK → pages.id; the page a successful Create produced. NULL otherwise.

    deep_research_run_id:
      FK → deep_research_runs.id; set when the Deep-Research action fires (AC-F10-5).

    Indexes:
      ix_review_items_vault_status_created: (vault_id, status, created_at) — paginated queue.
      ix_review_items_vault_proposed_title: (vault_id, proposed_title) — sweep title lookup.

    Event log — no per-page uniqueness constraint (ADR-0034 §3.2 / ADR-0025 §3.1 note).
    UUID type follows deep_research_runs pattern: UUID(as_uuid=True).with_variant(String(36)).
    """

    __tablename__ = "review_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Row identity",
    )

    vault_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment=(
            "Logical vault identifier — existing String (no FK, no vaults table). "
            "AQ-v0.5-6; ADR-0034 §3.1"
        ),
    )

    item_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "Proposal type (ADR-0034 §3.1 enum-by-convention, no DB CHECK): "
            "missing-page | suggestion | contradiction | duplicate | confirm. "
            "Old values (new_page/update_page/deep_research_candidate) are obsolete after "
            "migration 0013 left-shifts them to skipped."
        ),
    )

    proposal_origin: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="legacy",
        server_default=sa_text("'legacy'"),
        comment=(
            "Structured proposal provenance: rule | ai | corpus | system | lint | legacy. "
            "Application-validated; legacy is the migration/backward-compatible default "
            "(ADR-0073, migration 0031)."
        ),
    )

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="pending",
        server_default=sa_text("'pending'"),
        comment=(
            "Lifecycle (ADR-0034 §3.1; ADR-0044 adds dismissed): "
            "pending | created | skipped | dismissed | deep_researched | auto_resolved. "
            "Defaults 'pending'. (approved is gone; Create produces created.)"
        ),
    )

    # ── Review target: the existing page in conflict or source context ────────
    page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        ForeignKey("pages.id"),
        nullable=True,
        comment=(
            "FK → pages.id; the review TARGET: existing page a contradiction/duplicate "
            "conflicts with, or source-context page for missing-page/suggestion. "
            "NULL when none applies. (ADR-0034 §3.1 — re-documented column)"
        ),
    )

    # ── Provenance: the page whose ingest produced this proposal ─────────────
    source_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        ForeignKey("pages.id"),
        nullable=True,
        comment=(
            "FK → pages.id; the page WHOSE INGEST produced this proposal (provenance). "
            "Distinct from page_id (the conflicting/target page). "
            "Lets the UI show 'proposed while ingesting X'. ADR-0034 §3.1 ADD."
        ),
    )

    # ── Lazy Create skeleton ──────────────────────────────────────────────────
    proposed_title: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "The title the LLM proposes to create "
            "(required for missing-page; advisory for others). "
            "Drives the lazy skeleton (ADR-0034 §5.2) and "
            "rule-based sweep title match (§6.2). ADR-0034 §3.1 ADD."
        ),
    )

    proposed_page_type: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Inferred PageType for the lazy skeleton: entity|concept|source|synthesis|comparison. "
            "NULL → heuristic applied at Create time (ADR-0034 §5.2). "
            "source is never a valid Create target. ADR-0034 §3.1 ADD."
        ),
    )

    proposed_dir: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Target wiki/ subdir derived from proposed_page_type (DISPLAY ONLY). "
            "Recomputed from the final type at Create time — "
            "never trusted blindly (ADR-0034 §5.2). ADR-0034 §3.1 ADD."
        ),
    )

    # ── Human-readable rationale (replaces pre_generated_query) ──────────────
    rationale: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Short human-readable 'why this matters' (ADR-0034 §3.1 ADD). "
            "Replaces the old per-page follow-up questions (pre_generated_query is DROPPED). "
            "For suggestion: the gap/follow-up; for contradiction: the conflict description; "
            "for confirm: what needs confirming. Used as the topic hint for Deep Research."
        ),
    )

    # ── ADR-0044 §3.1: stable idempotency + contextual depth ──────────────────
    content_key: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "16-hex FNV-1a stable digest over "
            "vault_id + item_type + normalize(proposed_title) + (target_page_title|page_id) "
            "(ADR-0044 §3.2). Makes the queue idempotent across re-ingest: the same logical "
            "proposal keeps its content_key and therefore its status. NULL for `confirm` items "
            "(never deduped — every confirmation is a distinct human ask) and legacy rows. "
            "Migration 0019."
        ),
    )

    referenced_page_ids: Mapped[list[str] | None] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"),
        nullable=True,
        comment=(
            "JSON array of page-id STRINGS: the existing pages this proposal is contextually "
            "about (plural context set; ADR-0044 §2/§3.1). Bounded (≤ REVIEW_REFERENCED_PAGES_MAX, "
            "default 8). Distinct from page_id (single primary conflict) and source_page_id "
            "(provenance). Deliberately a JSON array, NOT a junction/FK — stale ids are filtered "
            "at render (ADR-0044 §9.2). NULL/[] when none. Migration 0019."
        ),
    )

    search_queries: Mapped[list[str] | None] = mapped_column(
        JSONB().with_variant(JSON, "sqlite"),
        nullable=True,
        comment=(
            "JSON array of ≤3 pre-generated web-search-query strings (ADR-0044 §2.3), produced by "
            "the SAME single proposal call (no extra provider call). Deep Research seeds its topic "
            "from search_queries[0]; the UI shows them on the card. NULL when none. Migration 0019."
        ),
    )

    # ── Terminal audit ────────────────────────────────────────────────────────
    resolution: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "How the item closed (ADR-0034 §3.1 ADD; ADR-0044 adds dismissed): "
            "created | skipped | dismissed | researched | rule_resolved | llm_resolved. "
            "NULL while pending. Complements status (status records *what* happened; "
            "resolution records *how* it was resolved)."
        ),
    )

    # ── Created page (lazy Create output) ────────────────────────────────────
    created_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        ForeignKey("pages.id"),
        nullable=True,
        comment=(
            "FK → pages.id; the page a successful Create action produced (ADR-0034 §5). "
            "NULL while status != 'created'. Distinct from page_id and source_page_id. "
            "ADR-0034 §3.1 ADD."
        ),
    )

    # ── Deep Research link ────────────────────────────────────────────────────
    deep_research_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        ForeignKey("deep_research_runs.id"),
        nullable=True,
        comment=(
            "FK → deep_research_runs.id; set when the Deep-Research action fires (AC-F10-5). "
            "NULL while status != 'deep_researched'. Unchanged from ADR-0025."
        ),
    )

    # ── Timestamps ────────────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time",
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment=(
            "Set on any terminal action "
            "(Create/Skip/Deep-Research/auto-resolve); NULL while pending."
        ),
    )

    reviewed_by: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "'web-ui' for human actions; 'auto-sweep' for the sweep (ADR-0034 §6.2/§6.3). "
            "NULL while pending."
        ),
    )

    __table_args__ = (
        # Paginated pending-queue read: WHERE vault_id=? AND status='pending' ORDER BY created_at
        Index(
            "ix_review_items_vault_status_created",
            "vault_id",
            "status",
            "created_at",
        ),
        # Rule-based sweep title match + duplicate-collision lookup (ADR-0034 §3.1 / §6.2)
        Index(
            "ix_review_items_vault_proposed_title",
            "vault_id",
            "proposed_title",
        ),
        # ADR-0044 §3.3: partial-unique idempotency index scoped to the live (pending) set.
        # Postgres enforces it; SQLite (unit tests) emulates it via enqueue_review's
        # read-before-write upsert (the application upsert is the portable contract —
        # mirrors the raw-SQL portability note in project memory). A terminal row with the
        # same content_key does NOT conflict (WHERE status IN ('pending')) — the upsert reads
        # it first and no-ops, respecting the human's prior decision.
        Index(
            "ix_review_items_vault_content_key_live",
            "vault_id",
            "content_key",
            unique=True,
            postgresql_where=sa_text("content_key IS NOT NULL AND status IN ('pending')"),
            sqlite_where=sa_text("content_key IS NOT NULL AND status IN ('pending')"),
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<ReviewItem id={self.id} type={self.item_type!r} "
            f"status={self.status!r} vault={self.vault_id!r} "
            f"title={self.proposed_title!r}>"
        )


class LintRun(Base):
    """
    v0.6 K2 lint-fix loop — one row per run_lint_scan() call (ADR-0037 §3, Alembic 0014).

    The lint scan is the third Karpathy core operation (Ingest · Query · Lint): a periodic,
    BOUNDED, HUMAN-GATED health check of the wiki. The scan PRODUCES findings (proposals); it
    NEVER auto-applies fixes (the human gate is apply_lint_fix — ADR-0037 §5).

    Bounds (max_iter, token_budget) are FROZEN at INSERT and never re-read mid-loop (I7),
    mirroring deep_research_runs. status defaults to 'running'; terminal values: completed |
    error. Never left 'running' on loop fall-through (terminal write always in finally).
    total_cost_usd: 0.0000 for local/cli (ADR-0009 convention); $1 anomaly threshold (I7).

    vault_id: String identifier (no FK, no vaults table — AQ-v0.5-6).
    UUID type follows deep_research_runs pattern: UUID(as_uuid=True).with_variant(String(36)).
    Index: (vault_id, created_at DESC) mirrors deep_research_runs / ingest_runs.
    """

    __tablename__ = "lint_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Run identity",
    )

    vault_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Scope — string, no vaults table (AQ-v0.5-6, ADR-0037 §3.1)",
    )

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="running",
        server_default=sa_text("'running'"),
        comment=(
            "running | completed | error. Defaults 'running'; terminal write always in finally "
            "(never left 'running' on fall-through — ADR-0037 §4)."
        ),
    )

    max_iter: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Iteration cap FROZEN at INSERT from POST body → LINT_MAX_ITER default (I7)",
    )

    token_budget: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="Token budget FROZEN at INSERT from POST body → LINT_TOKEN_BUDGET default (I7)",
    )

    iterations_used: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="Semantic rounds consumed (0..max_iter); 0 for deterministic-only scans",
    )

    findings_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="Number of findings emitted by this scan (capped at LINT_MAX_FINDINGS)",
    )

    total_cost_usd: Mapped[float] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
        comment="I7 cost ledger; 0.0000 for local/cli (ADR-0009); $1 anomaly threshold",
    )

    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Run start time",
    )

    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment="NULL while running; set in finally block (mirrors deep_research_runs)",
    )

    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Populated only on status='error'; NULL otherwise",
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time (mirrors deep_research_runs/review_items)",
    )

    # Relationships
    findings: Mapped[list[LintFinding]] = relationship(
        "LintFinding",
        back_populates="run",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    __table_args__ = (
        # Paginated list query: (vault_id, created_at DESC) mirrors deep_research_runs
        Index("ix_lint_runs_vault_created", "vault_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<LintRun id={self.id} status={self.status!r} "
            f"vault={self.vault_id!r} findings={self.findings_count}>"
        )


class LintFinding(Base):
    """
    v0.6 K2 lint finding — one PROPOSAL produced by a lint scan (ADR-0037 §3, Alembic 0014).

    Mirrors review_items semantics: a finding is a PROPOSAL the human reviews; it is NEVER
    auto-applied (the human gate is apply_lint_fix — ADR-0037 §5).

    category enum-by-convention (6 values, ADR-0037 §3.1, no DB CHECK):
      orphan-page     — graph in-degree 0 (deterministic via the graph engine; flag-only fix)
      missing-xref    — a page that should link to an existing page but does not (LLM;
                        apply reuses the wikilink-enrichment seam — ADR-0036)
      contradiction   — conflicting claims across pages (LLM; flag-only)
      stale-claim     — superseded information (LLM; flag-only)
      missing-page    — a concept mentioned but with no page (LLM; apply delegates to the
                        lazy-generation seam used by review.create_page_from_review — ADR-0034)
      broken-wikilink — a dangling [[link]] in the DB (deterministic from links.dangling=True;
                        L2 / ADR-0037 B1; apply rewrites the body — L3)

    severity enum-by-convention: info | warning | error (advisory; display ordering).

    status lifecycle (ADR-0037 §3.1):
      open      — awaiting human action (initial state)
      applied   — apply_lint_fix ran a safe/bounded fix (ADR-0037 §5)
      dismissed — human chose to dismiss (status change only)

    target_page_id: FK → pages.id; the page the finding is about (orphan/missing-xref/stale).
      NULL when none applies (e.g. a missing-page finding about a not-yet-existing title).
    proposed_action: human-readable description of the fix apply_lint_fix would attempt.

    vault_id: String identifier (no FK, no vaults table — AQ-v0.5-6).
    UUID type follows review_items / deep_research_runs pattern.
    """

    __tablename__ = "lint_findings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Finding identity",
    )

    lint_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        ForeignKey("lint_runs.id", ondelete="CASCADE"),
        nullable=False,
        comment="FK → lint_runs.id (ON DELETE CASCADE)",
    )

    vault_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Denormalised vault scope (matches the run's vault; AQ-v0.5-6)",
    )

    category: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "Finding category (ADR-0037 §3.1 enum-by-convention, no DB CHECK): "
            "orphan-page | missing-xref | contradiction | stale-claim | missing-page."
        ),
    )

    severity: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="warning",
        server_default=sa_text("'warning'"),
        comment="info | warning | error (advisory; display ordering). ADR-0037 §3.1.",
    )

    target_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        ForeignKey("pages.id"),
        nullable=True,
        comment=(
            "FK → pages.id; the page the finding is about. NULL when none applies "
            "(e.g. a missing-page finding about a not-yet-existing title). ADR-0037 §3.1."
        ),
    )

    target_title: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "The page title the finding concerns (missing-page: title to create; "
            "missing-xref: the existing page that should be linked). ADR-0037 §3.1."
        ),
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Human-readable 'what is wrong' (ADR-0037 §3.1).",
    )

    proposed_action: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Human-readable description of the fix apply_lint_fix would attempt. "
            "NULL for flag-only findings (contradiction/stale-claim/orphan). ADR-0037 §3.1/§5."
        ),
    )

    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="open",
        server_default=sa_text("'open'"),
        comment="open | applied | dismissed. Defaults 'open' (ADR-0037 §3.1).",
    )

    resolution_note: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="How the finding was resolved (apply outcome / dismiss reason). NULL while open.",
    )

    # L2 — suggested target fields (Alembic 0024, ADR-0037 B1).
    # Populated at scan time for broken-wikilink findings via the tolerant resolver
    # (exact → case-insensitive → slug). NULL for all other categories.
    suggested_target: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Title of the best matching existing page found by the tolerant resolver at "
            "broken-wikilink scan time (exact → case-insensitive → slug). "
            "NULL when no suggestion or for non-broken-wikilink categories. L2 / ADR-0037 B1."
        ),
    )

    suggested_page_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True).with_variant(String(36), "sqlite"),
        ForeignKey("pages.id"),
        nullable=True,
        comment=(
            "FK → pages.id; the live page whose title is `suggested_target`. "
            "NULL when no suggestion or for non-broken-wikilink categories. L2 / ADR-0037 B1."
        ),
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time",
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        comment="Set on apply/dismiss; NULL while open.",
    )

    # Relationship back to the run
    run: Mapped[LintRun] = relationship(
        "LintRun",
        back_populates="findings",
        lazy="raise",
    )

    __table_args__ = (
        # Paginated read: WHERE vault_id=? AND status=? ORDER BY created_at
        Index("ix_lint_findings_vault_status_created", "vault_id", "status", "created_at"),
        Index("ix_lint_findings_run_id", "lint_run_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<LintFinding id={self.id} category={self.category!r} "
            f"status={self.status!r} vault={self.vault_id!r}>"
        )


class AppConfig(Base):
    """
    v1.1 R11-2 runtime config-override layer — one row per active override (ADR-0053 §2.1).

    Schema invariant: value is NOT NULL — a row existing means an override is active.
    Reset-to-default is a row DELETE, not a null write (§3.3 — "row exists ⇔ override active").
    Absent row ⇒ the env baseline (settings.<key>) governs (backward-compat, §2.6).

    Only the 8 keys in config_overrides.ALLOWED_CONFIG_KEYS may ever be written via the
    API; unknown/removed keys in this table are silently ignored on load (forward-compat).

    Alembic migration 0023 (next-in-sequence, additive — does not touch any existing table).
    Run ``make er`` after applying the migration to regenerate docs/er/schema.mmd (I8).
    """

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        comment=(
            "Config key in lower-snake attribute form (e.g. pdf_extractor). "
            "Matches the settings.<key> attribute name and the API path segment. "
            "ADR-0053 §2.1."
        ),
    )

    value: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "Override value as TEXT. NOT NULL — 'a row exists ⇔ override is active'. "
            "Reset to default by deleting the row (§3.3). "
            "Typed values (bool/float) are stored as their string form and coerced at read. "
            "ADR-0053 §2.1."
        ),
    )

    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="Last write time (audit). Refreshed on every upsert. ADR-0053 §2.1.",
    )

    def __repr__(self) -> str:
        return f"<AppConfig key={self.key!r} value={self.value!r}>"


class ImageCaption(Base):
    """
    R8-2 / F12 vision-caption cache — one row per (vault_id, sha256) image content.

    The orchestrator sha256s an image file's raw bytes and looks up this table before making
    any provider vision call: a HIT returns the cached caption (zero cost, no provider call); a
    MISS triggers one bounded `provider.caption_image()` call (I7 — capped at
    VISION_MAX_IMAGES_PER_RUN per run, cost logged into the run ledger) whose result is stored
    here so re-ingesting the same image is idempotent and free.

    The (vault_id, sha256) uniqueness makes the cache content-addressed and per-vault: the same
    image bytes in two vaults may be captioned differently (different purpose.md context), but
    within one vault identical bytes reuse the caption. `provider_type` is audit metadata (which
    backend produced the caption); it is NEVER read back into a routing decision (I6).

    Alembic migration 0022 (additive; run `make er` after applying — I8/D2).
    """

    __tablename__ = "image_captions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=sa_text("gen_random_uuid()"),
        comment="Row identity",
    )

    vault_id: Mapped[str] = mapped_column(
        String,
        nullable=False,
        comment="Vault this cached caption belongs to (per-vault cache scoping)",
    )

    sha256: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="Lowercase hex sha256 of the image file's raw bytes (content-addressed key)",
    )

    file_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Relative raw source path last seen for this content (audit; may drift)",
    )

    caption: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Provider-generated caption used as the image's extracted text (R8-2)",
    )

    provider_type: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Backend that produced the caption: local|api|cli (audit only, never routing)",
    )

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Row creation time",
    )

    __table_args__ = (
        # Content-addressed lookup key: unique per (vault, content hash).
        UniqueConstraint("vault_id", "sha256", name="uq_image_captions_vault_sha256"),
        Index("ix_image_captions_vault_sha256", "vault_id", "sha256"),
    )

    def __repr__(self) -> str:
        return (
            f"<ImageCaption id={self.id} vault={self.vault_id!r} "
            f"sha256={self.sha256[:12]!r}… provider={self.provider_type!r}>"
        )
