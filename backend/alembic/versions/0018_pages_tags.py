"""pages.tags — K6 navigation tags (nashsu/llm_wiki parity)

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-01

Schema change (one new column on pages — mirrors pages.sources storage):

  ALTER TABLE pages
    ADD COLUMN tags JSONB NULL;

tags:
  - JSONB NULL — YAML frontmatter 'tags[]' as a JSON array of short lowercase strings.
  - Mirrors pages.sources exactly (JSONB, nullable) for portability.
  - K6 navigation metadata (tag-based navigation, nashsu/llm_wiki parity); NOT the F3
    traceability guarantee (that stays on sources[]).
  - Default NULL on upgrade ⇒ existing rows keep NULL until re-ingested/re-written; the
    write path and schema normalize absent → []. Backward-compatible, additive (I1).

Downgrade drops the column.

D2/ER: run ``make er`` after applying this migration to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add tags to pages (K6 navigation; mirrors sources)."""
    op.add_column(
        "pages",
        sa.Column(
            "tags",
            JSONB(),
            nullable=True,
            comment=(
                "YAML frontmatter 'tags[]' as JSONB array; NULL if absent (K6 navigation, "
                "nashsu/llm_wiki parity). Mirrors `sources` storage. Migration 0018."
            ),
        ),
    )


def downgrade() -> None:
    """Drop tags from pages."""
    op.drop_column("pages", "tags")
