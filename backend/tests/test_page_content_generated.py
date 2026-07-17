"""
Regression: GET/PUT /pages/{id}/content must work for a GENERATED wiki page.

Bug (found live during the v0.5 re-analysis): write_wiki_page stored content_hash =
sha256(serialized) while writing serialized + "\n", so the DB row hash never matched the file.
GET returned the
DB row hash as the optimistic-lock token, so PUT's on-disk comparison ALWAYS saw a mismatch → a
generated page could never be edited (permanent 409). The existing content tests only exercised
raw-source pages (ingest_file hashes the raw bytes, which happen to equal the file bytes), so they
missed it. Fixes: write_wiki_page hashes the bytes actually written; GET recomputes the token from
the file bytes it returns.
"""

from __future__ import annotations

import hashlib
from typing import Any

import pytest

from tests.test_api import api_client, api_env  # noqa: F401


def _wikipage(title: str, source: str) -> Any:
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

    return WikiPage(
        title=title,
        type=PageType.CONCEPT,
        content=f"Body about {title}. See related work for context.",
        frontmatter=WikiFrontmatter(
            type=PageType.CONCEPT, title=title, sources=[source], lang="en"
        ),
    )


@pytest.mark.asyncio
async def test_generated_page_get_token_matches_content_and_put_roundtrips(
    api_env: dict[str, Any],
    api_client: Any,
) -> None:
    from app.ingest.writer import write_wiki_page

    # Create a page through the generation write path (NOT a raw-source ingest).
    row = await write_wiki_page(
        None, _wikipage("Token Hash Page", "raw/sources/x.md"), "raw/sources/x.md"
    )
    pid = str(row.id)

    # GET: the returned content_hash MUST be the hash of the exact content bytes returned,
    # so it is a valid optimistic-lock token (this is what regressed).
    r_get = await api_client.get(f"/pages/{pid}/content")
    assert r_get.status_code == 200
    body = r_get.json()
    content = body["content"]
    assert body["content_hash"] == hashlib.sha256(content.encode("utf-8")).hexdigest()

    # PUT a manual edit using that token → must succeed (no spurious 409).
    edited = content.rstrip("\n") + "\n\nManually appended line.\n"
    r_put = await api_client.put(
        f"/pages/{pid}/content",
        json={"content": edited, "expected_hash": body["content_hash"]},
    )
    assert r_put.status_code == 200, r_put.text

    # GET again: edit persisted, token still consistent with the new content.
    r_get2 = await api_client.get(f"/pages/{pid}/content")
    assert r_get2.status_code == 200
    body2 = r_get2.json()
    assert "Manually appended line." in body2["content"]
    assert body2["content_hash"] == hashlib.sha256(body2["content"].encode("utf-8")).hexdigest()
