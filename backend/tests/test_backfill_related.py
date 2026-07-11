"""
Tests for ops/backfill_related.py — ADR-0067 D2 related: backfill + slug-link conversion.

Seeded vault scenario:
  PAGE_A  wiki/entities/aws-cloud.md       title="AWS Cloud"
  PAGE_B  wiki/concepts/machine-learning.md title="Machine Learning"
  PAGE_C  wiki/concepts/serverless.md       title="Serverless"

Body fixture for each page mentions the OTHER pages by title (not slug):
  "We use [[AWS Cloud]] and [[Machine Learning]]."  (in PAGE_C)

Test plan:
  T1  — dry-run: pages_changed reported, 0 files written, 0 bumps.
  T2  — apply: [[AWS Cloud]] → [[aws-cloud|AWS Cloud]], related: [aws-cloud] set,
         other frontmatter keys preserved, one data_version bump, reresolve called.
  T3  — code-fence links skipped: ```[[AWS Cloud]]``` not rewritten.
  T4  — already-slug links left alone: [[aws-cloud]] not rewritten.
  T5  — unresolvable links left alone: [[Ghost Page]] unchanged.
  T6  — [[Title|alias]] → [[slug|alias]] (alias preserved, not overwritten by Title).
  T7  — zero resolvable outbound links → related: absent (not empty list).
  T8  — idempotent: second apply run returns pages_changed=0 (no-op).
  T9  — maxpages cap: stopped_reason=maxpages when SQL limit hit.
  T10 — single-flight: is_running() / get_last_summary() contract.
  T11 — clamp_bounds: default / over-cap / zero handling.
  T12 — _patch_frontmatter_related: D2 insertion position (after tags, before sources).
  T13 — _patch_frontmatter_related: removes existing related: before inserting.
  T14 — _patch_frontmatter_related: empty slugs removes related: key.
  T15 — _rewrite_title_links: returns (body, count, samples) tuple.
  T16 — _collect_outbound_slugs: dedup + cap respected.
  T17 — as_dict serialisable: BackfillSummary.as_dict() is JSON-serialisable.
  T18 — filesystem integration: real tmpdir, file written correctly, DB+Qdrant stubbed.

DB and Qdrant are ALWAYS stubbed.  File I/O is real only in T18 (uses tmp_path).
SQL is portable (no Postgres-specific syntax used in production queries; stubs avoid SQL).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import app.ops.backfill_related as br
import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_page(
    title: str,
    file_path: str,
    page_type: str = "concept",
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
    p.source_mtime_ns = 0
    p.deleted_at = None
    return p


# ── Canonical seed pages ──────────────────────────────────────────────────────

PAGE_A = _fake_page("AWS Cloud", "wiki/entities/aws-cloud.md", "entity")
PAGE_B = _fake_page("Machine Learning", "wiki/concepts/machine-learning.md")
PAGE_C = _fake_page("Serverless", "wiki/concepts/serverless.md")

_ALL_PAGES = [PAGE_A, PAGE_B, PAGE_C]

# The resolver built from the three seed pages
_SEED_RESOLVER = br._Resolver(
    by_title={
        "AWS Cloud": "aws-cloud",
        "Machine Learning": "machine-learning",
        "Serverless": "serverless",
    },
    by_lower={
        "aws cloud": "aws-cloud",
        "machine learning": "machine-learning",
        "serverless": "serverless",
    },
    by_slug={
        "aws-cloud": "aws-cloud",
        "machine-learning": "machine-learning",
        "serverless": "serverless",
    },
    slug_set={"aws-cloud", "machine-learning", "serverless"},
)

# Frontmatter for PAGE_C (standard D2 shape with tags, without related)
_FM_C = "---\ntype: concept\ntitle: Serverless\ncreated: 2026-01-01\nupdated: 2026-07-10\ntags:\n- cloud\n---\n"
# Body for PAGE_C (mentions AWS Cloud and Machine Learning by title)
_BODY_C = "\nServerless computing relies on [[AWS Cloud]] and [[Machine Learning]] principles.\n"


# ── Shared fixture ────────────────────────────────────────────────────────────


@pytest.fixture()
def br_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Stub every I/O dependency so tests run infra-free (no Postgres, no Qdrant, no
    real filesystem — except T18 which uses tmp_path directly).

    Patched surfaces:
      br._load_candidate_pages  — returns (state["pages"], state["hit_limit"])
      br._build_resolver        — returns state["resolver"]
      br._read_page_split       — returns state["page_splits"][file_path] or None
      reindex_wiki_page_body    — records calls in state["reindex_calls"]
      bump_version              — records call count in state["bumps"]
      reresolve_dangling_links  — records call count in state["reconnects"]
    """
    state: dict[str, Any] = {
        "pages": list(_ALL_PAGES),
        "hit_limit": False,
        "resolver": _SEED_RESOLVER,
        # file_path → (fm_block, body) — controls what _read_page_split returns
        "page_splits": {
            PAGE_A.file_path: (
                "---\ntype: entity\ntitle: AWS Cloud\ncreated: 2026-01-01\nupdated: 2026-07-10\ntags:\n- cloud\n---\n",
                "\nAWS Cloud is a platform.\n",
            ),
            PAGE_B.file_path: (
                "---\ntype: concept\ntitle: Machine Learning\ncreated: 2026-01-01\nupdated: 2026-07-10\ntags:\n- ai\n---\n",
                "\nMachine Learning enables AI.\n",
            ),
            PAGE_C.file_path: (_FM_C, _BODY_C),
        },
        "reindex_calls": [],  # list of (file_path, new_file_text)
        "bumps": 0,
        "reconnects": 0,
    }

    async def fake_load(vault_id: str, max_pages: int) -> tuple[list[Any], bool]:
        pages = list(state["pages"])[:max_pages]
        hit = state["hit_limit"] or (len(state["pages"]) > max_pages)
        return pages, hit

    async def fake_build_resolver(vault_id: str) -> br._Resolver:
        return state["resolver"]

    def fake_read_split(page: Any) -> tuple[str, str] | None:
        return state["page_splits"].get(page.file_path)

    async def fake_reindex(
        *,
        page: Any,
        new_file_text: str,
        body_for_embedding: str,
        bump: bool = True,
    ) -> None:
        state["reindex_calls"].append((page.file_path, new_file_text))

    async def fake_bump() -> None:
        state["bumps"] += 1

    async def fake_reresolve(session: Any) -> int:
        state["reconnects"] += 1
        return 0

    monkeypatch.setattr(br, "_load_candidate_pages", fake_load)
    monkeypatch.setattr(br, "_build_resolver", fake_build_resolver)
    monkeypatch.setattr(br, "_read_page_split", fake_read_split)

    import app.ingest.orchestrator as orch

    monkeypatch.setattr(orch, "reindex_wiki_page_body", fake_reindex)
    monkeypatch.setattr(orch, "bump_version", fake_bump)

    import app.wiki.links as wlinks

    monkeypatch.setattr(wlinks, "reresolve_dangling_links", fake_reresolve)

    # Reset module-level single-flight state between tests
    br._state.is_running = False
    br._state.last_summary = None

    return state


# ── T1: dry-run reports changes without writing ───────────────────────────────


async def test_dry_run_reports_but_does_not_write(br_env: dict[str, Any]) -> None:
    """dry-run: pages_changed > 0 reported, 0 reindex calls, 0 bumps."""
    summary = await br.run_backfill_related("test-vault", apply=False)

    # PAGE_C has title-links → at least 1 change reported
    assert summary.pages_changed >= 1
    assert summary.links_converted >= 1

    # No file writes
    assert br_env["reindex_calls"] == []
    assert br_env["bumps"] == 0
    assert br_env["reconnects"] == 0

    # Dry-run result
    assert summary.apply is False
    assert not br.is_running()
    assert br.get_last_summary() is summary


# ── T2: apply converts links and sets related: ────────────────────────────────


async def test_apply_converts_links_and_adds_related(br_env: dict[str, Any]) -> None:
    """
    apply=True: [[AWS Cloud]] → [[aws-cloud|AWS Cloud]], related: populated,
    exactly one data_version bump, reresolve called once.
    """
    # Only process PAGE_C (has title-link targets)
    br_env["pages"] = [PAGE_C]

    summary = await br.run_backfill_related("test-vault", apply=True)

    assert summary.pages_changed == 1
    assert summary.links_converted == 2  # [[AWS Cloud]] and [[Machine Learning]]
    assert summary.related_added == 1
    assert summary.stopped_reason == "complete"

    # Exactly one reindex call
    assert len(br_env["reindex_calls"]) == 1
    file_path, new_text = br_env["reindex_calls"][0]
    assert file_path == PAGE_C.file_path

    # Body links converted
    assert "[[aws-cloud|AWS Cloud]]" in new_text
    assert "[[machine-learning|Machine Learning]]" in new_text

    # related: inserted
    assert "related:" in new_text
    assert "- aws-cloud" in new_text
    assert "- machine-learning" in new_text

    # Other frontmatter keys preserved
    assert "type: concept" in new_text
    assert "title: Serverless" in new_text
    assert "tags:" in new_text
    assert "- cloud" in new_text

    # Exactly one bump + reresolve
    assert br_env["bumps"] == 1
    assert br_env["reconnects"] == 1


# ── T3: code-fence links are never rewritten ─────────────────────────────────


async def test_code_fence_links_not_rewritten(br_env: dict[str, Any]) -> None:
    """Links inside ``` fences must not be rewritten (P2-2 invariant)."""
    fence_body = "\nSome text.\n\n```\n[[AWS Cloud]] stays unchanged here\n```\n\nNormal [[Machine Learning]] link.\n"
    br_env["page_splits"][PAGE_C.file_path] = (_FM_C, fence_body)
    br_env["pages"] = [PAGE_C]

    summary = await br.run_backfill_related("test-vault", apply=True)

    assert len(br_env["reindex_calls"]) == 1
    _, new_text = br_env["reindex_calls"][0]

    # Inside fence: unchanged
    assert "```\n[[AWS Cloud]] stays unchanged here\n```" in new_text
    # Outside fence: converted
    assert "[[machine-learning|Machine Learning]]" in new_text
    # Only 1 link converted (the one outside the fence)
    assert summary.links_converted == 1


# ── T4: already-slug links left alone ────────────────────────────────────────


async def test_already_slug_links_not_rewritten(br_env: dict[str, Any]) -> None:
    """[[aws-cloud]] is already a slug → must NOT be rewritten to [[aws-cloud|aws-cloud]]."""
    slug_body = "\nWe use [[aws-cloud]] services.\n"
    br_env["page_splits"][PAGE_C.file_path] = (_FM_C, slug_body)
    br_env["pages"] = [PAGE_C]

    summary = await br.run_backfill_related("test-vault", apply=False)

    # aws-cloud resolves via by_slug only (not a title) → resolve_as_title returns None
    # But wait: "aws-cloud" is not in by_title or by_lower (those have "AWS Cloud" and "aws cloud")
    # So resolve_as_title("aws-cloud") returns None → left alone
    # BUT: does it still add related: (collect_outbound_slugs uses resolve() which includes by_slug)?
    # collect_outbound_slugs uses .resolve() which hits by_slug → resolves to "aws-cloud"
    # So related WOULD be added even though no body links were converted.

    # links_converted = 0 (slug form not rewritten)
    assert summary.links_converted == 0
    # But related: would be added for the slug link that resolves via by_slug
    # (the related backfill uses all 3 strategies)
    assert summary.related_added == 1


# ── T5: unresolvable links left alone ────────────────────────────────────────


async def test_unresolvable_links_untouched(br_env: dict[str, Any]) -> None:
    """[[Ghost Page]] not in resolver → left alone in both body and related."""
    ghost_body = "\nSee [[Ghost Page]] for details.\n"
    br_env["page_splits"][PAGE_C.file_path] = (_FM_C, ghost_body)
    br_env["pages"] = [PAGE_C]

    summary = await br.run_backfill_related("test-vault", apply=True)

    # No links converted, no related added (unresolvable)
    assert summary.links_converted == 0
    assert summary.related_added == 0
    # Nothing changed → no reindex
    assert br_env["reindex_calls"] == []


# ── T6: [[Title|alias]] → [[slug|alias]] (alias preserved) ───────────────────


async def test_title_with_alias_preserves_alias(br_env: dict[str, Any]) -> None:
    """[[AWS Cloud|the cloud provider]] → [[aws-cloud|the cloud provider]]."""
    alias_body = "\nWe use [[AWS Cloud|the cloud provider]] here.\n"
    br_env["page_splits"][PAGE_C.file_path] = (_FM_C, alias_body)
    br_env["pages"] = [PAGE_C]

    summary = await br.run_backfill_related("test-vault", apply=True)

    assert summary.links_converted == 1
    assert len(br_env["reindex_calls"]) == 1
    _, new_text = br_env["reindex_calls"][0]
    # alias preserved, target replaced with slug
    assert "[[aws-cloud|the cloud provider]]" in new_text
    # Original alias NOT as the fallback display
    assert "[[aws-cloud|AWS Cloud]]" not in new_text


# ── T7: zero resolvable links → related: absent ───────────────────────────────


async def test_zero_resolvable_links_no_related(br_env: dict[str, Any]) -> None:
    """If a page has no resolvable outbound links, related: must NOT be written."""
    no_links_body = "\nThis page has no wikilinks at all.\n"
    br_env["page_splits"][PAGE_C.file_path] = (_FM_C, no_links_body)
    br_env["pages"] = [PAGE_C]

    summary = await br.run_backfill_related("test-vault", apply=True)

    assert summary.links_converted == 0
    assert summary.related_added == 0
    # Nothing changed → no reindex, no bump
    assert br_env["reindex_calls"] == []
    assert br_env["bumps"] == 0


# ── T8: idempotent — second run returns 0 changes ────────────────────────────


async def test_idempotent_second_run(br_env: dict[str, Any]) -> None:
    """
    After apply, the written content (already in slug form) causes a second run to
    find 0 changes (idempotency: convert and re-run should return pages_changed=0).
    """
    br_env["pages"] = [PAGE_C]

    # First run (apply)
    await br.run_backfill_related("test-vault", apply=True)

    # Simulate the file being updated: page_splits now holds the written text
    # The reindex call recorded what was written
    assert len(br_env["reindex_calls"]) == 1
    _, written_text = br_env["reindex_calls"][0]

    # Split the written text back into (fm, body) for the second run
    fm_new, body_new = br._split_frontmatter_for_test(written_text)
    br_env["page_splits"][PAGE_C.file_path] = (fm_new, body_new)

    # Reset state for second run
    br._state.is_running = False
    br_env["reindex_calls"].clear()
    br_env["bumps"] = 0

    # Second run — should find 0 changes
    summary2 = await br.run_backfill_related("test-vault", apply=True)

    assert summary2.pages_changed == 0
    assert summary2.links_converted == 0
    assert br_env["reindex_calls"] == []
    assert br_env["bumps"] == 0


# ── T9: maxpages cap ─────────────────────────────────────────────────────────


async def test_maxpages_cap(br_env: dict[str, Any]) -> None:
    """stopped_reason=maxpages when SQL LIMIT was hit."""
    br_env["hit_limit"] = True

    summary = await br.run_backfill_related("test-vault", apply=False, max_pages=1)
    assert summary.stopped_reason == "maxpages"


# ── T10: single-flight state ─────────────────────────────────────────────────


async def test_single_flight_state(br_env: dict[str, Any]) -> None:
    """is_running() is True during the run, False after; get_last_summary() populated."""
    import asyncio

    br_env["pages"] = [PAGE_C]
    seen: dict[str, bool] = {}

    orig_load = br._load_candidate_pages

    async def slow_load(vault_id: str, max_pages: int) -> tuple[list[Any], bool]:
        seen["running_during"] = br.is_running()
        await asyncio.sleep(0)
        return await orig_load(vault_id, max_pages)

    br._load_candidate_pages = slow_load  # type: ignore[assignment]
    try:
        assert not br.is_running()
        await br.run_backfill_related("test-vault")
    finally:
        br._load_candidate_pages = orig_load  # type: ignore[assignment]

    assert seen["running_during"] is True
    assert not br.is_running()
    assert br.get_last_summary() is not None


# ── T11: clamp_bounds ────────────────────────────────────────────────────────


async def test_clamp_bounds() -> None:
    """clamp_bounds: None → default, over-cap clamped, low value clamped to 1."""
    mp = br.clamp_bounds(None)
    assert 1 <= mp <= br.MAX_PAGES_HARD_CAP

    mp_over = br.clamp_bounds(999_999)
    assert mp_over == br.MAX_PAGES_HARD_CAP

    mp_one = br.clamp_bounds(1)
    assert mp_one == 1


# ── T12: _patch_frontmatter_related — D2 insertion position ──────────────────


def test_patch_frontmatter_d2_position() -> None:
    """related: inserted after tags block, before sources."""
    fm = (
        "---\n"
        "type: concept\n"
        "title: Foo\n"
        "created: 2026-01-01\n"
        "updated: 2026-07-10\n"
        "tags:\n"
        "- ai\n"
        "- ml\n"
        "sources:\n"
        "- raw/doc.pdf\n"
        "---\n"
    )
    result = br._patch_frontmatter_related(fm, ["page-a", "page-b"])

    lines = result.splitlines()
    tags_idx = next(i for i, ln in enumerate(lines) if ln.strip() == "tags:")
    related_idx = next(i for i, ln in enumerate(lines) if ln.strip() == "related:")
    sources_idx = next(i for i, ln in enumerate(lines) if ln.strip() == "sources:")

    # D2 order: tags < related < sources
    assert tags_idx < related_idx < sources_idx

    # Slug list present
    assert "- page-a" in result
    assert "- page-b" in result

    # Other keys preserved byte-for-byte
    assert "type: concept" in result
    assert "title: Foo" in result
    assert "- raw/doc.pdf" in result


# ── T13: _patch_frontmatter_related removes existing related: before inserting ─


def test_patch_frontmatter_removes_existing_related() -> None:
    """Existing related: block is removed before the new one is inserted."""
    fm = (
        "---\n"
        "type: concept\n"
        "title: Foo\n"
        "tags:\n"
        "- ai\n"
        "related:\n"
        "- old-slug\n"
        "- another-old\n"
        "---\n"
    )
    result = br._patch_frontmatter_related(fm, ["new-slug"])

    assert "old-slug" not in result
    assert "another-old" not in result
    assert "- new-slug" in result
    # Only one related: header
    assert result.count("related:") == 1


# ── T14: _patch_frontmatter_related with empty slugs removes related: ─────────


def test_patch_frontmatter_empty_slugs_removes_related() -> None:
    """Empty slugs list → related: key removed entirely (no empty list emitted)."""
    fm = "---\n" "type: concept\n" "title: Foo\n" "related:\n" "- old-slug\n" "---\n"
    result = br._patch_frontmatter_related(fm, [])

    assert "related" not in result
    assert "old-slug" not in result
    assert "type: concept" in result
    assert "title: Foo" in result


# ── T15: _rewrite_title_links return shape ────────────────────────────────────


def test_rewrite_title_links_shape() -> None:
    """_rewrite_title_links returns (new_body, count, samples)."""
    body = "We use [[AWS Cloud]] and [[Machine Learning]] here."
    new_body, count, samples = br._rewrite_title_links(body, _SEED_RESOLVER)

    assert isinstance(new_body, str)
    assert isinstance(count, int)
    assert isinstance(samples, list)

    assert count == 2
    assert "[[aws-cloud|AWS Cloud]]" in new_body
    assert "[[machine-learning|Machine Learning]]" in new_body

    # Samples contain human-readable descriptions
    assert len(samples) == 2
    assert any("→" in s for s in samples)


# ── T16: _collect_outbound_slugs dedup + cap ─────────────────────────────────


def test_collect_outbound_slugs_dedup_cap() -> None:
    """Deduplication and cap respected."""
    # Repeated link — should be collected once
    body = "[[AWS Cloud]] then [[AWS Cloud]] again and [[Machine Learning]]."
    out = br._collect_outbound_slugs(body, "serverless", _SEED_RESOLVER)

    assert out.count("aws-cloud") == 1  # deduped
    assert "machine-learning" in out
    assert len(out) <= br._RELATED_CAP


# ── T17: BackfillSummary.as_dict is JSON-serialisable ────────────────────────


def test_as_dict_json_serialisable() -> None:
    """BackfillSummary.as_dict() must be JSON-serialisable (no datetimes, UUIDs etc.)."""
    s = br.BackfillSummary(
        pages_scanned=10,
        pages_changed=3,
        links_converted=5,
        related_added=2,
        stopped_reason="complete",
        max_pages=500,
        apply=False,
        samples=[
            br.BackfillSample(
                page_title="Foo",
                file_path="wiki/concepts/foo.md",
                links_would_convert=["[[Foo]] → [[foo|Foo]]"],
                related_would_add=["foo"],
            )
        ],
    )
    serialised = json.dumps(s.as_dict())
    parsed = json.loads(serialised)
    assert parsed["pages_changed"] == 3
    assert parsed["total_cost_usd"] == 0.0
    assert len(parsed["samples"]) == 1
    assert parsed["samples"][0]["page_title"] == "Foo"


# ── T18: filesystem integration ──────────────────────────────────────────────


async def test_filesystem_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Real file I/O: writes a page with title-links to a tmpdir vault, runs apply,
    verifies the file on disk contains slug links and related: frontmatter.
    DB and Qdrant are stubbed via monkeypatch.
    """
    import app.ingest.orchestrator as orch
    import app.wiki.links as wlinks

    # ── Set up vault structure ─────────────────────────────────────────────────
    vault_root = tmp_path / "vault"
    wiki_dir = vault_root / "wiki" / "concepts"
    wiki_dir.mkdir(parents=True)

    target_dir = vault_root / "wiki" / "entities"
    target_dir.mkdir(parents=True)

    # Page under test: serverless.md with title-form links
    page_file = wiki_dir / "serverless.md"
    page_text = (
        "---\n"
        "type: concept\n"
        "title: Serverless\n"
        "created: 2026-01-01\n"
        "updated: 2026-07-10\n"
        "tags:\n"
        "- cloud\n"
        "---\n"
        "\n"
        "Serverless relies on [[AWS Cloud]] and [[Machine Learning]] concepts.\n"
    )
    page_file.write_text(page_text, encoding="utf-8")

    # Target pages (just need to exist in the resolver; files not strictly required)
    (target_dir / "aws-cloud.md").write_text(
        "---\ntype: entity\ntitle: AWS Cloud\n---\n\nAWS Cloud page.\n", encoding="utf-8"
    )
    (wiki_dir / "machine-learning.md").write_text(
        "---\ntype: concept\ntitle: Machine Learning\n---\n\nML page.\n", encoding="utf-8"
    )

    # ── Patch settings in the backfill_related module (vault_root is a computed property)
    monkeypatch.setattr(
        "app.ops.backfill_related.settings",
        type("S", (), {"vault_root": vault_root, "vault_id": "test-vault"})(),
    )

    # ── Stub DB and Qdrant ────────────────────────────────────────────────────
    bumps: list[int] = []
    reindex_calls: list[str] = []

    async def fake_bump() -> None:
        bumps.append(1)

    async def fake_reindex(
        *, page: Any, new_file_text: str, body_for_embedding: str, bump: bool = True
    ) -> None:
        reindex_calls.append(page.file_path)
        # Actually write the file (simulating the real reindex_wiki_page_body write step)
        abs_path = (vault_root / page.file_path).resolve()
        abs_path.write_text(new_file_text, encoding="utf-8")

    async def fake_reresolve(session: Any) -> int:
        return 0

    monkeypatch.setattr(orch, "bump_version", fake_bump)
    monkeypatch.setattr(orch, "reindex_wiki_page_body", fake_reindex)
    monkeypatch.setattr(wlinks, "reresolve_dangling_links", fake_reresolve)

    # ── Stub DB queries (no Postgres) ─────────────────────────────────────────
    page_obj = _fake_page("Serverless", "wiki/concepts/serverless.md", "concept")

    async def fake_load(vault_id: str, max_pages: int) -> tuple[list[Any], bool]:
        return [page_obj], False

    async def fake_resolver(vault_id: str) -> br._Resolver:
        return br._Resolver(
            by_title={"AWS Cloud": "aws-cloud", "Machine Learning": "machine-learning"},
            by_lower={"aws cloud": "aws-cloud", "machine learning": "machine-learning"},
            by_slug={"aws-cloud": "aws-cloud", "machine-learning": "machine-learning"},
            slug_set={"aws-cloud", "machine-learning", "serverless"},
        )

    monkeypatch.setattr(br, "_load_candidate_pages", fake_load)
    monkeypatch.setattr(br, "_build_resolver", fake_resolver)

    # Reset state
    br._state.is_running = False
    br._state.last_summary = None

    # ── Run apply ─────────────────────────────────────────────────────────────
    summary = await br.run_backfill_related("test-vault", apply=True)

    assert summary.pages_changed == 1
    assert summary.links_converted == 2

    # ── Verify on-disk result ─────────────────────────────────────────────────
    result_text = page_file.read_text(encoding="utf-8")

    # Body links converted
    assert "[[aws-cloud|AWS Cloud]]" in result_text
    assert "[[machine-learning|Machine Learning]]" in result_text

    # related: populated at D2 position
    assert "related:" in result_text
    assert "- aws-cloud" in result_text
    assert "- machine-learning" in result_text

    # Frontmatter keys preserved
    assert "type: concept" in result_text
    assert "title: Serverless" in result_text
    assert "tags:" in result_text
    assert "- cloud" in result_text

    # D2 order: tags before related
    lines = result_text.splitlines()
    tags_idx = next(i for i, ln in enumerate(lines) if ln.strip() == "tags:")
    related_idx = next(i for i, ln in enumerate(lines) if ln.strip() == "related:")
    assert tags_idx < related_idx

    # One bump
    assert len(bumps) == 1


# ── Idempotency helper: split the written text ────────────────────────────────

# Expose _split_frontmatter under a test-friendly alias so T8 can call it without
# importing enrich_wikilinks directly (the helper lives there; we expose it via br).


def _split_frontmatter_for_test(text: str) -> tuple[str, str]:
    from app.ops.enrich_wikilinks import _split_frontmatter

    return _split_frontmatter(text)


# Monkey-patch the helper onto the module so T8 can call br._split_frontmatter_for_test
br._split_frontmatter_for_test = _split_frontmatter_for_test  # type: ignore[attr-defined]
