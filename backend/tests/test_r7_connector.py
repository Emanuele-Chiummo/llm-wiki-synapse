"""
Sprint v0.7 connector tests (R7-7): ServiceNow watch-dir daemon unit tests.

These tests exercise the scheduler logic in tools/marker-converter/servicenow_connector.py
WITHOUT requiring Marker or pypdfium2 (I9 — no new service). Marker's converter is
injected as a stub; bookmarks are mocked.

Coverage:
  R7-7-1  run_watch_tick finds and processes new PDFs ..... test_tick_converts_new_pdf
  R7-7-2  max_per_tick cap enforced (I7) .................. test_tick_cap_enforced
  R7-7-2  hash gate: already-seen PDF is skipped .......... test_tick_hash_gate
  R7-7-4  auto-download stub logs warning and exits ........ test_auto_download_stub
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path

import pytest

# Add the connector's parent to sys.path so we can import it directly
_CONNECTOR_DIR = Path(__file__).resolve().parent.parent.parent / "tools" / "marker-converter"
if str(_CONNECTOR_DIR) not in sys.path:
    sys.path.insert(0, str(_CONNECTOR_DIR))


# ── Stub types (stand-ins for Bookmark / Section / Marker output) ───────────────


class _FakeBookmark:
    def __init__(self, level: int, title: str, page_index: int) -> None:
        self.level = level
        self.title = title
        self.page_index = page_index


# ── Fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def watch_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """
    Isolated tmp dir with a watch_dir containing a fake PDF and an out_dir.
    Mocks read_bookmarks, page_count, and select_sections in the connector module
    so no real Marker/pypdfium2 is needed.
    """
    import servicenow_connector as sc

    watch_dir = tmp_path / "watch"
    out_dir = tmp_path / "out"
    watch_dir.mkdir()
    out_dir.mkdir()

    # Create a minimal fake PDF (just bytes — connector hashes it, doesn't parse unless mocked)
    fake_pdf = watch_dir / "fake-module.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake content for hash gate testing")

    # Minimal stubs: bookmarks, page_count, select_sections, render_page
    def _fake_read_bookmarks(pdf_path: Path):  # noqa: ANN001, ANN202
        return []

    def _fake_page_count(pdf_path: Path) -> int:  # noqa: ANN001
        return 10

    @dataclass
    class _FakeSection:
        title: str = "Fake Section"
        module_title: str = "IT Asset Management"
        module_code: str = "ITAM"
        feature_title: str = "Software Asset Management"
        feature_code: str = "SAM"
        group_title: str = "Exploring"
        start_page: int = 0
        end_page: int = 2
        body: str = ""
        fields: dict = dc_field(default_factory=dict)

    def _fake_select_sections(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        return [_FakeSection()]

    def _fake_render_page(sec, source_label, source_url):  # noqa: ANN001, ANN202
        return f"---\ntitle: {sec.title}\n---\n\nFake content.\n"

    monkeypatch.setattr(sc, "read_bookmarks", _fake_read_bookmarks)
    monkeypatch.setattr(sc, "page_count", _fake_page_count)
    monkeypatch.setattr(sc, "select_sections", _fake_select_sections)
    monkeypatch.setattr(sc, "render_page", _fake_render_page)

    # Stub converter: just returns a simple markdown string (no Marker needed)
    def _stub_convert(pdf_path: Path, start_page: int, end_page: int) -> str:
        return "## Fake Section\n\nFake converted content.\n"

    class _Env:
        def __init__(self) -> None:
            self.watch_dir = watch_dir
            self.out_dir = out_dir
            self.fake_pdf = fake_pdf
            self.convert_fn = _stub_convert
            self.sc = sc

    return _Env()


# ── R7-7-1 — new PDF is converted and dropped into out_dir ─────────────────────


def test_tick_converts_new_pdf(watch_env: Any) -> None:
    """
    AC-R7-7-1: A new PDF in watch_dir is converted and its output appears in out_dir.
    """
    import servicenow_connector as sc

    result = sc.run_watch_tick(
        watch_dir=watch_env.watch_dir,
        out_dir=watch_env.out_dir,
        convert_fn=watch_env.convert_fn,
    )

    assert result["total_files_converted"] == 1, f"Expected 1 converted, got {result}"
    assert result["total_cost_usd"] == 0, "Marker is local — cost must be 0"

    # Output file must exist under out_dir/servicenow/
    sn_dir = watch_env.out_dir / "servicenow"
    md_files = list(sn_dir.rglob("*.md"))
    assert len(md_files) >= 1, f"Expected .md output files, got {md_files}"


# ── R7-7-2 — hash gate: already-converted PDF is skipped ───────────────────────


def test_tick_hash_gate(watch_env: Any) -> None:
    """
    AC-R7-7-2: A PDF already in the state file is NOT re-converted on the next tick.
    """
    import servicenow_connector as sc

    # First tick — converts the PDF
    result1 = sc.run_watch_tick(
        watch_dir=watch_env.watch_dir,
        out_dir=watch_env.out_dir,
        convert_fn=watch_env.convert_fn,
    )
    assert result1["total_files_converted"] == 1

    # Second tick — same PDF, already in state; must be skipped
    result2 = sc.run_watch_tick(
        watch_dir=watch_env.watch_dir,
        out_dir=watch_env.out_dir,
        convert_fn=watch_env.convert_fn,
    )
    assert (
        result2["total_files_converted"] == 0
    ), "AC-R7-7-2: Already-converted PDF must be skipped on the next tick (hash gate, I1 spirit)"


# ── R7-7-2 — I7 cap: max_per_tick limits PDFs processed ───────────────────────


def test_tick_cap_enforced(watch_env: Any) -> None:
    """
    AC-R7-7-2: At most max_per_tick PDFs are converted per tick (I7 cap).
    """
    import servicenow_connector as sc

    # Create 5 fake PDFs, but cap at 2
    for i in range(5):
        (watch_env.watch_dir / f"doc-{i}.pdf").write_bytes(f"%PDF-1.4 document {i}".encode())

    result = sc.run_watch_tick(
        watch_dir=watch_env.watch_dir,
        out_dir=watch_env.out_dir,
        max_per_tick=2,
        convert_fn=watch_env.convert_fn,
    )

    # Only 2 should be converted despite 5+1 available (the original fake + 5 new)
    assert (
        result["total_files_converted"] <= 2
    ), f"AC-R7-7-2: max_per_tick=2 cap must be enforced; got {result['total_files_converted']}"


# ── R7-7-4 — auto-download stub logs a warning ─────────────────────────────────


def test_auto_download_stub(capsys: pytest.CaptureFixture) -> None:  # type: ignore[type-arg]
    """
    AC-R7-7-4: _auto_download_stub() logs a clear 'not implemented' warning to stderr
    and does NOT raise or attempt to scrape docs.servicenow.com.
    """
    import servicenow_connector as sc

    sc._auto_download_stub()
    captured = capsys.readouterr()
    # Must log to stderr and mention "not implemented" (case-insensitive)
    assert (
        "not implemented" in captured.err.lower() or "not implemented" in captured.out.lower()
    ), "AC-R7-7-4: _auto_download_stub must log a clear 'not implemented' warning"


# ── --help smoke check ─────────────────────────────────────────────────────────


def test_connector_help_smoke() -> None:
    """
    Smoke test: servicenow_connector.py --help exits cleanly (not crashes).
    Documents the --watch-dir and --interval-minutes flags are present.
    """
    import subprocess

    result = subprocess.run(
        [sys.executable, str(_CONNECTOR_DIR / "servicenow_connector.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, f"--help exited with {result.returncode}: {result.stderr}"
    assert "--watch-dir" in result.stdout, "--watch-dir flag must be documented in --help"
    assert "--interval-minutes" in result.stdout, "--interval-minutes flag must be in --help"
    assert "--auto-download" in result.stdout, "--auto-download stub flag must be in --help"


# Type stub for pytest Any
from typing import Any  # noqa: E402
