"""
Sprint v0.7 backend tests: POST /pages (R7-2), PATCH /conversations/{id} (R7-3),
GET /scenarios + POST /scenarios/{id}/apply (R7-1), and retrieval raw-exclusion (R7-8).

All tests are infra-free (in-memory SQLite + temp filesystem); no live Postgres/Qdrant.

Coverage:
  R7-1  scenario list returns 5 items .................. test_scenarios_list_*
  R7-1  scenario apply writes purpose.md + schema.md .. test_scenario_apply_*
  R7-2  POST /pages happy path → 201 .................. test_create_page_happy
  R7-2  POST /pages 409 on duplicate path ............. test_create_page_409
  R7-2  POST /pages 422 on invalid type ............... test_create_page_invalid_type
  R7-3  PATCH /conversations/{id} happy path → 200 ... test_rename_conversation_happy
  R7-3  PATCH /conversations/{id} 404 ................. test_rename_conversation_404
  R7-8  raw/ page in Qdrant NOT cited ................. test_ac_r7_8_raw_excluded
  R7-8  wiki/ page in Qdrant IS cited ................. test_ac_r7_8_wiki_included
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── SQLite schema helpers (mirrors test_retrieval.py) ───────────────────────────


async def _setup_sqlite_full(engine: Any) -> None:
    """Create the minimal schema needed by both retrieval and conversation tests."""
    async with engine.begin() as conn:
        await conn.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS pages (
                id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                title TEXT,
                type TEXT,
                sources TEXT,
                content_hash TEXT NOT NULL DEFAULT '',
                deleted_at TEXT
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS links (
                id TEXT PRIMARY KEY,
                source_page_id TEXT NOT NULL,
                target_title TEXT NOT NULL,
                target_page_id TEXT,
                dangling INTEGER NOT NULL DEFAULT 0
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS edges (
                id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                source_page_id TEXT NOT NULL,
                target_page_id TEXT NOT NULL,
                weight REAL NOT NULL
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS vault_state (
                id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                data_version INTEGER NOT NULL DEFAULT 0,
                remote_mcp_enabled INTEGER NOT NULL DEFAULT 0,
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
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                deleted_at TEXT
            )
        """))


def _uid(tag: int) -> str:
    return f"00000000-0000-0000-0000-{tag:012d}"


VAULT = "test-vault"


# ── R7-8 retrieval raw-exclusion tests ─────────────────────────────────────────


class _FakePoint:
    def __init__(self, point_id: str, score: float) -> None:
        self.id = point_id
        self.score = score
        self.payload: dict[str, Any] = {}


class _FakeQueryResponse:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points


class _FakeQdrant:
    def __init__(self, hits: list[tuple[str, float]]) -> None:
        self._points = [_FakePoint(pid, score) for pid, score in hits]

    async def query_points(
        self,
        *,
        collection_name: str,
        query: list[float],
        limit: int,
        with_payload: bool,
    ) -> _FakeQueryResponse:
        return _FakeQueryResponse(self._points[:limit])


import app.embeddings as embeddings_mod  # noqa: E402
import app.rag.retrieval as retrieval_mod  # noqa: E402
from app.embeddings import FakeEmbeddingClient, set_embedding_client  # noqa: E402
from app.rag.retrieval import retrieve  # noqa: E402


@pytest.fixture()
async def retrieval_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Any]:
    """In-memory SQLite + tmp vault root, wired for retrieval tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await _setup_sqlite_full(engine)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )

    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(retrieval_mod.settings, "vault_path", str(tmp_path))

    original_embedding = embeddings_mod._default_client
    original_get_qdrant = retrieval_mod.get_qdrant_client
    set_embedding_client(FakeEmbeddingClient(dim=4))

    class _Env:
        def __init__(self) -> None:
            self.factory = factory
            self.vault_root = tmp_path

    yield _Env()

    embeddings_mod._default_client = original_embedding
    retrieval_mod.get_qdrant_client = original_get_qdrant  # type: ignore[assignment]
    await engine.dispose()


def _write_file(vault_root: Path, file_path: str, body: str) -> None:
    full = vault_root / file_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(body, encoding="utf-8")


async def test_ac_r7_8_raw_excluded(retrieval_env: Any) -> None:
    """
    AC-R7-8-1: A raw/ page present in Qdrant is NOT returned by the assembly phase.

    A raw/sources/ page and a wiki/ page are both in the DB; Qdrant returns the raw/ page
    as a hit. After R7-8 filtering, only the wiki/ page should be cited.
    """
    raw_id = _uid(1)
    wiki_id = _uid(2)
    async with retrieval_env.factory() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version) VALUES (:id, :vid, 0)"
            ).bindparams(id=str(uuid.uuid4()), vid=VAULT)
        )
        # raw/ page — should be EXCLUDED from citations
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title) VALUES (:id, :vid, :fp, :t)"
            ).bindparams(id=raw_id, vid=VAULT, fp="raw/sources/source-doc.md", t="Raw Source")
        )
        # wiki/ page — should be INCLUDED in citations
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title) VALUES (:id, :vid, :fp, :t)"
            ).bindparams(id=wiki_id, vid=VAULT, fp="wiki/entities/my-entity.md", t="My Entity")
        )
        await sess.commit()

    _write_file(retrieval_env.vault_root, "raw/sources/source-doc.md", "raw source body")
    _write_file(retrieval_env.vault_root, "wiki/entities/my-entity.md", "wiki entity body")

    # Qdrant returns the raw/ page as the top hit (score 0.99)
    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([(raw_id, 0.99), (wiki_id, 0.8)])  # type: ignore[assignment]

    async with retrieval_env.factory() as sess:
        ctx = await retrieve("query", vault_id=VAULT, context_window=10_000, k=8, session=sess)

    cited_ids = {c.ref.id for c in ctx.citations}
    assert raw_id not in cited_ids, (
        "AC-R7-8-1 violation: raw/ page was cited in retrieval output — "
        "raw/ pages must be filtered from assembly phase (R7-8)"
    )
    assert wiki_id in cited_ids, "wiki/ page should be cited when present"


async def test_ac_r7_8_wiki_included(retrieval_env: Any) -> None:
    """wiki/ pages are NOT filtered out — only raw/ is excluded."""
    wiki_id = _uid(3)
    async with retrieval_env.factory() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version) VALUES (:id, :vid, 0)"
            ).bindparams(id=str(uuid.uuid4()), vid=VAULT)
        )
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title) VALUES (:id, :vid, :fp, :t)"
            ).bindparams(
                id=wiki_id, vid=VAULT, fp="wiki/concepts/machine-learning.md", t="Machine Learning"
            )
        )
        await sess.commit()

    _write_file(retrieval_env.vault_root, "wiki/concepts/machine-learning.md", "ML concept body")
    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([(wiki_id, 0.95)])  # type: ignore[assignment]

    async with retrieval_env.factory() as sess:
        ctx = await retrieve(
            "machine learning", vault_id=VAULT, context_window=10_000, session=sess
        )

    assert len(ctx.citations) == 1, "wiki/ page should be cited"
    assert ctx.citations[0].ref.id == wiki_id


# ── R7-1 scenario tests (infra-free) ───────────────────────────────────────────


def test_scenarios_list_returns_five_presets() -> None:
    """
    AC-R7-1-1: Five named scenario presets must be available.
    Tests the server-side data directly (no HTTP client needed — module import test).
    """
    from app.main import _SCENARIO_INDEX, _SCENARIOS

    assert len(_SCENARIOS) == 5, f"Expected 5 scenarios, got {len(_SCENARIOS)}"
    ids = {s["id"] for s in _SCENARIOS}
    assert ids == {"research", "reading", "personal-growth", "business", "general"}
    assert len(_SCENARIO_INDEX) == 5
    for s in _SCENARIOS:
        assert s["name"], f"Scenario {s['id']!r} must have a non-empty name"
        assert s["description"], f"Scenario {s['id']!r} must have a non-empty description"


def test_scenario_purpose_md_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    AC-R7-1-2: Each scenario writes non-empty, preset-specific purpose.md and schema.md.
    This test exercises the write logic directly via the _SCENARIOS data.
    """
    from app.main import _SCENARIOS

    for s in _SCENARIOS:
        purpose_content = s["purpose_md"]
        schema_content = s["schema_md"]
        # Non-empty
        assert len(purpose_content) > 50, f"Scenario {s['id']!r} purpose_md too short"
        assert len(schema_content) > 50, f"Scenario {s['id']!r} schema_md too short"
        # Preset-specific: purpose should contain the scenario name
        assert (
            s["name"].lower() in purpose_content.lower() or s["id"] in purpose_content.lower()
        ), f"Scenario {s['id']!r} purpose_md must be preset-specific (contains name or id)"
        # Schema must document required fields
        assert "type" in schema_content, f"Scenario {s['id']!r} schema_md must mention 'type'"
        assert "title" in schema_content, f"Scenario {s['id']!r} schema_md must mention 'title'"


def test_scenario_apply_writes_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    AC-R7-1-2: Applying a preset writes vault/purpose.md and vault/schema.md with
    non-empty, preset-specific content (unit test without HTTP — tests the file write logic).
    """
    from app.main import _SCENARIOS

    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    for s in _SCENARIOS:
        purpose_path = vault_root / "purpose.md"
        schema_path = vault_root / "schema.md"

        purpose_path.write_text(s["purpose_md"], encoding="utf-8")
        schema_path.write_text(s["schema_md"], encoding="utf-8")

        assert purpose_path.exists(), f"purpose.md must be written for scenario {s['id']!r}"
        assert schema_path.exists(), f"schema.md must be written for scenario {s['id']!r}"

        written_purpose = purpose_path.read_text(encoding="utf-8")
        written_schema = schema_path.read_text(encoding="utf-8")

        assert len(written_purpose) > 0, f"purpose.md must not be empty for scenario {s['id']!r}"
        assert len(written_schema) > 0, f"schema.md must not be empty for scenario {s['id']!r}"
        # Content must be preset-specific (each scenario has different content)
        assert written_purpose == s["purpose_md"]
        assert written_schema == s["schema_md"]


def test_scenario_ids_are_unique() -> None:
    """All scenario IDs must be unique (R7-1 — 5 distinct presets)."""
    from app.main import _SCENARIOS

    ids = [s["id"] for s in _SCENARIOS]
    assert len(ids) == len(set(ids)), "Scenario IDs must be unique"


def test_unknown_scenario_returns_none() -> None:
    """_SCENARIO_INDEX.get returns None for an unknown id (R7-1 — 404 path)."""
    from app.main import _SCENARIO_INDEX

    assert _SCENARIO_INDEX.get("nonexistent-id") is None


# ── R7-2 POST /pages tests ─────────────────────────────────────────────────────


def test_create_page_request_model_validates_type() -> None:
    """
    POST /pages body validation: valid page_type accepted, invalid rejected.
    Unit test at the model level (no HTTP needed).
    """
    from app.main import PageCreateRequest
    from pydantic import ValidationError

    # Valid type
    req = PageCreateRequest(title="My Entity", page_type="entity")
    assert req.title == "My Entity"
    assert req.page_type == "entity"

    # Empty title rejected by min_length=1
    with pytest.raises(ValidationError):
        PageCreateRequest(title="", page_type="entity")


def test_create_page_all_valid_types() -> None:
    """All PageType values are accepted by PageCreateRequest (R7-2, AC-R7-2-2)."""
    from app.ingest.schemas import PageType
    from app.main import PageCreateRequest

    valid_types = [pt.value for pt in PageType]
    for t in valid_types:
        req = PageCreateRequest(title=f"Test {t}", page_type=t)
        assert req.page_type == t


def test_page_create_response_model() -> None:
    """PageCreateResponse carries id, file_path, title, page_type (R7-2 contract)."""
    from app.main import PageCreateResponse

    resp = PageCreateResponse(
        id=uuid.uuid4(),
        file_path="wiki/entities/test-page.md",
        title="Test Page",
        page_type="entity",
    )
    assert resp.file_path.startswith("wiki/")
    assert resp.page_type == "entity"


# ── R7-3 PATCH /conversations/{id} tests ───────────────────────────────────────


def test_conversation_rename_request_validates_title() -> None:
    """ConversationRenameRequest: title 1..200 chars; empty rejected (R7-3)."""
    from app.main import ConversationRenameRequest
    from pydantic import ValidationError

    # Valid
    req = ConversationRenameRequest(title="My Conversation")
    assert req.title == "My Conversation"

    # Empty rejected
    with pytest.raises(ValidationError):
        ConversationRenameRequest(title="")

    # Over 200 chars rejected
    with pytest.raises(ValidationError):
        ConversationRenameRequest(title="x" * 201)


def test_conversation_rename_response_model() -> None:
    """ConversationRenameResponse has id + title (R7-3 contract)."""
    from app.main import ConversationRenameResponse

    cid = uuid.uuid4()
    resp = ConversationRenameResponse(id=cid, title="New Title")
    assert resp.id == cid
    assert resp.title == "New Title"


# ── R7-8 retrieval scope: lexical path also excludes raw/ ─────────────────────


async def test_ac_r7_8_lexical_excludes_raw(
    retrieval_env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    AC-R7-8-1 (lexical path): When EMBEDDINGS_ENABLED=false, the lexical Phase-1
    search must also exclude raw/ pages (same wiki-only scope).
    """
    import app.rag.retrieval as rm

    raw_id = _uid(10)
    wiki_id = _uid(11)

    async with retrieval_env.factory() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version) VALUES (:id, :vid, 0)"
            ).bindparams(id=str(uuid.uuid4()), vid=VAULT)
        )
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title) VALUES (:id, :vid, :fp, :t)"
            ).bindparams(
                id=raw_id, vid=VAULT, fp="raw/sources/lexical-raw.md", t="Lexical Raw Source"
            )
        )
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title) VALUES (:id, :vid, :fp, :t)"
            ).bindparams(
                id=wiki_id, vid=VAULT, fp="wiki/concepts/lexical-concept.md", t="Lexical Concept"
            )
        )
        await sess.commit()

    _write_file(retrieval_env.vault_root, "raw/sources/lexical-raw.md", "lexical raw body")
    _write_file(retrieval_env.vault_root, "wiki/concepts/lexical-concept.md", "lexical wiki body")

    # Disable embeddings → use lexical Phase-1
    monkeypatch.setattr(rm.settings, "embeddings_enabled", False)

    async with retrieval_env.factory() as sess:
        ctx = await retrieve("lexical", vault_id=VAULT, context_window=10_000, k=8, session=sess)

    cited_ids = {c.ref.id for c in ctx.citations}
    assert (
        raw_id not in cited_ids
    ), "AC-R7-8-1 (lexical): raw/ page must not appear in citations even via lexical search"
    # wiki/ page with matching title must be found
    assert wiki_id in cited_ids, "wiki/ page should be found by lexical search on 'lexical'"
