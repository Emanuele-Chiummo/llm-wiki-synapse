"""v0.2 tables — provider_config, ingest_runs, links (one migration, ADR-0008)

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-28

Tables added:
  provider_config — F17 backend selection per scope (global|vault|operation)  (ADR-0008 §2)
  ingest_runs     — per-run cost/convergence audit ledger (I7, ADR-0008 §4)
  links           — K5 wikilink edges; source_page_id → target_title (ADR-0008 §5)

All three tables ship in ONE migration to honour the scope-§2 "one schema-change event"
decision (CLAUDE.md §3 I8, ADR-0008 §5). A data migration at the end seeds the single
global provider_config row using environment-derived model IDs (AC-F17-8, ADR-0008 §3 —
model IDs live in DB rows only, never in source literals).

provider_config holds NO API key column (§12 — keys are env-only).
"""

from __future__ import annotations

import os
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Default global model IDs read from the environment at migration time (AC-F17-8).
# These are NEVER literals in application code — the app always reads from provider_config.
_DEFAULT_MODEL_ID = os.environ.get("DEFAULT_MODEL_ID", "claude-sonnet-4-6")
_DEFAULT_FALLBACK_MODEL_ID = os.environ.get(
    "DEFAULT_FALLBACK_MODEL_ID", "claude-haiku-4-5-20251001"
)


def upgrade() -> None:
    # ── provider_config ────────────────────────────────────────────────────────
    op.create_table(
        "provider_config",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            comment="Row identity",
        ),
        sa.Column(
            "scope",
            sa.Text(),
            nullable=False,
            comment="global | vault | operation (ADR-0008 §2)",
        ),
        sa.Column(
            "operation",
            sa.Text(),
            nullable=True,
            comment="ingest | chat | lint; NULL unless scope='operation' (AQ-v0.2-5)",
        ),
        sa.Column(
            "vault_id",
            sa.String(),
            nullable=True,
            comment="NULL at global scope; required at vault/operation scope",
        ),
        sa.Column(
            "provider_type",
            sa.Text(),
            nullable=False,
            comment="local | api | cli — selects the InferenceProvider backend (I6)",
        ),
        sa.Column(
            "model_id",
            sa.Text(),
            nullable=False,
            comment=(
                "Model name e.g. claude-sonnet-4-6; value lives ONLY in DB rows (AC-F17-8). "
                "Seeded by this data migration from env var DEFAULT_MODEL_ID."
            ),
        ),
        sa.Column(
            "base_url",
            sa.Text(),
            nullable=True,
            comment="OpenAI-compatible endpoint for ApiProvider; NULL for Anthropic/local default",
        ),
        sa.Column(
            "max_iter",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
            comment="Orchestrated-loop iteration cap (I7, ADR-0009)",
        ),
        sa.Column(
            "token_budget",
            sa.Integer(),
            server_default=sa.text("60000"),
            nullable=False,
            comment="Loop token budget (I7); 60000 orchestrated / 100000 cli (ADR-0009)",
        ),
        sa.Column(
            "is_fallback",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="Marks the single fallback row for a scope (ADR-0009 §fallback)",
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
            comment="Updated on every change",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── ingest_runs ────────────────────────────────────────────────────────────
    op.create_table(
        "ingest_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            comment="Row identity",
        ),
        sa.Column(
            "vault_id",
            sa.String(),
            nullable=False,
            comment="Vault this run belongs to",
        ),
        sa.Column(
            "page_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Originating source page; NULL on a pre-write failure",
        ),
        sa.Column(
            "provider_name",
            sa.Text(),
            nullable=False,
            comment="Provider class name (e.g. OllamaProvider) — AUDIT ONLY, never routed on (I6)",
        ),
        sa.Column(
            "provider_type",
            sa.Text(),
            nullable=False,
            comment="local | api | cli (audit)",
        ),
        sa.Column(
            "model_id",
            sa.Text(),
            nullable=False,
            comment="Resolved model used (audit)",
        ),
        sa.Column(
            "route",
            sa.Text(),
            nullable=False,
            comment="orchestrated | delegated (capability-aware routing outcome)",
        ),
        sa.Column(
            "max_iter_used",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Iterations actually consumed (1..max_iter); 0 for delegated",
        ),
        sa.Column(
            "total_tokens",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="input+output tokens across all iterations (I7)",
        ),
        sa.Column(
            "total_cost_usd",
            sa.Numeric(10, 4),
            server_default=sa.text("0"),
            nullable=False,
            comment="0.0000 for local/cli (ADR-0009); logged per run (I7)",
        ),
        sa.Column(
            "converged",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="True if a valid batch was produced within max_iter",
        ),
        sa.Column(
            "cost_anomaly",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="True if total_cost_usd > 1.00 (ADR-0009 §3)",
        ),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Run start time",
        ),
        sa.Column(
            "finished_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Run finish time",
        ),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── links (K5) ─────────────────────────────────────────────────────────────
    op.create_table(
        "links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            comment="Row identity",
        ),
        sa.Column(
            "source_page_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            comment="FK → pages.id; the page that contains the wikilink (K5)",
        ),
        sa.Column(
            "target_title",
            sa.Text(),
            nullable=False,
            comment="The [[Target]] title string as written (K5)",
        ),
        sa.Column(
            "target_page_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Resolved FK → pages.id; NULL while the target page does not exist (K5, v0.3)",
        ),
        sa.Column(
            "alias",
            sa.Text(),
            nullable=True,
            comment="The |alias part of [[Target|alias]], if present (K5)",
        ),
        sa.Column(
            "dangling",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="True when target_page_id is unresolved (AC-K5-5); warn-not-error path",
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Row creation time",
        ),
        sa.ForeignKeyConstraint(["source_page_id"], ["pages.id"]),
        sa.ForeignKeyConstraint(["target_page_id"], ["pages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # Index to retrieve all links from a given source page efficiently.
    op.create_index("ix_links_source_page_id", "links", ["source_page_id"])
    # Index to look up links that resolve to a specific target page (for v0.3 graph edges).
    op.create_index("ix_links_target_page_id", "links", ["target_page_id"])

    # ── Data migration: seed one global provider_config row (AC-F17-8) ──────────
    # Model IDs come from environment vars at migration time — NEVER literals in app code.
    # A missing global row is a hard config error at runtime (I6, ADR-0008 §2).
    op.execute(
        sa.text(
            "INSERT INTO provider_config "
            "(id, scope, operation, vault_id, provider_type, model_id, base_url, "
            " max_iter, token_budget, is_fallback, created_at, updated_at) "
            "VALUES "
            "(:id, 'global', NULL, NULL, 'api', :model_id, NULL, "
            " 3, 60000, false, now(), now())"
        ).bindparams(
            id=str(uuid.uuid4()),
            model_id=_DEFAULT_MODEL_ID,
        )
    )
    # Seed a global fallback row using the haiku-class model (ADR-0009 §fallback).
    op.execute(
        sa.text(
            "INSERT INTO provider_config "
            "(id, scope, operation, vault_id, provider_type, model_id, base_url, "
            " max_iter, token_budget, is_fallback, created_at, updated_at) "
            "VALUES "
            "(:id, 'global', NULL, NULL, 'api', :model_id, NULL, "
            " 3, 60000, true, now(), now())"
        ).bindparams(
            id=str(uuid.uuid4()),
            model_id=_DEFAULT_FALLBACK_MODEL_ID,
        )
    )


def downgrade() -> None:
    op.drop_index("ix_links_target_page_id", table_name="links")
    op.drop_index("ix_links_source_page_id", table_name="links")
    op.drop_table("links")
    op.drop_table("ingest_runs")
    op.drop_table("provider_config")
