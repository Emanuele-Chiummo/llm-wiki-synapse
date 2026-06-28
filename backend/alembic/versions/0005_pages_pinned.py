"""pages.pinned column — manual node position lock for graph drag-and-drop

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-28

Changes:
  pages.pinned  BOOLEAN NOT NULL DEFAULT false
                Set to true by PATCH /pages/{id}/position; cleared on cascade-delete.
                Preserved across FR recomputes: engine reads the flag and overwrites
                FR-computed (x,y) with the stored (x,y) for pinned nodes before persist.

References:
  Feature A — node pinning + persisted manual position
  I2 — layout stays server-side; PATCH does NOT trigger FR or bump data_version
  I8 — D-artifacts: models.py + schema.mmd updated; alembic upgrade head clean
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pages",
        sa.Column(
            "pinned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment=(
                "True when the user manually positioned this node via PATCH /pages/{id}/position. "
                "Engine preserves pinned coords across FR recomputes (Feature A)."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("pages", "pinned")
