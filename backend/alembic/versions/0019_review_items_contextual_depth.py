"""review_items — contextual depth + stable idempotency (ADR-0044, F9 depth pass)

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-01

NOTE (correction to ADR-0044): the ADR text says migration "0018", but 0018 is already
taken by pages.tags. This migration is 0019, chained from 0018.

Additive schema change (three nullable columns + one partial-unique index):

  ALTER TABLE review_items
    ADD COLUMN content_key         TEXT  NULL;   -- 16-hex FNV-1a stable dedup handle
    ADD COLUMN referenced_page_ids JSONB NULL;   -- array of page-id strings (context set)
    ADD COLUMN search_queries      JSONB NULL;   -- ≤3 pre-generated search queries

  CREATE UNIQUE INDEX ix_review_items_vault_content_key_live
      ON review_items (vault_id, content_key)
      WHERE content_key IS NOT NULL AND status IN ('pending');

content_key (ADR-0044 §3.2):
  - Stable FNV-1a-16hex digest over vault_id + item_type + normalize(proposed_title) +
    (target_page_title | page_id). Makes the queue idempotent across re-ingest: the same
    logical proposal keeps its id + status. NULL for `confirm` (never deduped) and legacy rows.

referenced_page_ids / search_queries (ADR-0044 §2/§3.1):
  - JSONB arrays; ride the SAME single proposal call (no extra provider call). referenced_page_ids
    is a JSON array by design (NOT a junction/FK — stale ids filtered at render, §9.2, I9).

ix_review_items_vault_content_key_live (§3.3):
  - Partial-unique scoped to the live (pending) set — a terminal row with the same content_key
    does not conflict (the upsert reads it first and no-ops). Postgres enforces it; SQLite tests
    rely on the application-level read-before-write upsert (the portable contract).

status/resolution value sets gain `dismissed` (no type change — both stay Text; enum-by-convention).

Backfill: content_key left NULL for pre-existing rows (historical; not re-deduped
retroactively — M6 is single-operator pre-release; ADR-0044 §3.5).

Downgrade drops the index then the three columns.

D2/ER: run the ER generator after applying to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add content_key + referenced_page_ids + search_queries + the partial-unique index."""
    op.add_column(
        "review_items",
        sa.Column(
            "content_key",
            sa.Text(),
            nullable=True,
            comment=(
                "16-hex FNV-1a stable digest (ADR-0044 §3.2) making the queue idempotent across "
                "re-ingest. NULL for `confirm` items and legacy rows. Migration 0019."
            ),
        ),
    )
    op.add_column(
        "review_items",
        sa.Column(
            "referenced_page_ids",
            JSONB(),
            nullable=True,
            comment=(
                "JSON array of page-id strings — the existing pages the proposal is contextually "
                "about (ADR-0044 §2/§3.1). Bounded (≤ REVIEW_REFERENCED_PAGES_MAX). Deliberately "
                "a JSON array, not a junction/FK; stale ids filtered at render. Migration 0019."
            ),
        ),
    )
    op.add_column(
        "review_items",
        sa.Column(
            "search_queries",
            JSONB(),
            nullable=True,
            comment=(
                "JSON array of ≤3 pre-generated search-query strings (ADR-0044 §2.3), from the "
                "SAME single proposal call. Seeds Deep Research; shown on the card. Migration 0019."
            ),
        ),
    )
    # Partial-unique idempotency index scoped to the live (pending) set (ADR-0044 §3.3).
    op.create_index(
        "ix_review_items_vault_content_key_live",
        "review_items",
        ["vault_id", "content_key"],
        unique=True,
        postgresql_where=sa.text("content_key IS NOT NULL AND status IN ('pending')"),
    )


def downgrade() -> None:
    """Drop the partial-unique index then the three columns."""
    op.drop_index("ix_review_items_vault_content_key_live", table_name="review_items")
    op.drop_column("review_items", "search_queries")
    op.drop_column("review_items", "referenced_page_ids")
    op.drop_column("review_items", "content_key")
