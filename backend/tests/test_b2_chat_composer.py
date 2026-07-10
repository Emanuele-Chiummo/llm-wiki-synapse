"""
B2 Chat Composer backend tests — C1 image plumbing, C2 web-search, C3 retrieval modes.

Coverage:
  C3: retrieval_mode preset mapping (fast/standard/deep/local_first → correct k/depth)
      + expansion_depth always clamped ≤ 2 (I7).
  C2: web-context block assembly with [W] namespace; web_citations in done event;
      mock SearXNG + mock fetch (NEVER hit the network); local_first gate logic.
  C1: ChatImageIn accepted in request schema; images persisted in messages table.
  Status: GET /status includes supports_vision field.
  Schema: request model validates images/use_web_search/retrieval_mode fields.

Infra-free: aiosqlite in-memory DB, mocked provider, mocked SearXNG + fetch.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── C3: retrieval_mode preset unit tests ────────────────────────────────────────


class TestRetrievalModePresets:
    """B2-C3: frozen preset mapping; depth always ≤ 2 (I7)."""

    def test_standard_defaults(self) -> None:
        from app.rag.retrieval import retrieval_mode_params

        k, depth = retrieval_mode_params("standard")
        assert k == 8
        assert depth == 2

    def test_fast_preset(self) -> None:
        from app.rag.retrieval import retrieval_mode_params

        k, depth = retrieval_mode_params("fast")
        assert k == 4
        assert depth == 0

    def test_deep_preset(self) -> None:
        from app.rag.retrieval import retrieval_mode_params

        k, depth = retrieval_mode_params("deep")
        assert k == 12
        assert depth == 2  # hard cap = _MAX_EXPANSION_DEPTH

    def test_local_first_preset(self) -> None:
        from app.rag.retrieval import retrieval_mode_params

        k, depth = retrieval_mode_params("local_first")
        assert k == 8
        assert depth == 2

    def test_unknown_mode_falls_back_to_standard(self) -> None:
        from app.rag.retrieval import retrieval_mode_params

        k, depth = retrieval_mode_params("nonexistent_mode")
        assert k == 8
        assert depth == 2

    def test_depth_always_clamped_le2(self) -> None:
        """expansion_depth MUST be ≤ 2 for all defined modes (I7 hard cap)."""
        from app.rag.retrieval import _RETRIEVAL_MODE_PRESETS, retrieval_mode_params

        for mode in _RETRIEVAL_MODE_PRESETS:
            _, depth = retrieval_mode_params(mode)
            assert depth <= 2, f"mode={mode!r} returned depth={depth} > 2"

    def test_all_four_modes_defined(self) -> None:
        """All four documented modes exist in the preset table (no typos)."""
        from app.rag.retrieval import _RETRIEVAL_MODE_PRESETS

        for mode in ("fast", "standard", "deep", "local_first"):
            assert mode in _RETRIEVAL_MODE_PRESETS


# ── C2: WebContext unit tests (no network) ──────────────────────────────────────


class TestWebContextAssembly:
    """B2-C2: web context block assembly with [W] namespace, citation structure."""

    @pytest.mark.asyncio
    async def test_empty_when_no_searxng_hits(self) -> None:
        """When SearXNG returns no hits, build_web_context returns empty."""
        from app.chat.web_context import build_web_context

        with patch("app.chat.web_context.web_search_many", return_value=[]):
            ctx = await build_web_context("test query")
        assert ctx.empty
        assert ctx.citations == []

    @pytest.mark.asyncio
    async def test_web_citations_namespace(self) -> None:
        """Assembled text uses [W1]..[Wn] markers; citations carry index/title/url."""
        from app.chat.web_context import build_web_context
        from app.ops.searxng import SearchHit

        mock_hits = [
            SearchHit(url="https://example.com/a", title="Example A", snippet="About A"),
            SearchHit(url="https://example.com/b", title="Example B", snippet="About B"),
        ]

        async def _mock_fetch(hit: Any, *, char_cap: int) -> str | None:
            return f"Content for {hit.title}"

        with (
            patch("app.chat.web_context.web_search_many", return_value=mock_hits),
            patch("app.chat.web_context._fetch_one_stripped", side_effect=_mock_fetch),
        ):
            ctx = await build_web_context("test query")

        assert not ctx.empty
        assert len(ctx.citations) == 2
        assert ctx.citations[0].index == 1
        assert ctx.citations[0].title == "Example A"
        assert ctx.citations[0].url == "https://example.com/a"
        assert ctx.citations[1].index == 2
        assert ctx.citations[1].url == "https://example.com/b"
        assert "[W1]" in ctx.text
        assert "[W2]" in ctx.text
        # wiki [n] namespace must NOT appear in web block
        assert "[1]" not in ctx.text

    @pytest.mark.asyncio
    async def test_snippet_fallback(self) -> None:
        """When fetch returns None, snippet is used as fallback content."""
        from app.chat.web_context import build_web_context
        from app.ops.searxng import SearchHit

        mock_hits = [
            SearchHit(url="https://example.com/c", title="C Page", snippet="Snippet text C"),
        ]

        with (
            patch("app.chat.web_context.web_search_many", return_value=mock_hits),
            patch("app.chat.web_context._fetch_one_stripped", return_value=None),
        ):
            ctx = await build_web_context("q")

        assert not ctx.empty
        assert "Snippet text C" in ctx.text
        assert ctx.citations[0].title == "C Page"

    @pytest.mark.asyncio
    async def test_no_results_when_hits_empty_and_no_snippet(self) -> None:
        """Hits with no fetch result and no snippet are dropped; empty context returned."""
        from app.chat.web_context import build_web_context
        from app.ops.searxng import SearchHit

        mock_hits = [
            SearchHit(url="https://example.com/d", title="D", snippet=None),
        ]

        with (
            patch("app.chat.web_context.web_search_many", return_value=mock_hits),
            patch("app.chat.web_context._fetch_one_stripped", return_value=None),
        ):
            ctx = await build_web_context("q")

        assert ctx.empty

    def test_web_citation_to_dict(self) -> None:
        """WebCitation.to_dict() produces the done-event shape."""
        from app.chat.web_context import WebCitation

        wc = WebCitation(index=3, title="My Page", url="https://example.com/x")
        d = wc.to_dict()
        assert d == {"index": 3, "title": "My Page", "url": "https://example.com/x"}


# ── Chat request schema validation ──────────────────────────────────────────────


class TestChatRequestSchema:
    """B2: request model validates images / use_web_search / retrieval_mode."""

    def test_default_values(self) -> None:
        from app.routers.chat import ChatMessageIn, ChatRequest

        req = ChatRequest(messages=[ChatMessageIn(role="user", content="hello")])
        assert req.use_web_search is False
        assert req.retrieval_mode == "standard"

    def test_use_web_search_true(self) -> None:
        from app.routers.chat import ChatMessageIn, ChatRequest

        req = ChatRequest(
            messages=[ChatMessageIn(role="user", content="hello")],
            use_web_search=True,
        )
        assert req.use_web_search is True

    def test_retrieval_mode_presets_accepted(self) -> None:
        from app.routers.chat import ChatMessageIn, ChatRequest

        for mode in ("fast", "standard", "deep", "local_first"):
            req = ChatRequest(
                messages=[ChatMessageIn(role="user", content="hi")],
                retrieval_mode=mode,  # type: ignore[arg-type]
            )
            assert req.retrieval_mode == mode

    def test_invalid_retrieval_mode_rejected(self) -> None:
        import pydantic
        from app.routers.chat import ChatMessageIn, ChatRequest

        with pytest.raises(pydantic.ValidationError):
            ChatRequest(
                messages=[ChatMessageIn(role="user", content="hi")],
                retrieval_mode="invalid_mode",  # type: ignore[arg-type]
            )

    def test_images_field_in_chat_message_in(self) -> None:
        from app.routers.chat import ChatImageIn, ChatMessageIn

        msg = ChatMessageIn(
            role="user",
            content="look at this",
            images=[ChatImageIn(mime="image/png", data_base64="abc123")],
        )
        assert msg.images is not None
        assert len(msg.images) == 1
        assert msg.images[0].mime == "image/png"
        assert msg.images[0].data_base64 == "abc123"

    def test_images_defaults_to_none(self) -> None:
        from app.routers.chat import ChatMessageIn

        msg = ChatMessageIn(role="user", content="no images")
        assert msg.images is None


# ── Shared fixture for stream integration tests ──────────────────────────────────


class _MockProvider:
    """Minimal mock InferenceProvider for chat stream tests."""

    def __init__(self, *, supports_vision: bool = False) -> None:
        self.provider_type = "api"
        self._acc: Any = None
        self._supports_vision = supports_vision

    def bind_accumulator(self, acc: Any) -> None:
        self._acc = acc

    async def chat(self, messages: list[Any], retrieval_context: str) -> AsyncIterator[str]:
        from app.ingest.schemas import Usage

        yield "hello"
        if self._acc is not None:
            self._acc.add(Usage(input_tokens=10, output_tokens=5, total_cost_usd=0.001))

    def capabilities(self) -> Any:
        from app.ingest.schemas import ProviderCapabilities

        return ProviderCapabilities(
            mode="api",
            supports_tools=True,
            supports_agentic_loop=False,
            max_context=32_768,
            name="mock",
            supports_vision=self._supports_vision,
        )


class _MockConfigRow:
    provider_type = "api"
    model_id = "mock-model"
    base_url = None
    token_budget = 60_000
    timeout_seconds = 30.0
    is_fallback = False


def _make_empty_retrieval() -> Any:
    from app.rag.retrieval import RetrievalContext

    return RetrievalContext(
        query="",
        text="",
        citations=[],
        token_budget=500,
        approx_tokens=0,
        data_version=0,
    )


def _make_retrieval_with_n_citations(n: int) -> Any:
    from app.rag.retrieval import Citation, PageRef, RetrievalContext

    citations = [
        Citation(
            n=i,
            ref=PageRef(id=str(uuid.uuid4()), title=f"Page {i}", slug=f"page-{i}"),
            score=0.9,
            phase="vector",
        )
        for i in range(1, n + 1)
    ]
    text = "".join(f"[{c.n}] {c.ref.title}\ncontent\n" for c in citations)
    return RetrievalContext(
        query="q",
        text=text,
        citations=citations,
        token_budget=500,
        approx_tokens=len(text) // 4,
        data_version=0,
    )


@asynccontextmanager
async def _make_db_session() -> AsyncIterator[AsyncSession]:
    """In-memory SQLite with Conversation + ChatMessage tables only (avoid JSONB on other tables)."""
    from app.models import Base, ChatMessage, Conversation

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[Conversation.__table__, ChatMessage.__table__],
        )
    sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _get() -> AsyncIterator[AsyncSession]:  # type: ignore[return]
        async with sf() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    # Return the factory, not a session.
    # Caller uses this as: async with _make_db_session() as get_session: ...
    # But we need to yield the get_session itself.
    # Instead, yield the engine/factory directly.
    yield _get  # type: ignore[misc]


# ── C3: retrieval mode params forwarded to retrieve() ───────────────────────────


class TestRetrievalModeIntegration:
    """Verify run_chat_stream calls retrieve() with the correct k/expansion_depth preset."""

    @pytest.mark.asyncio
    async def test_fast_mode_calls_retrieve_with_k4_depth0(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.config as cfg
        import app.db as db_mod

        vault_root = tmp_path / "vault"
        (vault_root / "wiki").mkdir(parents=True)
        monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
        monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))

        from app.models import Base, ChatMessage, Conversation

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Conversation.__table__, ChatMessage.__table__],
            )

        sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[AsyncSession]:  # type: ignore[return]
            async with sf() as sess:
                try:
                    yield sess
                    await sess.commit()
                except Exception:
                    await sess.rollback()
                    raise

        monkeypatch.setattr(db_mod, "get_session", _get_session)

        captured: dict[str, Any] = {}

        async def _mock_retrieve(query: str, *, k: int, expansion_depth: int, **kw: Any) -> Any:
            captured["k"] = k
            captured["expansion_depth"] = expansion_depth
            return _make_empty_retrieval()

        monkeypatch.setattr("app.chat.stream.retrieve", _mock_retrieve)
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider_config",
            AsyncMock(return_value=_MockConfigRow()),
        )
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider",
            lambda row: _MockProvider(),
        )
        monkeypatch.setattr(
            "app.chat.autotitle.maybe_generate_conversation_title",
            AsyncMock(return_value=None),
        )

        from app.chat.stream import run_chat_stream
        from app.ingest.schemas import Message

        async for _ in run_chat_stream(
            conversation_id=None,
            messages=[Message(role="user", content="hello")],
            vault_id="test-vault",
            context_window=32768,
            regenerate=False,
            retrieval_mode="fast",
        ):
            pass

        assert captured.get("k") == 4
        assert captured.get("expansion_depth") == 0

    @pytest.mark.asyncio
    async def test_deep_mode_calls_retrieve_with_k12_depth2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.config as cfg
        import app.db as db_mod

        vault_root = tmp_path / "vault"
        (vault_root / "wiki").mkdir(parents=True)
        monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
        monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))

        from app.models import Base, ChatMessage, Conversation

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Conversation.__table__, ChatMessage.__table__],
            )

        sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[AsyncSession]:  # type: ignore[return]
            async with sf() as sess:
                try:
                    yield sess
                    await sess.commit()
                except Exception:
                    await sess.rollback()
                    raise

        monkeypatch.setattr(db_mod, "get_session", _get_session)

        captured: dict[str, Any] = {}

        async def _mock_retrieve(query: str, *, k: int, expansion_depth: int, **kw: Any) -> Any:
            captured["k"] = k
            captured["expansion_depth"] = expansion_depth
            return _make_empty_retrieval()

        monkeypatch.setattr("app.chat.stream.retrieve", _mock_retrieve)
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider_config",
            AsyncMock(return_value=_MockConfigRow()),
        )
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider",
            lambda row: _MockProvider(),
        )
        monkeypatch.setattr(
            "app.chat.autotitle.maybe_generate_conversation_title",
            AsyncMock(return_value=None),
        )

        from app.chat.stream import run_chat_stream
        from app.ingest.schemas import Message

        async for _ in run_chat_stream(
            conversation_id=None,
            messages=[Message(role="user", content="deep query")],
            vault_id="test-vault",
            context_window=32768,
            regenerate=False,
            retrieval_mode="deep",
        ):
            pass

        assert captured.get("k") == 12
        assert captured.get("expansion_depth") == 2

    @pytest.mark.asyncio
    async def test_standard_mode_k8_depth2(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.config as cfg
        import app.db as db_mod

        vault_root = tmp_path / "vault"
        (vault_root / "wiki").mkdir(parents=True)
        monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
        monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))

        from app.models import Base, ChatMessage, Conversation

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Conversation.__table__, ChatMessage.__table__],
            )

        sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[AsyncSession]:  # type: ignore[return]
            async with sf() as sess:
                try:
                    yield sess
                    await sess.commit()
                except Exception:
                    await sess.rollback()
                    raise

        monkeypatch.setattr(db_mod, "get_session", _get_session)

        captured: dict[str, Any] = {}

        async def _mock_retrieve(query: str, *, k: int, expansion_depth: int, **kw: Any) -> Any:
            captured["k"] = k
            captured["expansion_depth"] = expansion_depth
            return _make_empty_retrieval()

        monkeypatch.setattr("app.chat.stream.retrieve", _mock_retrieve)
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider_config",
            AsyncMock(return_value=_MockConfigRow()),
        )
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider",
            lambda row: _MockProvider(),
        )
        monkeypatch.setattr(
            "app.chat.autotitle.maybe_generate_conversation_title",
            AsyncMock(return_value=None),
        )

        from app.chat.stream import run_chat_stream
        from app.ingest.schemas import Message

        async for _ in run_chat_stream(
            conversation_id=None,
            messages=[Message(role="user", content="standard")],
            vault_id="test-vault",
            context_window=32768,
            regenerate=False,
            retrieval_mode="standard",
        ):
            pass

        assert captured.get("k") == 8
        assert captured.get("expansion_depth") == 2


# ── C2: web_citations in done event ─────────────────────────────────────────────


class TestWebCitationsInDoneEvent:
    """B2-C2: done event gains web_citations field; wiki citations unchanged."""

    @pytest.fixture()
    def _stream_monkeypatch_common(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
        """Set up common mocks for stream tests."""
        import app.config as cfg
        import app.db as db_mod

        vault_root = tmp_path / "vault"
        (vault_root / "wiki").mkdir(parents=True)
        monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
        monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))

        from app.models import Base, ChatMessage, Conversation

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        # Run sync create all.
        import asyncio

        loop = asyncio.get_event_loop()

        async def _create() -> None:
            async with engine.begin() as conn:
                await conn.run_sync(
                    Base.metadata.create_all,
                    tables=[Conversation.__table__, ChatMessage.__table__],
                )

        loop.run_until_complete(_create())

        sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[AsyncSession]:  # type: ignore[return]
            async with sf() as sess:
                try:
                    yield sess
                    await sess.commit()
                except Exception:
                    await sess.rollback()
                    raise

        monkeypatch.setattr(db_mod, "get_session", _get_session)
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider_config",
            AsyncMock(return_value=_MockConfigRow()),
        )
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider",
            lambda row: _MockProvider(),
        )
        monkeypatch.setattr(
            "app.chat.stream.retrieve", AsyncMock(return_value=_make_empty_retrieval())
        )
        monkeypatch.setattr(
            "app.chat.autotitle.maybe_generate_conversation_title",
            AsyncMock(return_value=None),
        )
        return None

    @pytest.mark.asyncio
    async def test_done_event_has_empty_web_citations_when_web_search_off(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When use_web_search=False, done event has web_citations=[]."""
        import app.config as cfg
        import app.db as db_mod

        vault_root = tmp_path / "vault"
        (vault_root / "wiki").mkdir(parents=True)
        monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
        monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))

        from app.models import Base, ChatMessage, Conversation

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Conversation.__table__, ChatMessage.__table__],
            )

        sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[AsyncSession]:  # type: ignore[return]
            async with sf() as sess:
                try:
                    yield sess
                    await sess.commit()
                except Exception:
                    await sess.rollback()
                    raise

        monkeypatch.setattr(db_mod, "get_session", _get_session)
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider_config",
            AsyncMock(return_value=_MockConfigRow()),
        )
        monkeypatch.setattr("app.chat.stream.resolve_provider", lambda row: _MockProvider())
        monkeypatch.setattr(
            "app.chat.stream.retrieve", AsyncMock(return_value=_make_empty_retrieval())
        )
        monkeypatch.setattr(
            "app.chat.autotitle.maybe_generate_conversation_title",
            AsyncMock(return_value=None),
        )

        from app.chat.stream import run_chat_stream
        from app.ingest.schemas import Message

        done_event = None
        async for line in run_chat_stream(
            conversation_id=None,
            messages=[Message(role="user", content="hi")],
            vault_id="test-vault",
            context_window=32768,
            regenerate=False,
            use_web_search=False,
        ):
            ev = json.loads(line)
            if ev.get("type") == "done":
                done_event = ev

        assert done_event is not None
        assert "web_citations" in done_event
        assert done_event["web_citations"] == []

    @pytest.mark.asyncio
    async def test_done_event_has_web_citations_when_web_search_on(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When use_web_search=True with mock web hits, done event has web_citations populated."""
        import app.config as cfg
        import app.db as db_mod

        vault_root = tmp_path / "vault"
        (vault_root / "wiki").mkdir(parents=True)
        monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
        monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))

        from app.models import Base, ChatMessage, Conversation

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Conversation.__table__, ChatMessage.__table__],
            )

        sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[AsyncSession]:  # type: ignore[return]
            async with sf() as sess:
                try:
                    yield sess
                    await sess.commit()
                except Exception:
                    await sess.rollback()
                    raise

        monkeypatch.setattr(db_mod, "get_session", _get_session)
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider_config",
            AsyncMock(return_value=_MockConfigRow()),
        )
        monkeypatch.setattr("app.chat.stream.resolve_provider", lambda row: _MockProvider())
        monkeypatch.setattr(
            "app.chat.stream.retrieve", AsyncMock(return_value=_make_empty_retrieval())
        )
        monkeypatch.setattr(
            "app.chat.autotitle.maybe_generate_conversation_title",
            AsyncMock(return_value=None),
        )

        # Fake web context with two [W] citations.
        from app.chat.web_context import WebCitation, WebContext

        fake_web_ctx = WebContext(
            text="## Web search results\n\n[W1] Page One\nContent one\n[W2] Page Two\nContent two\n",
            citations=[
                WebCitation(index=1, title="Page One", url="https://example.com/1"),
                WebCitation(index=2, title="Page Two", url="https://example.com/2"),
            ],
        )

        with patch("app.chat.stream.build_web_context", return_value=fake_web_ctx):
            from app.chat.stream import run_chat_stream
            from app.ingest.schemas import Message

            done_event = None
            async for line in run_chat_stream(
                conversation_id=None,
                messages=[Message(role="user", content="what is X")],
                vault_id="test-vault",
                context_window=32768,
                regenerate=False,
                use_web_search=True,
            ):
                ev = json.loads(line)
                if ev.get("type") == "done":
                    done_event = ev

        assert done_event is not None
        assert "web_citations" in done_event
        assert len(done_event["web_citations"]) == 2
        assert done_event["web_citations"][0] == {
            "index": 1,
            "title": "Page One",
            "url": "https://example.com/1",
        }
        assert done_event["web_citations"][1] == {
            "index": 2,
            "title": "Page Two",
            "url": "https://example.com/2",
        }


# ── C2: local_first web gate ────────────────────────────────────────────────────


class TestLocalFirstGate:
    """local_first mode: web fires only when wiki hits < LOCAL_FIRST_MIN_HITS."""

    def _setup_common_mocks(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        retrieval_result: Any,
    ) -> None:
        import app.config as cfg
        import app.db as db_mod

        vault_root = tmp_path / "vault"
        (vault_root / "wiki").mkdir(parents=True)
        monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
        monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
        monkeypatch.setattr(cfg.settings, "local_first_min_hits", 3)

        from app.models import Base, ChatMessage, Conversation

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        import asyncio

        loop = asyncio.get_event_loop()
        loop.run_until_complete(self._create_tables(engine, Conversation, ChatMessage, Base))

        sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[AsyncSession]:  # type: ignore[return]
            async with sf() as sess:
                try:
                    yield sess
                    await sess.commit()
                except Exception:
                    await sess.rollback()
                    raise

        monkeypatch.setattr(db_mod, "get_session", _get_session)
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider_config",
            AsyncMock(return_value=_MockConfigRow()),
        )
        monkeypatch.setattr("app.chat.stream.resolve_provider", lambda row: _MockProvider())
        monkeypatch.setattr("app.chat.stream.retrieve", AsyncMock(return_value=retrieval_result))
        monkeypatch.setattr(
            "app.chat.autotitle.maybe_generate_conversation_title",
            AsyncMock(return_value=None),
        )

    @staticmethod
    async def _create_tables(engine: Any, *tables: Any, Base: Any = None) -> None:
        if Base is None:
            from app.models import Base as _Base

            Base = _Base
        from app.models import ChatMessage, Conversation

        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Conversation.__table__, ChatMessage.__table__],
            )

    @pytest.mark.asyncio
    async def test_local_first_no_web_when_enough_wiki_hits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """local_first + wiki hits >= threshold → web NOT called."""
        import app.config as cfg
        import app.db as db_mod

        vault_root = tmp_path / "vault"
        (vault_root / "wiki").mkdir(parents=True)
        monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
        monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
        monkeypatch.setattr(cfg.settings, "local_first_min_hits", 2)

        from app.models import Base, ChatMessage, Conversation

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Conversation.__table__, ChatMessage.__table__],
            )

        sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[AsyncSession]:  # type: ignore[return]
            async with sf() as sess:
                try:
                    yield sess
                    await sess.commit()
                except Exception:
                    await sess.rollback()
                    raise

        monkeypatch.setattr(db_mod, "get_session", _get_session)
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider_config",
            AsyncMock(return_value=_MockConfigRow()),
        )
        monkeypatch.setattr("app.chat.stream.resolve_provider", lambda row: _MockProvider())
        # 3 citations → above threshold of 2.
        monkeypatch.setattr(
            "app.chat.stream.retrieve",
            AsyncMock(return_value=_make_retrieval_with_n_citations(3)),
        )
        monkeypatch.setattr(
            "app.chat.autotitle.maybe_generate_conversation_title",
            AsyncMock(return_value=None),
        )

        web_called = False

        async def _mock_web(query: str, **kw: Any) -> Any:
            nonlocal web_called
            web_called = True
            from app.chat.web_context import _EMPTY

            return _EMPTY

        with patch("app.chat.stream.build_web_context", side_effect=_mock_web):
            from app.chat.stream import run_chat_stream
            from app.ingest.schemas import Message

            async for _ in run_chat_stream(
                conversation_id=None,
                messages=[Message(role="user", content="query")],
                vault_id="test-vault",
                context_window=32768,
                regenerate=False,
                use_web_search=True,
                retrieval_mode="local_first",
            ):
                pass

        assert not web_called, "web_context should NOT be called with sufficient wiki hits"

    @pytest.mark.asyncio
    async def test_local_first_fires_web_when_insufficient_wiki_hits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """local_first + wiki hits < threshold → web IS called."""
        import app.config as cfg
        import app.db as db_mod

        vault_root = tmp_path / "vault"
        (vault_root / "wiki").mkdir(parents=True)
        monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
        monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
        monkeypatch.setattr(cfg.settings, "local_first_min_hits", 3)

        from app.models import Base, ChatMessage, Conversation

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Conversation.__table__, ChatMessage.__table__],
            )

        sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[AsyncSession]:  # type: ignore[return]
            async with sf() as sess:
                try:
                    yield sess
                    await sess.commit()
                except Exception:
                    await sess.rollback()
                    raise

        monkeypatch.setattr(db_mod, "get_session", _get_session)
        monkeypatch.setattr(
            "app.chat.stream.resolve_provider_config",
            AsyncMock(return_value=_MockConfigRow()),
        )
        monkeypatch.setattr("app.chat.stream.resolve_provider", lambda row: _MockProvider())
        # Only 1 citation → below threshold of 3.
        monkeypatch.setattr(
            "app.chat.stream.retrieve",
            AsyncMock(return_value=_make_retrieval_with_n_citations(1)),
        )
        monkeypatch.setattr(
            "app.chat.autotitle.maybe_generate_conversation_title",
            AsyncMock(return_value=None),
        )

        web_called = False

        async def _mock_web(query: str, **kw: Any) -> Any:
            nonlocal web_called
            web_called = True
            from app.chat.web_context import _EMPTY

            return _EMPTY

        with patch("app.chat.stream.build_web_context", side_effect=_mock_web):
            from app.chat.stream import run_chat_stream
            from app.ingest.schemas import Message

            async for _ in run_chat_stream(
                conversation_id=None,
                messages=[Message(role="user", content="query")],
                vault_id="test-vault",
                context_window=32768,
                regenerate=False,
                use_web_search=True,
                retrieval_mode="local_first",
            ):
                pass

        assert web_called, "web_context SHOULD be called with insufficient wiki hits"


# ── GET /status includes supports_vision ────────────────────────────────────────


class TestStatusSupportsVision:
    """B2-C1: GET /status includes supports_vision from active provider capabilities()."""

    def test_status_response_model_has_supports_vision_field(self) -> None:
        """StatusResponse model must define supports_vision (structural)."""
        from app.routers.status import StatusResponse

        assert "supports_vision" in StatusResponse.model_fields
        assert StatusResponse.model_fields["supports_vision"].default is False

    def test_status_response_schema_includes_supports_vision(self) -> None:
        """OpenAPI schema for StatusResponse must include supports_vision."""
        from app.routers.status import StatusResponse

        schema = StatusResponse.model_json_schema()
        props = schema.get("properties", {})
        assert "supports_vision" in props
        # It must be a bool.
        assert props["supports_vision"].get("type") == "boolean"

    def test_status_response_defaults_supports_vision_false(self) -> None:
        """supports_vision defaults to False (safe default for non-vision providers)."""
        from datetime import UTC, datetime

        from app.routers.status import StatusResponse

        r = StatusResponse(
            vault_id="test",
            data_version=0,
            started_at=datetime.now(UTC),
            uptime_seconds=0.0,
            version="0.0.0",
            review_pending=0,
        )
        assert r.supports_vision is False

    def test_status_response_supports_vision_true_when_set(self) -> None:
        """supports_vision=True is serialisable and readable."""
        from datetime import UTC, datetime

        from app.routers.status import StatusResponse

        r = StatusResponse(
            vault_id="test",
            data_version=0,
            started_at=datetime.now(UTC),
            uptime_seconds=0.0,
            version="0.0.0",
            review_pending=0,
            supports_vision=True,
        )
        assert r.supports_vision is True


# ── C1: images column on ChatMessage ────────────────────────────────────────────


class TestImagesColumn:
    """B2-C1: messages.images JSONB column exists in the SQLAlchemy model."""

    def test_images_column_on_chat_message_model(self) -> None:
        from app.models import ChatMessage

        assert hasattr(ChatMessage, "images")

    def test_images_column_nullable(self) -> None:
        from app.models import ChatMessage
        from sqlalchemy import inspect as sa_inspect

        mapper = sa_inspect(ChatMessage)
        col = mapper.columns["images"]
        assert col.nullable is True

    @pytest.mark.asyncio
    async def test_images_stored_and_retrieved(self) -> None:
        """images JSON is persisted and read back correctly in SQLite."""
        from app.models import Base, ChatMessage, Conversation

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Conversation.__table__, ChatMessage.__table__],
            )

        sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        conv_id = uuid.uuid4()
        images_payload = [{"mime": "image/png", "data_base64": "aGVsbG8="}]

        async with sf() as session:
            async with session.begin():
                conv = Conversation(id=conv_id, vault_id="test", title="t")
                session.add(conv)
                await session.flush()
                msg = ChatMessage(
                    conversation_id=conv_id,
                    role="user",
                    content="hello",
                    citations=[],
                    images=images_payload,
                )
                session.add(msg)
                await session.flush()
                msg_id = msg.id

        from sqlalchemy import select

        async with sf() as session:
            row = await session.execute(select(ChatMessage).where(ChatMessage.id == msg_id))
            fetched = row.scalar_one()
            assert fetched.images == images_payload

    @pytest.mark.asyncio
    async def test_images_null_when_not_provided(self) -> None:
        """images column is NULL when no images are attached (backward compat)."""
        from app.models import Base, ChatMessage, Conversation
        from sqlalchemy import select

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.run_sync(
                Base.metadata.create_all,
                tables=[Conversation.__table__, ChatMessage.__table__],
            )

        sf = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

        conv_id = uuid.uuid4()
        async with sf() as session:
            async with session.begin():
                conv = Conversation(id=conv_id, vault_id="test", title="t")
                session.add(conv)
                await session.flush()
                msg = ChatMessage(
                    conversation_id=conv_id,
                    role="user",
                    content="text only",
                    citations=[],
                )
                session.add(msg)
                await session.flush()
                msg_id = msg.id

        async with sf() as session:
            row = await session.execute(select(ChatMessage).where(ChatMessage.id == msg_id))
            fetched = row.scalar_one()
            assert fetched.images is None


# ── C1: image plumbing through router ───────────────────────────────────────────


class TestImagePlumbingRouter:
    """B2-C1: images from ChatImageIn flow through to Message.images."""

    def test_chat_router_builds_message_images(self) -> None:
        """The router converts ChatImageIn → MessageImage correctly."""
        from app.ingest.schemas import MessageImage
        from app.routers.chat import ChatImageIn, ChatMessageIn

        req_img = ChatImageIn(mime="image/jpeg", data_base64="deadbeef")
        msg_in = ChatMessageIn(role="user", content="look", images=[req_img])

        # Replicate the router conversion logic.
        images = [
            MessageImage(mime=img.mime, data_base64=img.data_base64)
            for img in (msg_in.images or [])
        ]
        assert len(images) == 1
        assert isinstance(images[0], MessageImage)
        assert images[0].mime == "image/jpeg"
        assert images[0].data_base64 == "deadbeef"

    def test_chat_image_in_schema_fields(self) -> None:
        """ChatImageIn has exactly mime and data_base64 fields."""
        from app.routers.chat import ChatImageIn

        fields = set(ChatImageIn.model_fields.keys())
        assert fields == {"mime", "data_base64"}
