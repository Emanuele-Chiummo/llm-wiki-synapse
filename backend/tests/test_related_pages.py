"""
Tests for GET /pages/{id}/related and the extended GET /pages/{id}/content response.

Coverage:
  T-REL-001  GET /pages/{id}/related returns 200 with correctly ranked items
  T-REL-002  GET /pages/{id}/related returns 200 empty list when page has no edges
  T-REL-003  GET /pages/{id}/related returns 404 for unknown UUID
  T-REL-004  GET /pages/{id}/related respects the limit query parameter
  T-REL-005  GET /pages/{id}/related limit cap: query param > 50 → 422
  T-CONT-006 GET /pages/{id}/content response now includes type and sources fields

All tests reuse the api_env / api_client fixtures from test_api.py (SQLite in-memory,
FakeQdrantClient, FakeEmbeddingClient — no live infra).

Invariant checks:
  I1 — tests only insert/read from the persisted edges table; no recompute triggered.
  I2 — no graph layout code touched; no GraphEngine/GraphCache interaction.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text as sa_text

# Re-use the shared fixtures from test_api.py (auto-discovered by conftest.py)
from tests.test_api import api_client, api_env  # noqa: F401


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _ingest_page(
    api_env: dict[str, Any],
    *,
    filename: str,
    content: str,
) -> str:
    """Write a markdown file to sources and ingest it; return page_id as str."""
    from app.ingest.orchestrator import ingest_file

    src: Path = api_env["sources_dir"] / filename
    src.write_text(content, encoding="utf-8")
    result = await ingest_file(src)
    return str(result.page_id)


def _uuid_to_db(val: str) -> str:
    """
    Normalise a UUID string to the 32-char hex format that SQLAlchemy writes
    for UUID columns in SQLite (no hyphens).  Postgres uses native UUID so the
    ORM comparison is type-safe; SQLite needs string equality on the same format.
    """
    return uuid.UUID(val).hex


async def _insert_edge(
    api_env: dict[str, Any],
    *,
    source_page_id: str,
    target_page_id: str,
    weight: float,
    vault_id: str = "test-vault",
) -> None:
    """
    Directly insert a row into the edges table (simulates a completed graph recompute).

    source_page_id / target_page_id must be strings returned by str(result.page_id).
    They are normalised to 32-char hex (no hyphens) so they match the format that
    SQLAlchemy uses for UUID columns on SQLite (pages.id is stored without hyphens).
    Postgres does not care about hyphenation at the SQL level (native UUID type).
    """
    session_factory = api_env["session_factory"]
    async with session_factory() as session:
        await session.execute(
            sa_text(
                "INSERT INTO edges (id, vault_id, source_page_id, target_page_id, weight) "
                "VALUES (:id, :vault_id, :src, :tgt, :w)"
            ),
            {
                "id": uuid.uuid4().hex,  # 32-char hex, matches SQLAlchemy SQLite UUID format
                "vault_id": vault_id,
                "src": _uuid_to_db(source_page_id),
                "tgt": _uuid_to_db(target_page_id),
                "w": weight,
            },
        )
        await session.commit()


# ── T-REL-001: related pages returned and ranked ──────────────────────────────


class TestGetRelatedPages:
    """GET /pages/{id}/related — T-REL-001 through T-REL-005"""

    async def test_returns_ranked_related_pages(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-REL-001: related pages are returned ordered by score descending."""
        page_a = await _ingest_page(
            api_env,
            filename="a.md",
            content="---\ntype: entity\ntitle: Alpha\nsources: []\n---\n\nA.\n",
        )
        page_b = await _ingest_page(
            api_env,
            filename="b.md",
            content="---\ntype: concept\ntitle: Beta\nsources: []\n---\n\nB.\n",
        )
        page_c = await _ingest_page(
            api_env,
            filename="c.md",
            content="---\ntype: entity\ntitle: Gamma\nsources: []\n---\n\nC.\n",
        )

        # Edge A↔B weight 7.0, A↔C weight 3.0 (canonical order doesn't matter here)
        await _insert_edge(api_env, source_page_id=page_a, target_page_id=page_b, weight=7.0)
        await _insert_edge(api_env, source_page_id=page_a, target_page_id=page_c, weight=3.0)

        resp = await api_client.get(f"/pages/{page_a}/related")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert "items" in data
        assert "total" in data
        assert data["total"] == 2

        items = data["items"]
        assert len(items) == 2

        # Highest weight first
        assert items[0]["score"] == 7.0
        assert items[0]["page_id"] == page_b
        assert items[0]["title"] == "Beta"
        assert items[0]["type"] == "concept"

        assert items[1]["score"] == 3.0
        assert items[1]["page_id"] == page_c

    async def test_returns_empty_list_when_no_edges(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-REL-002: 200 with empty items list when the page has no edges yet (I1 compliance)."""
        page_id = await _ingest_page(
            api_env,
            filename="isolated.md",
            content="---\ntype: entity\ntitle: Isolated\nsources: []\n---\n\nAlone.\n",
        )

        resp = await api_client.get(f"/pages/{page_id}/related")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert data["items"] == []
        assert data["total"] == 0

    async def test_404_for_unknown_page(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-REL-003: 404 when the page_id does not exist in the index."""
        unknown = str(uuid.uuid4())
        resp = await api_client.get(f"/pages/{unknown}/related")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"

    async def test_limit_respected(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-REL-004: limit query param caps the number of items returned."""
        page_a = await _ingest_page(
            api_env,
            filename="hub.md",
            content="---\ntype: entity\ntitle: Hub\nsources: []\n---\n\nHub.\n",
        )
        # Insert 5 neighbour pages with distinct weights
        neighbours = []
        for i in range(5):
            pid = await _ingest_page(
                api_env,
                filename=f"nb_{i}.md",
                content=f"---\ntype: concept\ntitle: Neighbour {i}\nsources: []\n---\n\nBody.\n",
            )
            await _insert_edge(
                api_env,
                source_page_id=page_a,
                target_page_id=pid,
                weight=float(10 - i),
            )
            neighbours.append(pid)

        # Default limit=10 → all 5
        resp_all = await api_client.get(f"/pages/{page_a}/related")
        assert resp_all.status_code == 200
        assert len(resp_all.json()["items"]) == 5
        assert resp_all.json()["total"] == 5

        # limit=2 → only top-2
        resp_lim = await api_client.get(f"/pages/{page_a}/related?limit=2")
        assert resp_lim.status_code == 200
        items_lim = resp_lim.json()["items"]
        assert len(items_lim) == 2
        # total still reflects full count
        assert resp_lim.json()["total"] == 5
        # Top-2 are the ones with weight 10.0 and 9.0
        scores = [item["score"] for item in items_lim]
        assert scores == [10.0, 9.0]

    async def test_limit_over_cap_returns_422(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-REL-005: limit > 50 (hard cap) → 422 Unprocessable Entity."""
        page_id = await _ingest_page(
            api_env,
            filename="cap_test.md",
            content="---\ntype: entity\ntitle: Cap Test\nsources: []\n---\n\nBody.\n",
        )
        resp = await api_client.get(f"/pages/{page_id}/related?limit=51")
        assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"

    async def test_edge_stored_reverse_canonical_order(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-REL-006: related works whether the page is source_page_id or target_page_id in the edge."""
        page_x = await _ingest_page(
            api_env,
            filename="x.md",
            content="---\ntype: entity\ntitle: X\nsources: []\n---\n\nX.\n",
        )
        page_y = await _ingest_page(
            api_env,
            filename="y.md",
            content="---\ntype: entity\ntitle: Y\nsources: []\n---\n\nY.\n",
        )

        # Insert with page_y as source (Y→X, so page_x is the target)
        await _insert_edge(api_env, source_page_id=page_y, target_page_id=page_x, weight=5.0)

        # Querying from page_x's perspective — should still find page_y
        resp = await api_client.get(f"/pages/{page_x}/related")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["page_id"] == page_y
        assert data["items"][0]["title"] == "Y"
        assert data["items"][0]["score"] == 5.0


# ── T-CONT-006: content endpoint includes type and sources ────────────────────


class TestPageContentFrontmatter:
    """GET /pages/{id}/content response includes type + sources (Task 2)."""

    async def test_content_response_includes_type_and_sources(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-CONT-006: type and sources appear in the /content response (additive, backward-compatible)."""
        page_id = await _ingest_page(
            api_env,
            filename="rich.md",
            content=(
                "---\ntype: concept\ntitle: Rich Page\n"
                "sources: [ref_a.pdf, ref_b.docx]\n---\n\nRich body.\n"
            ),
        )

        resp = await api_client.get(f"/pages/{page_id}/content")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        # Existing fields still present (backward compatibility)
        assert data["id"] == page_id
        assert "content" in data
        assert "content_hash" in data
        assert "updated_at" in data
        assert data["title"] == "Rich Page"

        # New frontmatter fields
        assert data["type"] == "concept", f"Expected type='concept', got {data.get('type')!r}"
        assert data["sources"] == ["ref_a.pdf", "ref_b.docx"], (
            f"Expected sources list, got {data.get('sources')!r}"
        )

    async def test_content_response_type_null_when_absent(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-CONT-007: type is null in the response when frontmatter type is absent."""
        page_id = await _ingest_page(
            api_env,
            filename="notype.md",
            content="---\ntitle: No Type\n---\n\nBody.\n",
        )

        resp = await api_client.get(f"/pages/{page_id}/content")
        assert resp.status_code == 200
        data = resp.json()
        assert data["type"] is None
        assert data["sources"] is None
