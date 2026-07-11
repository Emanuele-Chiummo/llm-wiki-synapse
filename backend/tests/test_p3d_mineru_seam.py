"""
P3-d — MinerU cloud PDF extractor seam (v1.5 LLM Wiki parity, ADR-0069).

Covers:
  - pdf_extractor gains 'mineru' as a valid config-override value (config_overrides)
  - mineru_api_url / mineru_timeout_seconds validation (runtime, non-secret)
  - config.py exposes mineru_api_url / mineru_api_key / mineru_timeout_seconds with sane defaults
  - PDF_EXTRACTOR=mineru routes extract_text() to _extract_pdf_via_mineru
  - no API key → nothing uploaded, returns None (unconditional pypdf fallback, I9)
  - a mocked 2xx cloud response returns the markdown
  - a non-2xx / exception falls back to pypdf (returns None)

The API key is a SECRET (env-only) — asserted absent from the config-override surface (§2.4).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Config-override surface ────────────────────────────────────────────────────


class TestConfigOverrideSurface:
    def test_mineru_is_a_valid_pdf_extractor(self) -> None:
        from app.config_overrides import validate_value

        assert validate_value("pdf_extractor", "mineru") is None
        assert validate_value("pdf_extractor", "pypdf") is None
        assert validate_value("pdf_extractor", "marker") is None
        assert validate_value("pdf_extractor", "bogus") is not None

    def test_mineru_api_url_validation(self) -> None:
        from app.config_overrides import validate_value

        assert validate_value("mineru_api_url", "https://mineru.net/api/v4") is None
        assert validate_value("mineru_api_url", "ftp://x") is not None

    def test_mineru_timeout_validation(self) -> None:
        from app.config_overrides import validate_value

        assert validate_value("mineru_timeout_seconds", "600") is None
        assert validate_value("mineru_timeout_seconds", "0") is not None
        assert validate_value("mineru_timeout_seconds", "abc") is not None

    def test_api_key_is_not_in_override_surface(self) -> None:
        """The MinerU API key is a secret — env-only, never PUT-able (config_overrides §2.4)."""
        from app.config_overrides import ALLOWED_CONFIG_KEYS

        assert "mineru_api_url" in ALLOWED_CONFIG_KEYS
        assert "mineru_timeout_seconds" in ALLOWED_CONFIG_KEYS
        assert "mineru_api_key" not in ALLOWED_CONFIG_KEYS

    def test_config_defaults(self) -> None:
        from app.config import settings

        assert settings.mineru_api_url.startswith("https://")
        assert settings.mineru_api_key == ""  # off by default — nothing uploaded
        assert settings.mineru_timeout_seconds > 0


# ── Extractor routing ──────────────────────────────────────────────────────────


class TestMineruRouting:
    def test_mineru_selected_routes_to_mineru_helper(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "pdf_extractor", "mineru")
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        with patch.object(
            ext_module, "_extract_pdf_via_mineru", return_value="# From MinerU\n\nBody"
        ) as m:
            out = ext_module.extract_text(pdf)

        m.assert_called_once()
        assert "From MinerU" in out

    def test_mineru_failure_falls_back_to_pypdf(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "pdf_extractor", "mineru")
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        # mineru returns None (e.g. no key) → _extract_pdf (pypdf) must be called
        with (
            patch.object(ext_module, "_extract_pdf_via_mineru", return_value=None),
            patch.object(ext_module, "_extract_pdf", return_value="pypdf text") as pdf_helper,
        ):
            out = ext_module.extract_text(pdf)

        pdf_helper.assert_called_once()
        assert out == "pypdf text"

    def test_pypdf_default_never_calls_mineru(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "pdf_extractor", "pypdf")
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        with (
            patch.object(ext_module, "_extract_pdf_via_mineru") as mineru_helper,
            patch.object(ext_module, "_extract_pdf", return_value="pypdf text"),
        ):
            ext_module.extract_text(pdf)

        mineru_helper.assert_not_called()


# ── Cloud adapter behaviour (I9 opt-in / fallback) ─────────────────────────────


class TestMineruAdapter:
    def test_no_api_key_returns_none_without_uploading(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty MINERU_API_KEY → None (fallback), and httpx is NEVER called (nothing uploaded)."""
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "mineru_api_key", "")
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        with patch("httpx.Client") as client_cls:
            result = ext_module._extract_pdf_via_mineru(pdf)

        assert result is None
        client_cls.assert_not_called()  # I9: no upload happens until a key is configured

    def test_success_returns_markdown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "mineru_api_key", "test-key")
        monkeypatch.setattr(cfg.settings, "mineru_api_url", "https://mineru.example/api/v4")
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"markdown": "# Cloud\n\nExtracted"}
        client = MagicMock()
        client.__enter__.return_value.post.return_value = resp

        with patch("httpx.Client", return_value=client):
            result = ext_module._extract_pdf_via_mineru(pdf)

        assert result == "# Cloud\n\nExtracted"

    def test_non_2xx_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ingest import extract as ext_module

        monkeypatch.setattr(cfg.settings, "mineru_api_key", "test-key")
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")

        resp = MagicMock()
        resp.status_code = 500
        client = MagicMock()
        client.__enter__.return_value.post.return_value = resp

        with patch("httpx.Client", return_value=client):
            result = ext_module._extract_pdf_via_mineru(pdf)

        assert result is None
