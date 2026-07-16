"""
Tests for GET /projects — multi-vault project registry (v1.5 P2, ADR-0082).

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


# ── POST /projects/open — register existing vault (slice 2) ───────────────────


@pytest.mark.asyncio
async def test_open_registers_existing_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening an existing dir registers it and persists the registry file."""
    _seed_env(tmp_path, monkeypatch)
    existing = tmp_path / "my-vault"
    existing.mkdir()
    async with _client() as c:
        resp = await c.post("/projects/open", json={"path": str(existing)})
        assert resp.status_code == 200, resp.text
        proj = resp.json()
        assert proj["name"] == "my-vault"
        assert proj["path"] == str(existing.resolve())
        # It now shows up in GET /projects.
        listed = (await c.get("/projects")).json()
    assert any(p["id"] == proj["id"] for p in listed["projects"])


@pytest.mark.asyncio
async def test_open_rejects_relative_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_env(tmp_path, monkeypatch)
    async with _client() as c:
        resp = await c.post("/projects/open", json={"path": "relative/dir"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_open_404_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_env(tmp_path, monkeypatch)
    async with _client() as c:
        resp = await c.post("/projects/open", json={"path": str(tmp_path / "nope")})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_open_is_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Opening the same path twice returns the same project id (no duplicate)."""
    _seed_env(tmp_path, monkeypatch)
    d = tmp_path / "v"
    d.mkdir()
    async with _client() as c:
        a = (await c.post("/projects/open", json={"path": str(d)})).json()
        b = (await c.post("/projects/open", json={"path": str(d)})).json()
        listed = (await c.get("/projects")).json()
    assert a["id"] == b["id"]
    assert sum(1 for p in listed["projects"] if p["id"] == a["id"]) == 1


# ── POST /projects — create + scaffold (slice 2) ──────────────────────────────


@pytest.mark.asyncio
async def test_create_scaffolds_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Creating a project scaffolds raw/, wiki/, purpose.md, schema.md at the path."""
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "new-vault"
    async with _client() as c:
        resp = await c.post("/projects", json={"name": "New Vault", "path": str(target)})
    assert resp.status_code == 201, resp.text
    proj = resp.json()
    assert proj["name"] == "New Vault"
    assert proj["id"] == "new-vault"  # slugified
    assert (target / "wiki").is_dir()
    assert (target / "raw" / "sources").is_dir()
    assert (target / "purpose.md").exists()
    assert (target / "schema.md").exists()


@pytest.mark.asyncio
async def test_create_409_on_duplicate_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_env(tmp_path, monkeypatch)
    target = tmp_path / "dup"
    async with _client() as c:
        first = await c.post("/projects", json={"name": "Dup", "path": str(target)})
        assert first.status_code == 201
        second = await c.post("/projects", json={"name": "Dup2", "path": str(target)})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_create_rejects_relative_and_empty_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_env(tmp_path, monkeypatch)
    async with _client() as c:
        rel = await c.post("/projects", json={"name": "X", "path": "rel/dir"})
        empty = await c.post("/projects", json={"name": "  ", "path": str(tmp_path / "z")})
    assert rel.status_code == 400
    assert empty.status_code == 400


# ── POST /projects/{id}/activate — runtime switch (slice 3) ───────────────────


@pytest.mark.asyncio
async def test_activate_updates_registry_and_calls_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """activate records active_id in the registry and invokes the runtime switch."""
    _seed_env(tmp_path, monkeypatch)
    (tmp_path / "va").mkdir()
    (tmp_path / "vb").mkdir()

    called: list[str] = []

    async def _spy(project: Any) -> None:  # noqa: ANN401
        called.append(project.id)

    from app import projects as proj_mod

    monkeypatch.setattr(proj_mod, "_apply_active_vault", _spy)

    async with _client() as c:
        a = (await c.post("/projects/open", json={"path": str(tmp_path / "va")})).json()
        b = (await c.post("/projects/open", json={"path": str(tmp_path / "vb")})).json()
        resp = await c.post(f"/projects/{b['id']}/activate")
        assert resp.status_code == 200, resp.text
        assert resp.json()["project"]["id"] == b["id"]
        listed = (await c.get("/projects")).json()

    assert listed["active_id"] == b["id"]
    assert called == [b["id"]]
    # 'a' still present, just not active
    assert a["id"] in {p["id"] for p in listed["projects"]}


@pytest.mark.asyncio
async def test_activate_unknown_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_env(tmp_path, monkeypatch)
    async with _client() as c:
        resp = await c.post("/projects/does-not-exist/activate")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_apply_active_vault_mutates_settings_and_bumps_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime helper re-points settings and bumps the epoch (side effects stubbed)."""
    from app import projects as proj_mod
    from app.config import settings

    # Stub the risky side effects so the unit test touches no watcher/DB.
    monkeypatch.setattr("app.watcher.stop_watcher", lambda: None)
    monkeypatch.setattr("app.watcher.start_watcher", lambda _loop: None)

    async def _noop_seed() -> None:
        return None

    monkeypatch.setattr("app.main._seed_vault_state", _noop_seed)
    monkeypatch.setattr("app.main._graph_cache", None, raising=False)

    before = proj_mod.active_vault_epoch()
    project = proj_mod.Project(
        id="switched", name="Switched", path="/tmp/switched", created_at="2026-01-01T00:00:00+00:00"
    )
    await proj_mod._apply_active_vault(project)

    assert settings.vault_id == "switched"
    assert settings.vault_path == "/tmp/switched"
    assert proj_mod.active_vault_epoch() == before + 1
