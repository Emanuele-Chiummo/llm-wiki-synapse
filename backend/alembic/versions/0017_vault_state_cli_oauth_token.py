"""vault_state CLI subscription OAuth token — ADR-0043 §2.2

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-01

Schema change (ADR-0043 §2.2 — one new column on vault_state):

  ALTER TABLE vault_state
    ADD COLUMN cli_oauth_token TEXT NULL;

cli_oauth_token:
  - TEXT NULL — plaintext Claude subscription OAuth token for the CLI provider.
  - Produced on the host by ``claude setup-token`` (``sk-ant-oat01-…``).
  - NULL = no UI token; env ``CLAUDE_CODE_OAUTH_TOKEN`` / ``CLAUDE_CODE_USE_SUBSCRIPTION``
    govern. When NOT NULL the DB value is authoritative: it is injected into the spawned
    ``claude`` CLI's env as CLAUDE_CODE_OAUTH_TOKEN AND ANTHROPIC_API_KEY is scrubbed from
    that child env so the subscription wins (ADR-0043 §2.3).
  - Stored PLAINTEXT because it is replayed outbound to the CLI, not verified against an
    incoming request — a hash cannot be replayed (§12 narrowly amended for this one
    credential). NEVER logged; NEVER returned by any endpoint.
  - Default NULL on upgrade ⇒ env-fallback behaviour holds unchanged (zero-friction upgrade).

Downgrade drops the column.

D2/ER: run ``make er`` after applying this migration to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add cli_oauth_token to vault_state (ADR-0043 §2.2)."""
    op.add_column(
        "vault_state",
        sa.Column(
            "cli_oauth_token",
            sa.Text(),
            nullable=True,
            comment=(
                "Plaintext Claude subscription OAuth token for the CLI provider (ADR-0043 §2.1). "
                "Produced on the host by `claude setup-token` (`sk-ant-oat01-…`). "
                "NULL = no UI token; env `CLAUDE_CODE_OAUTH_TOKEN` / `CLAUDE_CODE_USE_SUBSCRIPTION` govern. "
                "When NOT NULL the DB value is authoritative: it is injected into the spawned "
                "`claude` CLI's env as CLAUDE_CODE_OAUTH_TOKEN AND `ANTHROPIC_API_KEY` is scrubbed "
                "from that child env so the subscription wins (ADR-0043 §2.3). "
                "Stored PLAINTEXT because it is replayed outbound to the CLI, not verified against "
                "an incoming request — a hash cannot be replayed (§12 narrowly amended for this one "
                "credential). NEVER logged; NEVER returned by any endpoint. Migration 0017."
            ),
        ),
    )


def downgrade() -> None:
    """Drop cli_oauth_token from vault_state."""
    op.drop_column("vault_state", "cli_oauth_token")
