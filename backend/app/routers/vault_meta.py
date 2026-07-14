"""
WS-D8: Vault meta files endpoint (K1, I5) + WS-E: output-language read/write (ADR-0081).

GET /vault/meta?vault_id=<id>
  Returns the two fixed vault-root meta files (schema.md, purpose.md) read directly
  from disk. These files are written at bootstrap (vault.py) but are NEVER indexed as
  Page records in Postgres — they are vault-meta, not wiki content (AC-WS-D8-3/I1).

  Contract (exact — frontend builds against this):
    200 {"files": [
          {"name": "schema.md",  "path": "schema.md",  "title": "Schema",  "content": "..."},
          {"name": "purpose.md", "path": "purpose.md", "title": "Purpose", "content": "..."},
        ]}

PUT /vault/meta/{name}
  Overwrites schema.md or purpose.md; strict allow-list (no traversal).

GET /vault/meta/output-language                                       (WS-E, ADR-0081)
  Returns {"language": "<iso-639-1>" | null} — the AI output language stored in
  vault_state for the active vault.  NULL means pre-1.7.0 vault (auto-detect).

PUT /vault/meta/output-language                                       (WS-E, ADR-0081)
  Body: {"language": "<iso-639-1>" | null}.  Updates vault_state.output_language for
  the active vault.  Idempotent.  404 if no vault_state row exists yet.

Rules:
  - File endpoints read only two fixed filenames; no glob, no os.walk (I1).
  - Omits a file from the array if it does not exist on disk (AC-WS-D8-6).
  - No Postgres write for file endpoints, no Qdrant (AC-WS-D8-3/I1).
  - output-language endpoints read/write vault_state only (no disk file, no ingest side effect).
  - vault_id query param accepted for future multi-vault compatibility but currently
    ignored — path is resolved from settings.vault_root (single-vault model, §1 §5).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# The two fixed vault-root meta files (do NOT change to a glob/rglob — I1).
_META_FILES: tuple[tuple[str, str], ...] = (
    ("schema.md", "Schema"),
    ("purpose.md", "Purpose"),
)

# name → title, the AUTHORITATIVE allow-list for both read and write. A meta file is
# addressable ONLY if its exact filename is a key here — this is the path-traversal guard
# for PUT (no globs, no "..", no arbitrary paths ever reach the filesystem).
_META_TITLES: dict[str, str] = dict(_META_FILES)


class VaultMetaFile(BaseModel):
    """One vault meta file entry."""

    name: str
    path: str
    title: str
    content: str


class VaultMetaResponse(BaseModel):
    """Response body for GET /vault/meta."""

    files: list[VaultMetaFile]


class VaultMetaWriteRequest(BaseModel):
    """Request body for PUT /vault/meta/{name} — the full new file content."""

    content: str


@router.get(
    "/vault/meta",
    response_model=VaultMetaResponse,
    summary="Read vault meta files (schema.md, purpose.md)",
    description=(
        "Returns the two fixed vault-root meta files (schema.md, purpose.md) read "
        "directly from disk. Files are omitted from the array if absent. "
        "No Postgres write, no Qdrant, no ingest pipeline — read-only disk access only. "
        "[WS-D8, K1, I1, I5]"
    ),
)
async def get_vault_meta(
    vault_id: str = Query(default="default", description="Vault identifier (currently unused)."),
) -> VaultMetaResponse:
    """
    Read schema.md and purpose.md from the vault root and return their contents.

    The vault_id parameter is accepted for API compatibility with multi-vault
    future work but is currently ignored — path resolution always uses
    settings.vault_root (single-vault deployment model).

    Files that do not exist on disk are omitted from the response array (AC-WS-D8-6).
    """
    vault_root: Path = settings.vault_root
    result: list[VaultMetaFile] = []

    for filename, title in _META_FILES:
        file_path = vault_root / filename
        if not file_path.exists():
            logger.debug("vault/meta: %s not found at %s — omitting", filename, file_path)
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except OSError as exc:
            # Log and skip — do not crash the response if one file is unreadable.
            logger.warning("vault/meta: could not read %s: %s", file_path, exc)
            continue

        result.append(
            VaultMetaFile(
                name=filename,
                path=filename,
                title=title,
                content=content,
            )
        )

    return VaultMetaResponse(files=result)


# ── WS-E / ADR-0081: output-language read/write ───────────────────────────────
# IMPORTANT: These endpoints MUST be registered BEFORE PUT /vault/meta/{name}.
# FastAPI matches routes in registration order; a fixed path (/vault/meta/output-language)
# must appear before a parameterized path (/vault/meta/{name}) to avoid the parameterized
# route swallowing the literal "output-language" segment.


class OutputLanguageResponse(BaseModel):
    """Response for GET /vault/meta/output-language."""

    language: str | None
    """ISO-639-1 code (e.g. 'en', 'it') or null when not set (auto-detect, pre-1.7.0 vaults)."""


class OutputLanguageRequest(BaseModel):
    """Request body for PUT /vault/meta/output-language."""

    language: str | None
    """ISO-639-1 code to persist, or null to clear (revert to auto-detect)."""


@router.get(
    "/vault/meta/output-language",
    response_model=OutputLanguageResponse,
    summary="Read the AI output language for the active vault",
    description=(
        "Returns the ISO-639-1 output language stored in vault_state for the active vault "
        "(WS-E, ADR-0081). `null` means the vault predates 1.7.0 (auto-detect from source). "
        "404 if vault_state has no row yet (vault not yet fully seeded)."
    ),
    responses={404: {"description": "vault_state row not found for the active vault."}},
)
async def get_output_language() -> OutputLanguageResponse:
    """GET /vault/meta/output-language — read vault_state.output_language (WS-E, ADR-0081)."""
    # Deferred imports: DB access must not happen at module import time (test isolation,
    # startup order). Pattern mirrors _seed_vault_state_output_language in projects.py.
    from sqlalchemy import select  # noqa: PLC0415

    import app.db as _db  # noqa: PLC0415
    from app.models import VaultState  # noqa: PLC0415

    async with _db.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()

    if state is None:
        raise HTTPException(
            status_code=404,
            detail=f"No vault_state row found for vault_id={settings.vault_id!r}.",
        )
    return OutputLanguageResponse(language=state.output_language)


@router.put(
    "/vault/meta/output-language",
    response_model=OutputLanguageResponse,
    summary="Update the AI output language for the active vault",
    description=(
        "Persists the ISO-639-1 output language to vault_state for the active vault "
        "(WS-E, ADR-0081). Set to `null` to revert to auto-detect behaviour. "
        "Idempotent — safe to call multiple times. "
        "404 if vault_state has no row yet (activate the vault first)."
    ),
    responses={
        200: {"description": "Language updated; returns the new persisted value."},
        404: {"description": "vault_state row not found for the active vault."},
    },
)
async def put_output_language(body: OutputLanguageRequest) -> OutputLanguageResponse:
    """PUT /vault/meta/output-language — update vault_state.output_language (WS-E, ADR-0081)."""
    from sqlalchemy import select, update  # noqa: PLC0415

    import app.db as _db  # noqa: PLC0415
    from app.models import VaultState  # noqa: PLC0415

    async with _db.get_session() as session:
        # Existence check first (for 404) — column-level select avoids loading the full
        # ORM object; we only need to know whether a row exists.
        exists_row = await session.execute(
            select(VaultState.vault_id).where(VaultState.vault_id == settings.vault_id)
        )
        if exists_row.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=404,
                detail=f"No vault_state row found for vault_id={settings.vault_id!r}.",
            )

        # Core UPDATE (not ORM dirty-tracking) — avoids the ORM unit-of-work path that
        # would try to UPDATE via the UUID PK and trigger onupdate=func.now() on
        # updated_at (TIMESTAMP with timezone), both of which fail in SQLite tests.
        # Production (Postgres) also benefits from a single targeted UPDATE rather than
        # a full ORM flush of the whole row.
        await session.execute(
            update(VaultState)
            .where(VaultState.vault_id == settings.vault_id)
            .values(output_language=body.language)
        )
        # Commit is handled by get_session's context-manager on clean exit.

    logger.info(
        "vault/meta/output-language: set output_language=%r for vault_id=%r",
        body.language,
        settings.vault_id,
    )
    return OutputLanguageResponse(language=body.language)


# ── PUT /vault/meta/{name} — parameterized (MUST come after fixed paths above) ──


@router.put(
    "/vault/meta/{name}",
    response_model=VaultMetaFile,
    summary="Write a vault meta file (schema.md or purpose.md)",
    description=(
        "Overwrites one vault-root meta file with the supplied content. `name` MUST be exactly "
        "`schema.md` or `purpose.md` — any other value is 404 (this allow-list is the only "
        "path-traversal guard; no globs, no relative segments ever reach disk). Writes UTF-8 "
        "directly to the vault root; no Postgres/Qdrant/ingest side effects — these files are "
        "vault-meta, not wiki content (K1, I1, I5). v1.5 P1: makes purpose/schema editable "
        "in-app (ADR-0066)."
    ),
    responses={
        200: {"description": "File written; returns the persisted entry."},
        404: {"description": "name is not schema.md or purpose.md."},
    },
)
async def put_vault_meta(
    name: str,
    body: VaultMetaWriteRequest,
    vault_id: str = Query(default="default", description="Vault identifier (currently unused)."),
) -> VaultMetaFile:
    """
    Write schema.md or purpose.md to the vault root.

    Strict allow-list on ``name`` (must be a key of ``_META_TITLES``) is the sole gate — an
    unknown/traversal name is 404 before any filesystem access. Belt-and-braces: the resolved
    path must sit directly in the vault root.
    """
    title = _META_TITLES.get(name)
    if title is None:
        raise HTTPException(
            status_code=404,
            detail="Only 'schema.md' and 'purpose.md' are editable vault meta files.",
        )

    vault_root: Path = settings.vault_root
    file_path = vault_root / name
    # Defensive: the resolved target must be a direct child of the vault root.
    if file_path.resolve().parent != vault_root.resolve():
        raise HTTPException(status_code=400, detail="Invalid meta file path.")

    try:
        vault_root.mkdir(parents=True, exist_ok=True)
        file_path.write_text(body.content, encoding="utf-8")
    except OSError as exc:
        logger.error("vault/meta: could not write %s: %s", file_path, exc)
        raise HTTPException(status_code=500, detail=f"Could not write {name}.") from exc

    logger.info("vault/meta: wrote %s (%d chars)", name, len(body.content))
    return VaultMetaFile(name=name, path=name, title=title, content=body.content)
