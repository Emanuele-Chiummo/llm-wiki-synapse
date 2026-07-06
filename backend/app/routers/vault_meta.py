"""
WS-D8: Vault meta files endpoint (K1, I5).

GET /vault/meta?vault_id=<id>

Returns the two fixed vault-root meta files (schema.md, purpose.md) read directly
from disk. These files are written at bootstrap (vault.py) but are NEVER indexed as
Page records in Postgres — they are vault-meta, not wiki content (AC-WS-D8-3/I1).

Contract (exact — frontend builds against this):
  200 {"files": [
        {"name": "schema.md",  "path": "schema.md",  "title": "Schema",  "content": "..."},
        {"name": "purpose.md", "path": "purpose.md", "title": "Purpose", "content": "..."},
      ]}

Rules:
  - Reads only two fixed filenames from the vault root; no glob, no os.walk (I1).
  - Omits a file from the array if it does not exist on disk (AC-WS-D8-6).
  - No Postgres write, no new table/column, no Qdrant (AC-WS-D8-3/I1).
  - vault_id query param accepted for future multi-vault compatibility but currently
    ignored — path is resolved from settings.vault_root (single-vault model, §1 §5).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# The two fixed vault-root meta files (do NOT change to a glob/rglob — I1).
_META_FILES: tuple[tuple[str, str], ...] = (
    ("schema.md", "Schema"),
    ("purpose.md", "Purpose"),
)


class VaultMetaFile(BaseModel):
    """One vault meta file entry."""

    name: str
    path: str
    title: str
    content: str


class VaultMetaResponse(BaseModel):
    """Response body for GET /vault/meta."""

    files: list[VaultMetaFile]


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
