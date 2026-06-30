"""lint_runs + lint_findings — K2 lint-fix loop (ADR-0037)

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-30

Schema changes (ADR-0037 §3 — K2 lint-fix loop persistence):

CREATE TABLE lint_runs (mirrors deep_research_runs audit ledger):
  id              UUID PK
  vault_id        String NOT NULL
  status          Text NOT NULL DEFAULT 'running'   (running | completed | error)
  max_iter        Integer NOT NULL                  (FROZEN at INSERT — I7)
  token_budget    Integer NOT NULL                  (FROZEN at INSERT — I7)
  iterations_used Integer NOT NULL DEFAULT 0
  findings_count  Integer NOT NULL DEFAULT 0
  total_cost_usd  Numeric(10,4) NOT NULL DEFAULT 0  (I7; 0.0000 for local/cli)
  started_at      timestamptz NOT NULL DEFAULT now()
  completed_at    timestamptz NULL
  error_message   Text NULL
  created_at      timestamptz NOT NULL DEFAULT now()
  INDEX ix_lint_runs_vault_created (vault_id, created_at)

CREATE TABLE lint_findings (mirrors review_items proposal rows):
  id              UUID PK
  lint_run_id     UUID NOT NULL FK → lint_runs.id ON DELETE CASCADE
  vault_id        String NOT NULL
  category        Text NOT NULL  (orphan-page|missing-xref|contradiction|stale-claim|missing-page)
  severity        Text NOT NULL DEFAULT 'warning'  (info | warning | error)
  target_page_id  UUID NULL FK → pages.id
  target_title    Text NULL
  description     Text NOT NULL
  proposed_action Text NULL
  status          Text NOT NULL DEFAULT 'open'  (open | applied | dismissed)
  resolution_note Text NULL
  created_at      timestamptz NOT NULL DEFAULT now()
  reviewed_at     timestamptz NULL
  INDEX ix_lint_findings_vault_status_created (vault_id, status, created_at)
  INDEX ix_lint_findings_run_id (lint_run_id)

UUID columns use postgresql.UUID on Postgres; the ORM uses
``UUID(as_uuid=True).with_variant(String(36), "sqlite")`` so unit tests (aiosqlite) build
the same logical schema with TEXT keys. This migration targets Postgres (the live DB).

Downgrade: drop both tables (findings first — FK dependency).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """ADR-0037 §3 — create lint_runs then lint_findings (FK order)."""

    # ── lint_runs ─────────────────────────────────────────────────────────────
    op.create_table(
        "lint_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("vault_id", sa.String(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'running'"),
        ),
        sa.Column("max_iter", sa.Integer(), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column(
            "iterations_used",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "findings_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(10, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_lint_runs_vault_created",
        "lint_runs",
        ["vault_id", "created_at"],
    )

    # ── lint_findings ─────────────────────────────────────────────────────────
    op.create_table(
        "lint_findings",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "lint_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("lint_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vault_id", sa.String(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column(
            "severity",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'warning'"),
        ),
        sa.Column(
            "target_page_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pages.id"),
            nullable=True,
        ),
        sa.Column("target_title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("proposed_action", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_lint_findings_vault_status_created",
        "lint_findings",
        ["vault_id", "status", "created_at"],
    )
    op.create_index(
        "ix_lint_findings_run_id",
        "lint_findings",
        ["lint_run_id"],
    )


def downgrade() -> None:
    """Reverse ADR-0037 §3 — drop lint_findings (FK child) then lint_runs."""
    op.drop_index("ix_lint_findings_run_id", table_name="lint_findings")
    op.drop_index("ix_lint_findings_vault_status_created", table_name="lint_findings")
    op.drop_table("lint_findings")

    op.drop_index("ix_lint_runs_vault_created", table_name="lint_runs")
    op.drop_table("lint_runs")
