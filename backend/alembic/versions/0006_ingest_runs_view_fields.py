"""ingest_runs view fields — status, pages_created, error_message (ADR-0018 §7)

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-28

Changes (ADDITIVE — backward-compatible):
  ingest_runs.status          TEXT NOT NULL DEFAULT 'completed'
      Values: 'running' | 'completed' | 'failed' | 'converged_false'
      Backfilled: converged=true → 'completed', converged=false → 'converged_false'
  ingest_runs.pages_created   INTEGER NOT NULL DEFAULT 0
      Backfilled: 0 for all historical rows
  ingest_runs.error_message   TEXT NULL

No existing columns are renamed. max_iter_used and finished_at are aliased
in the API response layer (iterations_used, completed_at respectively).

References:
  ADR-0018 §7 — schema audit + migration decision
  AC-BE-IR-1  — GET /ingest/runs contract fields
  I8 — docs/er/schema.mmd and docs/api/openapi.json must be updated after this migration
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Add status column (TEXT NOT NULL DEFAULT 'completed') ─────────────────
    # We add with a server_default first so Postgres can backfill all existing rows,
    # then update from the `converged` boolean for a more accurate initial state.
    op.add_column(
        "ingest_runs",
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'completed'"),
            comment=(
                "Run lifecycle state: running | completed | failed | converged_false. "
                "Backfilled from converged for historical rows (ADR-0018 §7)."
            ),
        ),
    )

    # Backfill: converged=false rows that have no error → 'converged_false'
    # converged=true rows stay 'completed' (already defaulted correctly).
    op.execute(
        sa.text(
            "UPDATE ingest_runs "
            "SET status = 'converged_false' "
            "WHERE converged = false"
        )
    )

    # ── Add pages_created column (INTEGER NOT NULL DEFAULT 0) ─────────────────
    op.add_column(
        "ingest_runs",
        sa.Column(
            "pages_created",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
            comment=(
                "Number of wiki pages persisted during this run. "
                "0 for historical rows; set by orchestrator on new runs (ADR-0018 §7)."
            ),
        ),
    )

    # ── Add error_message column (TEXT NULL) ──────────────────────────────────
    op.add_column(
        "ingest_runs",
        sa.Column(
            "error_message",
            sa.Text(),
            nullable=True,
            comment=(
                "Human-readable error description for failed runs; "
                "NULL for completed/running/converged_false rows (ADR-0018 §7)."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("ingest_runs", "error_message")
    op.drop_column("ingest_runs", "pages_created")
    op.drop_column("ingest_runs", "status")
