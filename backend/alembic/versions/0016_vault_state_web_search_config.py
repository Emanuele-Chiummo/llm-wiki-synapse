"""vault_state SearXNG runtime config — ADR-0041

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-01

Schema changes (ADR-0041 §3 — three new columns on vault_state):

  ALTER TABLE vault_state
    ADD COLUMN searxng_url_db TEXT NULL;

  ALTER TABLE vault_state
    ADD COLUMN searxng_categories_db TEXT NULL;

  ALTER TABLE vault_state
    ADD COLUMN searxng_max_queries_db INTEGER NULL;

searxng_url_db:
  - TEXT NULL — runtime-settable SearXNG base URL.
  - NULL = not set in DB; fall back to SEARXNG_URL env var.
  - When NOT NULL the DB value is authoritative (overrides the env var).
  - The URL is NOT a secret (SearXNG is an open internal service) and IS returned
    by GET /web-search/config — unlike the clip token, no masking needed.
  - Default NULL on upgrade ⇒ env-fallback behaviour holds unchanged (zero-friction
    upgrade for existing deployments).

searxng_categories_db:
  - TEXT NULL — comma-separated SearXNG search categories (e.g. "general,news").
  - NULL = not set in DB; fall back to DEEP_RESEARCH_* env settings.
  - When NOT NULL the DB value is authoritative.
  - Parsed to list[str] at read time by splitting on commas.

searxng_max_queries_db:
  - INTEGER NULL — maximum SearXNG queries per deep-research iteration.
  - NULL = not set in DB; fall back to DEEP_RESEARCH_MAX_QUERIES env.
  - When NOT NULL the DB value is authoritative.
  - Bounded 1..50 at the PUT endpoint; stored raw (no range enforcement in DB).

Downgrade drops all three columns.

D2/ER: run `make er` after applying this migration to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add searxng_url_db, searxng_categories_db, searxng_max_queries_db to vault_state."""
    op.add_column(
        "vault_state",
        sa.Column(
            "searxng_url_db",
            sa.Text(),
            nullable=True,
            comment=(
                "Runtime SearXNG base URL (ADR-0041 §3). "
                "NULL = not set in DB; fall back to SEARXNG_URL env var. "
                "When set, DB value wins over env. "
                "NOT a secret — returned by GET /web-search/config (no masking). "
                "Migration 0016."
            ),
        ),
    )
    op.add_column(
        "vault_state",
        sa.Column(
            "searxng_categories_db",
            sa.Text(),
            nullable=True,
            comment=(
                "Comma-separated SearXNG categories (ADR-0041 §3). "
                "NULL = not set in DB; fall back to env / code defaults. "
                "When set, DB value wins over env. "
                "Migration 0016."
            ),
        ),
    )
    op.add_column(
        "vault_state",
        sa.Column(
            "searxng_max_queries_db",
            sa.Integer(),
            nullable=True,
            comment=(
                "Max SearXNG queries per deep-research iteration (ADR-0041 §3). "
                "NULL = not set in DB; fall back to DEEP_RESEARCH_MAX_QUERIES env. "
                "When set, DB value wins over env. "
                "Migration 0016."
            ),
        ),
    )


def downgrade() -> None:
    """Drop searxng_url_db, searxng_categories_db, searxng_max_queries_db from vault_state."""
    op.drop_column("vault_state", "searxng_max_queries_db")
    op.drop_column("vault_state", "searxng_categories_db")
    op.drop_column("vault_state", "searxng_url_db")
