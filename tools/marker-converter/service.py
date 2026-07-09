#!/usr/bin/env python3
"""
Marker PDF extractor microservice (ADR-0051, R8-1).

Exposes:
  POST /convert  — PDF bytes (multipart field "file") → {"markdown": str, "pages": int}
  GET  /health   — {"status": "ok"}

Run inside the Marker venv (which has marker-pdf + torch + surya):
  ./.venv/bin/python service.py --port 8555

The Synapse backend calls this service when PDF_EXTRACTOR=marker. On any error
the backend falls back to pypdf — this service is opt-in and never a hard
dependency of the main container.

Bounds:
  - Max upload: 50 MB (configurable via --max-upload-mb).
  - One conversion at a time: asyncio.Lock prevents parallel GPU saturation.
  - Returns HTTP 413 if the upload exceeds the size limit.
  - Returns HTTP 429 if a conversion is already in progress.
"""

# NOTE: deliberately NO `from __future__ import annotations` here. FastAPI resolves the
# /convert route's parameter types (Request, UploadFile) from the endpoint's annotations. Those
# types are imported LAZILY inside _build_app(), so PEP 563 stringized annotations would be
# unresolvable by FastAPI's get_type_hints() → it would misclassify `file` as a QUERY param and
# reject every real multipart upload with 422. Evaluating annotations eagerly (no __future__)
# stores the real classes on the endpoint, which FastAPI recognizes correctly.

import argparse
import asyncio
import logging
import os
import tempfile
from pathlib import Path

logger = logging.getLogger("marker_service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Shared state ──────────────────────────────────────────────────────────────

_conversion_lock: asyncio.Lock | None = None  # initialised in lifespan


def _get_lock() -> asyncio.Lock:
    """Return the module-level conversion lock (created lazily if needed)."""
    global _conversion_lock  # noqa: PLW0603
    if _conversion_lock is None:
        _conversion_lock = asyncio.Lock()
    return _conversion_lock


# ── Marker engine ─────────────────────────────────────────────────────────────


def _convert_pdf_bytes(pdf_bytes: bytes, *, filename: str = "upload.pdf") -> dict[str, object]:
    """
    Convert PDF bytes to Markdown using Marker.

    Writes bytes to a temp file, runs Marker via the same API pattern as
    servicenow_connector.py (make_converter_factory), returns
    {"markdown": str, "pages": int}.

    Raises RuntimeError if Marker is not installed or conversion fails.
    """
    try:
        from marker.converters.pdf import PdfConverter  # noqa: PLC0415
        from marker.models import create_model_dict  # noqa: PLC0415
        from marker.config.parser import ConfigParser  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            f"marker-pdf not installed in this venv: {exc}. "
            "Run: pip install marker-pdf"
        ) from exc

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = Path(tmp.name)

    try:
        # Count pages before full conversion (cheap)
        try:
            import pypdfium2 as pdfium  # noqa: PLC0415

            pdf_doc = pdfium.PdfDocument(str(tmp_path))
            n_pages = len(pdf_doc)
            pdf_doc.close()
        except Exception:  # noqa: BLE001
            n_pages = 0

        cfg: dict[str, object] = {"output_format": "markdown"}
        parser = ConfigParser(cfg)
        models = create_model_dict()
        converter = PdfConverter(
            config=parser.generate_config_dict(),
            artifact_dict=models,
            processor_list=parser.get_processors(),
            renderer=parser.get_renderer(),
        )
        rendered = converter(str(tmp_path))
        markdown: str = getattr(rendered, "markdown", "") or ""
        return {"markdown": markdown, "pages": n_pages}
    finally:
        try:
            tmp_path.unlink()
        except Exception:  # noqa: BLE001
            pass


# ── FastAPI app ───────────────────────────────────────────────────────────────


def _build_app(max_upload_bytes: int = 50 * 1024 * 1024) -> object:
    """
    Build and return the FastAPI application.

    Imported lazily so the module can be imported for testing without requiring
    fastapi to be installed in the outer test runner (though it must be in the
    marker venv).
    """
    from contextlib import asynccontextmanager  # noqa: PLC0415

    from fastapi import FastAPI, HTTPException, Request, UploadFile  # noqa: PLC0415
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[type-arg]  # noqa: ANN001
        global _conversion_lock  # noqa: PLW0603
        _conversion_lock = asyncio.Lock()
        yield

    app = FastAPI(
        title="Marker PDF extractor service",
        description=(
            "Lightweight wrapper around marker-pdf for the Synapse backend. "
            "POST /convert → {markdown, pages}. "
            "Used only when PDF_EXTRACTOR=marker on the backend side."
        ),
        version="0.8.0",
        lifespan=lifespan,
    )

    @app.get("/health", response_model=dict)
    async def health() -> dict:
        """Liveness probe — always returns 200 {"status": "ok"}."""
        return {"status": "ok"}

    @app.post("/convert", response_model=dict)
    async def convert(request: Request, file: UploadFile) -> JSONResponse:
        """
        Convert a PDF file to Markdown.

        Multipart field: "file" — the raw PDF bytes.
        Returns: {"markdown": str, "pages": int}
        Errors:
          413 if the upload exceeds the size limit.
          429 if a conversion is already in progress.
          500 if Marker conversion fails.
        """
        lock = _get_lock()

        # Size guard (I7)
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload exceeds {max_upload_bytes // (1024 * 1024)} MB limit.",
            )

        pdf_bytes = await file.read()
        if len(pdf_bytes) > max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload exceeds {max_upload_bytes // (1024 * 1024)} MB limit.",
            )

        # One-at-a-time gate (prevents GPU memory exhaustion)
        if lock.locked():
            raise HTTPException(
                status_code=429,
                detail="A conversion is already in progress. Retry after it completes.",
            )

        async with lock:
            filename = file.filename or "upload.pdf"
            logger.info("convert: starting Marker conversion for %s (%d bytes)", filename, len(pdf_bytes))
            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(
                    None, lambda: _convert_pdf_bytes(pdf_bytes, filename=filename)
                )
            except RuntimeError as exc:
                logger.error("convert: Marker conversion failed: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            logger.info(
                "convert: done — %d chars, %d pages", len(result["markdown"]), result["pages"]
            )
            return JSONResponse(content=result)

    return app


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """CLI entry point: parse args, build app, run with uvicorn."""
    import uvicorn  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Marker PDF extractor microservice")
    parser.add_argument("--port", type=int, default=8555, help="Port to listen on (default: 8555)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")  # noqa: S104
    parser.add_argument(
        "--max-upload-mb",
        type=int,
        default=50,
        help="Max PDF upload size in MB (default: 50)",
    )
    args = parser.parse_args()

    max_bytes = args.max_upload_mb * 1024 * 1024
    app = _build_app(max_upload_bytes=max_bytes)

    logger.info(
        "Starting Marker service on %s:%d (max upload: %d MB)",
        args.host,
        args.port,
        args.max_upload_mb,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
