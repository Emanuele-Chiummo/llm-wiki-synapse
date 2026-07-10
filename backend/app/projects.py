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
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
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


class OpenProjectRequest(BaseModel):
    """Body for POST /projects/open — register an existing vault folder."""

    path: str


class CreateProjectRequest(BaseModel):
    """Body for POST /projects — create + scaffold a new vault folder."""

    name: str
    path: str


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


def _slugify(name: str) -> str:
    """DB/filesystem-safe id base from a display name (lowercase alnum + hyphens)."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "vault"


def _unique_id(base: str, taken: set[str]) -> str:
    """Return *base*, or ``base-2``/``base-3``/… if it collides with an existing id."""
    if base not in taken:
        return base
    i = 2
    while f"{base}-{i}" in taken:
        i += 1
    return f"{base}-{i}"


def _resolved(path_str: str) -> str:
    return str(Path(path_str).expanduser().resolve())


# ── Endpoints ─────────────────────────────────────────────────────────────────


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


@router.post(
    "/projects/open",
    response_model=Project,
    summary="Register an existing vault folder as a project",
    description=(
        "Adds an existing vault directory to the registry (ADR-0067). Does NOT switch the active "
        "vault (that is POST /projects/{id}/activate). Idempotent: opening an already-registered "
        "path returns the existing entry (touching last_opened_at). Path must be absolute and an "
        "existing directory. Server-side path (self-hosted)."
    ),
    responses={
        400: {"description": "Path is not absolute."},
        404: {"description": "Path is not an existing directory."},
    },
)
async def open_project(body: OpenProjectRequest) -> Project:
    """POST /projects/open — register an existing vault folder (no activation)."""
    p = Path(body.path).expanduser()
    if not p.is_absolute():
        raise HTTPException(status_code=400, detail="Path must be absolute.")
    if not p.is_dir():
        raise HTTPException(status_code=404, detail=f"No such directory: {body.path}")

    resolved = _resolved(body.path)
    reg = read_registry()
    for existing in reg.projects:
        if _resolved(existing.path) == resolved:
            existing.last_opened_at = _now_iso()
            write_registry(reg)
            return existing

    pid = _unique_id(_slugify(p.name), {x.id for x in reg.projects})
    proj = Project(
        id=pid, name=p.name, path=resolved, created_at=_now_iso(), last_opened_at=_now_iso()
    )
    reg.projects.append(proj)
    write_registry(reg)
    logger.info("projects: opened %s (id=%s) at %s", p.name, pid, resolved)
    return proj


@router.post(
    "/projects",
    response_model=Project,
    status_code=201,
    summary="Create + scaffold a new project vault",
    description=(
        "Creates a new vault at *path* (scaffolds raw/, wiki/, purpose.md, schema.md via the "
        "shared bootstrap — idempotent) and registers it (ADR-0067). Does NOT switch the active "
        "vault. Path must be absolute; 409 if a project already exists at that path."
    ),
    responses={
        400: {"description": "Name missing or path not absolute."},
        409: {"description": "A project already exists at this path."},
        500: {"description": "Could not create the vault on disk."},
    },
)
async def create_project(body: CreateProjectRequest) -> Project:
    """POST /projects — create + scaffold a new vault (no activation)."""
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Name is required.")
    p = Path(body.path).expanduser()
    if not p.is_absolute():
        raise HTTPException(status_code=400, detail="Path must be absolute.")

    resolved = _resolved(body.path)
    reg = read_registry()
    if any(_resolved(x.path) == resolved for x in reg.projects):
        raise HTTPException(status_code=409, detail="A project already exists at this path.")

    # Scaffold the vault skeleton at the target path (idempotent, shared with boot bootstrap).
    from app.vault import bootstrap_vault_at  # noqa: PLC0415 - avoid import cycle at module load

    try:
        p.mkdir(parents=True, exist_ok=True)
        bootstrap_vault_at(p)
    except OSError as exc:
        logger.error("projects: could not scaffold vault at %s: %s", resolved, exc)
        raise HTTPException(status_code=500, detail=f"Could not create vault: {exc}") from exc

    pid = _unique_id(_slugify(body.name), {x.id for x in reg.projects})
    proj = Project(
        id=pid,
        name=body.name.strip(),
        path=resolved,
        created_at=_now_iso(),
        last_opened_at=_now_iso(),
    )
    reg.projects.append(proj)
    write_registry(reg)
    logger.info("projects: created %s (id=%s) at %s", proj.name, pid, resolved)
    return proj
