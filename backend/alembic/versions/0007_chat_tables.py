"""chat tables — conversations + messages (F6, ADR-0019 §2.5)

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-28

New tables (M4 Phase 3 chat persistence — ADR-0019 §2.5):

  conversations
    id          UUID PK default gen_random_uuid()
    vault_id    String      NOT NULL                 -- scope (pages/edges pattern)
    title       Text        NULL                     -- user-set / first-prompt-derived
    created_at  TIMESTAMPTZ NOT NULL default now()
    updated_at  TIMESTAMPTZ NOT NULL default now()   -- bumped per turn (AC-F6-1 restore)
    deleted_at  TIMESTAMPTZ NULL                      -- soft-delete (ADR-0005 pattern)
    INDEX (vault_id, updated_at) WHERE deleted_at IS NULL

  messages
    id              UUID PK default gen_random_uuid()
    conversation_id UUID FK → conversations.id NOT NULL
    role            Text          NOT NULL            -- 'user' | 'assistant' | 'system'
    content         Text          NOT NULL            -- RAW, incl. literal <think>… (AC-F7-2)
    citations       JSONB         NULL                -- RESERVED M5; [] in M4
    provider_type   Text          NULL                -- audit (assistant only)
    model_id        Text          NULL                -- audit
    input_tokens    Integer       NOT NULL default 0  -- I7 persistent cost record
    output_tokens   Integer       NOT NULL default 0
    total_cost_usd  Numeric(10,4) NOT NULL default 0  -- 0.0000 for local/cli (ADR-0009)
    created_at      TIMESTAMPTZ   NOT NULL default now()
    INDEX (conversation_id, created_at)

NB: there is intentionally NO chat_runs table (ADR-0019 §2.2 / Do-NOT #9). Chat cost lives on
the messages columns + the done event.

References:
  ADR-0019 §2.5 — chat persistence schema
  I7 — per-message token/cost columns + total_cost_usd
  I8 — docs/er/schema.mmd (make er) and docs/api/openapi.json (make openapi) regenerated
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── conversations ──────────────────────────────────────────────────────────
    op.create_table(
        "conversations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            comment="Conversation identity",
        ),
        sa.Column(
            "vault_id",
            sa.String(),
            nullable=False,
            comment="Logical vault scope; from VAULT_ID env var",
        ),
        sa.Column(
            "title",
            sa.Text(),
            nullable=True,
            comment="User-set or first-prompt-derived title; NULL until set",
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Row creation time",
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Bumped on each chat turn; drives last-active restore (AC-F6-1)",
        ),
        sa.Column(
            "deleted_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
            comment="NULL = live; set = soft-deleted (ADR-0005)",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversations_vault_updated_live",
        "conversations",
        ["vault_id", "updated_at"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ── messages ───────────────────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            comment="Message identity",
        ),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="FK → conversations.id",
        ),
        sa.Column(
            "role",
            sa.Text(),
            nullable=False,
            comment="'user' | 'assistant' | 'system'",
        ),
        sa.Column(
            "content",
            sa.Text(),
            nullable=False,
            comment="RAW content, incl. literal <think>… un-mutated (AC-F7-2)",
        ),
        sa.Column(
            "citations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="RESERVED for M5 [n] citations; always [] in M4",
        ),
        sa.Column(
            "provider_type",
            sa.Text(),
            nullable=True,
            comment="Backend that produced an assistant msg (audit)",
        ),
        sa.Column(
            "model_id",
            sa.Text(),
            nullable=True,
            comment="Resolved model (audit)",
        ),
        sa.Column(
            "input_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Prompt tokens for an assistant turn (I7)",
        ),
        sa.Column(
            "output_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Completion tokens for an assistant turn (I7)",
        ),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(precision=10, scale=4),
            server_default=sa.text("0"),
            nullable=False,
            comment="0.0000 for local/cli (ADR-0009); returned in done event (I7)",
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Row creation time; messages ordered created_at ASC",
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_messages_conversation_created",
        "messages",
        ["conversation_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_messages_conversation_created", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_vault_updated_live", table_name="conversations")
    op.drop_table("conversations")
