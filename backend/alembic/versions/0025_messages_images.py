"""messages.images — JSONB column for B2-C1 image attachments

Revision ID: 0025
Revises: 0024
Create Date: 2026-07-06

Adds a nullable JSONB column ``images`` to the ``messages`` table.

Stores ``[{mime: str, data_base64: str}]`` for user-turn image attachments so that
regenerate and message-history replays can reconstruct the full multimodal context
(B2-C1, F17/I6 vision surface). NULL when no images were attached (backward-compat —
every existing row reads as NULL without any fill).

On Postgres the column type is native ``JSONB`` (efficient binary JSON with
operator support). On SQLite (tests) it falls back to ``JSON`` via SQLAlchemy's
``.with_variant()`` — the migration uses ``sa.JSON`` which is portable.

D2/ER: run ``make er`` after applying this migration to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add messages.images JSONB column (B2-C1)."""
    op.add_column(
        "messages",
        sa.Column(
            "images",
            sa.JSON(),
            nullable=True,
            comment=(
                "B2-C1 image attachments: [{mime, data_base64}] for user messages. "
                "NULL when no images attached. Stored for regenerate/history replay. "
                "JSONB on Postgres (via SA dialect); JSON on SQLite (tests)."
            ),
        ),
    )


def downgrade() -> None:
    """Drop messages.images column."""
    op.drop_column("messages", "images")
