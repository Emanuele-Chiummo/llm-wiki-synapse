"""image_captions — vision-caption cache (R8-2 / F12)

Revision ID: 0022
Revises: 0021
Create Date: 2026-07-03

New table: one row per (vault_id, sha256) captioned image (R8-2 / F12, ADR-0025 §4.5
superseded — images are now optionally captioned via the InferenceProvider vision path).

  CREATE TABLE image_captions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vault_id      VARCHAR NOT NULL,
    sha256        VARCHAR(64) NOT NULL,
    file_path     TEXT NULL,
    caption       TEXT NOT NULL,
    provider_type TEXT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_image_captions_vault_sha256 UNIQUE (vault_id, sha256)
  );
  CREATE INDEX ix_image_captions_vault_sha256 ON image_captions (vault_id, sha256);

Content-addressed cache (I7 cost control): the orchestrator sha256s an image's raw
bytes and looks this up before any provider vision call. A HIT reuses the caption
(zero cost); a MISS makes ONE bounded provider.caption_image() call (capped at
VISION_MAX_IMAGES_PER_RUN per run, cost logged into the ingest_runs ledger) and stores
the result here so re-ingesting the same image is idempotent and free.

Unique (vault_id, sha256): per-vault, content-addressed. provider_type is audit-only
(never a routing input — I6).

Portable SQL: the id server_default is gen_random_uuid() on Postgres; the table is created
programmatically so the SQLite unit-test engine (no gen_random_uuid) supplies the id from the
ORM default at insert time. VARCHAR(64) for the hex sha256 is portable across both dialects.

Downgrade drops the index then the table.

D2/ER: run ``make er`` after applying this migration to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the image_captions vision-caption cache table (R8-2 / F12)."""
    op.create_table(
        "image_captions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="Row identity",
        ),
        sa.Column(
            "vault_id",
            sa.String(),
            nullable=False,
            comment="Vault this cached caption belongs to (per-vault cache scoping)",
        ),
        sa.Column(
            "sha256",
            sa.String(length=64),
            nullable=False,
            comment="Lowercase hex sha256 of the image file's raw bytes (content-addressed key)",
        ),
        sa.Column(
            "file_path",
            sa.Text(),
            nullable=True,
            comment="Relative raw source path last seen for this content (audit; may drift)",
        ),
        sa.Column(
            "caption",
            sa.Text(),
            nullable=False,
            comment="Provider-generated caption used as the image's extracted text (R8-2)",
        ),
        sa.Column(
            "provider_type",
            sa.Text(),
            nullable=True,
            comment="Backend that produced the caption: local|api|cli (audit only, never routing)",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Row creation time",
        ),
        sa.UniqueConstraint("vault_id", "sha256", name="uq_image_captions_vault_sha256"),
    )
    op.create_index(
        "ix_image_captions_vault_sha256",
        "image_captions",
        ["vault_id", "sha256"],
    )


def downgrade() -> None:
    """Drop the image_captions table (and its index)."""
    op.drop_index("ix_image_captions_vault_sha256", table_name="image_captions")
    op.drop_table("image_captions")
