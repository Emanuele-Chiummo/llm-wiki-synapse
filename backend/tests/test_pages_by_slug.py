"""
v1.3.3 — GET /pages/by-slug/{slug}: citation slug → page resolution.

Regression for the chat citation click-through 422: citations carry a DERIVED
slug (rag.retrieval.slugify(title), not a DB column) and the UI fed it into
/pages/{uuid} routes. This route is the single resolution point.

Reuses the api_env / api_client fixtures from test_api.py (SQLite in-memory).
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

# Re-use the shared fixtures from test_api.py (auto-discovered by conftest.py)
from tests.test_api import api_client, api_env  # noqa: F401
from tests.test_related_pages import _ingest_page


@pytest.mark.asyncio
class TestGetPageBySlug:
    async def test_resolves_slug_to_page(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        page_id = await _ingest_page(
            api_env,
            filename="sam.md",
            content=("---\ntype: concept\ntitle: ServiceNow SAM Pro\nsources: []\n---\n\nBody.\n"),
        )

        res = await api_client.get("/pages/by-slug/servicenow-sam-pro")
        assert res.status_code == 200
        body = res.json()
        assert body["id"] == page_id
        assert body["title"] == "ServiceNow SAM Pro"

    async def test_unknown_slug_returns_404_not_422(
        self, api_client: AsyncClient, api_env: dict[str, Any]  # noqa: ARG002
    ) -> None:
        res = await api_client.get("/pages/by-slug/non-esiste-proprio")
        assert res.status_code == 404

    async def test_literal_segment_not_captured_by_uuid_route(
        self, api_client: AsyncClient, api_env: dict[str, Any]  # noqa: ARG002
    ) -> None:
        # If /pages/{page_id} captured 'by-slug', this would be a 422 (invalid UUID).
        res = await api_client.get("/pages/by-slug/whatever")
        assert res.status_code == 404
