"""generation lifecycle parity metadata — ADR-0073 / ADR-0074

Revision ID: 0031
Revises: 0030
Create Date: 2026-07-13

Additive, non-destructive migration:

* review_items.proposal_origin — structured provenance, legacy default/backfill;
* pages.generation_key — portable corpus identity with a live partial unique index;
* ingest_runs.page_type_counts — nullable per-run generation diagnostics.

Legacy corpus pages deliberately keep generation_key=NULL. Duplicate cleanup is an explicit
dry-run/operator workflow, never a migration side effect.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "review_items",
        sa.Column(
            "proposal_origin",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'legacy'"),
            comment=(
                "Proposal provenance: rule | ai | corpus | system | lint | legacy "
                "(ADR-0073)."
            ),
        ),
    )
    op.add_column(
        "pages",
        sa.Column(
            "generation_key",
            sa.String(length=96),
            nullable=True,
            comment=(
                "Stable corpus-derived page identity mirrored by synapse_generation_key YAML; "
                "NULL for legacy/ordinary pages (ADR-0074)."
            ),
        ),
    )
    op.create_index(
        "uix_pages_vault_generation_key_live",
        "pages",
        ["vault_id", "generation_key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND generation_key IS NOT NULL"),
    )
    op.add_column(
        "ingest_runs",
        sa.Column(
            "page_type_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="Per-PageType pages created by this run; NULL for legacy rows (ADR-0073).",
        ),
    )


def downgrade() -> None:
    op.drop_column("ingest_runs", "page_type_counts")
    op.drop_index("uix_pages_vault_generation_key_live", table_name="pages")
    op.drop_column("pages", "generation_key")
    op.drop_column("review_items", "proposal_origin")
