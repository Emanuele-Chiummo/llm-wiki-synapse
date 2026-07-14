"""vault_state.output_language — ADR-0081 (llm_wiki onboarding parity)

Revision ID: 0032
Revises: 0031
Create Date: 2026-07-14

Additive, non-destructive migration for Synapse 1.7.0:

* vault_state.output_language — per-vault AI output language chosen at vault
  creation (llm_wiki create-dialog parity). ISO-639-1 code. NULL = auto
  (detect from source content), which is also the behavior for every vault
  created before 1.7.0.

No backfill: existing vaults keep NULL and behave exactly as before.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vault_state",
        sa.Column(
            "output_language",
            sa.String(length=16),
            nullable=True,
            comment=(
                "Mandatory AI output language chosen at vault creation "
                "(ADR-0081, llm_wiki parity). ISO-639-1 code. NULL = auto / "
                "pre-1.7.0 vault."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("vault_state", "output_language")
