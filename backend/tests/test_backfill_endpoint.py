"""
POST/GET /ops/backfill-domains endpoint tests (ADR-0054 §6, R12-2) — infra-free.

Covers the four contract behaviours wired in main.py:
  * 400 when the domain vocabulary is dormant (empty) — no task started.
  * 409 when a run is already in flight (single-flight).
  * 202 with the CLAMPED bounds echoed; run_backfill is scheduled with the raw body values.
  * GET returns {running, last_summary} from the module single-flight state.

The heavy lifting (run_backfill itself) is covered by test_backfill_domains.py; here it is
monkeypatched so no provider/DB is touched.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.ops import backfill_domains as bf
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
    bf._state.is_running = False
    bf._state.last_summary = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_backfill_endpoint_400_when_dormant(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.config_overrides.effective_domain_vocabulary", lambda: [])
    r = await client.post("/ops/backfill-domains", json={})
    assert r.status_code == 400
    assert "dormant" in r.json()["error"]["message"]


async def test_backfill_endpoint_409_when_running(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.config_overrides.effective_domain_vocabulary", lambda: ["ServiceNow"])
    bf._state.is_running = True
    try:
        r = await client.post("/ops/backfill-domains", json={})
        assert r.status_code == 409
    finally:
        bf._state.is_running = False


async def test_backfill_endpoint_202_starts_run(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.config_overrides.effective_domain_vocabulary", lambda: ["ServiceNow", "SAM"]
    )
    calls: list[dict[str, Any]] = []

    async def fake_run(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return bf.BackfillSummary()

    monkeypatch.setattr(bf, "run_backfill", fake_run)

    r = await client.post(
        "/ops/backfill-domains", json={"max_pages": 5, "token_budget": 1000, "force": True}
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "started"
    mp, tb = bf.clamp_bounds(5, 1000)
    assert body["max_pages"] == mp
    assert body["token_budget"] == tb
    assert body["force"] is True

    # Let the fire-and-forget task run.
    await asyncio.sleep(0.05)
    assert len(calls) == 1
    assert calls[0]["max_pages"] == 5
    assert calls[0]["force"] is True


async def test_backfill_endpoint_get_status(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = await client.get("/ops/backfill-domains")
    assert r.status_code == 200
    assert r.json() == {"running": False, "last_summary": None}

    bf._state.last_summary = bf.BackfillSummary(processed=3, tagged=2, stopped_reason="complete")
    try:
        r2 = await client.get("/ops/backfill-domains")
        data = r2.json()
        assert data["running"] is False
        assert data["last_summary"]["processed"] == 3
        assert data["last_summary"]["tagged"] == 2
        assert data["last_summary"]["stopped_reason"] == "complete"
    finally:
        bf._state.last_summary = None
