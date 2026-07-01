"""
Sources view tests — GET /sources, /sources/content, /sources/raw, /sources/derived-pages,
DELETE /sources, POST /sources/ingest-all, GET /sources/ingest-all/status.

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
  T-SRC-021  POST /sources/ingest-all: 202 candidate_files=N (supported files only, nested incl)
  T-SRC-022  POST /sources/ingest-all: driver calls ingest_file SERIALLY, once per file
  T-SRC-023  POST /sources/ingest-all: single-flight → 409 on second call while running
  T-SRC-024  POST /sources/ingest-all: empty directory → {started:false, candidate_files:0}
  T-SRC-025  POST /sources/ingest-all: cap truncates list + logs warning
  T-SRC-026  GET /sources/ingest-all/status: reflects running/done/total counters

Fixture pattern: reuses src_env + src_client defined below.
"""

from __future__ import annotations

import asyncio
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


# ── T-SRC-021..T-SRC-026: POST /sources/ingest-all ───────────────────────────


@pytest.fixture()
async def ingest_all_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Isolated environment for ingest-all tests.

    - Temp raw/sources/ tree with supported + unsupported files + nested subdir.
    - Module-level single-flight flag reset to False before each test.
    - ingest_file patched to a controllable async mock (never calls real LLM/DB/Qdrant).
    """
    import app.sources as src_module
    from app import config as cfg

    # ── Reset single-flight state before each test ────────────────────────────
    monkeypatch.setattr(src_module, "_ingest_all_running", False)
    monkeypatch.setattr(src_module, "_ingest_all_done", 0)
    monkeypatch.setattr(src_module, "_ingest_all_total", 0)

    # ── Vault / sources_dir ───────────────────────────────────────────────────
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    sources_dir = vault_root / "raw" / "sources"
    sources_dir.mkdir(parents=True)

    # Supported files (text)
    (sources_dir / "alpha.md").write_text("# Alpha\n", encoding="utf-8")
    (sources_dir / "beta.txt").write_text("beta\n", encoding="utf-8")

    # Nested subdir with a supported file
    sub = sources_dir / "sub"
    sub.mkdir()
    (sub / "gamma.md").write_text("# Gamma\n", encoding="utf-8")

    # Unsupported file (.DS_Store) — must NOT be included
    (sources_dir / ".DS_Store").write_bytes(b"\x00\x01\x02")

    # Unsupported extension (.xyz) — must NOT be included
    (sources_dir / "data.xyz").write_text("nope\n", encoding="utf-8")

    # ── Settings patch ────────────────────────────────────────────────────────
    monkeypatch.setattr(type(cfg.settings), "raw_sources_dir", property(lambda self: sources_dir))

    return {
        "sources_dir": sources_dir,
        "vault_root": vault_root,
    }


class TestIngestAll:
    """T-SRC-021..T-SRC-026 — POST /sources/ingest-all + GET /sources/ingest-all/status."""

    async def test_returns_202_with_candidate_count(
        self,
        ingest_all_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-021: 202 + candidate_files = count of SUPPORTED files (nested incl, junk excl)."""
        import app.sources as src_module
        from contextlib import asynccontextmanager
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.main import app

        @asynccontextmanager
        async def test_lifespan(application: FastAPI):  # type: ignore[override]
            yield

        app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

        # Patch the driver directly to record calls without actually ingesting
        patched_driver_calls: list[list[Any]] = []

        async def _patched_driver(candidates: list[Any]) -> None:
            patched_driver_calls.append(list(candidates))
            # Simulate completion: clear flag and advance done counter
            src_module._ingest_all_running = False
            src_module._ingest_all_done = len(candidates)

        monkeypatch.setattr(src_module, "_ingest_all_driver", _patched_driver)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/sources/ingest-all")

        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["started"] is True
        # 3 supported: alpha.md, beta.txt, sub/gamma.md
        assert data["candidate_files"] == 3, (
            f"Expected 3 supported files, got {data['candidate_files']}"
        )

        # Drain the event loop so the fire-and-forget create_task runs the patched driver.
        await asyncio.sleep(0)

        # Driver received the same 3 paths
        assert len(patched_driver_calls) == 1
        assert len(patched_driver_calls[0]) == 3

    async def test_driver_calls_ingest_file_serially(
        self,
        ingest_all_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-022: driver awaits ingest_file ONCE PER FILE and calls them SERIALLY (no overlap).

        Serial enforcement is tested by asserting:
        1. ingest_file is called exactly N times (once per supported file).
        2. Each call completes before the next starts — verified by tracking an 'in_flight'
           counter that must never exceed 1.
        """
        import app.sources as src_module
        from app.sources import _collect_ingest_all_candidates, _ingest_all_driver

        sources_dir = ingest_all_env["sources_dir"]
        candidates = _collect_ingest_all_candidates(sources_dir, max_files=200)
        assert len(candidates) == 3, f"Expected 3 candidates, got {len(candidates)}"

        call_order: list[str] = []
        in_flight_max: list[int] = [0]
        in_flight_current: list[int] = [0]

        async def _fake_ingest(file_path: str) -> Any:
            in_flight_current[0] += 1
            if in_flight_current[0] > in_flight_max[0]:
                in_flight_max[0] = in_flight_current[0]
            call_order.append(Path(file_path).name)
            # Yield to the event loop to simulate async I/O
            await asyncio.sleep(0)
            in_flight_current[0] -= 1
            result = MagicMock()
            result.status = "completed"
            result.page_id = "fake-uuid"
            return result

        # Patch ingest_file inside the sources module (where _ingest_all_driver imports it)
        monkeypatch.setattr(
            "app.ingest.orchestrator.ingest_file", _fake_ingest, raising=False
        )

        # Run the driver directly (not via HTTP) to avoid the fire-and-forget task
        await _ingest_all_driver(candidates)

        # All 3 files processed
        assert len(call_order) == 3, f"Expected 3 calls, got {call_order}"
        # Serial: in-flight count never exceeded 1
        assert in_flight_max[0] == 1, (
            f"Files were processed concurrently! max in-flight={in_flight_max[0]}"
        )
        # Flag cleared
        assert src_module._ingest_all_running is False
        assert src_module._ingest_all_done == 3

    async def test_single_flight_returns_409(
        self,
        ingest_all_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-023: second POST while running → 409 {detail: 'ingest-all already running'}."""
        import app.sources as src_module
        from contextlib import asynccontextmanager
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.main import app

        @asynccontextmanager
        async def test_lifespan(application: FastAPI):  # type: ignore[override]
            yield

        app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

        # Simulate a scan already running
        monkeypatch.setattr(src_module, "_ingest_all_running", True)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/sources/ingest-all")

        assert resp.status_code == 409
        assert "already running" in resp.json()["detail"]

    async def test_empty_directory_returns_started_false(
        self,
        ingest_all_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-024: empty raw/sources/ → {started: false, candidate_files: 0}."""
        import app.sources as src_module
        from app import config as cfg
        from contextlib import asynccontextmanager
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.main import app

        @asynccontextmanager
        async def test_lifespan(application: FastAPI):  # type: ignore[override]
            yield

        app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

        # Point sources_dir at an empty directory
        empty_dir = ingest_all_env["vault_root"] / "raw" / "empty_sources"
        empty_dir.mkdir(parents=True)
        monkeypatch.setattr(
            type(cfg.settings), "raw_sources_dir", property(lambda self: empty_dir)
        )

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/sources/ingest-all")

        assert resp.status_code == 202
        data = resp.json()
        assert data["started"] is False
        assert data["candidate_files"] == 0
        # Single-flight flag must NOT be set when nothing was started
        assert src_module._ingest_all_running is False

    async def test_cap_truncates_candidates_and_logs(
        self,
        ingest_all_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """T-SRC-025: more than max files → truncated + logged."""
        import app.sources as src_module
        import logging

        sources_dir = ingest_all_env["sources_dir"]

        # Lower the cap to 2 (there are 3 supported files)
        monkeypatch.setattr(src_module, "SOURCES_INGEST_ALL_MAX", 2)

        with caplog.at_level(logging.WARNING, logger="app.sources"):
            candidates = src_module._collect_ingest_all_candidates(sources_dir, max_files=2)

        assert len(candidates) == 2, f"Expected 2 (capped), got {len(candidates)}"
        assert any("truncated" in r.message for r in caplog.records), (
            "Expected a truncation WARNING log"
        )

    async def test_status_endpoint_reflects_counters(
        self,
        ingest_all_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-026: GET /sources/ingest-all/status returns running/done/total."""
        import app.sources as src_module
        from contextlib import asynccontextmanager
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from app.main import app

        @asynccontextmanager
        async def test_lifespan(application: FastAPI):  # type: ignore[override]
            yield

        app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

        # Simulate a scan in progress with 2/5 files done
        monkeypatch.setattr(src_module, "_ingest_all_running", True)
        monkeypatch.setattr(src_module, "_ingest_all_done", 2)
        monkeypatch.setattr(src_module, "_ingest_all_total", 5)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/sources/ingest-all/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["running"] is True
        assert data["done"] == 2
        assert data["total"] == 5

    def test_extension_filter_matches_upload_accepted(self) -> None:
        """T-SRC-021b: _is_ingest_all_supported matches _UPLOAD_ACCEPTED (exact match)."""
        from app.sources import _is_ingest_all_supported
        from app.upload import _UPLOAD_ACCEPTED

        for ext in _UPLOAD_ACCEPTED:
            fake = Path(f"file{ext}")
            assert _is_ingest_all_supported(fake), f"Expected {ext!r} to be supported"

        # Junk not in the set
        assert not _is_ingest_all_supported(Path("file.DS_Store"))
        assert not _is_ingest_all_supported(Path("file.xyz"))
        assert not _is_ingest_all_supported(Path("file"))


# ── T-SRC-020b: OpenAPI spec has /sources/ingest-all paths ────────────────────


class TestOpenAPIIngestAllPaths:
    """T-SRC-020b — ingest-all endpoints in openapi.json (I8/D4)"""

    def test_ingest_all_paths_in_openapi_json(self) -> None:
        """POST /sources/ingest-all + GET /sources/ingest-all/status must be in openapi.json."""
        import json
        from pathlib import Path

        p = Path(__file__).resolve().parent.parent.parent / "docs" / "api" / "openapi.json"
        if not p.exists():
            pytest.skip(
                "openapi.json not generated yet — run: cd backend && python scripts/generate_openapi.py"
            )

        data = json.loads(p.read_text(encoding="utf-8"))
        paths = data.get("paths", {})
        for required in ("/sources/ingest-all", "/sources/ingest-all/status"):
            assert required in paths, (
                f"openapi.json must include {required!r} (I8/D4). "
                f"Present /sources/* paths: {sorted(p for p in paths if p.startswith('/sources'))}"
            )
