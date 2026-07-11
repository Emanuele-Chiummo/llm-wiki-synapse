"""
GET /pages now returns `domain` and `community` on each item.

Coverage:
  T-LPD-001  GET /pages — item has `domain` field; null when page has no domain/* tag
  T-LPD-002  GET /pages — domain matches when page has a domain/* tag in controlled vocab
  T-LPD-003  GET /pages — domain is null when vocab is empty (no config)
  T-LPD-004  GET /pages — community field matches pages.community column
  T-LPD-005  GET /pages — domain/* tags not in vocab → domain=null (stale tag excluded)

Invariants:
  I1 — read-only; GET /pages never triggers a rescan.
  I6 — zero InferenceProvider calls; uses ingest_file() via watcher seam.
  backward-compat — `domain` and `community` are additive nullable fields.

Database: SQLite in-memory via shared api_env / api_client fixtures (test_api.py).
"""

from __future__ import annotations

import uuid as _uuid
from typing import Any
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import text as sa_text

from tests.test_api import api_client, api_env  # noqa: F401
from tests.test_related_pages import _ingest_page

# ── Helpers ────────────────────────────────────────────────────────────────────


async def _patch_community(
    api_env: dict[str, Any],
    page_id: str,
    community_id: int,
) -> None:
    """Set pages.community for a page (simulate GraphEngine output, I2).

    Uses REPLACE(id, '-', '') so the match is portable across SQLite (stores UUID
    as 32-char hex without hyphens) and Postgres (native UUID type).
    """
    session_factory = api_env["session_factory"]
    # Normalise to 32-char hex (no hyphens) — the format SQLite uses internally.
    pid_hex = _uuid.UUID(page_id).hex
    async with session_factory() as sess:
        await sess.execute(
            sa_text(
                "UPDATE pages SET community = :cid " "WHERE REPLACE(id, '-', '') = :pid"
            ).bindparams(cid=community_id, pid=pid_hex)
        )
        await sess.commit()


async def _get_pages_list(api_client: AsyncClient) -> list[dict]:
    """Fetch GET /pages?limit=500 and return the items list."""
    resp = await api_client.get("/pages?limit=500")
    assert resp.status_code == 200
    return resp.json()["items"]


# ── T-LPD-001: domain=null when no domain/* tag ───────────────────────────────


@pytest.mark.asyncio
async def test_domain_null_when_untagged(api_client: AsyncClient, api_env: dict[str, Any]) -> None:
    """T-LPD-001: page with no domain/* tags → domain=null in GET /pages."""
    await _ingest_page(
        api_env,
        filename="untagged.md",
        content=("---\ntype: concept\ntitle: Untagged Concept\nsources: []\n---\n\nBody.\n"),
    )
    with patch(
        "app.routers.pages.effective_domain_vocabulary",
        return_value=["SAM", "Procurement"],
    ):
        items = await _get_pages_list(api_client)

    page = next((p for p in items if p["title"] == "Untagged Concept"), None)
    assert page is not None, "page not found in list"
    assert page.get("domain") is None, f"expected domain=null, got {page.get('domain')}"


# ── T-LPD-002: domain matches vocab domain/* tag ─────────────────────────────


@pytest.mark.asyncio
async def test_domain_returned_when_tagged(
    api_client: AsyncClient, api_env: dict[str, Any]
) -> None:
    """T-LPD-002: page with 'domain/SAM' tag + SAM in vocab → domain='SAM'."""
    await _ingest_page(
        api_env,
        filename="sam_entity.md",
        content=(
            "---\ntype: entity\ntitle: SAM Entity\n"
            "sources: []\ntags: [domain/SAM]\n---\n\nBody.\n"
        ),
    )
    with patch(
        "app.routers.pages.effective_domain_vocabulary",
        return_value=["SAM", "Procurement"],
    ):
        items = await _get_pages_list(api_client)

    page = next((p for p in items if p["title"] == "SAM Entity"), None)
    assert page is not None, "page not found in list"
    assert page.get("domain") == "SAM", f"expected domain='SAM', got {page.get('domain')}"


# ── T-LPD-003: domain=null when vocab is empty ───────────────────────────────


@pytest.mark.asyncio
async def test_domain_null_when_vocab_empty(
    api_client: AsyncClient, api_env: dict[str, Any]
) -> None:
    """T-LPD-003: vocab=[] → domain=null for all pages regardless of tags."""
    await _ingest_page(
        api_env,
        filename="has_tag_no_vocab.md",
        content=(
            "---\ntype: concept\ntitle: Tagged No Vocab\n"
            "sources: []\ntags: [domain/SAM]\n---\n\nBody.\n"
        ),
    )
    with patch(
        "app.routers.pages.effective_domain_vocabulary",
        return_value=[],
    ):
        items = await _get_pages_list(api_client)

    page = next((p for p in items if p["title"] == "Tagged No Vocab"), None)
    assert page is not None, "page not found in list"
    assert (
        page.get("domain") is None
    ), f"expected domain=null when vocab is empty, got {page.get('domain')}"


# ── T-LPD-004: community field reflects DB column ────────────────────────────


@pytest.mark.asyncio
async def test_community_field_populated(api_client: AsyncClient, api_env: dict[str, Any]) -> None:
    """T-LPD-004: community field in GET /pages matches pages.community column."""
    page_id = await _ingest_page(
        api_env,
        filename="community_page.md",
        content=("---\ntype: entity\ntitle: Community Entity\nsources: []\n---\n\nBody.\n"),
    )
    # Simulate GraphEngine assigning community=7 to this page (I2).
    await _patch_community(api_env, page_id, 7)

    items = await _get_pages_list(api_client)
    page = next((p for p in items if p["title"] == "Community Entity"), None)
    assert page is not None, "page not found in list"
    assert page.get("community") == 7, f"expected community=7, got {page.get('community')}"


# ── T-LPD-005: stale domain/* tag not in vocab → domain=null ─────────────────


@pytest.mark.asyncio
async def test_domain_null_for_stale_tag(api_client: AsyncClient, api_env: dict[str, Any]) -> None:
    """T-LPD-005: page has 'domain/OldDomain' but vocab only has 'SAM' → domain=null."""
    await _ingest_page(
        api_env,
        filename="stale_tag.md",
        content=(
            "---\ntype: concept\ntitle: Stale Domain Concept\n"
            "sources: []\ntags: [domain/OldDomain]\n---\n\nBody.\n"
        ),
    )
    with patch(
        "app.routers.pages.effective_domain_vocabulary",
        return_value=["SAM"],
    ):
        items = await _get_pages_list(api_client)

    page = next((p for p in items if p["title"] == "Stale Domain Concept"), None)
    assert page is not None, "page not found in list"
    assert (
        page.get("domain") is None
    ), f"expected domain=null for stale tag not in vocab, got {page.get('domain')}"
