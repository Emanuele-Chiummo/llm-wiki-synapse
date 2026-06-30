"""vault_state.mcp_access_token_hash + mcp_allow_without_token — ADR-0033 §3

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-30

Schema changes (ADR-0033 §3 — two new columns on vault_state):

  ALTER TABLE vault_state
    ADD COLUMN mcp_access_token_hash TEXT NULL;

  ALTER TABLE vault_state
    ADD COLUMN mcp_allow_without_token BOOLEAN NOT NULL DEFAULT false;

mcp_access_token_hash:
  - TEXT NULL — stores PBKDF2-HMAC-SHA256 salted hash as
    ``pbkdf2_sha256$<iters>$<salt_b64>$<hash_b64>`` (ADR-0033 §2.1).
  - NULL on upgrade ⇒ no UI token exists yet; env-bootstrap or no-token behaviour
    holds unchanged (zero-friction upgrade).
  - NEVER stores plaintext. The token plaintext is shown once at generation time
    and is never re-displayable (ADR-0033 §2.1 / ADR-0008 reconciliation).

mcp_allow_without_token:
  - BOOLEAN NOT NULL DEFAULT false — fail-closed by design (ADR-0033 §2.3).
  - When ON: private-source requests (loopback/CGNAT/RFC1918/link-local/ULA)
    may access /mcp/server without a bearer token. Public sources (Cloudflare
    tunnel — CF-Connecting-IP/CF-Ray) are NEVER exempted regardless of this flag.
  - Persisted here; read into in-process _McpAllowWithoutTokenFlag at startup
    (mirrors remote_mcp_enabled / RemoteMcpFlag pattern, ADR-0032 §2.2).

Both columns default to fail-closed values:
  - mcp_access_token_hash = NULL  ⇒ no DB token; env-bootstrap or none
  - mcp_allow_without_token = false  ⇒ private sources still need a token

Downgrade drops both columns.

D2/ER: run `make er` after applying this migration to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add mcp_access_token_hash and mcp_allow_without_token to vault_state."""
    op.add_column(
        "vault_state",
        sa.Column(
            "mcp_access_token_hash",
            sa.Text(),
            nullable=True,
            comment=(
                "Salted PBKDF2-HMAC-SHA256 hash of the UI-set MCP access token (ADR-0033 §2.1). "
                "Format: pbkdf2_sha256$<iters>$<salt_b64>$<hash_b64>. "
                "NULL = no UI token (fall back to MCP_AUTH_TOKEN env bootstrap or none). "
                "NEVER stores plaintext; token shown once at generation time."
            ),
        ),
    )
    op.add_column(
        "vault_state",
        sa.Column(
            "mcp_allow_without_token",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment=(
                "Allow token-less access to /mcp/server from PRIVATE sources only "
                "(loopback/CGNAT/RFC1918/link-local/ULA). "
                "Default false — fail-closed (ADR-0033 §2.3). "
                "Public sources (Cloudflare tunnel) are NEVER exempted regardless of this flag."
            ),
        ),
    )


def downgrade() -> None:
    """Drop mcp_access_token_hash and mcp_allow_without_token from vault_state."""
    op.drop_column("vault_state", "mcp_allow_without_token")
    op.drop_column("vault_state", "mcp_access_token_hash")
