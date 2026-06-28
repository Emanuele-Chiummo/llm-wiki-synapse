"""
SQLAlchemy 2 ORM models — single source of truth for D2 ER diagram (I8 / AC-PG-3).

Tables defined here:
  - pages        : one row per source file; soft-deletable (ADR-0005).
  - vault_state  : one row per vault; holds the monotonic data_version (ADR-0005).

provider_config is OUT of v0.1 (ADR-0003 / v0.1-architecture §2.1).

Run `make er` to regenerate docs/er/schema.mmd from this file (I8).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Index,
    Integer,
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
