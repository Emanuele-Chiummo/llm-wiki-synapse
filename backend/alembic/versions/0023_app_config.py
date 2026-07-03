"""app_config — runtime config-override key/value table (R11-2 / ADR-0053)

Revision ID: 0023
Revises: 0022
Create Date: 2026-07-03

New table: generic key/value override store for the 8 user-facing runtime settings
(ADR-0053 §2.1). Absent row ⇒ env baseline governs (backward-compat, §2.6).
Reset-to-default is a row DELETE; value is NOT NULL by design.

  CREATE TABLE app_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
  );

Only the 8 ALLOWED_CONFIG_KEYS defined in config_overrides.py may be written via
the API. Unknown keys in the table are silently ignored on load (forward-compat).

D2/ER: run ``make er`` after applying this migration to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the app_config key/value override table (ADR-0053 §2.1)."""
    op.create_table(
        "app_config",
        sa.Column(
            "key",
            sa.Text(),
            primary_key=True,
            comment=(
                "Config key in lower-snake attribute form (e.g. pdf_extractor). "
                "ADR-0053 §2.1."
            ),
        ),
        sa.Column(
            "value",
            sa.Text(),
            nullable=False,
            comment=(
                "Override value as TEXT. NOT NULL — row exists ⇔ override active. "
                "Reset by DELETE. ADR-0053 §2.1."
            ),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Last write time (audit). ADR-0053 §2.1.",
        ),
    )


def downgrade() -> None:
    """Drop the app_config table."""
    op.drop_table("app_config")
