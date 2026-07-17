"""ingest_runs.diagnostics — non-convergence diagnostics [NC-1]

Revision ID: 0035
Revises: 0034
Create Date: 2026-07-16

1.9.1 W5 (finding NC-1, observed live 2026-07-16): a block-loop ingest run that exhausted
max_iter without converging surfaced ONLY a bare "Non convergito" / converged_false status in
the UI — the per-iteration validation errors and the stop reason existed only in the backend
logs (``logger.warning("block loop: stopped without convergence...")``), never persisted.
``ingest_runs.error_message`` is NULL by design on ``converged_false`` rows (ADR-0018 §7 — it is
reserved for actual run failures), so there was nowhere to look.

Adds a nullable ``diagnostics`` JSON/JSONB column populated by both loop shapes
(``app.ingest.loop.LoopResult`` / ``app.ingest.block_loop.BlockLoopResult``) on EVERY terminal
outcome (converged or not):

    {"stop_reason": "converged"|"max_iter"|"token_budget",
     "iterations": int,
     "last_errors": list[str],
     "tokens_used": int,
     "token_budget": int}

NULL for the delegated/CLI route (no bounded loop to report) and for legacy rows written before
this migration. Third migration of this workstream, chained after 0034. Additive,
non-destructive.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingest_runs",
        sa.Column(
            "diagnostics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "Non-convergence diagnostics: {stop_reason, iterations, last_errors, "
                "tokens_used, token_budget}; NULL for legacy/delegated rows (1.9.1 W5, NC-1)."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("ingest_runs", "diagnostics")
