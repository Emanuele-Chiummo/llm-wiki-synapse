"""
Obsidian compatibility check tests (I5, EC-M2-13, AC-K7-1..3).

These tests exercise check_obsidian.py in self-test mode and with
constructed vaults, asserting:
  - .obsidian/ config check passes on valid vault
  - .obsidian/ config check fails on missing or corrupt config
  - Frontmatter checker passes pages with valid required fields
  - Frontmatter checker catches missing fields
  - K5 wikilink parser accepts valid links and catches parse exceptions
  - Synthetic write test passes (write + read-back + wikilink extract)
  - Full self-test mode passes end-to-end

Test IDs: T-OBS-001 .. T-OBS-015
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_SCRIPT = _BACKEND / "scripts" / "check_obsidian.py"


def _load_check_module():  # type: ignore[return]
    """Load check_obsidian.py via importlib (same pattern as smoke test harness)."""
    _MOD_NAME = "check_obsidian"
    if _MOD_NAME in sys.modules:
        return sys.modules[_MOD_NAME]
    spec = importlib.util.spec_from_file_location(_MOD_NAME, _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _write_fm(path: Path, **kwargs: object) -> None:
    """Write a YAML frontmatter Markdown file."""
    import frontmatter as fm_lib

    content = kwargs.pop("content", "")
    post = fm_lib.Post(str(content), **kwargs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fm_lib.dumps(post) + "\n", encoding="utf-8")


# ── Obsidian config checks ────────────────────────────────────────────────────


class TestObsidianConfigCheck:
    """T-OBS-001..004 — AC-K7-1: .obsidian/ directory and app.json checks."""

    def test_valid_obsidian_config_passes(self) -> None:
        """T-OBS-001: Valid .obsidian/app.json with legacyEditor=false must pass."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            obs = wiki_dir / ".obsidian"
            obs.mkdir()
            (obs / "app.json").write_text(json.dumps({"legacyEditor": False}), encoding="utf-8")
            failures = mod.check_obsidian_config(wiki_dir)
        assert failures == [], f"Expected no failures; got: {failures}"

    def test_missing_obsidian_dir_fails(self) -> None:
        """T-OBS-002: Missing .obsidian/ directory must produce a failure."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            failures = mod.check_obsidian_config(wiki_dir)
        assert any(
            ".obsidian" in f for f in failures
        ), "Expected failure about missing .obsidian/ directory"

    def test_missing_app_json_fails(self) -> None:
        """T-OBS-003: Missing .obsidian/app.json must produce a failure."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            (wiki_dir / ".obsidian").mkdir()
            failures = mod.check_obsidian_config(wiki_dir)
        assert any("app.json" in f for f in failures), "Expected failure about missing app.json"

    def test_legacy_editor_true_fails(self) -> None:
        """T-OBS-004: legacyEditor=true in app.json must produce a failure (I4 — no WYSIWYG)."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            obs = wiki_dir / ".obsidian"
            obs.mkdir()
            (obs / "app.json").write_text(json.dumps({"legacyEditor": True}), encoding="utf-8")
            failures = mod.check_obsidian_config(wiki_dir)
        assert any(
            "legacyEditor" in f for f in failures
        ), "Expected failure about legacyEditor=true (I4 invariant)"


# ── Frontmatter checks ────────────────────────────────────────────────────────


class TestFrontmatterCheck:
    """T-OBS-005..008 — I5/K6: frontmatter on all pages must have required fields."""

    def test_valid_page_passes(self) -> None:
        """T-OBS-005: Page with all required frontmatter fields must pass."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            _write_fm(
                wiki_dir / "concept" / "vector-db.md",
                content="A vector database stores embeddings. See [[Qdrant]].",
                type="concept",
                title="Vector Database",
                sources=["raw/sources/sample.md"],
                lang="en",
            )
            failures = mod.check_all_pages_frontmatter(wiki_dir)
        assert failures == [], f"Expected no failures; got: {failures}"

    def test_missing_type_fails(self) -> None:
        """T-OBS-006: Page missing 'type' in frontmatter must produce a failure (I5)."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            _write_fm(
                wiki_dir / "concept" / "no-type.md",
                content="Content here.",
                title="No Type Page",
                sources=["raw/sources/sample.md"],
                lang="en",
            )
            failures = mod.check_all_pages_frontmatter(wiki_dir)
        assert any("type" in f for f in failures), "Expected failure about missing 'type' field"

    def test_empty_sources_fails(self) -> None:
        """T-OBS-007: Page with empty sources[] must produce a failure (F3 traceability)."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            _write_fm(
                wiki_dir / "entity" / "qdrant.md",
                content="Qdrant is a vector database.",
                type="entity",
                title="Qdrant",
                sources=[],  # empty!
                lang="en",
            )
            failures = mod.check_all_pages_frontmatter(wiki_dir)
        assert any(
            "sources" in f.lower() for f in failures
        ), "Expected failure about empty sources[]"

    def test_invalid_type_fails(self) -> None:
        """T-OBS-008: Page with invalid type (not in PageType enum) must fail."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            _write_fm(
                wiki_dir / "other" / "bad-type.md",
                content="Some content.",
                type="invalid-type",
                title="Bad Type",
                sources=["raw/sources/sample.md"],
                lang="en",
            )
            failures = mod.check_all_pages_frontmatter(wiki_dir)
        assert any("type" in f for f in failures), "Expected failure about invalid page type"


# ── Wikilink parser checks ────────────────────────────────────────────────────


class TestWikilinkCheck:
    """T-OBS-009..010 — K5: wikilinks must be parseable without exception."""

    def test_valid_wikilinks_pass(self) -> None:
        """T-OBS-009: Valid [[wikilinks]] in pages must not produce failures."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            _write_fm(
                wiki_dir / "concept" / "vector-db.md",
                content="See [[Qdrant]] and [[bge-m3|bge-m3 embeddings]] for details.",
                type="concept",
                title="Vector Database",
                sources=["raw/sources/sample.md"],
                lang="en",
            )
            failures = mod.check_wikilinks_parseable(wiki_dir)
        assert failures == [], f"Expected no failures; got: {failures}"

    def test_parse_wikilinks_extracts_targets(self) -> None:
        """T-OBS-010: K5 parser must extract correct targets from various link forms (K5)."""
        mod = _load_check_module()
        text = (
            "See [[Qdrant]], [[Vector Database|vector db]], "
            "[[bge-m3#Usage|model usage]], [[Index Page]]."
        )
        links = mod._parse_wikilinks(text)
        assert "Qdrant" in links
        assert "Vector Database" in links
        assert "bge-m3" in links
        assert "Index Page" in links


# ── Synthetic write test ──────────────────────────────────────────────────────


class TestSyntheticWriteTest:
    """T-OBS-011 — I5, K5, K6: write + read-back + wikilink extraction."""

    def test_synthetic_write_test_passes(self) -> None:
        """T-OBS-011: run_synthetic_write_test() must produce no failures."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            failures = mod.run_synthetic_write_test(Path(tmpdir))
        assert failures == [], f"Synthetic write test failed: {failures}"


# ── Full self-test mode ───────────────────────────────────────────────────────


class TestSelfTestMode:
    """T-OBS-012 — EC-M2-13: full self-test mode must pass end-to-end."""

    def test_self_test_mode_passes(self) -> None:
        """T-OBS-012: run_self_test() must pass (temp vault, all checks green)."""
        mod = _load_check_module()
        passed = mod.run_self_test(verbose=False)
        assert passed, "Obsidian self-test mode failed — see check_obsidian.py output"


# ── index.md check ───────────────────────────────────────────────────────────


class TestIndexMdCheck:
    """T-OBS-013..014 — K3: index.md must have valid frontmatter."""

    def test_valid_index_md_passes(self) -> None:
        """T-OBS-013: index.md with valid frontmatter must not produce failures."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            _write_fm(
                wiki_dir / "index.md",
                content="[[Vector Database]]\n[[Qdrant]]\n",
                type="catalogue",
                title="Synapse Index",
            )
            failures = mod.check_index_md(wiki_dir)
        assert failures == [], f"Expected no failures; got: {failures}"

    def test_missing_index_md_does_not_fail(self) -> None:
        """T-OBS-014: Missing index.md is tolerated (not an error before first ingest)."""
        mod = _load_check_module()
        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir)
            failures = mod.check_index_md(wiki_dir)
        assert failures == [], "Missing index.md should not fail (pre-ingest state)"

    def test_manual_obsidian_open_is_deferred(self) -> None:
        """
        T-OBS-015: EC-M2-13 (manual Obsidian open check with real LLM-generated pages)
        is a human gate — deferred to Emanuele's TrueNAS run after EC-M2-5 smoke matrix.
        This sentinel passes unconditionally.
        """
        assert True  # MANUAL GATE — EC-M2-13 / DEFERRED-TO-LIVE
