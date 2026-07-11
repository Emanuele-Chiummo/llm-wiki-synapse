"""vault_state web-search cloud provider API keys (encrypted) — P3-e (ADR-0071)

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-10

Schema change (one new nullable column on vault_state):

  ALTER TABLE vault_state
    ADD COLUMN web_search_api_keys_encrypted BYTEA NULL;

web_search_api_keys_encrypted:
  - BYTEA NULL — Fernet-encrypted JSON map {provider: api_key} for the opt-in cloud web-search
    providers (tavily/serpapi/firecrawl/brave). Master key from SYNAPSE_SECRET_KEY env
    (app/secrets_crypto.py).
  - NULL = no UI-stored keys; the env `{PROVIDER}_API_KEY` vars govern. When a provider's key is
    present in this blob the DB value wins over env.
  - Plaintext is NEVER stored, logged, or returned by any endpoint. GET /web-search/provider-keys
    exposes only a masked posture (configured flag + last-4). Requires SYNAPSE_SECRET_KEY to store
    (PUT returns 400 when the key is absent). Fail-closed on tampered ciphertext.

Downgrade: drop the column.

D2/ER: run ``make er`` after applying to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add web_search_api_keys_encrypted (BYTEA) to vault_state (P3-e / ADR-0071)."""
    op.add_column(
        "vault_state",
        sa.Column(
            "web_search_api_keys_encrypted",
            sa.LargeBinary(),
            nullable=True,
            comment=(
                "P3-e (ADR-0071). Fernet-encrypted JSON {provider: api_key} for cloud web-search "
                "providers. Master key from SYNAPSE_SECRET_KEY. NULL = env vars govern. "
                "Plaintext NEVER stored/returned. Migration 0029."
            ),
        ),
    )


def downgrade() -> None:
    """Drop web_search_api_keys_encrypted."""
    op.drop_column("vault_state", "web_search_api_keys_encrypted")
