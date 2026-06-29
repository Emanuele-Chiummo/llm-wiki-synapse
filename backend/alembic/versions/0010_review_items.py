"""review_items — F9 HITL Review Queue (ADR-0025 §3.1)

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-29

New table (v0.5 M5 Phase 3 — F9 HITL Review Queue, ADR-0025):

  review_items
    id                    UUID PK  DEFAULT gen_random_uuid()
    vault_id              String   NOT NULL            -- existing String id (no vaults FK, AQ-v0.5-6)
    page_id               UUID     NULL FK→pages.id    -- the wiki page being reviewed
    item_type             Text     NOT NULL            -- new_page|update_page|deep_research_candidate
    status                Text     NOT NULL DEFAULT 'pending'
                                                       -- pending|approved|skipped|deep_researched
    pre_generated_query   Text     NULL                -- newline-sep 1-3 questions; NULL on failure
    deep_research_run_id  UUID     NULL FK→deep_research_runs.id  -- set by deep-research action
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
    reviewed_at           TIMESTAMPTZ NULL             -- set on approve/skip/deep-research
    reviewed_by           Text     NULL                -- audit: 'web-ui' etc; NULL while pending

Index: ix_review_items_vault_status_created ON (vault_id, status, created_at)
  Optimises the paginated pending-queue read (ADR-0025 §3.1).

Zero invariant violations: vault_id is String (AQ-v0.5-6), not a UUID FK.
No per-page uniqueness — the table is an event log (ADR-0025 §3.1 note).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create review_items table + index (ADR-0025 §3.1)."""
    op.create_table(
        "review_items",
        sa.Column(
            "id",
            sa.String(36),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="Row identity",
        ),
        sa.Column(
            "vault_id",
            sa.String(),
            nullable=False,
            comment="Logical vault identifier — String, no FK (AQ-v0.5-6, ADR-0025 §3.1)",
        ),
        sa.Column(
            "page_id",
            sa.String(36),
            sa.ForeignKey("pages.id"),
            nullable=True,
            comment="FK → pages.id; NULL for page-less items",
        ),
        sa.Column(
            "item_type",
            sa.Text(),
            nullable=False,
            comment="new_page | update_page | deep_research_candidate",
        ),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'pending'"),
            comment="pending | approved | skipped | deep_researched",
        ),
        sa.Column(
            "pre_generated_query",
            sa.Text(),
            nullable=True,
            comment="Newline-sep 1-3 research questions; NULL on failure/timeout (I7, AC-F9-4)",
        ),
        sa.Column(
            "deep_research_run_id",
            sa.String(36),
            sa.ForeignKey("deep_research_runs.id"),
            nullable=True,
            comment="FK → deep_research_runs.id; set by deep-research action (AC-F10-5)",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Row creation time",
        ),
        sa.Column(
            "reviewed_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Set on approve/skip/deep-research; NULL while pending",
        ),
        sa.Column(
            "reviewed_by",
            sa.Text(),
            nullable=True,
            comment="Free-text actor (e.g. 'web-ui'); NULL while pending. Audit-only in M5.",
        ),
    )

    op.create_index(
        "ix_review_items_vault_status_created",
        "review_items",
        ["vault_id", "status", "created_at"],
    )


def downgrade() -> None:
    """Drop review_items table + index."""
    op.drop_index("ix_review_items_vault_status_created", table_name="review_items")
    op.drop_table("review_items")
