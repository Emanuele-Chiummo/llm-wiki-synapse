"""vault_state remote MCP write tools runtime toggle — ADR-0072

Revision ID: 0030
Revises: 0029
Create Date: 2026-07-13

Schema change (ADR-0072 §1 — one new nullable column on vault_state):

  ALTER TABLE vault_state
    ADD COLUMN remote_mcp_write_enabled BOOLEAN NULL;

remote_mcp_write_enabled:
  - BOOLEAN NULL — runtime toggle for write tools (write_page, resolve_review,
    trigger_source_rescan) on the HTTP MCP surface (ADR-0072 §1).
  - NULL = not set in DB; env ``MCP_REMOTE_WRITE_ENABLED`` is the fallback.
    This means existing deployments keep their env behaviour until the owner first
    toggles from the Settings UI, at which point the DB becomes authoritative.
    No data migration of existing env config is needed (ADR-0072 §1: nullable-default-NULL
    means zero-migration cost for existing deployments).
  - When NOT NULL the DB value is authoritative (DB-wins-else-env, mirrors the
    _ClipConfigCache pattern — ADR-0040).
  - Set via PUT /mcp/remote-write; reflected in GET /mcp/info as remote_write_enabled.
  - The in-process _mcp_write_flag (RemoteMcpFlag) is the process cache of this column;
    loaded at startup, refreshed on each PUT call (ADR-0072 §2).

Downgrade:
  - Drop ``remote_mcp_write_enabled`` — no data migration needed (was nullable).

D2/ER: run ``make er`` (or scripts/generate_er.py) after applying to regenerate
docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Add remote_mcp_write_enabled (BOOLEAN, nullable) to vault_state (ADR-0072 §1).

    NULL default = existing deployments fall back to MCP_REMOTE_WRITE_ENABLED env var
    until the owner first toggles from the Settings UI (zero migration cost).
    """
    op.add_column(
        "vault_state",
        sa.Column(
            "remote_mcp_write_enabled",
            sa.Boolean(),
            nullable=True,
            comment=(
                "Runtime toggle for write tools on the HTTP MCP surface (ADR-0072 §1). "
                "NULL = not set in DB; env MCP_REMOTE_WRITE_ENABLED is the fallback. "
                "When NOT NULL, DB value is authoritative. Migration 0030."
            ),
        ),
    )


def downgrade() -> None:
    """
    Drop remote_mcp_write_enabled from vault_state.

    No data migration needed — the column is nullable and the env var fallback
    (MCP_REMOTE_WRITE_ENABLED) takes over again after removal.
    """
    op.drop_column("vault_state", "remote_mcp_write_enabled")
