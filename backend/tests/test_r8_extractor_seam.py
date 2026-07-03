"""
R8-1 — Pluggable PDF extractor seam (ADR-0051).

Tests:
  T-R81-001  Default PDF_EXTRACTOR=pypdf: marker helper is NOT called (AC-R8-1-2)
  T-R81-002  PDF_EXTRACTOR=marker: _extract_pdf_via_marker is called first (AC-R8-1-1)
  T-R81-003  Marker success: returned markdown is used, not pypdf (AC-R8-1-1)
  T-R81-004  Fallback on httpx.ConnectError: pypdf called instead (AC-R8-1-1)
  T-R81-005  Fallback on httpx.ReadTimeout: pypdf called instead (I7)
  T-R81-006  Fallback on non-200 response: pypdf called instead (AC-R8-1-1)
  T-R81-007  Fallback on invalid JSON (missing 'markdown'): pypdf called instead
  T-R81-008  EXTRACT_MAX_CHARS cap applied to Marker result (I7)
  T-R81-009  _extract_pdf_via_marker sends multipart 'file' field to /convert (AC-R8-1-1)
  T-R81-010  PDF_EXTRACTOR=pypdf: existing pypdf tests unaffected (AC-R8-1-2)
  T-R81-011  config.py exposes pdf_extractor / marker_service_url / marker_timeout_seconds
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest  # noqa: E402

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_minimal_pdf() -> bytes:
    """Return a minimal (empty) PDF bytes object."""
    try:
        import io

        from pypdf import PdfWriter

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n%%EOF"


# ── T-R81-011: config.py settings ────────────────────────────────────────────


class TestConfigSettings:
    """T-R81-011: config.py exposes the three R8-1 env vars."""

    def test_pdf_extractor_default(self) -> None:
        """pdf_extractor defaults to 'pypdf'."""
        from app.config import settings

        assert settings.pdf_extractor == "pypdf"

    def test_marker_service_url_default(self) -> None:
        """marker_service_url defaults to host.docker.internal URL."""
        from app.config import settings

        assert "8555" in settings.marker_service_url
        assert "marker_service_url" not in settings.model_config  # type: ignore[operator]

    def test_marker_timeout_seconds_default(self) -> None:
        """marker_timeout_seconds defaults to 120.0."""
        from app.config import settings

        assert settings.marker_timeout_seconds == pytest.approx(120.0)


# ── T-R81-001: Default path bypasses marker ──────────────────────────────────


class TestDefaultPypdfPath:
    """T-R81-001/010: With pdf_extractor='pypdf', marker helper is never called."""

    def test_pypdf_path_does_not_call_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PDF_EXTRACTOR=pypdf (default): _extract_pdf_via_marker is never invoked."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "pdf_extractor", "pypdf")

        marker_called: list[bool] = []
        original_marker = ext_module._extract_pdf_via_marker

        def fake_marker(path: Path) -> str | None:
            marker_called.append(True)
            return original_marker(path)

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        with patch.object(ext_module, "_extract_pdf_via_marker", side_effect=fake_marker):
            ext_module.extract_text(pdf_file)

        assert (
            not marker_called
        ), "_extract_pdf_via_marker must NOT be called when PDF_EXTRACTOR=pypdf"

    def test_pypdf_path_calls_extract_pdf(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PDF_EXTRACTOR=pypdf: the _extract_pdf (pypdf) helper is called."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "pdf_extractor", "pypdf")

        pypdf_called: list[bool] = []
        original_extract_pdf = ext_module._extract_pdf

        def fake_pypdf(path: Path) -> str:
            pypdf_called.append(True)
            return original_extract_pdf(path)

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        with patch.object(ext_module, "_extract_pdf", side_effect=fake_pypdf):
            ext_module.extract_text(pdf_file)

        assert pypdf_called, "_extract_pdf (pypdf) must be called when PDF_EXTRACTOR=pypdf"


# ── T-R81-002/003: Marker path — success ─────────────────────────────────────


class TestMarkerPathSuccess:
    """T-R81-002/003: PDF_EXTRACTOR=marker — marker is called; returned markdown is used."""

    def test_marker_called_when_configured(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PDF_EXTRACTOR=marker: _extract_pdf_via_marker is invoked."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "pdf_extractor", "marker")

        marker_called: list[bool] = []

        def fake_marker(path: Path) -> str | None:
            marker_called.append(True)
            return "# Marker output\n\nSome text."

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        with patch.object(ext_module, "_extract_pdf_via_marker", side_effect=fake_marker):
            result = ext_module.extract_text(pdf_file)

        assert marker_called, "_extract_pdf_via_marker must be called when PDF_EXTRACTOR=marker"
        assert "Marker output" in result, "Marker result should be returned when successful"

    def test_marker_result_used_not_pypdf(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When Marker succeeds, pypdf is NOT called (AC-R8-1-1)."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "pdf_extractor", "marker")

        pypdf_called: list[bool] = []
        original_pypdf = ext_module._extract_pdf

        def fake_pypdf(path: Path) -> str:
            pypdf_called.append(True)
            return original_pypdf(path)

        def fake_marker(path: Path) -> str | None:
            return "# From Marker"

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        with (
            patch.object(ext_module, "_extract_pdf_via_marker", side_effect=fake_marker),
            patch.object(ext_module, "_extract_pdf", side_effect=fake_pypdf),
        ):
            result = ext_module.extract_text(pdf_file)

        assert not pypdf_called, "pypdf must NOT be called when Marker succeeds"
        assert "From Marker" in result


# ── T-R81-004/005/006/007: Fallback paths ─────────────────────────────────────


class TestMarkerFallback:
    """T-R81-004..007: Any Marker failure causes unconditional pypdf fallback."""

    def _setup(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        marker_return: str | None | Exception,
    ) -> tuple[list, list]:
        """Set up mocks and run extract_text; return (pypdf_calls, marker_calls)."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "pdf_extractor", "marker")

        pypdf_calls: list[str] = []
        marker_calls: list[str] = []

        def fake_pypdf(path: Path) -> str:
            pypdf_calls.append(str(path))
            return "pypdf fallback text"

        def fake_marker(path: Path) -> str | None:
            marker_calls.append(str(path))
            if isinstance(marker_return, Exception):
                raise marker_return
            return marker_return  # type: ignore[return-value]

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        with (
            patch.object(ext_module, "_extract_pdf_via_marker", side_effect=fake_marker),
            patch.object(ext_module, "_extract_pdf", side_effect=fake_pypdf),
        ):
            result = ext_module.extract_text(pdf_file)

        assert (
            "pypdf fallback text" in result
        ), f"pypdf fallback text expected in result: {result!r}"
        return pypdf_calls, marker_calls

    def test_fallback_on_none_return(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-R81-004: Marker returns None → pypdf fallback."""
        pypdf_calls, marker_calls = self._setup(tmp_path, monkeypatch, None)
        assert marker_calls, "Marker should have been attempted"
        assert pypdf_calls, "pypdf should have been called as fallback"

    def test_fallback_on_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Marker raises Exception → pypdf fallback (covers ConnectError, ReadTimeout, etc.)."""
        # We test the _extract_pdf_via_marker function itself returning None on exceptions,
        # which is the tested contract (the helper catches and returns None).
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "pdf_extractor", "marker")
        monkeypatch.setattr(cfg.settings, "marker_service_url", "http://localhost:8555")
        monkeypatch.setattr(cfg.settings, "marker_timeout_seconds", 5.0)

        import httpx

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        with patch("httpx.Client") as mock_client_cls:
            # Simulate connection refused
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client_cls.return_value = mock_client

            result = ext_module._extract_pdf_via_marker(pdf_file)

        assert result is None, "_extract_pdf_via_marker must return None on ConnectError"

    def test_fallback_on_timeout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-R81-005: Marker times out → _extract_pdf_via_marker returns None."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "marker_service_url", "http://localhost:8555")
        monkeypatch.setattr(cfg.settings, "marker_timeout_seconds", 5.0)

        import httpx

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.side_effect = httpx.ReadTimeout("Timed out")
            mock_client_cls.return_value = mock_client

            result = ext_module._extract_pdf_via_marker(pdf_file)

        assert result is None, "_extract_pdf_via_marker must return None on ReadTimeout"

    def test_fallback_on_non_200(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-R81-006: Marker returns non-200 → _extract_pdf_via_marker returns None."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "marker_service_url", "http://localhost:8555")

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.json.return_value = {"error": "Service unavailable"}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = ext_module._extract_pdf_via_marker(pdf_file)

        assert result is None, "_extract_pdf_via_marker must return None on non-200"

    def test_fallback_on_missing_markdown_field(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-R81-007: Response missing 'markdown' field → returns None."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "marker_service_url", "http://localhost:8555")

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"pages": 3}  # missing 'markdown'

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = ext_module._extract_pdf_via_marker(pdf_file)

        assert result is None, "Missing 'markdown' field should cause None return"

    def test_fallback_on_empty_markdown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty 'markdown' string → returns None (treat as no-content failure)."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "marker_service_url", "http://localhost:8555")

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"markdown": "", "pages": 1}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post.return_value = mock_response
            mock_client_cls.return_value = mock_client

            result = ext_module._extract_pdf_via_marker(pdf_file)

        assert result is None, "Empty 'markdown' should cause None return"


# ── T-R81-008: EXTRACT_MAX_CHARS cap on Marker result ────────────────────────


class TestExtractMaxCharsWithMarker:
    """T-R81-008: EXTRACT_MAX_CHARS cap applied to Marker-sourced output (I7)."""

    def test_marker_result_truncated_by_max_chars(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Marker returns a very long string; it is truncated to EXTRACT_MAX_CHARS."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "pdf_extractor", "marker")
        monkeypatch.setattr(cfg.settings, "extract_max_chars", 50)

        long_text = "M" * 500  # 500 chars > cap of 50

        def fake_marker(path: Path) -> str | None:
            return long_text

        pdf_file = tmp_path / "big.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        with patch.object(ext_module, "_extract_pdf_via_marker", side_effect=fake_marker):
            result = ext_module.extract_text(pdf_file)

        assert len(result) <= 50, f"Expected ≤50 chars after cap; got {len(result)}"
        assert result == "M" * 50


# ── T-R81-009: Request shape sent to /convert ────────────────────────────────


class TestMarkerRequestShape:
    """T-R81-009: _extract_pdf_via_marker sends correct multipart 'file' field."""

    def test_correct_request_shape(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verifies multipart 'file' field with PDF bytes is sent to {url}/convert."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "marker_service_url", "http://localhost:8555")
        monkeypatch.setattr(cfg.settings, "marker_timeout_seconds", 30.0)

        pdf_bytes = _make_minimal_pdf()
        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(pdf_bytes)

        captured_kwargs: list[dict] = []

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"markdown": "# Result", "pages": 1}

        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)

            def capture_post(url: str, **kwargs: object) -> MagicMock:
                captured_kwargs.append({"url": url, **kwargs})
                return mock_response

            mock_client.post.side_effect = capture_post
            mock_client_cls.return_value = mock_client

            result = ext_module._extract_pdf_via_marker(pdf_file)

        assert result == "# Result"
        assert len(captured_kwargs) == 1

        call = captured_kwargs[0]
        # URL must end with /convert
        assert call["url"].endswith("/convert"), f"Expected /convert endpoint; got {call['url']}"
        # Must use multipart files= kwarg
        assert "files" in call, "Must send multipart 'files=' kwarg"
        files = call["files"]
        assert (
            "file" in files
        ), f"'file' field required in multipart; got keys: {list(files.keys())}"
        # The 'file' tuple must contain the PDF bytes
        file_tuple = files["file"]
        assert pdf_bytes in file_tuple, "PDF bytes must be present in the 'file' field"

    def test_timeout_passed_to_client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The configured MARKER_TIMEOUT_SECONDS is passed to the httpx.Client."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        configured_timeout = 77.5
        monkeypatch.setattr(cfg.settings, "marker_service_url", "http://localhost:8555")
        monkeypatch.setattr(cfg.settings, "marker_timeout_seconds", configured_timeout)

        pdf_file = tmp_path / "doc.pdf"
        pdf_file.write_bytes(_make_minimal_pdf())

        captured_timeouts: list[float] = []

        with patch("httpx.Client") as mock_client_cls:

            def capture_client(timeout: float, **kwargs: object) -> MagicMock:
                captured_timeouts.append(timeout)
                mock_client = MagicMock()
                mock_client.__enter__ = MagicMock(return_value=mock_client)
                mock_client.__exit__ = MagicMock(return_value=False)
                mock_client.post.side_effect = Exception("abort after capturing timeout")
                return mock_client

            mock_client_cls.side_effect = capture_client

            # Will fail after capturing timeout — that's fine
            ext_module._extract_pdf_via_marker(pdf_file)

        assert len(captured_timeouts) == 1
        assert captured_timeouts[0] == pytest.approx(configured_timeout)
