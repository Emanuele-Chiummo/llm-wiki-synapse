"""
Tests for ops/reconcile_folders.py — folder vs. type reconcile sweep [K1,I1].

Seeded vault scenario:
  - PAGE_A: type="entity"  at wiki/concepts/aws.md
            → folder mismatch (should be wiki/entities/aws.md)

  - PAGE_B: type="concept" at wiki/entities/machine-learning.md
            → folder mismatch (should be wiki/concepts/machine-learning.md)

  - PAGE_C: type="source"  at wiki/sources/paper.md
            → CORRECT (should be wiki/sources/paper.md — no move)

Test plan:
  T1  — dry-run: 3 candidates loaded (2 mismatches + 1 correct).
        Plan has 2 items (the two mismatches); nothing moved; no bump; no reconnect.
  T2  — apply: 2 pages moved, exactly 1 data_version bump, 1 reresolve_dangling_links call.
  T3  — no candidates (0 mismatches → 0 processed, 0 bumps, stopped_reason=complete).
  T4  — maxpages cap: SQL returns max_pages rows (hit_sql_limit=True) → stopped_reason=maxpages.
  T5  — idempotent: second run returns 0 candidates → 0 moves, 0 bumps.
  T6  — collision_skip: _move_page raises FileExistsError → collision_skips++, no bump.
  T7  — failed_move: _move_page raises OSError → failed++, no bump.
  T8  — partial failure: first page fails, rest succeed → bump + reconnect still fires.
  T9  — single-flight flag: is_running() / get_last_summary() contract.
  T10 — clamp_bounds: default/over-cap/zero handling.
  T11 — _folder_from_path helper: extraction logic.
  T12 — filesystem integration: real tmpdir, file moved to correct folder, DB + Qdrant stubbed.
  T13 — wikilinks reconnected: reresolve_dangling_links called exactly once after batch.
  T14 — as_dict serialisable: ReconcileSummary.as_dict() is JSON-serialisable.
  T15 — correct-folder pages skip-counted in _run_inner defence-in-depth.

DB and Qdrant are ALWAYS stubbed. File I/O is real only in T12 (uses tmp_path).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import app.ops.reconcile_folders as rf
import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_page(
    title: str,
    file_path: str,
    page_type: str,
    vault_id: str = "test-vault",
) -> Any:
    """Lightweight fake Page ORM object (no DB, no ORM machinery)."""
    p = type("Page", (), {})()
    p.id = uuid.uuid4()
    p.vault_id = vault_id
    p.title = title
    p.file_path = file_path
    p.page_type = page_type
    p.sources = []
    p.tags = []
    p.deleted_at = None
    return p


# ── Canonical seed pages ──────────────────────────────────────────────────────

# PAGE_A: entity living in concepts/ → MISMATCH → target: wiki/entities/aws.md
PAGE_A = _fake_page(title="AWS", file_path="wiki/concepts/aws.md", page_type="entity")
# PAGE_B: concept living in entities/ → MISMATCH → target: wiki/concepts/machine-learning.md
PAGE_B = _fake_page(
    title="Machine Learning",
    file_path="wiki/entities/machine-learning.md",
    page_type="concept",
)
# PAGE_C: source already in sources/ → CORRECT (should be filtered out by _load_candidates)
PAGE_C = _fake_page(title="Research Paper", file_path="wiki/sources/paper.md", page_type="source")

# Only the two mismatch pages (pre-filtered list returned by the stub)
_MISMATCHES = [PAGE_A, PAGE_B]


# ── Shared fixture ────────────────────────────────────────────────────────────


@pytest.fixture()
def reconcile_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Stub every I/O dependency so tests run infra-free (no Postgres, no Qdrant, no
    real filesystem — except T12 which uses tmp_path directly).

    Patched surfaces:
      rf._load_candidates  — returns (state["candidates"], state["hit_sql_limit"])
      rf._move_page        — records calls; does NOT touch files; can raise
      rf._reconnect_links  — records call count
      bump_version         — records call count (patched at app.ingest.orchestrator)
    """
    state: dict[str, Any] = {
        # _load_candidates stub: list of (candidate, hit_limit)
        "candidates": list(_MISMATCHES),
        "hit_sql_limit": False,
        # per-call interception lists
        "move_calls": [],  # list of (old_path, new_rel_path)
        "bumps": 0,
        "reconnects": 0,
        # if set, _move_page raises this exception
        "move_error": None,
    }

    async def fake_load_candidates(vault_id: str, max_pages: int) -> tuple[list[Any], bool]:
        pages = list(state["candidates"])[:max_pages]
        hit = state["hit_sql_limit"] or (len(state["candidates"]) > max_pages)
        return pages, hit

    async def fake_move_page(page: Any, new_rel_path: str) -> None:
        if state["move_error"] is not None:
            raise state["move_error"]
        state["move_calls"].append((page.file_path, new_rel_path))

    async def fake_reconnect_links() -> None:
        state["reconnects"] += 1

    async def fake_bump() -> None:
        state["bumps"] += 1

    monkeypatch.setattr(rf, "_load_candidates", fake_load_candidates)
    monkeypatch.setattr(rf, "_move_page", fake_move_page)
    monkeypatch.setattr(rf, "_reconnect_links", fake_reconnect_links)

    # Patch bump_version where it is imported (deferred inside _run_inner → PLC0415)
    import app.ingest.orchestrator as orch

    monkeypatch.setattr(orch, "bump_version", fake_bump)

    # Reset module-level single-flight state between tests.
    rf._state.is_running = False
    rf._state.last_summary = None
    rf._state.current = {}

    return state


# ── T1: dry-run ───────────────────────────────────────────────────────────────


async def test_dryrun_builds_plan_no_move(reconcile_env: dict[str, Any]) -> None:
    """Dry-run: plan built for 2 mismatch pages; nothing moved; no bump; no reconnect."""
    summary = await rf.run_reconcile("test-vault", apply=False)

    assert summary.apply is False
    assert summary.processed == 2
    assert summary.moved == 0
    assert summary.failed == 0
    assert summary.collision_skips == 0
    assert summary.stopped_reason == "complete"
    assert summary.total_cost_usd == 0.0

    # Plan contains both mismatch candidates.
    assert len(summary.plan) == 2
    slugs = {p.slug for p in summary.plan}
    assert slugs == {"aws", "machine-learning"}

    # Verify target paths are in the correct folders (not the original wrong ones).
    plan_by_slug = {p.slug: p for p in summary.plan}
    assert plan_by_slug["aws"].new_path == "wiki/entities/aws.md"
    assert plan_by_slug["aws"].old_path == "wiki/concepts/aws.md"
    assert plan_by_slug["machine-learning"].new_path == "wiki/concepts/machine-learning.md"
    assert plan_by_slug["machine-learning"].old_path == "wiki/entities/machine-learning.md"

    # Type echoed correctly.
    assert plan_by_slug["aws"].page_type == "entity"
    assert plan_by_slug["machine-learning"].page_type == "concept"

    # Nothing was touched.
    assert reconcile_env["move_calls"] == []
    assert reconcile_env["bumps"] == 0
    assert reconcile_env["reconnects"] == 0


# ── T2: apply ─────────────────────────────────────────────────────────────────


async def test_apply_moves_pages_single_bump(reconcile_env: dict[str, Any]) -> None:
    """Apply: 2 pages moved, exactly 1 data_version bump, 1 reresolve call."""
    summary = await rf.run_reconcile("test-vault", apply=True)

    assert summary.apply is True
    assert summary.processed == 2
    assert summary.moved == 2
    assert summary.failed == 0
    assert summary.stopped_reason == "complete"

    # Both pages were moved.
    moved_old = {c[0] for c in reconcile_env["move_calls"]}
    assert "wiki/concepts/aws.md" in moved_old
    assert "wiki/entities/machine-learning.md" in moved_old

    # New paths are in the correct folders.
    for old_path, new_rel_path in reconcile_env["move_calls"]:
        if "aws" in old_path:
            assert new_rel_path == "wiki/entities/aws.md"
        elif "machine-learning" in old_path:
            assert new_rel_path == "wiki/concepts/machine-learning.md"

    # by_folder breakdown.
    assert summary.by_folder.get("entities", 0) == 1
    assert summary.by_folder.get("concepts", 0) == 1

    # Exactly ONE data_version bump for the whole batch (I1).
    assert reconcile_env["bumps"] == 1, "Expected exactly 1 data_version bump (I1)"

    # Exactly ONE reresolve_dangling_links call after all moves (K5).
    assert reconcile_env["reconnects"] == 1, "Expected exactly 1 reresolve_dangling_links call"


# ── T3: no candidates ─────────────────────────────────────────────────────────


async def test_no_candidates_no_bump(reconcile_env: dict[str, Any]) -> None:
    """Zero mismatch candidates → nothing processed, no bump, no reconnect."""
    reconcile_env["candidates"] = []
    summary = await rf.run_reconcile("test-vault", apply=True)

    assert summary.processed == 0
    assert summary.moved == 0
    assert reconcile_env["bumps"] == 0
    assert reconcile_env["reconnects"] == 0
    assert summary.stopped_reason == "complete"


# ── T4: maxpages cap ──────────────────────────────────────────────────────────


async def test_maxpages_stopped_reason(reconcile_env: dict[str, Any]) -> None:
    """SQL hits limit (hit_sql_limit=True) → stopped_reason=maxpages."""
    # 2 candidates, max_pages=1: fake_load_candidates returns ([PAGE_A], hit=True)
    summary = await rf.run_reconcile("test-vault", apply=True, max_pages=1)

    assert summary.processed == 1
    assert summary.moved == 1
    assert summary.stopped_reason == "maxpages"
    assert reconcile_env["bumps"] == 1  # partial batch still bumps


# ── T5: idempotent ────────────────────────────────────────────────────────────


async def test_idempotent_second_run_zero_moves(reconcile_env: dict[str, Any]) -> None:
    """After apply, second run finds 0 mismatches → 0 moves, 0 bumps."""
    # First run moves everything.
    await rf.run_reconcile("test-vault", apply=True)
    assert reconcile_env["bumps"] == 1

    # Simulate idempotent state: no more mismatches.
    reconcile_env["candidates"] = []
    reconcile_env["hit_sql_limit"] = False
    reconcile_env["bumps"] = 0  # reset counter
    reconcile_env["reconnects"] = 0

    summary2 = await rf.run_reconcile("test-vault", apply=True)

    assert summary2.processed == 0
    assert summary2.moved == 0
    assert reconcile_env["bumps"] == 0, "Second run must not bump when nothing moved"
    assert reconcile_env["reconnects"] == 0


# ── T6: collision skip ────────────────────────────────────────────────────────


async def test_collision_skip_no_bump(reconcile_env: dict[str, Any]) -> None:
    """Destination already exists → collision_skips++ for each; no bump when nothing moved."""
    reconcile_env["move_error"] = FileExistsError("destination occupied")
    summary = await rf.run_reconcile("test-vault", apply=True)

    assert summary.processed == 2
    assert summary.moved == 0
    assert summary.collision_skips == 2
    assert summary.failed == 0
    assert reconcile_env["bumps"] == 0, "No bump when nothing actually moved"
    assert reconcile_env["reconnects"] == 0


# ── T7: failed move ───────────────────────────────────────────────────────────


async def test_failed_move_no_bump(reconcile_env: dict[str, Any]) -> None:
    """_move_page raises a generic error → failed++ for each; no bump."""
    reconcile_env["move_error"] = OSError("disk full")
    summary = await rf.run_reconcile("test-vault", apply=True)

    assert summary.processed == 2
    assert summary.moved == 0
    assert summary.failed == 2
    assert summary.collision_skips == 0
    assert reconcile_env["bumps"] == 0, "No bump when nothing actually moved"
    assert reconcile_env["reconnects"] == 0


# ── T8: partial failure ───────────────────────────────────────────────────────


async def test_partial_failure_still_bumps(
    reconcile_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """First page move fails; second succeeds → bump + reconnect still fire."""
    call_count: list[int] = [0]

    async def flaky_move(page: Any, new_rel_path: str) -> None:
        call_count[0] += 1
        if call_count[0] == 1:
            raise OSError("transient I/O error on first page")
        reconcile_env["move_calls"].append((page.file_path, new_rel_path))

    monkeypatch.setattr(rf, "_move_page", flaky_move)

    summary = await rf.run_reconcile("test-vault", apply=True)

    assert summary.failed == 1
    assert summary.moved == 1
    assert reconcile_env["bumps"] == 1, "Partial success still triggers one bump (I1)"
    assert reconcile_env["reconnects"] == 1


# ── T9: single-flight flag ────────────────────────────────────────────────────


def test_is_running_reflects_state() -> None:
    """is_running() mirrors _state.is_running."""
    rf._state.is_running = False
    assert rf.is_running() is False
    rf._state.is_running = True
    assert rf.is_running() is True
    rf._state.is_running = False  # clean-up


def test_get_last_summary_none_before_any_run() -> None:
    """get_last_summary() returns None when no run has completed yet."""
    rf._state.last_summary = None
    assert rf.get_last_summary() is None


# ── T10: clamp_bounds ─────────────────────────────────────────────────────────


def test_clamp_bounds_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """None input → module default (200)."""

    class _FakeSettings:
        reconcile_folders_max_pages = rf.DEFAULT_MAX_PAGES
        vault_id = "test"

    monkeypatch.setattr(rf, "settings", _FakeSettings())
    assert rf.clamp_bounds(None) == rf.DEFAULT_MAX_PAGES


def test_clamp_bounds_over_hard_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Value above MAX_PAGES_HARD_CAP is clamped to the cap."""

    class _FakeSettings:
        reconcile_folders_max_pages = rf.DEFAULT_MAX_PAGES
        vault_id = "test"

    monkeypatch.setattr(rf, "settings", _FakeSettings())
    assert rf.clamp_bounds(999_999) == rf.MAX_PAGES_HARD_CAP


def test_clamp_bounds_zero_becomes_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zero input is clamped to minimum of 1."""

    class _FakeSettings:
        reconcile_folders_max_pages = rf.DEFAULT_MAX_PAGES
        vault_id = "test"

    monkeypatch.setattr(rf, "settings", _FakeSettings())
    assert rf.clamp_bounds(0) == 1


# ── T11: _folder_from_path helper ────────────────────────────────────────────


def test_folder_from_path_concepts() -> None:
    assert rf._folder_from_path("wiki/concepts/foo.md") == "concepts"


def test_folder_from_path_entities() -> None:
    assert rf._folder_from_path("wiki/entities/aws.md") == "entities"


def test_folder_from_path_sources() -> None:
    assert rf._folder_from_path("wiki/sources/paper.md") == "sources"


def test_folder_from_path_root_level_returns_none() -> None:
    """A page at wiki/<name>.md (no subfolder) must return None."""
    assert rf._folder_from_path("wiki/index.md") is None


def test_folder_from_path_deep_nested_uses_first_level() -> None:
    """Only the first subdirectory level below wiki/ is considered the folder."""
    assert rf._folder_from_path("wiki/entities/subdir/foo.md") == "entities"


def test_folder_from_path_non_wiki_prefix_returns_none() -> None:
    """Paths not under wiki/ return None (defensive)."""
    assert rf._folder_from_path("raw/sources/doc.pdf") is None


# ── T12: filesystem integration ───────────────────────────────────────────────


async def test_filesystem_move_entity_to_correct_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Integration: real file at wiki/concepts/aws.md (type=entity in frontmatter) is moved
    to wiki/entities/aws.md with NO content change.

    DB and Qdrant are fully stubbed.
    """
    import frontmatter as _fm

    # ── Set up vault ──────────────────────────────────────────────────────────
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    old_file = tmp_path / "wiki" / "concepts" / "aws.md"
    original_content = (
        "---\ntype: entity\ntitle: AWS\ntags:\n  - cloud\n---\n# AWS\n\nAmazon Web Services.\n"
    )
    old_file.write_text(original_content, encoding="utf-8")

    # ── Patch settings.vault_root ─────────────────────────────────────────────
    monkeypatch.setattr(
        "app.ops.reconcile_folders.settings",
        type(
            "S",
            (),
            {
                "vault_root": tmp_path,
                "vault_id": "test",
                "reconcile_folders_max_pages": rf.DEFAULT_MAX_PAGES,
            },
        )(),
    )

    # ── Stub get_session (DB operations) ──────────────────────────────────────
    execute_calls: list[Any] = []

    class _FakeSession:
        async def execute(self, stmt: Any) -> None:
            execute_calls.append(stmt)

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

    @asynccontextmanager
    async def fake_get_session() -> AsyncIterator[_FakeSession]:
        yield _FakeSession()

    monkeypatch.setattr("app.ops.reconcile_folders.get_session", fake_get_session)

    # ── Stub upsert_vector (Qdrant) ───────────────────────────────────────────
    upsert_calls: list[dict[str, Any]] = []

    async def fake_upsert_vector(**kwargs: Any) -> None:
        upsert_calls.append(kwargs)

    import app.ingest.orchestrator as orch

    monkeypatch.setattr(orch, "upsert_vector", fake_upsert_vector)

    # ── Build a fake page ─────────────────────────────────────────────────────
    page = _fake_page(title="AWS", file_path="wiki/concepts/aws.md", page_type="entity")
    new_rel_path = "wiki/entities/aws.md"

    # ── Execute _move_page ────────────────────────────────────────────────────
    await rf._move_page(page, new_rel_path)

    # ── Assertions ────────────────────────────────────────────────────────────
    # Old file must be gone.
    assert not old_file.exists(), "Old file must be deleted after move"

    # New file must exist in the correct folder.
    new_file = tmp_path / "wiki" / "entities" / "aws.md"
    assert new_file.exists(), "New file must exist at wiki/entities/aws.md"

    # Content must be byte-for-byte identical (no frontmatter rewrite).
    assert (
        new_file.read_text(encoding="utf-8") == original_content
    ), "Content must be unchanged after move"

    # Frontmatter type must still be 'entity' (was already correct).
    parsed = _fm.load(new_file)
    assert parsed["type"] == "entity"

    # DB was updated (at least one execute call).
    assert len(execute_calls) >= 1, "DB update must have been called"

    # Qdrant was updated with the new file_path.
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["file_path"] == new_rel_path
    assert upsert_calls[0]["page_type"] == "entity"


async def test_filesystem_collision_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If destination already exists, _move_page raises FileExistsError (no overwrite)."""
    # Create both source and destination files.
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    old_file = tmp_path / "wiki" / "concepts" / "aws.md"
    new_file = tmp_path / "wiki" / "entities" / "aws.md"
    old_file.write_text("---\ntype: entity\n---\n# AWS\n", encoding="utf-8")
    new_file.write_text("---\ntype: entity\n---\n# AWS already here\n", encoding="utf-8")
    original_dest_content = new_file.read_text(encoding="utf-8")

    monkeypatch.setattr(
        "app.ops.reconcile_folders.settings",
        type("S", (), {"vault_root": tmp_path, "vault_id": "test"})(),
    )

    @asynccontextmanager
    async def fake_get_session() -> AsyncIterator[Any]:
        class _FS:
            async def execute(self, *a: Any) -> None:
                pass

        yield _FS()

    monkeypatch.setattr("app.ops.reconcile_folders.get_session", fake_get_session)

    async def fake_upsert(**kw: Any) -> None:
        pass

    import app.ingest.orchestrator as orch

    monkeypatch.setattr(orch, "upsert_vector", fake_upsert)

    page = _fake_page(title="AWS", file_path="wiki/concepts/aws.md", page_type="entity")

    with pytest.raises(FileExistsError):
        await rf._move_page(page, "wiki/entities/aws.md")

    # Source must still exist (no partial move).
    assert old_file.exists(), "Source must be intact after collision guard"
    # Destination must not have been overwritten.
    assert new_file.read_text(encoding="utf-8") == original_dest_content


# ── T13: wikilinks reconnect ──────────────────────────────────────────────────


async def test_wikilinks_reconnected_once_after_batch(reconcile_env: dict[str, Any]) -> None:
    """reresolve_dangling_links is called exactly once after the whole batch (K5)."""
    await rf.run_reconcile("test-vault", apply=True)

    assert (
        reconcile_env["reconnects"] == 1
    ), "Expected reresolve_dangling_links called exactly once after the batch (K5)"


async def test_wikilinks_not_called_on_dryrun(reconcile_env: dict[str, Any]) -> None:
    """reresolve_dangling_links is NOT called during a dry-run."""
    await rf.run_reconcile("test-vault", apply=False)
    assert reconcile_env["reconnects"] == 0


# ── T14: as_dict serialisation ───────────────────────────────────────────────


def test_summary_as_dict_json_serialisable() -> None:
    """ReconcileSummary.as_dict() is JSON-serialisable and has all required keys."""
    import json

    summary = rf.ReconcileSummary(
        processed=2,
        moved=1,
        skipped=0,
        failed=1,
        collision_skips=0,
        by_folder={"entities": 1},
        plan=[
            rf.ReconcilePlanItem(
                page_id=str(uuid.uuid4()),
                slug="aws",
                old_path="wiki/concepts/aws.md",
                new_path="wiki/entities/aws.md",
                page_type="entity",
                title="AWS",
            )
        ],
        total_cost_usd=0.0,
        stopped_reason="complete",
        max_pages=200,
        apply=True,
    )
    d = summary.as_dict()
    # Must be JSON-serialisable (no UUID objects, no Path objects, etc.)
    json.dumps(d)
    assert d["total_cost_usd"] == 0.0
    assert d["apply"] is True
    assert d["stopped_reason"] == "complete"
    assert d["moved"] == 1
    assert len(d["plan"]) == 1
    assert d["plan"][0]["slug"] == "aws"
    assert d["plan"][0]["new_path"] == "wiki/entities/aws.md"
    assert "collision_skips" in d
    assert "by_folder" in d


# ── T15: defence-in-depth — invalid type in candidates ───────────────────────


async def test_invalid_page_type_skipped(
    reconcile_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    If a candidate has an invalid page_type (not a PageType enum value),
    it is counted as skipped and no move is attempted.
    """
    bad_page = _fake_page(
        title="Weird",
        file_path="wiki/concepts/weird.md",
        page_type="not-a-valid-type",
    )
    reconcile_env["candidates"] = [bad_page]

    async def fake_load(vault_id: str, max_pages: int) -> tuple[list[Any], bool]:
        return [bad_page], False

    monkeypatch.setattr(rf, "_load_candidates", fake_load)

    summary = await rf.run_reconcile("test-vault", apply=True)

    assert summary.processed == 1
    assert summary.moved == 0
    assert summary.skipped == 1
    assert reconcile_env["bumps"] == 0
