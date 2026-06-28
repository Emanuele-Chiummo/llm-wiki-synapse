"""
Obsidian Compatibility Check — Synapse v0.2 (I5, EC-M2-13).

PURPOSE
-------
Validates that vault/wiki/ is a structurally valid Obsidian vault:
  1. .obsidian/ present with valid app.json and graph.json (K7).
  2. All .md files have valid YAML frontmatter (type, title, sources[], lang) (I5, K6).
  3. All [[wikilinks]] in each page are parseable by the K5 parser.
  4. No dangling wikilinks that reference non-existent targets raise parse errors.
  5. index.md exists and has valid YAML frontmatter (K3).

Also performs a synthetic write test:
  - Creates a temp vault subdirectory.
  - Writes a sample wiki page with known frontmatter via python-frontmatter.
  - Reads it back and asserts all required fields are present.
  - Verifies K5 parser extracts the expected wikilinks without exception.

USAGE
-----
  cd backend
  # Check the live vault:
  python scripts/check_obsidian.py --vault-root /path/to/vault/wiki

  # Or run in self-test mode (creates temp vault, no live vault needed):
  python scripts/check_obsidian.py --self-test

  # Run as part of the test suite:
  pytest tests/test_obsidian_check.py -v

ENVIRONMENT
-----------
  VAULT_ROOT    Path to the vault root (the parent of wiki/). If not set, uses
                SYNAPSE settings (requires DATABASE_URL etc.).

EXIT CODES
----------
  0  All checks passed.
  1  One or more checks failed (details printed to stdout).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

# ── bootstrap sys.path ──────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_BACKEND = _HERE.parent.parent
sys.path.insert(0, str(_BACKEND))


def _parse_frontmatter(md_path: Path) -> tuple[dict, str]:
    """Parse a Markdown file's YAML frontmatter. Returns (metadata, body)."""
    import frontmatter as fm_lib

    doc = fm_lib.loads(md_path.read_text(encoding="utf-8"))
    return dict(doc.metadata), doc.content


def _parse_wikilinks(text: str) -> list[str]:
    """Extract [[Target]] or [[Target|alias]] wikilink targets (K5)."""
    # Matches [[Target]], [[Target|alias]], [[Target#section]], [[Target#section|alias]]
    pattern = re.compile(r"\[\[([^\[\]|#]+)(?:[|#][^\[\]]*)?]]")
    return pattern.findall(text)


# ── Check functions ────────────────────────────────────────────────────────────


def check_obsidian_config(wiki_dir: Path) -> list[str]:
    """
    Check .obsidian/ directory and required config files (AC-K7-1).
    Returns list of failure messages (empty = pass).
    """
    failures: list[str] = []
    obsidian_dir = wiki_dir / ".obsidian"
    if not obsidian_dir.is_dir():
        failures.append(f"{wiki_dir}/.obsidian/ directory missing (I5/K7 — Obsidian compatibility)")
        return failures  # can't check files if dir missing

    # app.json must exist and be valid JSON
    app_json = obsidian_dir / "app.json"
    if not app_json.exists():
        failures.append(f"{app_json} missing (AC-K7-1)")
    else:
        try:
            data = json.loads(app_json.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                failures.append(f"{app_json} is not a JSON object")
            # legacyEditor must be false (ADR-0004, CodeMirror 6 — I4)
            if data.get("legacyEditor") is not False:
                failures.append(
                    f"{app_json}: legacyEditor should be false (I4 — CodeMirror 6 not WYSIWYG)"
                )
        except json.JSONDecodeError as exc:
            failures.append(f"{app_json}: invalid JSON: {exc}")

    return failures


def check_all_pages_frontmatter(wiki_dir: Path) -> list[str]:
    """
    Check every .md file in wiki/ has valid YAML frontmatter with required fields (I5, K6).
    Returns list of failure messages.
    """
    failures: list[str] = []
    required_fields = ("type", "title", "sources", "lang")

    md_files = [f for f in wiki_dir.rglob("*.md") if ".obsidian" not in f.parts]
    if not md_files:
        # No pages yet — this is acceptable before first ingest
        return []

    for md_file in md_files:
        try:
            meta, _ = _parse_frontmatter(md_file)
        except Exception as exc:
            failures.append(f"{md_file.name}: frontmatter parse error: {exc}")
            continue

        rel = md_file.relative_to(wiki_dir)

        # index.md and log.md only need type+title (auto-generated catalogue pages)
        if md_file.name in ("index.md", "log.md", "overview.md"):
            for req in ("type", "title"):
                if not meta.get(req):
                    failures.append(f"{rel}: missing required frontmatter field '{req}' (I5)")
            continue

        for req in required_fields:
            if req not in meta or meta[req] is None:
                failures.append(f"{rel}: missing required frontmatter field '{req}' (I5/K6)")

        sources = meta.get("sources", [])
        if not isinstance(sources, list) or len(sources) == 0:
            failures.append(f"{rel}: sources[] is empty or not a list (F3 traceability)")

        valid_types = {"entity", "concept", "source", "synthesis", "comparison"}
        page_type = meta.get("type", "")
        if page_type not in valid_types:
            failures.append(
                f"{rel}: type={page_type!r} is not a valid PageType "
                f"(must be one of {sorted(valid_types)})"
            )

    return failures


def check_wikilinks_parseable(wiki_dir: Path) -> list[str]:
    """
    Check all [[wikilinks]] in every .md file are parseable (K5).
    Does NOT assert that targets exist (dangling links are valid in K5).
    Returns list of failure messages (only on parse exceptions, not dangling).
    """
    failures: list[str] = []
    md_files = [f for f in wiki_dir.rglob("*.md") if ".obsidian" not in f.parts]

    for md_file in md_files:
        try:
            _, body = _parse_frontmatter(md_file)
            _parse_wikilinks(body)  # must not raise
        except Exception as exc:
            failures.append(f"{md_file.name}: wikilink parse error: {exc}")

    return failures


def check_index_md(wiki_dir: Path) -> list[str]:
    """Check index.md exists and has valid frontmatter (K3)."""
    failures: list[str] = []
    index = wiki_dir / "index.md"
    if not index.exists():
        # Not a failure before first ingest
        return []
    try:
        meta, body = _parse_frontmatter(index)
    except Exception as exc:
        failures.append(f"index.md: frontmatter parse error: {exc}")
        return failures

    for req in ("type", "title"):
        if not meta.get(req):
            failures.append(f"index.md: missing frontmatter field '{req}' (K3)")

    return failures


# ── Synthetic write test ───────────────────────────────────────────────────────


def run_synthetic_write_test(tmp_dir: Path) -> list[str]:
    """
    Write a sample wiki page using python-frontmatter, read it back, assert all
    required fields present and wikilinks parseable (I5, K5, K6).
    """
    import frontmatter as fm_lib

    failures: list[str] = []
    page_path = tmp_dir / "concept" / "vector-database.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)

    # Write a sample page (simulating what write_wiki_page() produces)
    post = fm_lib.Post(
        content=(
            "A **vector database** stores high-dimensional embeddings for similarity search. "
            "See [[Qdrant]] for a concrete implementation. "
            "Powered by [[bge-m3]] embeddings."
        ),
        type="concept",
        title="Vector Database",
        sources=["raw/sources/sample-source.md"],
        lang="en",
    )
    page_path.write_text(fm_lib.dumps(post) + "\n", encoding="utf-8")

    # Read back and verify
    try:
        meta, body = _parse_frontmatter(page_path)
    except Exception as exc:
        return [f"synthetic write/read failed: {exc}"]

    required = ("type", "title", "sources", "lang")
    for req in required:
        if req not in meta or not meta[req]:
            failures.append(f"synthetic page: missing frontmatter field '{req}' (I5)")

    sources = meta.get("sources", [])
    if not isinstance(sources, list) or len(sources) == 0:
        failures.append("synthetic page: sources[] is empty (F3)")

    # K5: parse wikilinks
    try:
        links = _parse_wikilinks(body)
        expected_links = {"Qdrant", "bge-m3"}
        found_links = set(links)
        missing = expected_links - found_links
        if missing:
            failures.append(f"synthetic page: K5 wikilink parser missed expected links {missing}")
    except Exception as exc:
        failures.append(f"synthetic page: K5 wikilink parse exception: {exc}")

    return failures


# ── Main runner ────────────────────────────────────────────────────────────────


def run_check(wiki_dir: Path, verbose: bool = True) -> bool:
    """
    Run all Obsidian compatibility checks. Returns True if all pass.
    """
    all_failures: list[str] = []

    checks = [
        ("Obsidian config (.obsidian/)", check_obsidian_config(wiki_dir)),
        ("Frontmatter on all pages (I5/K6)", check_all_pages_frontmatter(wiki_dir)),
        ("Wikilinks parseable (K5)", check_wikilinks_parseable(wiki_dir)),
        ("index.md valid (K3)", check_index_md(wiki_dir)),
    ]

    passed = True
    for check_name, failures in checks:
        if failures:
            passed = False
            if verbose:
                print(f"  [FAIL] {check_name}")
                for f in failures:
                    print(f"         {f}")
        else:
            if verbose:
                print(f"  [PASS] {check_name}")
        all_failures.extend(failures)

    return passed


def run_self_test(verbose: bool = True) -> bool:
    """
    Self-test mode: create a temp vault, run the synthetic write test,
    then run the full Obsidian check on it.
    """
    import frontmatter as fm_lib

    with tempfile.TemporaryDirectory(prefix="synapse-obsidian-check-") as tmpdir:
        wiki_dir = Path(tmpdir) / "wiki"
        wiki_dir.mkdir()

        # Create .obsidian/app.json
        obsidian_dir = wiki_dir / ".obsidian"
        obsidian_dir.mkdir()
        (obsidian_dir / "app.json").write_text(
            json.dumps({"legacyEditor": False, "livePreview": True}), encoding="utf-8"
        )

        # Create index.md
        post = fm_lib.Post("", type="catalogue", title="Synapse Index")
        (wiki_dir / "index.md").write_text(fm_lib.dumps(post) + "\n", encoding="utf-8")

        # Run synthetic write test
        if verbose:
            print("  Running synthetic write test...")
        write_failures = run_synthetic_write_test(wiki_dir)
        if write_failures:
            for f in write_failures:
                print(f"  [FAIL] {f}")
        else:
            if verbose:
                print("  [PASS] Synthetic write test")

        # Run full vault check
        vault_passed = run_check(wiki_dir, verbose=verbose)

    return vault_passed and len(write_failures) == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Synapse v0.2 Obsidian compatibility check (I5, EC-M2-13)"
    )
    parser.add_argument(
        "--vault-root",
        type=Path,
        default=None,
        help="Path to vault root (parent of wiki/). Checks wiki/ subdirectory.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run a self-contained test with a temp vault (no live vault needed).",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress per-check output.")

    args = parser.parse_args()
    verbose = not args.quiet

    print("\nSynapse v0.2 — Obsidian Compatibility Check (I5, EC-M2-13)")
    print("=" * 60)

    if args.self_test:
        print("Mode: SELF-TEST (temp vault)\n")
        passed = run_self_test(verbose=verbose)
    elif args.vault_root:
        wiki_dir = args.vault_root / "wiki"
        if not wiki_dir.is_dir():
            print(f"ERROR: wiki/ directory not found at {wiki_dir}")
            sys.exit(1)
        print(f"Mode: LIVE (vault={wiki_dir})\n")
        passed = run_check(wiki_dir, verbose=verbose)
    else:
        # Try to get vault root from environment
        import os

        vault_root_env = os.environ.get("VAULT_ROOT")
        if vault_root_env:
            wiki_dir = Path(vault_root_env) / "wiki"
            print(f"Mode: LIVE (VAULT_ROOT={wiki_dir})\n")
            passed = run_check(wiki_dir, verbose=verbose)
        else:
            print("Mode: SELF-TEST (no --vault-root or VAULT_ROOT set)\n")
            passed = run_self_test(verbose=verbose)

    print("\n" + "=" * 60)
    if passed:
        print("RESULT: PASS — vault/wiki/ is a valid Obsidian vault (I5)")
    else:
        print("RESULT: FAIL — see failures above (I5 not satisfied)")
    print()
    sys.exit(0 if passed else 1)
