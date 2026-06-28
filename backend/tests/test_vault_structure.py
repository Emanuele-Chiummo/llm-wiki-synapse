"""
Tests for K1 vault skeleton and K7 Obsidian compatibility (infra-free filesystem tests).

Coverage:
  AC-K1-1  raw/sources/ and raw/assets/ exist
  AC-K1-2  wiki/index.md and wiki/log.md exist with valid YAML frontmatter (type+title)
  AC-K1-3  schema.md exists and contains 'type', 'title', 'sources'
  AC-K1-4  purpose.md exists and is non-empty
  AC-K1-5  watcher never writes to vault/raw/ (code-path + no-directory-walk guard)
  AC-K7-1  .obsidian/app.json created on bootstrap; valid JSON; legacyEditor=false
  AC-K7-2  all service-written wiki .md files parse without exception with python-frontmatter
  AC-K7-3  MANUAL — not tested here; see sign-off register

Test IDs (T-VAULT-*):
  T-VAULT-001 .. T-VAULT-012

No-directory-walk guard (I1 static assert):
  T-VAULT-013  grep scan of watcher.py for forbidden enumeration calls
               (rglob, os.walk, listdir, glob.glob, scandir used for full walk)
"""

from __future__ import annotations

import json
from pathlib import Path

import frontmatter as fm
import pytest
from app.vault import bootstrap_vault

# ── Fixture: isolated temp vault ──────────────────────────────────────────────


@pytest.fixture()
def temp_vault(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """
    Create a fresh temporary vault root and point Settings at it.

    This fixture is completely filesystem-isolated — no real vault is touched.
    """
    vault_root = tmp_path / "vault"
    vault_root.mkdir()

    # Patch Settings.vault_root so bootstrap_vault writes to our temp dir
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    # bootstrap_vault() reads settings.vault_root (a property on vault_path)
    # We need to ensure the property resolves to our temp path
    monkeypatch.setattr(
        type(cfg.settings),
        "vault_root",
        property(lambda self: vault_root),
    )
    bootstrap_vault()
    return vault_root


# ── AC-K1-1: raw/ subdirectories exist ────────────────────────────────────────


class TestRawSubdirectories:
    """T-VAULT-001, T-VAULT-002 — AC-K1-1"""

    def test_raw_sources_exists(self, temp_vault: Path) -> None:
        """T-VAULT-001: vault/raw/sources/ must exist as a directory."""
        assert (
            temp_vault / "raw" / "sources"
        ).is_dir(), "vault/raw/sources/ directory must exist after bootstrap"

    def test_raw_assets_exists(self, temp_vault: Path) -> None:
        """T-VAULT-002: vault/raw/assets/ must exist as a directory."""
        assert (
            temp_vault / "raw" / "assets"
        ).is_dir(), "vault/raw/assets/ directory must exist after bootstrap"


# ── AC-K1-2: wiki seed files with valid YAML frontmatter ──────────────────────


class TestWikiSeedFiles:
    """T-VAULT-003, T-VAULT-004 — AC-K1-2"""

    def test_index_md_exists_with_frontmatter(self, temp_vault: Path) -> None:
        """T-VAULT-003: vault/wiki/index.md must exist and have type+title in frontmatter."""
        p = temp_vault / "wiki" / "index.md"
        assert p.exists(), "vault/wiki/index.md must exist"
        doc = fm.load(str(p))
        assert "type" in doc.metadata, "index.md must have 'type' in frontmatter"
        assert "title" in doc.metadata, "index.md must have 'title' in frontmatter"

    def test_log_md_exists_with_frontmatter(self, temp_vault: Path) -> None:
        """T-VAULT-004: vault/wiki/log.md must exist and have type+title in frontmatter."""
        p = temp_vault / "wiki" / "log.md"
        assert p.exists(), "vault/wiki/log.md must exist"
        doc = fm.load(str(p))
        assert "type" in doc.metadata, "log.md must have 'type' in frontmatter"
        assert "title" in doc.metadata, "log.md must have 'title' in frontmatter"


# ── AC-K1-3: schema.md content ────────────────────────────────────────────────


class TestSchemaMd:
    """T-VAULT-005 — AC-K1-3"""

    def test_schema_md_documents_required_fields(self, temp_vault: Path) -> None:
        """T-VAULT-005: vault/schema.md must contain 'type', 'title', 'sources'."""
        p = temp_vault / "schema.md"
        assert p.exists(), "vault/schema.md must exist"
        text = p.read_text(encoding="utf-8")
        for field in ("type", "title", "sources"):
            assert field in text, f"schema.md must document the '{field}' frontmatter field"


# ── AC-K1-4: purpose.md exists ────────────────────────────────────────────────


class TestPurposeMd:
    """T-VAULT-006 — AC-K1-4"""

    def test_purpose_md_exists_and_non_empty(self, temp_vault: Path) -> None:
        """T-VAULT-006: vault/purpose.md must exist and be non-empty."""
        p = temp_vault / "purpose.md"
        assert p.exists(), "vault/purpose.md must exist"
        assert p.stat().st_size > 0, "vault/purpose.md must be non-empty"


# ── AC-K7-1: .obsidian/app.json ───────────────────────────────────────────────


class TestObsidianConfig:
    """T-VAULT-007, T-VAULT-008 — AC-K7-1"""

    def test_obsidian_app_json_exists(self, temp_vault: Path) -> None:
        """T-VAULT-007: vault/wiki/.obsidian/app.json must be created by bootstrap."""
        p = temp_vault / "wiki" / ".obsidian" / "app.json"
        assert p.exists(), "vault/wiki/.obsidian/app.json must be created on startup"

    def test_obsidian_app_json_valid_json_with_legacy_editor_false(self, temp_vault: Path) -> None:
        """T-VAULT-008: app.json must be valid JSON and contain legacyEditor=false."""
        p = temp_vault / "wiki" / ".obsidian" / "app.json"
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)  # raises if not valid JSON
        assert data.get("legacyEditor") is False, "app.json must have legacyEditor=false (AC-K7-1)"

    def test_obsidian_app_json_idempotent(self, temp_vault: Path) -> None:
        """T-VAULT-009: bootstrap_vault() called twice must not overwrite existing app.json."""
        p = temp_vault / "wiki" / ".obsidian" / "app.json"
        mtime_before = p.stat().st_mtime
        bootstrap_vault()  # second call
        mtime_after = p.stat().st_mtime
        assert (
            mtime_after == mtime_before
        ), "bootstrap_vault() must not overwrite app.json if it already exists"


# ── AC-K7-2: all service-written wiki .md files have valid frontmatter ─────────


class TestWikiFileFrontmatter:
    """T-VAULT-010 — AC-K7-2"""

    def test_all_wiki_md_files_parse_without_exception(self, temp_vault: Path) -> None:
        """T-VAULT-010: iterate all .md files in wiki/; none may raise on fm.load."""
        wiki_dir = temp_vault / "wiki"
        md_files = list(wiki_dir.rglob("*.md"))
        assert len(md_files) >= 2, "At minimum index.md and log.md must exist"
        for path in md_files:
            try:
                doc = fm.load(str(path))
                # Must have at minimum a non-empty frontmatter block
                assert isinstance(
                    doc.metadata, dict
                ), f"{path.name} must have a frontmatter metadata dict"
            except Exception as exc:
                pytest.fail(f"frontmatter.load raised on {path.name}: {exc}")


# ── AC-K1-5 static guard: no write calls to raw/ in watcher.py ────────────────


class TestNoWriteToRaw:
    """T-VAULT-011, T-VAULT-012 — AC-K1-5"""

    def test_watcher_py_does_not_open_raw_for_write(self) -> None:
        """
        T-VAULT-011: Static scan of watcher.py for write-mode open() calls
        targeting paths under raw/.

        This is a deterministic static assertion — if the string 'raw/' appears
        adjacent to an open mode 'w' in watcher.py, the test fails.
        Pattern: scan for lines with open(..., 'w') or 'w' mode combined with raw/.
        """
        watcher_path = Path(__file__).resolve().parent.parent / "app" / "watcher.py"
        text = watcher_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for i, line in enumerate(lines, start=1):
            # Look for write-mode file opens near 'raw'
            stripped = line.strip()
            if (
                "open(" in stripped
                and ("'w'" in stripped or '"w"' in stripped)
                and "raw" in stripped
            ):
                pytest.fail(
                    f"watcher.py line {i} appears to open a file under raw/ for writing "
                    f"(AC-K1-5 violation): {stripped!r}"
                )

    def test_vault_bootstrap_does_not_write_to_raw(self, temp_vault: Path) -> None:
        """
        T-VAULT-012: After bootstrap_vault(), no files exist under vault/raw/
        except the directories themselves (.gitkeep files allowed but none created
        by bootstrap).

        Assert raw/sources/ and raw/assets/ are empty directories.
        """
        sources = temp_vault / "raw" / "sources"
        assets = temp_vault / "raw" / "assets"
        assert sources.is_dir()
        assert assets.is_dir()
        # bootstrap must not have written any files into raw/
        raw_files = [p for p in (temp_vault / "raw").rglob("*") if p.is_file()]
        assert raw_files == [], (
            f"bootstrap_vault() must not write files under vault/raw/; "
            f"found: {[str(f) for f in raw_files]}"
        )


# ── I1 static guard: no full directory enumeration in watcher.py ──────────────


class TestNoDirectoryWalk:
    """
    T-VAULT-013 — I1 invariant static guard.

    The watcher MUST NOT contain calls to os.listdir, os.walk, rglob, or
    glob.glob for directory enumeration (would constitute a full rescan, violating I1).

    os.scandir with next() for a non-empty check is allowed (AQ-3 / ADR-0006).
    The test specifically looks for patterns that enumerate ALL directory entries,
    not just check whether the directory is non-empty.
    """

    @staticmethod
    def _non_comment_lines(path: Path) -> list[str]:
        """Return code lines from a Python file, excluding full-line comments and docstrings."""
        lines = path.read_text(encoding="utf-8").splitlines()
        result = []
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            # Toggle docstring state on triple-quote boundaries
            if '"""' in stripped or "'''" in stripped:
                count = stripped.count('"""') + stripped.count("'''")
                if count % 2 != 0:  # odd number → entering or exiting docstring
                    in_docstring = not in_docstring
                if in_docstring:
                    continue
            if in_docstring:
                continue
            # Skip full-line comments
            if stripped.startswith("#"):
                continue
            result.append(line)
        return result

    def test_watcher_has_no_full_directory_enumeration(self) -> None:
        """T-VAULT-013: watcher.py must not use os.listdir, os.walk, rglob, or glob.glob
        in executable code (comments/docstrings explaining the absence are allowed)."""
        watcher_path = Path(__file__).resolve().parent.parent / "app" / "watcher.py"
        code_lines = self._non_comment_lines(watcher_path)
        code_text = "\n".join(code_lines)
        forbidden = ["os.listdir", "os.walk", ".rglob(", "glob.glob"]
        for pattern in forbidden:
            assert pattern not in code_text, (
                f"watcher.py contains forbidden directory enumeration pattern "
                f"{pattern!r} in executable code — violates I1 (no full rescan)"
            )

    def test_orchestrator_has_no_full_directory_enumeration(self) -> None:
        """T-VAULT-014: orchestrator.py must not walk the vault directory either."""
        orch_path = Path(__file__).resolve().parent.parent / "app" / "ingest" / "orchestrator.py"
        code_lines = self._non_comment_lines(orch_path)
        code_text = "\n".join(code_lines)
        forbidden = ["os.listdir", "os.walk", ".rglob(", "glob.glob"]
        for pattern in forbidden:
            assert pattern not in code_text, (
                f"orchestrator.py contains forbidden directory enumeration pattern "
                f"{pattern!r} in executable code — violates I1 (no full rescan)"
            )


# ── AC-K7-3 MANUAL: recorded here as a sentinel ───────────────────────────────


class TestManualGates:
    """Sentinel tests that document manual gates — they always pass."""

    def test_ac_k7_3_is_manual_gate(self) -> None:
        """
        T-VAULT-MANUAL-001: AC-K7-3 (opening vault/wiki/ in Obsidian shows no errors)
        is NOT automatable by pytest.

        This test records the gap in the suite and passes unconditionally.
        The real gate is the human checkpoint in EC-1 / EC-15 sign-off register.
        Status in TRACEABILITY.md: MANUAL (GAP-1 / AQ-5).
        """
        # MANUAL GATE — see TRACEABILITY.md AC-K7-3
        assert True, "This test is a sentinel; AC-K7-3 requires human verification"
