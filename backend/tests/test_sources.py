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
  T-SRC-022  POST /sources/ingest-all: driver calls ingest_file with BOUNDED concurrency, once/file
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
from unittest.mock import MagicMock

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
        LargeBinary,
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
        Column("cli_oauth_token_encrypted", LargeBinary, nullable=True),
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
    from app.main import app
    from fastapi import FastAPI

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

    async def test_hidden_files_and_dirs_excluded(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-001c: dotfiles (.DS_Store) and hidden dirs are never listed under raw/sources."""
        sources_dir: Path = src_env["sources_dir"]
        (sources_dir / ".DS_Store").write_bytes(b"\x00\x01\x02")
        hidden_dir = sources_dir / ".hidden"
        hidden_dir.mkdir()
        (hidden_dir / "secret.txt").write_text("nope\n", encoding="utf-8")

        resp = await src_client.get("/sources")
        assert resp.status_code == 200
        paths = {e["path"] for e in resp.json()["entries"]}
        names = {e["name"] for e in resp.json()["entries"]}
        # Junk dotfile and hidden directory (and anything inside it) must be excluded.
        assert ".DS_Store" not in names
        assert ".hidden" not in names
        assert not any(p.startswith(".hidden") for p in paths)
        # Real content is still listed.
        assert "note.md" in paths

    async def test_empty_when_dir_missing(
        self, src_client: AsyncClient, src_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-SRC-002: returns empty list when raw/sources/ does not exist."""

        from app import config as cfg

        missing_dir = src_env["vault_root"] / "raw" / "nonexistent"
        monkeypatch.setattr(
            type(cfg.settings), "raw_sources_dir", property(lambda self: missing_dir)
        )

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

    async def test_category_mapping_for_extensions(self, src_env: dict[str, Any]) -> None:
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
        for field in (
            "path",
            "name",
            "ext",
            "size_bytes",
            "mtime",
            "category",
            "is_text",
            "ingested",
            "page_ids",
        ):
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
            pytest.skip(
                "openapi.json not generated yet — run: cd backend && python scripts/generate_openapi.py"
            )

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
        from contextlib import asynccontextmanager

        import app.sources as src_module
        from app.main import app
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

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

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/sources/ingest-all")

        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["started"] is True
        # 3 supported: alpha.md, beta.txt, sub/gamma.md
        assert (
            data["candidate_files"] == 3
        ), f"Expected 3 supported files, got {data['candidate_files']}"

        # Drain the event loop so the fire-and-forget create_task runs the patched driver.
        await asyncio.sleep(0)

        # Driver received the same 3 paths
        assert len(patched_driver_calls) == 1
        assert len(patched_driver_calls[0]) == 3

    async def test_driver_ingests_with_bounded_concurrency(
        self,
        ingest_all_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-022: driver awaits ingest_file ONCE PER FILE with BOUNDED concurrency (I7).

        Concurrency guarantees are tested by asserting:
        1. ingest_file is called exactly N times (once per supported file).
        2. Files ARE processed concurrently (max in-flight > 1 — proves the speedup).
        3. Concurrency is BOUNDED — max in-flight never exceeds SOURCES_INGEST_ALL_CONCURRENCY
           (I7: never unbounded).
        """
        import app.sources as src_module
        from app.sources import (
            SOURCES_INGEST_ALL_CONCURRENCY,
            _collect_ingest_all_candidates,
            _ingest_all_driver,
        )

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
        monkeypatch.setattr("app.ingest.orchestrator.ingest_file", _fake_ingest, raising=False)

        # Run the driver directly (not via HTTP) to avoid the fire-and-forget task
        await _ingest_all_driver(candidates)

        # All 3 files processed exactly once
        assert len(call_order) == 3, f"Expected 3 calls, got {call_order}"
        assert sorted(call_order) == sorted({c.name for c in candidates})
        # Bounded (I7): in-flight never exceeded the configured concurrency cap.
        assert in_flight_max[0] <= SOURCES_INGEST_ALL_CONCURRENCY, (
            f"Concurrency unbounded! max in-flight={in_flight_max[0]} "
            f"> cap={SOURCES_INGEST_ALL_CONCURRENCY}"
        )
        # With cap>=2 and 3 candidates, files should overlap (proves the parallel speedup).
        if SOURCES_INGEST_ALL_CONCURRENCY >= 2:
            assert (
                in_flight_max[0] >= 2
            ), f"Expected concurrent processing, but max in-flight={in_flight_max[0]}"
        # Flag cleared
        assert src_module._ingest_all_running is False
        assert src_module._ingest_all_done == 3

    async def test_single_flight_returns_409(
        self,
        ingest_all_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-023: second POST while running → 409 {detail: 'ingest-all already running'}."""
        from contextlib import asynccontextmanager

        import app.sources as src_module
        from app.main import app
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        @asynccontextmanager
        async def test_lifespan(application: FastAPI):  # type: ignore[override]
            yield

        app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

        # Simulate a scan already running
        monkeypatch.setattr(src_module, "_ingest_all_running", True)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post("/sources/ingest-all")

        assert resp.status_code == 409
        assert "already running" in resp.json()["detail"]

    async def test_empty_directory_returns_started_false(
        self,
        ingest_all_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-024: empty raw/sources/ → {started: false, candidate_files: 0}."""
        from contextlib import asynccontextmanager

        import app.sources as src_module
        from app import config as cfg
        from app.main import app
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        @asynccontextmanager
        async def test_lifespan(application: FastAPI):  # type: ignore[override]
            yield

        app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

        # Point sources_dir at an empty directory
        empty_dir = ingest_all_env["vault_root"] / "raw" / "empty_sources"
        empty_dir.mkdir(parents=True)
        monkeypatch.setattr(type(cfg.settings), "raw_sources_dir", property(lambda self: empty_dir))

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
        import logging

        import app.sources as src_module

        sources_dir = ingest_all_env["sources_dir"]

        # Lower the cap to 2 (there are 3 supported files)
        monkeypatch.setattr(src_module, "SOURCES_INGEST_ALL_MAX", 2)

        with caplog.at_level(logging.WARNING, logger="app.sources"):
            candidates = src_module._collect_ingest_all_candidates(sources_dir, max_files=2)

        assert len(candidates) == 2, f"Expected 2 (capped), got {len(candidates)}"
        assert any(
            "truncated" in r.message for r in caplog.records
        ), "Expected a truncation WARNING log"

    async def test_status_endpoint_reflects_counters(
        self,
        ingest_all_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-026: GET /sources/ingest-all/status returns running/done/total."""
        from contextlib import asynccontextmanager

        import app.sources as src_module
        from app.main import app
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        @asynccontextmanager
        async def test_lifespan(application: FastAPI):  # type: ignore[override]
            yield

        app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

        # Simulate a scan in progress with 2/5 files done
        monkeypatch.setattr(src_module, "_ingest_all_running", True)
        monkeypatch.setattr(src_module, "_ingest_all_done", 2)
        monkeypatch.setattr(src_module, "_ingest_all_total", 5)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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


# ── T-SRC-027..T-SRC-030: S1 — folder upload with rel_dir ────────────────────
#
# These tests exercise the new optional rel_dir form field in POST /ingest/upload.
# They call the endpoint directly via httpx (no real DB/provider needed — the upload
# handler writes the file and returns 202; the watcher/ingest is mocked away).


class TestFolderUploadRelDir:
    """
    T-SRC-027  rel_dir present → file written under raw/sources/<rel_dir>/<name>
    T-SRC-028  rel_dir=None    → file written flat (existing behaviour, unchanged)
    T-SRC-029  rel_dir with traversal '..' → 422
    T-SRC-030  rel_dir with backslash separator → 422
    """

    async def test_upload_with_rel_dir_writes_to_subdir(
        self,
        src_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-027: file uploaded with rel_dir is written under the correct subdirectory."""
        from httpx import ASGITransport, AsyncClient

        # Disable rate limiting for test
        monkeypatch.setattr("app.rate_limit.rate_limit", lambda: None, raising=False)

        sources_dir: Path = src_env["sources_dir"]
        app = src_env["app"]

        content = b"# Folder upload test\n"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/ingest/upload",
                files={"file": ("upload_test.md", content, "text/markdown")},
                data={"rel_dir": "projects/notes"},
            )

        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        data = resp.json()
        # The returned file_path should include the rel_dir segment
        assert (
            "projects/notes" in data["file_path"]
        ), f"Expected 'projects/notes' in file_path but got: {data['file_path']!r}"
        # File must exist on disk at the correct location
        expected_path = sources_dir / "projects" / "notes" / "upload_test.md"
        assert expected_path.exists(), (
            f"Expected file at {expected_path} but it does not exist. "
            f"sources_dir contents: {list(sources_dir.rglob('*'))}"
        )
        assert expected_path.read_bytes() == content

    async def test_upload_without_rel_dir_writes_flat(
        self,
        src_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-028: file uploaded WITHOUT rel_dir still writes flat (behaviour unchanged)."""
        from httpx import ASGITransport, AsyncClient

        monkeypatch.setattr("app.rate_limit.rate_limit", lambda: None, raising=False)

        sources_dir: Path = src_env["sources_dir"]
        app = src_env["app"]

        content = b"# Flat upload test\n"
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/ingest/upload",
                files={"file": ("flat_upload.md", content, "text/markdown")},
                # no rel_dir field — omit entirely
            )

        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
        # File written flat (directly under sources_dir)
        expected_path = sources_dir / "flat_upload.md"
        assert expected_path.exists(), f"Expected file at {expected_path} but it does not exist."
        assert expected_path.read_bytes() == content

    async def test_upload_rel_dir_traversal_rejected(
        self,
        src_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-029: rel_dir containing '..' → 422 (path traversal rejected)."""
        from httpx import ASGITransport, AsyncClient

        monkeypatch.setattr("app.rate_limit.rate_limit", lambda: None, raising=False)

        app = src_env["app"]
        content = b"# traversal attempt\n"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/ingest/upload",
                files={"file": ("evil.md", content, "text/markdown")},
                data={"rel_dir": "../../etc"},
            )

        assert (
            resp.status_code == 422
        ), f"Expected 422 for traversal rel_dir, got {resp.status_code}: {resp.text}"

    async def test_upload_rel_dir_dot_dot_segment_rejected(
        self,
        src_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-029b: rel_dir with a '..' segment among valid segments → 422."""
        from httpx import ASGITransport, AsyncClient

        monkeypatch.setattr("app.rate_limit.rate_limit", lambda: None, raising=False)

        app = src_env["app"]
        content = b"# traversal attempt\n"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/ingest/upload",
                files={"file": ("evil.md", content, "text/markdown")},
                data={"rel_dir": "valid/../../etc"},
            )

        assert (
            resp.status_code == 422
        ), f"Expected 422 for traversal rel_dir, got {resp.status_code}: {resp.text}"


# ── T-SRC-031: S1 — _sanitize_rel_dir unit tests ─────────────────────────────


class TestSanitizeRelDir:
    """Unit tests for _sanitize_rel_dir (S1 helper)."""

    def test_valid_single_segment(self) -> None:
        """Single valid segment is returned as-is."""
        from app.routers.ingest import _sanitize_rel_dir

        assert _sanitize_rel_dir("projects") == "projects"

    def test_valid_nested_segments(self) -> None:
        """Nested valid segments are joined with forward slash."""
        from app.routers.ingest import _sanitize_rel_dir

        assert _sanitize_rel_dir("projects/notes") == "projects/notes"
        assert _sanitize_rel_dir("a/b/c") == "a/b/c"

    def test_dot_dot_rejected(self) -> None:
        """'..' segment → HTTPException(422)."""
        from app.routers.ingest import _sanitize_rel_dir
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _sanitize_rel_dir("..")
        assert exc_info.value.status_code == 422

    def test_dot_segment_rejected(self) -> None:
        """'.' segment → HTTPException(422)."""
        from app.routers.ingest import _sanitize_rel_dir
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _sanitize_rel_dir(".")
        assert exc_info.value.status_code == 422

    def test_traversal_in_middle_rejected(self) -> None:
        """'valid/../secret' → HTTPException(422) (..' segment caught regardless of position)."""
        from app.routers.ingest import _sanitize_rel_dir
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _sanitize_rel_dir("valid/../secret")
        assert exc_info.value.status_code == 422

    def test_empty_string_rejected(self) -> None:
        """Empty string → HTTPException(422)."""
        from app.routers.ingest import _sanitize_rel_dir
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _sanitize_rel_dir("")
        assert exc_info.value.status_code == 422

    def test_only_slashes_rejected(self) -> None:
        """A string with only slashes (no valid segments) → HTTPException(422)."""
        from app.routers.ingest import _sanitize_rel_dir
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            _sanitize_rel_dir("///")
        assert exc_info.value.status_code == 422

    def test_leading_trailing_slashes_stripped(self) -> None:
        """Leading/trailing slashes are tolerated and stripped from output."""
        from app.routers.ingest import _sanitize_rel_dir

        result = _sanitize_rel_dir("/projects/notes/")
        assert result == "projects/notes"


# ── T-SRC-032..T-SRC-035: S2 — directory delete ──────────────────────────────
#
# These tests exercise DELETE /sources?path=<dir> (the new S2/B3b path).
# The cascade_delete machinery is patched (same as T-SRC-015) to avoid Postgres UUID ops.


class TestDeleteSourceDirectory:
    """
    T-SRC-032  Directory delete cascades each contained file + removes dirs from disk.
    T-SRC-033  Directory delete respects SOURCES_DELETE_MAX_FILES cap → 409.
    T-SRC-034  Single-file delete unchanged (dispatch on is_dir=False; backward compat).
    T-SRC-035  Directory delete on empty directory returns files_deleted=0, pages_cascaded=0.
    """

    async def test_directory_delete_cascades_files_and_removes_dirs(
        self,
        src_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-032: DELETE /sources?path=subdir removes files, cascades pages, removes dir."""
        from sqlalchemy import text as sa_text

        sources_dir: Path = src_env["sources_dir"]
        # Seed a derived page for subdir/child.txt (already present in src_env fixture)
        page_id = await _seed_derived_page(src_env, "subdir/child.txt", "Child Derived")

        cascade_calls: list[str] = []

        async def _fake_cascade(page_uuid: uuid.UUID) -> None:  # type: ignore[override]
            cascade_calls.append(str(page_uuid))
            session_factory = src_env["session_factory"]
            async with session_factory() as sess:
                await sess.execute(
                    sa_text("UPDATE pages SET deleted_at = datetime('now') WHERE id = :id"),
                    {"id": str(page_uuid)},
                )
                await sess.commit()

            from app.ops.cascade_delete import CascadeResult

            return CascadeResult(
                deleted_page_id=page_uuid,
                wikilinks_cleaned=0,
                index_entry_removed=False,
                shared_entity_warnings=[],
                files_written=0,
                data_version_after=1,
            )

        monkeypatch.setattr("app.sources._cascade_delete_page", _fake_cascade)

        # stub _bump_version_no_derived (async) for files with no derived pages
        async def _fake_bump_noop() -> None:
            pass

        monkeypatch.setattr("app.sources._bump_version_no_derived", _fake_bump_noop)

        async with AsyncClient(
            transport=ASGITransport(app=src_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.delete("/sources", params={"path": "subdir"})

        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()

        # deleted_source should be the directory rel path
        assert data["deleted_source"] == "subdir"
        # 1 file in subdir (child.txt)
        assert data["files_deleted"] == 1
        # 1 derived page cascaded
        assert data["pages_cascaded"] == 1
        # backward-compat field
        assert data["pages_deleted"] == 1

        # Directory and file must be gone from disk
        assert not (sources_dir / "subdir" / "child.txt").exists()
        assert not (sources_dir / "subdir").exists()

        # Cascade was called with the correct page UUID
        assert str(page_id) in cascade_calls

    async def test_directory_delete_cap_returns_409(
        self,
        src_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-033: directory with > SOURCES_DELETE_MAX_FILES → 409 (I7)."""
        import app.sources as src_module

        sources_dir: Path = src_env["sources_dir"]

        # Create a test directory with 3 files, then lower the cap to 2
        test_dir = sources_dir / "many_files"
        test_dir.mkdir()
        for i in range(3):
            (test_dir / f"file{i}.txt").write_text(f"content {i}\n", encoding="utf-8")

        # Lower the cap so 3 files exceeds it
        monkeypatch.setattr(src_module, "SOURCES_DELETE_MAX_FILES", 2)

        async with AsyncClient(
            transport=ASGITransport(app=src_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.delete("/sources", params={"path": "many_files"})

        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
        detail = resp.json()["detail"]
        assert "SOURCES_DELETE_MAX_FILES" in detail or "files" in detail.lower()

        # Directory must still exist (no partial deletion before the cap check)
        assert test_dir.exists()

    async def test_single_file_delete_unchanged_after_s2(
        self,
        src_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-034: single-file delete still works exactly as before (backward compat)."""
        from sqlalchemy import text as sa_text

        page_id = await _seed_derived_page(src_env, "note.md", "Note For S2 Compat")

        cascade_calls: list[str] = []

        async def _fake_cascade(page_uuid: uuid.UUID) -> None:  # type: ignore[override]
            cascade_calls.append(str(page_uuid))
            session_factory = src_env["session_factory"]
            async with session_factory() as sess:
                await sess.execute(
                    sa_text("UPDATE pages SET deleted_at = datetime('now') WHERE id = :id"),
                    {"id": str(page_uuid)},
                )
                await sess.commit()

            from app.ops.cascade_delete import CascadeResult

            return CascadeResult(
                deleted_page_id=page_uuid,
                wikilinks_cleaned=0,
                index_entry_removed=False,
                shared_entity_warnings=[],
                files_written=0,
                data_version_after=1,
            )

        monkeypatch.setattr("app.sources._cascade_delete_page", _fake_cascade)

        async with AsyncClient(
            transport=ASGITransport(app=src_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.delete("/sources", params={"path": "note.md"})

        assert resp.status_code == 200, f"Expected 200: {resp.text}"
        data = resp.json()
        assert data["deleted_source"] == "note.md"
        assert data["files_deleted"] == 1
        assert data["pages_cascaded"] == 1
        assert data["pages_deleted"] == 1  # backward-compat

        assert not (src_env["sources_dir"] / "note.md").exists()
        assert str(page_id) in cascade_calls

    async def test_empty_directory_delete_returns_zero_counts(
        self,
        src_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-035: deleting an empty directory returns files_deleted=0, pages_cascaded=0."""
        sources_dir: Path = src_env["sources_dir"]
        empty_dir = sources_dir / "empty_folder"
        empty_dir.mkdir()

        bumped: list[bool] = []

        async def _fake_bump() -> None:
            bumped.append(True)

        monkeypatch.setattr("app.sources._bump_version_no_derived", _fake_bump)

        async with AsyncClient(
            transport=ASGITransport(app=src_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.delete("/sources", params={"path": "empty_folder"})

        assert resp.status_code == 200, f"Expected 200: {resp.text}"
        data = resp.json()
        assert data["files_deleted"] == 0
        assert data["pages_cascaded"] == 0
        assert data["deleted_source"] == "empty_folder"
        assert not empty_dir.exists()
        # _bump_version_no_derived must be called once for the empty-dir case
        assert bumped


# ── T-SRC-036..T-SRC-040: root=wiki param on read endpoints ──────────────────
#
# Tests for the new root="wiki" capability added to GET /sources, /sources/content,
# /sources/raw. Exercises:
#   T-SRC-036  GET /sources?root=wiki lists wiki files+dirs; excludes .obsidian
#   T-SRC-037  GET /sources/content?root=wiki&path= returns wiki file text
#   T-SRC-038  GET /sources/raw?root=wiki&path= streams wiki file bytes
#   T-SRC-039  Traversal root=wiki&path=../raw/... → 404 (path safety)
#   T-SRC-040  root=wiki hides hidden entries (.obsidian, dotfiles); content/raw reject them
#   T-SRC-041  root=sources behaviour UNCHANGED (regression)
#   T-SRC-042  GET /sources?root=wiki with non-existent wiki dir → empty list (graceful)
#   T-SRC-043  GET /sources/content?root=wiki returns ingested=False + page_ids=[]


class TestWikiRoot:
    """
    T-SRC-036..T-SRC-043 — root=wiki param on GET /sources, /sources/content, /sources/raw.

    Uses the existing src_env fixture which already populates wiki/ with:
      log.md, .obsidian/app.json
    We seed additional files (index.md, concepts/foo.md) inside the test.
    """

    # ── T-SRC-036: listing ────────────────────────────────────────────────────

    async def test_wiki_listing_returns_wiki_files(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-036a: GET /sources?root=wiki lists files in vault/wiki/."""
        wiki_dir: Path = src_env["wiki_dir"]

        # Seed extra wiki content
        (wiki_dir / "index.md").write_text(
            "---\ntype: index\ntitle: Index\n---\n\n# Index\n", encoding="utf-8"
        )
        concepts_dir = wiki_dir / "concepts"
        concepts_dir.mkdir(exist_ok=True)
        (concepts_dir / "foo.md").write_text(
            "---\ntype: concept\ntitle: Foo\n---\n\nFoo content.\n", encoding="utf-8"
        )

        resp = await src_client.get("/sources", params={"root": "wiki"})
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "entries" in data
        assert data["truncated"] is False

        paths = {e["path"] for e in data["entries"]}
        # Root-level files (log.md was created by fixture; index.md added above)
        assert "log.md" in paths
        assert "index.md" in paths
        # concepts/ subdirectory
        assert "concepts" in paths
        # File inside concepts/
        assert "concepts/foo.md" in paths

    async def test_wiki_listing_excludes_obsidian_and_dotfiles(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-036b: GET /sources?root=wiki excludes .obsidian and dotfiles (I5)."""
        wiki_dir: Path = src_env["wiki_dir"]
        # .obsidian already exists in the fixture; add a dotfile at root level
        (wiki_dir / ".hidden_file").write_text("secret\n", encoding="utf-8")

        resp = await src_client.get("/sources", params={"root": "wiki"})
        assert resp.status_code == 200
        paths = {e["path"] for e in resp.json()["entries"]}

        # .obsidian dir AND its children must NOT appear
        assert ".obsidian" not in paths
        assert not any(p.startswith(".obsidian") for p in paths)
        # Root-level dotfile must NOT appear
        assert ".hidden_file" not in paths

    async def test_wiki_listing_does_not_leak_sources_files(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-036c: wiki listing does not contain any raw/sources/ file paths."""
        resp = await src_client.get("/sources", params={"root": "wiki"})
        assert resp.status_code == 200
        paths = {e["path"] for e in resp.json()["entries"]}

        # Files that exist in raw/sources/ must NOT appear in the wiki listing
        for sources_only in ("note.md", "plain.txt", "image.png", "subdir"):
            assert sources_only not in paths, f"{sources_only!r} should not appear in wiki listing"

    # ── T-SRC-037: /sources/content with root=wiki ────────────────────────────

    async def test_wiki_content_returns_text_for_md(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-037: GET /sources/content?root=wiki&path=log.md returns markdown text."""
        # log.md is seeded by the fixture
        resp = await src_client.get("/sources/content", params={"root": "wiki", "path": "log.md"})
        assert resp.status_code == 200, f"Expected 200: {resp.text}"
        data = resp.json()
        assert data["path"] == "log.md"
        assert data["category"] == "markdown"
        assert data["is_text"] is True
        assert data["text"] is not None
        assert "Synapse Ingest Log" in data["text"]

    async def test_wiki_content_ingested_always_false(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-043: root=wiki always returns ingested=False + page_ids=[]."""
        resp = await src_client.get("/sources/content", params={"root": "wiki", "path": "log.md"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ingested"] is False
        assert data["page_ids"] == []

    async def test_wiki_content_absent_file_returns_404(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-037b: absent wiki file → 404."""
        resp = await src_client.get(
            "/sources/content", params={"root": "wiki", "path": "nonexistent.md"}
        )
        assert resp.status_code == 404

    async def test_wiki_content_nested_file(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-037c: GET /sources/content?root=wiki&path=concepts/foo.md returns content."""
        wiki_dir: Path = src_env["wiki_dir"]
        concepts_dir = wiki_dir / "concepts"
        concepts_dir.mkdir(exist_ok=True)
        (concepts_dir / "bar.md").write_text(
            "---\ntype: concept\ntitle: Bar\n---\n\nBar body.\n", encoding="utf-8"
        )

        resp = await src_client.get(
            "/sources/content", params={"root": "wiki", "path": "concepts/bar.md"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "concepts/bar.md"
        assert "Bar body." in data["text"]

    # ── T-SRC-038: /sources/raw with root=wiki ────────────────────────────────

    async def test_wiki_raw_returns_bytes(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-038: GET /sources/raw?root=wiki&path=log.md streams bytes."""
        resp = await src_client.get("/sources/raw", params={"root": "wiki", "path": "log.md"})
        assert resp.status_code == 200, f"Expected 200: {resp.text}"
        ct = resp.headers.get("content-type", "")
        assert "markdown" in ct or "text" in ct
        assert len(resp.content) > 0
        assert b"Synapse Ingest Log" in resp.content

    # ── T-SRC-039: traversal with root=wiki → 404 ─────────────────────────────

    async def test_wiki_traversal_listing_is_bounded(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-039a: root=wiki listing has no path param — cannot traverse (read-only)."""
        resp = await src_client.get("/sources", params={"root": "wiki"})
        assert resp.status_code == 200
        paths = {e["path"] for e in resp.json()["entries"]}
        assert not any("/etc" in p or "passwd" in p for p in paths)

    async def test_wiki_content_traversal_rejected(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-039b: root=wiki&path=../../raw/sources/note.md → 404 (traversal blocked)."""
        resp = await src_client.get(
            "/sources/content",
            params={"root": "wiki", "path": "../../raw/sources/note.md"},
        )
        assert (
            resp.status_code == 404
        ), f"Expected 404 for traversal attempt, got {resp.status_code}"

    async def test_wiki_raw_traversal_rejected(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-039c: root=wiki&path=../raw/sources/note.md for raw → 404."""
        resp = await src_client.get(
            "/sources/raw",
            params={"root": "wiki", "path": "../raw/sources/note.md"},
        )
        assert (
            resp.status_code == 404
        ), f"Expected 404 for traversal attempt, got {resp.status_code}"

    # ── T-SRC-040: hidden entries rejected ────────────────────────────────────

    async def test_wiki_content_hidden_file_rejected(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-040a: root=wiki + path to .obsidian/app.json → 404 (hidden)."""
        resp = await src_client.get(
            "/sources/content",
            params={"root": "wiki", "path": ".obsidian/app.json"},
        )
        assert (
            resp.status_code == 404
        ), f"Expected 404 for hidden .obsidian path, got {resp.status_code}"

    async def test_wiki_raw_hidden_file_rejected(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-040b: root=wiki + raw request for .obsidian/app.json → 404."""
        resp = await src_client.get(
            "/sources/raw",
            params={"root": "wiki", "path": ".obsidian/app.json"},
        )
        assert (
            resp.status_code == 404
        ), f"Expected 404 for hidden .obsidian path (raw), got {resp.status_code}"

    async def test_wiki_content_root_dotfile_rejected(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-040c: root-level dotfile in wiki/ is also rejected by content endpoint."""
        wiki_dir: Path = src_env["wiki_dir"]
        (wiki_dir / ".dotfile").write_text("secret\n", encoding="utf-8")

        resp = await src_client.get("/sources/content", params={"root": "wiki", "path": ".dotfile"})
        assert resp.status_code == 404

    # ── T-SRC-041: root=sources unchanged (regression) ───────────────────────

    async def test_sources_root_listing_unchanged(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-041a: GET /sources?root=sources returns raw/sources/ files (unchanged)."""
        resp = await src_client.get("/sources", params={"root": "sources"})
        assert resp.status_code == 200
        paths = {e["path"] for e in resp.json()["entries"]}
        assert "note.md" in paths
        assert "plain.txt" in paths
        assert "image.png" in paths

    async def test_sources_root_default_unchanged(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-041b: GET /sources (no root param) = same as root=sources."""
        resp_no_param = await src_client.get("/sources")
        resp_explicit = await src_client.get("/sources", params={"root": "sources"})
        assert resp_no_param.status_code == 200
        assert resp_explicit.status_code == 200
        paths_no_param = {e["path"] for e in resp_no_param.json()["entries"]}
        paths_explicit = {e["path"] for e in resp_explicit.json()["entries"]}
        assert paths_no_param == paths_explicit

    async def test_sources_content_root_default_unchanged(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """T-SRC-041c: /sources/content?path=note.md still works without root param."""
        resp = await src_client.get("/sources/content", params={"path": "note.md"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["path"] == "note.md"
        assert data["category"] == "markdown"

    # ── T-SRC-042: missing wiki dir → empty list ─────────────────────────────

    async def test_wiki_listing_empty_when_dir_missing(
        self,
        src_client: AsyncClient,
        src_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-SRC-042: GET /sources?root=wiki returns empty list when wiki/ does not exist."""
        from app import config as cfg

        missing_wiki = src_env["vault_root"] / "wiki_nonexistent"
        monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: missing_wiki))

        resp = await src_client.get("/sources", params={"root": "wiki"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["entries"] == []
        assert data["total"] == 0


class TestVaultRoot:
    """v1.5 P1 (ADR-0066): root='vault' lists/previews the WHOLE vault (llm_wiki Files-tab)."""

    async def test_vault_root_lists_whole_tree(
        self, src_client: AsyncClient, src_env: dict[str, Any]
    ) -> None:
        """root='vault' returns raw/ + wiki/ + purpose.md + schema.md; prunes .obsidian."""
        vault_root = src_env["vault_root"]
        (vault_root / "purpose.md").write_text("# Purpose\n", encoding="utf-8")
        (vault_root / "schema.md").write_text("# Schema\n", encoding="utf-8")

        resp = await src_client.get("/sources", params={"root": "vault"})
        assert resp.status_code == 200, resp.text
        paths = {e["path"] for e in resp.json()["entries"]}
        # Top-level structure + meta files
        assert {"raw", "wiki", "purpose.md", "schema.md"} <= paths
        # Nested entries from BOTH trees (relative to the vault root)
        assert "raw/sources" in paths
        assert "raw/sources/note.md" in paths
        assert "wiki/log.md" in paths
        # .obsidian internals are pruned (I5)
        assert not any(p == "wiki/.obsidian" or p.startswith("wiki/.obsidian/") for p in paths)

    async def test_vault_root_previews_wiki_file(self, src_client: AsyncClient) -> None:
        """A wiki file is previewable through the vault root (vault-relative path)."""
        resp = await src_client.get(
            "/sources/content", params={"root": "vault", "path": "wiki/log.md"}
        )
        assert resp.status_code == 200, resp.text
        assert "Synapse Ingest Log" in resp.text

    async def test_vault_root_rejects_hidden(self, src_client: AsyncClient) -> None:
        """Hidden entries (.obsidian) are 404 through the vault root, like the wiki root."""
        resp = await src_client.get(
            "/sources/content", params={"root": "vault", "path": "wiki/.obsidian/app.json"}
        )
        assert resp.status_code == 404

    async def test_vault_root_rejects_traversal(self, src_client: AsyncClient) -> None:
        """Path traversal out of the vault root is 404 (containment guard)."""
        resp = await src_client.get(
            "/sources/content", params={"root": "vault", "path": "../../etc/passwd"}
        )
        assert resp.status_code == 404
