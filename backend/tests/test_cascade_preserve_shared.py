"""
Regression: source-delete must PRESERVE wiki pages still supported by another source
(F13 / ADR-0026 §4.1). Previously ``_delete_single_source_file`` cascade-deleted EVERY derived
page unconditionally, and cascade_delete's preserve-shared branch only fires for ``raw/sources/``
*page* targets (which sources never are) — so deleting source A destroyed an "AWS" entity page
still supported by source B (data loss).

These tests exercise the decision logic in ``_cascade_or_prune_derived`` with mocked collaborators
so they are independent of the Postgres/SQLite dialect split.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

from app import sources as src


def _dp(page_id: str, title: str = "P") -> src.SourceDerivedPage:
    return src.SourceDerivedPage(
        id=page_id,
        title=title,
        page_type="entity",
        file_path=f"wiki/entities/{title.lower()}.md",
    )


async def test_shared_page_preserved_and_source_pruned() -> None:
    """A page in TWO sources is KEPT (not deleted); only the deleted source is pruned."""
    shared_id = str(uuid.uuid4())
    with (
        patch.object(src, "_get_derived_pages", AsyncMock(return_value=[_dp(shared_id, "AWS")])),
        patch.object(
            src,
            "_get_page_sources",
            AsyncMock(return_value=["raw/sources/a.md", "raw/sources/b.md"]),
        ),
        patch.object(src, "_cascade_delete_page", AsyncMock()) as cascade,
        patch("app.ops.cascade_delete._prune_sources", AsyncMock()) as prune,
    ):
        cascaded, pruned = await src._cascade_or_prune_derived("a.md")

    assert cascaded == 0, "shared page must NOT be cascade-deleted"
    assert pruned == 1
    cascade.assert_not_called()
    prune.assert_awaited_once()
    # Pruned exactly the stored form of the deleted source, leaving b.md intact.
    assert prune.await_args.args[1] == "raw/sources/a.md"


async def test_last_source_page_is_cascade_deleted() -> None:
    """A page whose ONLY source is the deleted one is fully cascade-deleted."""
    only_id = str(uuid.uuid4())
    with (
        patch.object(src, "_get_derived_pages", AsyncMock(return_value=[_dp(only_id, "Solo")])),
        patch.object(src, "_get_page_sources", AsyncMock(return_value=["raw/sources/a.md"])),
        patch.object(src, "_cascade_delete_page", AsyncMock()) as cascade,
        patch("app.ops.cascade_delete._prune_sources", AsyncMock()) as prune,
    ):
        cascaded, pruned = await src._cascade_or_prune_derived("a.md")

    assert cascaded == 1
    assert pruned == 0
    cascade.assert_awaited_once()
    prune.assert_not_called()


async def test_mixed_batch_deletes_only_source_less_pages() -> None:
    """Mixed derived set: source-less page deleted, shared page preserved."""
    solo_id = str(uuid.uuid4())
    shared_id = str(uuid.uuid4())

    async def fake_sources(page_id: uuid.UUID) -> list[str]:
        return (
            ["raw/sources/a.md"]
            if str(page_id) == solo_id
            else ["raw/sources/a.md", "raw/sources/b.md"]
        )

    with (
        patch.object(
            src,
            "_get_derived_pages",
            AsyncMock(return_value=[_dp(solo_id, "Solo"), _dp(shared_id, "Shared")]),
        ),
        patch.object(src, "_get_page_sources", AsyncMock(side_effect=fake_sources)),
        patch.object(src, "_cascade_delete_page", AsyncMock()) as cascade,
        patch("app.ops.cascade_delete._prune_sources", AsyncMock()) as prune,
    ):
        cascaded, pruned = await src._cascade_or_prune_derived("a.md")

    assert (cascaded, pruned) == (1, 1)
    assert cascade.await_count == 1
    assert prune.await_count == 1
    # The cascaded page was the solo one, not the shared one.
    assert str(cascade.await_args.args[0]) == solo_id
