"""
Generate docs/er/schema.mmd from SQLAlchemy models (I8, AC-PG-2, AC-DC-3).

Run via: python backend/scripts/generate_er.py
Or via:  make er  (which calls this script)

The ER diagram is regenerated from the live model — never hand-written (ADR-0002, I8).
Source of truth: backend/app/models.py
Output: docs/er/schema.mmd
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure backend/ is on sys.path so `from app.models import ...` works
_HERE = Path(__file__).resolve()
_BACKEND_DIR = _HERE.parent.parent
_REPO_ROOT = _BACKEND_DIR.parent
sys.path.insert(0, str(_BACKEND_DIR))

# Set required env vars with dummy values so Settings() does not blow up
# (we only need the model metadata, not a real DB connection)
import os  # noqa: E402

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://dummy:dummy@localhost/dummy")
os.environ.setdefault("QDRANT_URL", "http://localhost:6333")
os.environ.setdefault("EMBEDDING_URL", "http://localhost:11434/api/embeddings")
os.environ.setdefault("EMBEDDING_DIM", "1024")


# Import Base to get all table metadata
# We use a sync engine for introspection only (no queries)
from app.models import Base  # noqa: E402

OUTFILE = _REPO_ROOT / "docs" / "er" / "schema.mmd"


_TYPE_MAP: dict[str, str] = {
    "UUID": "uuid",
    "VARCHAR": "string",
    "TEXT": "string",
    "INTEGER": "int",
    "BIGINT": "bigint",
    "BOOLEAN": "boolean",
    "JSONB": "jsonb",
    "TIMESTAMP": "timestamptz",
    "NUMERIC": "decimal",
}


def _pg_type(col_type: object) -> str:
    type_name = type(col_type).__name__.upper()
    # Handle dialect-specific types
    for k, v in _TYPE_MAP.items():
        if k in type_name:
            return v
    return type_name.lower()


def _nullable_marker(col: object) -> str:  # type: ignore[type-arg]
    return "" if getattr(col, "nullable", True) else " NOT NULL"


def generate_mermaid_er() -> str:
    from datetime import date

    header = f"<!-- Generated: v0.3→v0.4 transition | {date.today().isoformat()} — ADR-0016: edges.kind; Feature A: pages.pinned -->"
    lines = [header, "erDiagram", ""]

    for table in Base.metadata.sorted_tables:
        lines.append(f"    {table.name.upper()} {{")
        for col in table.columns:
            pg_t = _pg_type(col.type)
            pk_marker = " PK" if col.primary_key else ""
            fk_marker = " FK" if col.foreign_keys else ""
            comment = f'"{col.comment}"' if col.comment else ""
            if comment:
                lines.append(f"        {pg_t} {col.name}{pk_marker}{fk_marker} {comment}")
            else:
                lines.append(f"        {pg_t} {col.name}{pk_marker}{fk_marker}")
        lines.append("    }")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    mermaid = generate_mermaid_er()
    OUTFILE.parent.mkdir(parents=True, exist_ok=True)
    OUTFILE.write_text(mermaid, encoding="utf-8")
    print(f"Generated {OUTFILE}")
    # Quick sanity check
    assert "erDiagram" in mermaid
    assert "PAGES" in mermaid
    assert "VAULT_STATE" in mermaid
    assert "PROVIDER_CONFIG" in mermaid
    assert "INGEST_RUNS" in mermaid
    assert "LINKS" in mermaid
    assert "EDGES" in mermaid
    print(
        "Sanity check passed: all 6 tables present "
        "(PAGES, VAULT_STATE, PROVIDER_CONFIG, INGEST_RUNS, LINKS, EDGES)"
    )


if __name__ == "__main__":
    main()
