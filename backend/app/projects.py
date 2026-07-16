"""
Multi-vault Project registry + endpoints (v1.5 P2, ADR-0082).

A **project** = a vault folder. Synapse serves ONE active vault at a time (Model A); this module
owns the persisted list of known projects and which one is active, so the frontend Project
Launcher can list / open / create / switch them (LLM Wiki parity).

State lives OUTSIDE any single vault, at ``$SYNAPSE_STATE_DIR/projects.json`` (default
``~/.synapse/projects.json``) — filesystem state, no DB, bounded reads/writes (mirrors the
clip/MCP token-config precedent). Each project's ``id`` doubles as its ``vault_id`` for the
already-vault-scoped Postgres/Qdrant rows.

This first slice is READ-ONLY: ``GET /projects``. Create / open / activate land in later P2
slices (ADR-0082 §5). The registry is seeded from the boot vault (``settings.vault_id`` /
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
    scenario: str | None = None
    """Optional llm_wiki scenario template id (WS-E, v1.7.0). Applied at scaffold time."""
    output_language: str | None = None
    """ISO-639-1 output language (e.g. 'en', 'it'). Persisted to vault_state (not disk)."""


class ActivateResponse(BaseModel):
    """Response for POST /projects/{id}/activate — the now-active project + reload epoch."""

    project: Project
    active_vault_epoch: int


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


# ── Runtime active-vault switch (ADR-0082 §2c) ────────────────────────────────
# Bumped on every successful activate; the frontend reads it to hard-reload its stores
# against the new active vault.
_active_vault_epoch: int = 0


def active_vault_epoch() -> int:
    """Current epoch — increments each time the active vault is switched."""
    return _active_vault_epoch


async def _apply_active_vault(project: Project) -> None:
    """
    Re-point the running service at *project*'s vault (best-effort, each step guarded).

    Order (ADR-0082 §2c): mutate runtime config → restart the watcher on the new root →
    drop the graph cache (re-created lazily for the new vault_id) → seed vault_state → bump the
    epoch. A failing step is logged and skipped, never aborting the switch — the registry has
    already recorded the new active_id, so the service must follow as far as it can.
    Deferred imports avoid an import cycle with app.main (which registers this router).
    """
    global _active_vault_epoch  # noqa: PLW0603

    settings.vault_id = project.id
    settings.vault_path = project.path

    try:
        import asyncio  # noqa: PLC0415

        from app.watcher import start_watcher, stop_watcher  # noqa: PLC0415

        stop_watcher()
        start_watcher(asyncio.get_running_loop())
    except Exception:  # noqa: BLE001
        logger.exception("projects: watcher restart failed during activate")

    try:
        from app import main as _m  # noqa: PLC0415

        if _m._graph_cache is not None:
            _m._graph_cache.stop_background_loop()
        _m._graph_cache = None  # re-created lazily for the new vault_id by GET /graph
    except Exception:  # noqa: BLE001
        logger.exception("projects: graph-cache invalidation failed during activate")

    try:
        from app.main import _seed_vault_state  # noqa: PLC0415

        await _seed_vault_state()
    except Exception:  # noqa: BLE001
        logger.exception("projects: vault_state seed failed during activate")

    _active_vault_epoch += 1
    logger.info(
        "projects: activated %s (id=%s) at %s — epoch %d",
        project.name,
        project.id,
        project.path,
        _active_vault_epoch,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/projects",
    response_model=ProjectsResponse,
    summary="List known projects (vaults) and the active one",
    description=(
        "Returns the multi-vault project registry (ADR-0082): all known project vaults + which "
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
        "Adds an existing vault directory to the registry (ADR-0082). Does NOT switch the active "
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
        "shared bootstrap — idempotent) and registers it (ADR-0082). Does NOT switch the active "
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

    # Validate scenario id if given (400 on unknown — WS-E, v1.7.0).
    if body.scenario is not None:
        from app.scenarios_data import SCENARIO_INDEX as _sidx  # noqa: PLC0415

        if body.scenario not in _sidx:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown scenario {body.scenario!r}. " f"Valid ids: {sorted(_sidx)}",
            )

    resolved = _resolved(body.path)
    reg = read_registry()
    if any(_resolved(x.path) == resolved for x in reg.projects):
        raise HTTPException(status_code=409, detail="A project already exists at this path.")

    # Scaffold the vault skeleton at the target path (idempotent, shared with boot bootstrap).
    # scenario_id and output_language are passed through; vault.py applies the scenario
    # to schema.md/purpose.md/extra_dirs; output_language is accepted but NOT written to
    # disk by vault.py — we persist it to vault_state below.
    from app.vault import bootstrap_vault_at  # noqa: PLC0415 - avoid import cycle at module load

    try:
        p.mkdir(parents=True, exist_ok=True)
        bootstrap_vault_at(
            p,
            scenario_id=body.scenario,
            output_language=body.output_language,
        )
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
    logger.info(
        "projects: created %s (id=%s) at %s (scenario=%r, output_language=%r)",
        proj.name,
        pid,
        resolved,
        body.scenario,
        body.output_language,
    )

    # Persist output_language to vault_state for the new vault (WS-E, ADR-0081).
    # Best-effort: if the DB is not available (e.g. in filesystem-only tests) we log
    # a warning and continue — the vault and registry are already committed.
    if body.output_language is not None:
        await _seed_vault_state_output_language(pid, body.output_language)

    return proj


async def _seed_vault_state_output_language(vault_id: str, output_language: str) -> None:
    """
    Best-effort: upsert a vault_state row for *vault_id* with *output_language* set.

    Called from create_project immediately after registry commit. Uses a deferred import
    so that filesystem-only tests that don't set up a DB never fail here — any exception
    is caught and logged as a warning (the vault scaffold is already complete).

    Mirrors the _seed_vault_state pattern in main.py (ADR-0005) but targets a different
    vault_id (the newly-created project, not settings.vault_id).
    """
    try:
        from datetime import UTC, datetime  # noqa: PLC0415

        from sqlalchemy import select as _sa_select  # noqa: PLC0415

        import app.db as _db  # noqa: PLC0415
        from app.models import VaultState  # noqa: PLC0415

        async with _db.get_session() as session:
            row = await session.execute(
                _sa_select(VaultState).where(VaultState.vault_id == vault_id)
            )
            vs = row.scalar_one_or_none()
            if vs is None:
                session.add(
                    VaultState(
                        vault_id=vault_id,
                        data_version=0,
                        output_language=output_language,
                        updated_at=datetime.now(UTC),
                    )
                )
            else:
                vs.output_language = output_language
        logger.info(
            "projects: seeded vault_state.output_language=%r for vault_id=%r",
            output_language,
            vault_id,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "projects: could not persist output_language to vault_state for %r "
            "(DB may not be available — not fatal)",
            vault_id,
        )


@router.post(
    "/projects/{project_id}/activate",
    response_model=ActivateResponse,
    summary="Switch the active vault to this project",
    description=(
        "Makes *project_id* the active vault at runtime (ADR-0082 §2c): records it in the "
        "registry, re-points settings, restarts the watcher on the new root, invalidates the "
        "graph cache, seeds vault_state, and bumps active_vault_epoch (the frontend reloads its "
        "stores on epoch change). Runtime side effects are best-effort + logged. 404 if unknown."
    ),
    responses={404: {"description": "No such project id."}},
)
async def activate_project(project_id: str) -> ActivateResponse:
    """POST /projects/{id}/activate — switch the active vault (v1.5 P2 slice 3)."""
    reg = read_registry()
    proj = next((p for p in reg.projects if p.id == project_id), None)
    if proj is None:
        raise HTTPException(status_code=404, detail=f"No such project: {project_id}")

    proj.last_opened_at = _now_iso()
    reg.active_id = proj.id
    write_registry(reg)

    await _apply_active_vault(proj)
    return ActivateResponse(project=proj, active_vault_epoch=active_vault_epoch())
