"""
K3 index.md catalogue updater tests (infra-free / SQLite stub).

Coverage:
    - update_index generates valid frontmatter (I5)
    - update_index groups pages by type and renders [[wikilinks]] (K3, K5)
    - update_index is idempotent (same DB state → same content)
    - Pages with excluded types (overview, index) are excluded from the catalogue
    - Pages without a title fall back to the file stem
    - Empty vault (no live pages) produces a valid minimal index.md
    - ADR-0067 D6 IL-D4: 'query' type produces '## Queries' (not '## Querys')
    - ADR-0067 D6 IL-D3: NULL-type rows produce no '## Uncategorised' section
    - ADR-0067 D6 IL-D2/CE-D5: duplicate display titles are collapsed to a single line
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_row(
    title: str | None,
    page_type: str | None,
    file_path: str,
) -> Any:
    """Build a fake SQLAlchemy row-like object."""

    class _Row:
        pass

    r = _Row()
    r.title = title  # type: ignore[attr-defined]
    r.page_type = page_type  # type: ignore[attr-defined]
    r.file_path = file_path  # type: ignore[attr-defined]
    return r


async def _run_update_index(rows: list[Any], tmp_path: Path) -> str:
    """Run update_index with a mocked session and return the written file content."""
    from app.wiki.index import update_index

    mock_result = MagicMock()
    mock_result.all.return_value = [(r.title, r.page_type, r.file_path) for r in rows]

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    vault_path = tmp_path / "vault"
    vault_path.mkdir(parents=True, exist_ok=True)
    await update_index(mock_session, vault_path)

    index_path = vault_path / "wiki" / "index.md"
    assert index_path.exists(), "index.md must be created"
    return index_path.read_text(encoding="utf-8")


class TestIndexMdFrontmatter:
    @pytest.mark.asyncio
    async def test_valid_frontmatter_header(self, tmp_path: Path) -> None:
        """index.md must start with a valid YAML frontmatter block (I5, K7)."""
        content = await _run_update_index([], tmp_path)
        assert content.startswith("---\n"), "Must start with frontmatter block"
        assert "type: index" in content, "Must have type: index in frontmatter"
        assert "title:" in content, "Must have title in frontmatter"
        assert "---" in content, "Must close frontmatter block"

    @pytest.mark.asyncio
    async def test_auto_generated_flag_in_frontmatter(self, tmp_path: Path) -> None:
        """frontmatter must include auto_generated: true."""
        content = await _run_update_index([], tmp_path)
        assert "auto_generated: true" in content


class TestIndexMdCatalogue:
    @pytest.mark.asyncio
    async def test_pages_grouped_by_type(self, tmp_path: Path) -> None:
        """Pages are grouped under type headings with [[wikilinks]]."""
        rows = [
            _make_row("Qdrant", "concept", "wiki/concepts/qdrant.md"),
            _make_row("Alice", "entity", "wiki/entities/alice.md"),
            _make_row("My Paper", "source", "wiki/sources/my-paper.md"),
        ]
        content = await _run_update_index(rows, tmp_path)

        assert "## Entities" in content
        assert "## Concepts" in content
        assert "## Sources" in content
        assert "[[Alice]]" in content
        assert "[[Qdrant]]" in content
        assert "[[My Paper]]" in content

    @pytest.mark.asyncio
    async def test_overview_and_index_types_excluded(self, tmp_path: Path) -> None:
        """Pages with type 'overview' or 'index' are excluded from the catalogue."""
        rows = [
            _make_row("Overview", "overview", "wiki/overview.md"),
            _make_row("Index", "index", "wiki/index.md"),
            _make_row("Real Page", "concept", "wiki/concepts/real.md"),
        ]
        content = await _run_update_index(rows, tmp_path)

        assert "[[Overview]]" not in content
        assert "[[Index]]" not in content
        assert "[[Real Page]]" in content

    @pytest.mark.asyncio
    async def test_null_title_falls_back_to_file_stem(self, tmp_path: Path) -> None:
        """Pages with title=None fall back to the filename stem."""
        rows = [_make_row(None, "concept", "wiki/concepts/my-page.md")]
        content = await _run_update_index(rows, tmp_path)

        assert "[[my-page]]" in content

    @pytest.mark.asyncio
    async def test_empty_vault_produces_valid_index(self, tmp_path: Path) -> None:
        """An empty vault (no live pages) still produces a valid index.md."""
        content = await _run_update_index([], tmp_path)
        assert "type: index" in content
        assert "Total pages:" in content

    @pytest.mark.asyncio
    async def test_total_pages_count_correct(self, tmp_path: Path) -> None:
        """Total pages count in the index reflects all non-excluded pages."""
        rows = [
            _make_row("A", "entity", "wiki/entities/a.md"),
            _make_row("B", "concept", "wiki/concepts/b.md"),
            _make_row("OV", "overview", "wiki/overview.md"),  # excluded
        ]
        content = await _run_update_index(rows, tmp_path)
        # 2 user-content pages (OV is excluded)
        assert "**Total pages:** 2" in content


class TestIndexMdAdr0067D6:
    """Regression tests for ADR-0067 D6 catalogue fixes (IL-D1..D4)."""

    @pytest.mark.asyncio
    async def test_query_type_heading_is_queries_not_querys(self, tmp_path: Path) -> None:
        """
        IL-D4: 'query' page_type must produce '## Queries', never '## Querys'.

        Before ADR-0067 D6, 'query' was missing from _TYPE_ORDER and _PLURAL_EXCEPTIONS
        so the generic naïve pluraliser appended 's' → 'Querys'. This regression test
        locks in the correct heading.
        """
        rows = [
            _make_row("How does X work?", "query", "wiki/queries/how-does-x-work.md"),
        ]
        content = await _run_update_index(rows, tmp_path)

        assert "## Queries" in content, "heading must be '## Queries'"
        assert "## Querys" not in content, "'## Querys' must never appear"
        assert "[[How does X work?]]" in content

    @pytest.mark.asyncio
    async def test_null_type_produces_no_uncategorised_section(self, tmp_path: Path) -> None:
        """
        IL-D3: pages with NULL page_type are silently dropped — no '## Uncategorised'.

        Ghost rows (unresolved stubs with no type) must not pollute the catalogue.
        """
        rows = [
            _make_row("Ghost Page", None, "wiki/concepts/ghost.md"),
            _make_row("Real Entity", "entity", "wiki/entities/real.md"),
        ]
        content = await _run_update_index(rows, tmp_path)

        assert "## Uncategorised" not in content, "'## Uncategorised' must not appear"
        assert "[[Ghost Page]]" not in content, "NULL-type page must not be listed"
        assert "[[Real Entity]]" in content, "typed page must still be listed"

    @pytest.mark.asyncio
    async def test_empty_string_type_produces_no_uncategorised_section(
        self, tmp_path: Path
    ) -> None:
        """
        IL-D3 (empty-string variant): pages with empty-string page_type are also dropped.
        """
        rows = [
            _make_row("Empty Type", "", "wiki/concepts/empty-type.md"),
            _make_row("Real Concept", "concept", "wiki/concepts/real.md"),
        ]
        content = await _run_update_index(rows, tmp_path)

        assert "## Uncategorised" not in content
        assert "[[Empty Type]]" not in content
        assert "[[Real Concept]]" in content

    @pytest.mark.asyncio
    async def test_duplicate_titles_collapsed_to_single_entry(self, tmp_path: Path) -> None:
        """
        IL-D2/CE-D5: two rows with the same case-insensitive display title in the same
        type section must render as a single '- [[…]]' line (first occurrence wins).
        """
        rows = [
            _make_row("AWS", "entity", "wiki/entities/aws.md"),
            _make_row("aws", "entity", "wiki/entities/aws-alias.md"),
        ]
        content = await _run_update_index(rows, tmp_path)

        # Only one wikilink for the case-insensitive duplicate pair.
        assert content.count("[[AWS]]") + content.count("[[aws]]") == 1, (
            "duplicate case-insensitive titles must collapse to a single catalogue entry"
        )

    @pytest.mark.asyncio
    async def test_duplicate_titles_cross_type_both_rendered(self, tmp_path: Path) -> None:
        """
        IL-D2: dedup is scoped per type section; the same title in different types
        must appear once per type (they are distinct pages).
        """
        rows = [
            _make_row("Cloud", "concept", "wiki/concepts/cloud.md"),
            _make_row("Cloud", "entity", "wiki/entities/cloud.md"),
        ]
        content = await _run_update_index(rows, tmp_path)

        assert content.count("[[Cloud]]") == 2, (
            "same title in different type sections must each render once"
        )

    @pytest.mark.asyncio
    async def test_total_count_reflects_deduplicated_set(self, tmp_path: Path) -> None:
        """
        After dedup, **Total pages:** must reflect the visible (deduplicated) count,
        not the raw row count.
        """
        rows = [
            _make_row("AWS", "entity", "wiki/entities/aws.md"),
            _make_row("aws", "entity", "wiki/entities/aws-alias.md"),  # duplicate → dropped
            _make_row("Google", "entity", "wiki/entities/google.md"),
        ]
        content = await _run_update_index(rows, tmp_path)

        assert "**Total pages:** 2" in content, (
            "total must be 2 after dedup collapses AWS/aws to a single entry"
        )

    @pytest.mark.asyncio
    async def test_null_type_not_counted_in_total(self, tmp_path: Path) -> None:
        """
        IL-D3: NULL-type ghost rows must not increment the **Total pages:** counter.
        """
        rows = [
            _make_row("Ghost", None, "wiki/concepts/ghost.md"),
            _make_row("Real", "concept", "wiki/concepts/real.md"),
        ]
        content = await _run_update_index(rows, tmp_path)

        assert "**Total pages:** 1" in content, (
            "NULL-type ghost must not be counted; only 1 real page exists"
        )


class TestIndexMdIdempotency:
    @pytest.mark.asyncio
    async def test_idempotent_same_state(self, tmp_path: Path) -> None:
        """
        Running update_index twice with the same DB rows produces the same index.md.

        The only non-deterministic part is the timestamp; we check the structure is
        identical by comparing the page-listing lines (not the timestamp line).
        """
        rows = [
            _make_row("Alice", "entity", "wiki/entities/alice.md"),
            _make_row("Qdrant", "concept", "wiki/concepts/qdrant.md"),
        ]
        content1 = await _run_update_index(rows, tmp_path / "run1")
        content2 = await _run_update_index(rows, tmp_path / "run2")

        # Extract non-timestamp lines for comparison
        def _body_lines(c: str) -> list[str]:
            return [line for line in c.splitlines() if not line.startswith("*Last updated:")]

        assert _body_lines(content1) == _body_lines(content2)

    @pytest.mark.asyncio
    async def test_overwrites_previous_index(self, tmp_path: Path) -> None:
        """Second call with different page set overwrites the first index.md."""
        rows1 = [_make_row("Page A", "entity", "wiki/entities/a.md")]
        rows2 = [_make_row("Page B", "concept", "wiki/concepts/b.md")]

        vault_path = tmp_path / "vault"
        vault_path.mkdir()

        from unittest.mock import AsyncMock, MagicMock

        from app.wiki.index import update_index

        async def _run(rows: list[Any]) -> str:
            mock_result = MagicMock()
            mock_result.all.return_value = [(r.title, r.page_type, r.file_path) for r in rows]
            mock_session = MagicMock()
            mock_session.execute = AsyncMock(return_value=mock_result)
            await update_index(mock_session, vault_path)
            return (vault_path / "wiki" / "index.md").read_text(encoding="utf-8")

        c1 = await _run(rows1)
        c2 = await _run(rows2)

        assert "[[Page A]]" in c1
        assert "[[Page A]]" not in c2
        assert "[[Page B]]" in c2
