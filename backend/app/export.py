"""
Vault export / backup endpoints (R8-4, AC-R8-4-1..AC-R8-4-5).

Endpoints:
  GET /export          — streaming ZIP of the vault directory
                         (raw/ + wiki/ + purpose.md + schema.md + .obsidian/*.json).
                         Bounded: 413 if uncompressed total exceeds EXPORT_MAX_BYTES.
                         Rate-limited: 429 if another export is already running
                         for the same vault_id (asyncio.Lock per vault, AC-R8-4-5).
  GET /export/data.json — JSON dump of live pages, links, edges, ingest_runs summary,
                          review_items summary, generated_at, data_version.
                          All records for the current vault_id; no content bodies.
  GET /export/full      — 1.9.1 W4 (SEC-OPS-2). Everything in /export/data.json PLUS
                          conversations + messages, provider_config, and vault_state —
                          the tables /export/data.json and the vault ZIP both miss today.
                          Secrets stay SECRET: `provider_config.api_key_encrypted` is
                          exported AS-IS (Fernet ciphertext, base64-encoded for JSON
                          transport) — NEVER decrypted here. A restored dump is only
                          usable with the SAME SYNAPSE_SECRET_KEY that encrypted it;
                          rotating the key first orphans those ciphertexts (same
                          constraint as the live app — app/secrets_crypto.py).

Invariants honoured:
  I1  — read-only; no file mutations, no index re-scan.
  I2  — does NOT trigger a graph recompute.
  I6  — zero InferenceProvider calls.
  I7  — hard 413 cap (EXPORT_MAX_BYTES = 500 MB uncompressed).

Config:
  EXPORT_MAX_BYTES   — default 500 * 1024 * 1024 (500 MB). Defined here as a module
                       constant (do NOT read from config.py — another agent owns it).
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select

from app.config import settings
from app.db import get_session
from app.models import (
    ChatMessage,
    Conversation,
    Edge,
    IngestRun,
    Link,
    Page,
    ProviderConfig,
    ReviewItem,
    VaultState,
)

logger = logging.getLogger(__name__)

# ── Constants (I7 bounds — defined here, not in config.py per sprint note) ───
EXPORT_MAX_BYTES: int = 500 * 1024 * 1024  # 500 MB uncompressed cap (AC-R8-4-1)

# ── Per-vault export lock (AC-R8-4-5) ────────────────────────────────────────
# One asyncio.Lock per vault_id.  A second concurrent request returns 429.
_export_locks: dict[str, asyncio.Lock] = {}


def _get_export_lock(vault_id: str) -> asyncio.Lock:
    """Return (creating if absent) the asyncio.Lock for the given vault_id."""
    if vault_id not in _export_locks:
        _export_locks[vault_id] = asyncio.Lock()
    return _export_locks[vault_id]


# ── Router ────────────────────────────────────────────────────────────────────
router = APIRouter(tags=["export"])


# ── Helpers ───────────────────────────────────────────────────────────────────


def _vault_entries(vault_root: Path) -> list[tuple[Path, str]]:
    """
    Walk the vault directory and return (absolute_path, archive_name) pairs
    for all files that should be included in the export ZIP.

    Inclusion rules (AC-R8-4-1):
      - raw/         — full sub-tree (all files)
      - wiki/        — all files EXCEPT non-JSON files inside any .obsidian/ directory
                       (binary caches excluded; .obsidian/*.json config files included)
      - purpose.md   — if present at vault root
      - schema.md    — if present at vault root

    The Obsidian config lives at wiki/.obsidian/ (I5/K7 — wiki/ is the Obsidian vault).
    Non-JSON files in any .obsidian/ directory are excluded (binary workspace caches).
    JSON files in .obsidian/ are kept (app.json, appearance.json, plugins/, etc.).
    """
    entries: list[tuple[Path, str]] = []

    def _add_raw_tree(base: Path, arc_prefix: str) -> None:
        """Add all files in a tree to entries."""
        if not base.exists():
            return
        for p in base.rglob("*"):
            if p.is_file():
                arc = arc_prefix + "/" + p.relative_to(base).as_posix()
                entries.append((p, arc))

    def _add_wiki_tree(base: Path, arc_prefix: str) -> None:
        """Add wiki/ tree, skipping non-JSON files inside .obsidian/ directories."""
        if not base.exists():
            return
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            # Check if this file is inside any .obsidian/ directory
            rel = p.relative_to(base)
            parts = rel.parts
            in_obsidian = any(part == ".obsidian" for part in parts)
            if in_obsidian and p.suffix.lower() != ".json":
                # Skip non-JSON files in .obsidian/ (binary caches)
                continue
            arc = arc_prefix + "/" + rel.as_posix()
            entries.append((p, arc))

    # raw/ sub-tree (all files)
    _add_raw_tree(vault_root / "raw", "raw")
    # wiki/ sub-tree (JSON-only in .obsidian/)
    _add_wiki_tree(vault_root / "wiki", "wiki")
    # vault-root flat files
    for name in ("purpose.md", "schema.md"):
        p = vault_root / name
        if p.is_file():
            entries.append((p, name))

    return entries


def _check_size_cap(entries: list[tuple[Path, str]]) -> int:
    """
    Sum uncompressed file sizes.  Returns total bytes.
    Raises HTTPException 413 if the total exceeds EXPORT_MAX_BYTES.
    """
    total = 0
    for path, _ in entries:
        try:
            total += path.stat().st_size
        except OSError:
            pass  # skip unreadable files — they'll also be skipped during zip
        if total > EXPORT_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Vault export exceeds the maximum allowed size of "
                    f"{EXPORT_MAX_BYTES // (1024 * 1024)} MB. "
                    "Remove large files from raw/sources/ before exporting, "
                    "or raise EXPORT_MAX_BYTES via env var."
                ),
            )
    return total


def _build_zip_bytes(entries: list[tuple[Path, str]]) -> bytes:
    """
    Build the ZIP into a SpooledTemporaryFile-backed BytesIO and return the bytes.
    Uses ZIP_DEFLATED compression to keep payload size manageable.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, arc_name in entries:
            try:
                zf.write(path, arcname=arc_name)
            except OSError as exc:
                logger.warning("export: skipping unreadable file %s — %s", path, exc)
    buf.seek(0)
    return buf.read()


# ── GET /export ───────────────────────────────────────────────────────────────


@router.get(
    "/export",
    summary="Download vault as ZIP",
    description=(
        "Stream a ZIP archive of the vault directory "
        "(raw/ + wiki/ + purpose.md + schema.md + .obsidian/*.json). "
        "Returns 413 if the uncompressed total exceeds EXPORT_MAX_BYTES (500 MB). "
        "Returns 429 if another export is already running for this vault."
    ),
    response_class=StreamingResponse,
    responses={
        200: {"description": "ZIP archive stream", "content": {"application/zip": {}}},
        413: {"description": "Vault exceeds EXPORT_MAX_BYTES size cap"},
        429: {"description": "Another export is already running for this vault"},
    },
)
async def export_vault_zip() -> StreamingResponse:
    """
    GET /export — streaming ZIP of the vault directory (AC-R8-4-1).

    Steps:
      1. Acquire the per-vault asyncio.Lock (non-blocking → 429 if held).
      2. Walk vault_root and collect file entries.
      3. Sum uncompressed sizes — 413 if cap exceeded.
      4. Build ZIP in-memory using zipfile + BytesIO.
      5. Stream the bytes with Content-Disposition: attachment.
    """
    vault_id = settings.vault_id
    vault_root = settings.vault_root
    lock = _get_export_lock(vault_id)

    if lock.locked():
        raise HTTPException(
            status_code=429,
            detail="An export is already running for this vault. Try again shortly.",
        )

    async with lock:
        # Collect entries (synchronous filesystem walk — acceptable; no heavy I/O)
        entries = _vault_entries(vault_root)

        # Size cap check (AC-R8-4-1, I7)
        _check_size_cap(entries)

        # Build ZIP in memory (sync — file I/O is bounded by cap above)
        zip_bytes = await asyncio.get_event_loop().run_in_executor(None, _build_zip_bytes, entries)

        date_str = datetime.now(tz=UTC).strftime("%Y%m%d")
        filename = f"synapse-vault-{vault_id}-{date_str}.zip"

        logger.info(
            "export: vault=%s files=%d zip_size=%d",
            vault_id,
            len(entries),
            len(zip_bytes),
        )

        return StreamingResponse(
            content=iter([zip_bytes]),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length": str(len(zip_bytes)),
            },
        )


# ── GET /export/data.json ─────────────────────────────────────────────────────


@router.get(
    "/export/data.json",
    summary="Export vault metadata as JSON",
    description=(
        "Return a JSON dump of live pages (no content bodies), links, edges, "
        "ingest_runs summary, review_items summary, data_version, and generated_at. "
        "All records for the current vault_id."
    ),
    response_class=JSONResponse,
    responses={
        200: {"description": "JSON metadata export"},
    },
)
async def export_data_json() -> JSONResponse:
    """
    GET /export/data.json — JSON dump of database metadata (AC-R8-4-2).

    Returns:
      pages              — list of live pages (no content bodies)
      links              — all link rows for the vault
      edges              — all edge rows for the vault
      runs               — ingest_runs summary (id, status, cost, started_at)
      review_items       — review_items summary (id, item_type, status, created_at)
      exported_at        — ISO-8601 timestamp (UTC)
      data_version       — current vault data_version
    """
    vault_id = settings.vault_id

    async with get_session() as session:
        # ── data_version ──────────────────────────────────────────────────────
        vs_row = await session.execute(select(VaultState).where(VaultState.vault_id == vault_id))
        vs = vs_row.scalar_one_or_none()
        data_version = vs.data_version if vs else 0

        # ── pages (live only, no content bodies) ──────────────────────────────
        pages_result = await session.execute(
            select(Page).where(
                Page.vault_id == vault_id,
                Page.deleted_at.is_(None),
            )
        )
        pages_rows = pages_result.scalars().all()
        pages_out: list[dict[str, Any]] = [
            {
                "id": str(p.id),
                "file_path": p.file_path,
                "title": p.title,
                "type": p.page_type,
                "sources": p.sources,
                "tags": p.tags,
                "content_hash": p.content_hash,
                "x": p.x,
                "y": p.y,
                "community": p.community,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in pages_rows
        ]

        # ── links ─────────────────────────────────────────────────────────────
        links_result = await session.execute(
            select(Link).where(
                Link.source_page_id.in_(
                    select(Page.id).where(
                        Page.vault_id == vault_id,
                        Page.deleted_at.is_(None),
                    )
                )
            )
        )
        links_rows = links_result.scalars().all()
        links_out: list[dict[str, Any]] = [
            {
                "id": str(lnk.id),
                "source_page_id": str(lnk.source_page_id),
                "target_title": lnk.target_title,
                "target_page_id": str(lnk.target_page_id) if lnk.target_page_id else None,
                "alias": lnk.alias,
                "dangling": lnk.dangling,
                "created_at": lnk.created_at.isoformat() if lnk.created_at else None,
            }
            for lnk in links_rows
        ]

        # ── edges ─────────────────────────────────────────────────────────────
        edges_result = await session.execute(select(Edge).where(Edge.vault_id == vault_id))
        edges_rows = edges_result.scalars().all()
        edges_out: list[dict[str, Any]] = [
            {
                "id": str(e.id),
                "source_page_id": str(e.source_page_id),
                "target_page_id": str(e.target_page_id),
                "weight": e.weight,
                "kind": e.kind,
                "signals": e.signals,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in edges_rows
        ]

        # ── ingest_runs summary ───────────────────────────────────────────────
        runs_result = await session.execute(select(IngestRun).where(IngestRun.vault_id == vault_id))
        runs_rows = runs_result.scalars().all()
        runs_out: list[dict[str, Any]] = [
            {
                "id": str(r.id),
                "status": r.status,
                "provider_name": r.provider_name,
                "model_id": r.model_id,
                "total_cost_usd": float(r.total_cost_usd),
                "pages_created": r.pages_created,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs_rows
        ]

        # ── review_items summary ──────────────────────────────────────────────
        review_result = await session.execute(
            select(ReviewItem).where(ReviewItem.vault_id == vault_id)
        )
        review_rows = review_result.scalars().all()
        review_out: list[dict[str, Any]] = [
            {
                "id": str(ri.id),
                "item_type": ri.item_type,
                "status": ri.status,
                "proposed_title": ri.proposed_title,
                "created_at": ri.created_at.isoformat() if ri.created_at else None,
            }
            for ri in review_rows
        ]

    payload: dict[str, Any] = {
        "pages": pages_out,
        "links": links_out,
        "edges": edges_out,
        "runs": runs_out,
        "review_items": review_out,
        "exported_at": datetime.now(tz=UTC).isoformat(),
        "data_version": data_version,
    }

    logger.info(
        "export/data.json: vault=%s pages=%d links=%d edges=%d",
        vault_id,
        len(pages_out),
        len(links_out),
        len(edges_out),
    )

    return JSONResponse(content=payload)


# ── GET /export/full ──────────────────────────────────────────────────────────
# 1.9.1 W4 (SEC-OPS-2): /export/data.json omits conversations, provider_config, and
# vault_state — a restore from it alone loses chat history, provider credentials/config,
# and the data_version/MCP-token state. /export/full adds all three, ciphertext untouched.


@router.get(
    "/export/full",
    summary="Extended JSON export — data.json PLUS conversations/provider_config/vault_state",
    description=(
        "Everything GET /export/data.json returns, PLUS: conversations (with their "
        "messages), provider_config rows, and the vault_state row. Secrets are NEVER "
        "decrypted: provider_config.api_key_encrypted is emitted as base64-encoded Fernet "
        "ciphertext — restoring it requires the SAME SYNAPSE_SECRET_KEY that encrypted it. "
        "Read-only; no file mutations, no provider calls, no graph recompute (I1/I2/I6)."
    ),
    response_class=JSONResponse,
    responses={
        200: {"description": "Extended JSON metadata export"},
    },
)
async def export_full_json() -> JSONResponse:
    """
    GET /export/full — data.json + conversations/messages + provider_config + vault_state
    (1.9.1 W4, SEC-OPS-2).

    Encrypted provider API keys travel as base64 ciphertext — this endpoint has no access
    to SYNAPSE_SECRET_KEY-decrypted plaintext anywhere in its code path (app/secrets_crypto.py
    is never imported here), so there is nothing to accidentally leak.
    """
    vault_id = settings.vault_id

    async with get_session() as session:
        # ── data_version / vault_state (full row — includes MCP token hash, toggles) ──
        vs_row = await session.execute(select(VaultState).where(VaultState.vault_id == vault_id))
        vs = vs_row.scalar_one_or_none()
        data_version = vs.data_version if vs else 0
        vault_state_out: dict[str, Any] | None = (
            None
            if vs is None
            else {
                "id": str(vs.id),
                "vault_id": vs.vault_id,
                "data_version": vs.data_version,
                "remote_mcp_enabled": vs.remote_mcp_enabled,
                "mcp_access_token_hash": vs.mcp_access_token_hash,
                "mcp_allow_without_token": vs.mcp_allow_without_token,
            }
        )

        # ── pages (live only, no content bodies) ──────────────────────────────
        pages_result = await session.execute(
            select(Page).where(
                Page.vault_id == vault_id,
                Page.deleted_at.is_(None),
            )
        )
        pages_rows = pages_result.scalars().all()
        pages_out: list[dict[str, Any]] = [
            {
                "id": str(p.id),
                "file_path": p.file_path,
                "title": p.title,
                "type": p.page_type,
                "sources": p.sources,
                "tags": p.tags,
                "content_hash": p.content_hash,
                "x": p.x,
                "y": p.y,
                "community": p.community,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in pages_rows
        ]

        # ── links ─────────────────────────────────────────────────────────────
        links_result = await session.execute(
            select(Link).where(
                Link.source_page_id.in_(
                    select(Page.id).where(
                        Page.vault_id == vault_id,
                        Page.deleted_at.is_(None),
                    )
                )
            )
        )
        links_rows = links_result.scalars().all()
        links_out = [
            {
                "id": str(lnk.id),
                "source_page_id": str(lnk.source_page_id),
                "target_title": lnk.target_title,
                "target_page_id": str(lnk.target_page_id) if lnk.target_page_id else None,
                "alias": lnk.alias,
                "dangling": lnk.dangling,
                "created_at": lnk.created_at.isoformat() if lnk.created_at else None,
            }
            for lnk in links_rows
        ]

        # ── edges ─────────────────────────────────────────────────────────────
        edges_result = await session.execute(select(Edge).where(Edge.vault_id == vault_id))
        edges_rows = edges_result.scalars().all()
        edges_out = [
            {
                "id": str(e.id),
                "source_page_id": str(e.source_page_id),
                "target_page_id": str(e.target_page_id),
                "weight": e.weight,
                "kind": e.kind,
                "signals": e.signals,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in edges_rows
        ]

        # ── ingest_runs summary ───────────────────────────────────────────────
        runs_result = await session.execute(select(IngestRun).where(IngestRun.vault_id == vault_id))
        runs_rows = runs_result.scalars().all()
        runs_out = [
            {
                "id": str(r.id),
                "status": r.status,
                "provider_name": r.provider_name,
                "model_id": r.model_id,
                "total_cost_usd": float(r.total_cost_usd),
                "pages_created": r.pages_created,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            }
            for r in runs_rows
        ]

        # ── review_items summary ──────────────────────────────────────────────
        review_result = await session.execute(
            select(ReviewItem).where(ReviewItem.vault_id == vault_id)
        )
        review_rows = review_result.scalars().all()
        review_out = [
            {
                "id": str(ri.id),
                "item_type": ri.item_type,
                "status": ri.status,
                "proposed_title": ri.proposed_title,
                "created_at": ri.created_at.isoformat() if ri.created_at else None,
            }
            for ri in review_rows
        ]

        # ── conversations + messages (F6) — NEW in /export/full ────────────────
        conv_result = await session.execute(
            select(Conversation).where(Conversation.vault_id == vault_id)
        )
        conv_rows = conv_result.scalars().all()
        conversations_out: list[dict[str, Any]] = []
        for c in conv_rows:
            msgs_result = await session.execute(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == c.id)
                .order_by(ChatMessage.created_at.asc())
            )
            msgs_rows = msgs_result.scalars().all()
            conversations_out.append(
                {
                    "id": str(c.id),
                    "title": c.title,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "updated_at": c.updated_at.isoformat() if c.updated_at else None,
                    "deleted_at": c.deleted_at.isoformat() if c.deleted_at else None,
                    "messages": [
                        {
                            "id": str(m.id),
                            "role": m.role,
                            "content": m.content,
                            "citations": m.citations,
                            "images": m.images,
                            "provider_type": m.provider_type,
                            "model_id": m.model_id,
                            "input_tokens": m.input_tokens,
                            "output_tokens": m.output_tokens,
                            "total_cost_usd": float(m.total_cost_usd),
                            "created_at": m.created_at.isoformat() if m.created_at else None,
                        }
                        for m in msgs_rows
                    ],
                }
            )

        # ── provider_config (F17) — NEW in /export/full. Ciphertext, NEVER decrypted ──
        pc_result = await session.execute(
            select(ProviderConfig).where(
                (ProviderConfig.vault_id == vault_id) | (ProviderConfig.scope == "global")
            )
        )
        pc_rows = pc_result.scalars().all()
        provider_config_out = [
            {
                "id": str(pc.id),
                "scope": pc.scope,
                "operation": pc.operation,
                "vault_id": pc.vault_id,
                "provider_type": pc.provider_type,
                "model_id": pc.model_id,
                "base_url": pc.base_url,
                # SECRET AT REST: exported AS CIPHERTEXT, base64-encoded for JSON transport.
                # Restoring requires the SAME SYNAPSE_SECRET_KEY that encrypted it
                # (app/secrets_crypto.py) — never decrypted anywhere in this code path.
                "api_key_encrypted_b64": (
                    base64.b64encode(pc.api_key_encrypted).decode("ascii")
                    if pc.api_key_encrypted
                    else None
                ),
                "reasoning_effort": pc.reasoning_effort,
                "max_iter": pc.max_iter,
                "token_budget": pc.token_budget,
                "is_fallback": pc.is_fallback,
                "created_at": pc.created_at.isoformat() if pc.created_at else None,
                "updated_at": pc.updated_at.isoformat() if pc.updated_at else None,
            }
            for pc in pc_rows
        ]

    payload: dict[str, Any] = {
        "pages": pages_out,
        "links": links_out,
        "edges": edges_out,
        "runs": runs_out,
        "review_items": review_out,
        "conversations": conversations_out,
        "provider_config": provider_config_out,
        "vault_state": vault_state_out,
        "exported_at": datetime.now(tz=UTC).isoformat(),
        "data_version": data_version,
        "secrets_note": (
            "provider_config[].api_key_encrypted_b64 is Fernet ciphertext (base64-encoded), "
            "NOT plaintext. Restoring these rows requires the SAME SYNAPSE_SECRET_KEY that "
            "encrypted them on the source deployment — a different/rotated key renders them "
            "unusable (they must be re-entered via Settings instead)."
        ),
    }

    logger.info(
        "export/full: vault=%s pages=%d links=%d edges=%d conversations=%d provider_config=%d",
        vault_id,
        len(pages_out),
        len(links_out),
        len(edges_out),
        len(conversations_out),
        len(provider_config_out),
    )

    return JSONResponse(content=payload)
