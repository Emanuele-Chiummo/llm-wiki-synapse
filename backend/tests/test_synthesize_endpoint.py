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
    assert body["mode"] == "auto"
    assert body["max_candidates"] >= body["max_pages"]

    await asyncio.sleep(0.05)
    assert len(calls) == 1
    assert calls[0]["max_pages"] is None
    assert calls[0]["token_budget"] is None
    assert calls[0]["force"] is False
    assert calls[0]["mode"] == "auto"


async def test_synthesize_endpoint_accepts_bounded_mode_and_candidates(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run(**kwargs: Any) -> Any:
        calls.append(kwargs)
        return sy.SynthesizeSummary()

    monkeypatch.setattr(sy, "run_synthesize", fake_run)
    r = await client.post(
        "/ops/synthesize",
        json={"mode": "review-only", "max_candidates": 3, "max_pages": 2},
    )
    assert r.status_code == 202
    assert r.json()["mode"] == "review-only"
    assert r.json()["max_candidates"] == 3
    await asyncio.sleep(0.05)
    assert calls[0]["mode"] == "review-only"
    assert calls[0]["max_candidates"] == 3


async def test_synthesize_endpoint_accepts_empty_json_body(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run(**kwargs: Any) -> Any:
        return sy.SynthesizeSummary()

    monkeypatch.setattr(sy, "run_synthesize", fake_run)

    r = await client.post("/ops/synthesize", json={})
    assert r.status_code == 202


async def test_synthesize_endpoint_reserves_single_flight_before_task_runs(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two concurrent POSTs cannot both pass the pre-task single-flight gate."""
    release = asyncio.Event()

    async def blocked_run(**kwargs: Any) -> Any:
        try:
            await release.wait()
            return sy.SynthesizeSummary()
        finally:
            sy._state.is_running = False
            sy._state.current = {}

    monkeypatch.setattr(sy, "run_synthesize", blocked_run)

    first = await client.post("/ops/synthesize")
    second = await client.post("/ops/synthesize")

    assert first.status_code == 202
    assert second.status_code == 409
    release.set()
    await asyncio.sleep(0.01)


async def test_synthesize_audit_endpoint_is_read_only(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_audit(vault_id: str, max_pages: int = 500) -> dict[str, Any]:
        return {
            "pages_scanned": 4,
            "legacy_pages": 4,
            "duplicate_groups": 1,
            "groups": [{"kind": "comparison", "pages": []}],
            "dry_run": True,
        }

    monkeypatch.setattr(sy, "audit_legacy_duplicates", fake_audit)
    r = await client.get("/ops/synthesize/audit?max_pages=100")
    assert r.status_code == 200
    assert r.json()["dry_run"] is True
    assert r.json()["duplicate_groups"] == 1
