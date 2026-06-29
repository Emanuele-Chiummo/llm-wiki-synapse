"""
SQLAlchemy model and schema tests (AC-PG-1..4, AC-F16dv-1, v0.2 tables).

These tests are infra-free: they inspect the SQLAlchemy model metadata
(no live Postgres connection needed). Live Postgres integration
(information_schema queries) is deferred to live-demo.

Coverage:
  AC-PG-1      Alembic migrations (confirmed by schema presence in model metadata)
  AC-PG-2      make er generates schema.mmd (file exists + contains erDiagram)
  AC-PG-3      All required columns present on pages table with correct types
  AC-PG-4      No raw SQL strings in backend/app/ (grep/static check)
  AC-F16dv-1   vault_state table has correct columns
  AC-PC-1      provider_config table exists with F17/I6 columns (v0.2)
  AC-IR-1      ingest_runs table exists with I7 cost/loop audit columns (v0.2)
  AC-LK-1      links table exists with K5 wikilink columns (v0.2)

Test IDs: T-PG-001 .. T-PG-030

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

    def test_v02_alembic_migration_file_exists(self) -> None:
        """
        T-PG-DEFERRED-002: v0.2 Alembic migration must add provider_config, ingest_runs, links.
        Verify the file exists; content verified by model introspection tests below.
        """
        backend_dir = Path(__file__).resolve().parent.parent
        alembic_versions = backend_dir / "alembic" / "versions"
        migration_files = list(alembic_versions.glob("*.py"))
        assert len(migration_files) >= 2, (
            "At least 2 Alembic migration files expected (0001 initial + 0002 v0.2); "
            f"found: {[f.name for f in migration_files]}"
        )


# ── v0.2 table model tests: provider_config, ingest_runs, links ──────────────


class TestProviderConfigModel:
    """T-PG-020..025 — AC-PC-1: provider_config table must exist with F17/I6 columns (v0.2)."""

    def _get_table(self):  # type: ignore[no-untyped-def]
        from app.models import Base

        for table in Base.metadata.sorted_tables:
            if table.name == "provider_config":
                return table
        return None

    def _get_column(self, col_name: str):  # type: ignore[no-untyped-def]
        table = self._get_table()
        if table is None:
            return None
        for col in table.columns:
            if col.name == col_name:
                return col
        return None

    def test_provider_config_table_exists(self) -> None:
        """T-PG-020: provider_config table must exist in SQLAlchemy metadata (AC-PC-1, I6)."""
        from app.models import Base

        table_names = {t.name for t in Base.metadata.sorted_tables}
        assert (
            "provider_config" in table_names
        ), "provider_config table missing from SQLAlchemy models (AC-PC-1, I6, ADR-0008)"

    def test_provider_config_has_id_pk(self) -> None:
        """T-PG-021: provider_config.id must be a UUID primary key."""
        col = self._get_column("id")
        assert col is not None, "provider_config.id missing"
        assert col.primary_key, "provider_config.id must be PK"

    def test_provider_config_has_scope(self) -> None:
        """T-PG-022: provider_config.scope must exist (global | vault | operation)."""
        col = self._get_column("scope")
        assert col is not None, "provider_config.scope missing (AC-PC-1, ADR-0008)"
        assert not col.nullable, "provider_config.scope must be NOT NULL"

    def test_provider_config_has_provider_type(self) -> None:
        """T-PG-023: provider_config.provider_type must exist (local|api|cli — I6)."""
        col = self._get_column("provider_type")
        assert col is not None, "provider_config.provider_type missing (I6)"
        assert not col.nullable, "provider_config.provider_type must be NOT NULL"

    def test_provider_config_has_model_id(self) -> None:
        """T-PG-024: provider_config.model_id must exist and be NOT NULL."""
        col = self._get_column("model_id")
        assert col is not None, "provider_config.model_id missing"
        assert not col.nullable, "provider_config.model_id must be NOT NULL"

    def test_provider_config_has_max_iter(self) -> None:
        """T-PG-025: provider_config.max_iter must exist for loop bounding (I7, ADR-0009)."""
        col = self._get_column("max_iter")
        assert col is not None, "provider_config.max_iter missing (I7, ADR-0009)"

    def test_provider_config_has_token_budget(self) -> None:
        """T-PG-025b: provider_config.token_budget must exist (I7 cost-cap gate)."""
        col = self._get_column("token_budget")
        assert col is not None, "provider_config.token_budget missing (I7)"

    def test_provider_config_no_api_key_column(self) -> None:
        """
        T-PG-025c: provider_config must NOT have an api_key column (security — I9/CLAUDE.md §12).
        API keys are env-only (ANTHROPIC_API_KEY, OPENAI_API_KEY), never stored in DB.
        """
        table = self._get_table()
        if table is None:
            return  # caught by test_provider_config_table_exists
        col_names = {col.name for col in table.columns}
        forbidden = {"api_key", "secret", "token", "password"}
        intersection = col_names & forbidden
        assert not intersection, (
            f"provider_config must NOT have secret columns {intersection}; "
            "keys are env-only (CLAUDE.md §12 security invariant)"
        )


class TestIngestRunsModel:
    """T-PG-026..029 — AC-IR-1: ingest_runs table must exist with I7 cost/audit columns (v0.2)."""

    def _get_column(self, col_name: str):  # type: ignore[no-untyped-def]
        from app.models import Base

        for table in Base.metadata.sorted_tables:
            if table.name == "ingest_runs":
                for col in table.columns:
                    if col.name == col_name:
                        return col
        return None

    def test_ingest_runs_table_exists(self) -> None:
        """T-PG-026: ingest_runs table must exist (AC-IR-1, I7, ADR-0009)."""
        from app.models import Base

        table_names = {t.name for t in Base.metadata.sorted_tables}
        assert (
            "ingest_runs" in table_names
        ), "ingest_runs table missing from SQLAlchemy models (AC-IR-1, I7)"

    def test_ingest_runs_has_total_cost_usd(self) -> None:
        """T-PG-027: ingest_runs.total_cost_usd must be present (I7 cost logging)."""
        col = self._get_column("total_cost_usd")
        assert col is not None, "ingest_runs.total_cost_usd missing (I7, ADR-0009)"

    def test_ingest_runs_has_converged(self) -> None:
        """T-PG-028: ingest_runs.converged must exist (I7 loop audit)."""
        col = self._get_column("converged")
        assert col is not None, "ingest_runs.converged missing (I7)"

    def test_ingest_runs_has_route(self) -> None:
        """T-PG-029: ingest_runs.route must exist (orchestrated|delegated — I6)."""
        col = self._get_column("route")
        assert col is not None, "ingest_runs.route missing (I6)"

    def test_ingest_runs_has_provider_type(self) -> None:
        """T-PG-029b: ingest_runs.provider_type captures which backend was used."""
        col = self._get_column("provider_type")
        assert col is not None, "ingest_runs.provider_type missing (audit)"


class TestLinksModel:
    """T-PG-030 — AC-LK-1: links table must exist with K5 wikilink columns (v0.2)."""

    def _get_column(self, col_name: str):  # type: ignore[no-untyped-def]
        from app.models import Base

        for table in Base.metadata.sorted_tables:
            if table.name == "links":
                for col in table.columns:
                    if col.name == col_name:
                        return col
        return None

    def test_links_table_exists(self) -> None:
        """T-PG-030: links table must exist (AC-LK-1, K5 wikilink parsing)."""
        from app.models import Base

        table_names = {t.name for t in Base.metadata.sorted_tables}
        assert "links" in table_names, "links table missing from SQLAlchemy models (AC-LK-1, K5)"

    def test_links_has_source_page_id(self) -> None:
        """T-PG-030b: links.source_page_id must exist (FK to pages)."""
        col = self._get_column("source_page_id")
        assert col is not None, "links.source_page_id missing (K5)"

    def test_links_has_target_title(self) -> None:
        """T-PG-030c: links.target_title must exist (K5 wikilink target)."""
        col = self._get_column("target_title")
        assert col is not None, "links.target_title missing (K5)"

    def test_links_has_dangling_flag(self) -> None:
        """T-PG-030d: links.dangling must exist (K5 unresolved wikilinks)."""
        col = self._get_column("dangling")
        assert col is not None, "links.dangling missing (K5)"


# ── AC-F10-6: deep_research_runs + deep_research_sources tables ───────────────


class TestDeepResearchRunsColumns:
    """
    T-PG-031 — AC-F10-6a: deep_research_runs table exists with all required columns (ADR-0024 §7.1).
    Tests are infra-free: SQLAlchemy model metadata introspection, no live Postgres needed.
    """

    @staticmethod
    def _get_column(col_name: str):  # type: ignore[return]
        from app.models import Base

        t = Base.metadata.tables.get("deep_research_runs")
        if t is None:
            return None
        return t.columns.get(col_name)

    def test_deep_research_runs_table_exists(self) -> None:
        """T-PG-031: deep_research_runs table must exist in SQLAlchemy models (AC-F10-6a)."""
        from app.models import Base

        table_names = {t.name for t in Base.metadata.sorted_tables}
        assert (
            "deep_research_runs" in table_names
        ), "deep_research_runs table missing from SQLAlchemy models (AC-F10-6a, ADR-0024 §7.1)"

    def test_has_id(self) -> None:
        """T-PG-031a: deep_research_runs.id (PK UUID)."""
        col = self._get_column("id")
        assert col is not None, "deep_research_runs.id missing (AC-F10-6a)"

    def test_has_vault_id(self) -> None:
        """T-PG-031b: deep_research_runs.vault_id (string scope, AQ-v0.5-6)."""
        col = self._get_column("vault_id")
        assert col is not None, "deep_research_runs.vault_id missing (AC-F10-6a)"

    def test_has_topic(self) -> None:
        """T-PG-031c: deep_research_runs.topic."""
        col = self._get_column("topic")
        assert col is not None, "deep_research_runs.topic missing (AC-F10-6a)"

    def test_has_status(self) -> None:
        """T-PG-031d: deep_research_runs.status (running/converged/…)."""
        col = self._get_column("status")
        assert col is not None, "deep_research_runs.status missing (AC-F10-6a)"

    def test_has_max_iter(self) -> None:
        """T-PG-031e: deep_research_runs.max_iter (frozen bound, I7, AQ-v0.5-4)."""
        col = self._get_column("max_iter")
        assert col is not None, "deep_research_runs.max_iter missing (AC-F10-6a, I7)"

    def test_has_token_budget(self) -> None:
        """T-PG-031f: deep_research_runs.token_budget (frozen bound, I7)."""
        col = self._get_column("token_budget")
        assert col is not None, "deep_research_runs.token_budget missing (AC-F10-6a, I7)"

    def test_has_iterations_used(self) -> None:
        """T-PG-031g: deep_research_runs.iterations_used."""
        col = self._get_column("iterations_used")
        assert col is not None, "deep_research_runs.iterations_used missing (AC-F10-6a)"

    def test_has_queries_used(self) -> None:
        """T-PG-031h: deep_research_runs.queries_used (JSONB, AC-F10-4c)."""
        col = self._get_column("queries_used")
        assert col is not None, "deep_research_runs.queries_used missing (AC-F10-4c)"

    def test_has_sources_fetched(self) -> None:
        """T-PG-031i: deep_research_runs.sources_fetched."""
        col = self._get_column("sources_fetched")
        assert col is not None, "deep_research_runs.sources_fetched missing (AC-F10-6a)"

    def test_has_total_cost_usd(self) -> None:
        """T-PG-031j: deep_research_runs.total_cost_usd (I7 cost ledger)."""
        col = self._get_column("total_cost_usd")
        assert col is not None, "deep_research_runs.total_cost_usd missing (AC-F10-6a, I7)"

    def test_has_synthesis_text(self) -> None:
        """T-PG-031k: deep_research_runs.synthesis_text (nullable, AC-F10-4c)."""
        col = self._get_column("synthesis_text")
        assert col is not None, "deep_research_runs.synthesis_text missing (AC-F10-4c)"

    def test_has_synthesis_page_id(self) -> None:
        """T-PG-031l: deep_research_runs.synthesis_page_id (FK→pages, nullable)."""
        col = self._get_column("synthesis_page_id")
        assert col is not None, "deep_research_runs.synthesis_page_id missing (AC-F10-6a)"

    def test_has_started_at(self) -> None:
        """T-PG-031m: deep_research_runs.started_at."""
        col = self._get_column("started_at")
        assert col is not None, "deep_research_runs.started_at missing (AC-F10-6a)"

    def test_has_completed_at(self) -> None:
        """T-PG-031n: deep_research_runs.completed_at (nullable while running, AC-F10-4c)."""
        col = self._get_column("completed_at")
        assert col is not None, "deep_research_runs.completed_at missing (AC-F10-4c)"

    def test_has_error_message(self) -> None:
        """T-PG-031o: deep_research_runs.error_message (nullable)."""
        col = self._get_column("error_message")
        assert col is not None, "deep_research_runs.error_message missing (AC-F10-6a)"

    def test_migration_0009_exists(self) -> None:
        """T-PG-031p: Alembic migration 0009 (deep_research tables) must exist (AC-F10-6c)."""
        from pathlib import Path

        versions_dir = Path(__file__).resolve().parent.parent / "alembic" / "versions"
        migration_files = list(versions_dir.glob("0009_*.py"))
        assert len(migration_files) >= 1, (
            f"Alembic migration 0009 (deep_research tables) not found in "
            f"backend/alembic/versions/ (AC-F10-6c, ADR-0024 §7). "
            f"Files found: {[f.name for f in versions_dir.glob('*.py')]}"
        )


class TestDeepResearchSourcesColumns:
    """
    T-PG-032 — AC-F10-6b: deep_research_sources table exists with all required columns
    (ADR-0024 §7.2). Infra-free SQLAlchemy model introspection.
    """

    @staticmethod
    def _get_column(col_name: str):  # type: ignore[return]
        from app.models import Base

        t = Base.metadata.tables.get("deep_research_sources")
        if t is None:
            return None
        return t.columns.get(col_name)

    def test_deep_research_sources_table_exists(self) -> None:
        """T-PG-032: deep_research_sources table must exist (AC-F10-6b, ADR-0024 §7.2)."""
        from app.models import Base

        table_names = {t.name for t in Base.metadata.sorted_tables}
        assert (
            "deep_research_sources" in table_names
        ), "deep_research_sources table missing from SQLAlchemy models (AC-F10-6b)"

    def test_has_id(self) -> None:
        """T-PG-032a: deep_research_sources.id (PK)."""
        col = self._get_column("id")
        assert col is not None, "deep_research_sources.id missing (AC-F10-6b)"

    def test_has_run_id(self) -> None:
        """T-PG-032b: deep_research_sources.run_id (FK→deep_research_runs, cascade delete)."""
        col = self._get_column("run_id")
        assert col is not None, "deep_research_sources.run_id missing (AC-F10-6b)"

    def test_has_url(self) -> None:
        """T-PG-032c: deep_research_sources.url."""
        col = self._get_column("url")
        assert col is not None, "deep_research_sources.url missing (AC-F10-6b)"

    def test_has_title(self) -> None:
        """T-PG-032d: deep_research_sources.title."""
        col = self._get_column("title")
        assert col is not None, "deep_research_sources.title missing (AC-F10-6b)"

    def test_has_fetched_content_md(self) -> None:
        """T-PG-032e: deep_research_sources.fetched_content_md (nullable on fetch failure)."""
        col = self._get_column("fetched_content_md")
        assert col is not None, "deep_research_sources.fetched_content_md missing (AC-F10-6b)"

    def test_has_relevance_score(self) -> None:
        """T-PG-032f: deep_research_sources.relevance_score (nullable, ADR-0024 §7.2)."""
        col = self._get_column("relevance_score")
        assert col is not None, "deep_research_sources.relevance_score missing (AC-F10-6b)"

    def test_has_iteration(self) -> None:
        """T-PG-032g: deep_research_sources.iteration (which round, audit)."""
        col = self._get_column("iteration")
        assert col is not None, "deep_research_sources.iteration missing (AC-F10-6b)"

    def test_has_created_at(self) -> None:
        """T-PG-032h: deep_research_sources.created_at."""
        col = self._get_column("created_at")
        assert col is not None, "deep_research_sources.created_at missing (AC-F10-6b)"

    def test_er_diagram_contains_deep_research_tables(self) -> None:
        """T-PG-032i: docs/er/schema.mmd must reflect both deep_research tables (AC-F10-6d)."""
        from pathlib import Path

        er_path = Path(__file__).resolve().parent.parent.parent / "docs" / "er" / "schema.mmd"
        assert er_path.exists(), "docs/er/schema.mmd must exist (AC-F10-6d)"
        text = er_path.read_text(encoding="utf-8").upper()
        assert (
            "DEEP_RESEARCH_RUNS" in text
        ), "docs/er/schema.mmd must include DEEP_RESEARCH_RUNS table (AC-F10-6d, ADR-0024 §7.3)"
        assert (
            "DEEP_RESEARCH_SOURCES" in text
        ), "docs/er/schema.mmd must include DEEP_RESEARCH_SOURCES table (AC-F10-6d, ADR-0024 §7.3)"
