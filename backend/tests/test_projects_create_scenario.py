"""
Tests for POST /projects with scenario + output_language (WS-E, v1.7.0 onboarding parity).

Covered:
  T-SC-001  Creating a project with scenario="research" scaffolds wiki/methodology,
            wiki/findings, wiki/thesis extra dirs.
  T-SC-002  schema.md written by bootstrap contains the thesis/methodology/finding
            Page Types rows with correct wiki/<dir>/ paths.
  T-SC-003  purpose.md written by bootstrap matches the research purpose template.
  T-SC-004  index.md has base per-type sections + ## Recently Updated + custom sections.
  T-SC-005  log.md first entry is "- Project created".
  T-SC-006  output_language is persisted to vault_state (requires SQLite DB fixture).
  T-SC-007  Unknown scenario id → 400.
  T-SC-008  Creating a project without scenario still scaffolds correctly (no regression).
  T-SC-009  All 5 scenario ids are accepted (smoke test).
  T-SC-010  GET /vault/meta/output-language round-trips with PUT (requires SQLite DB).
  T-SC-011  PUT /vault/meta/output-language sets null correctly.
  T-SC-012  GET /vault/meta/output-language 404 when no vault_state row.
  T-SC-013  POST /scenarios/{id}/apply creates extra_dirs on the active vault.
  T-SC-014  scenarios_data.py: every scenario has extra_dirs key (list), schema_md
            contains ## Page Types table, and base 7 types are always present.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── Vault-state SQLite schema (matches test_graph_api.py / test_stats.py DDL) ─

_VAULT_STATE_DDL = """
CREATE TABLE IF NOT EXISTS vault_state (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL UNIQUE,
    data_version INTEGER NOT NULL DEFAULT 0,
    remote_mcp_enabled INTEGER NOT NULL DEFAULT 0,
    remote_mcp_write_enabled INTEGER,
    mcp_access_token_hash TEXT,
    mcp_allow_without_token INTEGER NOT NULL DEFAULT 0,
    clip_enabled_db INTEGER,
    clip_access_token TEXT,
    clip_allowed_origins_db TEXT,
    cli_oauth_token TEXT,
    cli_oauth_token_encrypted BLOB,
    web_search_api_keys_encrypted BLOB,
    searxng_url_db TEXT,
    searxng_categories_db TEXT,
    searxng_max_queries_db INTEGER,
    output_language TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


# ── Shared helpers ─────────────────────────────────────────────────────────────


@asynccontextmanager
async def _null_lifespan(app: Any) -> Any:  # noqa: ANN401
    yield


def _client(lifespan: Any = None) -> AsyncClient:
    from app.main import app

    app.router.lifespan_context = lifespan or _null_lifespan
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _seed_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point SYNAPSE_STATE_DIR at a temp dir and the boot vault at a temp path."""
    state = tmp_path / "state"
    monkeypatch.setenv("SYNAPSE_STATE_DIR", str(state))
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "default")
    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path / "vault"))
    return state


def _make_sqlite_engine() -> Any:
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _make_session_factory(engine: Any) -> Any:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _setup_vault_state_schema(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa_text(_VAULT_STATE_DDL))


# ── T-SC-014: scenarios_data unit tests (no HTTP, no FS) ──────────────────────


def test_all_scenarios_have_extra_dirs_key() -> None:
    """T-SC-014a: every scenario dict has an 'extra_dirs' key that is a list of strings."""
    from app.scenarios_data import SCENARIOS

    for s in SCENARIOS:
        assert "extra_dirs" in s, f"scenario {s['id']} missing extra_dirs"
        assert isinstance(s["extra_dirs"], list), f"scenario {s['id']} extra_dirs is not a list"
        for d in s["extra_dirs"]:
            assert isinstance(d, str), f"scenario {s['id']} extra_dirs item is not str: {d!r}"
            assert d.startswith(
                "wiki/"
            ), f"scenario {s['id']} extra_dir must start with wiki/: {d!r}"


def test_all_scenarios_schema_has_page_types_table() -> None:
    """T-SC-014b: every scenario schema_md contains a ## Page Types section."""
    from app.scenarios_data import SCENARIOS

    for s in SCENARIOS:
        schema = s["schema_md"]
        assert "## Page Types" in schema, f"scenario {s['id']} schema_md missing ## Page Types"
        # Base 7 types must always be present in the table
        for base_type in (
            "entity",
            "concept",
            "source",
            "query",
            "comparison",
            "synthesis",
            "overview",
        ):
            assert (
                f"| {base_type} |" in schema
            ), f"scenario {s['id']} schema_md missing base type row: {base_type}"
        # Directory cells must use wiki/<dir>/ form
        assert "wiki/entities/" in schema, f"scenario {s['id']} missing wiki/entities/ cell"
        assert "wiki/sources/" in schema, f"scenario {s['id']} missing wiki/sources/ cell"


def test_research_scenario_has_custom_types() -> None:
    """T-SC-014c: research scenario adds thesis/methodology/finding rows."""
    from app.scenarios_data import SCENARIO_INDEX

    s = SCENARIO_INDEX["research"]
    schema = s["schema_md"]
    assert "| thesis | wiki/thesis/ |" in schema
    assert "| methodology | wiki/methodology/ |" in schema
    assert "| finding | wiki/findings/ |" in schema
    assert s["extra_dirs"] == ["wiki/methodology", "wiki/findings", "wiki/thesis"]


def test_reading_scenario_has_custom_types() -> None:
    """T-SC-014d: reading scenario adds character/theme/plot-thread/chapter rows."""
    from app.scenarios_data import SCENARIO_INDEX

    s = SCENARIO_INDEX["reading"]
    schema = s["schema_md"]
    assert "| character | wiki/characters/ |" in schema
    assert "| theme | wiki/themes/ |" in schema
    assert "| plot-thread | wiki/plot-threads/ |" in schema
    assert "| chapter | wiki/chapters/ |" in schema
    assert s["extra_dirs"] == [
        "wiki/characters",
        "wiki/themes",
        "wiki/plot-threads",
        "wiki/chapters",
    ]


def test_personal_growth_scenario_has_custom_types() -> None:
    """T-SC-014e: personal-growth scenario adds goal/habit/reflection/journal rows."""
    from app.scenarios_data import SCENARIO_INDEX

    s = SCENARIO_INDEX["personal-growth"]
    schema = s["schema_md"]
    assert "| goal | wiki/goals/ |" in schema
    assert "| habit | wiki/habits/ |" in schema
    assert "| reflection | wiki/reflections/ |" in schema
    assert "| journal | wiki/journal/ |" in schema
    assert s["extra_dirs"] == ["wiki/goals", "wiki/habits", "wiki/reflections", "wiki/journal"]


def test_business_scenario_has_custom_types() -> None:
    """T-SC-014f: business scenario adds meeting/decision/project/stakeholder rows."""
    from app.scenarios_data import SCENARIO_INDEX

    s = SCENARIO_INDEX["business"]
    schema = s["schema_md"]
    assert "| meeting | wiki/meetings/ |" in schema
    assert "| decision | wiki/decisions/ |" in schema
    assert "| project | wiki/projects/ |" in schema
    assert "| stakeholder | wiki/stakeholders/ |" in schema
    assert s["extra_dirs"] == [
        "wiki/meetings",
        "wiki/decisions",
        "wiki/projects",
        "wiki/stakeholders",
    ]


def test_general_scenario_has_no_extra_dirs() -> None:
    """T-SC-014g: general scenario has no extra dirs."""
    from app.scenarios_data import SCENARIO_INDEX

    s = SCENARIO_INDEX["general"]
    assert s["extra_dirs"] == []


def test_all_schemas_have_synapse_note() -> None:
    """T-SC-014h: all schemas contain the non-empty sources note."""
    from app.scenarios_data import SCENARIOS

    for s in SCENARIOS:
        assert (
            "non-empty" in s["schema_md"] and "sources" in s["schema_md"]
        ), f"scenario {s['id']} schema_md missing Synapse sources note"


# ── T-SC-001 to T-SC-005, T-SC-007, T-SC-008, T-SC-009: filesystem tests ─────


@pytest.mark.asyncio
async def test_create_with_research_scenario_extra_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-SC-001: POST /projects with scenario=research creates wiki/methodology, wiki/findings, wiki/thesis."""
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "research-vault"
    async with _client() as c:
        resp = await c.post(
            "/projects",
            json={"name": "Research Vault", "path": str(target), "scenario": "research"},
        )
    assert resp.status_code == 201, resp.text

    # Extra dirs must exist
    assert (target / "wiki" / "methodology").is_dir()
    assert (target / "wiki" / "findings").is_dir()
    assert (target / "wiki" / "thesis").is_dir()


@pytest.mark.asyncio
async def test_create_with_research_scenario_schema_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-SC-002: schema.md contains thesis/methodology/finding Page Types rows."""
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "research-vault2"
    async with _client() as c:
        await c.post(
            "/projects",
            json={"name": "Research Vault 2", "path": str(target), "scenario": "research"},
        )

    schema = (target / "schema.md").read_text(encoding="utf-8")
    assert "| thesis | wiki/thesis/ |" in schema
    assert "| methodology | wiki/methodology/ |" in schema
    assert "| finding | wiki/findings/ |" in schema
    # Base types must also be present
    assert "| entity | wiki/entities/ |" in schema
    assert "| concept | wiki/concepts/ |" in schema


@pytest.mark.asyncio
async def test_create_with_research_scenario_purpose_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-SC-003: purpose.md matches the research template (key headings present)."""
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "research-vault3"
    async with _client() as c:
        await c.post(
            "/projects",
            json={"name": "Research Vault 3", "path": str(target), "scenario": "research"},
        )

    purpose = (target / "purpose.md").read_text(encoding="utf-8")
    # Research template distinctive headings
    assert "## Research Question" in purpose
    assert (
        "## Hypothesis" in purpose
        or "## Working Thesis" in purpose
        or "Hypothesis / Working Thesis" in purpose
    )
    assert "## Scope" in purpose
    assert "## Success Criteria" in purpose


@pytest.mark.asyncio
async def test_create_index_md_has_required_sections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-SC-004: index.md has per-type sections + ## Recently Updated + custom sections."""
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "research-vault4"
    async with _client() as c:
        await c.post(
            "/projects",
            json={"name": "Research Vault 4", "path": str(target), "scenario": "research"},
        )

    index = (target / "wiki" / "index.md").read_text(encoding="utf-8")
    # llm_wiki parity sections
    assert "# Wiki Index" in index
    assert "## Recently Updated" in index
    assert "## Entities" in index
    assert "## Concepts" in index
    assert "## Sources" in index
    assert "## Queries" in index
    assert "## Comparisons" in index
    assert "## Synthesis" in index
    # Research custom sections
    assert "## Methodology" in index
    assert "## Findings" in index
    assert "## Thesis" in index


@pytest.mark.asyncio
async def test_log_md_has_project_created_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-SC-005: log.md first entry is '- Project created'."""
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "log-test-vault"
    async with _client() as c:
        await c.post(
            "/projects",
            json={"name": "Log Test Vault", "path": str(target)},
        )

    log = (target / "wiki" / "log.md").read_text(encoding="utf-8")
    assert "- Project created" in log
    # Must have a dated section (## YYYY-MM-DD)
    import re

    assert re.search(r"## \d{4}-\d{2}-\d{2}", log), "log.md missing dated section"


@pytest.mark.asyncio
async def test_unknown_scenario_returns_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-SC-007: an unknown scenario id returns HTTP 400."""
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "bad-scenario-vault"
    async with _client() as c:
        resp = await c.post(
            "/projects",
            json={"name": "Bad Vault", "path": str(target), "scenario": "nonexistent"},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_without_scenario_no_extra_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-SC-008: creating a project without a scenario still scaffolds base dirs correctly."""
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "bare-vault"
    async with _client() as c:
        resp = await c.post("/projects", json={"name": "Bare Vault", "path": str(target)})
    assert resp.status_code == 201, resp.text

    assert (target / "wiki" / "entities").is_dir()
    assert (target / "wiki" / "concepts").is_dir()
    assert (target / "raw" / "sources").is_dir()
    assert (target / "purpose.md").exists()
    assert (target / "schema.md").exists()
    # No extra dirs from any scenario
    assert not (target / "wiki" / "methodology").exists()
    assert not (target / "wiki" / "thesis").exists()


@pytest.mark.asyncio
async def test_all_five_scenario_ids_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-SC-009: all 5 valid scenario ids produce HTTP 201."""
    _seed_env(tmp_path, monkeypatch)
    scenario_ids = ["research", "reading", "personal-growth", "business", "general"]
    async with _client() as c:
        for sid in scenario_ids:
            target = tmp_path / f"vault-{sid}"
            resp = await c.post(
                "/projects",
                json={"name": f"Vault {sid}", "path": str(target), "scenario": sid},
            )
            assert (
                resp.status_code == 201
            ), f"scenario {sid!r} returned {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_reading_scenario_extra_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """reading scenario scaffolds wiki/characters, wiki/themes, wiki/plot-threads, wiki/chapters."""
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "reading-vault"
    async with _client() as c:
        await c.post(
            "/projects",
            json={"name": "Reading Vault", "path": str(target), "scenario": "reading"},
        )

    assert (target / "wiki" / "characters").is_dir()
    assert (target / "wiki" / "themes").is_dir()
    assert (target / "wiki" / "plot-threads").is_dir()
    assert (target / "wiki" / "chapters").is_dir()


@pytest.mark.asyncio
async def test_personal_growth_scenario_extra_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """personal-growth scenario scaffolds wiki/goals, wiki/habits, wiki/reflections, wiki/journal."""
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "personal-vault"
    async with _client() as c:
        await c.post(
            "/projects",
            json={"name": "Personal Vault", "path": str(target), "scenario": "personal-growth"},
        )

    assert (target / "wiki" / "goals").is_dir()
    assert (target / "wiki" / "habits").is_dir()
    assert (target / "wiki" / "reflections").is_dir()
    assert (target / "wiki" / "journal").is_dir()


@pytest.mark.asyncio
async def test_business_scenario_extra_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """business scenario scaffolds wiki/meetings, wiki/decisions, wiki/projects, wiki/stakeholders."""
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "business-vault"
    async with _client() as c:
        await c.post(
            "/projects",
            json={"name": "Business Vault", "path": str(target), "scenario": "business"},
        )

    assert (target / "wiki" / "meetings").is_dir()
    assert (target / "wiki" / "decisions").is_dir()
    assert (target / "wiki" / "projects").is_dir()
    assert (target / "wiki" / "stakeholders").is_dir()


# ── T-SC-013: POST /scenarios/{id}/apply creates extra_dirs ───────────────────


@pytest.mark.asyncio
async def test_apply_scenario_creates_extra_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-SC-013: POST /scenarios/{id}/apply creates scenario's extra_dirs on the active vault."""
    from unittest.mock import AsyncMock

    from app import config as cfg

    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    (vault_root / "wiki").mkdir()
    (vault_root / "purpose.md").write_text("# Vault Purpose\n", encoding="utf-8")
    (vault_root / "schema.md").write_text("# Schema\n", encoding="utf-8")

    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))

    # Patch bump_version at the orchestrator module level so the deferred
    # `from app.ingest.orchestrator import bump_version` inside apply_scenario
    # picks up the mock (Python resolves deferred imports as attribute lookups on the
    # cached module object — setting the attribute here is sufficient).
    import app.ingest.orchestrator as orch_mod

    monkeypatch.setattr(orch_mod, "bump_version", AsyncMock())

    # Use the full app.main (same as existing vault_meta tests) with null lifespan
    # to avoid DB/watcher/graph-cache initialization.
    from app.main import app

    app.router.lifespan_context = _null_lifespan

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post("/scenarios/research/apply")

    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] is True

    # Extra dirs must now exist under the active vault
    assert (vault_root / "wiki" / "methodology").is_dir()
    assert (vault_root / "wiki" / "findings").is_dir()
    assert (vault_root / "wiki" / "thesis").is_dir()


# ── T-SC-006: output_language persisted to vault_state (requires SQLite) ──────


@pytest.mark.asyncio
async def test_create_project_persists_output_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-SC-006: creating a project with output_language persists it to vault_state."""
    engine = _make_sqlite_engine()
    await _setup_vault_state_schema(engine)
    factory = _make_session_factory(engine)

    # Patch app.db.get_session to use the in-memory SQLite session
    @asynccontextmanager
    async def mock_get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    import app.db as db_mod

    monkeypatch.setattr(db_mod, "get_session", mock_get_session)

    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "lang-vault"

    async with _client() as c:
        resp = await c.post(
            "/projects",
            json={
                "name": "Lang Vault",
                "path": str(target),
                "scenario": "research",
                "output_language": "en",
            },
        )

    assert resp.status_code == 201, resp.text
    proj_id = resp.json()["id"]

    # Verify vault_state row has output_language = "en"
    async with factory() as session:
        result = await session.execute(
            sa_text("SELECT output_language FROM vault_state WHERE vault_id = :vid").bindparams(
                vid=proj_id
            )
        )
        row = result.fetchone()

    assert row is not None, f"No vault_state row found for vault_id={proj_id!r}"
    assert row[0] == "en", f"Expected output_language='en', got {row[0]!r}"


# ── T-SC-010, T-SC-011, T-SC-012: GET/PUT /vault/meta/output-language ────────


@pytest.mark.asyncio
async def test_output_language_get_put_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-SC-010: GET /vault/meta/output-language round-trips with PUT."""
    engine = _make_sqlite_engine()
    await _setup_vault_state_schema(engine)
    factory = _make_session_factory(engine)

    vid = "roundtrip-vault"

    # Seed a vault_state row with output_language=null initially
    async with engine.begin() as conn:
        await conn.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version) VALUES (:id, :vid, 0)"
            ).bindparams(id=str(uuid.uuid4()), vid=vid)
        )

    @asynccontextmanager
    async def mock_get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    import app.db as db_mod
    from app import config as cfg

    # Patch app.db.get_session — the vault_meta handlers use `import app.db as _db;
    # _db.get_session()` (deferred, dynamic attribute lookup) so this patch is effective.
    monkeypatch.setattr(db_mod, "get_session", mock_get_session)
    monkeypatch.setattr(cfg.settings, "vault_id", vid)

    # Use the full app.main with null lifespan (same pattern as test_vault_meta.py).
    from app.main import app

    app.router.lifespan_context = _null_lifespan

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Initially null
        get1 = await c.get("/vault/meta/output-language")
        assert get1.status_code == 200, get1.text
        assert get1.json()["language"] is None

        # PUT sets it
        put = await c.put("/vault/meta/output-language", json={"language": "it"})
        assert put.status_code == 200, put.text
        assert put.json()["language"] == "it"

        # GET returns the updated value
        get2 = await c.get("/vault/meta/output-language")
        assert get2.status_code == 200
        assert get2.json()["language"] == "it"


@pytest.mark.asyncio
async def test_output_language_put_null_clears_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-SC-011: PUT /vault/meta/output-language with language=null clears the value."""
    engine = _make_sqlite_engine()
    await _setup_vault_state_schema(engine)
    factory = _make_session_factory(engine)

    vid = "clear-vault"

    async with engine.begin() as conn:
        await conn.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, output_language) "
                "VALUES (:id, :vid, 0, 'en')"
            ).bindparams(id=str(uuid.uuid4()), vid=vid)
        )

    @asynccontextmanager
    async def mock_get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    import app.db as db_mod
    from app import config as cfg

    monkeypatch.setattr(db_mod, "get_session", mock_get_session)
    monkeypatch.setattr(cfg.settings, "vault_id", vid)

    from app.main import app

    app.router.lifespan_context = _null_lifespan

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Clear
        put = await c.put("/vault/meta/output-language", json={"language": None})
        assert put.status_code == 200
        assert put.json()["language"] is None

        # Verify cleared
        get = await c.get("/vault/meta/output-language")
        assert get.json()["language"] is None


@pytest.mark.asyncio
async def test_output_language_get_404_when_no_vault_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-SC-012: GET /vault/meta/output-language returns 404 when vault_state has no row."""
    engine = _make_sqlite_engine()
    await _setup_vault_state_schema(engine)
    factory = _make_session_factory(engine)

    vid = "no-state-vault"

    @asynccontextmanager
    async def mock_get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    import app.db as db_mod
    from app import config as cfg

    monkeypatch.setattr(db_mod, "get_session", mock_get_session)
    monkeypatch.setattr(cfg.settings, "vault_id", vid)

    from app.main import app

    app.router.lifespan_context = _null_lifespan

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/vault/meta/output-language")
    assert resp.status_code == 404


# ── vault.py bootstrap unit tests (no HTTP) ───────────────────────────────────


def test_build_index_md_base_sections() -> None:
    """_build_index_md with no extra dirs produces base sections + ## Recently Updated."""
    from app.vault import _build_index_md

    content = _build_index_md()
    assert "# Wiki Index" in content
    assert "## Recently Updated" in content
    assert "## Entities" in content
    assert "## Concepts" in content
    assert "## Sources" in content
    assert "## Queries" in content
    assert "## Comparisons" in content
    assert "## Synthesis" in content


def test_build_index_md_extra_dirs() -> None:
    """_build_index_md with extra dirs adds custom sections (hyphens → title-case)."""
    from app.vault import _build_index_md

    content = _build_index_md(["wiki/thesis", "wiki/methodology", "wiki/plot-threads"])
    assert "## Thesis" in content
    assert "## Methodology" in content
    assert "## Plot Threads" in content  # hyphen → space → title-case


def test_build_log_md_contains_project_created() -> None:
    """_build_log_md returns content with today's date and '- Project created'."""
    from app.vault import _build_log_md

    content = _build_log_md()
    assert "- Project created" in content
    assert "# Research Log" in content

    import re

    assert re.search(r"## \d{4}-\d{2}-\d{2}", content), "log.md missing dated section"


def test_bootstrap_vault_at_with_scenario_creates_dirs(tmp_path: Path) -> None:
    """bootstrap_vault_at with scenario_id creates the scenario's extra dirs."""
    from app.vault import bootstrap_vault_at

    vault = tmp_path / "vault"
    bootstrap_vault_at(vault, scenario_id="research")

    assert (vault / "wiki" / "methodology").is_dir()
    assert (vault / "wiki" / "findings").is_dir()
    assert (vault / "wiki" / "thesis").is_dir()
    # Base dirs still present
    assert (vault / "wiki" / "entities").is_dir()
    assert (vault / "raw" / "sources").is_dir()


def test_bootstrap_vault_at_scenario_overwrites_schema(tmp_path: Path) -> None:
    """bootstrap_vault_at with scenario overwrites schema.md with scenario content."""
    from app.vault import bootstrap_vault_at

    vault = tmp_path / "vault"
    # Pre-write a schema.md (simulates re-run where file already exists)
    vault.mkdir(parents=True)
    (vault / "schema.md").write_text("# Old Schema\n", encoding="utf-8")

    bootstrap_vault_at(vault, scenario_id="research")

    schema = (vault / "schema.md").read_text(encoding="utf-8")
    # Old content replaced
    assert "# Old Schema" not in schema
    # Research-specific content present
    assert "| thesis | wiki/thesis/ |" in schema


def test_bootstrap_vault_at_no_scenario_write_if_absent(tmp_path: Path) -> None:
    """bootstrap_vault_at without scenario does NOT overwrite existing schema.md."""
    from app.vault import bootstrap_vault_at

    vault = tmp_path / "vault"
    vault.mkdir(parents=True)
    (vault / "schema.md").write_text("# My Custom Schema\n", encoding="utf-8")

    bootstrap_vault_at(vault)  # no scenario

    schema = (vault / "schema.md").read_text(encoding="utf-8")
    assert "# My Custom Schema" in schema  # unchanged
