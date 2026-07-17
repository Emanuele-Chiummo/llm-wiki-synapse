"""
GET /pages — optional server-side `type` filter (FE-PERF-2).

Fixes an over-fetch on the frontend Home dashboard: OpenQuestionsBlock used to fetch
limit=100 rows and filter client-side to type=="query" just to show 5. This filter lets
the client ask the server for `?type=query&limit=5` directly.

Coverage:
  T-LPTF-001  GET /pages?type=query — only query-type pages are returned
  T-LPTF-002  GET /pages?type=query&limit=5 — respects limit alongside the filter
  T-LPTF-003  GET /pages (no type param) — unfiltered, unchanged behaviour (non-regression)
  T-LPTF-004  GET /pages?type=concept — total reflects the FILTERED count, not the full vault

Invariants:
  I1 — read-only; GET /pages never triggers a rescan.
  I6 — zero InferenceProvider calls; uses ingest_file() via watcher seam.

Database: SQLite in-memory via shared api_env / api_client fixtures (test_api.py).
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from tests.test_api import api_client, api_env  # noqa: F401
from tests.test_related_pages import _ingest_page


async def _get_pages(api_client: AsyncClient, query: str = "") -> dict:
    resp = await api_client.get(f"/pages{query}")
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.asyncio
async def test_type_filter_returns_only_matching_type(
    api_client: AsyncClient, api_env: dict[str, Any]
) -> None:
    """T-LPTF-001: ?type=query returns only pages whose frontmatter type is 'query'."""
    await _ingest_page(
        api_env,
        filename="q1.md",
        content="---\ntype: query\ntitle: Open Question One\nsources: []\n---\n\nBody.\n",
    )
    await _ingest_page(
        api_env,
        filename="c1.md",
        content="---\ntype: concept\ntitle: Some Concept\nsources: []\n---\n\nBody.\n",
    )

    body = await _get_pages(api_client, "?type=query")
    titles = {item["title"] for item in body["items"]}
    assert titles == {"Open Question One"}


@pytest.mark.asyncio
async def test_type_filter_respects_limit(api_client: AsyncClient, api_env: dict[str, Any]) -> None:
    """T-LPTF-002: ?type=query&limit=5 — filter + limit compose correctly."""
    for i in range(7):
        await _ingest_page(
            api_env,
            filename=f"query_{i}.md",
            content=f"---\ntype: query\ntitle: Query {i}\nsources: []\n---\n\nBody.\n",
        )

    body = await _get_pages(api_client, "?type=query&limit=5")
    assert len(body["items"]) == 5
    assert all(item["type"] == "query" for item in body["items"])


@pytest.mark.asyncio
async def test_no_type_param_is_unfiltered(
    api_client: AsyncClient, api_env: dict[str, Any]
) -> None:
    """T-LPTF-003: omitting `type` preserves the original unfiltered behaviour."""
    await _ingest_page(
        api_env,
        filename="q2.md",
        content="---\ntype: query\ntitle: Query Two\nsources: []\n---\n\nBody.\n",
    )
    await _ingest_page(
        api_env,
        filename="c2.md",
        content="---\ntype: concept\ntitle: Concept Two\nsources: []\n---\n\nBody.\n",
    )

    body = await _get_pages(api_client)
    titles = {item["title"] for item in body["items"]}
    assert {"Query Two", "Concept Two"}.issubset(titles)


@pytest.mark.asyncio
async def test_type_filter_total_reflects_filtered_count(
    api_client: AsyncClient, api_env: dict[str, Any]
) -> None:
    """T-LPTF-004: `total` in the response is the FILTERED total, not the whole vault."""
    await _ingest_page(
        api_env,
        filename="concept_only.md",
        content="---\ntype: concept\ntitle: Concept Only\nsources: []\n---\n\nBody.\n",
    )
    await _ingest_page(
        api_env,
        filename="query_only.md",
        content="---\ntype: query\ntitle: Query Only\nsources: []\n---\n\nBody.\n",
    )

    body = await _get_pages(api_client, "?type=concept")
    assert body["total"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["title"] == "Concept Only"
