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
  - Max upload: 300 MB (configurable via --max-upload-mb).
  - Large PDFs are split into fixed page-range chunks (default 25 pages, --pages-per-chunk)
    and converted one chunk at a time with a SINGLE shared model set, then the per-chunk
    markdown is concatenated. This bounds peak VRAM to (models + one chunk) so a 190 MB /
    several-hundred-page ServiceNow export converts on a 12 GB GPU without OOM, and it keeps
    each Marker call small. Topical splitting into wiki pages still happens downstream in the
    Synapse ingest orchestrator — this only makes the giant PDF convertible in the first place.
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

# ── Defaults ──────────────────────────────────────────────────────────────────
# Chunking makes large PDFs convertible on a 12 GB GPU without OOM (see module docstring).
DEFAULT_MAX_UPLOAD_MB: int = 300
DEFAULT_PAGES_PER_CHUNK: int = 25
_CHUNK_SEPARATOR: str = "\n\n"  # joins per-chunk markdown; page order is preserved

# ── Shared state ──────────────────────────────────────────────────────────────

_conversion_lock: asyncio.Lock | None = None  # initialised in lifespan


def _get_lock() -> asyncio.Lock:
    """Return the module-level conversion lock (created lazily if needed)."""
    global _conversion_lock  # noqa: PLW0603
    if _conversion_lock is None:
        _conversion_lock = asyncio.Lock()
    return _conversion_lock


# ── Marker engine ─────────────────────────────────────────────────────────────


def _build_converter() -> object:
    """
    Build a Marker PdfConverter with the shared model set loaded ONCE.

    Kept separate from conversion so a chunked job loads the (heavy) Surya models a single
    time and reuses the converter for every page-range chunk — the models are the fixed
    per-job cost; only per-chunk activations are freed between chunks.

    Raises RuntimeError if marker-pdf is not installed in this venv.
    """
    try:
        from marker.converters.pdf import PdfConverter  # noqa: PLC0415
        from marker.models import create_model_dict  # noqa: PLC0415
        from marker.config.parser import ConfigParser  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError(
            f"marker-pdf not installed in this venv: {exc}. Run: pip install marker-pdf"
        ) from exc

    cfg: dict[str, object] = {"output_format": "markdown"}
    parser = ConfigParser(cfg)
    models = create_model_dict()
    return PdfConverter(
        config=parser.generate_config_dict(),
        artifact_dict=models,
        processor_list=parser.get_processors(),
        renderer=parser.get_renderer(),
    )


def _convert_one(converter: object, pdf_path: Path) -> str:
    """Run the shared converter on one PDF path and return its markdown (never None)."""
    rendered = converter(str(pdf_path))  # type: ignore[operator]
    return getattr(rendered, "markdown", "") or ""


def _count_pages(pdf_path: Path) -> int:
    """Cheap page count via pypdfium2; returns 0 if it cannot be determined."""
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415

        doc = pdfium.PdfDocument(str(pdf_path))
        n = len(doc)
        doc.close()
        return n
    except Exception:  # noqa: BLE001
        return 0


def _split_pdf_pages(pdf_path: Path, pages_per_chunk: int, dest_dir: Path) -> list[Path]:
    """
    Split *pdf_path* into sub-PDFs of at most *pages_per_chunk* pages each, in page order.

    Uses pypdfium2 (already a dependency for page counting) — no extra install. Returns the
    list of chunk file paths written under *dest_dir*. Raises RuntimeError on failure so the
    caller can fall back to whole-file conversion.
    """
    try:
        import pypdfium2 as pdfium  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - pypdfium2 ships with marker
        raise RuntimeError(f"pypdfium2 unavailable for splitting: {exc}") from exc

    src = pdfium.PdfDocument(str(pdf_path))
    try:
        n_pages = len(src)
        chunk_paths: list[Path] = []
        for chunk_idx, start in enumerate(range(0, n_pages, pages_per_chunk)):
            end = min(start + pages_per_chunk, n_pages)
            dst = pdfium.PdfDocument.new()
            try:
                dst.import_pages(src, list(range(start, end)))
                chunk_path = dest_dir / f"chunk_{chunk_idx:04d}.pdf"
                with open(chunk_path, "wb") as fh:
                    dst.save(fh)
                chunk_paths.append(chunk_path)
            finally:
                dst.close()
        return chunk_paths
    finally:
        src.close()


def _convert_pdf_bytes(
    pdf_bytes: bytes,
    *,
    filename: str = "upload.pdf",
    pages_per_chunk: int = DEFAULT_PAGES_PER_CHUNK,
) -> dict[str, object]:
    """
    Convert PDF bytes to Markdown using Marker, chunking large PDFs by page range.

    Small PDFs (<= pages_per_chunk pages, or unknown page count) convert whole, exactly as
    before. Larger PDFs are split into fixed page-range chunks and converted one at a time
    with a single shared model set; the per-chunk markdown is concatenated in page order.
    If splitting fails for any reason, falls back to a single whole-file conversion.

    Returns {"markdown": str, "pages": int, "chunks": int}.
    Raises RuntimeError if Marker is not installed or conversion fails.
    """
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = Path(tmp.name)

    chunk_paths: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="marker_chunks_") as chunk_dir_str:
        chunk_dir = Path(chunk_dir_str)
        try:
            n_pages = _count_pages(tmp_path)
            converter = _build_converter()  # models loaded ONCE for the whole job

            # ── Small / uncountable → whole-file (unchanged behaviour) ──────────
            if n_pages == 0 or pages_per_chunk <= 0 or n_pages <= pages_per_chunk:
                markdown = _convert_one(converter, tmp_path)
                return {"markdown": markdown, "pages": n_pages, "chunks": 1}

            # ── Large → split by page range, convert serially, concatenate ──────
            try:
                chunk_paths = _split_pdf_pages(tmp_path, pages_per_chunk, chunk_dir)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "convert: page-split failed (%s) — falling back to whole-file conversion",
                    exc,
                )
                markdown = _convert_one(converter, tmp_path)
                return {"markdown": markdown, "pages": n_pages, "chunks": 1}

            logger.info(
                "convert: %s is %d pages → %d chunk(s) of %d page(s) each",
                filename,
                n_pages,
                len(chunk_paths),
                pages_per_chunk,
            )
            parts: list[str] = []
            for i, chunk_path in enumerate(chunk_paths):
                logger.info("convert: chunk %d/%d — %s", i + 1, len(chunk_paths), chunk_path.name)
                parts.append(_convert_one(converter, chunk_path))
            markdown = _CHUNK_SEPARATOR.join(p for p in parts if p)
            return {"markdown": markdown, "pages": n_pages, "chunks": len(chunk_paths)}
        finally:
            try:
                tmp_path.unlink()
            except Exception:  # noqa: BLE001
                pass


# ── FastAPI app ───────────────────────────────────────────────────────────────




def _build_app(
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_MB * 1024 * 1024,
    pages_per_chunk: int = DEFAULT_PAGES_PER_CHUNK,
    service_token: str | None = None,
) -> object:
    """
    Build and return the FastAPI application.

    Imported lazily so the module can be imported for testing without requiring
    fastapi to be installed in the outer test runner (though it must be in the
    marker venv).

    ``pages_per_chunk`` bounds the page-range of each Marker call for large PDFs
    (<= 0 disables chunking → always whole-file).
    """
    from contextlib import asynccontextmanager  # noqa: PLC0415

    from fastapi import FastAPI, HTTPException, Request, UploadFile  # noqa: PLC0415
    from fastapi.responses import JSONResponse  # noqa: PLC0415

    def _check_auth_inner(request: Request, token: str | None) -> None:
        """Verify optional bearer token authorization (SEC-OPS-1)."""
        if not token:
            return  # No token configured, auth is optional
        auth_header = request.headers.get("authorization", "").strip()
        expected = f"Bearer {token}"
        if auth_header != expected:
            raise HTTPException(status_code=403, detail="Invalid or missing Authorization header")


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
        Returns: {"markdown": str, "pages": int, "chunks": int}
        Errors:
          413 if the upload exceeds the size limit.
          429 if a conversion is already in progress.
          500 if Marker conversion fails.
        """
        _check_auth_inner(request, service_token)
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
                    None,
                    lambda: _convert_pdf_bytes(
                        pdf_bytes, filename=filename, pages_per_chunk=pages_per_chunk
                    ),
                )
            except RuntimeError as exc:
                logger.error("convert: Marker conversion failed: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc)) from exc

            logger.info(
                "convert: done — %d chars, %d pages, %d chunk(s)",
                len(result["markdown"]),
                result["pages"],
                result.get("chunks", 1),
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
        default=DEFAULT_MAX_UPLOAD_MB,
        help=f"Max PDF upload size in MB (default: {DEFAULT_MAX_UPLOAD_MB})",
    )
    parser.add_argument(
        "--pages-per-chunk",
        type=int,
        default=DEFAULT_PAGES_PER_CHUNK,
        help=(
            "Split PDFs larger than this many pages into page-range chunks converted "
            f"one at a time (default: {DEFAULT_PAGES_PER_CHUNK}; <= 0 disables chunking)"
        ),
    )
    args = parser.parse_args()

    max_bytes = args.max_upload_mb * 1024 * 1024
    # Load optional token from env (SEC-OPS-1)
    service_token = os.environ.get("MARKER_SERVICE_TOKEN", "").strip() or None
    app = _build_app(max_upload_bytes=max_bytes, pages_per_chunk=args.pages_per_chunk, service_token=service_token)

    logger.info(
        "Starting Marker service on %s:%d (max upload: %d MB, pages/chunk: %d)",
        args.host,
        args.port,
        args.max_upload_mb,
        args.pages_per_chunk,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
