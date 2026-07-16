"""
Thin Qdrant wrapper — ensure_collection, upsert_point, delete_point (ADR-0002).

Collection: synapse_pages (configurable via Settings.qdrant_collection).
Distance:   Cosine.
Vector size: EMBEDDING_DIM (ADR-0004 — from Settings, never hardcoded).
Point id:   == pages.id UUID (ADR-0002 — stable join key).
Payload:    {file_path, title, type, vault_id} (AC-QD-2, BE-PERF-3).

BE-PERF-3 (also a correctness/security fix, not just performance): the payload MUST carry
``vault_id`` so that Phase-1 vector search (``app.rag.retrieval._phase1_vector_search``) can
scope its ``query_points`` call with a ``Filter`` on ``vault_id``. Without this, a multi-vault
deployment's dense top-k is diluted by — and can surface — points from OTHER vaults, which is
both a recall regression and a cross-tenant data leak (mirrors the graph-resolver cross-vault
fix pattern already applied elsewhere in this codebase, e.g. ``app.graph.engine`` /
``app.ops.cascade_delete`` scoping all reads by ``vault_id``). A payload index on ``vault_id``
is created so the filtered query stays O(log n) rather than a full collection scan.

Points written BEFORE this fix landed lack ``vault_id`` in their payload. Callers that rely on
vault-scoped filtering must tolerate zero-hit results for those legacy points until a backfill
is run — see ``backend/scripts/backfill_qdrant_vault_id.py``.

Write ordering (ADR-0002): Postgres commits first; then this module is called.
If this call fails, Postgres row is the source of truth; a future reconciliation
pass (out of v0.1 scope) can repair the derived index.
"""

from __future__ import annotations

import logging
import uuid

from qdrant_client.http import models as qmodels

from app.config import settings
from qdrant_client import AsyncQdrantClient

logger = logging.getLogger(__name__)

# ── Client singleton ──────────────────────────────────────────────────────────

_client: AsyncQdrantClient | None = None


def get_qdrant_client() -> AsyncQdrantClient:
    """Return (or create) the module-level Qdrant async client."""
    global _client  # noqa: PLW0603
    if _client is None:
        # OPS-DATA-1: optional API key for Qdrant authentication
        _client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )
    return _client


def set_qdrant_client(client: AsyncQdrantClient) -> None:
    """Override the Qdrant client (test isolation)."""
    global _client  # noqa: PLW0603
    _client = client


# ── Collection management ─────────────────────────────────────────────────────


async def ensure_collection(dim: int | None = None) -> None:
    """
    Create the synapse_pages collection if it does not exist.

    If the collection already exists with a *different* vector dimension, log an
    error and raise RuntimeError — the service must not silently recreate a
    collection with wrong dimensions, because that would destroy existing vectors
    (ADR-0004).

    Args:
        dim: Override vector dimension (for testing); defaults to Settings.embedding_dim.
    """
    vector_size = dim if dim is not None else settings.embedding_dim
    collection_name = settings.qdrant_collection
    client = get_qdrant_client()

    existing = await client.get_collections()
    collection_names = {c.name for c in existing.collections}

    if collection_name not in collection_names:
        logger.info(
            "Creating Qdrant collection %r with dim=%d distance=Cosine",
            collection_name,
            vector_size,
        )
        await client.create_collection(
            collection_name=collection_name,
            vectors_config=qmodels.VectorParams(
                size=vector_size,
                distance=qmodels.Distance.COSINE,
            ),
        )
        await _ensure_vault_id_payload_index(client, collection_name)
        return

    # Collection exists — validate dimension (ADR-0004 fail-fast guard)
    info = await client.get_collection(collection_name)
    existing_size: int | None = None
    vectors_cfg = info.config.params.vectors
    if isinstance(vectors_cfg, qmodels.VectorParams):
        existing_size = vectors_cfg.size
    elif isinstance(vectors_cfg, dict):
        # Named vectors; pick the default unnamed entry if present
        unnamed = vectors_cfg.get("")
        if unnamed is not None:
            existing_size = getattr(unnamed, "size", None)

    if existing_size is not None and existing_size != vector_size:
        msg = (
            f"Qdrant collection {collection_name!r} already exists with dimension "
            f"{existing_size}, but EMBEDDING_DIM={vector_size}. "
            "This is a deliberate migration gate — resolve manually (ADR-0004)."
        )
        logger.error(msg)
        raise RuntimeError(msg)

    logger.info(
        "Qdrant collection %r already exists with dim=%s — reusing.",
        collection_name,
        existing_size,
    )
    # BE-PERF-3: idempotent — also ensures the payload index on pre-existing collections
    # created before this fix (create_payload_index is a no-op if the index already exists).
    await _ensure_vault_id_payload_index(client, collection_name)


async def _ensure_vault_id_payload_index(client: AsyncQdrantClient, collection_name: str) -> None:
    """
    Create a keyword payload index on ``vault_id`` (BE-PERF-3), so vault-scoped
    ``Filter(must=[FieldCondition(key="vault_id", ...)])`` queries stay indexed rather than
    falling back to a full collection scan.

    Best-effort: some Qdrant client versions / test doubles may not implement
    ``create_payload_index``; any failure is logged and swallowed so collection setup never
    aborts on this optimization (the filter itself still works without the index).
    """
    try:
        await client.create_payload_index(
            collection_name=collection_name,
            field_name="vault_id",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
    except Exception as exc:  # noqa: BLE001 - best-effort index creation, never fatal
        logger.warning(
            "Qdrant create_payload_index(vault_id) skipped for collection %r: %s",
            collection_name,
            exc,
        )


# ── Point operations ──────────────────────────────────────────────────────────


async def upsert_point(
    *,
    page_id: uuid.UUID,
    vector: list[float],
    file_path: str,
    title: str | None,
    page_type: str | None,
    vault_id: str,
) -> None:
    """
    Upsert a Qdrant point for *page_id*.

    Uses the UUID as the point id (ADR-0002).
    Payload = {file_path, title, type, vault_id} (AC-QD-2, BE-PERF-3).

    ``vault_id`` MUST be supplied by every caller so Phase-1 vector search can scope its
    query to the active vault (BE-PERF-3 — a correctness/security fix: without it, points
    from other vaults could be returned and even cited in chat/search).
    """
    client = get_qdrant_client()
    await client.upsert(
        collection_name=settings.qdrant_collection,
        points=[
            qmodels.PointStruct(
                id=str(page_id),
                vector=vector,
                payload={
                    "file_path": file_path,
                    "title": title,
                    "type": page_type,
                    "vault_id": vault_id,
                },
            )
        ],
    )
    logger.debug(
        "Qdrant upsert: point_id=%s file_path=%r vault_id=%r", page_id, file_path, vault_id
    )


async def delete_point(page_id: uuid.UUID) -> None:
    """
    Hard-delete the Qdrant point for *page_id* (AC-WATCH-4, AC-QD-3, ADR-0005).

    Soft-deleted pages must not surface in vector search (ADR-0002).
    """
    client = get_qdrant_client()
    await client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=qmodels.PointIdsList(points=[str(page_id)]),
    )
    logger.debug("Qdrant delete: point_id=%s", page_id)
