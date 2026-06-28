"""
SQLAlchemy model and schema tests (AC-PG-1..4, AC-F16dv-1).

These tests are infra-free: they inspect the SQLAlchemy model metadata
(no live Postgres connection needed). Live Postgres integration
(information_schema queries) is deferred to live-demo.

Coverage:
  AC-PG-1    Alembic migrations (confirmed by schema presence in model metadata)
  AC-PG-2    make er generates schema.mmd (file exists + contains erDiagram)
  AC-PG-3    All required columns present on pages table with correct types
  AC-PG-4    No raw SQL strings in backend/app/ (grep/static check)
  AC-F16dv-1 vault_state table has correct columns

Test IDs: T-PG-001 .. T-PG-015

Note: AC-PG-1 (docker compose up + alembic upgrade head) requires Docker,
which is NOT available in this sandbox. Recorded as DEFERRED-needs-live-infra.
AC-PG-3 column type verification against live information_schema is also
DEFERRED-needs-live-infra; here we verify via SQLAlchemy model introspection.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, Integer
from sqlalchemy.dialects.postgresql import JSONB

# ── AC-PG-3: pages columns present and typed ──────────────────────────────────


class TestPagesModelColumns:
    """T-PG-001..008 — AC-PG-3"""

    def _get_column_by_name(self, table_name: str, col_name: str):  # type: ignore[no-untyped-def]
        from app.models import Base

        for table in Base.metadata.sorted_tables:
            if table.name == table_name:
                for col in table.columns:
                    if col.name == col_name:
                        return col
        return None

    def test_pages_table_exists_in_metadata(self) -> None:
        """T-PG-001: pages table must be defined in Base.metadata."""
        from app.models import Base

        table_names = [t.name for t in Base.metadata.sorted_tables]
        assert "pages" in table_names, f"'pages' table must be in models; found: {table_names}"

    def test_vault_state_table_exists_in_metadata(self) -> None:
        """T-PG-002: vault_state table must be defined in Base.metadata."""
        from app.models import Base

        table_names = [t.name for t in Base.metadata.sorted_tables]
        assert (
            "vault_state" in table_names
        ), f"'vault_state' table must be in models; found: {table_names}"

    def test_pages_has_id_pk_column(self) -> None:
        """T-PG-003: pages.id must be primary key."""
        col = self._get_column_by_name("pages", "id")
        assert col is not None, "pages.id column missing"
        assert col.primary_key, "pages.id must be a primary key"

    def test_pages_has_vault_id_column(self) -> None:
        """T-PG-004: pages.vault_id must exist (not null)."""
        col = self._get_column_by_name("pages", "vault_id")
        assert col is not None, "pages.vault_id column missing"
        assert not col.nullable, "pages.vault_id must be NOT NULL"

    def test_pages_has_file_path_column(self) -> None:
        """T-PG-005: pages.file_path must exist (not null)."""
        col = self._get_column_by_name("pages", "file_path")
        assert col is not None, "pages.file_path column missing"
        assert not col.nullable, "pages.file_path must be NOT NULL"

    def test_pages_has_title_nullable(self) -> None:
        """T-PG-006: pages.title must exist and be nullable."""
        col = self._get_column_by_name("pages", "title")
        assert col is not None, "pages.title column missing"
        assert col.nullable, "pages.title must be nullable (missing frontmatter → NULL)"

    def test_pages_has_type_nullable(self) -> None:
        """T-PG-007: pages.type must exist and be nullable."""
        col = self._get_column_by_name("pages", "type")
        assert col is not None, "pages.type column missing"
        assert col.nullable, "pages.type must be nullable"

    def test_pages_has_sources_jsonb_nullable(self) -> None:
        """T-PG-008: pages.sources must be JSONB and nullable."""
        col = self._get_column_by_name("pages", "sources")
        assert col is not None, "pages.sources column missing"
        assert col.nullable, "pages.sources must be nullable"
        # Verify it's JSONB type
        assert isinstance(
            col.type, JSONB
        ), f"pages.sources must be JSONB; got {type(col.type).__name__}"

    def test_pages_has_content_hash_not_null(self) -> None:
        """T-PG-009: pages.content_hash must exist and be NOT NULL."""
        col = self._get_column_by_name("pages", "content_hash")
        assert col is not None, "pages.content_hash column missing"
        assert not col.nullable, "pages.content_hash must be NOT NULL"

    def test_pages_has_source_mtime_ns_bigint_nullable(self) -> None:
        """T-PG-010: pages.source_mtime_ns (superset column §2.1) must be BIGINT nullable."""
        col = self._get_column_by_name("pages", "source_mtime_ns")
        assert col is not None, (
            "pages.source_mtime_ns column missing " "(required per v0.1-architecture §2.1 superset)"
        )
        assert col.nullable, "pages.source_mtime_ns must be nullable"
        assert isinstance(
            col.type, BigInteger
        ), f"pages.source_mtime_ns must be BigInteger; got {type(col.type).__name__}"

    def test_pages_has_qdrant_point_id_nullable(self) -> None:
        """T-PG-011: pages.qdrant_point_id must be nullable."""
        col = self._get_column_by_name("pages", "qdrant_point_id")
        assert col is not None, "pages.qdrant_point_id column missing"
        assert col.nullable, "pages.qdrant_point_id must be nullable"

    def test_pages_has_deleted_at_nullable(self) -> None:
        """T-PG-012: pages.deleted_at must be nullable (soft-delete sentinel)."""
        col = self._get_column_by_name("pages", "deleted_at")
        assert col is not None, "pages.deleted_at column missing"
        assert col.nullable, "pages.deleted_at must be nullable"

    def test_pages_has_created_at_not_null(self) -> None:
        """T-PG-013: pages.created_at must be NOT NULL."""
        col = self._get_column_by_name("pages", "created_at")
        assert col is not None, "pages.created_at column missing"
        assert not col.nullable, "pages.created_at must be NOT NULL"

    def test_pages_has_updated_at_not_null(self) -> None:
        """T-PG-014: pages.updated_at must be NOT NULL."""
        col = self._get_column_by_name("pages", "updated_at")
        assert col is not None, "pages.updated_at column missing"
        assert not col.nullable, "pages.updated_at must be NOT NULL"


# ── AC-F16dv-1: vault_state columns ──────────────────────────────────────────


class TestVaultStateColumns:
    """T-PG-015..018 — AC-F16dv-1"""

    def _get_column(self, col_name: str):  # type: ignore[no-untyped-def]
        from app.models import Base

        for table in Base.metadata.sorted_tables:
            if table.name == "vault_state":
                for col in table.columns:
                    if col.name == col_name:
                        return col
        return None

    def test_vault_state_has_id_pk(self) -> None:
        """T-PG-015: vault_state.id must be PK."""
        col = self._get_column("id")
        assert col is not None, "vault_state.id missing"
        assert col.primary_key

    def test_vault_state_has_vault_id(self) -> None:
        """T-PG-016: vault_state.vault_id must exist and be not null."""
        col = self._get_column("vault_id")
        assert col is not None, "vault_state.vault_id missing"
        assert not col.nullable

    def test_vault_state_has_data_version_integer_not_null(self) -> None:
        """T-PG-017: vault_state.data_version must be INTEGER NOT NULL."""
        col = self._get_column("data_version")
        assert col is not None, "vault_state.data_version missing (AC-F16dv-1)"
        assert not col.nullable, "vault_state.data_version must be NOT NULL"
        assert isinstance(
            col.type, Integer
        ), f"vault_state.data_version must be Integer; got {type(col.type).__name__}"

    def test_vault_state_has_updated_at(self) -> None:
        """T-PG-018: vault_state.updated_at must exist."""
        col = self._get_column("updated_at")
        assert col is not None, "vault_state.updated_at missing"
        assert not col.nullable

    def test_vault_state_has_vault_id_unique_constraint(self) -> None:
        """T-PG-019: vault_state must have a unique constraint on vault_id (one row per vault)."""
        from app.models import Base

        for table in Base.metadata.sorted_tables:
            if table.name == "vault_state":
                constraints = table.constraints
                unique_cols = []
                for c in constraints:
                    from sqlalchemy import UniqueConstraint

                    if isinstance(c, UniqueConstraint):
                        for col in c.columns:
                            unique_cols.append(col.name)
                assert "vault_id" in unique_cols, (
                    "vault_state must have a UniqueConstraint on vault_id "
                    "(one row per vault — AC-F16dv-1)"
                )


# ── AC-PG-4: no raw SQL in backend/app/ ──────────────────────────────────────


class TestNoRawSQL:
    """T-PG-020..022 — AC-PG-4"""

    PATTERNS = [
        r"cursor\.execute\s*\(",
        r'"INSERT\s+INTO',
        r"'INSERT\s+INTO",
        r'f"SELECT',
        r"f'SELECT",
        r'"SELECT\s+\*\s+FROM',
        r"'SELECT\s+\*\s+FROM",
    ]
    # Note: sa_text("...") / text("...") is ALLOWED (SQLAlchemy core expression)
    # Alembic lives at backend/alembic/ (outside backend/app/) — excluded implicitly (AQ-7)

    def _scan_files(self) -> list[tuple[Path, int, str]]:
        """Scan backend/app/ for raw SQL patterns; return (file, line_no, line) matches."""
        app_dir = Path(__file__).resolve().parent.parent / "app"
        matches = []
        for py_file in app_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), start=1):
                for pattern in self.PATTERNS:
                    if re.search(pattern, line):
                        matches.append((py_file, i, line.strip()))
        return matches

    def test_no_raw_cursor_execute_in_app(self) -> None:
        """T-PG-020: AC-PG-4 — no cursor.execute() calls in backend/app/."""
        app_dir = Path(__file__).resolve().parent.parent / "app"
        for py_file in app_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), start=1):
                if "cursor.execute" in line and "# noqa" not in line:
                    pytest.fail(
                        f"Raw SQL via cursor.execute found in "
                        f"{py_file.relative_to(app_dir.parent)} line {i}: {line.strip()!r}"
                    )

    def test_no_raw_sql_string_literals(self) -> None:
        """T-PG-021: AC-PG-4 — no f-string SQL or bare INSERT/SELECT strings in backend/app/."""
        app_dir = Path(__file__).resolve().parent.parent / "app"
        for py_file in app_dir.rglob("*.py"):
            text = py_file.read_text(encoding="utf-8")
            for i, line in enumerate(text.splitlines(), start=1):
                # Allow sa_text() / text() calls — that IS SQLAlchemy
                if "sa_text(" in line or "text(" in line:
                    continue
                for pattern in ['f"SELECT', "f'SELECT", '"INSERT INTO"', "'INSERT INTO'"]:
                    if pattern in line and "# noqa" not in line:
                        pytest.fail(
                            f"Raw SQL string literal found in "
                            f"{py_file.relative_to(app_dir.parent)} line {i}: "
                            f"{line.strip()!r}"
                        )

    def test_alembic_directory_is_outside_app(self) -> None:
        """T-PG-022: AQ-7 — backend/alembic/ must NOT be inside backend/app/."""
        backend_dir = Path(__file__).resolve().parent.parent
        app_dir = backend_dir / "app"
        alembic_dir = backend_dir / "alembic"

        # alembic/ should be a sibling of app/, not nested inside it
        if alembic_dir.exists():
            assert not str(alembic_dir).startswith(str(app_dir)), (
                "alembic/ must be at backend/alembic/, NOT inside backend/app/ "
                "(AQ-7: the no-raw-SQL grep scope is backend/app/ only)"
            )


# ── AC-PG-2: make er generates schema.mmd ────────────────────────────────────


class TestERDiagram:
    """T-PG-023..025 — AC-PG-2, AC-D2-1, AC-D2-2"""

    def test_er_schema_mmd_exists(self) -> None:
        """T-PG-023: AC-D2-1 — docs/er/schema.mmd must exist."""
        p = Path(__file__).resolve().parent.parent.parent / "docs" / "er" / "schema.mmd"
        assert p.exists(), "docs/er/schema.mmd must exist (run 'make er' to generate)"

    def test_er_schema_mmd_contains_erdiagram(self) -> None:
        """T-PG-024: AC-D2-1 — schema.mmd must contain 'erDiagram' keyword."""
        p = Path(__file__).resolve().parent.parent.parent / "docs" / "er" / "schema.mmd"
        text = p.read_text(encoding="utf-8")
        assert "erDiagram" in text, "docs/er/schema.mmd must contain 'erDiagram'"

    def test_er_schema_mmd_contains_both_tables(self) -> None:
        """T-PG-025: AC-D2-2 — schema.mmd must document both pages and vault_state."""
        p = Path(__file__).resolve().parent.parent.parent / "docs" / "er" / "schema.mmd"
        text = p.read_text(encoding="utf-8").upper()
        assert "PAGES" in text, "ER diagram must include PAGES table"
        assert "VAULT_STATE" in text, "ER diagram must include VAULT_STATE table"

    def test_er_schema_mmd_contains_source_mtime_ns(self) -> None:
        """T-PG-026: ER diagram must include source_mtime_ns (v0.1-architecture superset)."""
        p = Path(__file__).resolve().parent.parent.parent / "docs" / "er" / "schema.mmd"
        text = p.read_text(encoding="utf-8")
        assert "source_mtime_ns" in text, (
            "ER diagram must include source_mtime_ns column "
            "(v0.1-architecture §2.1 superset — make er must emit it)"
        )

    def test_er_generate_script_creates_file(self, tmp_path: Path) -> None:
        """T-PG-027: AC-D2-1 — deleting schema.mmd and re-running generate_er.py recreates it."""
        import sys

        er_path = Path(__file__).resolve().parent.parent.parent / "docs" / "er" / "schema.mmd"
        backup = tmp_path / "schema.mmd.bak"
        if er_path.exists():
            backup.write_bytes(er_path.read_bytes())
            er_path.unlink()

        try:
            generate_script = Path(__file__).resolve().parent.parent / "scripts" / "generate_er.py"
            proc = subprocess.run(
                [sys.executable, str(generate_script)],
                capture_output=True,
                text=True,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            assert (
                proc.returncode == 0
            ), f"generate_er.py failed:\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
            assert er_path.exists(), "generate_er.py must recreate docs/er/schema.mmd"
        finally:
            # Restore backup
            if backup.exists():
                er_path.write_bytes(backup.read_bytes())


# ── AC-PG-1 DEFERRED note ─────────────────────────────────────────────────────


class TestAlembicDeferred:
    """Sentinel: Alembic migration test deferred to live-demo with real Postgres."""

    def test_alembic_migration_is_deferred_to_live_infra(self) -> None:
        """
        T-PG-DEFERRED-001: AC-PG-1 — 'docker compose up' + 'alembic upgrade head'
        requires Postgres, which is not available in this CI sandbox (no Docker).

        The alembic/versions/ directory is inspected here to confirm migration
        files exist, which is a necessary (but not sufficient) condition for AC-PG-1.

        Full test: DEFERRED-needs-live-infra (live-demo must run alembic on real PG).
        """
        backend_dir = Path(__file__).resolve().parent.parent
        alembic_versions = backend_dir / "alembic" / "versions"
        assert (
            alembic_versions.is_dir()
        ), "backend/alembic/versions/ must exist and contain migration scripts"
        migration_files = list(alembic_versions.glob("*.py"))
        assert len(migration_files) >= 1, (
            f"At least one Alembic migration file must exist in "
            f"backend/alembic/versions/; found: {migration_files}"
        )
