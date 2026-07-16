"""ingest_runs vault+started_at composite index [BE-PERF-8]

Revision ID: 0034
Revises: 0033
Create Date: 2026-07-16

BE-PERF-8: ingest_runs had NO index at all (beyond the primary key). Two hot paths filter
``WHERE vault_id = ? ORDER BY started_at DESC``:
  - GET /ingest/runs (run-list poll, app/routers/ingest.py)
  - costs.get_monthly_cost_usd (per-month cost scan, reused by GET /costs/summary and
    GET /stats/overview)

Both forced a sequential scan + explicit sort on every call. This migration adds
``ix_ingest_runs_vault_started`` on ``(vault_id, started_at)`` — the same composite-index
pattern already applied to the two sibling audit-ledger tables
(``ix_deep_research_runs_vault_started``, ``ix_lint_runs_vault_created``, both added in
earlier migrations).

Second migration of this workstream, chained after 0033. Additive, non-destructive.
"""

from __future__ import annotations

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_ingest_runs_vault_started",
        "ingest_runs",
        ["vault_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ingest_runs_vault_started", table_name="ingest_runs")
