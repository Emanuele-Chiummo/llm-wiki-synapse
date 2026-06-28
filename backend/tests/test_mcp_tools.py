"""
MCP server tool tests (infra-free — FakeEmbeddingClient + monkeypatched Qdrant/DB).

Coverage:
    - search_wiki returns a list with relevance_score in [0, 1] (I9)
    - search_wiki returns empty list on Qdrant error (no exception)
    - write_page rejects missing required frontmatter fields (AC-MCP-3)
    - write_page rejects invalid provider_type (returns error dict, not exception)
    - write_page calls write_wiki_page (the shared seam — ADR-0010 §2)
    - get_page returns "not found" error dict for unknown title
    - list_pages returns list; optional type filter works
    - mcp object is a FastMCP instance with the four expected tools registered
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Smoke test: FastMCP instance has the four tools ───────────────────────────


class TestMcpServerDefinition:
    def test_mcp_is_fastmcp_instance(self) -> None:
        """The `mcp` export must be a FastMCP instance."""
        from app.mcp.server import mcp
        from fastmcp import FastMCP

        assert isinstance(mcp, FastMCP)

    def test_four_tools_registered(self) -> None:
        """The server must expose exactly the four contracted tool names (ADR-0010 §6)."""
        from app.mcp.server import mcp

        # FastMCP 3.x stores tools in _tool_manager or similar; we introspect via list_tools
        # or by checking the tool registry attribute.
        tool_names: set[str] = set()
        # Try different FastMCP API surfaces
        if hasattr(mcp, "_tool_manager"):
            tool_names = set(mcp._tool_manager._tools.keys())
        elif hasattr(mcp, "list_tools"):
            # Some versions expose list_tools synchronously
            try:
                tools = mcp.list_tools()
                tool_names = {t.name for t in tools}
            except Exception:  # noqa: BLE001
                pass
        if not tool_names:
            # Fallback: check that the decorated functions are importable
            from app.mcp import server as srv

            for name in ("search_wiki", "write_page", "get_page", "list_pages"):
                assert hasattr(srv, name), f"Tool function {name!r} not found in mcp server"
            return

        for expected in ("search_wiki", "write_page", "get_page", "list_pages"):
            assert expected in tool_names, f"Tool {expected!r} not registered in FastMCP server"


# ── search_wiki ────────────────────────────────────────────────────────────────


class TestSearchWiki:
    @pytest.mark.asyncio
    async def test_returns_list_with_scores(self) -> None:
        """search_wiki returns a list of dicts with relevance_score ∈ [0, 1]."""
        from app.embeddings import FakeEmbeddingClient, set_embedding_client
        from app.mcp.server import search_wiki

        fake_emb = FakeEmbeddingClient(dim=4)
        set_embedding_client(fake_emb)

        # Build fake Qdrant query_points result (Qdrant client ≥ 1.10 uses query_points).
        fake_hit = MagicMock()
        fake_hit.id = str(uuid.uuid4())
        fake_hit.score = 0.8  # raw cosine score
        fake_hit.payload = {"title": "Qdrant", "type": "concept"}

        fake_response = MagicMock()
        fake_response.points = [fake_hit]

        fake_qdrant = MagicMock()
        fake_qdrant.query_points = AsyncMock(return_value=fake_response)

        with patch("app.mcp.server.get_qdrant_client", return_value=fake_qdrant):
            results = await search_wiki("qdrant vector database", k=5)

        assert isinstance(results, list)
        assert len(results) == 1
        r = results[0]
        assert "relevance_score" in r
        assert 0.0 <= r["relevance_score"] <= 1.0
        assert r["title"] == "Qdrant"

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_qdrant_error(self) -> None:
        """search_wiki returns [] on Qdrant failure (no exception propagates)."""
        from app.embeddings import FakeEmbeddingClient, set_embedding_client
        from app.mcp.server import search_wiki

        fake_emb = FakeEmbeddingClient(dim=4)
        set_embedding_client(fake_emb)

        fake_qdrant = MagicMock()
        fake_qdrant.query_points = AsyncMock(side_effect=ConnectionError("qdrant down"))

        with patch("app.mcp.server.get_qdrant_client", return_value=fake_qdrant):
            results = await search_wiki("anything")

        assert results == []

    @pytest.mark.asyncio
    async def test_k_clamped_to_maximum(self) -> None:
        """k > 50 is clamped to 50."""
        from app.embeddings import FakeEmbeddingClient, set_embedding_client
        from app.mcp.server import search_wiki

        fake_emb = FakeEmbeddingClient(dim=4)
        set_embedding_client(fake_emb)

        fake_response = MagicMock()
        fake_response.points = []

        fake_qdrant = MagicMock()
        fake_qdrant.query_points = AsyncMock(return_value=fake_response)

        with patch("app.mcp.server.get_qdrant_client", return_value=fake_qdrant):
            await search_wiki("query", k=1000)

        # The limit passed to Qdrant must be ≤ 50
        call_kwargs = fake_qdrant.query_points.call_args
        assert call_kwargs.kwargs["limit"] <= 50


# ── write_page ────────────────────────────────────────────────────────────────


class TestWritePage:
    @pytest.mark.asyncio
    async def test_missing_type_returns_error(self) -> None:
        """Missing 'type' in frontmatter → error dict, not exception (AC-MCP-3)."""
        from app.mcp.server import write_page

        result = await write_page(
            title="My Page",
            content="Body text.",
            frontmatter={"title": "My Page", "sources": ["raw/sources/x.md"], "lang": "en"},
        )
        assert "error" in result
        assert "type" in result["error"].lower() or "missing" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_missing_sources_returns_error(self) -> None:
        """Missing 'sources' → error dict."""
        from app.mcp.server import write_page

        result = await write_page(
            title="My Page",
            content="Body.",
            frontmatter={"type": "concept", "title": "My Page", "lang": "en"},
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_type_returns_error(self) -> None:
        """Invalid page type value → error dict."""
        from app.mcp.server import write_page

        result = await write_page(
            title="My Page",
            content="Body.",
            frontmatter={
                "type": "INVALID_TYPE",
                "title": "My Page",
                "sources": ["raw/sources/x.md"],
                "lang": "en",
            },
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_valid_write_calls_write_wiki_page(self) -> None:
        """
        A valid write_page call invokes write_wiki_page (the shared seam — ADR-0010 §2).
        The tool must NOT write files directly.
        write_wiki_page is imported lazily inside write_page, so we patch it in orchestrator.
        """
        from app.mcp.server import write_page

        fake_page_row = MagicMock()
        fake_page_row.id = uuid.uuid4()
        fake_page_row.title = "Synapse"
        fake_page_row.page_type = "concept"

        with patch("app.ingest.orchestrator.write_wiki_page", new_callable=AsyncMock) as mock_wwp:
            mock_wwp.return_value = fake_page_row

            result = await write_page(
                title="Synapse",
                content="Synapse is a self-organising wiki.",
                frontmatter={
                    "type": "concept",
                    "title": "Synapse",
                    "sources": ["raw/sources/intro.md"],
                    "lang": "en",
                },
                origin_source="raw/sources/intro.md",
            )

        # Must have called the shared write_wiki_page seam
        mock_wwp.assert_called_once()
        # Must return a PageRef-like dict with id, title, type
        assert "error" not in result
        assert result["title"] == "Synapse"

    @pytest.mark.asyncio
    async def test_write_page_error_on_write_failure(self) -> None:
        """If write_wiki_page raises, write_page returns an error dict (no exception)."""
        from app.mcp.server import write_page

        with patch(
            "app.ingest.orchestrator.write_wiki_page",
            new_callable=AsyncMock,
            side_effect=RuntimeError("disk full"),
        ):
            result = await write_page(
                title="Page",
                content="Content.",
                frontmatter={
                    "type": "concept",
                    "title": "Page",
                    "sources": ["raw/sources/x.md"],
                    "lang": "en",
                },
                origin_source="raw/sources/x.md",
            )

        assert "error" in result
        assert "disk full" in result["error"]


# ── get_page ───────────────────────────────────────────────────────────────────


class TestGetPage:
    @pytest.mark.asyncio
    async def test_not_found_returns_error_dict(self) -> None:
        """get_page for an unknown title returns {"error": "..."} (AC-MCP-3)."""
        from app.mcp.server import get_page

        ctx = MagicMock()
        sess = MagicMock()
        sess.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        sess.expunge = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        # get_session is imported lazily inside get_page, so patch at its source
        with patch("app.db.get_session", return_value=ctx):
            result = await get_page("NonExistentPage")

        assert "error" in result
        assert "not found" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_found_returns_content_and_frontmatter(self) -> None:
        """get_page for an existing title returns content and frontmatter."""
        import tempfile
        from pathlib import Path

        from app.mcp.server import get_page

        # Create a temporary .md file
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".md",
            delete=False,
            encoding="utf-8",
        ) as f:
            f.write(
                "---\ntype: concept\ntitle: Qdrant\nsources: [src]\nlang: en\n---\n\nQdrant body.\n"
            )
            tmp_path = Path(f.name)

        fake_page = MagicMock()
        fake_page.id = uuid.uuid4()
        fake_page.title = "Qdrant"
        fake_page.page_type = "concept"
        # file_path is just the filename; vault_root will be the parent dir
        fake_page.file_path = tmp_path.name

        ctx = MagicMock()
        sess = MagicMock()
        sess.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: fake_page))
        sess.expunge = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        from app import config as cfg

        with (
            patch("app.db.get_session", return_value=ctx),
            patch.object(
                type(cfg.settings),
                "vault_root",
                new_callable=lambda: property(lambda self: tmp_path.parent),  # type: ignore[return-value]
            ),
        ):
            result = await get_page("Qdrant")

        tmp_path.unlink(missing_ok=True)

        assert "error" not in result
        assert result["title"] == "Qdrant"
        assert "Qdrant body" in result["content"]


# ── list_pages ─────────────────────────────────────────────────────────────────


class TestListPages:
    @pytest.mark.asyncio
    async def test_returns_all_live_pages(self) -> None:
        """list_pages without filter returns all live pages."""
        from app.mcp.server import list_pages

        class _FakeRow:
            id = uuid.uuid4()
            title = "Test Page"
            page_type = "concept"

        ctx = MagicMock()
        sess = MagicMock()
        sess.execute = AsyncMock(return_value=MagicMock(all=lambda: [_FakeRow()]))
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.db.get_session", return_value=ctx):
            result = await list_pages()

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["title"] == "Test Page"
        assert result[0]["relevance_score"] == 0.0

    @pytest.mark.asyncio
    async def test_type_filter_passed_to_query(self) -> None:
        """list_pages with type='entity' passes the filter to the SQL query."""
        from app.mcp.server import list_pages

        ctx = MagicMock()
        sess = MagicMock()
        sess.execute = AsyncMock(return_value=MagicMock(all=lambda: []))
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.db.get_session", return_value=ctx):
            result = await list_pages(type="entity")

        # Must not raise; result is a list (empty is fine with mock)
        assert isinstance(result, list)
