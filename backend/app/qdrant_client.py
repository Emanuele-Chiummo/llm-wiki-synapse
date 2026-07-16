"""
Thin Qdrant wrapper — ensure_collection, upsert_point, delete_point (ADR-0002).

Collection: synapse_pages (configurable via Settings.qdrant_collection).
Distance:   Cosine.
Vector size: EMBEDDING_DIM (ADR-0004 — from Settings, never hardcoded).
Point id:   == pages.id UUID (ADR-0002 — stable join key).
Payload:    {file_path, title, type} (AC-QD-2).

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


# ── Point operations ──────────────────────────────────────────────────────────


async def upsert_point(
    *,
    page_id: uuid.UUID,
    vector: list[float],
    file_path: str,
    title: str | None,
    page_type: str | None,
) -> None:
    """
    Upsert a Qdrant point for *page_id*.

    Uses the UUID as the point id (ADR-0002).
    Payload = {file_path, title, type} (AC-QD-2).
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
                },
            )
        ],
    )
    logger.debug("Qdrant upsert: point_id=%s file_path=%r", page_id, file_path)


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
