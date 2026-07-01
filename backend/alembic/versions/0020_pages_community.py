"""pages.community — Louvain community id per node (G-P0-2)

Revision ID: 0020
Revises: 0019
Create Date: 2026-07-01

Additive schema change: single nullable INTEGER column on the pages table.

  ALTER TABLE pages ADD COLUMN community INTEGER NULL;

community (G-P0-2):
  Louvain community id assigned by GraphEngine.recompute() alongside FA2 layout.
  NULL until the first recompute after this migration is applied.
  Communities are re-numbered by size (largest = 0) for stable coloring — same
  convention as nashsu/llm_wiki (R1).  Recomputed server-side on every debounced
  dataVersion bump; client receives it in GET /graph nodes (I2 — never computed
  client-side).

No data migration: community starts NULL for all existing rows; the next debounced
recompute (or first GET /graph miss) will populate it (I1 — no full rescan).

D2/ER: run the ER generator after applying to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add pages.community (Louvain community id, NULL until first recompute)."""
    op.add_column(
        "pages",
        sa.Column(
            "community",
            sa.Integer(),
            nullable=True,
            comment=(
                "Louvain community id (re-numbered by size, largest=0). "
                "NULL until first GraphEngine.recompute() after migration 0020 (G-P0-2). "
                "Server-side only — client receives it in GET /graph; never computed client-side (I2)."
            ),
        ),
    )


def downgrade() -> None:
    """Drop pages.community."""
    op.drop_column("pages", "community")
