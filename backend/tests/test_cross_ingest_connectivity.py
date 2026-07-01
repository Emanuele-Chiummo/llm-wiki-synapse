"""
F3 / K3 — cross-ingest graph connectivity tests.

Problem being fixed: each ingest produced an isolated graph island because the ingest LLM did
not know which pages already existed, so it invented new titles → [[wikilinks]] did not match
existing page titles → links stayed dangling (no edge). nashsu/llm_wiki avoids this by feeding
the existing index catalogue to the LLM so it links to existing pages → one connected web.

Coverage:
    Part 1 — _load_existing_pages_catalogue:
        - excludes index/log/overview page types + raw/sources/ pages
        - groups titles by page_type
        - respects the title cap (truncates + warns)
        - contains the "LINK TO THESE" + exact [[wikilink]] instruction
    Part 2 — tolerant resolution (_resolve_target / persist_links):
        - exact, case-insensitive, and slug matches each resolve to the right page
        - a genuine non-match stays dangling
        - precedence is exact-first
    Part 3 — reresolve_dangling_links backfill:
        - a dangling link whose title now matches (case/slug variant) is reconnected
        - a truly-dangling link stays dangling
        - the reconnected count is returned

These tests are infra-free: they use lightweight fakes for the AsyncSession (matching the
convention in test_wikilink_parser.py), so they run without Postgres/Qdrant/Ollama.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Shared fakes ─────────────────────────────────────────────────────────────────


@dataclass
class _Row:
    """Row exposing attribute access (mimics a SQLAlchemy Row for the resolver maps)."""

    id: uuid.UUID
    title: str | None


class _MapResult:
    """Result of the resolver-maps SELECT: .all() → list of _Row."""

    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def all(self) -> list[_Row]:
        return self._rows


# ── Part 1: existing-pages catalogue ─────────────────────────────────────────────


class _CatalogueRow:
    """(title, page_type) row for the catalogue SELECT."""

    def __init__(self, title: str, page_type: str) -> None:
        self.title = title
        self.page_type = page_type
        # _load_existing_pages_catalogue reads rows via `for title, page_type in rows`
        # → must be tuple-iterable.

    def __iter__(self) -> Any:
        return iter((self.title, self.page_type))


class _CatalogueResult:
    def __init__(self, rows: list[_CatalogueRow]) -> None:
        self._rows = rows

    def all(self) -> list[_CatalogueRow]:
        return self._rows


class _CatalogueSession:
    def __init__(self, rows: list[_CatalogueRow]) -> None:
        self._rows = rows

    async def execute(self, _stmt: Any) -> _CatalogueResult:
        return _CatalogueResult(self._rows)

    async def __aenter__(self) -> _CatalogueSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _patch_catalogue_session(monkeypatch: pytest.MonkeyPatch, rows: list[_CatalogueRow]) -> None:
    import app.ingest.orchestrator as orch

    def _factory() -> _CatalogueSession:
        return _CatalogueSession(rows)

    monkeypatch.setattr(orch, "get_session", _factory)


class TestCatalogue:
    @pytest.mark.asyncio
    async def test_contains_link_instruction(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The catalogue carries the 'LINK TO THESE' + exact [[wikilink]] instruction."""
        from app.ingest.orchestrator import _load_existing_pages_catalogue

        _patch_catalogue_session(
            monkeypatch,
            [_CatalogueRow("Retrieval-Augmented Generation", "concept")],
        )
        cat = await _load_existing_pages_catalogue()

        assert "Existing wiki pages — LINK TO THESE" in cat
        assert "EXACT title" in cat
        assert "[[wikilink]]" in cat
        assert "Retrieval-Augmented Generation" in cat

    @pytest.mark.asyncio
    async def test_groups_by_type(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Titles are grouped under a '## <page_type>' section header."""
        from app.ingest.orchestrator import _load_existing_pages_catalogue

        _patch_catalogue_session(
            monkeypatch,
            [
                _CatalogueRow("Knowledge Graph", "concept"),
                _CatalogueRow("Andrej Karpathy", "entity"),
            ],
        )
        cat = await _load_existing_pages_catalogue()

        assert "## concept" in cat
        assert "## entity" in cat
        # Titles appear as bullet items under their group.
        assert "- Knowledge Graph" in cat
        assert "- Andrej Karpathy" in cat

    @pytest.mark.asyncio
    async def test_empty_when_no_pages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No linkable pages yet → empty string (first-ever ingest)."""
        from app.ingest.orchestrator import _load_existing_pages_catalogue

        _patch_catalogue_session(monkeypatch, [])
        assert await _load_existing_pages_catalogue() == ""

    @pytest.mark.asyncio
    async def test_respects_title_cap_and_warns(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Over the title cap → truncated subset + truncation note + WARNING log (I7)."""
        import logging

        from app.ingest.orchestrator import (
            _CATALOGUE_MAX_TITLES,
            _load_existing_pages_catalogue,
        )

        over = _CATALOGUE_MAX_TITLES + 25
        rows = [_CatalogueRow(f"Concept {i:04d}", "concept") for i in range(over)]
        _patch_catalogue_session(monkeypatch, rows)

        with caplog.at_level(logging.WARNING):
            cat = await _load_existing_pages_catalogue()

        # Truncation note is present and mentions the true total.
        assert "catalogue truncated" in cat
        assert str(over) in cat
        # At most the cap number of bullet lines survive.
        bullet_count = cat.count("\n- ")
        assert bullet_count <= _CATALOGUE_MAX_TITLES
        # A WARNING was emitted (no silent truncation).
        assert any("truncated" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_excludes_meta_and_raw_sources_via_query_filter(self) -> None:
        """
        The SELECT filters out index/log/overview page types and raw/sources/ file paths.

        We assert the WHERE clause the helper builds so the exclusion is verified at the query
        layer (the DB never returns those rows to the grouping code).
        """
        from app.ingest.orchestrator import _CATALOGUE_EXCLUDED_TYPES

        assert "index" in _CATALOGUE_EXCLUDED_TYPES
        assert "log" in _CATALOGUE_EXCLUDED_TYPES
        assert "overview" in _CATALOGUE_EXCLUDED_TYPES

        # Capture the compiled WHERE clause of the SELECT the helper issues.
        captured: dict[str, str] = {}

        class _CapSession:
            async def execute(self, stmt: Any) -> _CatalogueResult:
                captured["sql"] = str(stmt)
                return _CatalogueResult([])

            async def __aenter__(self) -> _CapSession:
                return self

            async def __aexit__(self, *exc: Any) -> None:
                return None

        import app.ingest.orchestrator as orch

        orig = orch.get_session
        orch.get_session = lambda: _CapSession()  # type: ignore[assignment]
        try:
            from app.ingest.orchestrator import _load_existing_pages_catalogue

            await _load_existing_pages_catalogue()
        finally:
            orch.get_session = orig  # type: ignore[assignment]

        sql = captured["sql"].lower()
        # raw/sources/ exclusion (NOT LIKE) and the page_type NOT IN filter are in the SQL.
        assert "not like" in sql
        assert "not in" in sql
        assert "deleted_at is null" in sql


# ── Part 2: tolerant resolution ──────────────────────────────────────────────────


def _persist_session(live_pages: list[_Row]) -> Any:
    """
    Fake AsyncSession for persist_links: first execute() is the DELETE, second is the
    resolver-maps SELECT (returns live_pages).
    """
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            MagicMock(rowcount=0),  # DELETE
            _MapResult(live_pages),  # resolver maps SELECT
        ]
    )
    session.add = MagicMock()
    return session


class TestTolerantResolution:
    @pytest.mark.asyncio
    async def test_exact_match(self) -> None:
        from app.wiki.links import ParsedLink, persist_links

        pid = uuid.uuid4()
        session = _persist_session([_Row(pid, "Knowledge Graph")])
        await persist_links(session, uuid.uuid4(), [ParsedLink("Knowledge Graph", None)])

        link = session.add.call_args[0][0]
        assert link.dangling is False
        assert link.target_page_id == pid

    @pytest.mark.asyncio
    async def test_case_insensitive_match(self) -> None:
        from app.wiki.links import ParsedLink, persist_links

        pid = uuid.uuid4()
        session = _persist_session([_Row(pid, "Knowledge Graph")])
        # LLM wrote lowercase.
        await persist_links(session, uuid.uuid4(), [ParsedLink("knowledge graph", None)])

        link = session.add.call_args[0][0]
        assert link.dangling is False
        assert link.target_page_id == pid

    @pytest.mark.asyncio
    async def test_slug_match(self) -> None:
        from app.wiki.links import ParsedLink, persist_links

        pid = uuid.uuid4()
        session = _persist_session([_Row(pid, "Retrieval-Augmented Generation")])
        # LLM wrote a spacing/punctuation variant that slugifies the same.
        await persist_links(
            session, uuid.uuid4(), [ParsedLink("Retrieval Augmented Generation", None)]
        )

        link = session.add.call_args[0][0]
        assert link.dangling is False
        assert link.target_page_id == pid

    @pytest.mark.asyncio
    async def test_genuine_non_match_stays_dangling(self) -> None:
        from app.wiki.links import ParsedLink, persist_links

        session = _persist_session([_Row(uuid.uuid4(), "Knowledge Graph")])
        await persist_links(session, uuid.uuid4(), [ParsedLink("Something Unrelated", None)])

        link = session.add.call_args[0][0]
        assert link.dangling is True
        assert link.target_page_id is None

    def test_precedence_is_exact_first(self) -> None:
        """When an exact title exists, it wins over a case/slug near-miss of another page."""
        from app.wiki.links import _resolve_target, _ResolverMaps

        exact_id = uuid.uuid4()
        other_id = uuid.uuid4()
        maps = _ResolverMaps(
            by_title={"RAG": exact_id},
            by_lower={"rag": exact_id, "other": other_id},
            by_slug={"rag": exact_id, "other": other_id},
        )
        # Exact "RAG" resolves to the exact page, not any lossy fallback.
        assert _resolve_target("RAG", maps) == exact_id


# ── Part 3: dangling-link backfill ───────────────────────────────────────────────


class _Link:
    """Minimal Link stand-in for the backfill (matches the columns the function touches)."""

    def __init__(self, target_title: str) -> None:
        self.target_title = target_title
        self.target_page_id: uuid.UUID | None = None
        self.dangling = True


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def all(self) -> list[Any]:
        return self._items


class _BackfillResult:
    """Result that supports both .scalars().all() (dangling links) and .all() (maps rows)."""

    def __init__(self, *, links: list[_Link] | None = None, rows: list[_Row] | None = None) -> None:
        self._links = links
        self._rows = rows

    def scalars(self) -> _ScalarResult:
        assert self._links is not None
        return _ScalarResult(self._links)

    def all(self) -> list[_Row]:
        assert self._rows is not None
        return self._rows


def _backfill_session(dangling: list[_Link], live_pages: list[_Row]) -> Any:
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[
            _BackfillResult(links=dangling),  # SELECT dangling links
            _BackfillResult(rows=live_pages),  # resolver maps SELECT
        ]
    )
    return session


class TestReresolveBackfill:
    @pytest.mark.asyncio
    async def test_reconnects_case_and_slug_variants(self) -> None:
        from app.wiki.links import reresolve_dangling_links

        pid_a = uuid.uuid4()
        pid_b = uuid.uuid4()
        # Two dangling links whose titles now match live pages via case / slug variants.
        l_case = _Link("knowledge graph")  # matches "Knowledge Graph" case-insensitively
        l_slug = _Link("Retrieval Augmented Generation")  # slug-matches the hyphenated title
        live = [_Row(pid_a, "Knowledge Graph"), _Row(pid_b, "Retrieval-Augmented Generation")]

        session = _backfill_session([l_case, l_slug], live)
        count = await reresolve_dangling_links(session)

        assert count == 2
        assert l_case.dangling is False and l_case.target_page_id == pid_a
        assert l_slug.dangling is False and l_slug.target_page_id == pid_b

    @pytest.mark.asyncio
    async def test_truly_dangling_stays_dangling(self) -> None:
        from app.wiki.links import reresolve_dangling_links

        good = _Link("Knowledge Graph")  # will reconnect
        orphan = _Link("Nonexistent Concept")  # no live page → stays dangling
        live = [_Row(uuid.uuid4(), "Knowledge Graph")]

        session = _backfill_session([good, orphan], live)
        count = await reresolve_dangling_links(session)

        assert count == 1
        assert good.dangling is False
        assert orphan.dangling is True
        assert orphan.target_page_id is None

    @pytest.mark.asyncio
    async def test_no_dangling_returns_zero(self) -> None:
        from app.wiki.links import reresolve_dangling_links

        session = MagicMock()
        session.execute = AsyncMock(return_value=_BackfillResult(links=[]))
        count = await reresolve_dangling_links(session)
        assert count == 0
        # Only the dangling SELECT ran; no resolver-maps query when there is nothing to do.
        assert session.execute.call_count == 1
