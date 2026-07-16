"""links partial index for the vault-scoped dangling-reresolve query [BE-PERF-11]

Revision ID: 0033
Revises: 0032
Create Date: 2026-07-16

BE-PERF-11: reresolve_dangling_links (app/wiki/links.py) previously loaded EVERY
dangling link across ALL vaults with no vault scoping, then resolved them against the
ACTIVE vault's resolver maps only — a correctness bug (a title collision could silently
steal a different vault's dangling link into this vault's graph) as well as a
performance one (unbounded cross-vault scan). The fix scopes the query with a JOIN to
``pages`` on ``source_page_id`` filtered by ``pages.vault_id`` (same code-level fix
already used by ``persist_links`` / ``_build_resolver_maps``).

This migration adds a partial index on ``links(source_page_id) WHERE dangling = true`` —
``dangling`` rows are typically a small minority of all links, so a partial index keeps
the reresolve join's dangling-side lookup small regardless of total link-table size,
instead of relying on the existing full ``ix_links_source_page_id`` index (which covers
every link, resolved or not).

Additive, non-destructive; no data migration needed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_links_dangling_source_page_id",
        "links",
        ["source_page_id"],
        postgresql_where=sa.text("dangling = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_links_dangling_source_page_id", table_name="links")
