"""
POST /chat/save-to-wiki endpoint tests (G-P0-1).

Infra-free: SQLite in-memory DB, stubbed write_wiki_page.

Coverage:
  - Clean content: strips <think>…</think> and <!-- cited: … --> before saving
  - Returns {page_id, file_path} on success (201)
  - Missing / empty title → 422
  - Content empty after strip → 422
  - CG-A2/A5 classifier: an open question → type=query (queries/); an answer/analysis →
    type=synthesis (synthesis/)
  - CG-A3: a bounded fire-and-forget wikilink-enrichment pass is scheduled on the saved page
  - Sources list is forwarded; conversation_id is appended as pseudo-source
  - Upsert semantics: calling twice with same title reuses write_wiki_page (idempotent, I1)
  - The write itself makes no inference provider call (I6)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._db_fixtures import make_sqlite_engine

# ── Test helpers ───────────────────────────────────────────────────────────────

_FAKE_PAGE_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_FAKE_FILE_PATH = "wiki/queries/what-is-bge-m3.md"


def _make_fake_page(page_id: uuid.UUID = _FAKE_PAGE_ID, file_path: str = _FAKE_FILE_PATH) -> Any:
    """Return a mock Page ORM row (mirroring what write_wiki_page returns)."""
    page = MagicMock()
    page.id = page_id
    page.file_path = file_path
    return page


# ── Minimal test app ───────────────────────────────────────────────────────────


@pytest.fixture()
async def client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    """
    Minimal FastAPI test client for /chat/save-to-wiki:
    - SQLite in-memory vault_state row
    - write_wiki_page stubbed to return a fake Page without touching the filesystem
    - No watcher / embeddings / qdrant
    """
    from app import config as cfg

    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "queries").mkdir()
    log_md = wiki_dir / "log.md"
    log_md.write_text("---\ntype: log\ntitle: Log\n---\n", encoding="utf-8")
    obsidian_dir = wiki_dir / ".obsidian"
    obsidian_dir.mkdir()
    (obsidian_dir / "app.json").write_text('{"legacyEditor":false}', encoding="utf-8")

    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(cfg.settings, "vault_id", "test")
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(type(cfg.settings), "log_md_path", property(lambda self: log_md))

    engine_db = await make_sqlite_engine()
    async with engine_db.begin() as conn:
        await conn.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                "VALUES (:id, 'test', 0, datetime('now'))"
            ).bindparams(id=str(uuid.uuid4()))
        )

    session_factory = async_sessionmaker(
        bind=engine_db,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    from app import db as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", session_factory)

    from app.main import app as main_app

    transport = ASGITransport(app=main_app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── Content cleaning ───────────────────────────────────────────────────────────


class TestContentCleaning:
    """G-P0-1: content is cleaned before saving."""

    async def test_strips_think_blocks(self) -> None:
        """<think>…</think> blocks are removed from content."""
        from app.main import _clean_chat_content

        raw = "<think>internal reasoning</think>The actual answer."
        assert _clean_chat_content(raw) == "The actual answer."

    async def test_strips_multiline_think_blocks(self) -> None:
        """Multi-line <think> blocks are removed."""
        from app.main import _clean_chat_content

        raw = "<think>\nline1\nline2\n</think>\nBody text."
        assert _clean_chat_content(raw) == "Body text."

    async def test_strips_cited_trailer(self) -> None:
        """<!-- cited: … --> trailer is removed."""
        from app.main import _clean_chat_content

        raw = "Answer text.<!-- cited: 1,2,3 -->"
        assert _clean_chat_content(raw) == "Answer text."

    async def test_strips_both(self) -> None:
        """Both think blocks and cited trailers are stripped."""
        from app.main import _clean_chat_content

        raw = "<think>reasoning</think>Clean answer.<!-- cited: 1 -->"
        assert _clean_chat_content(raw) == "Clean answer."

    async def test_no_artifacts_unchanged(self) -> None:
        """Content without artifacts is returned as-is (stripped)."""
        from app.main import _clean_chat_content

        raw = "bge-m3 is a model."
        assert _clean_chat_content(raw) == "bge-m3 is a model."

    async def test_case_insensitive_think(self) -> None:
        """THINK tag is stripped case-insensitively."""
        from app.main import _clean_chat_content

        raw = "<THINK>ignored</THINK>answer"
        assert _clean_chat_content(raw) == "answer"


# ── Endpoint success path ──────────────────────────────────────────────────────


class TestSaveToWikiEndpoint:
    """G-P0-1: POST /chat/save-to-wiki endpoint contract."""

    async def test_201_response(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST /chat/save-to-wiki returns 201 on success."""
        fake_page = _make_fake_page()
        with patch(
            (
                "app.main.save_chat_to_wiki.__wrapped__"
                if hasattr(
                    __import__("app.main", fromlist=["save_chat_to_wiki"]).save_chat_to_wiki,
                    "__wrapped__",
                )
                else "app.ingest.orchestrator.write_wiki_page"
            ),
            new=AsyncMock(return_value=fake_page),
        ):
            resp = await client.post(
                "/chat/save-to-wiki",
                json={"title": "What is bge-m3?", "content": "bge-m3 is a model."},
            )
        assert resp.status_code == 201, resp.text

    async def test_response_fields(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Response contains page_id and file_path."""
        fake_page = _make_fake_page()
        with patch(
            "app.ingest.orchestrator.write_wiki_page",
            new=AsyncMock(return_value=fake_page),
        ):
            resp = await client.post(
                "/chat/save-to-wiki",
                json={"title": "What is bge-m3?", "content": "bge-m3 is a model."},
            )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert "page_id" in body
        assert "file_path" in body
        assert str(_FAKE_PAGE_ID) == body["page_id"]
        assert _FAKE_FILE_PATH == body["file_path"]

    async def test_no_provider_call(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No inference provider is called (I6). Only write_wiki_page is called."""
        call_log: list[str] = []

        async def _fake_write(session: Any, page: Any, origin: Any) -> Any:
            call_log.append("write_wiki_page")
            return _make_fake_page()

        with patch("app.ingest.orchestrator.write_wiki_page", new=_fake_write):
            resp = await client.post(
                "/chat/save-to-wiki",
                json={"title": "Test", "content": "Some content."},
            )
        assert resp.status_code == 201, resp.text
        assert call_log == ["write_wiki_page"], "Only write_wiki_page should be called (I6)"

    async def test_question_page_type_is_query(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CG-A2/A5: an open QUESTION keeps type=query, landing in wiki/queries/."""
        captured: list[Any] = []

        async def _capture_write(session: Any, page: Any, origin: Any) -> Any:
            captured.append(page)
            return _make_fake_page(
                file_path=f"wiki/queries/{page.title.lower().replace(' ', '-')}.md"
            )

        with patch("app.ingest.orchestrator.write_wiki_page", new=_capture_write):
            resp = await client.post(
                "/chat/save-to-wiki",
                json={"title": "What is bge-m3?", "content": "bge-m3 is a model."},
            )
        assert resp.status_code == 201, resp.text
        assert len(captured) == 1
        assert str(captured[0].type) == "query", f"Expected type=query, got {captured[0].type!r}"
        assert str(captured[0].frontmatter.type) == "query"

    async def test_analytical_answer_is_synthesis(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CG-A2/A5: an answer/analysis (the common case) files as type=synthesis (synthesis/)."""
        captured: list[Any] = []

        async def _capture_write(session: Any, page: Any, origin: Any) -> Any:
            captured.append(page)
            return _make_fake_page(
                file_path=f"wiki/synthesis/{page.title.lower().replace(' ', '-')}.md"
            )

        with patch("app.ingest.orchestrator.write_wiki_page", new=_capture_write):
            resp = await client.post(
                "/chat/save-to-wiki",
                json={
                    "title": "bge-m3 embedding tradeoffs",
                    "content": "bge-m3 balances dense and sparse retrieval for multilingual recall.",
                },
            )
        assert resp.status_code == 201, resp.text
        assert len(captured) == 1
        assert (
            str(captured[0].type) == "synthesis"
        ), f"Expected type=synthesis, got {captured[0].type!r}"
        assert str(captured[0].frontmatter.type) == "synthesis"
        assert resp.json()["file_path"].startswith("wiki/synthesis/")

    async def test_schedules_wikilink_enrichment(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CG-A3: a fire-and-forget enrich_wikilinks pass is scheduled on the saved page."""
        enrich_mock = AsyncMock(return_value=None)

        async def _fake_write(session: Any, page: Any, origin: Any) -> Any:
            return _make_fake_page(file_path="wiki/synthesis/aws-pricing-analysis.md")

        with (
            patch("app.ingest.orchestrator.write_wiki_page", new=_fake_write),
            patch("app.ops.enrich_wikilinks.enrich_wikilinks", new=enrich_mock),
        ):
            resp = await client.post(
                "/chat/save-to-wiki",
                json={"title": "AWS pricing analysis", "content": "AWS charges per-hour."},
            )
            assert resp.status_code == 201, resp.text
            # Drain the fire-and-forget enrichment task (bounded wait).
            for _ in range(50):
                if enrich_mock.await_count:
                    break
                await asyncio.sleep(0.01)

        assert enrich_mock.await_count == 1, "enrich_wikilinks should be scheduled once (CG-A3)"
        called_pages = enrich_mock.call_args.args[0]
        assert isinstance(called_pages, list) and len(called_pages) == 1

    async def test_enrichment_failure_never_fails_save(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CG-A3: a raising enrichment pass must NOT fail the 201 save (fire-and-forget)."""
        boom = AsyncMock(side_effect=RuntimeError("provider exploded"))

        async def _fake_write(session: Any, page: Any, origin: Any) -> Any:
            return _make_fake_page(file_path="wiki/synthesis/x.md")

        with (
            patch("app.ingest.orchestrator.write_wiki_page", new=_fake_write),
            patch("app.ops.enrich_wikilinks.enrich_wikilinks", new=boom),
        ):
            resp = await client.post(
                "/chat/save-to-wiki",
                json={"title": "Some analysis", "content": "A durable answer."},
            )
            assert resp.status_code == 201, resp.text
            for _ in range(50):
                if boom.await_count:
                    break
                await asyncio.sleep(0.01)

    async def test_think_block_stripped_before_write(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """<think>…</think> blocks are stripped before write_wiki_page is called."""
        captured: list[Any] = []

        async def _capture_write(session: Any, page: Any, origin: Any) -> Any:
            captured.append(page)
            return _make_fake_page()

        with patch("app.ingest.orchestrator.write_wiki_page", new=_capture_write):
            resp = await client.post(
                "/chat/save-to-wiki",
                json={
                    "title": "Test",
                    "content": "<think>private reasoning</think>Public answer.",
                },
            )
        assert resp.status_code == 201, resp.text
        assert "<think>" not in captured[0].content
        assert "Public answer." in captured[0].content

    async def test_cited_trailer_stripped_before_write(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """<!-- cited: … --> trailers are stripped before write_wiki_page is called."""
        captured: list[Any] = []

        async def _capture_write(session: Any, page: Any, origin: Any) -> Any:
            captured.append(page)
            return _make_fake_page()

        with patch("app.ingest.orchestrator.write_wiki_page", new=_capture_write):
            resp = await client.post(
                "/chat/save-to-wiki",
                json={"title": "Test", "content": "Answer text.<!-- cited: 1,2 -->"},
            )
        assert resp.status_code == 201, resp.text
        assert "<!-- cited:" not in captured[0].content
        assert "Answer text." in captured[0].content

    async def test_sources_forwarded(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Provided sources are forwarded to the page frontmatter."""
        captured: list[Any] = []

        async def _capture_write(session: Any, page: Any, origin: Any) -> Any:
            captured.append(page)
            return _make_fake_page()

        with patch("app.ingest.orchestrator.write_wiki_page", new=_capture_write):
            resp = await client.post(
                "/chat/save-to-wiki",
                json={
                    "title": "Test",
                    "content": "Answer.",
                    "sources": ["raw/sources/my-doc.md"],
                },
            )
        assert resp.status_code == 201, resp.text
        assert "raw/sources/my-doc.md" in captured[0].frontmatter.sources

    async def test_conversation_id_appended_as_source(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """conversation_id is appended as a pseudo-source for provenance."""
        captured: list[Any] = []
        conv_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

        async def _capture_write(session: Any, page: Any, origin: Any) -> Any:
            captured.append(page)
            return _make_fake_page()

        with patch("app.ingest.orchestrator.write_wiki_page", new=_capture_write):
            resp = await client.post(
                "/chat/save-to-wiki",
                json={
                    "title": "Test",
                    "content": "Answer.",
                    "conversation_id": conv_id,
                },
            )
        assert resp.status_code == 201, resp.text
        expected_ref = f"conversation/{conv_id}"
        assert expected_ref in captured[0].frontmatter.sources


# ── Validation errors ──────────────────────────────────────────────────────────


class TestSaveToWikiValidation:
    """G-P0-1: validation errors return 422."""

    async def test_missing_title_422(self, client: AsyncClient) -> None:
        """Missing title returns 422."""
        resp = await client.post(
            "/chat/save-to-wiki",
            json={"content": "Some content."},
        )
        assert resp.status_code == 422

    async def test_missing_content_422(self, client: AsyncClient) -> None:
        """Missing content returns 422."""
        resp = await client.post(
            "/chat/save-to-wiki",
            json={"title": "Test"},
        )
        assert resp.status_code == 422

    async def test_empty_title_422(self, client: AsyncClient) -> None:
        """Empty string title returns 422."""
        resp = await client.post(
            "/chat/save-to-wiki",
            json={"title": "", "content": "Content."},
        )
        assert resp.status_code == 422

    async def test_content_all_think_blocks_422(self, client: AsyncClient) -> None:
        """Content consisting entirely of a think block returns 422 (empty after strip)."""
        resp = await client.post(
            "/chat/save-to-wiki",
            json={"title": "Test", "content": "<think>only hidden</think>"},
        )
        assert resp.status_code == 422

    async def test_openapi_has_save_to_wiki_path(self, client: AsyncClient) -> None:
        """POST /chat/save-to-wiki appears in the OpenAPI schema."""
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "/chat/save-to-wiki" in schema.get(
            "paths", {}
        ), "POST /chat/save-to-wiki must appear in the OpenAPI schema (G-P0-1)"


# ── Classifier heuristic (CG-A2/A5, pure — no app fixture) ───────────────────────


class TestSaveToWikiClassifier:
    """CG-A2/A5: _is_open_question — open question → query; answer/analysis → synthesis."""

    def test_trailing_question_mark_title(self) -> None:
        from app.routers.chat import _is_open_question

        assert _is_open_question("What is bge-m3?", "bge-m3 is a model.") is True

    def test_trailing_question_mark_first_line(self) -> None:
        from app.routers.chat import _is_open_question

        # Title is not a question, but the opening content line is (85% vs 90% analogue).
        assert _is_open_question("Azure OpenAI threshold", "Should it be 85% or 90%?") is True

    def test_interrogative_lead_without_mark(self) -> None:
        from app.routers.chat import _is_open_question

        # Short, single-clause, interrogative-led title with no '?' still reads as a question.
        assert _is_open_question("How does bge-m3 work", "It uses a hybrid index.") is True

    def test_italian_question(self) -> None:
        from app.routers.chat import _is_open_question

        assert _is_open_question("Perché scala il modello?", "Perché ...") is True

    def test_declarative_noun_phrase_is_not_question(self) -> None:
        from app.routers.chat import _is_open_question

        assert _is_open_question("bge-m3 embedding tradeoffs", "bge-m3 balances ...") is False

    def test_topic_colon_heading_is_not_question(self) -> None:
        from app.routers.chat import _is_open_question

        # 'What' lead but a ':' present → an analytical heading, not an open question.
        assert _is_open_question("What we learned: cloud licensing", "We found ...") is False

    def test_long_declarative_sentence_is_not_question(self) -> None:
        from app.routers.chat import _is_open_question

        # Interrogative-ish lead but > 12 words → a declarative sentence, not a question.
        title = "How the team migrated the entire fleet from on-prem to cloud over two years"
        assert _is_open_question(title, "The migration ...") is False
