"""lint_suggested_target — suggested_target + suggested_page_id on lint_findings (L2)

Revision ID: 0024
Revises: 0023
Create Date: 2026-07-06

Additive schema change: two nullable columns on the lint_findings table.

  ALTER TABLE lint_findings ADD COLUMN suggested_target TEXT NULL;
  ALTER TABLE lint_findings ADD COLUMN suggested_page_id UUID NULL REFERENCES pages(id);

suggested_target (L2):
  Human-readable title of the best matching existing page found by the tolerant
  resolver (exact → case-insensitive → slug) at scan time.  NULL when no match.
  Displayed as the green "Suggested target:" strip in the lint UI.

suggested_page_id (L2):
  FK → pages.id; the live page whose title is `suggested_target`.  NULL when no
  match or when the finding is not a broken-wikilink category.
  Uses the UUID(as_uuid=True).with_variant(String(36), "sqlite") pattern so
  unit tests on aiosqlite remain green (mirrors 0014 / ADR-0037 §3.2).

No data migration: both columns are NULL for all existing rows — server_default
NULL covers the additive insert (backward-compat with pre-L2 rows).

D2/ER: run ``make er`` after applying this migration to regenerate
docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add lint_findings.suggested_target (nullable Text) and suggested_page_id (nullable UUID)."""
    op.add_column(
        "lint_findings",
        sa.Column(
            "suggested_target",
            sa.Text(),
            nullable=True,
            comment=(
                "Title of the best matching existing page found by the tolerant resolver "
                "(exact → case-insensitive → slug) at broken-wikilink scan time. "
                "NULL when no suggestion found. L2 / ADR-0037 B1."
            ),
        ),
    )
    op.add_column(
        "lint_findings",
        sa.Column(
            "suggested_page_id",
            sa.UUID(as_uuid=True).with_variant(sa.String(36), "sqlite"),
            sa.ForeignKey("pages.id"),
            nullable=True,
            comment=(
                "FK → pages.id; the live page whose title is `suggested_target`. "
                "NULL when no suggestion or for non-broken-wikilink categories. L2 / ADR-0037 B1."
            ),
        ),
    )


def downgrade() -> None:
    """Drop lint_findings.suggested_target and suggested_page_id."""
    op.drop_column("lint_findings", "suggested_page_id")
    op.drop_column("lint_findings", "suggested_target")
