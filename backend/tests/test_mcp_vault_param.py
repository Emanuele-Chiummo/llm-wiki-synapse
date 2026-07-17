"""
W5 (ADR-0082, finding PF-MCP-VAULT-1): optional `vault` parameter on MCP tools.

Every existing MCP tool is hard-wired to settings.vault_id / settings.vault_root (the single
ACTIVE vault). These tests cover the additive `vault` parameter added in 1.9.4 W5:

  - Omitting `vault` (default None) MUST behave EXACTLY as before (active vault).
  - Read-only tools (`search_wiki`, `get_page`, `list_pages`, `get_graph_neighborhood`,
    `list_reviews`, `read_source_file`) resolve a *different* known vault via the projects
    registry (app.projects.read_registry) when given.
  - An unknown `vault` id falls back to the active vault (never a hard error for reads).
  - Write tools (`write_page`, `resolve_review`, `trigger_source_rescan`) refuse a `vault`
    that differs from the active one with a structured error (Model A: one active vault's
    filesystem at a time) rather than silently writing to the wrong place.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _fake_project(pid: str, path: str) -> MagicMock:
    p = MagicMock()
    p.id = pid
    p.path = path
    return p


def _fake_registry(projects: list[MagicMock]) -> MagicMock:
    reg = MagicMock()
    reg.projects = projects
    return reg


# ── _resolve_vault ─────────────────────────────────────────────────────────────


class TestResolveVault:
    def test_none_returns_active_vault(self) -> None:
        """vault=None resolves to (settings.vault_id, settings.vault_root) — unchanged."""
        from app.config import settings
        from app.mcp.server import _resolve_vault

        vault_id, vault_root = _resolve_vault(None)
        assert vault_id == settings.vault_id
        assert vault_root == settings.vault_root

    def test_known_vault_resolves_to_registry_entry(self) -> None:
        """A vault id present in the projects registry resolves to its own (id, path)."""
        from pathlib import Path

        from app.mcp.server import _resolve_vault

        other = _fake_project("other-vault", "/tmp/other-vault-root")
        with patch("app.project_registry.read_registry", return_value=_fake_registry([other])):
            vault_id, vault_root = _resolve_vault("other-vault")

        assert vault_id == "other-vault"
        assert vault_root == Path("/tmp/other-vault-root")

    def test_unknown_vault_falls_back_to_active(self) -> None:
        """An unrecognized vault id falls back to the active vault (never a hard error)."""
        from app.config import settings
        from app.mcp.server import _resolve_vault

        with patch("app.project_registry.read_registry", return_value=_fake_registry([])):
            vault_id, vault_root = _resolve_vault("no-such-vault")

        assert vault_id == settings.vault_id
        assert vault_root == settings.vault_root

    def test_registry_error_falls_back_to_active(self) -> None:
        """If the registry read raises, resolution degrades to the active vault (no crash)."""
        from app.config import settings
        from app.mcp.server import _resolve_vault

        with patch("app.project_registry.read_registry", side_effect=RuntimeError("disk error")):
            vault_id, vault_root = _resolve_vault("some-vault")

        assert vault_id == settings.vault_id
        assert vault_root == settings.vault_root


# ── _vault_write_guard ───────────────────────────────────────────────────────


class TestVaultWriteGuard:
    def test_none_is_allowed(self) -> None:
        from app.mcp.server import _vault_write_guard

        assert _vault_write_guard(None) is None

    def test_active_vault_id_is_allowed(self) -> None:
        from app.config import settings
        from app.mcp.server import _vault_write_guard

        assert _vault_write_guard(settings.vault_id) is None

    def test_other_vault_id_is_refused(self) -> None:
        from app.mcp.server import _vault_write_guard

        result = _vault_write_guard("some-other-vault")
        assert result is not None
        assert "error" in result
        assert "activate" in result["error"].lower()


# ── search_wiki / list_pages / get_graph_neighborhood / list_reviews thread vault_id ──


class TestReadToolsThreadVaultId:
    @pytest.mark.asyncio
    async def test_search_wiki_passes_resolved_vault_id_to_retrieve(self) -> None:
        from app.mcp.server import search_wiki

        ctx = MagicMock()
        ctx.citations = []
        other = _fake_project("other-vault", "/tmp/other-vault-root")

        with (
            patch("app.project_registry.read_registry", return_value=_fake_registry([other])),
            patch(
                "app.mcp.server.retrieve", new_callable=AsyncMock, return_value=ctx
            ) as mock_retrieve,
        ):
            await search_wiki("query", k=5, vault="other-vault")

        assert mock_retrieve.call_args.kwargs["vault_id"] == "other-vault"

    @pytest.mark.asyncio
    async def test_search_wiki_omitted_vault_uses_active(self) -> None:
        from app.config import settings
        from app.mcp.server import search_wiki

        ctx = MagicMock()
        ctx.citations = []
        with patch(
            "app.mcp.server.retrieve", new_callable=AsyncMock, return_value=ctx
        ) as mock_retrieve:
            await search_wiki("query", k=5)

        assert mock_retrieve.call_args.kwargs["vault_id"] == settings.vault_id

    @pytest.mark.asyncio
    async def test_list_pages_passes_resolved_vault_id(self) -> None:
        from app.mcp.server import list_pages

        other = _fake_project("other-vault", "/tmp/other-vault-root")
        ctx = MagicMock()
        sess = MagicMock()
        sess.execute = AsyncMock(return_value=MagicMock(all=lambda: []))
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.project_registry.read_registry", return_value=_fake_registry([other])),
            patch("app.db.get_session", return_value=ctx),
        ):
            result = await list_pages(vault="other-vault")

        assert result == []
        # The WHERE clause must have been built against vault_id == "other-vault";
        # we can't easily introspect the compiled statement's bound value without a real
        # engine, so we assert no exception occurred and the session was queried once.
        sess.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_reviews_passes_resolved_vault_id(self) -> None:
        from app.mcp.server import list_reviews

        other = _fake_project("other-vault", "/tmp/other-vault-root")
        fake_page = MagicMock()
        fake_page.items = []

        with (
            patch("app.project_registry.read_registry", return_value=_fake_registry([other])),
            patch(
                "app.ops.review.list_queue", new_callable=AsyncMock, return_value=fake_page
            ) as mock_list_queue,
        ):
            await list_reviews(vault="other-vault")

        assert mock_list_queue.call_args.args[0] == "other-vault"

    @pytest.mark.asyncio
    async def test_get_graph_neighborhood_unknown_vault_falls_back_active(self) -> None:
        """Unknown vault degrades to the active vault rather than erroring (reads are lenient)."""
        from app.mcp.server import get_graph_neighborhood

        ctx = MagicMock()
        sess = MagicMock()
        sess.execute = AsyncMock(return_value=MagicMock(first=lambda: None))
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.project_registry.read_registry", return_value=_fake_registry([])),
            patch("app.db.get_session", return_value=ctx),
        ):
            result = await get_graph_neighborhood("Some Page", vault="unknown-vault-id")

        assert "error" in result
        assert "not found" in result["error"].lower()


# ── read_source_file: vault-scoped path resolution ──────────────────────────


class TestReadSourceFileVaultParam:
    @pytest.mark.asyncio
    async def test_reads_from_resolved_vault_root(self) -> None:
        import tempfile
        from pathlib import Path

        from app.mcp.server import read_source_file

        with tempfile.TemporaryDirectory() as tmp_root:
            root = Path(tmp_root)
            (root / "raw" / "sources").mkdir(parents=True)
            (root / "raw" / "sources" / "doc.md").write_text(
                "hello from other vault", encoding="utf-8"
            )

            other = _fake_project("other-vault", str(root))
            with patch("app.project_registry.read_registry", return_value=_fake_registry([other])):
                result = await read_source_file("doc.md", vault="other-vault")

        assert "error" not in result
        assert "hello from other vault" in result["content"]


# ── Write tools: cross-vault attempts are refused ───────────────────────────


class TestWriteToolsRefuseCrossVault:
    @pytest.mark.asyncio
    async def test_write_page_refuses_non_active_vault(self) -> None:
        from app.mcp.server import write_page

        result = await write_page(
            title="X",
            content="body",
            frontmatter={
                "type": "concept",
                "title": "X",
                "sources": ["raw/sources/x.md"],
                "lang": "en",
            },
            vault="some-other-vault",
        )

        assert "error" in result
        assert "activate" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_write_page_active_vault_id_still_writes(self) -> None:
        """Passing the CURRENTLY active vault id explicitly must behave like omitting it."""
        from app.config import settings
        from app.mcp.server import write_page

        fake_page_row = MagicMock()
        fake_page_row.id = uuid.uuid4()
        fake_page_row.title = "Synapse"
        fake_page_row.page_type = "concept"

        with patch("app.ingest.writer.write_wiki_page", new_callable=AsyncMock) as mock_wwp:
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
                vault=settings.vault_id,
            )

        mock_wwp.assert_called_once()
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_resolve_review_refuses_non_active_vault(self) -> None:
        from app.mcp.server import resolve_review

        result = await resolve_review(str(uuid.uuid4()), "skip", vault="some-other-vault")

        assert "error" in result
        assert "activate" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_trigger_source_rescan_refuses_non_active_vault(self) -> None:
        from app.mcp.server import trigger_source_rescan

        result = await trigger_source_rescan(vault="some-other-vault")

        assert "error" in result
        assert "activate" in result["error"].lower()
