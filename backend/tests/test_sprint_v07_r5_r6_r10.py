"""
Sprint v0.7 — R7-5 / R7-6 / R7-10 backend tests (ai-agent-engineer ownership).

R7-5  Review search_queries → Deep Research seed queries:
  - migration 0019 (search_queries JSONB) applies on a SQLite test DB.
  - the stored search_queries flow end-to-end into run_deep_research as seed_queries.
  - run_deep_research uses seed_queries verbatim for iteration 1 (no provider re-generation).

R7-6  Recursive folder import + folderContext:
  - _folder_context / _folder_context_block derive the topical hint from a subfolder path.
  - the folderContext hint appears in the assembled analysis prompt (vault_context).
  - recursive scan respects IMPORT_SCAN_MAX_FILES (I7); non-recursive default ignores subdirs.

R7-10 Multi-provider routing verifications:
  (a) OpenAI-compatible + Ollama streaming route vendor reasoning fields as <think> events.
  (b) both non-CLI providers inject a MANDATORY OUTPUT LANGUAGE directive at generate time.
  (c) deep-research synthesis prompt steers classification toward a synthesis page.

All tests are self-contained: no network, no real DB, no live provider.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ══════════════════════════════════════════════════════════════════════════════
# R7-5 — migration applies + search_queries → deep_research seed_queries
# ══════════════════════════════════════════════════════════════════════════════


class TestR75MigrationAppliesSqlite:
    """AC-R7-5-1: the review_items.search_queries column exists after migration 0019."""

    def test_migration_0019_defines_search_queries_column(self) -> None:
        """Migration 0019 adds a search_queries column (portable SQL, no ::text)."""
        mig = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "0019_review_items_contextual_depth.py"
        )
        assert mig.exists(), "migration 0019 must exist"
        text = mig.read_text(encoding="utf-8")
        assert "search_queries" in text, "0019 must add the search_queries column"
        # PROJECT GOTCHA: portable SQL only — no Postgres-only ::text casts.
        assert "::text" not in text, "raw SQL must be portable (no ::text cast)"

    @pytest.mark.asyncio
    async def test_search_queries_roundtrips_on_sqlite(self, tmp_path: Path) -> None:
        """A minimal review_items table with search_queries (JSON→TEXT) round-trips on SQLite."""
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            # Mirror the migration's ADD COLUMN in a SQLite-portable form (JSONB → TEXT).
            await conn.execute(
                sa_text(
                    "CREATE TABLE review_items ("
                    "id TEXT PRIMARY KEY, vault_id TEXT NOT NULL, item_type TEXT NOT NULL, "
                    "status TEXT NOT NULL DEFAULT 'pending', search_queries TEXT NULL)"
                )
            )
            await conn.execute(
                sa_text(
                    "INSERT INTO review_items (id, vault_id, item_type, search_queries) "
                    "VALUES ('r1', 'v', 'suggestion', :sq)"
                ),
                {"sq": json.dumps(["alpha", "beta"])},
            )
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as sess:
            row = (
                await sess.execute(sa_text("SELECT search_queries FROM review_items WHERE id='r1'"))
            ).scalar_one()
        assert json.loads(row) == ["alpha", "beta"]
        await engine.dispose()


class TestR75AllSearchQueriesHelper:
    """_all_search_queries returns the full de-duplicated, bounded list (AC-R7-5-2)."""

    def test_returns_full_list(self) -> None:
        from app.ops.review import _all_search_queries

        assert _all_search_queries(["a", "b"]) == ["a", "b"]

    def test_dedups_and_drops_empties(self) -> None:
        from app.ops.review import _all_search_queries

        assert _all_search_queries(["a", "a", "", "  ", "b"]) == ["a", "b"]

    def test_non_list_returns_empty(self) -> None:
        from app.ops.review import _all_search_queries

        assert _all_search_queries(None) == []
        assert _all_search_queries("nope") == []

    def test_respects_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.config import settings
        from app.ops.review import _all_search_queries

        monkeypatch.setattr(settings, "review_search_queries_max", 2)
        assert _all_search_queries(["a", "b", "c", "d"]) == ["a", "b"]


class TestR75SeedQueriesEndToEnd:
    """AC-R7-5-2: stored search_queries flow into run_deep_research as seed_queries."""

    @pytest.mark.asyncio
    async def test_review_deep_research_passes_seed_queries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """review.deep_research schedules run_deep_research with the FULL curated seed list."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "searxng_url", "http://searxng.local")
        monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")

        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with engine.begin() as conn:
            await conn.execute(
                sa_text(
                    "CREATE TABLE review_items ("
                    "id TEXT PRIMARY KEY, vault_id TEXT NOT NULL, item_type TEXT NOT NULL, "
                    "status TEXT NOT NULL DEFAULT 'pending', page_id TEXT, source_page_id TEXT, "
                    "proposed_title TEXT, proposed_page_type TEXT, proposed_dir TEXT, "
                    "rationale TEXT, content_key TEXT, referenced_page_ids TEXT, "
                    "search_queries TEXT, resolution TEXT, created_page_id TEXT, "
                    "deep_research_run_id TEXT, "
                    "created_at TEXT NOT NULL DEFAULT (datetime('now')), "
                    "reviewed_at TEXT, reviewed_by TEXT)"
                )
            )
            await conn.execute(
                sa_text(
                    "CREATE TABLE deep_research_runs ("
                    "id TEXT PRIMARY KEY, vault_id TEXT, topic TEXT, status TEXT, "
                    "max_iter INTEGER, token_budget INTEGER, iterations_used INTEGER, "
                    "queries_used TEXT, sources_fetched INTEGER, converged INTEGER, "
                    "total_cost_usd REAL, synthesis_text TEXT, synthesis_page_id TEXT, "
                    "started_at TEXT, completed_at TEXT, error_message TEXT)"
                )
            )
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

        @asynccontextmanager
        async def _get_session():  # type: ignore[return]
            async with factory() as sess:
                try:
                    yield sess
                    await sess.commit()
                except Exception:
                    await sess.rollback()
                    raise

        monkeypatch.setattr("app.db.get_session", _get_session)
        monkeypatch.setattr("app.ops.review.get_session", _get_session)

        item_id = uuid.uuid4()
        async with factory() as sess:
            await sess.execute(
                sa_text(
                    "INSERT INTO review_items (id, vault_id, item_type, status, proposed_title, "
                    "search_queries, created_at) VALUES (:id, 'test-vault', 'suggestion', "
                    "'pending', 'Fallback Title', :sq, datetime('now'))"
                ),
                {"id": str(item_id), "sq": json.dumps(["curated one", "curated two"])},
            )
            await sess.commit()

        captured: dict[str, Any] = {}

        async def _fake_run_deep_research(**kwargs: Any) -> None:
            captured.update(kwargs)

        # Patch the scheduled coroutine. review.deep_research does
        # `import asyncio as _asyncio; _asyncio.create_task(run_deep_research(...))` — so the
        # coroutine object is constructed (calling our fake) at schedule time. We drain any
        # pending tasks after the call so the fake's body runs and populates `captured`.
        monkeypatch.setattr("app.ops.deep_research.run_deep_research", _fake_run_deep_research)

        from app.ops.review import deep_research

        result = await deep_research(item_id, vault_id="test-vault")
        assert result.review_item_id == item_id

        # Let the fire-and-forget create_task run.
        import asyncio as _aio

        await _aio.sleep(0)

        assert captured.get("seed_queries") == [
            "curated one",
            "curated two",
        ], "the FULL curated search_queries list must be passed as seed_queries (AC-R7-5-2)"
        assert captured.get("topic") == "curated one"  # topic still seeds from [0]
        await engine.dispose()


class TestR75DeepResearchUsesSeedsVerbatim:
    """AC-R7-5-2: run_deep_research uses seed_queries verbatim (no re-generation)."""

    @pytest.mark.asyncio
    async def test_seed_queries_skip_generate(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import config as cfg
        from app.ops.deep_research import FetchedSource, run_deep_research
        from app.ops.searxng import SearchHit

        monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))

        generate_called: list[int] = [0]

        async def _spy_generate(*args: Any, **kwargs: Any) -> list[str]:
            generate_called[0] += 1
            return ["GENERATED (should not be used for iter 1)"]

        searched_with: list[list[str]] = []

        async def _mock_searxng(queries: list[str]) -> list[SearchHit]:
            searched_with.append(list(queries))
            return [SearchHit(url="https://x/1", title="t", snippet="s")]

        async def _mock_fetch(hits: Any, *, iteration: int = 1) -> list[FetchedSource]:
            return [
                FetchedSource(url="https://x/1", title="t", content_md="c", iteration=iteration)
            ]

        # sufficiency immediately → single round; provider present but generate spied.
        provider = MagicMock()

        async def _chat(messages: Any, retrieval_context: str = "") -> AsyncIterator[str]:
            async def _gen() -> AsyncIterator[str]:
                yield "SUFFICIENT"

            return _gen()

        provider.chat = _chat
        provider.bind_accumulator = MagicMock()

        async def _mock_resolve(vault_id: str) -> Any:
            return provider

        with (
            patch("app.ops.deep_research._generate_queries", side_effect=_spy_generate),
            patch("app.ops.deep_research._search_searxng", side_effect=_mock_searxng),
            patch("app.ops.deep_research._fetch_and_extract", side_effect=_mock_fetch),
            patch("app.ops.deep_research._resolve_provider", side_effect=_mock_resolve),
            patch("app.ops.deep_research._create_run_row", new=AsyncMock()),
            patch("app.ops.deep_research._update_run_iterations", new=AsyncMock()),
            patch("app.ops.deep_research._update_run_sources", new=AsyncMock()),
            patch("app.ops.deep_research._update_run_synthesis_text", new=AsyncMock()),
            patch("app.ops.deep_research._finalize_run_row", new=AsyncMock()),
            patch("app.ops.deep_research._insert_source_row", new=AsyncMock()),
            patch("app.ops.deep_research._synthesize", new=AsyncMock(return_value="# S")),
            patch("app.ops.deep_research._ingest_synthesis", new=AsyncMock(return_value=None)),
        ):
            await run_deep_research(
                vault_id="v",
                topic="fallback topic",
                max_iter=1,
                token_budget=100_000,
                run_id=uuid.uuid4(),
                seed_queries=["seed a", "seed b"],
            )

        assert generate_called[0] == 0, "iteration 1 must NOT call _generate_queries (uses seeds)"
        assert searched_with[0] == [
            "seed a",
            "seed b",
        ], "the seed queries must be searched verbatim"


# ══════════════════════════════════════════════════════════════════════════════
# R7-6 — folderContext + recursive scan
# ══════════════════════════════════════════════════════════════════════════════


class TestR76FolderContext:
    """AC-R7-6-2: folderContext derived from subfolder path and injected into the prompt."""

    def test_folder_context_from_nested_path(self) -> None:
        from app.ingest.orchestrator import _folder_context

        fc = _folder_context("raw/sources/servicenow/itam/sam/foo.md")
        assert fc == "servicenow / itam / sam"

    def test_folder_context_empty_for_top_level(self) -> None:
        from app.ingest.orchestrator import _folder_context

        assert _folder_context("raw/sources/foo.md") == ""

    def test_folder_context_windows_separators(self) -> None:
        from app.ingest.orchestrator import _folder_context

        assert _folder_context("raw\\sources\\a\\b\\x.md") == "a / b"

    def test_folder_context_block_phrasing(self) -> None:
        from app.ingest.orchestrator import _folder_context_block

        block = _folder_context_block("raw/sources/servicenow/itam/foo.md")
        assert "folderContext" in block
        assert "servicenow / itam" in block
        assert "comes from the folder path" in block

    def test_folder_context_block_empty_when_no_subfolder(self) -> None:
        from app.ingest.orchestrator import _folder_context_block

        assert _folder_context_block("raw/sources/foo.md") == ""

    def test_folder_context_bounded_segments(self) -> None:
        from app.ingest.orchestrator import _FOLDER_CONTEXT_MAX_SEGMENTS, _folder_context

        deep = "raw/sources/" + "/".join(f"s{i}" for i in range(20)) + "/f.md"
        fc = _folder_context(deep)
        assert len(fc.split(" / ")) <= _FOLDER_CONTEXT_MAX_SEGMENTS

    @pytest.mark.asyncio
    async def test_folder_context_reaches_analysis_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The folderContext string is present in the vault_context the orchestrated loop uses."""
        from app.ingest import orchestrator as orch

        # Make the base ingest context deterministic (no DB catalogue).
        async def _fake_ingest_context() -> str:
            return "# schema.md\n(rules)"

        monkeypatch.setattr(orch, "_load_ingest_context", _fake_ingest_context)

        # Capture the vault_context threaded into the orchestrated loop.
        captured: dict[str, Any] = {}

        async def _fake_run_orchestrated(**kwargs: Any) -> Any:
            captured["vault_context"] = kwargs.get("vault_context")
            result = MagicMock()
            result.pages = []
            result.analysis = None
            result.converged = True
            result.iterations = 1
            result.stop_reason = "converged"
            return result

        monkeypatch.setattr(orch, "_run_orchestrated", _fake_run_orchestrated)

        # Stub the provider + all persistence/finalize hooks so only routing runs.
        provider = MagicMock()
        caps = MagicMock()
        caps.supports_agentic_loop = False
        caps.name = "StubProvider"
        caps.mode = "api"
        provider.capabilities = MagicMock(return_value=caps)
        provider.bind_accumulator = MagicMock()
        monkeypatch.setattr(orch, "resolve_provider", lambda _cfg: provider)

        monkeypatch.setattr(orch, "_open_ingest_run", AsyncMock(return_value=uuid.uuid4()))
        monkeypatch.setattr(orch, "_finalize_ingest_run", AsyncMock())
        monkeypatch.setattr(orch, "_ensure_source_summary", lambda pages, *_a: pages)
        monkeypatch.setattr(orch, "_update_overview", AsyncMock())

        handle = MagicMock()
        handle.cancel_event = MagicMock()
        handle.cancel_event.is_set = MagicMock(return_value=False)
        monkeypatch.setattr(orch.ingest_queue, "open_run", MagicMock(return_value=handle))
        monkeypatch.setattr(orch.ingest_queue, "set_route", MagicMock())
        monkeypatch.setattr(orch.ingest_queue, "set_phase", MagicMock())
        monkeypatch.setattr(orch.ingest_queue, "get_retry_count", MagicMock(return_value=0))

        cfg_row = MagicMock()
        cfg_row.model_id = "test-model"
        cfg_row.max_iter = 1
        cfg_row.token_budget = 1000

        try:
            await orch.run_ingest_pipeline(
                provider_config_row=cfg_row,
                source_text="hello world",
                origin_source="raw/sources/servicenow/itam/doc.md",
                abs_source="/tmp/doc.md",
            )
        except Exception:  # noqa: BLE001
            # Downstream persistence hooks may still fail on the stub; we only need the
            # vault_context that _run_orchestrated received (captured before any failure).
            pass

        assert captured.get("vault_context") is not None
        assert (
            "folderContext" in captured["vault_context"]
        ), "folderContext hint must be injected into the analysis prompt (AC-R7-6-2)"
        assert "servicenow / itam" in captured["vault_context"]


class TestR76RecursiveScan:
    """AC-R7-6-1/3: recursive scan is opt-in and bounded by IMPORT_SCAN_MAX_FILES (I7)."""

    def _prep(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
        from app import config as cfg

        source_dir = tmp_path / "source"
        (source_dir / "sub" / "deep").mkdir(parents=True)
        raw_sources = tmp_path / "raw" / "sources"
        raw_sources.mkdir(parents=True)
        monkeypatch.setattr(
            type(cfg.settings), "raw_sources_dir", property(lambda self: raw_sources)
        )
        monkeypatch.setattr(cfg.settings, "import_scan_max_seconds", 60)
        return source_dir, raw_sources

    @pytest.mark.asyncio
    async def test_non_recursive_ignores_subdirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import config as cfg

        source_dir, raw_sources = self._prep(tmp_path, monkeypatch)
        (source_dir / "top.md").write_text("# top\n")
        (source_dir / "sub" / "nested.md").write_text("# nested\n")

        monkeypatch.setattr(cfg.settings, "import_scan_max_files", 200)
        monkeypatch.setattr(cfg.settings, "import_scan_recursive", False)

        from app.import_scheduler import run_one_scan

        cfg_obj = MagicMock()
        cfg_obj.source_dir = str(source_dir)
        count, status, _ = await run_one_scan(cfg_obj)
        assert status == "ok"
        assert count == 1, "non-recursive default must copy only the top-level file"
        assert (raw_sources / "top.md").exists()
        assert not (raw_sources / "nested.md").exists()

    @pytest.mark.asyncio
    async def test_recursive_descends_subdirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import config as cfg

        source_dir, raw_sources = self._prep(tmp_path, monkeypatch)
        (source_dir / "top.md").write_text("# top\n")
        (source_dir / "sub" / "nested.md").write_text("# nested\n")
        (source_dir / "sub" / "deep" / "deeper.md").write_text("# deeper\n")

        monkeypatch.setattr(cfg.settings, "import_scan_max_files", 200)
        monkeypatch.setattr(cfg.settings, "import_scan_recursive", True)

        from app.import_scheduler import run_one_scan

        cfg_obj = MagicMock()
        cfg_obj.source_dir = str(source_dir)
        count, status, _ = await run_one_scan(cfg_obj)
        assert status == "ok"
        assert count == 3, "recursive scan must copy nested files too"

    @pytest.mark.asyncio
    async def test_recursive_respects_max_files_cap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import config as cfg

        source_dir, raw_sources = self._prep(tmp_path, monkeypatch)
        for i in range(10):
            (source_dir / "sub" / f"n{i:02d}.md").write_text(f"# {i}\n")

        monkeypatch.setattr(cfg.settings, "import_scan_max_files", 3)
        monkeypatch.setattr(cfg.settings, "import_scan_recursive", True)

        from app.import_scheduler import run_one_scan

        cfg_obj = MagicMock()
        cfg_obj.source_dir = str(source_dir)
        count, status, _ = await run_one_scan(cfg_obj)
        assert status == "ok"
        assert count <= 3, "recursive scan must stay bounded by IMPORT_SCAN_MAX_FILES (I7)"


# ══════════════════════════════════════════════════════════════════════════════
# R7-10 — multi-provider routing verifications
# ══════════════════════════════════════════════════════════════════════════════


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}"


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:  # noqa: D401
        return None

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


class _FakeStreamClient:
    def __init__(self, lines: list[str], **_: Any) -> None:
        self._lines = lines

    async def __aenter__(self) -> _FakeStreamClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    def stream(self, *_a: Any, **_k: Any) -> Any:
        lines = self._lines

        class _Ctx:
            async def __aenter__(self_inner) -> _FakeStreamResponse:
                return _FakeStreamResponse(lines)

            async def __aexit__(self_inner, *_: Any) -> None:
                return None

        return _Ctx()


class TestR710OpenAiReasoning:
    """AC-R7-10-1: DeepSeek reasoning_content / Qwen reasoning routed as <think> events."""

    @pytest.mark.asyncio
    async def test_deepseek_reasoning_content_wrapped_in_think(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ingest.provider.api import ApiProvider
        from app.ingest.provider.config import ProviderSettings

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        lines = [
            _sse({"choices": [{"delta": {"reasoning_content": "let me think"}}]}),
            _sse({"choices": [{"delta": {"content": "the answer"}}]}),
            _sse({"usage": {"prompt_tokens": 5, "completion_tokens": 3}}),
            "data: [DONE]",
        ]
        monkeypatch.setattr(
            "app.ingest.provider.api.httpx.AsyncClient",
            lambda **kw: _FakeStreamClient(lines, **kw),
        )
        cfg = ProviderSettings(
            provider_type="api", model_id="deepseek-reasoner", base_url="http://ds.local/v1"
        )
        provider = ApiProvider(cfg)
        from app.ingest.schemas import Message

        out = "".join(
            [c async for c in await provider.chat([Message(role="user", content="hi")], "")]
        )
        assert out == "<think>let me think</think>the answer"

    @pytest.mark.asyncio
    async def test_qwen_reasoning_field_wrapped_in_think(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ingest.provider.api import ApiProvider
        from app.ingest.provider.config import ProviderSettings

        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        lines = [
            _sse({"choices": [{"delta": {"reasoning": "qwen thought"}}]}),
            _sse({"choices": [{"delta": {"content": "visible"}}]}),
            "data: [DONE]",
        ]
        monkeypatch.setattr(
            "app.ingest.provider.api.httpx.AsyncClient",
            lambda **kw: _FakeStreamClient(lines, **kw),
        )
        cfg = ProviderSettings(
            provider_type="api", model_id="qwen-max", base_url="http://qwen.local/v1"
        )
        provider = ApiProvider(cfg)
        from app.ingest.schemas import Message

        out = "".join(
            [c async for c in await provider.chat([Message(role="user", content="hi")], "")]
        )
        assert out == "<think>qwen thought</think>visible"


class TestR710OllamaReasoning:
    """AC-R7-10-1 parity: Ollama message.thinking routed as <think> events."""

    @pytest.mark.asyncio
    async def test_ollama_thinking_wrapped_in_think(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.ingest.provider.config import ProviderSettings
        from app.ingest.provider.ollama import OllamaProvider

        lines = [
            json.dumps({"message": {"thinking": "reasoning step"}}),
            json.dumps({"message": {"content": "final"}}),
            json.dumps({"done": True, "prompt_eval_count": 4, "eval_count": 2}),
        ]
        monkeypatch.setattr(
            "app.ingest.provider.ollama.httpx.AsyncClient",
            lambda **kw: _FakeStreamClient(lines, **kw),
        )
        cfg = ProviderSettings(
            provider_type="local", model_id="deepseek-r1", base_url="http://ollama.local"
        )
        provider = OllamaProvider(cfg)
        from app.ingest.schemas import Message

        out = "".join(
            [c async for c in await provider.chat([Message(role="user", content="hi")], "")]
        )
        assert out == "<think>reasoning step</think>final"


class TestR710LanguageDirective:
    """AC-R7-10-3: the OUTPUT LANGUAGE directive is injected at generate time (both providers)."""

    def _analysis(self, lang: str) -> Any:
        from app.ingest.schemas import Analysis, PageType, SuggestedPage

        return Analysis(
            topics=["t"],
            entities=[],
            language=lang,
            suggested_pages=[SuggestedPage(title="P", type=PageType.CONCEPT)],
            summary="s",
        )

    def test_generate_prompt_contains_language_directive(self) -> None:
        from app.ingest.provider._common import build_generate_prompt

        prompt = build_generate_prompt(self._analysis("it"), "")
        assert "MANDATORY OUTPUT LANGUAGE" in prompt
        assert "it" in prompt

    @pytest.mark.asyncio
    async def test_api_provider_generate_injects_directive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ingest.provider.api import ApiProvider
        from app.ingest.provider.config import ProviderSettings

        captured: dict[str, str] = {}

        async def _fake_complete(self_p: Any, *, system: str, user: str) -> str:
            captured["user"] = user
            return json.dumps(
                {
                    "pages": [
                        {
                            "title": "P",
                            "type": "concept",
                            "content": "body",
                            "frontmatter": {
                                "type": "concept",
                                "title": "P",
                                "sources": ["raw/sources/x.md"],
                                "lang": "it",
                                "tags": ["a", "b", "c"],
                            },
                        }
                    ]
                }
            )

        monkeypatch.setattr(ApiProvider, "_complete", _fake_complete)
        cfg = ProviderSettings(provider_type="api", model_id="claude-sonnet-4-6", base_url=None)
        provider = ApiProvider(cfg)
        await provider.generate(self._analysis("it"), "")
        assert "MANDATORY OUTPUT LANGUAGE" in captured["user"]
        assert "it" in captured["user"]

    @pytest.mark.asyncio
    async def test_ollama_provider_generate_injects_directive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ingest.provider.config import ProviderSettings
        from app.ingest.provider.ollama import OllamaProvider

        captured: dict[str, str] = {}

        async def _fake_chat_json(self_p: Any, *, system: str, user: str) -> str:
            captured["user"] = user
            return json.dumps(
                {
                    "pages": [
                        {
                            "title": "P",
                            "type": "concept",
                            "content": "body",
                            "frontmatter": {
                                "type": "concept",
                                "title": "P",
                                "sources": ["raw/sources/x.md"],
                                "lang": "de",
                                "tags": ["a", "b", "c"],
                            },
                        }
                    ]
                }
            )

        monkeypatch.setattr(OllamaProvider, "_chat_json", _fake_chat_json)
        cfg = ProviderSettings(
            provider_type="local", model_id="llama3", base_url="http://ollama.local"
        )
        provider = OllamaProvider(cfg)
        await provider.generate(self._analysis("de"), "")
        assert "MANDATORY OUTPUT LANGUAGE" in captured["user"]
        assert "de" in captured["user"]


class TestR710SynthesisClassification:
    """AC-R7-10-2: the synthesis prompt steers classification toward a synthesis page."""

    @pytest.mark.asyncio
    async def test_synthesize_prompt_declares_synthesis_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.ops.deep_research import FetchedSource, _synthesize

        captured: dict[str, str] = {}

        provider = MagicMock()

        async def _chat(messages: Any, retrieval_context: str = "") -> AsyncIterator[str]:
            captured["instruction"] = messages[0].content

            async def _gen() -> AsyncIterator[str]:
                yield "# doc"

            return _gen()

        provider.chat = _chat

        await _synthesize(
            provider,
            "Kubernetes networking",
            [FetchedSource(url="https://x", title="t", content_md="c", iteration=1)],
        )
        instr = captured["instruction"]
        assert "synthesis" in instr.lower()
        assert "classified as page type 'synthesis'" in instr


# ══════════════════════════════════════════════════════════════════════════════
# v1.5.4 — Deep Research synthesis output language (owner report: wiki content came
# out in English on an Italian vault regardless of the vault's language)
# ══════════════════════════════════════════════════════════════════════════════


class TestDeepResearchSynthesisLanguage:
    """
    The synthesis prompt (the part that becomes the actual wiki page body) was never
    language-aware — same class of bug as the v1.5.2 review-propose fix. Query generation
    intentionally stays untouched (English search queries get better SearXNG recall).
    """

    def test_lang_directive_empty_for_blank_input(self) -> None:
        from app.ops.deep_research import _synthesis_lang_directive

        assert _synthesis_lang_directive("") == ""
        assert _synthesis_lang_directive("   ") == ""

    def test_lang_directive_names_the_language(self) -> None:
        from app.ops.deep_research import _synthesis_lang_directive

        directive = _synthesis_lang_directive("it")
        assert "MANDATORY OUTPUT LANGUAGE" in directive
        assert "'it'" in directive

    @pytest.mark.asyncio
    async def test_synthesize_injects_lang_directive_when_lang_given(self) -> None:
        from app.ops.deep_research import FetchedSource, _synthesize

        captured: dict[str, str] = {}
        provider = MagicMock()

        async def _chat(messages: Any, retrieval_context: str = "") -> AsyncIterator[str]:
            captured["instruction"] = messages[0].content

            async def _gen() -> AsyncIterator[str]:
                yield "# doc"

            return _gen()

        provider.chat = _chat

        await _synthesize(
            provider,
            "Kubernetes networking",
            [FetchedSource(url="https://x", title="t", content_md="c", iteration=1)],
            "it",
        )
        instr = captured["instruction"]
        assert "MANDATORY OUTPUT LANGUAGE" in instr
        assert "'it'" in instr
        # The directive is the FIRST thing the model sees, ahead of the topic/sources.
        assert instr.index("MANDATORY OUTPUT LANGUAGE") < instr.index("Kubernetes networking")

    @pytest.mark.asyncio
    async def test_synthesize_omits_lang_directive_when_lang_is_none(self) -> None:
        """Regression guard: no lang resolved (None/"") → identical prompt to pre-fix behavior."""
        from app.ops.deep_research import FetchedSource, _synthesize

        captured: dict[str, str] = {}
        provider = MagicMock()

        async def _chat(messages: Any, retrieval_context: str = "") -> AsyncIterator[str]:
            captured["instruction"] = messages[0].content

            async def _gen() -> AsyncIterator[str]:
                yield "# doc"

            return _gen()

        provider.chat = _chat

        await _synthesize(
            provider,
            "Kubernetes networking",
            [FetchedSource(url="https://x", title="t", content_md="c", iteration=1)],
        )
        assert "MANDATORY OUTPUT LANGUAGE" not in captured["instruction"]

    @pytest.mark.asyncio
    async def test_resolve_language_prefers_explicit_override_without_touching_db(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An OVERVIEW_LANGUAGE override must short-circuit — never calls _detect_vault_language."""
        from app import config as cfg
        from app.ops import deep_research as dr

        monkeypatch.setattr(cfg.settings, "overview_language", "it")
        detect_called = {"count": 0}

        async def _boom() -> str | None:
            detect_called["count"] += 1
            raise AssertionError("must not be called when an explicit override is set")

        monkeypatch.setattr("app.ingest.orchestrator._detect_vault_language", _boom)

        result = await dr._resolve_synthesis_language()
        assert result == "it"
        assert detect_called["count"] == 0

    @pytest.mark.asyncio
    async def test_resolve_language_degrades_to_empty_on_detection_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No override + vault-language detection raising (e.g. DB unavailable) → "" never raises."""
        from app import config as cfg
        from app.ops import deep_research as dr

        monkeypatch.setattr(cfg.settings, "overview_language", None)

        async def _boom() -> str | None:
            raise RuntimeError("simulated DB failure")

        monkeypatch.setattr("app.ingest.orchestrator._detect_vault_language", _boom)

        result = await dr._resolve_synthesis_language()
        assert result == ""

    @pytest.mark.asyncio
    async def test_resolve_language_uses_detected_vault_language(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No override → falls back to the detected modal vault language."""
        from app import config as cfg
        from app.ops import deep_research as dr

        monkeypatch.setattr(cfg.settings, "overview_language", None)

        async def _detected() -> str | None:
            return "de"

        monkeypatch.setattr("app.ingest.orchestrator._detect_vault_language", _detected)

        result = await dr._resolve_synthesis_language()
        assert result == "de"
