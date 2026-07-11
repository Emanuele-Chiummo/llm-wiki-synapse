"""
P3-c — wider Source-Watch types in the scheduled scan (v1.5 LLM Wiki parity).

Covers run_one_scan()'s new per-schedule behaviour:
  - default wider set imports extractable types (.csv) and writes the .extracted.md companion
  - allowed_extensions restricts which types are copied
  - excluded_folders skips files under named subfolders
  - max_size_mb skips oversized files (I7)
  - the config-parsing helpers are robust to NULL / non-str values

The scan takes a plain cfg object (no DB), so these tests construct a lightweight cfg stub.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest


def _cfg(source_dir: Path, **kw: object) -> SimpleNamespace:
    """Build a schedule-config stub with the given source_dir + optional P3-c fields."""
    return SimpleNamespace(
        source_dir=str(source_dir),
        allowed_extensions=kw.get("allowed_extensions"),
        excluded_folders=kw.get("excluded_folders"),
        max_size_mb=kw.get("max_size_mb"),
    )


def _prep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, recursive: bool = False) -> Path:
    """Point vault_root at tmp_path (so companions resolve) and return the source dir."""
    from app import config as cfg

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (tmp_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: tmp_path))
    monkeypatch.setattr(cfg.settings, "import_scan_max_files", 200)
    monkeypatch.setattr(cfg.settings, "import_scan_max_seconds", 60)
    monkeypatch.setattr(cfg.settings, "import_scan_recursive", recursive)
    return source_dir


# ── Config-parsing helpers (pure) ──────────────────────────────────────────────


class TestConfigHelpers:
    def test_effective_allowed_defaults_to_wider_set_when_null(self) -> None:
        from app.import_scheduler import _effective_allowed_extensions
        from app.upload import _ALLOWED_EXTENSIONS, _EXTRACTABLE_EXTENSIONS

        eff = _effective_allowed_extensions(SimpleNamespace(allowed_extensions=None))
        assert ".csv" in eff and ".pdf" in eff and ".md" in eff
        assert eff == frozenset(_ALLOWED_EXTENSIONS | _EXTRACTABLE_EXTENSIONS)

    def test_effective_allowed_respects_explicit_list(self) -> None:
        from app.import_scheduler import _effective_allowed_extensions

        eff = _effective_allowed_extensions(SimpleNamespace(allowed_extensions=".md, csv"))
        assert eff == frozenset({".md", ".csv"})

    def test_effective_allowed_ignores_unknown_and_placeholder(self) -> None:
        from app.import_scheduler import _effective_allowed_extensions

        # .png is a placeholder (not auto-imported); .xyz is unknown → both dropped, fall back to text
        eff = _effective_allowed_extensions(SimpleNamespace(allowed_extensions=".png,.xyz"))
        from app.upload import _ALLOWED_EXTENSIONS

        assert eff == frozenset(_ALLOWED_EXTENSIONS)

    def test_helpers_robust_to_non_str(self) -> None:
        from app.import_scheduler import (
            _max_size_bytes,
            _parse_excluded_folders,
            _parse_ext_csv,
        )

        assert _parse_ext_csv(object()) == set()
        assert _parse_excluded_folders(SimpleNamespace(excluded_folders=object())) == set()
        assert _max_size_bytes(SimpleNamespace(max_size_mb=object())) is None
        assert _max_size_bytes(SimpleNamespace(max_size_mb=True)) is None
        assert _max_size_bytes(SimpleNamespace(max_size_mb=0)) is None
        assert _max_size_bytes(SimpleNamespace(max_size_mb=5)) == 5 * 1024 * 1024


# ── Scan behaviour ─────────────────────────────────────────────────────────────


class TestWiderScan:
    @pytest.mark.asyncio
    async def test_default_imports_csv_and_writes_companion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Default (NULL config) wider set imports a .csv and writes its .extracted.md companion."""
        from app.import_scheduler import run_one_scan

        source_dir = _prep(tmp_path, monkeypatch)
        (source_dir / "data.csv").write_text("name,score\nAlice,10\nBob,7\n")

        count, status, error = await run_one_scan(_cfg(source_dir))

        raw_sources = tmp_path / "raw" / "sources"
        assert status == "ok" and error is None
        assert count == 1
        assert (raw_sources / "data.csv").exists(), "binary/original preserved in raw/sources"
        companion = raw_sources / "data.extracted.md"
        assert companion.exists(), "extractable type must produce a .extracted.md companion"
        text = companion.read_text()
        assert text.startswith("---\ntype: source\n"), "valid Obsidian YAML frontmatter (I5)"
        assert 'sources: ["raw/sources/data.csv"]' in text
        assert "| name | score |" in text, "csv rendered as GFM table"

    @pytest.mark.asyncio
    async def test_text_file_has_no_companion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A plain .md is copied as-is with no companion (it is ingested directly)."""
        from app.import_scheduler import run_one_scan

        source_dir = _prep(tmp_path, monkeypatch)
        (source_dir / "note.md").write_text("# hi\n")

        count, status, _ = await run_one_scan(_cfg(source_dir))
        raw_sources = tmp_path / "raw" / "sources"
        assert count == 1 and status == "ok"
        assert (raw_sources / "note.md").exists()
        assert not (raw_sources / "note.extracted.md").exists()

    @pytest.mark.asyncio
    async def test_allowed_extensions_restricts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """allowed_extensions='.md' imports the .md and skips the .csv."""
        from app.import_scheduler import run_one_scan

        source_dir = _prep(tmp_path, monkeypatch)
        (source_dir / "keep.md").write_text("# keep\n")
        (source_dir / "drop.csv").write_text("a,b\n1,2\n")

        count, status, _ = await run_one_scan(_cfg(source_dir, allowed_extensions=".md"))
        raw_sources = tmp_path / "raw" / "sources"
        assert status == "ok" and count == 1
        assert (raw_sources / "keep.md").exists()
        assert not (raw_sources / "drop.csv").exists()

    @pytest.mark.asyncio
    async def test_excluded_folders_skips_subtree(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """excluded_folders='skip' drops files under source/skip/ (recursive scan)."""
        from app.import_scheduler import run_one_scan

        source_dir = _prep(tmp_path, monkeypatch, recursive=True)
        (source_dir / "keep").mkdir()
        (source_dir / "skip").mkdir()
        (source_dir / "keep" / "a.md").write_text("# a\n")
        (source_dir / "skip" / "b.md").write_text("# b\n")

        count, status, _ = await run_one_scan(_cfg(source_dir, excluded_folders="skip"))
        raw_sources = tmp_path / "raw" / "sources"
        assert status == "ok" and count == 1
        assert (raw_sources / "a.md").exists()
        assert not (raw_sources / "b.md").exists()

    @pytest.mark.asyncio
    async def test_max_size_mb_skips_oversized(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """max_size_mb=1 skips a >1 MB file but keeps a small one (I7)."""
        from app.import_scheduler import run_one_scan

        source_dir = _prep(tmp_path, monkeypatch)
        (source_dir / "small.md").write_text("# small\n")
        (source_dir / "big.md").write_text("x" * (2 * 1024 * 1024))  # 2 MB

        count, status, _ = await run_one_scan(_cfg(source_dir, max_size_mb=1))
        raw_sources = tmp_path / "raw" / "sources"
        assert status == "ok" and count == 1
        assert (raw_sources / "small.md").exists()
        assert not (raw_sources / "big.md").exists()
