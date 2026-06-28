"""Initial schema — pages + vault_state (v0.1)

Revision ID: 0001
Revises:
Create Date: 2026-06-28

Tables:
  pages       — one row per source file; soft-deletable (ADR-0005)
  vault_state — one row per vault; monotonic data_version (ADR-0005)

No provider_config in v0.1 (ADR-0003).
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── pages ──────────────────────────────────────────────────────────────────
    op.create_table(
        "pages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            comment="Page identity; also the Qdrant point id (ADR-0002)",
        ),
        sa.Column(
            "vault_id",
            sa.String(),
            nullable=False,
            comment="Logical vault identifier — from VAULT_ID env var",
        ),
        sa.Column(
            "file_path",
            sa.Text(),
            nullable=False,
            comment="Relative path under vault/raw/sources/",
        ),
        sa.Column(
            "title",
            sa.Text(),
            nullable=True,
            comment="YAML frontmatter 'title'; NULL if absent (K6)",
        ),
        sa.Column(
            "type",
            sa.Text(),
            nullable=True,
            comment="YAML frontmatter 'type'; NULL if absent (K6)",
        ),
        sa.Column(
            "sources",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="YAML frontmatter 'sources[]' as JSONB array; NULL if absent",
        ),
        sa.Column(
            "content_hash",
            sa.String(length=64),
            nullable=False,
            comment="sha256 hex of raw file bytes — authoritative change signal (ADR-0001)",
        ),
        sa.Column(
            "source_mtime_ns",
            sa.BigInteger(),
            nullable=True,
            comment="st_mtime_ns at last confirmed index — fast-path gate (ADR-0001)",
        ),
        sa.Column(
            "qdrant_point_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Qdrant point id == pages.id (ADR-0002)",
        ),
        sa.Column(
            "deleted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
            comment="NULL = live; non-NULL = soft-deleted (ADR-0005)",
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Row creation time",
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Updated on every upsert (AC-WATCH-3)",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Partial unique index: one live row per (vault_id, file_path) (ADR-0005)
    op.create_index(
        "uix_pages_vault_file_path_live",
        "pages",
        ["vault_id", "file_path"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ── vault_state ────────────────────────────────────────────────────────────
    op.create_table(
        "vault_state",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            comment="Row identity",
        ),
        sa.Column(
            "vault_id",
            sa.String(),
            nullable=False,
            comment="One row per vault; from VAULT_ID env var",
        ),
        sa.Column(
            "data_version",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Monotonic; +1 per successful upsert ingest (AC-F16dv-2/4)",
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Last bump time",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vault_id", name="uq_vault_state_vault_id"),
    )


def downgrade() -> None:
    op.drop_table("vault_state")
    op.drop_index("uix_pages_vault_file_path_live", table_name="pages")
    op.drop_table("pages")
