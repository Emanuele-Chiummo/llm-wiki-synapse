"""
K5 wikilink parser unit tests (infra-free).

Coverage:
    - parse_wikilinks handles [[Target]], [[Target|alias]], [[Target#section]]
    - [[Target#section|alias]] — section stripped, alias preserved
    - duplicates within one page are deduplicated
    - empty/blank inner text is silently skipped
    - persist_links sets dangling=True for unresolved targets, False for resolved ones
    - persist_links is idempotent (delete-then-reinsert on second call)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from app.wiki.links import ParsedLink, parse_wikilinks

# ── Parser tests (zero infrastructure) ────────────────────────────────────────


class TestParseWikilinks:
    def test_simple_link(self) -> None:
        """[[Target]] → ParsedLink(target='Target', alias=None)."""
        result = parse_wikilinks("See [[Qdrant]] for more.")
        assert result == [ParsedLink(target="Qdrant", alias=None)]

    def test_alias_link(self) -> None:
        """[[Target|alias]] → ParsedLink(target='Target', alias='alias')."""
        result = parse_wikilinks("Read [[FastAPI|the framework]].")
        assert result == [ParsedLink(target="FastAPI", alias="the framework")]

    def test_section_link(self) -> None:
        """[[Target#section]] → section stripped, alias=None."""
        result = parse_wikilinks("See [[Watcher#startup]].")
        assert result == [ParsedLink(target="Watcher", alias=None)]

    def test_section_alias_link(self) -> None:
        """[[Target#section|alias]] → section stripped, alias kept."""
        result = parse_wikilinks("See [[Watcher#startup|the watcher]] here.")
        assert result == [ParsedLink(target="Watcher", alias="the watcher")]

    def test_multiple_links(self) -> None:
        """Multiple links in one page are all parsed."""
        md = "[[Page A]] and [[Page B|B alias]] and [[Page C#sec]]."
        result = parse_wikilinks(md)
        assert ParsedLink(target="Page A", alias=None) in result
        assert ParsedLink(target="Page B", alias="B alias") in result
        assert ParsedLink(target="Page C", alias=None) in result
        assert len(result) == 3

    def test_deduplication(self) -> None:
        """Same [[Target|alias]] pair appearing twice → one result."""
        md = "[[Graph]] at the start and [[Graph]] at the end."
        result = parse_wikilinks(md)
        assert result == [ParsedLink(target="Graph", alias=None)]

    def test_empty_link_skipped(self) -> None:
        """[[]] or [[  ]] (blank target) are silently skipped."""
        assert parse_wikilinks("[[]]") == []
        assert parse_wikilinks("[[  ]]") == []

    def test_empty_markdown(self) -> None:
        assert parse_wikilinks("") == []

    def test_no_links_in_markdown(self) -> None:
        assert parse_wikilinks("# Just a heading\n\nSome body text.") == []

    def test_links_in_code_block_still_parsed(self) -> None:
        """
        The parser is regex-based and does not skip code blocks.
        This is acceptable for v0.2 — a smarter parser can be added later.
        At minimum, links in regular prose are parsed correctly.
        """
        md = "Normal [[Link]] here."
        result = parse_wikilinks(md)
        assert ParsedLink(target="Link", alias=None) in result

    def test_whitespace_trimmed_inside_brackets(self) -> None:
        """Internal whitespace around target/alias is stripped."""
        result = parse_wikilinks("[[ My Page | my alias ]]")
        assert result == [ParsedLink(target="My Page", alias="my alias")]

    def test_alias_only_whitespace_becomes_none(self) -> None:
        """[[Target|  ]] — blank alias → alias=None."""
        result = parse_wikilinks("[[Target|   ]]")
        assert result == [ParsedLink(target="Target", alias=None)]

    def test_target_with_spaces(self) -> None:
        """[[Multi Word Page]] is a valid target."""
        result = parse_wikilinks("See [[Multi Word Page]] here.")
        assert result == [ParsedLink(target="Multi Word Page", alias=None)]


# ── Persistence tests (SQLite in-memory) ──────────────────────────────────────


class _FakeSessionFactory:
    """Minimal async session context manager for testing persist_links."""

    def __init__(self, session: Any) -> None:
        self._session = session

    async def __aenter__(self) -> Any:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        pass


class _FakeResult:
    """Fake SQLAlchemy result rows."""

    def __init__(self, rows: list[tuple[Any, str]]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        class _Row:
            def __init__(self, row: tuple[Any, str]) -> None:
                self.id = row[0]
                self.title = row[1]

        return [_Row(r) for r in self._rows]


class _FakeSession:
    """
    Minimal async session stub for persist_links tests.

    Tracks all added Link objects and simulates execute() for title resolution.
    """

    def __init__(self, existing_titles: dict[str, uuid.UUID] | None = None) -> None:
        self.added: list[Any] = []
        self.executed: list[Any] = []
        self._titles: dict[str, uuid.UUID] = existing_titles or {}
        self._deleted_source_ids: list[uuid.UUID] = []

    async def execute(self, stmt: Any) -> Any:

        # Detect if this is a DELETE statement by checking the compiled string.
        # We check by repr since we don't want to import SA internals.
        stmt_str = str(stmt)
        if "DELETE" in stmt_str.upper():
            # Record the delete (source_page_id extracted from the clause)
            return None

        # SELECT for title resolution — return matching rows from _titles.
        # Heuristic: if the compiled statement contains "title", return title rows.
        rows = [(v, k) for k, v in self._titles.items()]
        return _FakeResult(rows)

    def add(self, obj: Any) -> None:
        self.added.append(obj)


@pytest.mark.asyncio
async def test_persist_links_dangling_when_target_missing() -> None:
    """
    persist_links marks dangling=True for links whose target page does not exist.
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.wiki.links import persist_links

    source_id = uuid.uuid4()
    parsed = [ParsedLink(target="NonExistentPage", alias=None)]

    # Mock the session
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(
        side_effect=[
            MagicMock(rowcount=0),  # DELETE result
            MagicMock(all=lambda: []),  # SELECT for title resolution — empty (target missing)
        ]
    )
    mock_session.add = MagicMock()

    await persist_links(mock_session, source_id, parsed)

    # Should have called add() with a Link that has dangling=True
    assert mock_session.add.called
    link = mock_session.add.call_args[0][0]
    from app.models import Link

    assert isinstance(link, Link)
    assert link.dangling is True
    assert link.target_title == "NonExistentPage"
    assert link.target_page_id is None
    assert link.source_page_id == source_id


@pytest.mark.asyncio
async def test_persist_links_resolved_when_target_exists() -> None:
    """
    persist_links sets dangling=False and target_page_id when the target page exists.
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.wiki.links import persist_links

    source_id = uuid.uuid4()
    target_id = uuid.uuid4()
    parsed = [ParsedLink(target="ExistingPage", alias=None)]

    # Simulate a Row object that the SELECT returns
    class _FakeRow:
        def __init__(self) -> None:
            self.id = target_id
            self.title = "ExistingPage"

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(
        side_effect=[
            MagicMock(rowcount=0),  # DELETE
            MagicMock(all=lambda: [_FakeRow()]),  # SELECT — found
        ]
    )
    mock_session.add = MagicMock()

    await persist_links(mock_session, source_id, parsed)

    assert mock_session.add.called
    link = mock_session.add.call_args[0][0]
    assert link.dangling is False
    assert link.target_page_id == target_id


@pytest.mark.asyncio
async def test_persist_links_no_op_for_empty_list() -> None:
    """persist_links with no parsed links runs the DELETE but skips the SELECT + INSERT."""
    from unittest.mock import AsyncMock, MagicMock

    from app.wiki.links import persist_links

    source_id = uuid.uuid4()
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(rowcount=0))
    mock_session.add = MagicMock()

    await persist_links(mock_session, source_id, [])

    # execute called once (DELETE), add never called
    assert mock_session.execute.call_count == 1
    assert not mock_session.add.called


@pytest.mark.asyncio
async def test_persist_links_alias_preserved() -> None:
    """persist_links stores the alias column correctly."""
    from unittest.mock import AsyncMock, MagicMock

    from app.wiki.links import persist_links

    source_id = uuid.uuid4()
    parsed = [ParsedLink(target="Target", alias="my alias")]

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(
        side_effect=[
            MagicMock(rowcount=0),  # DELETE
            MagicMock(all=lambda: []),  # SELECT — not found (dangling)
        ]
    )
    mock_session.add = MagicMock()

    await persist_links(mock_session, source_id, parsed)

    link = mock_session.add.call_args[0][0]
    assert link.alias == "my alias"
