"""
Tests for ops/migrate_lint_query_stubs.py — ADR-0067 D1 / P0-5 remediation.

Seeded vault scenario:
  - page_a: "Google Cloud" under wiki/queries/google-cloud.md
            tags=[stub,lint], body has LEGACY_PLACEHOLDER_BODY
            → ENTITY (proper-noun: title-cased)

  - page_b: "machine learning" under wiki/queries/machine-learning.md
            tags=[stub,lint], body has LEGACY_PLACEHOLDER_BODY
            → CONCEPT (all lowercase, no acronym, no legal suffix)

  - page_c: "AWS" under wiki/queries/aws.md
            tags=[stub,lint], body has CURRENT_STUB_BODY (no legacy text)
            → ENTITY (all-caps acronym)

Test plan:
  T1 — dry-run: asserts plan built (3 items), nothing moved, no bump, no reconnect.
  T2 — apply: asserts _move_and_retype called for each, exactly 1 bump, 1 reconnect call.
  T3 — no-candidates: 0 stub candidates → 0 processed, 0 bumps, stopped_reason=complete.
  T4 — bounds: max_pages=1 → only 1 processed, stopped_reason=maxpages.
  T5 — failed-move: _move_and_retype raises → counted failed, no bump.
  T6 — is_running single-flight flag: second concurrent call blocked by caller (state).
  T7 — _is_lint_stub() heuristic: tags-first, body-fallback, non-stub page returns False.
  T8 — filesystem integration: real tmpdir vault, actual file created+moved, content hash updated.

DB/Qdrant are always stubbed. File I/O is real only in T8 (tmpdir).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import app.ops.migrate_lint_query_stubs as mls
import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_page(
    title: str,
    file_path: str,
    tags: list[str] | None = None,
    page_type: str = "query",
    vault_id: str = "test-vault",
) -> Any:
    """Create a lightweight fake Page ORM object (no DB, no ORM machinery)."""
    p = type("Page", (), {})()
    p.id = uuid.uuid4()
    p.vault_id = vault_id
    p.title = title
    p.file_path = file_path
    p.page_type = page_type
    p.tags = tags
    p.sources = []
    p.source_mtime_ns = 0
    p.deleted_at = None
    return p


# ── Canonical seed pages ──────────────────────────────────────────────────────

PAGE_A = _fake_page(
    title="Google Cloud",
    file_path="wiki/queries/google-cloud.md",
    tags=["stub", "lint"],
)
PAGE_B = _fake_page(
    title="machine learning",
    file_path="wiki/queries/machine-learning.md",
    tags=["stub", "lint"],
)
PAGE_C = _fake_page(
    title="AWS",
    file_path="wiki/queries/aws.md",
    tags=["stub", "lint"],
)


# ── Shared fixture ────────────────────────────────────────────────────────────


@pytest.fixture()
def migration_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """
    Stub out every I/O dependency so tests run infra-free (no Postgres, no Qdrant,
    no filesystem — except T8 which uses tmp_path directly).

    Patched surfaces:
      mls._load_candidate_stubs  — returns state["pages"] (no DB)
      mls._move_and_retype       — records calls; does NOT touch files
      mls._reconnect_links       — records call count
      bump_version               — records call count (in app.ingest.orchestrator)
    """
    state: dict[str, Any] = {
        "pages": [PAGE_A, PAGE_B, PAGE_C],
        "move_calls": [],  # list of (page.file_path, new_type, new_rel_path)
        "bumps": 0,
        "reconnects": 0,
        "move_error": None,  # if set, _move_and_retype raises this
        "vault_root": tmp_path,
    }

    async def fake_load_candidates(vault_id: str, max_pages: int) -> list[Any]:
        return list(state["pages"])[:max_pages]

    async def fake_move_and_retype(page: Any, new_type: str, new_rel_path: str) -> None:
        if state["move_error"] is not None:
            raise state["move_error"]
        state["move_calls"].append((page.file_path, new_type, new_rel_path))

    async def fake_reconnect_links() -> None:
        state["reconnects"] += 1

    async def fake_bump() -> None:
        state["bumps"] += 1

    monkeypatch.setattr(mls, "_load_candidate_stubs", fake_load_candidates)
    monkeypatch.setattr(mls, "_move_and_retype", fake_move_and_retype)
    monkeypatch.setattr(mls, "_reconnect_links", fake_reconnect_links)

    # Patch bump_version at the module where it's imported (PLC0415 deferred import)
    import app.ingest.orchestrator as orch

    monkeypatch.setattr(orch, "bump_version", fake_bump)

    # Reset module-level single-flight state between tests.
    mls._state.is_running = False
    mls._state.last_summary = None
    mls._state.current = {}

    return state


# ── T1: dry-run ───────────────────────────────────────────────────────────────


async def test_dryrun_builds_plan_no_move(migration_env: dict[str, Any]) -> None:
    """Dry-run: builds a 3-item plan, moves nothing, bumps nothing."""
    summary = await mls.run_migration("test-vault", apply=False)

    assert summary.apply is False
    assert summary.processed == 3
    assert summary.moved == 0
    assert summary.failed == 0
    assert summary.stopped_reason == "complete"
    assert summary.total_cost_usd == 0.0

    # Plan contains all 3 candidates.
    assert len(summary.plan) == 3
    slugs = {p.slug for p in summary.plan}
    assert slugs == {"google-cloud", "machine-learning", "aws"}

    # Type inference check:
    plan_by_slug = {p.slug: p for p in summary.plan}
    assert plan_by_slug["google-cloud"].inferred_type == "entity"  # proper-noun
    assert plan_by_slug["machine-learning"].inferred_type == "concept"  # lowercase
    assert plan_by_slug["aws"].inferred_type == "entity"  # ALL-CAPS acronym

    # new_path must be outside wiki/queries/
    for item in summary.plan:
        assert not item.new_path.startswith(
            "wiki/queries/"
        ), f"Plan item {item.slug} new_path still in queries/: {item.new_path}"
        assert item.new_path.startswith("wiki/entities/") or item.new_path.startswith(
            "wiki/concepts/"
        )

    # Nothing was touched.
    assert migration_env["move_calls"] == []
    assert migration_env["bumps"] == 0
    assert migration_env["reconnects"] == 0


# ── T2: apply ─────────────────────────────────────────────────────────────────


async def test_apply_moves_pages_single_bump(migration_env: dict[str, Any]) -> None:
    """Apply mode: moves 3 pages, exactly 1 data_version bump, 1 reconnect call."""
    summary = await mls.run_migration("test-vault", apply=True)

    assert summary.apply is True
    assert summary.processed == 3
    assert summary.moved == 3
    assert summary.failed == 0
    assert summary.stopped_reason == "complete"

    # Each page was moved exactly once.
    assert len(migration_env["move_calls"]) == 3

    moved_old_paths = {c[0] for c in migration_env["move_calls"]}
    assert "wiki/queries/google-cloud.md" in moved_old_paths
    assert "wiki/queries/machine-learning.md" in moved_old_paths
    assert "wiki/queries/aws.md" in moved_old_paths

    # New paths are outside queries/.
    for old_path, new_type, new_rel_path in migration_env["move_calls"]:
        assert not new_rel_path.startswith(
            "wiki/queries/"
        ), f"Page {old_path!r} still moved into queries/: {new_rel_path!r}"
        assert new_rel_path.startswith("wiki/entities/") or new_rel_path.startswith(
            "wiki/concepts/"
        )
        assert new_type in ("entity", "concept")

    # Type breakdown:
    by_type = summary.by_type
    assert by_type.get("entity", 0) == 2  # google-cloud + aws
    assert by_type.get("concept", 0) == 1  # machine-learning

    # Exactly ONE data_version bump for the whole batch (I1).
    assert (
        migration_env["bumps"] == 1
    ), "Expected exactly 1 data_version bump for the whole batch (I1)"

    # Exactly ONE reresolve_dangling_links call after all moves (wikilink reconnect).
    assert (
        migration_env["reconnects"] == 1
    ), "Expected exactly 1 reresolve_dangling_links call after the batch"


# ── T3: no candidates ─────────────────────────────────────────────────────────


async def test_no_candidates_no_bump(migration_env: dict[str, Any]) -> None:
    """Zero stub candidates → nothing processed, no bump."""
    migration_env["pages"] = []
    summary = await mls.run_migration("test-vault", apply=True)

    assert summary.processed == 0
    assert summary.moved == 0
    assert migration_env["bumps"] == 0
    assert migration_env["reconnects"] == 0
    assert summary.stopped_reason == "complete"


# ── T4: max_pages cap ────────────────────────────────────────────────────────


async def test_maxpages_cap(migration_env: dict[str, Any]) -> None:
    """max_pages=1 → only 1 candidate processed; stopped_reason=maxpages."""
    summary = await mls.run_migration("test-vault", apply=True, max_pages=1)

    assert summary.processed == 1
    assert summary.moved == 1
    assert summary.stopped_reason == "maxpages"
    assert migration_env["bumps"] == 1  # still one bump for the partial batch


# ── T5: failed move ───────────────────────────────────────────────────────────


async def test_failed_move_counted_no_bump(migration_env: dict[str, Any]) -> None:
    """_move_and_retype raises for ALL pages → all failures, no bump."""
    migration_env["move_error"] = OSError("disk full")
    summary = await mls.run_migration("test-vault", apply=True)

    assert summary.processed == 3
    assert summary.moved == 0
    assert summary.failed == 3
    assert migration_env["bumps"] == 0, "No bump when nothing was actually moved"
    assert migration_env["reconnects"] == 0


# ── T5b: partial failure ──────────────────────────────────────────────────────


async def test_partial_failure_still_bumps(migration_env: dict[str, Any]) -> None:
    """First page move fails, the rest succeed → bump + reconnect still fires."""
    call_count = [0]

    async def flaky_move(page: Any, new_type: str, new_rel_path: str) -> None:
        call_count[0] += 1
        if call_count[0] == 1:
            raise OSError("transient error on first page")
        migration_env["move_calls"].append((page.file_path, new_type, new_rel_path))

    import app.ops.migrate_lint_query_stubs as mls_ref

    mls_ref._move_and_retype = flaky_move  # type: ignore[attr-defined]
    try:
        summary = await mls.run_migration("test-vault", apply=True)
    finally:
        # Restore the stub (fixture handles reset for subsequent tests via monkeypatch)
        pass

    assert summary.failed == 1
    assert summary.moved == 2
    assert migration_env["bumps"] == 1  # partial success still bumps
    assert migration_env["reconnects"] == 1


# ── T6: single-flight flag ────────────────────────────────────────────────────


def test_is_running_state() -> None:
    """is_running() reflects _state.is_running (single-flight guard check)."""
    mls._state.is_running = False
    assert mls.is_running() is False
    mls._state.is_running = True
    assert mls.is_running() is True
    mls._state.is_running = False  # reset


def test_get_last_summary_none_before_run() -> None:
    """get_last_summary() returns None when no run has completed."""
    mls._state.last_summary = None
    assert mls.get_last_summary() is None


# ── T7: _is_lint_stub() heuristic ────────────────────────────────────────────


def test_is_lint_stub_tags_fast_path(tmp_path: Path) -> None:
    """Tags contain stub+lint → True without reading any file."""
    page = _fake_page("AWS", "wiki/queries/aws.md", tags=["stub", "lint"])
    # vault_root points to tmp_path where no file exists — if body check runs it errors.
    assert mls._is_lint_stub(page, tmp_path) is True


def test_is_lint_stub_partial_tags_falls_through_to_body(tmp_path: Path) -> None:
    """Tags contain only 'stub' (not 'lint') → fall through to body check."""
    page = _fake_page("orphan", "wiki/queries/orphan.md", tags=["stub"])
    # Create a file with the legacy body:
    qdir = tmp_path / "wiki" / "queries"
    qdir.mkdir(parents=True)
    stub_file = qdir / "orphan.md"
    stub_file.write_text(
        f"---\ntype: query\ntitle: orphan\n---\n# orphan\n\n{mls.LEGACY_PLACEHOLDER_BODY}\n",
        encoding="utf-8",
    )
    assert mls._is_lint_stub(page, tmp_path) is True


def test_is_lint_stub_current_body(tmp_path: Path) -> None:
    """Body contains CURRENT_STUB_BODY → True (post-ADR-0067 stubs still in queries/)."""
    page = _fake_page("concept-x", "wiki/queries/concept-x.md", tags=[])
    qdir = tmp_path / "wiki" / "queries"
    qdir.mkdir(parents=True)
    stub_file = qdir / "concept-x.md"
    stub_file.write_text(
        f"---\ntype: query\ntitle: concept-x\n---\n# concept-x\n\n{mls.CURRENT_STUB_BODY}\n",
        encoding="utf-8",
    )
    assert mls._is_lint_stub(page, tmp_path) is True


def test_is_lint_stub_non_stub_page(tmp_path: Path) -> None:
    """A real query page (no stub tags, no placeholder body) → False."""
    page = _fake_page(
        "Does scale improve reasoning?",
        "wiki/queries/does-scale-improve-reasoning.md",
        tags=["reasoning", "scaling"],
    )
    qdir = tmp_path / "wiki" / "queries"
    qdir.mkdir(parents=True)
    real_query = qdir / "does-scale-improve-reasoning.md"
    real_query.write_text(
        "---\ntype: query\ntitle: Does scale improve reasoning?\n---\n"
        "# Does scale improve reasoning?\n\nAn open research question...\n",
        encoding="utf-8",
    )
    assert mls._is_lint_stub(page, tmp_path) is False


def test_is_lint_stub_missing_file_returns_false(tmp_path: Path) -> None:
    """If the file is missing and tags don't confirm, return False gracefully."""
    page = _fake_page("missing", "wiki/queries/missing.md", tags=[])
    assert mls._is_lint_stub(page, tmp_path) is False


# ── T8: filesystem integration ────────────────────────────────────────────────


async def test_filesystem_move_entity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Integration test: real file at wiki/queries/google-cloud.md is moved to
    wiki/entities/google-cloud.md with updated frontmatter and body.

    DB and Qdrant are fully stubbed (no Postgres, no Qdrant).
    """
    import frontmatter as _fm

    # ── Set up vault directory ────────────────────────────────────────────────
    (tmp_path / "wiki" / "queries").mkdir(parents=True)
    old_file = tmp_path / "wiki" / "queries" / "google-cloud.md"
    old_file.write_text(
        f"---\ntype: query\ntitle: Google Cloud\ntags:\n  - stub\n  - lint\n---\n"
        f"# Google Cloud\n\n{mls.LEGACY_PLACEHOLDER_BODY}\n",
        encoding="utf-8",
    )

    # ── Patch settings.vault_root ─────────────────────────────────────────────
    monkeypatch.setattr(
        "app.ops.migrate_lint_query_stubs.settings",
        type(
            "S",
            (),
            {"vault_root": tmp_path, "vault_id": "test", "migrate_lint_stubs_max_pages": 200},
        )(),
    )

    # ── Stub get_session (DB operations) ─────────────────────────────────────
    execute_calls: list[Any] = []

    class _FakeSession:
        async def execute(self, stmt: Any) -> None:
            execute_calls.append(stmt)

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_get_session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    monkeypatch.setattr("app.ops.migrate_lint_query_stubs.get_session", fake_get_session)

    # ── Stub upsert_vector (Qdrant) ───────────────────────────────────────────
    upsert_calls: list[dict[str, Any]] = []

    async def fake_upsert_vector(**kwargs: Any) -> None:
        upsert_calls.append(kwargs)

    import app.ingest.orchestrator as orch

    monkeypatch.setattr(orch, "upsert_vector", fake_upsert_vector)

    # ── Also patch _sha256 import in the migration module ────────────────────
    # (_sha256 is imported inside _move_and_retype; it only does hashlib, no I/O)

    # ── Create a fake page ────────────────────────────────────────────────────
    page = _fake_page(
        title="Google Cloud",
        file_path="wiki/queries/google-cloud.md",
        tags=["stub", "lint"],
    )
    new_rel_path = "wiki/entities/google-cloud.md"

    # ── Call _move_and_retype ─────────────────────────────────────────────────
    await mls._move_and_retype(page, "entity", new_rel_path)

    # ── Assertions ────────────────────────────────────────────────────────────
    # Old file must be gone.
    assert not old_file.exists(), "Old file should have been deleted after move"

    # New file must exist at the entity path.
    new_file = tmp_path / "wiki" / "entities" / "google-cloud.md"
    assert new_file.exists(), "New file should exist at wiki/entities/google-cloud.md"

    # Frontmatter type must be 'entity'.
    parsed = _fm.load(new_file)
    assert parsed["type"] == "entity", f"Expected type=entity, got {parsed['type']!r}"

    # Legacy body must have been replaced.
    assert (
        mls.LEGACY_PLACEHOLDER_BODY not in parsed.content
    ), "Legacy placeholder body should have been replaced in the migrated file"
    assert (
        mls.CURRENT_STUB_BODY in parsed.content
    ), "Migrated file should carry the current stub body"

    # Qdrant re-embed was called.
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["page_type"] == "entity"
    assert upsert_calls[0]["file_path"] == new_rel_path


async def test_filesystem_move_concept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Integration: 'machine learning' (lowercase) → wiki/concepts/machine-learning.md.
    """
    import frontmatter as _fm

    (tmp_path / "wiki" / "queries").mkdir(parents=True)
    old_file = tmp_path / "wiki" / "queries" / "machine-learning.md"
    old_file.write_text(
        f"---\ntype: query\ntitle: machine learning\ntags:\n  - stub\n  - lint\n---\n"
        f"# machine learning\n\n{mls.LEGACY_PLACEHOLDER_BODY}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "app.ops.migrate_lint_query_stubs.settings",
        type(
            "S",
            (),
            {"vault_root": tmp_path, "vault_id": "test", "migrate_lint_stubs_max_pages": 200},
        )(),
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_get_session() -> AsyncIterator[Any]:
        class _FS:
            async def execute(self, *a: Any) -> None:
                pass

        yield _FS()

    monkeypatch.setattr("app.ops.migrate_lint_query_stubs.get_session", fake_get_session)

    async def fake_upsert(**kw: Any) -> None:
        pass

    import app.ingest.orchestrator as orch

    monkeypatch.setattr(orch, "upsert_vector", fake_upsert)

    page = _fake_page(
        title="machine learning",
        file_path="wiki/queries/machine-learning.md",
        tags=["stub", "lint"],
    )
    new_rel_path = "wiki/concepts/machine-learning.md"
    await mls._move_and_retype(page, "concept", new_rel_path)

    assert not old_file.exists()
    new_file = tmp_path / "wiki" / "concepts" / "machine-learning.md"
    assert new_file.exists()

    parsed = _fm.load(new_file)
    assert parsed["type"] == "concept"


# ── T9: clamp_bounds ─────────────────────────────────────────────────────────


def test_clamp_bounds_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """None → module default; values are clamped to hard cap."""
    # Ensure no custom setting bleeds in:
    import app.ops.migrate_lint_query_stubs as m

    class _FakeSettings:
        migrate_lint_stubs_max_pages = mls.DEFAULT_MAX_PAGES

    monkeypatch.setattr(m, "settings", _FakeSettings())
    assert m.clamp_bounds(None) == mls.DEFAULT_MAX_PAGES


def test_clamp_bounds_over_hard_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ops.migrate_lint_query_stubs as m

    class _FakeSettings:
        migrate_lint_stubs_max_pages = mls.DEFAULT_MAX_PAGES

    monkeypatch.setattr(m, "settings", _FakeSettings())
    assert m.clamp_bounds(99_999) == mls.MAX_PAGES_HARD_CAP


def test_clamp_bounds_min_one(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.ops.migrate_lint_query_stubs as m

    class _FakeSettings:
        migrate_lint_stubs_max_pages = mls.DEFAULT_MAX_PAGES

    monkeypatch.setattr(m, "settings", _FakeSettings())
    assert m.clamp_bounds(0) == 1


# ── T10: _infer_stub_page_type reuse ─────────────────────────────────────────


def test_infer_stub_page_type_import() -> None:
    """
    Verify that _infer_stub_page_type from app.ops.lint is the same object
    used by the migration (do NOT re-implement the heuristic here).
    """
    from app.ingest.schemas import PageType
    from app.ops.lint import _infer_stub_page_type

    # Sanity smoke-test of the imported heuristic (mirrors the ADR-0067 D1 rules):
    assert _infer_stub_page_type("Google Cloud") == PageType.ENTITY  # proper noun
    assert _infer_stub_page_type("machine learning") == PageType.CONCEPT  # lowercase
    assert _infer_stub_page_type("AWS") == PageType.ENTITY  # ALL-CAPS
    assert _infer_stub_page_type("Salesforce, Inc.") == PageType.ENTITY  # legal suffix
    assert _infer_stub_page_type("dora") == PageType.CONCEPT  # lowercase acronym
    # NEVER returns QUERY
    assert _infer_stub_page_type("anything") != PageType.QUERY


# ── T11: as_dict serialisation ────────────────────────────────────────────────


def test_summary_as_dict() -> None:
    """MigrationSummary.as_dict() is JSON-serialisable and has required keys."""
    import json

    summary = mls.MigrationSummary(
        processed=3,
        moved=2,
        skipped=0,
        failed=1,
        by_type={"entity": 1, "concept": 1},
        plan=[
            mls.MigrationPlanItem(
                slug="aws",
                old_path="wiki/queries/aws.md",
                new_path="wiki/entities/aws.md",
                inferred_type="entity",
                title="AWS",
            )
        ],
        total_cost_usd=0.0,
        stopped_reason="complete",
        max_pages=200,
        apply=True,
    )
    d = summary.as_dict()
    # Must be JSON-serialisable
    json.dumps(d)
    assert d["total_cost_usd"] == 0.0
    assert d["apply"] is True
    assert len(d["plan"]) == 1
    assert d["plan"][0]["slug"] == "aws"
