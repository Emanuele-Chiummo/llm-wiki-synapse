"""
POST/GET /ops/reclassify-types endpoint tests (SPRINT-v1.2 tail) — infra-free.

Covers the contract behaviours wired in main.py:
  * 409 when a run is already in flight (single-flight).
  * 202 with the CLAMPED bounds echoed; run_reclassify is scheduled with the raw body values.
  * NO dormant-400 — schema.md always exists (the endpoint never short-circuits on config).
  * GET returns {running, last_summary} from the module single-flight state.

The heavy lifting (run_reclassify itself) is covered by test_reclassify_types.py; here it is
monkeypatched so no provider/DB is touched.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.ops import reclassify_types as rt
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _noop_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


@pytest.fixture()
async def client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    from app.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    # Clean module single-flight state between tests.
    rt._state.is_running = False
    rt._state.last_summary = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_reclassify_endpoint_409_when_running(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    rt._state.is_running = True
    try:
        r = await client.post("/ops/reclassify-types", json={})
        assert r.status_code == 409
    finally:
        rt._state.is_running = False


async def test_reclassify_endpoint_202_starts_run(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return rt.ReclassifySummary()

    monkeypatch.setattr(rt, "run_reclassify", fake_run)

    r = await client.post(
        "/ops/reclassify-types", json={"max_pages": 5, "token_budget": 1000, "force": True}
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "started"
    mp, tb = rt.clamp_bounds(5, 1000)
    assert body["max_pages"] == mp
    assert body["token_budget"] == tb
    assert body["force"] is True

    # Let the fire-and-forget task run.
    await asyncio.sleep(0.05)
    assert len(calls) == 1
    assert calls[0]["max_pages"] == 5
    assert calls[0]["force"] is True


async def test_reclassify_endpoint_202_no_dormant_gate(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unlike backfill-domains, there is NO dormant-400: an empty body still starts (schema.md
    # always exists). Prove a bare POST returns 202, not 400.
    async def fake_run(**kwargs: Any) -> Any:
        return rt.ReclassifySummary()

    monkeypatch.setattr(rt, "run_reclassify", fake_run)
    r = await client.post("/ops/reclassify-types", json={})
    assert r.status_code == 202


async def test_reclassify_endpoint_get_status(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = await client.get("/ops/reclassify-types")
    assert r.status_code == 200
    assert r.json() == {"running": False, "last_summary": None}

    rt._state.last_summary = rt.ReclassifySummary(
        processed=3, changed=2, skipped=1, by_type={"entity": 2}, stopped_reason="complete"
    )
    try:
        r2 = await client.get("/ops/reclassify-types")
        data = r2.json()
        assert data["running"] is False
        assert data["last_summary"]["processed"] == 3
        assert data["last_summary"]["changed"] == 2
        assert data["last_summary"]["by_type"] == {"entity": 2}
        assert data["last_summary"]["stopped_reason"] == "complete"
    finally:
        rt._state.last_summary = None
