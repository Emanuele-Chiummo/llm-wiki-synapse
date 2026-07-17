"""pages.summary — gloss catalogue [W6, PF-INDEX-GLOSS-1]

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-17

1.9.4 W6 (finding PF-INDEX-GLOSS-1): index.md (K3) has always listed pages as bare
[[wikilinks]], with no hint of what each page is about until it is opened — the original
nashsu/llm_wiki pattern (and plain usability) calls for a short em-dash gloss next to each
entry, e.g. `- [[Page Title]] — {summary}`.

Adds a nullable ``summary`` text column to ``pages``, populated by a simple (non-LLM)
first-paragraph extraction:
  - on the write path (app.ingest.writer.write_wiki_page) for every new/updated page, and
  - via a one-off backfill script (backend/scripts/backfill_page_summary.py) for existing
    pages written before this migration.

NULL for pages with an empty body or written before the backfill has run. Additive,
non-destructive; no data loss on downgrade (column is simply dropped).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pages",
        sa.Column(
            "summary",
            sa.Text(),
            nullable=True,
            comment=(
                "Short gloss derived from the page's first content paragraph (no LLM); "
                "shown as the em-dash gloss next to the wikilink in index.md (K3, 1.9.4 W6)."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("pages", "summary")
