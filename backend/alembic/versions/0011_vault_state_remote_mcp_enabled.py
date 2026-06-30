"""vault_state.remote_mcp_enabled — ADR-0032 runtime toggle for remote MCP HTTP surface

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-29

Schema change (ADR-0032 §2.1 / §3):

  ALTER TABLE vault_state
    ADD COLUMN remote_mcp_enabled BOOLEAN NOT NULL DEFAULT false;

This is the single schema change for the remote MCP runtime toggle feature (ADR-0032).
The column is:
  - NOT NULL DEFAULT false   (fail-closed by design — remote is OFF unless explicitly enabled)
  - server_default "false"   (all existing rows are backfilled to false by the DEFAULT clause)
  - type: BOOLEAN            (Postgres native boolean; maps to Mapped[bool] in SQLAlchemy)

Downgrade removes the column.

D2/ER: run `make er` after applying this migration to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add remote_mcp_enabled column to vault_state; backfills false on existing rows."""
    op.add_column(
        "vault_state",
        sa.Column(
            "remote_mcp_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=(
                "Runtime toggle for the remote (HTTP) MCP surface (ADR-0032 §2.1). "
                "Default OFF; requires MCP_AUTH_TOKEN to be set before enabling."
            ),
        ),
    )


def downgrade() -> None:
    """Drop remote_mcp_enabled column from vault_state."""
    op.drop_column("vault_state", "remote_mcp_enabled")
