"""
SQLAlchemy 2 ORM models — single source of truth for D2 ER diagram (I8 / AC-PG-3).

Tables defined here:
  - pages           : one row per source file; soft-deletable (ADR-0005).
  - vault_state     : one row per vault; holds the monotonic data_version (ADR-0005).
  - provider_config : F17 backend selection per scope (global|vault|operation) (ADR-0008).
  - ingest_runs     : per-run cost/convergence audit ledger (I7, ADR-0008 §4).
  - links           : K5 wikilink edges; source_page_id → target_title (dangling until resolved).

provider_config + ingest_runs added in v0.2 (ADR-0008). links added in v0.2 (ADR-0008 §5).
All three new tables ship in a single Alembic migration 0002 (one schema-change event).

Run `make er` to regenerate docs/er/schema.mmd from this file (I8).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
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

    Holds NO API key column — secrets are environment-only (§12, ADR-0008 §3). `model_id`
    values live ONLY in DB rows (seeded by the Alembic data migration), never as literals in
    app code (AC-F17-8). `provider_name`/`model_id` are config, not routing inputs (I6 routing
    is by capabilities().supports_agentic_loop).
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

    def __repr__(self) -> str:
        return (
            f"<IngestRun provider={self.provider_name!r} route={self.route!r} "
            f"converged={self.converged} cost=${self.total_cost_usd}>"
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
