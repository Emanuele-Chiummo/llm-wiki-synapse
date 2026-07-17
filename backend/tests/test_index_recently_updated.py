"""
ADR-0078 "## Recently Updated" section tests.

Coverage (all infra-free / no Postgres required):
  RU-01  Section present with entries when live content pages exist.
  RU-02  Format: `- [[<slug>]] — <title>` per entry.
  RU-03  Cap at 200 entries max.
  RU-04  Dedup by filename stem (slug); first occurrence wins.
  RU-05  Excludes index/log/overview types and raw/* paths.
  RU-06  Gracefully handles an empty vault (no section rendered, minimal file produced).
  RU-07  Section appears BEFORE any type catalogue section (prominent placement, llm_wiki §1.8).
  RU-08  Idempotent: same DB state → same file content.
  RU-09  Title fallback to slug when page title is NULL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_row(
    title: str | None,
    page_type: str | None,
    file_path: str,
    updated_at: Any = None,
    summary: str | None = None,
) -> Any:
    class _Row:
        pass

    r = _Row()
    r.title = title  # type: ignore[attr-defined]
    r.page_type = page_type  # type: ignore[attr-defined]
    r.file_path = file_path  # type: ignore[attr-defined]
    r.updated_at = updated_at  # type: ignore[attr-defined]
    r.summary = summary  # type: ignore[attr-defined]
    return r


async def _run_update_index(rows: list[Any], tmp_path: Path) -> str:
    """Run update_index with a mocked session and return the written index.md content."""
    from app.wiki.index import update_index

    mock_result = MagicMock()
    mock_result.all.return_value = [
        (r.title, r.page_type, r.file_path, r.updated_at, r.summary) for r in rows
    ]
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    vault_path = tmp_path / "vault"
    vault_path.mkdir(exist_ok=True)
    await update_index(mock_session, vault_path)
    return (vault_path / "wiki" / "index.md").read_text(encoding="utf-8")


_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


# ── tests ──────────────────────────────────────────────────────────────────────


class TestRecentlyUpdatedSection:
    """RU-01: Section present and RU-02: format check."""

    @pytest.mark.asyncio
    async def test_section_present(self, tmp_path: Path) -> None:
        """RU-01: At least one live content page → "## Recently Updated" rendered."""
        rows = [_make_row("Alice", "entity", "wiki/entities/alice.md", _NOW)]
        content = await _run_update_index(rows, tmp_path)
        assert "## Recently Updated" in content

    @pytest.mark.asyncio
    async def test_entry_format(self, tmp_path: Path) -> None:
        """RU-02: Each entry is `- [[slug]] — display title`."""
        rows = [_make_row("My Concept", "concept", "wiki/concepts/my-concept.md", _NOW)]
        content = await _run_update_index(rows, tmp_path)
        assert "- [[my-concept]] — My Concept" in content

    @pytest.mark.asyncio
    async def test_section_before_catalogue(self, tmp_path: Path) -> None:
        """RU-07: "## Recently Updated" appears before the first type catalogue heading."""
        rows = [
            _make_row("Alpha", "entity", "wiki/entities/alpha.md", _NOW),
        ]
        content = await _run_update_index(rows, tmp_path)
        ru_pos = content.find("## Recently Updated")
        entities_pos = content.find("## Entities")
        assert ru_pos < entities_pos, "'## Recently Updated' must appear before '## Entities'"


class TestRecentlyUpdatedCap:
    """RU-03: Cap at 200 entries."""

    @pytest.mark.asyncio
    async def test_cap_200(self, tmp_path: Path) -> None:
        """More than 200 content pages → exactly 200 entries in the section."""
        rows = [
            _make_row(f"Page {i}", "entity", f"wiki/entities/page-{i}.md", _NOW) for i in range(250)
        ]
        content = await _run_update_index(rows, tmp_path)
        # Count lines that match the entry pattern inside the Recently Updated section.
        section_start = content.find("## Recently Updated")
        # Find the end of the section (next ## heading or EOF).
        after_section = content[section_start + len("## Recently Updated") :]
        next_heading = after_section.find("\n##")
        section_body = after_section[:next_heading] if next_heading != -1 else after_section
        entry_count = section_body.count("\n- [[")
        assert entry_count == 200, f"Expected 200 entries, got {entry_count}"


class TestRecentlyUpdatedDedup:
    """RU-04: Dedup by filename stem (slug)."""

    @pytest.mark.asyncio
    async def test_same_slug_deduped(self, tmp_path: Path) -> None:
        """Two rows with identical filename stems → only one entry in Recently Updated."""
        rows = [
            _make_row("AWS v1", "entity", "wiki/entities/aws.md", _NOW),
            _make_row("AWS v2", "entity", "wiki/entities/aws.md", _NOW),
        ]
        content = await _run_update_index(rows, tmp_path)
        section_start = content.find("## Recently Updated")
        after_section = content[section_start + len("## Recently Updated") :]
        next_heading = after_section.find("\n##")
        section_body = after_section[:next_heading] if next_heading != -1 else after_section
        # Slug 'aws' must appear at most once.
        assert section_body.count("[[aws]]") == 1

    @pytest.mark.asyncio
    async def test_different_slugs_both_rendered(self, tmp_path: Path) -> None:
        """Two rows with distinct slugs → both entries present."""
        rows = [
            _make_row("AWS", "entity", "wiki/entities/aws.md", _NOW),
            _make_row("aws-alias", "entity", "wiki/entities/aws-alias.md", _NOW),
        ]
        content = await _run_update_index(rows, tmp_path)
        assert "[[aws]]" in content
        assert "[[aws-alias]]" in content


class TestRecentlyUpdatedExclusions:
    """RU-05: Exclusions for aggregate types and raw/* paths."""

    @pytest.mark.asyncio
    async def test_excludes_aggregate_types(self, tmp_path: Path) -> None:
        """Pages with type index/log/overview are not shown in Recently Updated."""
        rows = [
            _make_row("Index", "index", "wiki/index.md", _NOW),
            _make_row("Log", "log", "wiki/log.md", _NOW),
            _make_row("Overview", "overview", "wiki/overview.md", _NOW),
            _make_row("Real", "entity", "wiki/entities/real.md", _NOW),
        ]
        content = await _run_update_index(rows, tmp_path)
        section_start = content.find("## Recently Updated")
        assert section_start != -1
        after_section = content[section_start + len("## Recently Updated") :]
        next_heading = after_section.find("\n##")
        section_body = after_section[:next_heading] if next_heading != -1 else after_section
        assert "[[index]]" not in section_body
        assert "[[log]]" not in section_body
        assert "[[overview]]" not in section_body
        assert "[[real]]" in section_body

    @pytest.mark.asyncio
    async def test_excludes_raw_paths(self, tmp_path: Path) -> None:
        """Files under raw/* are excluded from Recently Updated (not wiki content)."""
        rows = [
            _make_row("Source Doc", "source", "raw/sources/doc.md", _NOW),
            _make_row("Wiki Source", "source", "wiki/sources/wiki-source.md", _NOW),
        ]
        content = await _run_update_index(rows, tmp_path)
        section_start = content.find("## Recently Updated")
        after_section = content[section_start + len("## Recently Updated") :]
        next_heading = after_section.find("\n##")
        section_body = after_section[:next_heading] if next_heading != -1 else after_section
        assert "[[doc]]" not in section_body
        assert "[[wiki-source]]" in section_body


class TestRecentlyUpdatedEdgeCases:
    """RU-06, RU-08, RU-09: edge cases and idempotency."""

    @pytest.mark.asyncio
    async def test_empty_vault(self, tmp_path: Path) -> None:
        """RU-06: No live pages → no '## Recently Updated' section; file is still valid."""
        content = await _run_update_index([], tmp_path)
        assert "## Recently Updated" not in content
        # File still has valid frontmatter.
        assert content.startswith("---")
        assert "type: index" in content

    @pytest.mark.asyncio
    async def test_idempotent(self, tmp_path: Path) -> None:
        """RU-08: Two runs with identical rows produce byte-for-byte the same section content."""
        from app.wiki.index import update_index

        rows = [
            _make_row("Beta", "concept", "wiki/concepts/beta.md", _NOW),
            _make_row("Gamma", "entity", "wiki/entities/gamma.md", _NOW),
        ]
        # Run once.
        c1 = await _run_update_index(rows, tmp_path)

        # Re-run from same tmp_path — update_index overwrites the file.
        mock_result = MagicMock()
        mock_result.all.return_value = [
            (r.title, r.page_type, r.file_path, r.updated_at, r.summary) for r in rows
        ]
        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        vault_path = tmp_path / "vault"
        await update_index(mock_session, vault_path)
        c2 = (vault_path / "wiki" / "index.md").read_text(encoding="utf-8")

        # Strip the timestamp line so idempotency isn't broken by wall-clock drift.
        def strip_timestamp(text: str) -> str:
            return "\n".join(
                line for line in text.splitlines() if not line.startswith("*Last updated:")
            )

        assert strip_timestamp(c1) == strip_timestamp(c2)

    @pytest.mark.asyncio
    async def test_null_title_falls_back_to_slug(self, tmp_path: Path) -> None:
        """RU-09: Page with NULL title renders [[slug]] — slug (stem as display title)."""
        rows = [_make_row(None, "entity", "wiki/entities/my-page.md", _NOW)]
        content = await _run_update_index(rows, tmp_path)
        # Entry format: [[my-page]] — my-page (slug used as display title).
        assert "[[my-page]] — my-page" in content
