"""
POST /ops/synthesize endpoint tests — infra-free.

Covers the request-body compatibility needed by homepage triggers: older clients may
POST with no JSON body, while newer clients send an explicit empty object.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.ops import synthesize as sy
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


@asynccontextmanager
async def _noop_lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield


@pytest.fixture()
async def client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    app.router.lifespan_context = _noop_lifespan  # type: ignore[assignment]
    sy._state.is_running = False
    sy._state.last_summary = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_synthesize_endpoint_accepts_missing_body(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return sy.SynthesizeSummary()

    monkeypatch.setattr(sy, "run_synthesize", fake_run)

    r = await client.post("/ops/synthesize")
    assert r.status_code == 202
    body = r.json()
    mp, tb = sy.clamp_bounds(None, None)
    assert body["status"] == "started"
    assert body["max_pages"] == mp
    assert body["token_budget"] == tb
    assert body["force"] is False

    await asyncio.sleep(0.05)
    assert len(calls) == 1
    assert calls[0]["max_pages"] is None
    assert calls[0]["token_budget"] is None
    assert calls[0]["force"] is False


async def test_synthesize_endpoint_accepts_empty_json_body(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(**kwargs: Any) -> Any:
        return sy.SynthesizeSummary()

    monkeypatch.setattr(sy, "run_synthesize", fake_run)

    r = await client.post("/ops/synthesize", json={})
    assert r.status_code == 202
