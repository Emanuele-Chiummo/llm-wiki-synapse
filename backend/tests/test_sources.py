"""
Sources view tests — GET /sources, /sources/content, /sources/raw, /sources/derived-pages,
DELETE /sources (nashsu/llm_wiki Sources tab backend).

Covers:
  T-SRC-001  GET /sources lists files + subdirs in a temp tree; truncation warning (I7)
  T-SRC-002  GET /sources returns empty list when directory does not exist
  T-SRC-003  GET /sources path traversal (../../etc/passwd) → 404 (cannot reach root)
  T-SRC-004  GET /sources/content returns text for .md/.txt
  T-SRC-005  GET /sources/content category mapping (.png → image, .pdf → pdf, .md → markdown)
  T-SRC-006  GET /sources/content ingested=True + page_ids for a source with a derived page
  T-SRC-007  GET /sources/content absent file → 404
  T-SRC-008  GET /sources/content traversal path → 404
  T-SRC-009  GET /sources/raw returns bytes + correct Content-Type for small png/txt
  T-SRC-010  GET /sources/raw oversize file → 413
  T-SRC-011  GET /sources/raw traversal path → 404
  T-SRC-012  GET /sources/raw absent file → 404
  T-SRC-013  GET /sources/derived-pages returns derived pages for a source; empty when none
  T-SRC-014  GET /sources/derived-pages traversal → 404
  T-SRC-015  DELETE /sources removes the raw file + cascades derived pages
  T-SRC-016  DELETE /sources traversal → 404
  T-SRC-017  DELETE /sources absent file → 404
  T-SRC-018  DELETE /sources source with no derived pages: file deleted, pages_deleted=0
  T-SRC-019  GET /sources content-type: correct MIME per extension
  T-SRC-020  TestOpenAPISpec: /sources paths in openapi.json (I8/D4)

Fixture pattern: reuses api_env + api_client from test_api.py (conftest-less approach —
both fixtures are defined in test_api.py and collected by pytest via conftest.py import-all).
"""

from __future__ import annotations

import json
import struct
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


# ── Shared fixture (mirrors api_env pattern from test_api.py) ─────────────────

@pytest.fixture()
async def src_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Set up an isolated Sources test environment:
    - Temporary vault with a raw/sources/ tree pre-populated
    - SQLite in-memory DB
    - FakeQdrantClient + FakeEmbeddingClient
    - FastAPI app with mocked lifespan
    """
    from contextlib import asynccontextmanager

    from app import config as cfg
    from app.embeddings import FakeEmbeddingClient, set_embedding_client
    from sqlalchemy import (
        BigInteger,
        Column,
        Float,
        Integer,
        MetaData,
        String,
        Table,
        Text,
    )
    from sqlalchemy import text as sa_text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    # ── Vault structure ───────────────────────────────────────────────────────
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    sources_dir = vault_root / "raw" / "sources"
    sources_dir.mkdir(parents=True)
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    log_md = wiki_dir / "log.md"
    log_md.write_text("---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n", encoding="utf-8")
    obsidian_dir = wiki_dir / ".obsidian"
    obsidian_dir.mkdir()
    (obsidian_dir / "app.json").write_text('{"legacyEditor": false}', encoding="utf-8")

    # Seed some source files
    (sources_dir / "note.md").write_text(
        "---\ntype: entity\ntitle: Note\n---\n\nBody.\n", encoding="utf-8"
    )
    (sources_dir / "plain.txt").write_text("Hello world\n", encoding="utf-8")

    # A minimal valid 1x1 PNG (PNG signature + IHDR + IDAT + IEND)
    _png_bytes = _make_minimal_png()
    (sources_dir / "image.png").write_bytes(_png_bytes)

    # A subdir
    sub_dir = sources_dir / "subdir"
    sub_dir.mkdir()
    (sub_dir / "child.txt").write_text("child content\n", encoding="utf-8")

    # ── Settings patch ────────────────────────────────────────────────────────
    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault-src")
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "raw_sources_dir", property(lambda self: sources_dir))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(type(cfg.settings), "log_md_path", property(lambda self: log_md))

    # ── SQLite in-memory DB ───────────────────────────────────────────────────
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    meta = MetaData()
    Table(
        "pages",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("file_path", Text, nullable=False),
        Column("title", Text, nullable=True),
        Column("type", Text, nullable=True),
        Column("sources", Text, nullable=True),  # JSON text in SQLite
        Column("tags", Text, nullable=True),
        Column("content_hash", String(64), nullable=False),
        Column("source_mtime_ns", BigInteger, nullable=True),
        Column("qdrant_point_id", String(36), nullable=True),
        Column("x", Float, nullable=True),
        Column("y", Float, nullable=True),
        Column("community", Integer, nullable=True),
        Column("pinned", Integer, nullable=False, server_default=sa_text("0")),
        Column("deleted_at", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("updated_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )
    Table(
        "vault_state",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False, unique=True),
        Column("data_version", Integer, nullable=False, default=0),
        Column("remote_mcp_enabled", Integer, nullable=False, server_default=sa_text("0")),
        Column("mcp_access_token_hash", Text, nullable=True),
        Column("mcp_allow_without_token", Integer, nullable=False, server_default=sa_text("0")),
        Column("clip_enabled_db", Integer, nullable=True),
        Column("clip_access_token", Text, nullable=True),
        Column("clip_allowed_origins_db", Text, nullable=True),
        Column("cli_oauth_token", Text, nullable=True),
        Column("searxng_url_db", Text, nullable=True),
        Column("searxng_categories_db", Text, nullable=True),
        Column("searxng_max_queries_db", Integer, nullable=True),
        Column("updated_at", Text, nullable=False),
    )
    Table(
        "edges",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("source_page_id", String(36), nullable=False),
        Column("target_page_id", String(36), nullable=False),
        Column("weight", Float, nullable=False),
    )
    Table(
        "links",
        meta,
        Column("id", String(36), primary_key=True),
        Column("source_page_id", String(36), nullable=False),
        Column("target_title", Text, nullable=False),
        Column("target_page_id", String(36), nullable=True),
        Column("dangling", Integer, nullable=False, server_default=sa_text("1")),
    )

    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # Seed vault_state
    async with session_factory() as session:
        await session.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                "VALUES (:id, :vault_id, 0, datetime('now'))"
            ),
            {"id": str(uuid.uuid4()), "vault_id": "test-vault-src"},
        )
        await session.commit()

    # ── Fake clients ──────────────────────────────────────────────────────────
    fake_emb = FakeEmbeddingClient(dim=8)
    set_embedding_client(fake_emb)

    class _FakeQdrant:
        def __init__(self) -> None:
            self.points: dict[str, Any] = {}

        async def get_collections(self) -> MagicMock:
            m = MagicMock()
            m.collections = [MagicMock(name="synapse_pages")]
            return m

        async def create_collection(self, *a: Any, **kw: Any) -> None:
            pass

        async def get_collection(self, *a: Any, **kw: Any) -> MagicMock:
            m = MagicMock()
            m.config.params.vectors = MagicMock()
            m.config.params.vectors.size = 8
            return m

        async def upsert(self, collection_name: str, points: list[Any]) -> None:
            for pt in points:
                self.points[str(pt.id)] = pt.payload or {}

        async def delete(self, collection_name: str, points_selector: Any) -> None:
            for pid in points_selector.points:
                self.points.pop(str(pid), None)

        async def query_points(self, **kw: Any) -> MagicMock:
            resp = MagicMock()
            resp.points = []
            return resp

    fake_qdrant = _FakeQdrant()

    # ── Patch db.get_session ──────────────────────────────────────────────────
    @asynccontextmanager
    async def patched_get_session():  # type: ignore[return]
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    monkeypatch.setattr("app.db.get_session", patched_get_session)
    monkeypatch.setattr("app.ingest.orchestrator.get_session", patched_get_session)
    monkeypatch.setattr("app.main.get_session", patched_get_session)
    monkeypatch.setattr("app.provider_config_service.get_session", patched_get_session)
    monkeypatch.setattr("app.rag.retrieval.get_session", patched_get_session)
    monkeypatch.setattr("app.sources.get_session", patched_get_session, raising=False)

    monkeypatch.setattr("app.qdrant_client.get_qdrant_client", lambda: fake_qdrant)
    monkeypatch.setattr("app.rag.retrieval.get_qdrant_client", lambda: fake_qdrant)
    monkeypatch.setattr(
        "app.ingest.orchestrator.upsert_point",
        lambda **kw: fake_qdrant.upsert(
            "synapse_pages",
            [
                type(
                    "Pt",
                    (),
                    {
                        "id": str(kw["page_id"]),
                        "vector": kw["vector"],
                        "payload": {
                            "file_path": kw["file_path"],
                            "title": kw["title"],
                            "type": kw["page_type"],
                        },
                    },
                )()
            ],
        ),
    )
    monkeypatch.setattr(
        "app.ingest.orchestrator.delete_point",
        lambda page_id: fake_qdrant.delete(
            "synapse_pages", type("Sel", (), {"points": [str(page_id)]})()
        ),
    )

    # ── FastAPI app with mocked lifespan ──────────────────────────────────────
    from fastapi import FastAPI

    from app.main import app

    @asynccontextmanager
    async def test_lifespan(application: FastAPI):  # type: ignore[override]
        yield

    app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

    yield {
        "app": app,
        "session_factory": session_factory,
        "qdrant": fake_qdrant,
        "vault_root": vault_root,
        "sources_dir": sources_dir,
        "wiki_dir": wiki_dir,
    }

    set_embedding_client(None)  # type: ignore[arg-type]


@pytest.fixture()
async def src_client(src_env: dict[str, Any]) -> AsyncClient:
    """httpx AsyncClient backed by the FastAPI test app."""
    async with AsyncClient(
        transport=ASGITransport(app=src_env["app"]),
        base_url="http://test",
    ) as client:
        yield client


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_minimal_png() -> bytes:
    """Return a valid 1x1 white PNG byte string (hardcoded minimal structure)."""
    # Minimal valid PNG bytes for a 1x1 white pixel
    import zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    # 1x1 white pixel RGBA as raw scanline: filter byte 0x00 + R G B
    raw = zlib.compress(b"\x00\xff\xff\xff")
    idat = _chunk(b"IDAT", raw)
    iend = _chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


async def _seed_derived_page(
    src_env: dict[str, Any],
    source_rel_path: str,
    page_title: str = "Derived Page",
) -> str:
    """Insert a wiki page row whose sources[] contains source_rel_path; return page UUID."""
    page_id = str(uuid.uuid4())
    session_factory = src_env["session_factory"]
    from sqlalchemy import text as sa_text

    async with session_factory() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, sources, content_hash, "
                " created_at, updated_at) "
                "VALUES (:id, :vault_id, :fp, :title, :type, :sources, :hash, "
                "        datetime('now'), datetime('now'))"
            ),
            {
                "id": page_id,
                "vault_id": "test-vault-src",
                "fp": f"wiki/entities/{page_title.lower().replace(' ', '-')}.md",
                "title": page_title,
                "type": "entity",
                "sources": json.dumps([source_rel_path]),
                "hash": "abc123",
            },
        )
        await sess.commit()
    return page_id


# ── T-SRC-001: GET /sources listing ──────────────────────────────────────────


class TestListSources:
    """T-SRC-001, T-SRC-002, T-SRC-003"""

    async def test_lists_files_and_subdirs(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-001: GET /sources returns files + subdirs with expected fields."""
        resp = await src_client.get("/sources")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "entries" in data
        assert "total" in data
        assert "truncated" in data
        assert data["truncated"] is False

        paths = {e["path"] for e in data["entries"]}
        # Root-level files
        assert "note.md" in paths
        assert "plain.txt" in paths
        assert "image.png" in paths
        # Subdir
        assert "subdir" in paths
        # Child file inside subdir
        assert "subdir/child.txt" in paths

    async def test_entries_have_expected_fields(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-001b: each entry has name, is_dir, and files have size_bytes/ext/mtime."""
        resp = await src_client.get("/sources")
        assert resp.status_code == 200
        entries = resp.json()["entries"]
        files = [e for e in entries if not e["is_dir"]]
        dirs = [e for e in entries if e["is_dir"]]
        assert files, "Expected at least one file entry"
        assert dirs, "Expected at least one dir entry"
        for f in files:
            assert "name" in f
            assert f["is_dir"] is False
            assert f["ext"] is not None
            assert f["size_bytes"] is not None
            assert f["mtime"] is not None
        for d in dirs:
            assert d["is_dir"] is True

    async def test_empty_when_dir_missing(
        self, src_client: AsyncClient, src_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-SRC-002: returns empty list when raw/sources/ does not exist."""
        from app import config as cfg
        from pathlib import Path

        missing_dir = src_env["vault_root"] / "raw" / "nonexistent"
        monkeypatch.setattr(type(cfg.settings), "raw_sources_dir", property(lambda self: missing_dir))

        resp = await src_client.get("/sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["entries"] == []
        assert data["total"] == 0

    async def test_traversal_path_cannot_reach_outside(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-003: GET /sources?path=../../etc/passwd — listing has no traversal (read-only).

        The listing endpoint has no 'path' param; it always lists the whole sources_dir.
        Traversal vectors are against /content, /raw, /derived-pages, DELETE — tested below.
        This test simply confirms the listing itself is safe (no 'path' param accepted).
        """
        # GET /sources does not accept a path param — all files come from sources_dir
        resp = await src_client.get("/sources")
        assert resp.status_code == 200
        # No file with 'passwd' or '/etc' in path
        entries = resp.json()["entries"]
        assert not any("/etc" in e["path"] or "passwd" in e["path"] for e in entries)


# ── T-SRC-004..T-SRC-008: GET /sources/content ───────────────────────────────


class TestSourceContent:
    """T-SRC-004, T-SRC-005, T-SRC-006, T-SRC-007, T-SRC-008"""

    async def test_returns_text_for_md(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-004: GET /sources/content returns text + is_text=True for .md."""
        resp = await src_client.get("/sources/content", params={"path": "note.md"})
        assert resp.status_code == 200, f"Expected 200: {resp.text}"
        data = resp.json()
        assert data["path"] == "note.md"
        assert data["category"] == "markdown"
        assert data["is_text"] is True
        assert data["text"] is not None
        assert "Body." in data["text"]

    async def test_returns_text_for_txt(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-004b: GET /sources/content returns text for .txt."""
        resp = await src_client.get("/sources/content", params={"path": "plain.txt"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["category"] == "text"
        assert data["is_text"] is True
        assert "Hello world" in data["text"]

    async def test_category_mapping_png(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-005: .png → category=image, is_text=False, text=None."""
        resp = await src_client.get("/sources/content", params={"path": "image.png"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["category"] == "image"
        assert data["is_text"] is False
        assert data["text"] is None

    async def test_category_mapping_for_extensions(
        self, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-005b: category helper maps extensions correctly."""
        from app.sources import _get_category

        assert _get_category(".md") == "markdown"
        assert _get_category(".txt") == "text"
        assert _get_category(".png") == "image"
        assert _get_category(".jpg") == "image"
        assert _get_category(".pdf") == "pdf"
        assert _get_category(".docx") == "document"
        assert _get_category(".xlsx") == "data"
        assert _get_category(".py") == "code"
        assert _get_category(".mp4") == "av"
        assert _get_category(".unknown_ext") == "other"

    async def test_ingested_and_page_ids_with_derived_page(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-006: ingested=True + page_ids populated when a derived page exists."""
        page_id = await _seed_derived_page(src_env, "note.md", "Note Derived")

        resp = await src_client.get("/sources/content", params={"path": "note.md"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] is True
        assert page_id in data["page_ids"]

    async def test_ingested_false_when_no_derived_pages(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-006b: ingested=False when no derived pages exist."""
        resp = await src_client.get("/sources/content", params={"path": "plain.txt"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] is False
        assert data["page_ids"] == []

    async def test_absent_file_returns_404(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-007: absent file → 404."""
        resp = await src_client.get("/sources/content", params={"path": "nonexistent.md"})
        assert resp.status_code == 404

    async def test_traversal_returns_404(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-008: traversal path (../../etc/passwd) → 404, never 500."""
        resp = await src_client.get("/sources/content", params={"path": "../../etc/passwd"})
        assert resp.status_code == 404
        assert resp.status_code != 500

    async def test_required_fields_present(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """GET /sources/content always returns the required schema fields."""
        resp = await src_client.get("/sources/content", params={"path": "note.md"})
        assert resp.status_code == 200
        data = resp.json()
        for field in ("path", "name", "ext", "size_bytes", "mtime", "category", "is_text",
                      "ingested", "page_ids"):
            assert field in data, f"Missing field: {field!r}"


# ── T-SRC-009..T-SRC-012: GET /sources/raw ───────────────────────────────────


class TestSourceRaw:
    """T-SRC-009, T-SRC-010, T-SRC-011, T-SRC-012"""

    async def test_returns_bytes_and_content_type_for_png(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-009: GET /sources/raw returns bytes + correct Content-Type for PNG."""
        resp = await src_client.get("/sources/raw", params={"path": "image.png"})
        assert resp.status_code == 200, f"Expected 200: {resp.text}"
        assert "image/png" in resp.headers.get("content-type", "")
        assert len(resp.content) > 0

    async def test_returns_bytes_and_content_type_for_txt(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-009b: GET /sources/raw returns bytes + correct Content-Type for txt."""
        resp = await src_client.get("/sources/raw", params={"path": "plain.txt"})
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "text" in ct or "plain" in ct
        assert b"Hello world" in resp.content

    async def test_oversize_file_returns_413(
        self, src_client: AsyncClient, src_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-SRC-010: file > SOURCES_RAW_MAX_BYTES → 413."""
        import app.sources as src_module

        # Patch the max to 1 byte so our tiny PNG triggers it
        monkeypatch.setattr(src_module, "SOURCES_RAW_MAX_BYTES", 1)

        resp = await src_client.get("/sources/raw", params={"path": "image.png"})
        assert resp.status_code == 413

    async def test_traversal_returns_404(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-011: traversal path → 404."""
        resp = await src_client.get("/sources/raw", params={"path": "../../etc/passwd"})
        assert resp.status_code == 404

    async def test_absent_file_returns_404(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-012: absent file → 404."""
        resp = await src_client.get("/sources/raw", params={"path": "no_such_file.png"})
        assert resp.status_code == 404

    async def test_inline_content_disposition(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """GET /sources/raw sets Content-Disposition: inline."""
        resp = await src_client.get("/sources/raw", params={"path": "plain.txt"})
        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "inline" in cd


# ── T-SRC-013..T-SRC-014: GET /sources/derived-pages ─────────────────────────


class TestSourceDerivedPages:
    """T-SRC-013, T-SRC-014"""

    async def test_returns_derived_pages(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-013: GET /sources/derived-pages returns the derived page(s)."""
        page_id = await _seed_derived_page(src_env, "note.md", "Derived Note")

        resp = await src_client.get("/sources/derived-pages", params={"path": "note.md"})
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        ids = [p["id"] for p in data]
        assert page_id in ids

    async def test_returns_empty_when_no_derived_pages(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-013b: empty list when no derived pages exist for this source."""
        resp = await src_client.get("/sources/derived-pages", params={"path": "plain.txt"})
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_traversal_returns_404(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-014: traversal path → 404."""
        resp = await src_client.get("/sources/derived-pages", params={"path": "../../etc/passwd"})
        assert resp.status_code == 404

    async def test_derived_page_fields(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """Each derived page entry has id, file_path (+ optional title, page_type)."""
        await _seed_derived_page(src_env, "note.md", "Fields Test Page")
        resp = await src_client.get("/sources/derived-pages", params={"path": "note.md"})
        assert resp.status_code == 200
        pages = resp.json()
        assert len(pages) >= 1
        for p in pages:
            assert "id" in p
            assert "file_path" in p


# ── T-SRC-015..T-SRC-018: DELETE /sources ────────────────────────────────────


class TestDeleteSource:
    """T-SRC-015, T-SRC-016, T-SRC-017, T-SRC-018"""

    async def test_deletes_file_and_cascades_derived_pages(
        self, src_client: AsyncClient, src_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-SRC-015: DELETE /sources removes the raw file + soft-deletes derived pages."""
        from sqlalchemy import text as sa_text

        # Seed a derived page
        page_id = await _seed_derived_page(src_env, "plain.txt", "To Delete")
        assert (src_env["sources_dir"] / "plain.txt").exists()

        # Patch cascade_delete to avoid its DB machinery (avoids Postgres UUID ops in SQLite)
        cascade_calls: list[str] = []

        async def _fake_cascade(page_uuid):  # type: ignore[override]
            cascade_calls.append(str(page_uuid))
            # Soft-delete the page in our SQLite DB
            session_factory = src_env["session_factory"]
            async with session_factory() as sess:
                await sess.execute(
                    sa_text("UPDATE pages SET deleted_at = datetime('now') WHERE id = :id"),
                    {"id": str(page_uuid)},
                )
                await sess.commit()
            from app.ops.cascade_delete import CascadeResult
            import uuid as _uuid
            return CascadeResult(
                deleted_page_id=page_uuid,
                wikilinks_cleaned=0,
                index_entry_removed=False,
                shared_entity_warnings=[],
                files_written=0,
                data_version_after=1,
            )

        monkeypatch.setattr("app.sources._cascade_delete_page", _fake_cascade)

        resp = await src_client.delete("/sources", params={"path": "plain.txt"})
        assert resp.status_code == 200, f"Expected 200: {resp.text}"
        data = resp.json()
        assert data["deleted_source"] == "plain.txt"
        assert data["pages_deleted"] == 1

        # File must be gone from disk
        assert not (src_env["sources_dir"] / "plain.txt").exists()

        # cascade_delete was called with the correct page UUID
        assert str(page_id) in cascade_calls

    async def test_traversal_returns_404(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-016: DELETE /sources traversal → 404."""
        resp = await src_client.delete("/sources", params={"path": "../../etc/passwd"})
        assert resp.status_code == 404

    async def test_absent_file_returns_404(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-017: DELETE /sources for non-existent file → 404."""
        resp = await src_client.delete("/sources", params={"path": "no_such_file.txt"})
        assert resp.status_code == 404

    async def test_no_derived_pages_still_deletes_file(
        self, src_client: AsyncClient, src_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-SRC-018: source with no derived pages — file deleted, pages_deleted=0."""
        # image.png has no derived pages seeded
        assert (src_env["sources_dir"] / "image.png").exists()

        # Stub _bump_version_no_derived to avoid DB calls in the no-derived path
        bumped: list[bool] = []

        async def _fake_bump() -> None:
            bumped.append(True)

        monkeypatch.setattr("app.sources._bump_version_no_derived", _fake_bump)

        resp = await src_client.delete("/sources", params={"path": "image.png"})
        assert resp.status_code == 200, f"Expected 200: {resp.text}"
        data = resp.json()
        assert data["deleted_source"] == "image.png"
        assert data["pages_deleted"] == 0
        assert not (src_env["sources_dir"] / "image.png").exists()
        assert bumped, "_bump_version_no_derived must be called when no derived pages"


# ── T-SRC-019: MIME type mapping ──────────────────────────────────────────────


class TestMimeMapping:
    """T-SRC-019"""

    def test_mime_for_ext(self) -> None:
        """_mime_for_ext returns correct MIME types for key extensions."""
        from app.sources import _mime_for_ext

        assert _mime_for_ext(".png") == "image/png"
        assert _mime_for_ext(".jpg") == "image/jpeg"
        assert _mime_for_ext(".jpeg") == "image/jpeg"
        assert _mime_for_ext(".pdf") == "application/pdf"
        assert _mime_for_ext(".txt") == "text/plain"
        assert _mime_for_ext(".md") == "text/markdown"
        # Unknown → fallback (not empty)
        ct = _mime_for_ext(".xyz_unknown")
        assert ct  # non-empty string


# ── T-SRC-020: OpenAPI spec has /sources paths ────────────────────────────────


class TestOpenAPISourcesPaths:
    """T-SRC-020 — I8/D4"""

    def test_sources_paths_in_openapi_json(self) -> None:
        """T-SRC-020: /sources, /sources/content, /sources/raw, /sources/derived-pages
        must be in docs/api/openapi.json (I8)."""
        import json
        from pathlib import Path

        p = Path(__file__).resolve().parent.parent.parent / "docs" / "api" / "openapi.json"
        if not p.exists():
            pytest.skip("openapi.json not generated yet — run: cd backend && python scripts/generate_openapi.py")

        data = json.loads(p.read_text(encoding="utf-8"))
        paths = data.get("paths", {})
        for required in ("/sources", "/sources/content", "/sources/raw", "/sources/derived-pages"):
            assert required in paths, (
                f"openapi.json must include {required!r} (I8/D4). "
                f"Present paths: {sorted(p for p in paths if p.startswith('/sources'))}"
            )
