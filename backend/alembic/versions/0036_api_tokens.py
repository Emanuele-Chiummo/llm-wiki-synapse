"""api_tokens — scoped, revocable API tokens [PF-AUTH-1]

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-17

1.9.4 W4 (audit v2.0 finding PF-AUTH-1): SYNAPSE_AUTH_TOKEN is a single global bootstrap
bearer token (app/auth.py, ADR-0052) — one credential for the whole API, never scoped,
never revocable without an env-var restart. This migration adds ``api_tokens``: operator-
issued, DB-backed tokens that can be scoped to a single vault_id (or left global), marked
read_only, and individually revoked (soft-delete via revoked_at) without touching the
bootstrap token.

Secret storage mirrors the existing PBKDF2 pattern (vault_state.mcp_access_token_hash /
vault_state.clip_access_token, app.runtime_state.hash_token/verify_token) — secret_hash
NEVER stores the plaintext; the plaintext is generated once at creation time and returned
only in the POST /config/api-tokens response body.

Additive, non-destructive. Does not alter SynapseAuthMiddleware's existing bootstrap-token
behaviour (non-breaking extension).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="Row identity",
        ),
        sa.Column(
            "label",
            sa.Text(),
            nullable=False,
            comment="Human-readable description set by the operator at creation time",
        ),
        sa.Column(
            "secret_hash",
            sa.Text(),
            nullable=False,
            comment="PBKDF2-SHA256 hash of the token secret (app.runtime_state.hash_token). "
            "NEVER the plaintext.",
        ),
        sa.Column(
            "vault_id",
            sa.String(),
            nullable=True,
            comment="NULL = global token; non-NULL = scoped to that vault_id",
        ),
        sa.Column(
            "read_only",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="True = token may only be used for GET/HEAD/OPTIONS requests",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Row creation time",
        ),
        sa.Column(
            "last_used_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Timestamp of the most recent successful request authenticated with this token",
        ),
        sa.Column(
            "revoked_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Soft-delete marker — NULL = active",
        ),
    )
    op.create_index("ix_api_tokens_vault_id", "api_tokens", ["vault_id"])


def downgrade() -> None:
    op.drop_index("ix_api_tokens_vault_id", table_name="api_tokens")
    op.drop_table("api_tokens")
