"""
Regression test for write_wiki_page upsert-by-natural-key (slug-collision crash).

Bug (found live during the v0.5 re-analysis): write_wiki_page assigned a fresh uuid4 page_id
on every call, so re-generating a page whose slug already exists took persist_metadata's INSERT
branch and violated the (vault_id, file_path) "uix_pages_vault_file_path_live" unique constraint
(asyncpg UniqueViolationError). The fix reuses the existing LIVE page's id and unions sources to
preserve provenance (F13 shared-entity detection).

NOTE on test fidelity (see project memory "Raw SQL: SQLite tests vs Postgres runtime"): the
offending constraint is a Postgres PARTIAL unique index (WHERE deleted_at IS NULL); SQLite does
not enforce it identically, so the OLD code would not necessarily raise here. This test therefore
asserts the POSITIVE post-fix behavior — single live row, STABLE id across regenerations, and
UNIONED sources — which validates the lookup/reuse/merge logic deterministically regardless of
constraint enforcement. The Postgres-level enforcement is covered by the live integration run.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select

# Re-use the shared SQLite + FakeQdrant/FakeEmbedding fixture.
from tests.test_api import api_env  # noqa: F401


def _wikipage(title: str, source: str) -> Any:
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

    return WikiPage(
        title=title,
        type=PageType.ENTITY,
        content=f"Body about {title}.",
        frontmatter=WikiFrontmatter(type=PageType.ENTITY, title=title, sources=[source], lang="en"),
    )


@pytest.mark.asyncio
async def test_write_wiki_page_reuses_id_and_merges_sources_on_slug_collision(
    api_env: dict[str, Any],
) -> None:
    from app.db import get_session
    from app.ingest.orchestrator import write_wiki_page
    from app.models import Page

    rel_path = "wiki/entities/vector-embeddings.md"

    # First generation of "Vector Embeddings" from source A.
    row1 = await write_wiki_page(
        None, _wikipage("Vector Embeddings", "raw/sources/a.md"), "raw/sources/a.md"
    )
    # Second generation of the SAME title (→ same slug) from a different source B.
    row2 = await write_wiki_page(
        None, _wikipage("Vector Embeddings", "raw/sources/b.md"), "raw/sources/b.md"
    )

    # id is STABLE → persist_metadata took the UPDATE branch, not a second INSERT.
    assert row1.id == row2.id

    async with get_session() as sess:
        rows = (
            (
                await sess.execute(
                    select(Page).where(
                        Page.file_path == rel_path,
                        Page.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )

    # Exactly ONE live page with that file_path (no duplicate row).
    assert len(rows) == 1
    page = rows[0]
    assert page.id == row1.id

    # Sources are UNIONED: both origin sources preserved (provenance for F13 cascade-delete).
    srcs = page.sources or []
    assert "raw/sources/a.md" in srcs
    assert "raw/sources/b.md" in srcs
