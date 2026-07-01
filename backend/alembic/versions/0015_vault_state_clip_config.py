"""vault_state clip runtime config — ADR-0040 §3

Revision ID: 0015
Revises: 0014
Create Date: 2026-06-30

Schema changes (ADR-0040 §3 — three new columns on vault_state):

  ALTER TABLE vault_state
    ADD COLUMN clip_enabled_db BOOLEAN NULL;

  ALTER TABLE vault_state
    ADD COLUMN clip_access_token TEXT NULL;

  ALTER TABLE vault_state
    ADD COLUMN clip_allowed_origins_db TEXT NULL;

clip_enabled_db:
  - BOOLEAN NULL — three-state: NULL = "not set in DB, fall back to CLIP_ENABLED env"
  - When NOT NULL the DB value is authoritative (overrides the env var).
  - Default NULL on upgrade ⇒ env-fallback behaviour holds unchanged (zero-friction upgrade).

clip_access_token:
  - TEXT NULL — plaintext bearer token for POST /clip (set by PUT /clip/config).
  - NULL = no DB token (fall back to CLIP_TOKEN env bootstrap or none).
  - Stored plaintext (not hashed): the clip token is a simple pre-shared secret, not
    a credential that Synapse issues to itself. The one-time-reveal semantics mirror
    MCP (generated_token shown once); subsequent GET /clip/config returns only
    token_configured=bool (never the value). NEVER logged.
  - Mirror of MCP pattern: plaintext in DB means a DB/backup leak yields the token, so
    the UI warns "shown once; rotate if compromised" (same as MCP).

clip_allowed_origins_db:
  - TEXT NULL — comma-separated allowlist of permitted request Origins.
  - NULL = fall back to CLIP_ALLOWED_ORIGINS env var.

Downgrade drops all three columns.

D2/ER: run `make er` after applying this migration to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add clip_enabled_db, clip_access_token, clip_allowed_origins_db to vault_state."""
    op.add_column(
        "vault_state",
        sa.Column(
            "clip_enabled_db",
            sa.Boolean(),
            nullable=True,
            comment=(
                "Runtime enabled-gate for POST /clip (ADR-0040 §3). "
                "NULL = not set in DB; fall back to CLIP_ENABLED env var. "
                "When set, DB value wins over env."
            ),
        ),
    )
    op.add_column(
        "vault_state",
        sa.Column(
            "clip_access_token",
            sa.Text(),
            nullable=True,
            comment=(
                "Plaintext bearer token for POST /clip (ADR-0040 §3). "
                "NULL = no DB token; fall back to CLIP_TOKEN env bootstrap or none. "
                "Shown once at generation time (one-time reveal); NEVER logged. "
                "DB value wins over CLIP_TOKEN env when set."
            ),
        ),
    )
    op.add_column(
        "vault_state",
        sa.Column(
            "clip_allowed_origins_db",
            sa.Text(),
            nullable=True,
            comment=(
                "Comma-separated Origin allowlist for POST /clip (ADR-0040 §3). "
                "NULL = fall back to CLIP_ALLOWED_ORIGINS env var. "
                "DB value wins over env when set."
            ),
        ),
    )


def downgrade() -> None:
    """Drop clip_enabled_db, clip_access_token, clip_allowed_origins_db from vault_state."""
    op.drop_column("vault_state", "clip_allowed_origins_db")
    op.drop_column("vault_state", "clip_access_token")
    op.drop_column("vault_state", "clip_enabled_db")
