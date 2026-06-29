"""deep_research_runs + deep_research_sources — F10 Deep Research loop (ADR-0024 §7)

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-29

New tables (v0.5 M5 Phase 2 — F10 Deep Research, ADR-0024):

  deep_research_runs
    id                UUID PK  default gen_random_uuid()
    vault_id          String   NOT NULL           -- scope (no vaults table, AQ-v0.5-6)
    topic             Text     NOT NULL           -- research topic
    status            Text     NOT NULL DEFAULT 'running'
                                                  -- running|converged|max_iter_reached|
                                                  --   budget_exhausted|error
    max_iter          Integer  NOT NULL           -- FROZEN at INSERT (AQ-v0.5-4, I7)
    token_budget      Integer  NOT NULL           -- FROZEN at INSERT (AQ-v0.5-4, I7)
    iterations_used   Integer  NOT NULL DEFAULT 0
    queries_used      JSONB    NOT NULL DEFAULT '[]'  -- all queries issued (AC-F10-4c)
    sources_fetched   Integer  NOT NULL DEFAULT 0
    converged         Boolean  NOT NULL DEFAULT false -- audit convenience
    total_cost_usd    Numeric(10,4) NOT NULL DEFAULT 0
    synthesis_text    Text     NULL               -- NULL until step 5 (AC-F10-4c)
    synthesis_page_id UUID     NULL FK→pages.id   -- NULL until ingest_file completes
    started_at        TIMESTAMPTZ NOT NULL default now()
    completed_at      TIMESTAMPTZ NULL            -- NULL while running
    error_message     Text     NULL               -- populated on status='error'

  deep_research_sources
    id                UUID PK  default gen_random_uuid()
    run_id            UUID     NOT NULL FK→deep_research_runs.id ON DELETE CASCADE
    url               Text     NOT NULL
    title             Text     NULL
    fetched_content_md Text    NULL               -- NULL on fetch failure
    relevance_score   Numeric(6,4) NULL           -- optional in Phase 2
    iteration         Integer  NOT NULL DEFAULT 1
    created_at        TIMESTAMPTZ NOT NULL default now()

Indexes:
  ix_deep_research_runs_vault_started  (vault_id, started_at)  — paginated list
  ix_deep_research_sources_run_id      (run_id)                 — per-run sources lookup
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── deep_research_runs ───────────────────────────────────────────────────
    op.create_table(
        "deep_research_runs",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("vault_id", sa.String(), nullable=False),
        sa.Column("topic", sa.Text(), nullable=False),
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
            "queries_used",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "sources_fetched",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "converged",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(10, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("synthesis_text", sa.Text(), nullable=True),
        sa.Column(
            "synthesis_page_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pages.id"),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )

    op.create_index(
        "ix_deep_research_runs_vault_started",
        "deep_research_runs",
        ["vault_id", "started_at"],
    )

    # ── deep_research_sources ─────────────────────────────────────────────────
    op.create_table(
        "deep_research_sources",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("deep_research_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("fetched_content_md", sa.Text(), nullable=True),
        sa.Column("relevance_score", sa.Numeric(6, 4), nullable=True),
        sa.Column(
            "iteration",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index(
        "ix_deep_research_sources_run_id",
        "deep_research_sources",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_deep_research_sources_run_id", table_name="deep_research_sources")
    op.drop_table("deep_research_sources")
    op.drop_index("ix_deep_research_runs_vault_started", table_name="deep_research_runs")
    op.drop_table("deep_research_runs")
