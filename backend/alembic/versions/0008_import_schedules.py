"""import_schedules — M4-EXT scheduled folder import config table (ADR-0020 §4.1)

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-28

New table (M4-EXT Feature S — ADR-0020 §4.1):

  import_schedules
    id                   UUID PK default gen_random_uuid()
    vault_id             String      NOT NULL UNIQUE           -- one row per vault
    enabled              Boolean     NOT NULL DEFAULT false     -- scheduler no-op while false
    source_dir           Text        NULL                       -- container-visible path
    frequency            Text        NOT NULL DEFAULT '1h'      -- '15m'|'1h'|'6h'|'daily' (I7)
    last_run_at          TIMESTAMPTZ NULL                       -- NULL until first scan
    last_status          Text        NULL                       -- ok|error|running|dir_missing|NULL
    last_imported_count  Integer     NOT NULL DEFAULT 0         -- files copied on last scan
    last_error           Text        NULL                       -- error detail on failed scan
    created_at           TIMESTAMPTZ NOT NULL default now()
    updated_at           TIMESTAMPTZ NOT NULL default now()

References:
  ADR-0020 §4.1 — import_schedules schema
  I7 — frequency enum bounds the scan interval; per-scan MAX_FILES + MAX_SECONDS caps
  I8 — docs/er/schema.mmd (make er) and docs/api/openapi.json (make openapi) regenerated
  I9 — no new tracking table; per-file cost in existing ingest_runs ledger
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "import_schedules",
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
            comment="Logical vault identifier — one row per vault (UNIQUE)",
        ),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="Scheduler is a no-op while false (ADR-0020 §4.1)",
        ),
        sa.Column(
            "source_dir",
            sa.Text(),
            nullable=True,
            comment=(
                "Container-visible absolute path to scan (e.g. /import). "
                "NULL until set. Must be mounted into the container (ADR-0020 §7)."
            ),
        ),
        sa.Column(
            "frequency",
            sa.Text(),
            server_default=sa.text("'1h'"),
            nullable=False,
            comment="Scan interval enum: '15m' | '1h' | '6h' | 'daily' (I7 — bounded)",
        ),
        sa.Column(
            "last_run_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
            comment="Timestamp of the last completed scan; NULL if never run",
        ),
        sa.Column(
            "last_status",
            sa.Text(),
            nullable=True,
            comment=(
                "Outcome of the last scan: 'ok' | 'error' | 'running' | "
                "'skipped_disabled' | 'dir_missing' | NULL (never run)"
            ),
        ),
        sa.Column(
            "last_imported_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="Number of files copied (new/changed) during the last scan",
        ),
        sa.Column(
            "last_error",
            sa.Text(),
            nullable=True,
            comment="Human-readable error from the last failed scan; NULL on success",
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
            comment="Updated on every PUT or scan completion",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vault_id", name="uq_import_schedules_vault_id"),
    )


def downgrade() -> None:
    op.drop_table("import_schedules")
