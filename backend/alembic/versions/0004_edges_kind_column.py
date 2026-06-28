"""edges.kind column — structural edge discriminator (ADR-0016 §4)

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-28

Changes:
  edges.kind  TEXT NULL → "link" | "source"
              Derived from signals.direct but persisted for fast read
              without JSONB extraction on every GET /graph.

  Default NULL for existing rows; GraphEngine.recompute() will re-populate
  with the correct kind on the next layout recompute.  NULL rows are treated
  as "link" by the response assembler (backward-compatible default).

References:
  ADR-0016 §4 — per-edge kind field
  ADR-0016 §5 — backend-engineer change list item 3
  I8 — D-artifacts updated (ER schema.mmd updated separately)
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # edges.kind — nullable text; NULL for rows inserted before this migration.
    # Populated by GraphEngine._persist_results() on every recompute.
    op.add_column(
        "edges",
        sa.Column(
            "kind",
            sa.Text(),
            nullable=True,
            comment=(
                'Structural edge discriminator: "link" (direct wikilink) | '
                '"source" (shared provenance only). ADR-0016 §4. '
                "NULL for rows written before migration 0004 (treated as link)."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("edges", "kind")
