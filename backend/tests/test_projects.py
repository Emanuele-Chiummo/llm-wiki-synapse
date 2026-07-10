"""
Tests for GET /projects — multi-vault project registry (v1.5 P2, ADR-0067).

Covered:
  - Seeds a one-entry registry from the boot vault when projects.json is absent (back-compat).
  - Reads an existing registry (order + active_id preserved).
  - Corrupt/unparseable registry degrades to the boot vault, no crash.
  - active_id pointing at an unknown project falls back to the first project.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient


@asynccontextmanager
async def _null_lifespan(app: Any) -> Any:  # noqa: ANN401
    yield


def _client() -> AsyncClient:
    from app.main import app

    app.router.lifespan_context = _null_lifespan
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _seed_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point SYNAPSE_STATE_DIR at a temp dir and the boot vault at a temp path."""
    state = tmp_path / "state"
    monkeypatch.setenv("SYNAPSE_STATE_DIR", str(state))
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "default")
    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path / "vault"))
    return state


@pytest.mark.asyncio
async def test_projects_seeds_from_boot_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No registry yet → one project (the boot vault), marked active."""
    _seed_env(tmp_path, monkeypatch)
    async with _client() as c:
        resp = await c.get("/projects")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["projects"]) == 1
    assert body["projects"][0]["id"] == "default"
    assert body["active_id"] == "default"


@pytest.mark.asyncio
async def test_projects_reads_existing_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing registry file is returned verbatim (order + active_id)."""
    state = _seed_env(tmp_path, monkeypatch)
    state.mkdir(parents=True, exist_ok=True)
    (state / "projects.json").write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "a",
                        "name": "A",
                        "path": "/x/a",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                    {
                        "id": "b",
                        "name": "B",
                        "path": "/x/b",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    },
                ],
                "active_id": "b",
            }
        ),
        encoding="utf-8",
    )
    async with _client() as c:
        resp = await c.get("/projects")
    body = resp.json()
    assert [p["id"] for p in body["projects"]] == ["a", "b"]
    assert body["active_id"] == "b"


@pytest.mark.asyncio
async def test_projects_corrupt_registry_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt projects.json degrades to the boot vault instead of crashing."""
    state = _seed_env(tmp_path, monkeypatch)
    state.mkdir(parents=True, exist_ok=True)
    (state / "projects.json").write_text("{ not json", encoding="utf-8")
    async with _client() as c:
        resp = await c.get("/projects")
    assert resp.status_code == 200
    assert len(resp.json()["projects"]) == 1


@pytest.mark.asyncio
async def test_projects_active_fallback_when_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """active_id pointing at a missing project falls back to the first project."""
    state = _seed_env(tmp_path, monkeypatch)
    state.mkdir(parents=True, exist_ok=True)
    (state / "projects.json").write_text(
        json.dumps(
            {
                "projects": [
                    {
                        "id": "a",
                        "name": "A",
                        "path": "/x/a",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                ],
                "active_id": "ghost",
            }
        ),
        encoding="utf-8",
    )
    async with _client() as c:
        resp = await c.get("/projects")
    assert resp.json()["active_id"] == "a"
