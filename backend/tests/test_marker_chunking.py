"""
Unit tests for the Marker service's large-PDF page-range chunking (ADR-0065).

The real split/convert path needs pypdfium2 + marker-pdf (marker venv only, not the backend
test venv), so these tests exercise the ORCHESTRATION logic of `_convert_pdf_bytes` with the
pypdfium2/marker-dependent helpers monkeypatched out:

  _count_pages      → how many pages the PDF has (decides whole-file vs chunk)
  _build_converter  → the shared model set (loaded once)
  _split_pdf_pages  → the pypdfium2 page-range split
  _convert_one      → one Marker call → markdown

Covered:
  - small / uncountable PDF → single whole-file conversion (no split)
  - large PDF → split into page-range chunks, converted serially, markdown concatenated in order
  - split failure → graceful fallback to a single whole-file conversion
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_SERVICE_PATH = Path(__file__).resolve().parents[2] / "tools" / "marker-converter" / "service.py"


def _load_service() -> Any:
    spec = importlib.util.spec_from_file_location("marker_service_chunking", _SERVICE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pytestmark = pytest.mark.skipif(
    not _SERVICE_PATH.exists(), reason="marker service source not present"
)


def _patch_common(svc: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the model build so no Surya/torch load happens in the test venv."""
    monkeypatch.setattr(svc, "_build_converter", lambda: object())


def test_small_pdf_converts_whole_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """<= pages_per_chunk → one whole-file conversion; split is never invoked."""
    svc = _load_service()
    _patch_common(svc, monkeypatch)
    monkeypatch.setattr(svc, "_count_pages", lambda _p: 10)
    monkeypatch.setattr(svc, "_convert_one", lambda _c, _p: "WHOLE")

    def _boom(*_a: Any, **_k: Any) -> Any:  # split must NOT be called
        raise AssertionError("_split_pdf_pages must not run for a small PDF")

    monkeypatch.setattr(svc, "_split_pdf_pages", _boom)

    result = svc._convert_pdf_bytes(b"%PDF-1.4 fake", filename="small.pdf", pages_per_chunk=25)
    assert result == {"markdown": "WHOLE", "pages": 10, "chunks": 1}


def test_uncountable_pdf_converts_whole_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """page count 0 (unknown) → whole-file, never chunked."""
    svc = _load_service()
    _patch_common(svc, monkeypatch)
    monkeypatch.setattr(svc, "_count_pages", lambda _p: 0)
    monkeypatch.setattr(svc, "_convert_one", lambda _c, _p: "WHOLE0")
    monkeypatch.setattr(
        svc, "_split_pdf_pages", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no split"))
    )

    result = svc._convert_pdf_bytes(b"%PDF-1.4 fake", filename="weird.pdf", pages_per_chunk=25)
    assert result == {"markdown": "WHOLE0", "pages": 0, "chunks": 1}


def test_large_pdf_splits_and_concatenates_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """> pages_per_chunk → split; each chunk converted; markdown joined in page order."""
    svc = _load_service()
    _patch_common(svc, monkeypatch)
    monkeypatch.setattr(svc, "_count_pages", lambda _p: 60)

    # Fake split → 3 chunk paths; converter returns the chunk stem so order is verifiable.
    def _fake_split(_src: Path, _ppc: int, dest: Path) -> list[Path]:
        return [dest / "chunk_0000.pdf", dest / "chunk_0001.pdf", dest / "chunk_0002.pdf"]

    monkeypatch.setattr(svc, "_split_pdf_pages", _fake_split)
    monkeypatch.setattr(svc, "_convert_one", lambda _c, p: Path(p).stem.upper())

    result = svc._convert_pdf_bytes(b"%PDF-1.4 fake", filename="big.pdf", pages_per_chunk=25)
    assert result["pages"] == 60
    assert result["chunks"] == 3
    assert result["markdown"] == "CHUNK_0000\n\nCHUNK_0001\n\nCHUNK_0002"


def test_split_failure_falls_back_to_whole_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """If splitting raises, fall back to a single whole-file conversion (never crash)."""
    svc = _load_service()
    _patch_common(svc, monkeypatch)
    monkeypatch.setattr(svc, "_count_pages", lambda _p: 60)

    def _split_boom(*_a: Any, **_k: Any) -> list[Path]:
        raise RuntimeError("pypdfium2 exploded")

    monkeypatch.setattr(svc, "_split_pdf_pages", _split_boom)
    monkeypatch.setattr(svc, "_convert_one", lambda _c, _p: "FALLBACK")

    result = svc._convert_pdf_bytes(b"%PDF-1.4 fake", filename="big.pdf", pages_per_chunk=25)
    assert result == {"markdown": "FALLBACK", "pages": 60, "chunks": 1}


def test_chunking_disabled_when_pages_per_chunk_nonpositive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pages_per_chunk <= 0 disables chunking → whole-file even for a big PDF."""
    svc = _load_service()
    _patch_common(svc, monkeypatch)
    monkeypatch.setattr(svc, "_count_pages", lambda _p: 500)
    monkeypatch.setattr(svc, "_convert_one", lambda _c, _p: "WHOLE_BIG")
    monkeypatch.setattr(
        svc, "_split_pdf_pages", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no split"))
    )

    result = svc._convert_pdf_bytes(b"%PDF-1.4 fake", filename="big.pdf", pages_per_chunk=0)
    assert result == {"markdown": "WHOLE_BIG", "pages": 500, "chunks": 1}
