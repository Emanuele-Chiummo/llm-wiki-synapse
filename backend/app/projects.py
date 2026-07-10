"""
Multi-vault Project registry + endpoints (v1.5 P2, ADR-0067).

A **project** = a vault folder. Synapse serves ONE active vault at a time (Model A); this module
owns the persisted list of known projects and which one is active, so the frontend Project
Launcher can list / open / create / switch them (LLM Wiki parity).

State lives OUTSIDE any single vault, at ``$SYNAPSE_STATE_DIR/projects.json`` (default
``~/.synapse/projects.json``) — filesystem state, no DB, bounded reads/writes (mirrors the
clip/MCP token-config precedent). Each project's ``id`` doubles as its ``vault_id`` for the
already-vault-scoped Postgres/Qdrant rows.

This first slice is READ-ONLY: ``GET /projects``. Create / open / activate land in later P2
slices (ADR-0067 §5). The registry is seeded from the boot vault (``settings.vault_id`` /
``vault_path``) on first read, so existing single-vault deploys keep working unchanged.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["projects"])


# ── Models ─────────────────────────────────────────────────────────────────────


class Project(BaseModel):
    """One known project (vault). ``id`` is the vault_id used for its DB/Qdrant rows."""

    id: str
    name: str
    path: str  # absolute vault root
    created_at: str
    last_opened_at: str | None = None


class ProjectsResponse(BaseModel):
    """Response for GET /projects — the registry snapshot."""

    projects: list[Project]
    active_id: str | None


# ── State-dir + registry file ────────────────────────────────────────────────


def _state_dir() -> Path:
    """Cross-vault state directory (``$SYNAPSE_STATE_DIR`` or ``~/.synapse``)."""
    raw = os.environ.get("SYNAPSE_STATE_DIR", "").strip()
    return Path(raw).expanduser() if raw else (Path.home() / ".synapse")


def _registry_path() -> Path:
    return _state_dir() / "projects.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _boot_project() -> Project:
    """The project describing the vault this process booted with (seed + back-compat)."""
    return Project(
        id=settings.vault_id,
        name=settings.vault_id or "default",
        path=str(settings.vault_root),
        created_at=_now_iso(),
        last_opened_at=_now_iso(),
    )


def read_registry() -> ProjectsResponse:
    """
    Read the registry from disk, seeding it from the boot vault when absent/empty/corrupt.

    Never raises: a missing or unparseable file degrades to a one-entry registry containing the
    boot vault (marked active), so the app always has at least the vault it is serving.
    """
    path = _registry_path()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            projects = [Project.model_validate(p) for p in data.get("projects", [])]
            active_id = data.get("active_id")
            if projects:
                if active_id not in {p.id for p in projects}:
                    active_id = projects[0].id
                return ProjectsResponse(projects=projects, active_id=active_id)
    except (OSError, ValueError, TypeError) as exc:  # noqa: BLE001 - degrade, don't crash
        logger.warning("projects: cannot read %s (%s) — seeding from boot vault", path, exc)

    boot = _boot_project()
    return ProjectsResponse(projects=[boot], active_id=boot.id)


# ── Endpoint ──────────────────────────────────────────────────────────────────


@router.get(
    "/projects",
    response_model=ProjectsResponse,
    summary="List known projects (vaults) and the active one",
    description=(
        "Returns the multi-vault project registry (ADR-0067): all known project vaults + which "
        "is active. Seeded from the boot vault when the registry does not exist yet, so "
        "single-vault deploys always see exactly their one vault. Read-only; no DB, no Qdrant."
    ),
)
async def list_projects() -> ProjectsResponse:
    """GET /projects — the project registry snapshot (v1.5 P2 slice 1)."""
    return read_registry()
