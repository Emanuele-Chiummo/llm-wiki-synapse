"""provider_config UI API-key (encrypted) + reasoning_effort — W1 (F17, §12 amendment)

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-10

Schema change (two new nullable columns on provider_config):

  ALTER TABLE provider_config
    ADD COLUMN api_key_encrypted BYTEA NULL;
  ALTER TABLE provider_config
    ADD COLUMN reasoning_effort  TEXT  NULL;

api_key_encrypted:
  - BYTEA NULL — Fernet-encrypted UI-supplied provider API key (master key from
    SYNAPSE_SECRET_KEY env; app/secrets_crypto.py). The plaintext is NEVER stored and NEVER
    returned by any endpoint. NULL ⇒ no UI key; the provider layer falls back to env-var keys
    (ANTHROPIC_API_KEY / OPENAI_API_KEY). Zero-friction upgrade — existing rows keep env-key
    behaviour.

reasoning_effort:
  - TEXT NULL — auto|off|low|medium|high|max|custom. NULL/auto ⇒ provider default (no override).
    Threaded into the ApiProvider where the backend supports it; degrade-safe when unsupported.

Downgrade drops both columns.

D2/ER: run `make er` (or scripts/generate_er.py) after applying to regenerate
docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add api_key_encrypted (BYTEA) + reasoning_effort (TEXT) to provider_config (W1)."""
    op.add_column(
        "provider_config",
        sa.Column(
            "api_key_encrypted",
            sa.LargeBinary(),
            nullable=True,
            comment=(
                "W1 (F17, §12 amendment). Fernet-encrypted UI-supplied provider API key "
                "(master key from SYNAPSE_SECRET_KEY env; app/secrets_crypto.py). "
                "NULL = no UI key; provider layer falls back to env-var keys. "
                "Plaintext NEVER stored/returned. Migration 0026."
            ),
        ),
    )
    op.add_column(
        "provider_config",
        sa.Column(
            "reasoning_effort",
            sa.Text(),
            nullable=True,
            comment=(
                "W1 (F17). Per-provider reasoning/thinking effort: "
                "auto|off|low|medium|high|max|custom. NULL/auto = provider default. "
                "Threaded where supported; degrade-safe otherwise. Migration 0026."
            ),
        ),
    )


def downgrade() -> None:
    """Drop the two W1 columns from provider_config."""
    op.drop_column("provider_config", "reasoning_effort")
    op.drop_column("provider_config", "api_key_encrypted")
