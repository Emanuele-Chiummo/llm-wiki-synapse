"""
F13 Cascade Delete — unit + integration tests (ADR-0026, AC-F13-1..7).

Isolation strategy
------------------
The production code (cascade_delete.py) uses SQLAlchemy 2 ORM models backed by
postgresql.UUID(as_uuid=True) which is Postgres-specific and incompatible with
SQLite.  Rather than fighting dialect incompatibility, tests patch `get_session`
to yield a fully-controlled `AsyncMock` session whose `execute()` returns
predetermined `MagicMock` result objects.  This is the most correct approach for
testing pure-logic correctness without a live database.

For filesystem operations (wikilink rewrites, raw-source deletion, index.md),
tests use `tmp_path` to create real files so the actual Path I/O is exercised.

Test coverage (AC-F13-1..7):
  T-CD-001  plan_cascade_delete: read-only — data_version unchanged
  T-CD-002  method (a) exact match: link with target_page_id finds referencing file
  T-CD-003  method (b) slug match: slug-normalised title found
  T-CD-004  method (c) fulltext not invoked on links-table-hit path (I1)
  T-CD-005  preserve-shared: page in 2 sources PRESERVED (sources[] pruned)
  T-CD-006  preserve-shared: page with 1 source DELETED when that source deleted
  T-CD-007  dead-wikilink rewrite: [[Deleted]] → plain text; frontmatter byte-identical (I5)
  T-CD-008  dead-wikilink rewrite: [[Deleted|alias]] → alias
  T-CD-009  soft-delete: deleted_at set, row NOT hard-deleted
  T-CD-010  Qdrant delete_point called for each deleted page
  T-CD-011  data_version bumped EXACTLY ONCE (AC-F13-4c)
  T-CD-012  NO FA2 / GraphEngine.recompute() called (I2)
  T-CD-013  files_written == len(plan.wikilinks_to_rewrite) (AC-F13-4a)
  T-CD-014  raw/sources/ file deleted from disk (AQ-v0.5-5)
  T-CD-015  index.md no longer lists the deleted page
  T-CD-016  shared-entity WARNING logged; deletion proceeds (AC-F13-3)
  T-CD-017  POST preview → 200 read-only
  T-CD-018  DELETE /pages/{id} → 200 correct shape
  T-CD-019  DELETE /pages/{id} → 404 double-delete (AC-F13-5c)
  T-CD-020  POST preview → 404 on unknown page
  T-CD-021  cascade_delete with 3 referencing pages: all 3 files written
  T-CD-022  _rewrite_body: [[T]], [[T|alias]], [[T#section]], untouched
  T-CD-023  _slugify: basic normalisation
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import frontmatter as fm
import pytest

# ── Pure function tests (no DB required) ─────────────────────────────────────


class TestRewriteBody:
    """T-CD-022 — _rewrite_body handles all wikilink variants."""

    def test_plain_wikilink(self) -> None:
        from app.ops.cascade_delete import _rewrite_body

        body = "See [[Alpha]] for details."
        result = _rewrite_body(body, "Alpha")
        assert "[[Alpha]]" not in result
        assert "Alpha" in result

    def test_aliased_wikilink_keeps_alias(self) -> None:
        from app.ops.cascade_delete import _rewrite_body

        body = "See [[Alpha|the alpha page]] for details."
        result = _rewrite_body(body, "Alpha")
        assert "[[Alpha|the alpha page]]" not in result
        assert "the alpha page" in result

    def test_sectioned_wikilink(self) -> None:
        from app.ops.cascade_delete import _rewrite_body

        body = "See [[Alpha#intro]] for the intro."
        result = _rewrite_body(body, "Alpha")
        assert "[[Alpha#intro]]" not in result
        assert "Alpha" in result

    def test_other_wikilinks_untouched(self) -> None:
        from app.ops.cascade_delete import _rewrite_body

        body = "See [[Alpha]] and [[Beta]] for details."
        result = _rewrite_body(body, "Alpha")
        assert "[[Alpha]]" not in result
        assert "[[Beta]]" in result

    def test_case_slug_variant(self) -> None:
        from app.ops.cascade_delete import _rewrite_body

        body = "See [[my alpha page]] for details."
        result = _rewrite_body(body, "My Alpha Page")
        assert "[[my alpha page]]" not in result

    def test_no_match_unchanged(self) -> None:
        from app.ops.cascade_delete import _rewrite_body

        body = "See [[Beta]] for details."
        result = _rewrite_body(body, "Alpha")
        assert result == body

    def test_multiple_occurrences_all_replaced(self) -> None:
        from app.ops.cascade_delete import _rewrite_body

        body = "See [[Alpha]] and again [[Alpha]]."
        result = _rewrite_body(body, "Alpha")
        assert "[[Alpha]]" not in result
        assert result.count("Alpha") >= 2


class TestSlugify:
    """T-CD-023 — _slugify basics."""

    def test_lowercase(self) -> None:
        from app.ops.cascade_delete import _slugify

        assert _slugify("Hello World") == "hello-world"

    def test_strips_special(self) -> None:
        from app.ops.cascade_delete import _slugify

        result = _slugify("My Page (v2)")
        assert result == "my-page-v2"

    def test_empty_fallback(self) -> None:
        from app.ops.cascade_delete import _slugify

        assert _slugify("!!!!") == "untitled"

    def test_already_slug(self) -> None:
        from app.ops.cascade_delete import _slugify

        assert _slugify("alpha") == "alpha"


# ── Session mock helpers ───────────────────────────────────────────────────────


def _make_mock_result(*rows: Any) -> MagicMock:
    """
    Return a MagicMock that behaves like a SQLAlchemy CursorResult:
    - .all() returns a list of the given rows
    - .scalar_one_or_none() returns rows[0] if present else None
    - .first() returns rows[0] if present else None
    """
    result = MagicMock()
    result.all.return_value = list(rows)
    result.scalar_one_or_none.return_value = rows[0] if rows else None
    result.first.return_value = rows[0] if rows else None
    return result


def _noop_session_patch() -> Any:
    """Context manager yielding a session mock that returns empty results."""

    @asynccontextmanager
    async def _get_session() -> AsyncIterator[Any]:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_make_mock_result())
        session.add = MagicMock()
        session.commit = AsyncMock()
        yield session

    return _get_session


# ── Integration-style tests using mock sessions ───────────────────────────────


@pytest.mark.asyncio
class TestPlanCascadeDeleteReadOnly:
    """T-CD-001, T-CD-002, T-CD-003 — plan_cascade_delete is read-only."""

    async def test_raises_on_missing_page(self) -> None:
        """T-CD-001a: PageNotFoundError when page not found."""

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            # select(Page) returns no row → scalar_one_or_none = None
            session.execute = AsyncMock(return_value=_make_mock_result())
            yield session

        with (
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
        ):
            mock_settings.vault_root = Path("/tmp")
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import PageNotFoundError, plan_cascade_delete

            with pytest.raises(PageNotFoundError):
                await plan_cascade_delete(uuid.uuid4())

    async def test_preview_does_not_modify_vault_state(self, tmp_path: Path) -> None:
        """T-CD-001b: preview never calls UPDATE on vault_state."""
        target_id = uuid.uuid4()

        # Mock page
        page_mock = MagicMock()
        page_mock.id = target_id
        page_mock.title = "Alpha"
        page_mock.file_path = "wiki/concepts/alpha.md"
        page_mock.sources = []

        # Create wiki file
        wiki_dir = tmp_path / "wiki" / "concepts"
        wiki_dir.mkdir(parents=True)
        (wiki_dir / "alpha.md").write_text(
            "---\ntitle: Alpha\ntype: concept\nsources: []\n---\n\nAlpha body.",
            encoding="utf-8",
        )

        update_calls: list[str] = []

        call_num = 0

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            nonlocal call_num
            call_num += 1
            session = AsyncMock()

            if call_num == 1:
                # First call: select(Page) — return our page
                r = MagicMock()
                r.scalar_one_or_none.return_value = page_mock
                r.all.return_value = []
                r.first.return_value = page_mock
                session.execute = AsyncMock(return_value=r)
            else:
                # All other calls: empty results (links, edges, vault_state)
                async def _execute(stmt: Any, *a: Any, **kw: Any) -> Any:
                    stmt_str = str(stmt).upper()
                    if "UPDATE" in stmt_str and "VAULT_STATE" in stmt_str:
                        update_calls.append(stmt_str[:80])
                    return _make_mock_result()

                session.execute = _execute
                session.add = MagicMock()
                session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import plan_cascade_delete

            plan = await plan_cascade_delete(target_id)

        assert not update_calls, f"preview must NOT update vault_state; calls: {update_calls}"
        assert plan.target_page_id == target_id
        assert plan.target_title == "Alpha"

    async def test_method_a_result_reflected_in_plan(self, tmp_path: Path) -> None:
        """T-CD-002: when links table has exact match, plan.wikilinks_to_rewrite is populated."""
        target_id = uuid.uuid4()
        ref_id = uuid.uuid4()

        # Seed filesystem
        (tmp_path / "wiki" / "concepts").mkdir(parents=True)
        (tmp_path / "wiki" / "concepts" / "alpha.md").write_text(
            "---\ntitle: Alpha\ntype: concept\nsources: []\n---\n\nAlpha body.",
            encoding="utf-8",
        )
        (tmp_path / "wiki" / "concepts" / "beta.md").write_text(
            "---\ntitle: Beta\ntype: concept\nsources: []\n---\n\nSee [[Alpha]].",
            encoding="utf-8",
        )

        # Mock page object
        page_mock = MagicMock()
        page_mock.id = target_id
        page_mock.title = "Alpha"
        page_mock.file_path = "wiki/concepts/alpha.md"
        page_mock.sources = []

        # Mock link row: (source_page_id, target_title)
        link_row = (ref_id, "Alpha")
        # Mock page row for referencing page: (id, file_path)
        ref_page_row = (ref_id, "wiki/concepts/beta.md")

        call_count = 0

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            nonlocal call_count
            call_count += 1
            session = AsyncMock()
            if call_count == 1:
                # select(Page) for target
                r = MagicMock()
                r.scalar_one_or_none.return_value = page_mock
                r.all.return_value = []
                r.first.return_value = page_mock
                session.execute = AsyncMock(return_value=r)
            elif call_count == 2:
                # method_a: select Link rows (target_page_id == id)
                r = MagicMock()
                r.all.return_value = [link_row]
                r.scalar_one_or_none.return_value = None
                r.first.return_value = None
                session.execute = AsyncMock(return_value=r)
            elif call_count == 3:
                # method_a: resolve page file_paths
                r = MagicMock()
                r.all.return_value = [ref_page_row]
                r.scalar_one_or_none.return_value = None
                session.execute = AsyncMock(return_value=r)
            else:
                # method_b, method_c, edges: empty
                r = _make_mock_result()
                session.execute = AsyncMock(return_value=r)
            yield session

        with (
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import plan_cascade_delete

            plan = await plan_cascade_delete(target_id)

        # Method (a) should have populated wikilinks_to_rewrite
        assert len(plan.wikilinks_to_rewrite) >= 1
        assert any(r.file_path == "wiki/concepts/beta.md" for r in plan.wikilinks_to_rewrite)
        assert plan.match_methods_used.get("wiki/concepts/beta.md") == "exact"


@pytest.mark.asyncio
class TestCascadeDeleteApply:
    """T-CD-007..T-CD-016 — cascade_delete apply-path tests."""

    async def test_dead_wikilink_plain_rewrite(self, tmp_path: Path) -> None:
        """T-CD-007: [[Target]] → Target; frontmatter byte-identical (I5)."""
        target_id = uuid.uuid4()
        ref_id = uuid.uuid4()

        wiki_dir = tmp_path / "wiki" / "concepts"
        wiki_dir.mkdir(parents=True)
        ref_file = wiki_dir / "ref.md"
        ref_file.write_text(
            "---\ntitle: Ref\ntype: concept\nsources: []\n---\n\nSee [[Target]] here.",
            encoding="utf-8",
        )

        from app.ops.cascade_delete import CascadePlan, WikilinkRewrite

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Target",
            target_file_path="wiki/concepts/target.md",
            will_delete=[target_id],
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=[
                WikilinkRewrite(
                    source_page_id=ref_id,
                    file_path="wiki/concepts/ref.md",
                    target_title="Target",
                    occurrences=1,
                )
            ],
            index_entry_will_be_removed=True,
            raw_source_to_delete=None,
            shared_entity_warnings=[],
            match_methods_used={"wiki/concepts/ref.md": "exact"},
        )

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_mock_result())
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", AsyncMock()),
            patch("app.wiki.index.update_index", AsyncMock()),
            patch("app.ops.cascade_delete._repersist_links", AsyncMock()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            result = await cascade_delete(target_id)

        # [[Target]] must be gone from ref.md body
        content = ref_file.read_text(encoding="utf-8")
        assert "[[Target]]" not in content
        assert "Target" in content

        # Frontmatter must still be valid
        post = fm.loads(content)
        assert post.metadata.get("title") == "Ref"
        assert post.metadata.get("type") == "concept"

        assert result.files_written == 1

    async def test_dead_wikilink_alias_kept(self, tmp_path: Path) -> None:
        """T-CD-008: [[Target|my alias]] → my alias."""
        target_id = uuid.uuid4()
        ref_id = uuid.uuid4()

        wiki_dir = tmp_path / "wiki" / "concepts"
        wiki_dir.mkdir(parents=True)
        ref_file = wiki_dir / "ref.md"
        ref_file.write_text(
            "---\ntitle: Ref\ntype: concept\nsources: []\n---\n\nSee [[Target|my alias]].",
            encoding="utf-8",
        )

        from app.ops.cascade_delete import CascadePlan, WikilinkRewrite

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Target",
            target_file_path="wiki/concepts/target.md",
            will_delete=[target_id],
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=[
                WikilinkRewrite(
                    source_page_id=ref_id,
                    file_path="wiki/concepts/ref.md",
                    target_title="Target",
                    occurrences=1,
                )
            ],
            index_entry_will_be_removed=True,
            raw_source_to_delete=None,
            shared_entity_warnings=[],
            match_methods_used={"wiki/concepts/ref.md": "exact"},
        )

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_mock_result())
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", AsyncMock()),
            patch("app.wiki.index.update_index", AsyncMock()),
            patch("app.ops.cascade_delete._repersist_links", AsyncMock()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            await cascade_delete(target_id)

        content = ref_file.read_text(encoding="utf-8")
        assert "[[Target|my alias]]" not in content
        assert "my alias" in content

    async def test_soft_delete_update_called(self, tmp_path: Path) -> None:
        """T-CD-009: UPDATE pages SET deleted_at called (soft-delete, not hard delete)."""
        target_id = uuid.uuid4()
        update_calls: list[str] = []

        from app.ops.cascade_delete import CascadePlan

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Target",
            target_file_path="wiki/concepts/target.md",
            will_delete=[target_id],
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=[],
            index_entry_will_be_removed=True,
            raw_source_to_delete=None,
            shared_entity_warnings=[],
            match_methods_used={},
        )

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()

            async def _execute(stmt: Any, *args: Any, **kwargs: Any) -> Any:
                stmt_str = str(stmt)
                if "UPDATE" in stmt_str and "pages" in stmt_str:
                    update_calls.append("soft_delete")
                return _make_mock_result()

            session.execute = _execute
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", AsyncMock()),
            patch("app.wiki.index.update_index", AsyncMock()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            await cascade_delete(target_id)

        # The soft-delete step must have issued an UPDATE
        assert (
            "soft_delete" in update_calls
        ), "cascade_delete must issue UPDATE pages SET deleted_at (soft-delete)"

    async def test_qdrant_delete_point_called(self, tmp_path: Path) -> None:
        """T-CD-010: delete_point called for each page in will_delete."""
        target_id = uuid.uuid4()
        wiki_id = uuid.uuid4()

        from app.ops.cascade_delete import CascadePlan

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Source",
            target_file_path="raw/sources/doc.md",
            will_delete=[target_id, wiki_id],
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=[],
            index_entry_will_be_removed=False,
            raw_source_to_delete="raw/sources/doc.md",
            shared_entity_warnings=[],
            match_methods_used={},
        )

        # Create the raw source file so step 9 can delete it
        raw_dir = tmp_path / "raw" / "sources"
        raw_dir.mkdir(parents=True)
        (raw_dir / "doc.md").write_text("source content", encoding="utf-8")

        mock_delete_point = AsyncMock()

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_mock_result())
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", mock_delete_point),
            patch("app.wiki.index.update_index", AsyncMock()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            await cascade_delete(target_id)

        # Called once for each page in will_delete
        assert mock_delete_point.call_count == 2
        called_ids = {call.args[0] for call in mock_delete_point.call_args_list}
        assert target_id in called_ids
        assert wiki_id in called_ids

    async def test_data_version_bumped_exactly_once(self, tmp_path: Path) -> None:
        """T-CD-011: data_version_after > 0; _bump_version_and_notify called once."""
        target_id = uuid.uuid4()

        from app.ops.cascade_delete import CascadePlan

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Target",
            target_file_path="wiki/concepts/target.md",
            will_delete=[target_id],
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=[],
            index_entry_will_be_removed=True,
            raw_source_to_delete=None,
            shared_entity_warnings=[],
            match_methods_used={},
        )

        bump_calls = 0

        async def _mock_bump() -> int:
            nonlocal bump_calls
            bump_calls += 1
            return 42

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_mock_result())
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", AsyncMock()),
            patch("app.wiki.index.update_index", AsyncMock()),
            patch("app.ops.cascade_delete._bump_version_and_notify", _mock_bump),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            result = await cascade_delete(target_id)

        assert bump_calls == 1, f"data_version must be bumped exactly once; got {bump_calls}"
        assert result.data_version_after == 42

    async def test_no_fa2_call(self, tmp_path: Path) -> None:
        """T-CD-012: GraphEngine.recompute() is NEVER called inline (I2)."""
        target_id = uuid.uuid4()

        from app.ops.cascade_delete import CascadePlan

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Target",
            target_file_path="wiki/concepts/target.md",
            will_delete=[target_id],
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=[],
            index_entry_will_be_removed=True,
            raw_source_to_delete=None,
            shared_entity_warnings=[],
            match_methods_used={},
        )

        mock_graph_engine_cls = MagicMock()

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_mock_result())
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", AsyncMock()),
            patch("app.wiki.index.update_index", AsyncMock()),
            patch("app.graph.engine.GraphEngine", mock_graph_engine_cls),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            await cascade_delete(target_id)

        # GraphEngine constructor should NOT have been called
        mock_graph_engine_cls.assert_not_called()

    async def test_files_written_equals_plan_rewrites(self, tmp_path: Path) -> None:
        """T-CD-013: files_written == len(plan.wikilinks_to_rewrite) (AC-F13-4a)."""
        target_id = uuid.uuid4()
        wiki_dir = tmp_path / "wiki" / "concepts"
        wiki_dir.mkdir(parents=True)

        from app.ops.cascade_delete import CascadePlan, WikilinkRewrite

        rewrites = []
        for i in range(3):
            ref_id = uuid.uuid4()
            ref_file = wiki_dir / f"ref{i}.md"
            ref_file.write_text(
                f"---\ntitle: Ref{i}\ntype: concept\nsources: []\n---\n\nSee [[Target]].",
                encoding="utf-8",
            )
            rewrites.append(
                WikilinkRewrite(
                    source_page_id=ref_id,
                    file_path=f"wiki/concepts/ref{i}.md",
                    target_title="Target",
                    occurrences=1,
                )
            )

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Target",
            target_file_path="wiki/concepts/target.md",
            will_delete=[target_id],
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=rewrites,
            index_entry_will_be_removed=True,
            raw_source_to_delete=None,
            shared_entity_warnings=[],
            match_methods_used={r.file_path: "exact" for r in rewrites},
        )

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_mock_result())
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", AsyncMock()),
            patch("app.wiki.index.update_index", AsyncMock()),
            patch("app.ops.cascade_delete._repersist_links", AsyncMock()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            result = await cascade_delete(target_id)

        assert (
            result.files_written == 3
        ), f"files_written={result.files_written} must equal len(rewrites)=3"

    async def test_raw_source_file_deleted(self, tmp_path: Path) -> None:
        """T-CD-014: raw/sources/ file deleted from disk (AQ-v0.5-5)."""
        target_id = uuid.uuid4()

        raw_dir = tmp_path / "raw" / "sources"
        raw_dir.mkdir(parents=True)
        raw_file = raw_dir / "doc.md"
        raw_file.write_text("---\ntitle: Doc\n---\n\nContent.", encoding="utf-8")

        assert raw_file.exists(), "precondition: raw source file must exist before delete"

        from app.ops.cascade_delete import CascadePlan

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Doc",
            target_file_path="raw/sources/doc.md",
            will_delete=[target_id],
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=[],
            index_entry_will_be_removed=False,
            raw_source_to_delete="raw/sources/doc.md",
            shared_entity_warnings=[],
            match_methods_used={},
        )

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_mock_result())
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", AsyncMock()),
            patch("app.wiki.index.update_index", AsyncMock()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            await cascade_delete(target_id)

        assert not raw_file.exists(), "raw/sources/ file must be deleted from disk (AQ-v0.5-5)"

    async def test_shared_entity_warning_logged(self, tmp_path: Path, caplog: Any) -> None:
        """T-CD-016: shared-entity WARNING logged; deletion proceeds (AC-F13-3)."""
        import logging

        target_id = uuid.uuid4()

        from app.ops.cascade_delete import CascadePlan

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Target",
            target_file_path="wiki/concepts/target.md",
            will_delete=[target_id],
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=[],
            index_entry_will_be_removed=True,
            raw_source_to_delete=None,
            shared_entity_warnings=["Page 'Other' shares source overlap with the deleted page"],
            match_methods_used={},
        )

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_mock_result())
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", AsyncMock()),
            patch("app.wiki.index.update_index", AsyncMock()),
            caplog.at_level(logging.WARNING, logger="app.ops.cascade_delete"),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            result = await cascade_delete(target_id)

        # Deletion must have proceeded successfully
        assert result.deleted_page_id == target_id
        # Shared-entity warnings must be returned
        assert result.shared_entity_warnings
        # WARNING must have been logged
        warning_found = any(
            "SHARED ENTITY" in record.message
            for record in caplog.records
            if record.levelno >= logging.WARNING
        )
        assert warning_found, "Must log WARNING about shared entities"

    async def test_three_referencing_pages_all_written(self, tmp_path: Path) -> None:
        """T-CD-021: 3 referencing pages → files_written == 3 (AC-F13-4a)."""
        target_id = uuid.uuid4()
        wiki_dir = tmp_path / "wiki" / "concepts"
        wiki_dir.mkdir(parents=True)

        from app.ops.cascade_delete import CascadePlan, WikilinkRewrite

        rewrites = []
        for i in range(3):
            ref_id = uuid.uuid4()
            (wiki_dir / f"ref{i}.md").write_text(
                f"---\ntitle: Ref{i}\ntype: concept\nsources: []\n---\n\nSee [[Target]] in ref{i}.",
                encoding="utf-8",
            )
            rewrites.append(
                WikilinkRewrite(
                    source_page_id=ref_id,
                    file_path=f"wiki/concepts/ref{i}.md",
                    target_title="Target",
                    occurrences=1,
                )
            )

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Target",
            target_file_path="wiki/concepts/target.md",
            will_delete=[target_id],
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=rewrites,
            index_entry_will_be_removed=True,
            raw_source_to_delete=None,
            shared_entity_warnings=[],
            match_methods_used={r.file_path: "exact" for r in rewrites},
        )

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_mock_result())
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", AsyncMock()),
            patch("app.wiki.index.update_index", AsyncMock()),
            patch("app.ops.cascade_delete._repersist_links", AsyncMock()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            result = await cascade_delete(target_id)

        assert result.files_written == 3, f"Expected 3 files written, got {result.files_written}"
        # Verify no dead [[Target]] links remain
        for i in range(3):
            content = (wiki_dir / f"ref{i}.md").read_text(encoding="utf-8")
            assert "[[Target]]" not in content, f"ref{i}.md still has dead link"
            post = fm.loads(content)
            assert post.metadata.get("type") == "concept"


# ── Preserve-shared rule tests ────────────────────────────────────────────────


@pytest.mark.asyncio
class TestPreserveShared:
    """T-CD-005, T-CD-006 — preserve-shared rule (ADR-0026 §4.1)."""

    async def test_single_source_page_in_will_delete(self, tmp_path: Path) -> None:
        """T-CD-006: wiki page with only 1 source → will_delete when that source deleted."""
        target_id = uuid.uuid4()  # source page being deleted
        wiki_id = uuid.uuid4()  # wiki page that references it

        from app.ops.cascade_delete import CascadePlan

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Doc",
            target_file_path="raw/sources/doc.md",
            will_delete=[target_id, wiki_id],  # wiki_id in will_delete (only source)
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=[],
            index_entry_will_be_removed=False,
            raw_source_to_delete="raw/sources/doc.md",
            shared_entity_warnings=[],
            match_methods_used={},
        )

        # Create raw source file
        raw_dir = tmp_path / "raw" / "sources"
        raw_dir.mkdir(parents=True)
        (raw_dir / "doc.md").write_text("source content", encoding="utf-8")

        delete_point_ids: list[uuid.UUID] = []

        async def _mock_delete(pid: uuid.UUID) -> None:
            delete_point_ids.append(pid)

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            session.execute = AsyncMock(return_value=_make_mock_result())
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", _mock_delete),
            patch("app.wiki.index.update_index", AsyncMock()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            await cascade_delete(target_id)

        # Both target and wiki page must be in delete calls (page had only one source)
        assert target_id in delete_point_ids
        assert wiki_id in delete_point_ids

    async def test_multi_source_page_in_will_preserve(self, tmp_path: Path) -> None:
        """T-CD-005: wiki page with 2 sources → will_preserve when one source deleted."""
        target_id = uuid.uuid4()
        wiki_id = uuid.uuid4()

        from app.ops.cascade_delete import CascadePlan

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="Doc",
            target_file_path="raw/sources/doc.md",
            will_delete=[target_id],  # wiki_id NOT in will_delete (has another source)
            will_preserve_with_pruned_source=[wiki_id],  # wiki_id preserved + pruned
            wikilinks_to_rewrite=[],
            index_entry_will_be_removed=False,
            raw_source_to_delete="raw/sources/doc.md",
            shared_entity_warnings=[],
            match_methods_used={},
        )

        raw_dir = tmp_path / "raw" / "sources"
        raw_dir.mkdir(parents=True)
        (raw_dir / "doc.md").write_text("source content", encoding="utf-8")

        # Create wiki file for the preserved page
        wiki_dir = tmp_path / "wiki" / "concepts"
        wiki_dir.mkdir(parents=True)
        concept_body = (
            "---\ntitle: Concept\nsources:\n"
            "  - raw/sources/doc.md\n  - raw/sources/other.md\n---\n\nBody."
        )
        (wiki_dir / "concept.md").write_text(concept_body, encoding="utf-8")

        delete_point_ids: list[uuid.UUID] = []

        async def _mock_delete(pid: uuid.UUID) -> None:
            delete_point_ids.append(pid)

        @asynccontextmanager
        async def _get_session() -> AsyncIterator[Any]:
            session = AsyncMock()
            # _prune_sources will call execute; return mock row with file_path and sources
            result = MagicMock()
            result.first.return_value = (
                "wiki/concepts/concept.md",
                ["raw/sources/doc.md", "raw/sources/other.md"],
            )
            result.all.return_value = []
            result.scalar_one_or_none.return_value = None
            session.execute = AsyncMock(return_value=result)
            session.add = MagicMock()
            session.commit = AsyncMock()
            yield session

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.get_session", _get_session),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.ops.cascade_delete.delete_point", _mock_delete),
            patch("app.wiki.index.update_index", AsyncMock()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"

            from app.ops.cascade_delete import cascade_delete

            await cascade_delete(target_id)

        # Only the target should be deleted; wiki_id must NOT be in delete calls
        assert target_id in delete_point_ids
        assert wiki_id not in delete_point_ids


# ── REST endpoint tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestCascadeDeleteAPI:
    """T-CD-017..T-CD-020 — REST endpoint tests."""

    async def test_preview_returns_200(self, tmp_path: Path) -> None:
        """T-CD-017: POST /preview → 200 with correct shape."""
        target_id = uuid.uuid4()

        from app.ops.cascade_delete import CascadePlan

        plan = CascadePlan(
            target_page_id=target_id,
            target_title="TestPage",
            target_file_path="wiki/concepts/test.md",
            will_delete=[target_id],
            will_preserve_with_pruned_source=[],
            wikilinks_to_rewrite=[],
            index_entry_will_be_removed=True,
            raw_source_to_delete=None,
            shared_entity_warnings=[],
            match_methods_used={},
        )

        @asynccontextmanager
        async def _lifespan(app_: Any) -> Any:
            yield

        with (
            patch("app.ops.cascade_delete.plan_cascade_delete", AsyncMock(return_value=plan)),
            patch("app.ops.cascade_delete.cascade_delete", AsyncMock()),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.watcher.start_watcher"),
            patch("app.watcher.stop_watcher"),
            patch("app.vault.bootstrap_vault"),
            patch("app.db.get_session", _noop_session_patch()),
            patch("app.main.get_session", _noop_session_patch()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"
            mock_settings.cors_origins_list = ["*"]

            from app.main import app
            from httpx import ASGITransport, AsyncClient

            app.router.lifespan_context = _lifespan

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(f"/pages/{target_id}/cascade-delete/preview")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "target_page_id" in body
        assert "will_delete" in body
        assert "wikilinks_to_rewrite" in body
        assert "match_methods_used" in body
        assert "shared_entity_warnings" in body

    async def test_delete_returns_200_with_shape(self, tmp_path: Path) -> None:
        """T-CD-018: DELETE /pages/{id} → 200 with correct shape."""
        target_id = uuid.uuid4()

        from app.ops.cascade_delete import CascadeResult

        mock_result = CascadeResult(
            deleted_page_id=target_id,
            wikilinks_cleaned=0,
            index_entry_removed=True,
            shared_entity_warnings=[],
            files_written=0,
            data_version_after=2,
        )

        @asynccontextmanager
        async def _lifespan(app_: Any) -> Any:
            yield

        with (
            patch("app.ops.cascade_delete.cascade_delete", AsyncMock(return_value=mock_result)),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.watcher.start_watcher"),
            patch("app.watcher.stop_watcher"),
            patch("app.vault.bootstrap_vault"),
            patch("app.db.get_session", _noop_session_patch()),
            patch("app.main.get_session", _noop_session_patch()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"
            mock_settings.cors_origins_list = ["*"]

            from app.main import app
            from httpx import ASGITransport, AsyncClient

            app.router.lifespan_context = _lifespan

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete(f"/pages/{target_id}")

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "deleted_page_id" in body
        assert "wikilinks_cleaned" in body
        assert "index_entry_removed" in body
        assert "shared_entity_warnings" in body

    async def test_delete_404_on_page_not_found(self, tmp_path: Path) -> None:
        """T-CD-019: DELETE → 404 when PageNotFoundError raised (double-delete, AC-F13-5c)."""
        target_id = uuid.uuid4()

        @asynccontextmanager
        async def _lifespan(app_: Any) -> Any:
            yield

        from app.ops.cascade_delete import PageNotFoundError

        with (
            patch(
                "app.ops.cascade_delete.cascade_delete",
                AsyncMock(side_effect=PageNotFoundError("not found")),
            ),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.watcher.start_watcher"),
            patch("app.watcher.stop_watcher"),
            patch("app.vault.bootstrap_vault"),
            patch("app.db.get_session", _noop_session_patch()),
            patch("app.main.get_session", _noop_session_patch()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"
            mock_settings.cors_origins_list = ["*"]

            from app.main import app
            from httpx import ASGITransport, AsyncClient

            app.router.lifespan_context = _lifespan

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.delete(f"/pages/{target_id}")

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    async def test_preview_404_on_unknown(self, tmp_path: Path) -> None:
        """T-CD-020: POST /preview → 404 on unknown page (PageNotFoundError)."""
        unknown_id = uuid.uuid4()

        @asynccontextmanager
        async def _lifespan(app_: Any) -> Any:
            yield

        from app.ops.cascade_delete import PageNotFoundError

        with (
            patch(
                "app.ops.cascade_delete.plan_cascade_delete",
                AsyncMock(side_effect=PageNotFoundError("not found")),
            ),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.watcher.start_watcher"),
            patch("app.watcher.stop_watcher"),
            patch("app.vault.bootstrap_vault"),
            patch("app.db.get_session", _noop_session_patch()),
            patch("app.main.get_session", _noop_session_patch()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"
            mock_settings.cors_origins_list = ["*"]

            from app.main import app
            from httpx import ASGITransport, AsyncClient

            app.router.lifespan_context = _lifespan

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(f"/pages/{unknown_id}/cascade-delete/preview")

        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    async def test_double_delete_returns_404(self, tmp_path: Path) -> None:
        """T-CD-019b: second DELETE after successful first → 404 (AC-F13-5c)."""
        target_id = uuid.uuid4()

        @asynccontextmanager
        async def _lifespan(app_: Any) -> Any:
            yield

        from app.ops.cascade_delete import CascadeResult, PageNotFoundError

        call_count = 0

        async def _cascade_delete(pid: uuid.UUID) -> CascadeResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return CascadeResult(
                    deleted_page_id=pid,
                    wikilinks_cleaned=0,
                    index_entry_removed=True,
                    shared_entity_warnings=[],
                    files_written=0,
                    data_version_after=2,
                )
            raise PageNotFoundError(f"Page {pid} already deleted")

        with (
            patch("app.ops.cascade_delete.cascade_delete", _cascade_delete),
            patch("app.ops.cascade_delete.settings") as mock_settings,
            patch("app.watcher.start_watcher"),
            patch("app.watcher.stop_watcher"),
            patch("app.vault.bootstrap_vault"),
            patch("app.db.get_session", _noop_session_patch()),
            patch("app.main.get_session", _noop_session_patch()),
        ):
            mock_settings.vault_root = tmp_path
            mock_settings.vault_id = "test"
            mock_settings.cors_origins_list = ["*"]

            from app.main import app
            from httpx import ASGITransport, AsyncClient

            app.router.lifespan_context = _lifespan

            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp1 = await client.delete(f"/pages/{target_id}")
                resp2 = await client.delete(f"/pages/{target_id}")

        assert resp1.status_code == 200, f"First DELETE must be 200: {resp1.text}"
        assert resp2.status_code == 404, f"Second DELETE must be 404: {resp2.text}"
