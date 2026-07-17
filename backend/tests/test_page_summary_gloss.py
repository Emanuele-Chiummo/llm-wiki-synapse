"""
1.9.4 W6 — Page.summary gloss catalogue (finding PF-INDEX-GLOSS-1).

Coverage:
    - extract_first_paragraph_summary: heading-skip, truncation, empty-body handling.
    - app.wiki.index._render_catalogue_line: gloss rendering + bare fallback.
    - app.ingest.orchestrator.persist_metadata: summary preserved when omitted on UPDATE,
      overwritten when explicitly passed (mirrors generation_key's semantics).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── extract_first_paragraph_summary ─────────────────────────────────────────────


class TestExtractFirstParagraphSummary:
    def test_empty_body_returns_none(self) -> None:
        from app.wiki.summary import extract_first_paragraph_summary

        assert extract_first_paragraph_summary("") is None
        assert extract_first_paragraph_summary("   \n\n  ") is None

    def test_simple_paragraph(self) -> None:
        from app.wiki.summary import extract_first_paragraph_summary

        body = "Qdrant is a vector database used for similarity search.\n\nMore details here."
        result = extract_first_paragraph_summary(body)
        assert result == "Qdrant is a vector database used for similarity search."

    def test_skips_leading_heading(self) -> None:
        from app.wiki.summary import extract_first_paragraph_summary

        body = "# Qdrant\n\nQdrant is a vector database.\n\nMore text."
        result = extract_first_paragraph_summary(body)
        assert result == "Qdrant is a vector database."

    def test_multiline_paragraph_collapsed_to_one_line(self) -> None:
        from app.wiki.summary import extract_first_paragraph_summary

        body = "This is line one\nand this is line two.\n\nSecond paragraph."
        result = extract_first_paragraph_summary(body)
        assert result == "This is line one and this is line two."

    def test_truncates_long_paragraph(self) -> None:
        from app.wiki.summary import extract_first_paragraph_summary

        long_text = "word " * 100  # far exceeds any reasonable max_chars
        result = extract_first_paragraph_summary(long_text, max_chars=20)
        assert result is not None
        assert len(result) <= 21  # 20 chars + ellipsis
        assert result.endswith("…")

    def test_strips_markdown_emphasis_markers(self) -> None:
        from app.wiki.summary import extract_first_paragraph_summary

        body = "This is **bold** and _italic_ and `code`."
        result = extract_first_paragraph_summary(body)
        assert result == "This is bold and italic and code."

    def test_only_heading_no_body_returns_none(self) -> None:
        from app.wiki.summary import extract_first_paragraph_summary

        body = "# Just A Title\n\n"
        assert extract_first_paragraph_summary(body) is None


# ── app.wiki.index._render_catalogue_line ───────────────────────────────────────


class TestRenderCatalogueLine:
    def test_with_summary_renders_gloss(self) -> None:
        from app.wiki.index import _render_catalogue_line

        line = _render_catalogue_line("Qdrant", "A vector database.")
        assert line == "- [[Qdrant]] — A vector database.\n"

    def test_without_summary_renders_bare_wikilink(self) -> None:
        from app.wiki.index import _render_catalogue_line

        assert _render_catalogue_line("Qdrant", None) == "- [[Qdrant]]\n"
        assert _render_catalogue_line("Qdrant", "") == "- [[Qdrant]]\n"
        assert _render_catalogue_line("Qdrant", "   ") == "- [[Qdrant]]\n"

    def test_truncates_at_gloss_max_chars(self) -> None:
        from app.wiki.index import _GLOSS_MAX_CHARS, _render_catalogue_line

        long_summary = "x" * (_GLOSS_MAX_CHARS + 50)
        line = _render_catalogue_line("Title", long_summary)
        # "- [[Title]] — " prefix + up to _GLOSS_MAX_CHARS chars + ellipsis + "\n"
        gloss_part = line.split(" — ", 1)[1].rstrip("\n")
        assert gloss_part.endswith("…")
        assert len(gloss_part) <= _GLOSS_MAX_CHARS + 1


# ── index.md end-to-end gloss rendering ─────────────────────────────────────────


class TestIndexMdGlossRendering:
    @pytest.mark.asyncio
    async def test_catalogue_entry_with_summary_shows_gloss(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from app.wiki.index import update_index

        mock_result = MagicMock()
        mock_result.all.return_value = [
            ("Qdrant", "concept", "wiki/concepts/qdrant.md", None, "A vector database.")
        ]
        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        vault_path = tmp_path / "vault"
        vault_path.mkdir()
        await update_index(mock_session, vault_path)
        content = (vault_path / "wiki" / "index.md").read_text(encoding="utf-8")

        assert "- [[Qdrant]] — A vector database.\n" in content

    @pytest.mark.asyncio
    async def test_catalogue_entry_without_summary_stays_bare(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from app.wiki.index import update_index

        mock_result = MagicMock()
        mock_result.all.return_value = [
            ("Qdrant", "concept", "wiki/concepts/qdrant.md", None, None)
        ]
        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        vault_path = tmp_path / "vault"
        vault_path.mkdir()
        await update_index(mock_session, vault_path)
        content = (vault_path / "wiki" / "index.md").read_text(encoding="utf-8")

        assert "- [[Qdrant]]\n" in content
        assert "- [[Qdrant]] — " not in content


# ── persist_metadata: summary preserve-if-omitted semantics ─────────────────────


class TestPersistMetadataSummarySemantics:
    @pytest.mark.asyncio
    async def test_insert_new_page_writes_summary(self) -> None:
        from app.ingest.orchestrator import persist_metadata

        sess = MagicMock()
        sess.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
        sess.add = MagicMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.ingest.orchestrator.get_session", return_value=ctx):
            await persist_metadata(
                page_id=uuid.uuid4(),
                vault_id="v",
                file_path="wiki/concepts/x.md",
                title="X",
                page_type="concept",
                sources=["s"],
                content_hash="h" * 64,
                source_mtime_ns=0,
                summary="A short gloss.",
            )

        added_page = sess.add.call_args.args[0]
        assert added_page.summary == "A short gloss."

    @pytest.mark.asyncio
    async def test_update_without_summary_preserves_existing(self) -> None:
        """Omitting summary= on an UPDATE call must NOT wipe an existing gloss (mirrors
        generation_key's preserve-if-omitted contract)."""
        from app.ingest.orchestrator import persist_metadata

        existing_page = MagicMock()
        existing_page.summary = "Existing gloss."

        sess = MagicMock()
        sess.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: existing_page))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.ingest.orchestrator.get_session", return_value=ctx):
            await persist_metadata(
                page_id=uuid.uuid4(),
                vault_id="v",
                file_path="wiki/concepts/x.md",
                title="X",
                page_type="concept",
                sources=["s"],
                content_hash="h" * 64,
                source_mtime_ns=0,
                # summary omitted (default None) — a tags/type-only metadata update.
            )

        assert existing_page.summary == "Existing gloss."

    @pytest.mark.asyncio
    async def test_update_with_summary_overwrites_existing(self) -> None:
        from app.ingest.orchestrator import persist_metadata

        existing_page = MagicMock()
        existing_page.summary = "Stale gloss."

        sess = MagicMock()
        sess.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: existing_page))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=sess)
        ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("app.ingest.orchestrator.get_session", return_value=ctx):
            await persist_metadata(
                page_id=uuid.uuid4(),
                vault_id="v",
                file_path="wiki/concepts/x.md",
                title="X",
                page_type="concept",
                sources=["s"],
                content_hash="h" * 64,
                source_mtime_ns=0,
                summary="Fresh gloss.",
            )

        assert existing_page.summary == "Fresh gloss."
