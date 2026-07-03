"""
R8-3 / F12 — Audio/video transcription via Whisper microservice.

Tests:
  T-R83-001  AV_TRANSCRIPTION_ENABLED False (default) → placeholder unchanged, no HTTP call
  T-R83-002  Enabled + httpx mock success → transcript flows into source_text for ingest
  T-R83-003  Enabled + httpx connection failure → None (placeholder path, WARNING logged)
  T-R83-004  Enabled + httpx timeout → None (placeholder path)
  T-R83-005  Enabled + non-200 response → None (placeholder path)
  T-R83-006  Enabled + missing 'text' field in response → None (placeholder path)
  T-R83-007  Per-run cap (AV_MAX_FILES_PER_RUN) → stop calling service after cap reached
  T-R83-008  Transcript capped at EXTRACT_MAX_CHARS (I7)
  T-R83-009  config.py exposes all four R8-3 env vars with correct defaults
  T-R83-010  AV extension set is correct (mp3, wav, m4a, mp4)
  T-R83-011  tools/whisper-service/service.py health endpoint returns 200 {"status": "ok"}
             (mocked engine — no real Whisper needed)
  T-R83-012  tools/whisper-service/service.py 429 when lock is held
  T-R83-013  tools/whisper-service/service.py 413 on oversized upload
  T-R83-014  tools/whisper-service/service.py 503 when engine is None

All infra-free: mocked httpx calls, no live Postgres/Qdrant/Ollama/Whisper.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_fake_response(
    status_code: int = 200,
    json_data: dict[str, Any] | None = None,
) -> httpx.Response:
    """Build a minimal fake httpx.Response for test assertions."""
    import json

    body = json.dumps(json_data or {}).encode()
    return httpx.Response(
        status_code=status_code,
        content=body,
        headers={"content-type": "application/json"},
    )


# ── T-R83-009: Config settings ────────────────────────────────────────────────


class TestConfigSettings:
    """T-R83-009: config.py exposes all four R8-3 env vars with correct defaults."""

    def test_av_transcription_enabled_default_false(self) -> None:
        from app.config import settings

        assert settings.av_transcription_enabled is False

    def test_whisper_service_url_default(self) -> None:
        from app.config import settings

        assert "8666" in settings.whisper_service_url
        assert "host.docker.internal" in settings.whisper_service_url

    def test_whisper_timeout_seconds_default(self) -> None:
        from app.config import settings

        assert settings.whisper_timeout_seconds == pytest.approx(300.0)

    def test_av_max_files_per_run_default(self) -> None:
        from app.config import settings

        assert settings.av_max_files_per_run == 3


# ── T-R83-010: AV extension set ──────────────────────────────────────────────


class TestAvExtensionSet:
    """T-R83-010: AV_EXTENSIONS frozenset covers exactly the required extensions."""

    def test_av_extensions_in_transcription_module(self) -> None:
        from app.ingest.transcription import AV_EXTENSIONS

        assert ".mp3" in AV_EXTENSIONS
        assert ".wav" in AV_EXTENSIONS
        assert ".m4a" in AV_EXTENSIONS
        assert ".mp4" in AV_EXTENSIONS

    def test_image_extensions_not_in_av_set(self) -> None:
        from app.ingest.transcription import AV_EXTENSIONS

        for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            assert ext not in AV_EXTENSIONS

    def test_orchestrator_has_av_extension_set(self) -> None:
        from app.ingest.orchestrator import _AV_EXTENSIONS

        assert _AV_EXTENSIONS == frozenset({".mp3", ".wav", ".m4a", ".mp4"})


# ── T-R83-001: Disabled by default — no HTTP call ────────────────────────────


class TestDisabledByDefault:
    """T-R83-001: AV_TRANSCRIPTION_ENABLED=False → placeholder unchanged, no HTTP call."""

    @pytest.mark.asyncio
    async def test_disabled_returns_none_without_network_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app import config as cfg
        from app.ingest import transcription as tr_module

        monkeypatch.setattr(cfg.settings, "av_transcription_enabled", False)

        # Patch httpx.AsyncClient to assert it is never instantiated
        with patch("httpx.AsyncClient") as mock_client:
            result = await tr_module.maybe_transcribe_av(
                raw_bytes=b"fake audio bytes",
                origin_source="raw/sources/test.mp3",
            )

        assert result is None
        mock_client.assert_not_called()

    @pytest.mark.asyncio
    async def test_placeholder_text_unchanged_when_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When disabled, extract_text returns AV placeholder (pre-R8-3 path)."""
        from app import config as cfg
        from app.ingest.extract import extract_text

        monkeypatch.setattr(cfg.settings, "av_transcription_enabled", False)

        mp3_file = tmp_path / "audio.mp3"
        mp3_file.write_bytes(b"\xff\xfb\x90\x00" * 10)  # fake MP3 header bytes

        text = extract_text(mp3_file)
        assert "AV file" in text or "transcript" in text.lower() or "audio" in text.lower()


# ── T-R83-002: Success path — transcript flows into ingest ───────────────────


class TestSuccessPath:
    """T-R83-002: Enabled + mock httpx success → transcript returned."""

    @pytest.mark.asyncio
    async def test_success_returns_transcript(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ingest import transcription as tr_module

        monkeypatch.setattr(cfg.settings, "av_transcription_enabled", True)
        monkeypatch.setattr(cfg.settings, "whisper_service_url", "http://whisper-test:8666")
        monkeypatch.setattr(cfg.settings, "whisper_timeout_seconds", 30.0)
        monkeypatch.setattr(cfg.settings, "av_max_files_per_run", 3)

        fake_response = _make_fake_response(
            200,
            {"text": "Hello world transcription.", "language": "en", "duration_seconds": 5.2},
        )

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fake_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tr_module.maybe_transcribe_av(
                raw_bytes=b"audio bytes",
                origin_source="raw/sources/speech.wav",
            )

        assert result == "Hello world transcription."

    @pytest.mark.asyncio
    async def test_correct_request_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """POST is sent to {whisper_service_url}/transcribe with multipart 'file' field."""
        from app import config as cfg
        from app.ingest import transcription as tr_module

        monkeypatch.setattr(cfg.settings, "av_transcription_enabled", True)
        monkeypatch.setattr(cfg.settings, "whisper_service_url", "http://whisper-test:8666")
        monkeypatch.setattr(cfg.settings, "whisper_timeout_seconds", 30.0)
        monkeypatch.setattr(cfg.settings, "av_max_files_per_run", 3)

        fake_response = _make_fake_response(
            200,
            {"text": "Test transcript.", "language": "en", "duration_seconds": 3.1},
        )

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fake_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await tr_module.maybe_transcribe_av(
                raw_bytes=b"audio data",
                origin_source="raw/sources/talk.mp3",
            )

        # Verify POST was called to the correct URL
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://whisper-test:8666/transcribe"
        # Verify multipart 'file' field is present
        files = (
            call_args[1].get("files") or call_args[0][1]
            if len(call_args[0]) > 1
            else call_args[1].get("files")
        )
        assert files is not None
        assert "file" in files


# ── T-R83-003: Connection failure → placeholder ───────────────────────────────


class TestConnectionFailure:
    """T-R83-003: Enabled + httpx connection failure → None, WARNING logged."""

    @pytest.mark.asyncio
    async def test_connect_error_returns_none(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging

        from app import config as cfg
        from app.ingest import transcription as tr_module

        monkeypatch.setattr(cfg.settings, "av_transcription_enabled", True)
        monkeypatch.setattr(cfg.settings, "whisper_service_url", "http://whisper-test:8666")
        monkeypatch.setattr(cfg.settings, "whisper_timeout_seconds", 5.0)
        monkeypatch.setattr(cfg.settings, "av_max_files_per_run", 3)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client), caplog.at_level(logging.WARNING):
            result = await tr_module.maybe_transcribe_av(
                raw_bytes=b"audio bytes",
                origin_source="raw/sources/audio.mp4",
            )

        assert result is None
        assert any("placeholder" in r.message.lower() for r in caplog.records)


# ── T-R83-004: Timeout → placeholder ─────────────────────────────────────────


class TestTimeoutFailure:
    """T-R83-004: Enabled + httpx timeout → None."""

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ingest import transcription as tr_module

        monkeypatch.setattr(cfg.settings, "av_transcription_enabled", True)
        monkeypatch.setattr(cfg.settings, "whisper_service_url", "http://whisper-test:8666")
        monkeypatch.setattr(cfg.settings, "whisper_timeout_seconds", 1.0)
        monkeypatch.setattr(cfg.settings, "av_max_files_per_run", 3)

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tr_module.maybe_transcribe_av(
                raw_bytes=b"audio bytes",
                origin_source="raw/sources/longvideo.mp4",
            )

        assert result is None


# ── T-R83-005: Non-200 response → placeholder ────────────────────────────────


class TestNon200Response:
    """T-R83-005: Enabled + non-200 response → None."""

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ingest import transcription as tr_module

        monkeypatch.setattr(cfg.settings, "av_transcription_enabled", True)
        monkeypatch.setattr(cfg.settings, "whisper_service_url", "http://whisper-test:8666")
        monkeypatch.setattr(cfg.settings, "whisper_timeout_seconds", 30.0)
        monkeypatch.setattr(cfg.settings, "av_max_files_per_run", 3)

        for status_code in (429, 500, 503):
            fake_response = _make_fake_response(status_code, {"detail": "error"})

            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=fake_response)

            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await tr_module.maybe_transcribe_av(
                    raw_bytes=b"audio bytes",
                    origin_source=f"raw/sources/audio_{status_code}.mp3",
                )

            assert result is None, f"Expected None for HTTP {status_code}"


# ── T-R83-006: Missing 'text' field → placeholder ────────────────────────────


class TestMissingTextField:
    """T-R83-006: Enabled + missing/empty 'text' field in response → None."""

    @pytest.mark.asyncio
    async def test_missing_text_field_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ingest import transcription as tr_module

        monkeypatch.setattr(cfg.settings, "av_transcription_enabled", True)
        monkeypatch.setattr(cfg.settings, "whisper_service_url", "http://whisper-test:8666")
        monkeypatch.setattr(cfg.settings, "whisper_timeout_seconds", 30.0)
        monkeypatch.setattr(cfg.settings, "av_max_files_per_run", 3)

        for bad_body in [
            {"language": "en", "duration_seconds": 1.0},  # missing 'text'
            {"text": "", "language": "en", "duration_seconds": 1.0},  # empty 'text'
            {"text": "   ", "language": "en", "duration_seconds": 1.0},  # whitespace only
        ]:
            fake_response = _make_fake_response(200, bad_body)

            mock_client = MagicMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=fake_response)

            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await tr_module.maybe_transcribe_av(
                    raw_bytes=b"audio bytes",
                    origin_source="raw/sources/audio.wav",
                )

            assert result is None, f"Expected None for bad body: {bad_body}"


# ── T-R83-007: Per-run cap ───────────────────────────────────────────────────


class TestPerRunCap:
    """T-R83-007: AV_MAX_FILES_PER_RUN cap → no service call after cap reached."""

    @pytest.mark.asyncio
    async def test_cap_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ingest.transcription import AvRunBudget, maybe_transcribe_av

        monkeypatch.setattr(cfg.settings, "av_transcription_enabled", True)
        monkeypatch.setattr(cfg.settings, "whisper_service_url", "http://whisper-test:8666")
        monkeypatch.setattr(cfg.settings, "whisper_timeout_seconds", 30.0)
        monkeypatch.setattr(cfg.settings, "av_max_files_per_run", 2)

        fake_response = _make_fake_response(
            200,
            {"text": "Transcript.", "language": "en", "duration_seconds": 2.0},
        )

        call_count = 0

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def fake_post(*_args: object, **_kwargs: object) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return fake_response

        mock_client.post = fake_post

        budget = AvRunBudget(max_files=2)

        with patch("httpx.AsyncClient", return_value=mock_client):
            r1 = await maybe_transcribe_av(raw_bytes=b"a1", origin_source="a1.mp3", budget=budget)
            r2 = await maybe_transcribe_av(raw_bytes=b"a2", origin_source="a2.mp3", budget=budget)
            r3 = await maybe_transcribe_av(raw_bytes=b"a3", origin_source="a3.mp3", budget=budget)

        assert r1 == "Transcript."
        assert r2 == "Transcript."
        assert r3 is None  # cap reached — no service call
        assert call_count == 2  # exactly 2 calls, not 3

    @pytest.mark.asyncio
    async def test_cap_zero_never_calls_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ingest.transcription import AvRunBudget, maybe_transcribe_av

        monkeypatch.setattr(cfg.settings, "av_transcription_enabled", True)
        monkeypatch.setattr(cfg.settings, "whisper_service_url", "http://whisper-test:8666")

        budget = AvRunBudget(max_files=0)

        with patch("httpx.AsyncClient") as mock_cls:
            result = await maybe_transcribe_av(
                raw_bytes=b"audio", origin_source="a.mp3", budget=budget
            )

        assert result is None
        mock_cls.assert_not_called()


# ── T-R83-008: Transcript cap ────────────────────────────────────────────────


class TestTranscriptCap:
    """T-R83-008: Transcript capped at EXTRACT_MAX_CHARS (I7)."""

    @pytest.mark.asyncio
    async def test_long_transcript_is_truncated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app import config as cfg
        from app.ingest import transcription as tr_module

        monkeypatch.setattr(cfg.settings, "av_transcription_enabled", True)
        monkeypatch.setattr(cfg.settings, "whisper_service_url", "http://whisper-test:8666")
        monkeypatch.setattr(cfg.settings, "whisper_timeout_seconds", 30.0)
        monkeypatch.setattr(cfg.settings, "av_max_files_per_run", 3)
        monkeypatch.setattr(cfg.settings, "extract_max_chars", 100)

        long_text = "A" * 200  # 200 chars, cap is 100
        fake_response = _make_fake_response(
            200, {"text": long_text, "language": "en", "duration_seconds": 60.0}
        )

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=fake_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await tr_module.maybe_transcribe_av(
                raw_bytes=b"long audio",
                origin_source="raw/sources/long.mp3",
            )

        assert result is not None
        assert len(result) == 100
        assert result == "A" * 100


# ── T-R83-011..014: tools/whisper-service/service.py handler logic ───────────


class TestWhisperServiceHandlers:
    """
    T-R83-011..014: Validate whisper-service/service.py handler logic with the engine mocked.
    Imports service.py from tools/whisper-service/ relative to the repo root.
    """

    @pytest.fixture(autouse=True)
    def _patch_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch _ENGINE to 'mlx' and _transcribe_bytes to a simple stub."""
        import sys

        # Ensure the tools/whisper-service module is importable
        tools_dir = str(Path(__file__).parent.parent.parent / "tools" / "whisper-service")
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)

        # Import (or reimport to reset state)
        if "service" in sys.modules:
            del sys.modules["service"]

        import service  # noqa: PLC0415 — tools/whisper-service/service.py

        # Patch the engine and the transcribe function so no GPU is needed
        monkeypatch.setattr(service, "_ENGINE", "mlx")
        monkeypatch.setattr(service, "_ENGINE_ERROR", None)

        def _fake_transcribe(
            av_bytes: bytes, *, filename: str = "upload", model_name: str = "whisper-large-v3"
        ) -> dict[str, object]:
            return {
                "text": f"Transcribed {len(av_bytes)} bytes.",
                "language": "en",
                "duration_seconds": 5.0,
            }

        monkeypatch.setattr(service, "_transcribe_bytes", _fake_transcribe)
        self._service_module = service

    def _get_test_client(self) -> Any:
        from fastapi.testclient import TestClient  # noqa: PLC0415

        app = self._service_module._build_app(max_upload_bytes=5 * 1024 * 1024)
        return TestClient(app)

    def test_health_returns_200_ok(self) -> None:
        """T-R83-011: GET /health returns 200 {"status": "ok"}."""
        client = self._get_test_client()
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_transcribe_success(self) -> None:
        """T-R83-011: POST /transcribe with valid audio → 200 with text/language/duration_seconds."""
        client = self._get_test_client()
        resp = client.post(
            "/transcribe",
            files={"file": ("test.mp3", b"\xff\xfb\x90\x00" * 10, "audio/mpeg")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "text" in body
        assert "language" in body
        assert "duration_seconds" in body
        assert isinstance(body["text"], str) and len(body["text"]) > 0

    def test_413_on_oversized_upload(self) -> None:
        """T-R83-013: Upload exceeding max_upload_bytes → 413."""
        # Build app with 10-byte cap
        app = self._service_module._build_app(max_upload_bytes=10)
        from fastapi.testclient import TestClient  # noqa: PLC0415

        client = TestClient(app)
        resp = client.post(
            "/transcribe",
            files={"file": ("big.mp3", b"\xff" * 50, "audio/mpeg")},
        )
        assert resp.status_code == 413

    def test_429_when_lock_is_held(self) -> None:
        """T-R83-012: 429 when transcription is already in progress."""
        service_mod = self._service_module

        # Force a fresh lock into the module
        service_mod._transcription_lock = asyncio.Lock()

        # Simulate a held lock by acquiring it in a thread (asyncio.Lock is not thread-safe
        # for acquire, but we can use the underlying _locked flag via a fresh coroutine in
        # the same event loop — for testing purposes we patch _get_lock to return a pre-locked mock).
        from unittest.mock import MagicMock  # noqa: PLC0415

        mock_lock = MagicMock()
        mock_lock.locked.return_value = True
        mock_lock.__aenter__ = AsyncMock(return_value=mock_lock)
        mock_lock.__aexit__ = AsyncMock(return_value=False)

        original_get_lock = service_mod._get_lock
        service_mod._get_lock = lambda: mock_lock

        try:
            from fastapi.testclient import TestClient  # noqa: PLC0415

            app = service_mod._build_app(max_upload_bytes=5 * 1024 * 1024)
            client = TestClient(app)
            resp = client.post(
                "/transcribe",
                files={"file": ("test.mp3", b"\xff\xfb\x90\x00", "audio/mpeg")},
            )
            assert resp.status_code == 429
        finally:
            service_mod._get_lock = original_get_lock

    def test_503_when_engine_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-R83-014: 503 when no Whisper engine is available."""
        service_mod = self._service_module
        monkeypatch.setattr(service_mod, "_ENGINE", None)
        monkeypatch.setattr(service_mod, "_ENGINE_ERROR", "No engine installed.")

        from fastapi.testclient import TestClient  # noqa: PLC0415

        app = service_mod._build_app()
        client = TestClient(app)
        resp = client.post(
            "/transcribe",
            files={"file": ("test.mp3", b"\xff\xfb", "audio/mpeg")},
        )
        assert resp.status_code == 503
