"""
Tags vertical-slice tests (K6 navigation tags, nashsu/llm_wiki parity).

Three surfaces, one write path:
  (a) WikiFrontmatter accepts / validates / normalizes / caps `tags`  — infra-free schema tests.
  (b) write_wiki_page persists tags to Page.tags AND writes them into the on-disk frontmatter
      with the content_hash consistent (no DB/disk desync)               — SQLite api_env fixture.
  (c) GET /pages/{id}/content returns `tags`                            — SQLite api_env fixture.

Additive / backward-compatible: absent tags → [] at the schema boundary, NULL on the column,
and the frontmatter block omits the `tags` key entirely (I5, clean YAML).
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

# Re-use the shared SQLite + FakeQdrant/FakeEmbedding fixtures for the write-path + API tests.
from tests.test_api import api_client, api_env  # noqa: F401

# ── (a) WikiFrontmatter schema: accept / validate / normalize / cap ──────────────


class TestWikiFrontmatterTags:
    """WikiFrontmatter.tags — additive, normalized, capped (K6)."""

    def _fm(self, **overrides: Any) -> Any:
        from app.ingest.schemas import PageType, WikiFrontmatter

        base: dict[str, Any] = {
            "type": PageType.CONCEPT,
            "title": "T",
            "sources": ["raw/sources/a.md"],
            "lang": "en",
        }
        base.update(overrides)
        return WikiFrontmatter(**base)

    def test_absent_defaults_to_empty_list(self) -> None:
        """Backward-compatible: omitting tags → []."""
        assert self._fm().tags == []

    def test_none_defaults_to_empty_list(self) -> None:
        assert self._fm(tags=None).tags == []

    def test_basic_list_passthrough(self) -> None:
        assert self._fm(tags=["homelab", "vector-db"]).tags == ["homelab", "vector-db"]

    def test_trim_and_lowercase(self) -> None:
        assert self._fm(tags=["  Homelab ", "Vector-DB"]).tags == ["homelab", "vector-db"]

    def test_dedupe_preserves_first_order(self) -> None:
        assert self._fm(tags=["a", "b", "A", " b ", "c"]).tags == ["a", "b", "c"]

    def test_blanks_dropped(self) -> None:
        assert self._fm(tags=["ok", "", "   ", "fine"]).tags == ["ok", "fine"]

    def test_scalar_string_coerced_to_single_tag(self) -> None:
        assert self._fm(tags="solo").tags == ["solo"]

    def test_each_tag_capped_at_40_chars(self) -> None:
        long = "x" * 100
        (tag,) = self._fm(tags=[long]).tags
        assert tag == "x" * 40

    def test_list_capped_at_12_tags(self) -> None:
        many = [f"tag{i}" for i in range(30)]
        out = self._fm(tags=many).tags
        assert len(out) == 12
        assert out == [f"tag{i}" for i in range(12)]

    def test_non_string_items_stringified(self) -> None:
        # Never raises on odd input — best-effort clean (tags are navigation metadata, not F3).
        assert self._fm(tags=[1, 2, "three"]).tags == ["1", "2", "three"]

    def test_model_dump_roundtrip(self) -> None:
        fm = self._fm(tags=["Alpha", "beta"])
        assert fm.model_dump()["tags"] == ["alpha", "beta"]


# ── (b) write_wiki_page persists tags to Page.tags + into the file frontmatter ───


@pytest.mark.asyncio
async def test_write_wiki_page_persists_tags_and_serializes_frontmatter(
    api_env: dict[str, Any],
) -> None:
    """
    A WikiPage carrying tags must (1) persist them to Page.tags, (2) serialize them into the
    on-disk YAML frontmatter as a valid list (I5), and (3) keep content_hash == hash of the
    exact bytes on disk (no DB/disk desync).
    """
    import frontmatter as fm_lib
    from app.config import settings
    from app.db import get_session
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
    from app.models import Page
    from sqlalchemy import select

    page = WikiPage(
        title="Vector Embeddings",
        type=PageType.CONCEPT,
        content="Vector embeddings map tokens to dense vectors.\n",
        frontmatter=WikiFrontmatter(
            type=PageType.CONCEPT,
            title="Vector Embeddings",
            sources=["raw/sources/a.md"],
            lang="en",
            tags=["  Embeddings ", "embeddings", "RAG", "vector-search"],
        ),
    )

    row = await write_wiki_page(None, page, "raw/sources/a.md")

    # ── (2) file frontmatter has a valid, normalized tags list ────────────────
    abs_path = settings.vault_root / "wiki" / "concepts" / "vector-embeddings.md"
    file_bytes = abs_path.read_bytes()
    parsed = fm_lib.loads(file_bytes.decode("utf-8"))
    assert parsed.metadata["tags"] == ["embeddings", "rag", "vector-search"]
    # Valid YAML list on disk (Obsidian-compatible) — not a python repr / scalar.
    assert "tags:" in file_bytes.decode("utf-8")

    # ── (1) Page.tags persisted (mirrors sources) ─────────────────────────────
    async with get_session() as sess:
        db_row = (await sess.execute(select(Page).where(Page.id == row.id))).scalar_one()
    assert db_row.tags == ["embeddings", "rag", "vector-search"]

    # ── (3) content_hash == hash of the bytes on disk ────────────────────────
    assert db_row.content_hash == hashlib.sha256(file_bytes).hexdigest()


@pytest.mark.asyncio
async def test_write_wiki_page_without_tags_omits_key_and_stores_null(
    api_env: dict[str, Any],
) -> None:
    """
    No tags → the frontmatter block must NOT contain a `tags:` key (clean YAML, I5) and the
    column stores NULL (backward-compatible). Hash still consistent.
    """
    from app.config import settings
    from app.db import get_session
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
    from app.models import Page
    from sqlalchemy import select

    page = WikiPage(
        title="No Tags Page",
        type=PageType.CONCEPT,
        content="Body without tags.\n",
        frontmatter=WikiFrontmatter(
            type=PageType.CONCEPT,
            title="No Tags Page",
            sources=["raw/sources/b.md"],
            lang="en",
        ),
    )

    row = await write_wiki_page(None, page, "raw/sources/b.md")

    abs_path = settings.vault_root / "wiki" / "concepts" / "no-tags-page.md"
    file_text = abs_path.read_text(encoding="utf-8")
    assert "tags:" not in file_text  # clean frontmatter — no empty tags key

    async with get_session() as sess:
        db_row = (await sess.execute(select(Page).where(Page.id == row.id))).scalar_one()
    assert not db_row.tags  # NULL or [] — backward compatible


# ── (c) GET /pages/{id}/content returns tags ─────────────────────────────────────


@pytest.mark.asyncio
async def test_get_page_content_returns_tags(api_env: dict[str, Any], api_client: Any) -> None:
    """GET /pages/{id}/content includes the `tags` key (additive, like type/sources)."""
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

    page = WikiPage(
        title="Tagged Concept",
        type=PageType.CONCEPT,
        content="A tagged concept page.\n",
        frontmatter=WikiFrontmatter(
            type=PageType.CONCEPT,
            title="Tagged Concept",
            sources=["raw/sources/c.md"],
            lang="en",
            tags=["homelab", "graph"],
        ),
    )
    row = await write_wiki_page(None, page, "raw/sources/c.md")

    resp = await api_client.get(f"/pages/{row.id}/content")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "tags" in payload
    assert payload["tags"] == ["homelab", "graph"]


@pytest.mark.asyncio
async def test_get_page_content_tags_present_even_when_empty(
    api_env: dict[str, Any], api_client: Any
) -> None:
    """The `tags` key is always present in the response (NULL/empty when the page has none)."""
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

    page = WikiPage(
        title="Untagged Concept",
        type=PageType.CONCEPT,
        content="An untagged concept page.\n",
        frontmatter=WikiFrontmatter(
            type=PageType.CONCEPT,
            title="Untagged Concept",
            sources=["raw/sources/d.md"],
            lang="en",
        ),
    )
    row = await write_wiki_page(None, page, "raw/sources/d.md")

    resp = await api_client.get(f"/pages/{row.id}/content")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "tags" in payload
    assert not payload["tags"]  # None or []
