"""
Tests for GET /pages/{id}/content and PUT /pages/{id}/content.

Coverage:
  T-PC-001  GET returns content for a known page
  T-PC-002  GET 404 for unknown UUID
  T-PC-003  GET 410 when file is missing on disk
  T-PC-004  GET path-traversal guard via _resolve_page_path (400)
  T-PC-005  PUT writes content; new content_hash returned; row updated (inline re-index, I1)
  T-PC-006  PUT enforces trailing newline (I5)
  T-PC-007  PUT 409 on stale expected_hash
  T-PC-007b PUT succeeds with correct expected_hash
  T-PC-008  PUT 404 for unknown UUID
  T-PC-009  PUT 403 when file_path is outside vault/wiki/ (K1 layer separation, ADR-0035)
  T-PC-010  PUT 413 when body exceeds _MAX_PAGE_CONTENT_BYTES (ADR-0035)
  T-PC-011  PUT 422 when YAML frontmatter is invalid (ADR-0035, I5)
  T-PC-012  PUT 400 path-traversal guard via _resolve_wiki_page_path

All tests use the shared api_env / api_client fixtures from test_api.py (SQLite in-memory,
FakeQdrantClient, FakeEmbeddingClient — no live infra).

(F1-content-read, F1-content-write, I1, I5, ADR-0035)
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from httpx import AsyncClient

# Re-use the shared fixtures from test_api.py (registered by conftest.py auto-discovery)
from tests.test_api import _ingest_test_file, api_client, api_env  # noqa: F401

# ── Helpers ────────────────────────────────────────────────────────────────────


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def _ingest_source_page(
    api_env: dict[str, Any],
    *,
    filename: str = "source_page.md",
    content: str = "---\ntype: entity\ntitle: Source Page\nsources: [a.pdf]\n---\n\nBody.\n",
) -> tuple[str, Path]:
    """
    Write a file to vault/raw/sources/ and ingest it.
    Returns (page_id_str, abs_path).
    Used by GET content tests (GET works for any file_path — no wiki/ restriction).
    """
    from app.ingest.orchestrator import ingest_file

    src = api_env["sources_dir"] / filename
    src.write_text(content, encoding="utf-8")
    result = await ingest_file(src)
    return str(result.page_id), src


async def _ingest_wiki_entity(
    api_env: dict[str, Any],
    *,
    filename: str = "wiki_entity.md",
    content: str = "---\ntype: entity\ntitle: Wiki Entity\nsources: []\n---\n\nBody.\n",
) -> tuple[str, Path]:
    """
    Write a file to vault/wiki/entities/ and ingest it via ingest_file().
    Returns (page_id_str, abs_path).

    Used by PUT content tests — PUT only allows edits to vault/wiki/ files (ADR-0035).
    The ingest seam stores file_path relative to vault_root, so the stored path will be
    'wiki/entities/<filename>' which passes the _resolve_wiki_page_path guard.
    """
    from app.ingest.orchestrator import ingest_file

    wiki_entities = api_env["vault_root"] / "wiki" / "entities"
    wiki_entities.mkdir(parents=True, exist_ok=True)
    wiki_file = wiki_entities / filename
    wiki_file.write_text(content, encoding="utf-8")
    result = await ingest_file(wiki_file)
    return str(result.page_id), wiki_file


# ── T-PC-001..004: GET /pages/{id}/content ────────────────────────────────────


class TestGetPageContent:
    """GET /pages/{id}/content — T-PC-001..004"""

    async def test_get_content_returns_200_with_content(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-PC-001: GET /pages/{id}/content returns 200 and the raw markdown."""
        md_content = "---\ntype: entity\ntitle: Alpha\nsources: []\n---\n\nAlpha body.\n"
        page_id, src = await _ingest_source_page(
            api_env, filename="alpha.md", content=md_content
        )

        resp = await api_client.get(f"/pages/{page_id}/content")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert data["id"] == page_id
        assert data["content"] == md_content
        assert data["file_path"] != ""
        assert "content_hash" in data
        assert "updated_at" in data

    async def test_get_content_hash_matches_file_bytes(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-PC-001b: returned content_hash equals sha256 of file bytes (matches watcher)."""
        md_content = "---\ntype: concept\ntitle: Beta\nsources: []\n---\n\nBeta body.\n"
        page_id, src = await _ingest_source_page(
            api_env, filename="beta.md", content=md_content
        )

        resp = await api_client.get(f"/pages/{page_id}/content")
        assert resp.status_code == 200
        data = resp.json()

        expected_hash = _sha256(md_content.encode("utf-8"))
        assert data["content_hash"] == expected_hash, (
            f"content_hash mismatch: API returned {data['content_hash']!r}, "
            f"expected sha256 of file bytes {expected_hash!r}"
        )

    async def test_get_content_404_unknown_id(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-PC-002: GET /pages/{id}/content returns 404 for an unknown UUID."""
        unknown = str(uuid.uuid4())
        resp = await api_client.get(f"/pages/{unknown}/content")
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"

    async def test_get_content_410_file_missing_on_disk(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-PC-003: GET returns 410 when the page row exists but the file was deleted."""
        page_id, src = await _ingest_source_page(api_env, filename="gone.md")

        # Remove the file without updating the DB row (simulates an in-flight deletion)
        src.unlink()

        resp = await api_client.get(f"/pages/{page_id}/content")
        assert resp.status_code == 410, (
            f"Expected 410 (file gone) but got {resp.status_code}: {resp.text}"
        )
        detail = resp.json()["detail"].lower()
        assert "not present" in detail or "missing" in detail, (
            f"410 detail should mention file missing; got: {detail!r}"
        )

    def test_resolve_page_path_rejects_traversal(
        self, api_env: dict[str, Any]
    ) -> None:
        """
        T-PC-004: _resolve_page_path raises HTTPException 400 for paths that escape the
        vault root. Tests the guard directly without a DB round-trip.
        """
        import app.main as main_module
        from fastapi import HTTPException

        traversal_cases = [
            "../../../etc/passwd",
            "../../secret",
            "wiki/../../../etc/shadow",
        ]
        for evil in traversal_cases:
            try:
                main_module._resolve_page_path(evil)
                raise AssertionError(
                    f"Expected HTTPException for {evil!r} but none was raised"
                )
            except HTTPException as exc:
                assert exc.status_code == 400, (
                    f"Expected 400 for {evil!r}, got {exc.status_code}"
                )
                assert "traversal" in exc.detail.lower(), (
                    f"Expected 'traversal' in detail for {evil!r}; got: {exc.detail!r}"
                )


# ── T-PC-005..012: PUT /pages/{id}/content ────────────────────────────────────


class TestPutPageContent:
    """PUT /pages/{id}/content — T-PC-005..012"""

    async def test_put_writes_content_and_returns_new_hash(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-PC-005: PUT writes new content; response carries the new content_hash."""
        page_id, wiki_file = await _ingest_wiki_entity(
            api_env,
            filename="put_target.md",
            content="---\ntype: entity\ntitle: Put Target\nsources: []\n---\n\nOld body.\n",
        )

        new_content = "---\ntype: entity\ntitle: Put Target\nsources: []\n---\n\nNew body.\n"
        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={"content": new_content},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        assert data["id"] == page_id
        expected_hash = _sha256(new_content.encode("utf-8"))
        assert data["content_hash"] == expected_hash, (
            f"content_hash mismatch after PUT: got {data['content_hash']!r}, "
            f"expected {expected_hash!r}"
        )

        # Verify the file on disk was actually updated
        on_disk = wiki_file.read_text(encoding="utf-8")
        assert on_disk == new_content, (
            f"File on disk does not match PUT body: {on_disk!r} != {new_content!r}"
        )

    async def test_put_inline_reindex_updates_db_row(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """
        T-PC-005b: After PUT, the Postgres row reflects the new content_hash.
        Verifies inline re-index (I1, ADR-0035) — no watcher event needed.
        """
        from app.ingest.orchestrator import _load_page  # type: ignore[attr-defined]

        old_content = "---\ntype: entity\ntitle: Reindex Test\nsources: []\n---\n\nOld.\n"
        page_id, wiki_file = await _ingest_wiki_entity(
            api_env, filename="reindex_test.md", content=old_content
        )

        new_content = "---\ntype: entity\ntitle: Reindex Test\nsources: []\n---\n\nUpdated.\n"
        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={"content": new_content},
        )
        assert resp.status_code == 200

        # Confirm the row in Postgres has the new hash
        rel_path = str(
            wiki_file.resolve().relative_to(api_env["vault_root"].resolve())
        )
        db_page = await _load_page(rel_path)
        assert db_page is not None, "Page row must still exist after PUT"
        expected_hash = _sha256(new_content.encode("utf-8"))
        assert db_page.content_hash == expected_hash, (
            f"DB row content_hash not updated after PUT: "
            f"got {db_page.content_hash!r}, expected {expected_hash!r}. "
            "Inline re-index (I1) must update the row synchronously."
        )

    async def test_put_enforces_trailing_newline(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-PC-006: PUT adds trailing newline if the body lacks one (I5 convention)."""
        page_id, wiki_file = await _ingest_wiki_entity(
            api_env,
            filename="newline_target.md",
            content="---\ntype: entity\ntitle: NL\nsources: []\n---\n\nBody.\n",
        )

        no_trailing = "---\ntype: entity\ntitle: NL\nsources: []\n---\n\nUpdated."
        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={"content": no_trailing},
        )
        assert resp.status_code == 200

        on_disk = wiki_file.read_text(encoding="utf-8")
        assert on_disk.endswith("\n"), (
            f"PUT must enforce trailing newline; file ends with: {on_disk[-20:]!r}"
        )

    async def test_put_409_on_stale_expected_hash(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-PC-007: PUT returns 409 when expected_hash does not match the current file hash."""
        page_id, wiki_file = await _ingest_wiki_entity(
            api_env,
            filename="stale_target.md",
            content="---\ntype: entity\ntitle: Stale\nsources: []\n---\n\nBody.\n",
        )

        stale_hash = "0" * 64  # obviously wrong hash

        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={
                "content": "---\ntype: entity\ntitle: Stale\nsources: []\n---\n\nNew body.\n",
                "expected_hash": stale_hash,
            },
        )
        assert resp.status_code == 409, (
            f"Stale expected_hash must return 409; got {resp.status_code}: {resp.text}"
        )
        detail = resp.json()["detail"]
        assert "hash" in detail.lower() or "mismatch" in detail.lower(), (
            f"409 detail should mention hash mismatch; got: {detail!r}"
        )

    async def test_put_succeeds_with_correct_expected_hash(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-PC-007b: PUT succeeds when expected_hash matches the current file."""
        original_content = (
            "---\ntype: entity\ntitle: Correct Hash\nsources: []\n---\n\nBody.\n"
        )
        page_id, wiki_file = await _ingest_wiki_entity(
            api_env, filename="correct_hash.md", content=original_content
        )

        correct_hash = _sha256(original_content.encode("utf-8"))
        new_content = (
            "---\ntype: entity\ntitle: Correct Hash\nsources: []\n---\n\nUpdated.\n"
        )
        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={"content": new_content, "expected_hash": correct_hash},
        )
        assert resp.status_code == 200, (
            f"PUT with correct expected_hash must return 200; got {resp.status_code}: {resp.text}"
        )

    async def test_put_404_unknown_page(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-PC-008: PUT /pages/{id}/content returns 404 for an unknown UUID."""
        unknown = str(uuid.uuid4())
        resp = await api_client.put(
            f"/pages/{unknown}/content",
            json={"content": "---\ntype: entity\ntitle: X\nsources: []\n---\n\nX.\n"},
        )
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"

    async def test_put_403_for_sources_file(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """
        T-PC-009: PUT returns 403 when the page's file_path is inside raw/sources/
        (K1 layer separation, ADR-0035 wiki-only guard).
        """
        # Ingest a raw/sources/ file — file_path will be 'raw/sources/...'
        page_id, src = await _ingest_source_page(
            api_env,
            filename="sources_file.md",
            content="---\ntype: entity\ntitle: Sources File\nsources: []\n---\n\nBody.\n",
        )

        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={"content": "---\ntype: entity\ntitle: Sources File\nsources: []\n---\n\nEdit.\n"},
        )
        assert resp.status_code == 403, (
            f"PUT on a raw/sources/ file must return 403 (K1 layer); "
            f"got {resp.status_code}: {resp.text}"
        )

    async def test_put_413_body_too_large(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-PC-010: PUT returns 413 when body exceeds _MAX_PAGE_CONTENT_BYTES (ADR-0035)."""
        import app.main as main_module

        page_id, wiki_file = await _ingest_wiki_entity(
            api_env,
            filename="size_target.md",
            content="---\ntype: entity\ntitle: Size\nsources: []\n---\n\nBody.\n",
        )

        oversized = "x" * (main_module._MAX_PAGE_CONTENT_BYTES + 1)
        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={"content": oversized},
        )
        assert resp.status_code == 413, (
            f"Oversized body must return 413; got {resp.status_code}: {resp.text}"
        )

    async def test_put_422_invalid_yaml_frontmatter(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-PC-011: PUT returns 422 when YAML frontmatter is invalid (ADR-0035, I5)."""
        page_id, wiki_file = await _ingest_wiki_entity(
            api_env,
            filename="fm_target.md",
            content="---\ntype: entity\ntitle: FM\nsources: []\n---\n\nBody.\n",
        )

        # Intentionally broken YAML frontmatter (unclosed bracket)
        broken_content = "---\ntitle: [broken\ntype: entity\n---\n\nBody.\n"
        resp = await api_client.put(
            f"/pages/{page_id}/content",
            json={"content": broken_content},
        )
        assert resp.status_code == 422, (
            f"Invalid YAML frontmatter must return 422; got {resp.status_code}: {resp.text}"
        )
        detail = resp.json()["detail"].lower()
        assert "frontmatter" in detail or "yaml" in detail or "parse" in detail, (
            f"422 detail should mention frontmatter or YAML; got: {detail!r}"
        )

    def test_resolve_wiki_page_path_rejects_traversal_and_non_wiki(
        self, api_env: dict[str, Any]
    ) -> None:
        """
        T-PC-012: _resolve_wiki_page_path raises 400 for vault-escaping paths and 403
        for paths inside the vault but outside wiki/.
        """
        import app.main as main_module
        from fastapi import HTTPException

        # Total escape → 400
        try:
            main_module._resolve_wiki_page_path("../../../etc/passwd")
            raise AssertionError("Expected HTTPException for traversal but none raised")
        except HTTPException as exc:
            assert exc.status_code == 400, f"Expected 400 for traversal, got {exc.status_code}"
            assert "traversal" in exc.detail.lower()

        # Inside vault but not wiki/ → 403
        try:
            main_module._resolve_wiki_page_path("raw/sources/foo.md")
            raise AssertionError("Expected HTTPException for non-wiki path but none raised")
        except HTTPException as exc:
            assert exc.status_code == 403, (
                f"Expected 403 for raw/sources path, got {exc.status_code}"
            )
