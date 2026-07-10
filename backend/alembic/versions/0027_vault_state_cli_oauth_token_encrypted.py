"""vault_state CLI OAuth token at-rest encryption — W7 (security hardening)

Revision ID: 0027
Revises: 0026
Create Date: 2026-07-10

Schema change (W7 — one new column on vault_state):

  ALTER TABLE vault_state
    ADD COLUMN cli_oauth_token_encrypted BYTEA NULL;

cli_oauth_token_encrypted:
  - BYTEA NULL — Fernet-encrypted CLI subscription OAuth token (master key from
    SYNAPSE_SECRET_KEY env; app/secrets_crypto.py).
  - NULL = no UI token; env ``CLAUDE_CODE_OAUTH_TOKEN`` / ``CLAUDE_CODE_USE_SUBSCRIPTION``
    govern. When NOT NULL the DB value is authoritative: it is decrypted at startup and
    injected into the spawned ``claude`` CLI's env as CLAUDE_CODE_OAUTH_TOKEN (ADR-0043 §2.3).
  - Fernet-encrypted at rest (AES-128-CBC + HMAC-SHA256).  The plaintext is NEVER stored or
    logged.  The master key (SYNAPSE_SECRET_KEY) must be set to store a new token via
    PUT /provider/cli-auth — the endpoint returns HTTP 400 when the key is absent (fail-closed
    on write; degrade-safe on read).
  - Zero-friction upgrade: if SYNAPSE_SECRET_KEY is available at migration time, any existing
    plaintext value in ``cli_oauth_token`` is encrypted in-place into this column and the
    plaintext column is nulled.  If the key is absent the column is added empty (NULL) and the
    operator must re-store the token via PUT /provider/cli-auth once the key is configured.
    The legacy ``cli_oauth_token`` TEXT column is KEPT in this migration for rollback safety
    (downgrade re-populates it from the ciphertext when the key is available).

Downgrade:
  - If SYNAPSE_SECRET_KEY is available: decrypt ``cli_oauth_token_encrypted`` back into
    ``cli_oauth_token`` for each row.
  - Drop ``cli_oauth_token_encrypted``.
  - The legacy plaintext column is never dropped in this migration; a future cleanup migration
    (0028+) may remove it once the encrypted column is proven stable in production.

D2/ER: run ``make er`` (or scripts/generate_er.py) after applying to regenerate
docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import logging
import os

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_SECRET_KEY_ENV = "SYNAPSE_SECRET_KEY"  # noqa: S105 — env-var NAME, not a secret


def _get_fernet() -> object:  # returns Fernet | None — Fernet imported lazily inside
    """
    Return a Fernet instance or None if SYNAPSE_SECRET_KEY is absent/invalid.

    Imported lazily so this module loads cleanly even without the cryptography package
    at import time (Alembic autogenerate mode).
    """
    raw = os.environ.get(_SECRET_KEY_ENV)
    if not raw:
        return None
    try:
        from cryptography.fernet import Fernet

        return Fernet(raw.encode("utf-8"))
    except Exception:  # noqa: BLE001 — malformed key; degrade gracefully
        return None


def upgrade() -> None:
    """
    Add cli_oauth_token_encrypted (BYTEA) to vault_state (W7).

    Encrypt-in-place strategy:
      - If SYNAPSE_SECRET_KEY is available: encrypt any existing cli_oauth_token values
        into cli_oauth_token_encrypted; null cli_oauth_token for those rows.
      - If key is absent: add the column empty (NULL).  Operator must re-store the token
        via PUT /provider/cli-auth once the key is configured.
    """
    op.add_column(
        "vault_state",
        sa.Column(
            "cli_oauth_token_encrypted",
            sa.LargeBinary(),
            nullable=True,
            comment=(
                "W7 (ADR-0043 amendment). Fernet-encrypted CLI subscription OAuth token "
                "(master key from SYNAPSE_SECRET_KEY env; app/secrets_crypto.py). "
                "NULL = no UI token; env governs. "
                "Plaintext NEVER stored/logged. Migration 0027."
            ),
        ),
    )

    # Migrate existing plaintext values if the master key is available.
    fernet = _get_fernet()
    if fernet is not None:
        conn = op.get_bind()
        # Fetch rows with a non-NULL, non-empty plaintext token.
        rows = conn.execute(
            sa.text(
                "SELECT vault_id, cli_oauth_token FROM vault_state "
                "WHERE cli_oauth_token IS NOT NULL AND cli_oauth_token <> ''"
            )
        ).fetchall()

        for vault_id, plaintext in rows:
            ciphertext: bytes = fernet.encrypt(plaintext.encode("utf-8"))  # type: ignore[attr-defined]
            conn.execute(
                sa.text(
                    "UPDATE vault_state "
                    "SET cli_oauth_token_encrypted = :enc, cli_oauth_token = NULL "
                    "WHERE vault_id = :vid"
                ),
                {"enc": ciphertext, "vid": vault_id},
            )
            logger.info("migration 0027: encrypted cli_oauth_token for vault_id=%r (W7)", vault_id)
    else:
        # Count rows that have a plaintext token so the operator knows action is needed.
        conn = op.get_bind()
        result = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM vault_state "
                "WHERE cli_oauth_token IS NOT NULL AND cli_oauth_token <> ''"
            )
        ).scalar()
        if result and result > 0:
            logger.warning(
                "migration 0027: SYNAPSE_SECRET_KEY is unset — %d vault(s) have a plaintext "
                "cli_oauth_token that could NOT be encrypted in-place.  The cli_oauth_token "
                "column still holds the plaintext value for those rows (the read path in "
                "_load_cli_auth_config_cache falls back to it with a security warning).  "
                "Set SYNAPSE_SECRET_KEY and re-store the token via PUT /provider/cli-auth "
                "to complete the migration.",
                result,
            )
        else:
            logger.info(
                "migration 0027: SYNAPSE_SECRET_KEY is unset but no plaintext tokens exist "
                "— cli_oauth_token_encrypted column added empty (W7)."
            )


def downgrade() -> None:
    """
    Decrypt cli_oauth_token_encrypted back to cli_oauth_token (if key available), then drop
    the encrypted column.
    """
    fernet = _get_fernet()
    if fernet is not None:
        conn = op.get_bind()
        rows = conn.execute(
            sa.text(
                "SELECT vault_id, cli_oauth_token_encrypted FROM vault_state "
                "WHERE cli_oauth_token_encrypted IS NOT NULL"
            )
        ).fetchall()

        for vault_id, ciphertext in rows:
            try:
                plaintext = fernet.decrypt(bytes(ciphertext)).decode("utf-8")  # type: ignore[attr-defined]
                conn.execute(
                    sa.text(
                        "UPDATE vault_state " "SET cli_oauth_token = :pt " "WHERE vault_id = :vid"
                    ),
                    {"pt": plaintext, "vid": vault_id},
                )
                logger.info(
                    "migration 0027 downgrade: decrypted cli_oauth_token_encrypted for "
                    "vault_id=%r",
                    vault_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "migration 0027 downgrade: failed to decrypt cli_oauth_token_encrypted "
                    "for vault_id=%r (%s) — cli_oauth_token left NULL for this row",
                    vault_id,
                    type(exc).__name__,
                )
    else:
        logger.warning(
            "migration 0027 downgrade: SYNAPSE_SECRET_KEY is unset — cannot decrypt "
            "cli_oauth_token_encrypted; cli_oauth_token will remain NULL for rows that had "
            "an encrypted token."
        )

    op.drop_column("vault_state", "cli_oauth_token_encrypted")
