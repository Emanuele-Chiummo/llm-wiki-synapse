"""mcp_oauth_clients + mcp_oauth_tokens — OAuth 2.1/PKCE authorization server for MCP [ADR-0090]

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-20

2.1.6 (ADR-0090): claude.ai's web "Custom connector" UI only speaks OAuth 2.1 + PKCE — it
cannot send a static bearer header the way Claude Desktop's JSON config can. This adds a
minimal, single-operator-oriented authorization server (``/authorize``, ``/token``,
``/register``) so that connector can complete its authorization_code flow against
``/mcp/server``.

``mcp_oauth_clients`` — dynamically-registered (RFC 7591) or JIT-registered (first-seen at
``/authorize``) public clients; PKCE is the confidentiality mechanism, so no client_secret
column.

``mcp_oauth_tokens`` — issued access/refresh token pairs, PBKDF2-hashed (mirrors
``api_tokens.secret_hash`` / ``vault_state.mcp_access_token_hash``, never plaintext).
Authorization codes are NOT persisted (short-lived, in-process store — single-process
deployment assumption already documented for RemoteMcpFlag/McpAuthCache).

Additive, non-destructive. Does not alter the existing static MCP_AUTH_TOKEN / DB-hash
bearer gate (BearerAuthMiddleware) — an OAuth-issued access token is simply an ADDITIONAL
way to satisfy that same gate.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mcp_oauth_clients",
        sa.Column(
            "client_id",
            sa.Text(),
            primary_key=True,
            comment="Opaque public-client identifier — NOT a secret (RFC 6749 §2.1)",
        ),
        sa.Column(
            "client_name",
            sa.Text(),
            nullable=True,
            comment="Operator-facing name presented by the client at registration (RFC 7591)",
        ),
        sa.Column(
            "redirect_uris",
            postgresql.JSONB(),
            nullable=False,
            comment="Registered redirect URI(s); /authorize rejects any URI not in this set",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Row creation time (explicit registration or JIT first-sight)",
        ),
    )

    op.create_table(
        "mcp_oauth_tokens",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="Row identity",
        ),
        sa.Column(
            "client_id",
            sa.Text(),
            nullable=False,
            comment="mcp_oauth_clients.client_id this token pair was issued to",
        ),
        sa.Column(
            "access_token_hash",
            sa.Text(),
            nullable=False,
            comment="PBKDF2-SHA256 hash of the access token. NEVER the plaintext.",
        ),
        sa.Column(
            "refresh_token_hash",
            sa.Text(),
            nullable=False,
            comment="PBKDF2-SHA256 hash of the refresh token. Rotated on every use.",
        ),
        sa.Column(
            "expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Access token expiry",
        ),
        sa.Column(
            "refresh_expires_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            comment="Refresh token expiry",
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
            comment="Row creation time",
        ),
        sa.Column(
            "revoked_at",
            sa.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Soft-delete marker — NULL = active",
        ),
    )
    op.create_index("ix_mcp_oauth_tokens_client_id", "mcp_oauth_tokens", ["client_id"])


def downgrade() -> None:
    op.drop_index("ix_mcp_oauth_tokens_client_id", table_name="mcp_oauth_tokens")
    op.drop_table("mcp_oauth_tokens")
    op.drop_table("mcp_oauth_clients")
