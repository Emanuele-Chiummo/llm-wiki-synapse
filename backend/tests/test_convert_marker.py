"""
Tests for R11-1 Marker conversion endpoints (ADR-0051 / SPRINT-v1.1 §3 R11-1).

Acceptance checks:
  AC-R11-1-1 : POST /ingest/convert-marker → 400 for >10 files, 413 for oversize, 415 for non-pdf
  AC-R11-1-2 : On Marker success, writes {stem}.extracted.md with valid YAML frontmatter (I5)
  AC-R11-1-3 : On Marker error → 502 {"error":"marker_unavailable","detail":"..."};
               no .extracted.md written; NO pypdf fallback
  AC-R11-1-4 : GET /ingest/marker-health proxies Marker /health:
               200 {"status":"ok"} when Marker 200; 503 {"status":"offline",...} otherwise
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ── Test helpers ─────────────────────────────────────────────────────────────


def _make_client() -> AsyncClient:
    from contextlib import asynccontextmanager as acm

    from app.main import app
    from fastapi import FastAPI

    @acm
    async def _test_lifespan(app: FastAPI) -> Any:
        yield

    app.router.lifespan_context = _test_lifespan
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _make_pdf_bytes() -> bytes:
    """Return minimal valid PDF bytes for test uploads."""
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"


def _make_httpx_response(status_code: int, body: dict[str, Any]) -> MagicMock:
    """Build a mock httpx response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = body
    mock_resp.text = json.dumps(body)
    return mock_resp


# ─────────────────────────────────────────────────────────────────────────────
# AC-R11-1-1: rejection paths
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_convert_marker_rejects_more_than_10_files() -> None:
    """AC-R11-1-1: >10 files → HTTP 400."""
    pdf_bytes = _make_pdf_bytes()
    files = [
        ("files", (f"doc{i}.pdf", io.BytesIO(pdf_bytes), "application/pdf")) for i in range(11)
    ]

    async with _make_client() as client:
        resp = await client.post("/ingest/convert-marker", files=files)

    assert resp.status_code == 400
    assert "10" in resp.text


@pytest.mark.asyncio
async def test_convert_marker_rejects_non_pdf() -> None:
    """AC-R11-1-1: non-.pdf file → HTTP 415."""
    files = [("files", ("document.docx", io.BytesIO(b"fake content"), "application/octet-stream"))]

    async with _make_client() as client:
        resp = await client.post("/ingest/convert-marker", files=files)

    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_convert_marker_rejects_oversize_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-R11-1-1: file > MAX_UPLOAD_BYTES → HTTP 413."""
    from app import config as cfg_mod

    # Set a very small upload limit (1 byte) to trigger the 413
    monkeypatch.setattr(cfg_mod.settings, "max_upload_bytes", 1)

    files = [("files", ("big.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf"))]

    async with _make_client() as client:
        resp = await client.post("/ingest/convert-marker", files=files)

    assert resp.status_code == 413


# ─────────────────────────────────────────────────────────────────────────────
# AC-R11-1-2: successful Marker conversion writes .extracted.md
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_convert_marker_success_writes_frontmatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-R11-1-2: success writes {stem}.extracted.md with valid YAML frontmatter."""
    from app import config as cfg_mod

    # Point vault to tmp dir
    vault_path = tmp_path / "vault"
    (vault_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(cfg_mod.settings, "vault_path", str(vault_path))
    monkeypatch.setattr(cfg_mod.settings, "max_upload_bytes", 10 * 1024 * 1024)

    marker_markdown = "# Extracted Content\n\nThis is the extracted text from Marker.\n"
    mock_response = _make_httpx_response(200, {"markdown": marker_markdown, "pages": 1})

    mock_async_client = AsyncMock()
    mock_async_client.post = AsyncMock(return_value=mock_response)
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        files = [("files", ("report.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf"))]
        async with _make_client() as client:
            resp = await client.post("/ingest/convert-marker", files=files)

    assert resp.status_code == 200
    body = resp.json()
    results = body["results"]
    assert len(results) == 1
    assert results[0]["status"] == "ok"

    # Verify companion file was written
    companion = vault_path / "raw" / "sources" / "report.extracted.md"
    assert companion.exists(), "Expected companion .extracted.md to be written"

    content = companion.read_text(encoding="utf-8")

    # Valid YAML frontmatter (I5)
    assert content.startswith("---\n"), "Frontmatter must start with ---"
    assert "type: source" in content
    assert "title:" in content
    assert "sources:" in content
    assert "---" in content[4:]  # closing ---
    # Contains the Marker output
    assert marker_markdown in content


@pytest.mark.asyncio
async def test_convert_marker_success_no_pypdf_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-R11-1-2 / AC-R11-1-3: on success, Marker output is used (pypdf never called)."""
    from app import config as cfg_mod
    from app.ingest import extract as extract_mod

    vault_path = tmp_path / "vault"
    (vault_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(cfg_mod.settings, "vault_path", str(vault_path))
    monkeypatch.setattr(cfg_mod.settings, "max_upload_bytes", 10 * 1024 * 1024)

    marker_markdown = "# Real Marker Output\n"
    mock_response = _make_httpx_response(200, {"markdown": marker_markdown, "pages": 1})

    mock_async_client = AsyncMock()
    mock_async_client.post = AsyncMock(return_value=mock_response)
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    pypdf_called = []

    def _spy_pypdf(*args: Any, **kwargs: Any) -> str:
        pypdf_called.append(True)
        return "pypdf fallback text"

    monkeypatch.setattr(extract_mod, "_extract_pdf", _spy_pypdf)

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        files = [("files", ("doc.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf"))]
        async with _make_client() as client:
            await client.post("/ingest/convert-marker", files=files)

    assert not pypdf_called, "pypdf must NOT be called on the explicit Marker path"


# ─────────────────────────────────────────────────────────────────────────────
# AC-R11-1-3: Marker error → 502, no .extracted.md written
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_convert_marker_error_returns_502_no_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-R11-1-3: Marker non-200 → 502 with error body; no .extracted.md written."""
    from app import config as cfg_mod

    vault_path = tmp_path / "vault"
    (vault_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(cfg_mod.settings, "vault_path", str(vault_path))
    monkeypatch.setattr(cfg_mod.settings, "max_upload_bytes", 10 * 1024 * 1024)

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_response.json.return_value = {}

    mock_async_client = AsyncMock()
    mock_async_client.post = AsyncMock(return_value=mock_response)
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        files = [("files", ("failed.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf"))]
        async with _make_client() as client:
            resp = await client.post("/ingest/convert-marker", files=files)

    assert resp.status_code == 502
    body = resp.json()
    detail = body.get("detail", body)
    assert detail.get("error") == "marker_unavailable"
    assert "detail" in detail

    # No .extracted.md must be written
    sources = vault_path / "raw" / "sources"
    md_files = list(sources.glob("*.extracted.md"))
    assert not md_files, f"No .extracted.md should be written on error; found: {md_files}"


@pytest.mark.asyncio
async def test_convert_marker_timeout_returns_502(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-R11-1-3: Marker timeout → 502; no .extracted.md."""
    import httpx
    from app import config as cfg_mod

    vault_path = tmp_path / "vault"
    (vault_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(cfg_mod.settings, "vault_path", str(vault_path))
    monkeypatch.setattr(cfg_mod.settings, "max_upload_bytes", 10 * 1024 * 1024)

    mock_async_client = AsyncMock()
    mock_async_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        files = [("files", ("timeout.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf"))]
        async with _make_client() as client:
            resp = await client.post("/ingest/convert-marker", files=files)

    assert resp.status_code == 502
    detail = resp.json().get("detail", resp.json())
    assert detail.get("error") == "marker_unavailable"

    sources = vault_path / "raw" / "sources"
    assert not list(sources.glob("*.extracted.md"))


@pytest.mark.asyncio
async def test_convert_marker_connection_refused_returns_502(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-R11-1-3: Marker connection refused → 502."""
    import httpx
    from app import config as cfg_mod

    vault_path = tmp_path / "vault"
    (vault_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(cfg_mod.settings, "vault_path", str(vault_path))
    monkeypatch.setattr(cfg_mod.settings, "max_upload_bytes", 10 * 1024 * 1024)

    mock_async_client = AsyncMock()
    mock_async_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        files = [("files", ("conn.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf"))]
        async with _make_client() as client:
            resp = await client.post("/ingest/convert-marker", files=files)

    assert resp.status_code == 502
    detail = resp.json().get("detail", resp.json())
    assert detail.get("error") == "marker_unavailable"


# ─────────────────────────────────────────────────────────────────────────────
# AC-R11-1-4: GET /ingest/marker-health
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_marker_health_ok_when_marker_200() -> None:
    """AC-R11-1-4: Marker /health returns 200 → proxy returns 200 {'status':'ok'}."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"status":"ok"}'

    mock_async_client = AsyncMock()
    mock_async_client.get = AsyncMock(return_value=mock_response)
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        async with _make_client() as client:
            resp = await client.get("/ingest/marker-health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_marker_health_offline_when_marker_non_200() -> None:
    """AC-R11-1-4: Marker /health returns non-200 → proxy returns 503 {'status':'offline'}."""
    mock_response = MagicMock()
    mock_response.status_code = 503
    mock_response.text = "Service Unavailable"

    mock_async_client = AsyncMock()
    mock_async_client.get = AsyncMock(return_value=mock_response)
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        async with _make_client() as client:
            resp = await client.get("/ingest/marker-health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "offline"
    assert "detail" in body


@pytest.mark.asyncio
async def test_marker_health_offline_when_unreachable() -> None:
    """AC-R11-1-4: Marker unreachable → proxy returns 503 {'status':'offline'}."""
    import httpx

    mock_async_client = AsyncMock()
    mock_async_client.get = AsyncMock(side_effect=httpx.ConnectError("conn refused"))
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        async with _make_client() as client:
            resp = await client.get("/ingest/marker-health")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "offline"
    assert "detail" in body
