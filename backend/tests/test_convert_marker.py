"""
Tests for R11-1 Marker conversion endpoints (ADR-0051 / W0 async rewrite).

Acceptance checks:
  AC-R11-1-1 : POST /ingest/convert-marker → 400 for >10 files, 413 for oversize,
               415 for non-pdf, 409 if a batch is already running
  AC-R11-1-2 : Returns 202 immediately (no blocking Marker call in the request handler).
               Background task writes {stem}.extracted.md with valid YAML frontmatter (I5).
  AC-R11-1-3 : Per-file Marker failure marks that file 'failed' with detail;
               does NOT fail the rest of the batch; no .extracted.md written for that file.
  AC-R11-1-4 : GET /ingest/marker-health proxies Marker /health:
               200 {"status":"ok"} when Marker 200; 503 {"status":"offline",...} otherwise.
  AC-W0-1    : GET /ingest/convert-marker/status returns per-file progress snapshot.
  AC-W0-2    : GET /ingest/queue response includes marker_batch summary field.
"""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# ── Test helpers ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_marker_state() -> Any:
    """
    Reset marker_converter module state before (and after) every test.

    Prevents state pollution from module-level _current_batch / _current_task
    between tests that run in separate asyncio event loops.
    """
    from app.marker_converter import _reset_state

    _reset_state()
    yield
    _reset_state()


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


async def _drain_marker_task() -> None:
    """
    Await the current marker background task to completion.

    Must be called after POST /ingest/convert-marker returns 202 to let the
    background driver run before assertions on the filesystem or status endpoint.
    Silently ignores CancelledError (task was cancelled in cleanup) and tasks
    that are already done.
    """
    from app.marker_converter import get_current_task

    task = get_current_task()
    if task is not None and not task.done():
        try:
            await task
        except Exception:  # noqa: BLE001
            pass


# ─────────────────────────────────────────────────────────────────────────────
# AC-R11-1-1: rejection paths (remain synchronous)
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
# AC-R11-1-1 (extra): single-flight guard
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_convert_marker_rejects_concurrent_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-R11-1-1 / AC-W0: second POST while a batch is running → 409."""
    import app.marker_converter as mc_mod

    vault_path = tmp_path / "vault"
    (vault_path / "raw" / "sources").mkdir(parents=True)

    from app import config as cfg_mod

    monkeypatch.setattr(cfg_mod.settings, "vault_path", str(vault_path))
    monkeypatch.setattr(cfg_mod.settings, "max_upload_bytes", 10 * 1024 * 1024)

    # Simulate an in-progress batch by patching is_running directly
    monkeypatch.setattr(mc_mod, "is_running", lambda: True)

    files = [("files", ("report.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf"))]
    async with _make_client() as client:
        resp = await client.post("/ingest/convert-marker", files=files)

    assert resp.status_code == 409
    assert "already running" in resp.text.lower()


# ─────────────────────────────────────────────────────────────────────────────
# AC-R11-1-2 / AC-W0-1: immediate 202 return + async companion write
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_convert_marker_returns_202_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    AC-W0: POST /ingest/convert-marker returns 202 without waiting for Marker to respond.

    Strategy: use an asyncio.Event gate so the background Marker call is blocked.
    Verify the HTTP response arrives (202) while the task is still "converting" (gate not
    yet released). This proves the request handler does not block on the Marker call.
    """
    from app import config as cfg_mod

    vault_path = tmp_path / "vault"
    (vault_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(cfg_mod.settings, "vault_path", str(vault_path))
    monkeypatch.setattr(cfg_mod.settings, "max_upload_bytes", 10 * 1024 * 1024)

    # Gate that keeps the mock Marker call blocked until we release it
    _gate = asyncio.Event()

    async def _gated_post(*args: Any, **kwargs: Any) -> MagicMock:
        await _gate.wait()  # Blocks until we set the event
        return _make_httpx_response(200, {"markdown": "# content", "pages": 1})

    mock_async_client = AsyncMock()
    mock_async_client.post = _gated_post
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        files = [("files", ("report.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf"))]
        async with _make_client() as client:
            resp = await client.post("/ingest/convert-marker", files=files)

        # Response must be 202 — the handler did not block on Marker
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "batch_id" in body
        assert body["total"] == 1
        assert len(body["queued"]) == 1
        assert body["queued"][0]["file"] == "report.pdf"

        # Release the gate and drain the task cleanly INSIDE the patch block
        _gate.set()
        await _drain_marker_task()


@pytest.mark.asyncio
async def test_convert_marker_success_writes_frontmatter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-R11-1-2: background task writes {stem}.extracted.md with valid YAML frontmatter."""
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

    # Keep the patch active while draining the background task: the driver imports
    # httpx lazily so the patch must still be in scope when the task runs.
    with patch("httpx.AsyncClient", return_value=mock_async_client):
        files = [("files", ("report.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf"))]
        async with _make_client() as client:
            resp = await client.post("/ingest/convert-marker", files=files)

        assert resp.status_code == 202
        body = resp.json()
        assert body["total"] == 1
        batch_id = body["batch_id"]
        assert batch_id  # non-empty UUID string

        # Await the background task INSIDE the patch block so the mock httpx is used
        await _drain_marker_task()

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

        # Await background task INSIDE the patch block so mock httpx is used
        await _drain_marker_task()

    assert not pypdf_called, "pypdf must NOT be called on the explicit Marker path"


# ─────────────────────────────────────────────────────────────────────────────
# AC-R11-1-3: Marker errors become per-file failures (no whole-batch abort)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_convert_marker_error_marks_file_failed_no_companion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    AC-R11-1-3 (W0): Marker non-200 marks the file 'failed'; no .extracted.md written;
    POST still returns 202 (failure does NOT abort the HTTP response).
    """
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

        # POST returns 202 immediately — the Marker error is a background concern
        assert resp.status_code == 202
        body = resp.json()
        assert body["total"] == 1

        # Await background task INSIDE patch block so mock httpx is used
        await _drain_marker_task()

    # No .extracted.md must be written
    sources = vault_path / "raw" / "sources"
    md_files = list(sources.glob("*.extracted.md"))
    assert not md_files, f"No .extracted.md should be written on error; found: {md_files}"

    # Status endpoint must show failure
    async with _make_client() as client:
        status_resp = await client.get("/ingest/convert-marker/status")
    assert status_resp.status_code == 200
    status = status_resp.json()
    assert status["running"] is False
    assert len(status["files"]) == 1
    assert status["files"][0]["status"] == "failed"
    assert status["files"][0]["detail"] is not None


@pytest.mark.asyncio
async def test_convert_marker_timeout_marks_file_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-R11-1-3 (W0): Marker timeout marks file 'failed'; no .extracted.md; POST returns 202."""
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

        assert resp.status_code == 202

        # Drain INSIDE patch block so mock httpx is used
        await _drain_marker_task()

    sources = vault_path / "raw" / "sources"
    assert not list(sources.glob("*.extracted.md"))

    async with _make_client() as client:
        status_resp = await client.get("/ingest/convert-marker/status")
    status = status_resp.json()
    assert status["files"][0]["status"] == "failed"
    assert "timed out" in status["files"][0]["detail"].lower()


@pytest.mark.asyncio
async def test_convert_marker_connection_refused_marks_file_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-R11-1-3 (W0): Marker connection refused marks file 'failed'; POST returns 202."""
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

        assert resp.status_code == 202

        # Drain INSIDE patch block
        await _drain_marker_task()

    async with _make_client() as client:
        status_resp = await client.get("/ingest/convert-marker/status")
    status = status_resp.json()
    assert status["files"][0]["status"] == "failed"
    assert "unreachable" in status["files"][0]["detail"].lower()


@pytest.mark.asyncio
async def test_convert_marker_partial_failure_does_not_abort_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    AC-R11-1-3 (W0): one file fails, the next succeeds — batch is NOT aborted.
    The successful file's companion IS written; the failed file's companion is NOT.
    """
    import httpx
    from app import config as cfg_mod

    vault_path = tmp_path / "vault"
    (vault_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(cfg_mod.settings, "vault_path", str(vault_path))
    monkeypatch.setattr(cfg_mod.settings, "max_upload_bytes", 10 * 1024 * 1024)

    call_count = 0

    async def _side_effect(*args: Any, **kwargs: Any) -> MagicMock:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("first call fails")
        return _make_httpx_response(200, {"markdown": "# Good output\n", "pages": 1})

    mock_async_client = AsyncMock()
    mock_async_client.post = _side_effect
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    pdf_bytes = _make_pdf_bytes()
    with patch("httpx.AsyncClient", return_value=mock_async_client):
        files = [
            ("files", ("bad.pdf", io.BytesIO(pdf_bytes), "application/pdf")),
            ("files", ("good.pdf", io.BytesIO(pdf_bytes), "application/pdf")),
        ]
        async with _make_client() as client:
            resp = await client.post("/ingest/convert-marker", files=files)

        assert resp.status_code == 202
        assert resp.json()["total"] == 2

        # Drain INSIDE patch block so mock httpx is used for both files
        await _drain_marker_task()

    sources = vault_path / "raw" / "sources"
    # bad.pdf companion must NOT exist
    assert not (sources / "bad.extracted.md").exists()
    # good.pdf companion MUST exist
    assert (sources / "good.extracted.md").exists()

    async with _make_client() as client:
        status_resp = await client.get("/ingest/convert-marker/status")
    status = status_resp.json()
    assert status["running"] is False
    statuses = {f["safe_stem"]: f["status"] for f in status["files"]}
    assert statuses["bad"] == "failed"
    assert statuses["good"] == "ok"


# ─────────────────────────────────────────────────────────────────────────────
# AC-W0-1: GET /ingest/convert-marker/status
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_convert_marker_status_no_batch() -> None:
    """AC-W0-1: GET /ingest/convert-marker/status when no batch has run → empty/idle."""
    # autouse reset_marker_state fixture ensures _current_batch is None
    async with _make_client() as client:
        resp = await client.get("/ingest/convert-marker/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    assert body["total"] == 0
    assert body["done"] == 0
    assert body["files"] == []


@pytest.mark.asyncio
async def test_convert_marker_status_shows_ok_after_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC-W0-1: status endpoint shows 'ok' after successful conversion."""
    from app import config as cfg_mod

    vault_path = tmp_path / "vault"
    (vault_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(cfg_mod.settings, "vault_path", str(vault_path))
    monkeypatch.setattr(cfg_mod.settings, "max_upload_bytes", 10 * 1024 * 1024)

    mock_response = _make_httpx_response(200, {"markdown": "# Doc\n", "pages": 1})
    mock_async_client = AsyncMock()
    mock_async_client.post = AsyncMock(return_value=mock_response)
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_async_client):
        files = [("files", ("report.pdf", io.BytesIO(_make_pdf_bytes()), "application/pdf"))]
        async with _make_client() as client:
            post_resp = await client.post("/ingest/convert-marker", files=files)

        assert post_resp.status_code == 202
        batch_id_from_post = post_resp.json()["batch_id"]

        # Drain INSIDE patch block so mock httpx is used by the background driver
        await _drain_marker_task()

    async with _make_client() as client:
        status_resp = await client.get("/ingest/convert-marker/status")
    assert status_resp.status_code == 200
    body = status_resp.json()
    assert body["running"] is False
    assert body["batch_id"] == batch_id_from_post
    assert body["total"] == 1
    assert body["done"] == 1
    assert len(body["files"]) == 1
    f = body["files"][0]
    assert f["status"] == "ok"
    assert f["companion_path"] is not None
    assert f["detail"] is None


# ─────────────────────────────────────────────────────────────────────────────
# AC-W0-2: GET /ingest/queue includes marker_batch
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_queue_includes_marker_batch_field() -> None:
    """AC-W0-2: GET /ingest/queue response includes 'marker_batch' field (null when idle)."""
    # autouse reset_marker_state fixture ensures _current_batch is None
    async with _make_client() as client:
        resp = await client.get("/ingest/queue")
    assert resp.status_code == 200
    body = resp.json()
    # marker_batch key must exist; null when no batch is running
    assert "marker_batch" in body
    # When no batch has run, marker_batch should be null
    assert body["marker_batch"] is None


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
