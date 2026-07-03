#!/usr/bin/env python3
"""
Whisper AV transcription microservice (R8-3, F12).

Exposes:
  POST /transcribe — AV bytes (multipart field "file") → {"text": str, "language": str, "duration_seconds": float}
  GET  /health     — {"status": "ok"}

Run inside the Whisper venv (which has mlx-whisper or faster-whisper installed):
  ./.venv/bin/python service.py --port 8666

The Synapse backend calls this service when AV_TRANSCRIPTION_ENABLED=true. On any error
the backend falls back to the extract.py placeholder — this service is opt-in and never a
hard dependency of the main container.

Engine selection (try in order):
  1. mlx-whisper (Apple Silicon MPS path — fastest on macOS).
  2. faster-whisper (CPU/CUDA cross-platform path).
  3. openai-whisper (original; CPU-only fallback).
If none is importable, the service starts but all /transcribe requests return HTTP 503
with a clear error message (fail-fast for misconfigured venvs).

Bounds (I7):
  - Max upload: 200 MB (configurable via --max-upload-mb).
  - One transcription at a time: asyncio.Lock prevents parallel GPU saturation.
  - Returns HTTP 413 if the upload exceeds the size limit.
  - Returns HTTP 429 if a transcription is already in progress.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger("whisper_service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── FastAPI imports (module-level so Pydantic can resolve type annotations) ───
try:
    from fastapi import FastAPI, File, HTTPException, UploadFile
    from fastapi.responses import JSONResponse

    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

# ── Engine detection (at module import time) ──────────────────────────────────
# Try mlx-whisper → faster-whisper → openai-whisper, in that order.
# The chosen engine name is stored in a module-level var so the service can
# report a clear error at startup when none is found.

_ENGINE: str | None = None  # "mlx" | "faster" | "openai" | None
_ENGINE_ERROR: str | None = None


def _detect_engine() -> None:
    """Probe available Whisper engines and record the first importable one."""
    global _ENGINE, _ENGINE_ERROR  # noqa: PLW0603

    try:
        import mlx_whisper  # noqa: F401, PLC0415

        _ENGINE = "mlx"
        logger.info("whisper-service: engine=mlx-whisper (Apple Silicon MPS path)")
        return
    except ImportError:
        pass

    try:
        from faster_whisper import WhisperModel  # noqa: F401, PLC0415

        _ENGINE = "faster"
        logger.info("whisper-service: engine=faster-whisper (CPU/CUDA path)")
        return
    except ImportError:
        pass

    try:
        import whisper  # noqa: F401, PLC0415

        _ENGINE = "openai"
        logger.info("whisper-service: engine=openai-whisper (CPU-only fallback)")
        return
    except ImportError:
        pass

    _ENGINE = None
    _ENGINE_ERROR = (
        "No Whisper engine found. Install one of: "
        "mlx-whisper (macOS/MPS), faster-whisper (CPU/CUDA), or openai-whisper. "
        "See README.md for setup instructions."
    )
    logger.error("whisper-service: %s", _ENGINE_ERROR)


_detect_engine()

# ── Shared state ──────────────────────────────────────────────────────────────

_transcription_lock: asyncio.Lock | None = None  # initialised in lifespan
_max_upload_bytes: int = 200 * 1024 * 1024  # overridden by _build_app
_model_name: str = "whisper-large-v3"  # overridden by _build_app


def _get_lock() -> asyncio.Lock:
    """Return the module-level transcription lock (created lazily if needed)."""
    global _transcription_lock  # noqa: PLW0603
    if _transcription_lock is None:
        _transcription_lock = asyncio.Lock()
    return _transcription_lock


# ── Transcription engines ─────────────────────────────────────────────────────


def _transcribe_with_mlx(audio_path: str, model_name: str) -> dict[str, Any]:
    """Transcribe using mlx-whisper (Apple Silicon MPS)."""
    import mlx_whisper  # noqa: PLC0415

    result = mlx_whisper.transcribe(audio_path, path_or_hf_repo=f"mlx-community/{model_name}")
    text: str = result.get("text", "").strip()
    language: str = result.get("language", "")
    # mlx-whisper does not expose duration directly — derive from segments if present.
    segments = result.get("segments", [])
    duration_seconds: float = 0.0
    if segments:
        last = segments[-1]
        duration_seconds = float(last.get("end", 0.0))
    return {"text": text, "language": language, "duration_seconds": duration_seconds}


def _transcribe_with_faster(audio_path: str, model_name: str) -> dict[str, Any]:
    """Transcribe using faster-whisper (CPU/CUDA)."""
    from faster_whisper import WhisperModel  # noqa: PLC0415

    model = WhisperModel(model_name, device="auto", compute_type="int8")
    segments, info = model.transcribe(audio_path)
    text = " ".join(seg.text.strip() for seg in segments)
    language: str = info.language if info else ""
    duration_seconds: float = float(info.duration) if info and hasattr(info, "duration") else 0.0
    return {"text": text.strip(), "language": language, "duration_seconds": duration_seconds}


def _transcribe_with_openai(audio_path: str, model_name: str) -> dict[str, Any]:
    """Transcribe using openai-whisper (CPU-only original)."""
    import whisper  # noqa: PLC0415

    model = whisper.load_model(model_name)
    result = model.transcribe(audio_path)
    text: str = result.get("text", "").strip()
    language: str = result.get("language", "")
    # openai-whisper segments carry start/end times.
    segments = result.get("segments", [])
    duration_seconds: float = 0.0
    if segments:
        duration_seconds = float(segments[-1].get("end", 0.0))
    return {"text": text, "language": language, "duration_seconds": duration_seconds}


def _transcribe_bytes(
    av_bytes: bytes, *, filename: str = "upload", model_name: str = "whisper-large-v3"
) -> dict[str, Any]:
    """
    Transcribe AV bytes using the best available Whisper engine.

    Writes bytes to a temp file (Whisper requires a filesystem path), runs the selected
    engine, cleans up the temp file, and returns:
      {"text": str, "language": str, "duration_seconds": float}

    Raises RuntimeError if no engine is available or if transcription fails.
    """
    if _ENGINE is None:
        raise RuntimeError(_ENGINE_ERROR or "No Whisper engine available.")

    suffix = Path(filename).suffix.lower() or ".audio"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(av_bytes)
        tmp_path = tmp.name

    try:
        if _ENGINE == "mlx":
            return _transcribe_with_mlx(tmp_path, model_name)
        elif _ENGINE == "faster":
            return _transcribe_with_faster(tmp_path, model_name)
        else:  # "openai"
            return _transcribe_with_openai(tmp_path, model_name)
    finally:
        try:
            Path(tmp_path).unlink()
        except Exception:  # noqa: BLE001
            pass


# ── FastAPI app ───────────────────────────────────────────────────────────────
# Routes are defined at module level (not inside _build_app) so that Pydantic
# can resolve the UploadFile annotation. _build_app() creates the FastAPI instance,
# registers these module-level handlers onto it, and returns it.


if _FASTAPI_AVAILABLE:

    @asynccontextmanager
    async def _lifespan(app: "FastAPI"):  # type: ignore[type-arg]  # noqa: ANN001
        global _transcription_lock  # noqa: PLW0603
        _transcription_lock = asyncio.Lock()
        if _ENGINE is None:
            logger.error(
                "whisper-service: no Whisper engine available — /transcribe will return 503. %s",
                _ENGINE_ERROR,
            )
        else:
            logger.info(
                "whisper-service: ready (engine=%s, model=%s)", _ENGINE, _model_name
            )
        yield

    async def _health_handler() -> dict:  # type: ignore[type-arg]
        """Liveness probe — always returns 200 {"status": "ok"}."""
        return {"status": "ok"}

    async def _transcribe_handler(file: UploadFile = File(...)) -> "JSONResponse":
        """
        Transcribe an audio/video file.

        Multipart field: "file" — the raw AV bytes.
        Returns: {"text": str, "language": str, "duration_seconds": float}
        Errors:
          413 if the upload exceeds the size limit (200 MB default).
          429 if a transcription is already in progress.
          503 if no Whisper engine is available.
          500 if Whisper transcription fails.
        """
        if _ENGINE is None:
            raise HTTPException(
                status_code=503,
                detail=_ENGINE_ERROR or "No Whisper engine available. See README.md for setup.",
            )

        lock = _get_lock()

        av_bytes = await file.read()
        # Size guard (I7) — checked after reading so it works regardless of content-length header
        if len(av_bytes) > _max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload exceeds {_max_upload_bytes // (1024 * 1024)} MB limit.",
            )

        # One-at-a-time gate (prevents GPU memory exhaustion)
        if lock.locked():
            raise HTTPException(
                status_code=429,
                detail="A transcription is already in progress. Retry after it completes.",
            )

        async with lock:
            filename = file.filename or "upload.audio"
            logger.info(
                "transcribe: starting Whisper transcription for %s (%d bytes, engine=%s)",
                filename,
                len(av_bytes),
                _ENGINE,
            )
            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(
                    None,
                    lambda: _transcribe_bytes(
                        av_bytes, filename=filename, model_name=_model_name
                    ),
                )
            except RuntimeError as exc:
                logger.error("transcribe: Whisper transcription failed: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            logger.info(
                "transcribe: done — %d chars, language=%s, duration=%.1fs",
                len(result.get("text", "")),
                result.get("language", ""),
                result.get("duration_seconds", 0.0),
            )
            return JSONResponse(content=result)


def _build_app(
    max_upload_bytes: int = 200 * 1024 * 1024,
    model_name: str = "whisper-large-v3",
) -> "FastAPI":
    """
    Build and return the FastAPI application.

    ``max_upload_bytes`` and ``model_name`` are stored in module-level vars so the
    route handlers (defined at module level for Pydantic compatibility) can access them.
    """
    global _max_upload_bytes, _model_name  # noqa: PLW0603
    _max_upload_bytes = max_upload_bytes
    _model_name = model_name

    if not _FASTAPI_AVAILABLE:
        raise RuntimeError("fastapi is not installed. Run: pip install -r requirements.txt")

    app = FastAPI(
        title="Whisper AV transcription service",
        description=(
            "Lightweight Whisper wrapper for the Synapse backend. "
            "POST /transcribe → {text, language, duration_seconds}. "
            "Used only when AV_TRANSCRIPTION_ENABLED=true on the backend side."
        ),
        version="0.8.0",
        lifespan=_lifespan,
    )

    app.get("/health", response_model=dict)(_health_handler)
    app.post("/transcribe", response_model=dict)(_transcribe_handler)

    return app


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """CLI entry point: parse args, build app, run with uvicorn."""
    import uvicorn  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Whisper AV transcription microservice")
    parser.add_argument("--port", type=int, default=8666, help="Port to listen on (default: 8666)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")  # noqa: S104
    parser.add_argument(
        "--max-upload-mb",
        type=int,
        default=200,
        help="Max AV upload size in MB (default: 200)",
    )
    parser.add_argument(
        "--model",
        default="whisper-large-v3",
        help="Whisper model name (default: whisper-large-v3). "
        "For mlx-whisper this is the mlx-community repo suffix; "
        "for faster-whisper/openai-whisper this is the standard model size "
        "(tiny, base, small, medium, large-v3, etc.).",
    )
    args = parser.parse_args()

    max_bytes = args.max_upload_mb * 1024 * 1024
    app = _build_app(max_upload_bytes=max_bytes, model_name=args.model)

    logger.info(
        "Starting Whisper service on %s:%d (max upload: %d MB, engine: %s, model: %s)",
        args.host,
        args.port,
        args.max_upload_mb,
        _ENGINE or "NONE — will return 503",
        args.model,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
