"""
Pure project-registry read model (v1.5 P2, ADR-0082) — extracted from ``app/projects.py``.

Holds ONLY the read-only registry types + ``read_registry()``: filesystem state at
``$SYNAPSE_STATE_DIR/projects.json``, no DB, no dependency on ``app.main``. Split out so
lower-layer/routers modules (e.g. ``app.mcp.server``, per its layering contract — MCP must
not transitively import ``app.main``) can resolve a project id -> vault root without pulling
in ``app.projects``'s write-path helpers (``activate_project`` etc.), which DO lazily import
``app.main`` (graph-cache invalidation, vault_state reseed) and would otherwise create a
"routers/mcp -> app.main" edge that import-linter's layering contract forbids.

``app/projects.py`` re-exports everything here for backward compatibility — existing
importers of ``app.projects.read_registry`` / ``Project`` / ``ProjectsResponse`` are
unaffected.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)


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


def write_registry(reg: ProjectsResponse) -> None:
    """Persist the registry to ``$SYNAPSE_STATE_DIR/projects.json`` (creates the dir)."""
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg.model_dump(), indent=2) + "\n", encoding="utf-8")
