"""ingest_runs — queue fields for live activity panel (ADR-0046)

Revision ID: 0021
Revises: 0020
Create Date: 2026-07-01

Additive schema change: two columns on the ingest_runs table.

  ALTER TABLE ingest_runs ADD COLUMN source_path TEXT NULL;
  ALTER TABLE ingest_runs ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0;

source_path (ADR-0046 §1):
  Relative raw source path (raw/sources/…) the run is ingesting.
  NULL for historical rows written before this migration.
  Lets the live activity queue display `filename` and enables cancel/retry
  to target a file without requiring a page_id.

retry_count (ADR-0046 §1, I7):
  Times this source has been retried.  0 for the initial attempt.
  Enforced at MAX_INGEST_RETRIES=3 (queue_manager.py constant).
  A successful run clears the per-path counter in the queue manager;
  the column retains the count at run-open time for the audit ledger (I7).

No data migration: source_path starts NULL and retry_count starts 0 for
all existing rows — both server_defaults cover the additive insert.

D2/ER: run `make er` after applying to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add ingest_runs.source_path (nullable Text) and ingest_runs.retry_count (int, default 0)."""
    op.add_column(
        "ingest_runs",
        sa.Column(
            "source_path",
            sa.Text(),
            nullable=True,
            comment=(
                "Relative raw source path (raw/sources/…) the run is ingesting. "
                "NULL for historical rows written before migration 0021 (ADR-0046 §1)."
            ),
        ),
    )
    op.add_column(
        "ingest_runs",
        sa.Column(
            "retry_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment=(
                "Times this source has been retried. Enforces MAX_INGEST_RETRIES=3 (I7, ADR-0046 §5). "
                "Incremented by the queue manager on each re-dispatch; 0 for the initial attempt."
            ),
        ),
    )


def downgrade() -> None:
    """Drop ingest_runs.source_path and ingest_runs.retry_count."""
    op.drop_column("ingest_runs", "retry_count")
    op.drop_column("ingest_runs", "source_path")
