"""v0.3 graph schema — pages.x/y columns + edges table (one migration, ADR-0012/0013)

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-28

Changes:
  pages.x   DOUBLE PRECISION NULL — FA2 x-coordinate (ADR-0013 / AQ-6)
  pages.y   DOUBLE PRECISION NULL — FA2 y-coordinate (ADR-0013 / AQ-6)
  edges     — 4-signal weighted undirected page pairs (ADR-0012 / AQ-5)
              unique on canonicalised (vault_id, source_page_id, target_page_id)
              indexes on source_page_id and target_page_id

Both changes ship in ONE migration to honour the scope-§2 "one schema-change event"
decision (CLAUDE.md §3 I8). No data migration — x/y start NULL (ADR-0013 §3);
edges are populated by GraphEngine.recompute() on the first GET /graph or debounce.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── pages.x and pages.y — FA2 layout coordinates (ADR-0013 / AQ-6) ───────
    op.add_column(
        "pages",
        sa.Column(
            "x",
            sa.Double(),
            nullable=True,
            comment="FA2 x-coordinate (DOUBLE PRECISION); NULL until first layout (ADR-0013)",
        ),
    )
    op.add_column(
        "pages",
        sa.Column(
            "y",
            sa.Double(),
            nullable=True,
            comment="FA2 y-coordinate (DOUBLE PRECISION); NULL until first layout (ADR-0013)",
        ),
    )

    # ── edges table — 4-signal weighted undirected page pairs (ADR-0012 / AQ-5) ─
    op.create_table(
        "edges",
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
            comment="Scope edges per vault (matches pages/vault_state pattern)",
        ),
        sa.Column(
            "source_page_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment=(
                "Unordered pair stored canonically (smaller UUID first by string sort). "
                "FK → pages.id (ADR-0012 / AQ-5)"
            ),
        ),
        sa.Column(
            "target_page_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="FK → pages.id; target of the undirected pair (ADR-0012)",
        ),
        sa.Column(
            "weight",
            sa.Double(),
            nullable=False,
            comment=(
                "Additive 4-signal weight > 0 (ADR-0012): "
                "3·direct + 4·source_overlap + 1.5·adamic_adar + 1·same_type"
            ),
        ),
        sa.Column(
            "signals",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='Per-signal breakdown {"direct","source","aa","type"} for audit (AC-F4-1(e))',
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Row creation time (set on each recompute batch)",
        ),
        sa.ForeignKeyConstraint(["source_page_id"], ["pages.id"]),
        sa.ForeignKeyConstraint(["target_page_id"], ["pages.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "vault_id",
            "source_page_id",
            "target_page_id",
            name="uq_edges_vault_pair",
        ),
    )

    # Indexes on both endpoints for GET /graph reads and cascade cleanup (F13, v0.5)
    op.create_index("ix_edges_source_page_id", "edges", ["source_page_id"])
    op.create_index("ix_edges_target_page_id", "edges", ["target_page_id"])


def downgrade() -> None:
    op.drop_index("ix_edges_target_page_id", table_name="edges")
    op.drop_index("ix_edges_source_page_id", table_name="edges")
    op.drop_table("edges")
    op.drop_column("pages", "y")
    op.drop_column("pages", "x")
