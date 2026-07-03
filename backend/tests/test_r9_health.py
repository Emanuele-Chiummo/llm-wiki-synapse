"""
Health details endpoint tests (R9-2, AC-R9-2-1..AC-R9-2-4).

Coverage:
  T-HEALTH-001  response shape — all required top-level and component keys present
  T-HEALTH-002  overall status is "ok" when all probes succeed with low latency
  T-HEALTH-003  overall status is "error" when DB probe raises
  T-HEALTH-004  overall status is "error" when Qdrant probe raises (embeddings enabled)
  T-HEALTH-005  overall status is "degraded" when DB latency exceeds 200 ms
  T-HEALTH-006  overall status is "degraded" when Qdrant latency exceeds 500 ms
  T-HEALTH-007  Qdrant reported as "skipped" when EMBEDDINGS_ENABLED=false
  T-HEALTH-008  embeddings.ok="skipped" when EMBEDDINGS_ENABLED=false
  T-HEALTH-009  endpoint always returns HTTP 200 even when a probe raises
  T-HEALTH-010  db.latency_ms is present and numeric on a successful probe
  T-HEALTH-011  last_errors list is present (may be empty)
  T-HEALTH-012  checked_at is a valid ISO datetime string

Database: no real DB/Qdrant — all probes are monkeypatched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

# ── Build a minimal test app ───────────────────────────────────────────────────


def _build_test_app() -> FastAPI:
    """
    Create a minimal FastAPI app with only the health router mounted.
    Avoids importing main.py (which triggers the full lifespan / DB / Qdrant).
    """
    from app.health import router as health_router

    test_app = FastAPI()
    test_app.include_router(health_router)
    return test_app


@pytest.fixture
def app() -> FastAPI:
    return _build_test_app()


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Helpers for common patch patterns ─────────────────────────────────────────


def _fast_db() -> dict[str, Any]:
    return {"ok": True, "latency_ms": 1.5}


def _fast_qdrant(enabled: bool = True) -> dict[str, Any]:
    if not enabled:
        return {"ok": "skipped", "latency_ms": None}
    return {"ok": True, "latency_ms": 10.0}


def _watcher_alive() -> tuple[bool, datetime | None]:
    return True, datetime.now(UTC)


def _sched_enabled() -> dict[str, Any]:
    return {"enabled": True, "last_run_at": None, "last_error": None}


def _queue_idle() -> dict[str, Any]:
    return {"running": 0, "pending": 0, "paused": False}


def _graph_cold() -> dict[str, Any]:
    return {"warm": False, "last_recompute_at": None, "node_count": 0}


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_health_shape(app: FastAPI) -> None:
    """T-HEALTH-001: all required keys present in response."""
    with (
        patch("app.health._probe_db", new=AsyncMock(return_value=_fast_db())),
        patch("app.health._probe_qdrant", new=AsyncMock(return_value=_fast_qdrant())),
        patch(
            "app.health._probe_watcher",
            new=AsyncMock(return_value={"alive": True, "last_event_at": None}),
        ),
        patch("app.health._probe_import_scheduler", new=AsyncMock(return_value=_sched_enabled())),
        patch("app.health._probe_ingest_queue", return_value=_queue_idle()),
        patch("app.health._probe_graph_cache", return_value=_graph_cold()),
    ):
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    assert resp.status_code == 200
    body = resp.json()

    # Top-level keys
    assert "status" in body
    assert "components" in body
    assert "last_errors" in body
    assert "checked_at" in body

    comps = body["components"]
    assert "watcher" in comps
    assert "import_scheduler" in comps
    assert "ingest_queue" in comps
    assert "graph_cache" in comps
    assert "database" in comps
    assert "qdrant" in comps
    assert "embeddings" in comps

    # Component sub-keys
    assert "alive" in comps["watcher"]
    assert "last_event_at" in comps["watcher"]
    assert "enabled" in comps["import_scheduler"]
    assert "last_run_at" in comps["import_scheduler"]
    assert "last_error" in comps["import_scheduler"]
    assert "running" in comps["ingest_queue"]
    assert "pending" in comps["ingest_queue"]
    assert "paused" in comps["ingest_queue"]
    assert "warm" in comps["graph_cache"]
    assert "node_count" in comps["graph_cache"]
    assert "ok" in comps["database"]
    assert "latency_ms" in comps["database"]
    assert "ok" in comps["qdrant"]
    assert "latency_ms" in comps["qdrant"]
    assert "enabled" in comps["embeddings"]
    assert "ok" in comps["embeddings"]


@pytest.mark.asyncio
async def test_status_ok_when_all_probes_succeed(app: FastAPI) -> None:
    """T-HEALTH-002: overall status is 'ok' when all probes succeed with low latency."""
    with (
        patch("app.health._probe_db", new=AsyncMock(return_value={"ok": True, "latency_ms": 5.0})),
        patch(
            "app.health._probe_qdrant", new=AsyncMock(return_value={"ok": True, "latency_ms": 20.0})
        ),
        patch(
            "app.health._probe_watcher",
            new=AsyncMock(return_value={"alive": True, "last_event_at": None}),
        ),
        patch("app.health._probe_import_scheduler", new=AsyncMock(return_value=_sched_enabled())),
        patch("app.health._probe_ingest_queue", return_value=_queue_idle()),
        patch("app.health._probe_graph_cache", return_value=_graph_cold()),
    ):
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_status_error_when_db_probe_raises(app: FastAPI) -> None:
    """T-HEALTH-003: overall status is 'error' when DB probe raises."""

    async def _failing_db() -> dict[str, Any]:
        raise ConnectionError("DB unreachable")

    with (
        patch("app.health._probe_db", new=AsyncMock(side_effect=ConnectionError("DB unreachable"))),
        patch("app.health._probe_qdrant", new=AsyncMock(return_value=_fast_qdrant())),
        patch(
            "app.health._probe_watcher",
            new=AsyncMock(return_value={"alive": True, "last_event_at": None}),
        ),
        patch("app.health._probe_import_scheduler", new=AsyncMock(return_value=_sched_enabled())),
        patch("app.health._probe_ingest_queue", return_value=_queue_idle()),
        patch("app.health._probe_graph_cache", return_value=_graph_cold()),
    ):
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    assert resp.status_code == 200  # NEVER 5xx (AC-R9-2-1)
    assert resp.json()["status"] == "error"


@pytest.mark.asyncio
async def test_status_error_when_qdrant_probe_raises(app: FastAPI) -> None:
    """T-HEALTH-004: overall status is 'error' when Qdrant probe raises (embeddings on)."""
    with (
        patch("app.health.settings") as mock_settings,
        patch("app.health._probe_db", new=AsyncMock(return_value=_fast_db())),
        patch(
            "app.health._probe_qdrant", new=AsyncMock(side_effect=ConnectionError("Qdrant down"))
        ),
        patch(
            "app.health._probe_watcher",
            new=AsyncMock(return_value={"alive": True, "last_event_at": None}),
        ),
        patch("app.health._probe_import_scheduler", new=AsyncMock(return_value=_sched_enabled())),
        patch("app.health._probe_ingest_queue", return_value=_queue_idle()),
        patch("app.health._probe_graph_cache", return_value=_graph_cold()),
    ):
        mock_settings.embeddings_enabled = True
        mock_settings.qdrant_collection = "synapse_pages"
        mock_settings.vault_id = "test"
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


@pytest.mark.asyncio
async def test_status_degraded_when_db_slow(app: FastAPI) -> None:
    """T-HEALTH-005: overall status is 'degraded' when DB latency exceeds 200 ms."""
    with (
        patch(
            "app.health._probe_db", new=AsyncMock(return_value={"ok": True, "latency_ms": 350.0})
        ),
        patch(
            "app.health._probe_qdrant", new=AsyncMock(return_value={"ok": True, "latency_ms": 10.0})
        ),
        patch(
            "app.health._probe_watcher",
            new=AsyncMock(return_value={"alive": True, "last_event_at": None}),
        ),
        patch("app.health._probe_import_scheduler", new=AsyncMock(return_value=_sched_enabled())),
        patch("app.health._probe_ingest_queue", return_value=_queue_idle()),
        patch("app.health._probe_graph_cache", return_value=_graph_cold()),
    ):
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_status_degraded_when_qdrant_slow(app: FastAPI) -> None:
    """T-HEALTH-006: overall status is 'degraded' when Qdrant latency exceeds 500 ms."""
    with (
        patch("app.health._probe_db", new=AsyncMock(return_value={"ok": True, "latency_ms": 5.0})),
        patch(
            "app.health._probe_qdrant",
            new=AsyncMock(return_value={"ok": True, "latency_ms": 600.0}),
        ),
        patch(
            "app.health._probe_watcher",
            new=AsyncMock(return_value={"alive": True, "last_event_at": None}),
        ),
        patch("app.health._probe_import_scheduler", new=AsyncMock(return_value=_sched_enabled())),
        patch("app.health._probe_ingest_queue", return_value=_queue_idle()),
        patch("app.health._probe_graph_cache", return_value=_graph_cold()),
    ):
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


@pytest.mark.asyncio
async def test_qdrant_skipped_when_embeddings_disabled(app: FastAPI) -> None:
    """T-HEALTH-007: Qdrant component shows ok='skipped' when EMBEDDINGS_ENABLED=false."""
    with (
        patch("app.health._probe_db", new=AsyncMock(return_value=_fast_db())),
        patch(
            "app.health._probe_qdrant",
            new=AsyncMock(return_value={"ok": "skipped", "latency_ms": None}),
        ),
        patch(
            "app.health._probe_watcher",
            new=AsyncMock(return_value={"alive": True, "last_event_at": None}),
        ),
        patch("app.health._probe_import_scheduler", new=AsyncMock(return_value=_sched_enabled())),
        patch("app.health._probe_ingest_queue", return_value=_queue_idle()),
        patch("app.health._probe_graph_cache", return_value=_graph_cold()),
    ):
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    assert resp.status_code == 200
    body = resp.json()
    assert body["components"]["qdrant"]["ok"] == "skipped"
    assert body["components"]["qdrant"]["latency_ms"] is None


@pytest.mark.asyncio
async def test_embeddings_ok_skipped_when_disabled(app: FastAPI) -> None:
    """T-HEALTH-008: embeddings.ok='skipped' when EMBEDDINGS_ENABLED=false."""
    with (
        patch("app.health._probe_db", new=AsyncMock(return_value=_fast_db())),
        patch(
            "app.health._probe_qdrant",
            new=AsyncMock(return_value={"ok": "skipped", "latency_ms": None}),
        ),
        patch(
            "app.health._probe_watcher",
            new=AsyncMock(return_value={"alive": True, "last_event_at": None}),
        ),
        patch("app.health._probe_import_scheduler", new=AsyncMock(return_value=_sched_enabled())),
        patch("app.health._probe_ingest_queue", return_value=_queue_idle()),
        patch("app.health._probe_graph_cache", return_value=_graph_cold()),
        patch("app.health.settings") as mock_settings,
    ):
        mock_settings.embeddings_enabled = False
        mock_settings.vault_id = "test"
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    assert resp.status_code == 200
    body = resp.json()
    assert body["components"]["embeddings"]["ok"] == "skipped"


@pytest.mark.asyncio
async def test_endpoint_always_returns_200(app: FastAPI) -> None:
    """T-HEALTH-009: endpoint returns HTTP 200 even when multiple probes raise."""
    with (
        patch("app.health._probe_db", new=AsyncMock(side_effect=RuntimeError("DB down"))),
        patch("app.health._probe_qdrant", new=AsyncMock(side_effect=RuntimeError("Qdrant down"))),
        patch(
            "app.health._probe_watcher", new=AsyncMock(side_effect=RuntimeError("Watcher broken"))
        ),
        patch(
            "app.health._probe_import_scheduler",
            new=AsyncMock(side_effect=RuntimeError("Sched broken")),
        ),
        patch("app.health._probe_ingest_queue", side_effect=RuntimeError("Queue broken")),
        patch("app.health._probe_graph_cache", side_effect=RuntimeError("Cache broken")),
    ):
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    # Must always be 200 (AC-R9-2-1)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"


@pytest.mark.asyncio
async def test_db_latency_present_and_numeric(app: FastAPI) -> None:
    """T-HEALTH-010: db.latency_ms is present and numeric on a successful probe."""
    with (
        patch("app.health._probe_db", new=AsyncMock(return_value={"ok": True, "latency_ms": 42.7})),
        patch("app.health._probe_qdrant", new=AsyncMock(return_value=_fast_qdrant())),
        patch(
            "app.health._probe_watcher",
            new=AsyncMock(return_value={"alive": True, "last_event_at": None}),
        ),
        patch("app.health._probe_import_scheduler", new=AsyncMock(return_value=_sched_enabled())),
        patch("app.health._probe_ingest_queue", return_value=_queue_idle()),
        patch("app.health._probe_graph_cache", return_value=_graph_cold()),
    ):
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    assert resp.status_code == 200
    latency = resp.json()["components"]["database"]["latency_ms"]
    assert latency is not None
    assert isinstance(latency, float | int)
    assert latency == pytest.approx(42.7, abs=0.1)


@pytest.mark.asyncio
async def test_last_errors_list_present(app: FastAPI) -> None:
    """T-HEALTH-011: last_errors list is present (may be empty list)."""
    with (
        patch("app.health._probe_db", new=AsyncMock(return_value=_fast_db())),
        patch("app.health._probe_qdrant", new=AsyncMock(return_value=_fast_qdrant())),
        patch(
            "app.health._probe_watcher",
            new=AsyncMock(return_value={"alive": True, "last_event_at": None}),
        ),
        patch("app.health._probe_import_scheduler", new=AsyncMock(return_value=_sched_enabled())),
        patch("app.health._probe_ingest_queue", return_value=_queue_idle()),
        patch("app.health._probe_graph_cache", return_value=_graph_cold()),
    ):
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    assert resp.status_code == 200
    body = resp.json()
    assert "last_errors" in body
    assert isinstance(body["last_errors"], list)


@pytest.mark.asyncio
async def test_checked_at_is_iso_datetime(app: FastAPI) -> None:
    """T-HEALTH-012: checked_at is a valid ISO datetime string."""
    with (
        patch("app.health._probe_db", new=AsyncMock(return_value=_fast_db())),
        patch("app.health._probe_qdrant", new=AsyncMock(return_value=_fast_qdrant())),
        patch(
            "app.health._probe_watcher",
            new=AsyncMock(return_value={"alive": True, "last_event_at": None}),
        ),
        patch("app.health._probe_import_scheduler", new=AsyncMock(return_value=_sched_enabled())),
        patch("app.health._probe_ingest_queue", return_value=_queue_idle()),
        patch("app.health._probe_graph_cache", return_value=_graph_cold()),
    ):
        async with _client(app) as c:
            resp = await c.get("/health/detailed")

    assert resp.status_code == 200
    checked_at = resp.json()["checked_at"]
    assert isinstance(checked_at, str)
    # Must parse as a valid datetime
    parsed = datetime.fromisoformat(checked_at)
    assert parsed is not None
