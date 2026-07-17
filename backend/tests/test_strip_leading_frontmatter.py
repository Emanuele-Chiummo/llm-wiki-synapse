"""
Regression tests for the DUPLICATED-frontmatter bug (found live during a real CLI ingest).

On the CLI delegated path the agent called write_page with a `content` that ALREADY began with
a YAML frontmatter block, even though the tool contract says content must be the body only. The
shared write seam (write_wiki_page) then prepended its own frontmatter → ~24/35 pages had TWO
`---...---` blocks (a valid top block + a duplicate at the start of the body).

The fix strips exactly ONE stray leading frontmatter block from the body before composing the
file (orchestrator._strip_leading_frontmatter), applied at the single shared write seam so both
the orchestrated loop and the MCP/CLI path benefit. These tests cover the pure helper's
edge-cases plus a functional composition assertion (exactly one frontmatter block on disk, and
the DB content hash == the hash of the bytes actually written — no desync).
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

# Re-use the shared SQLite + FakeQdrant/FakeEmbedding fixture for the composition test.
from tests.test_api import api_env  # noqa: F401

# ── Pure helper unit tests (no DB) ───────────────────────────────────────────


def test_strip_leading_frontmatter_removes_a_leading_block_once() -> None:
    """(a) body with a leading `---...---` block → stripped exactly once."""
    from app.ingest.writer import _strip_leading_frontmatter

    body = (
        "---\n"
        "type: entity\n"
        "title: Vector Embeddings\n"
        "sources:\n"
        "  - raw/sources/a.md\n"
        "---\n"
        "\n"
        "Vector embeddings map tokens to dense vectors.\n"
    )
    out = _strip_leading_frontmatter(body)
    assert out == "Vector embeddings map tokens to dense vectors.\n"
    # Only ONE block removed: no residual fence at the start.
    assert not out.lstrip().startswith("---")


def test_strip_leading_frontmatter_no_frontmatter_unchanged() -> None:
    """(b) body with NO frontmatter → returned unchanged."""
    from app.ingest.writer import _strip_leading_frontmatter

    body = "Just a plain body.\n\nWith a second paragraph.\n"
    assert _strip_leading_frontmatter(body) == body


def test_strip_leading_frontmatter_later_horizontal_rule_untouched() -> None:
    """(c) body whose content legitimately contains a `---` horizontal rule later → untouched."""
    from app.ingest.writer import _strip_leading_frontmatter

    body = (
        "Intro paragraph before the rule.\n" "\n" "---\n" "\n" "Section after a horizontal rule.\n"
    )
    # The body does not START with a fence, so nothing is removed.
    assert _strip_leading_frontmatter(body) == body


def test_strip_leading_frontmatter_unterminated_fence_unchanged() -> None:
    """(d) unterminated leading `---` (no closing fence) → returned unchanged (never corrupt)."""
    from app.ingest.writer import _strip_leading_frontmatter

    body = "---\ntype: entity\ntitle: Oops no closing fence\n\nBody text follows.\n"
    assert _strip_leading_frontmatter(body) == body


def test_strip_leading_frontmatter_yaml_document_end_terminator() -> None:
    """A leading block closed by the `...` YAML document-end marker is also stripped once."""
    from app.ingest.writer import _strip_leading_frontmatter

    body = "---\ntype: concept\ntitle: X\n...\n\nActual body.\n"
    assert _strip_leading_frontmatter(body) == "Actual body.\n"


def test_strip_leading_frontmatter_tolerates_leading_blank_lines() -> None:
    """A block preceded by blank lines is still recognised and stripped once."""
    from app.ingest.writer import _strip_leading_frontmatter

    body = "\n\n---\ntype: entity\ntitle: Y\n---\nBody.\n"
    assert _strip_leading_frontmatter(body) == "Body.\n"


# ── Functional composition test (via the shared write seam) ──────────────────


@pytest.mark.asyncio
async def test_write_wiki_page_produces_single_frontmatter_block(
    api_env: dict[str, Any],
) -> None:
    """
    Passing a WikiPage whose `content` erroneously begins with a full frontmatter block must
    yield a file on disk with EXACTLY ONE frontmatter block, and the persisted content_hash must
    equal the hash of the bytes actually written (no DB/disk desync).
    """
    from app.config import settings
    from app.db import get_session
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
    from app.ingest.writer import write_wiki_page
    from app.models import Page
    from sqlalchemy import select

    dup_content = (
        "---\n"
        "type: entity\n"
        "title: Vector Embeddings\n"
        "sources:\n"
        "  - raw/sources/a.md\n"
        "lang: en\n"
        "---\n"
        "\n"
        "Vector embeddings map tokens to dense vectors.\n"
    )
    page = WikiPage(
        title="Vector Embeddings",
        type=PageType.ENTITY,
        content=dup_content,  # contract violation: body includes a frontmatter block
        frontmatter=WikiFrontmatter(
            type=PageType.ENTITY,
            title="Vector Embeddings",
            sources=["raw/sources/a.md"],
            lang="en",
        ),
    )

    row = await write_wiki_page(None, page, "raw/sources/a.md")

    abs_path = settings.vault_root / "wiki" / "entities" / "vector-embeddings.md"
    file_bytes = abs_path.read_bytes()
    file_text = file_bytes.decode("utf-8")

    # EXACTLY ONE frontmatter block: count leading-of-line `---` fences.
    fence_lines = [ln for ln in file_text.split("\n") if ln == "---"]
    assert len(fence_lines) == 2, f"expected one frontmatter block (2 fences), got:\n{file_text}"

    # The body content survived, and there is no duplicated title/type inside the body.
    assert "Vector embeddings map tokens to dense vectors." in file_text

    # DB content hash == hash of bytes on disk (I1/I5 — no desync).
    async with get_session() as sess:
        db_row = (await sess.execute(select(Page).where(Page.id == row.id))).scalar_one()
    assert db_row.content_hash == hashlib.sha256(file_bytes).hexdigest()
