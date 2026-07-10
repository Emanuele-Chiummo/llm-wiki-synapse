"""import_schedules wider Source-Watch types — P3-c (v1.5 LLM Wiki parity)

Revision ID: 0028
Revises: 0027
Create Date: 2026-07-10

Schema change (P3-c — three new nullable columns on import_schedules):

  ALTER TABLE import_schedules
    ADD COLUMN allowed_extensions TEXT    NULL;
    ADD COLUMN excluded_folders   TEXT    NULL;
    ADD COLUMN max_size_mb        INTEGER NULL;

allowed_extensions:
  - Comma-separated file extensions the scheduled scan imports (e.g. '.pdf,.csv').
  - NULL → default wider set (text + all extractable formats: csv/html/mdx/rtf/odt/ods/odp
    in addition to pdf/docx/pptx/xlsx). Back-compatible: existing rows read as NULL and get
    the new wider default.

excluded_folders:
  - Comma-separated folder names skipped during the recursive scan (matched against path parts).
  - NULL → nothing excluded.

max_size_mb:
  - Max file size in MB the scan will import; larger files are skipped (I7 bound).
  - NULL → no size cap.

Downgrade: drop the three columns.

D2/ER: run ``make er`` after applying to regenerate docs/er/schema.mmd (I8).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add allowed_extensions, excluded_folders, max_size_mb to import_schedules (P3-c)."""
    op.add_column(
        "import_schedules",
        sa.Column(
            "allowed_extensions",
            sa.Text(),
            nullable=True,
            comment=(
                "Comma-separated extensions the scan imports (e.g. '.pdf,.csv'). "
                "NULL → default wider set (text + all extractable). P3-c (v1.5)."
            ),
        ),
    )
    op.add_column(
        "import_schedules",
        sa.Column(
            "excluded_folders",
            sa.Text(),
            nullable=True,
            comment=(
                "Comma-separated folder names skipped during the scan. "
                "NULL → nothing excluded. P3-c (v1.5)."
            ),
        ),
    )
    op.add_column(
        "import_schedules",
        sa.Column(
            "max_size_mb",
            sa.Integer(),
            nullable=True,
            comment=(
                "Max file size in MB the scan imports; larger files skipped (I7). "
                "NULL → no cap. P3-c (v1.5)."
            ),
        ),
    )


def downgrade() -> None:
    """Drop the three P3-c columns."""
    op.drop_column("import_schedules", "max_size_mb")
    op.drop_column("import_schedules", "excluded_folders")
    op.drop_column("import_schedules", "allowed_extensions")
