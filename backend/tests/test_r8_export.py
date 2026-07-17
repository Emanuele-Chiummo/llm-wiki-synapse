"""
R8-4 — Vault export / backup (AC-R8-4-1..5).

Tests:
  T-R84-001  GET /export — ZIP contains expected entries from a tmp vault fixture
  T-R84-002  GET /export — Content-Disposition header carries expected filename
  T-R84-003  GET /export/data.json — top-level keys present (AC-R8-4-2)
  T-R84-004  GET /export/data.json — pages count matches DB for fixture vault
  T-R84-005  GET /export — 429 when export Lock is already held (AC-R8-4-5)
  T-R84-006  GET /export — 413 when vault exceeds EXPORT_MAX_BYTES cap (AC-R8-4-1)
  T-R84-007  GET /export/data.json — edges and links present in output
  T-R84-008  GET /export/data.json — data_version matches VaultState row

All tests use SQLite in-memory (no live Postgres required).
Vault filesystem: tmp_path fixture provides a real (tiny) directory tree.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
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

# ── Vault fixture builder ──────────────────────────────────────────────────────


def _build_fixture_vault(tmp_path: Path) -> Path:
    """
    Create a minimal vault directory tree for export tests.

    Structure:
      vault/
        raw/sources/doc1.txt
        wiki/index.md
        wiki/.obsidian/app.json
        purpose.md
        schema.md
    """
    vault_root = tmp_path / "vault"
    (vault_root / "raw" / "sources").mkdir(parents=True)
    (vault_root / "wiki" / ".obsidian").mkdir(parents=True)

    (vault_root / "raw" / "sources" / "doc1.txt").write_text(
        "Hello world source document.", encoding="utf-8"
    )
    (vault_root / "wiki" / "index.md").write_text(
        "---\ntype: index\ntitle: Index\n---\n\n# Index\n", encoding="utf-8"
    )
    (vault_root / "wiki" / ".obsidian" / "app.json").write_text(
        '{"legacyEditor": false}', encoding="utf-8"
    )
    (vault_root / "purpose.md").write_text(
        "# Purpose\n\nTest vault for export tests.", encoding="utf-8"
    )
    (vault_root / "schema.md").write_text("# Schema\n\nVault schema rules.", encoding="utf-8")
    return vault_root


# ── SQLite in-memory DB fixture ────────────────────────────────────────────────


def _build_sqlite_meta() -> MetaData:
    """Return a SQLAlchemy MetaData object with the minimal tables needed by export.py."""
    meta = MetaData()

    Table(
        "pages",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("file_path", Text, nullable=False),
        Column("title", Text, nullable=True),
        Column("type", Text, nullable=True),
        Column("sources", Text, nullable=True),
        Column("tags", Text, nullable=True),
        Column("generation_key", Text, nullable=True),
        Column("summary", Text, nullable=True),
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
        Column("remote_mcp_write_enabled", Integer, nullable=True),
        Column("mcp_access_token_hash", Text, nullable=True),
        Column("mcp_allow_without_token", Integer, nullable=False, server_default=sa_text("0")),
        Column("clip_enabled_db", Integer, nullable=True),
        Column("clip_access_token", Text, nullable=True),
        Column("clip_allowed_origins_db", Text, nullable=True),
        Column("cli_oauth_token", Text, nullable=True),
        Column("cli_oauth_token_encrypted", LargeBinary, nullable=True),
        Column("web_search_api_keys_encrypted", LargeBinary, nullable=True),
        Column("searxng_url_db", Text, nullable=True),
        Column("searxng_categories_db", Text, nullable=True),
        Column("searxng_max_queries_db", Integer, nullable=True),
        Column("output_language", Text, nullable=True),
        Column("updated_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )
    Table(
        "links",
        meta,
        Column("id", String(36), primary_key=True),
        Column("source_page_id", String(36), nullable=False),
        Column("target_title", Text, nullable=False),
        Column("target_page_id", String(36), nullable=True),
        Column("alias", Text, nullable=True),
        Column("dangling", Integer, nullable=False, server_default=sa_text("1")),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )
    Table(
        "edges",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("source_page_id", String(36), nullable=False),
        Column("target_page_id", String(36), nullable=False),
        Column("weight", Float, nullable=False),
        Column("signals", Text, nullable=True),
        Column("kind", String, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )
    Table(
        "ingest_runs",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("page_id", String(36), nullable=True),
        Column("provider_name", Text, nullable=False),
        Column("provider_type", Text, nullable=False),
        Column("model_id", Text, nullable=False),
        Column("route", Text, nullable=False),
        Column("max_iter_used", Integer, nullable=False, server_default=sa_text("0")),
        Column("total_tokens", Integer, nullable=False, server_default=sa_text("0")),
        Column("total_cost_usd", Float, nullable=False, server_default=sa_text("0")),
        Column("converged", Integer, nullable=False, server_default=sa_text("0")),
        Column("cost_anomaly", Integer, nullable=False, server_default=sa_text("0")),
        Column("started_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("finished_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("status", Text, nullable=False, server_default=sa_text("'completed'")),
        Column("pages_created", Integer, nullable=False, server_default=sa_text("0")),
        Column("error_message", Text, nullable=True),
        Column("page_type_counts", Text, nullable=True),
        Column("diagnostics", Text, nullable=True),
        Column("source_path", Text, nullable=True),
        Column("retry_count", Integer, nullable=False, server_default=sa_text("0")),
    )
    Table(
        "review_items",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("item_type", Text, nullable=False),
        Column("status", Text, nullable=False, server_default=sa_text("'pending'")),
        Column("proposal_origin", Text, nullable=False, server_default=sa_text("'legacy'")),
        Column("page_id", String(36), nullable=True),
        Column("source_page_id", String(36), nullable=True),
        Column("proposed_title", Text, nullable=True),
        Column("proposed_page_type", Text, nullable=True),
        Column("proposed_dir", Text, nullable=True),
        Column("rationale", Text, nullable=True),
        Column("resolution", Text, nullable=True),
        Column("created_page_id", String(36), nullable=True),
        Column("deep_research_run_id", String(36), nullable=True),
        Column("content_key", String(16), nullable=True),
        Column("referenced_page_ids", Text, nullable=True),
        Column("search_queries", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("reviewed_at", Text, nullable=True),
        Column("reviewed_by", Text, nullable=True),
        Column("updated_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )
    # 1.9.1 W4 (SEC-OPS-2) — /export/full additions
    Table(
        "conversations",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("title", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("updated_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("deleted_at", Text, nullable=True),
    )
    Table(
        "messages",
        meta,
        Column("id", String(36), primary_key=True),
        Column("conversation_id", String(36), nullable=False),
        Column("role", Text, nullable=False),
        Column("content", Text, nullable=False),
        Column("citations", Text, nullable=True),
        Column("images", Text, nullable=True),
        Column("provider_type", Text, nullable=True),
        Column("model_id", Text, nullable=True),
        Column("input_tokens", Integer, nullable=False, server_default=sa_text("0")),
        Column("output_tokens", Integer, nullable=False, server_default=sa_text("0")),
        Column("total_cost_usd", Float, nullable=False, server_default=sa_text("0")),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )
    Table(
        "provider_config",
        meta,
        Column("id", String(36), primary_key=True),
        Column("scope", Text, nullable=False),
        Column("operation", Text, nullable=True),
        Column("vault_id", String, nullable=True),
        Column("provider_type", Text, nullable=False),
        Column("model_id", Text, nullable=False),
        Column("base_url", Text, nullable=True),
        Column("api_key_encrypted", LargeBinary, nullable=True),
        Column("reasoning_effort", Text, nullable=True),
        Column("max_iter", Integer, nullable=False, server_default=sa_text("3")),
        Column("token_budget", Integer, nullable=False, server_default=sa_text("60000")),
        Column("is_fallback", Integer, nullable=False, server_default=sa_text("0")),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("updated_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )
    return meta


# ── Export test fixture ────────────────────────────────────────────────────────


@pytest.fixture()
async def export_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Set up an isolated export test environment:
    - Temporary vault directory with real files.
    - SQLite in-memory DB seeded with 2 pages, 1 link, 1 edge, 1 run, 1 review_item.
    - Patched settings.vault_root + settings.vault_id.
    - Patched app.db.get_session → SQLite session factory.
    - FastAPI app with mocked lifespan.
    """
    from app import config as cfg

    # ── Vault ─────────────────────────────────────────────────────────────────
    vault_root = _build_fixture_vault(tmp_path)
    vault_id = "export-test-vault"

    monkeypatch.setattr(cfg.settings, "vault_id", vault_id)
    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))

    # ── SQLite engine ─────────────────────────────────────────────────────────
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    meta = _build_sqlite_meta()
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # Seed: vault_state (data_version=7)
    page1_id = str(uuid.uuid4())
    page2_id = str(uuid.uuid4())
    link_id = str(uuid.uuid4())
    edge_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    review_id = str(uuid.uuid4())

    async with session_factory() as session:
        await session.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                "VALUES (:id, :vault_id, 7, datetime('now'))"
            ),
            {"id": str(uuid.uuid4()), "vault_id": vault_id},
        )
        # Two live pages
        for pid, fpath in [(page1_id, "wiki/index.md"), (page2_id, "wiki/concepts/A.md")]:
            await session.execute(
                sa_text(
                    "INSERT INTO pages (id, vault_id, file_path, title, type, "
                    "content_hash, deleted_at, created_at, updated_at) "
                    "VALUES (:id, :vault_id, :fp, :title, :t, :ch, NULL, datetime('now'), datetime('now'))"
                ),
                {
                    "id": pid,
                    "vault_id": vault_id,
                    "fp": fpath,
                    "title": fpath,
                    "t": "concept",
                    "ch": "abc" * 21 + "ab",  # 64 hex chars
                },
            )
        # One soft-deleted page (must NOT appear in export)
        await session.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title, type, "
                "content_hash, deleted_at, created_at, updated_at) "
                "VALUES (:id, :vault_id, 'wiki/deleted.md', 'Deleted', 'concept', "
                "'dd' * 32, datetime('now'), datetime('now'), datetime('now'))"
            ),
            {"id": str(uuid.uuid4()), "vault_id": vault_id},
        )
        # One link row
        await session.execute(
            sa_text(
                "INSERT INTO links (id, source_page_id, target_title, target_page_id, "
                "alias, dangling, created_at) "
                "VALUES (:id, :src, 'A', :tgt, NULL, 0, datetime('now'))"
            ),
            {"id": link_id, "src": page1_id, "tgt": page2_id},
        )
        # One edge row
        await session.execute(
            sa_text(
                "INSERT INTO edges (id, vault_id, source_page_id, target_page_id, "
                "weight, signals, kind, created_at) "
                "VALUES (:id, :vault_id, :src, :tgt, 3.5, NULL, 'link', datetime('now'))"
            ),
            {"id": edge_id, "vault_id": vault_id, "src": page1_id, "tgt": page2_id},
        )
        # One ingest_run
        await session.execute(
            sa_text(
                "INSERT INTO ingest_runs (id, vault_id, page_id, provider_name, "
                "provider_type, model_id, route, max_iter_used, total_tokens, "
                "total_cost_usd, converged, cost_anomaly, started_at, finished_at, "
                "status, pages_created, error_message, source_path, retry_count) "
                "VALUES (:id, :vault_id, NULL, 'ApiProvider', 'api', 'test-model', "
                "'orchestrated', 1, 500, 0.01, 1, 0, datetime('now'), datetime('now'), "
                "'completed', 2, NULL, NULL, 0)"
            ),
            {"id": run_id, "vault_id": vault_id},
        )
        # One review_item
        await session.execute(
            sa_text(
                "INSERT INTO review_items (id, vault_id, item_type, status, "
                "proposed_title, created_at, updated_at) "
                "VALUES (:id, :vault_id, 'missing-page', 'pending', 'NewPage', "
                "datetime('now'), datetime('now'))"
            ),
            {"id": review_id, "vault_id": vault_id},
        )
        # 1.9.1 W4 (SEC-OPS-2) — one conversation with two messages.
        # NOTE: stored as .hex (no dashes) — matches how SQLAlchemy's
        # postgresql.UUID(as_uuid=True) bind-processes a Python uuid.UUID on non-native
        # dialects (SQLite). The ORM join `ChatMessage.conversation_id == c.id` (where
        # `c.id` is a real uuid.UUID read back from the `conversations` row) binds via that
        # same processor, so the FK column must be seeded in the identical hex form or the
        # WHERE clause silently matches zero rows.
        conv_uuid = uuid.uuid4()
        conv_id_storage = conv_uuid.hex
        conv_id = str(conv_uuid)  # dashed form — how it round-trips in the JSON response
        msg1_id = str(uuid.uuid4())
        msg2_id = str(uuid.uuid4())
        await session.execute(
            sa_text(
                "INSERT INTO conversations (id, vault_id, title, created_at, updated_at, "
                "deleted_at) VALUES (:id, :vault_id, 'Test chat', datetime('now'), "
                "datetime('now'), NULL)"
            ),
            {"id": conv_id_storage, "vault_id": vault_id},
        )
        await session.execute(
            sa_text(
                "INSERT INTO messages (id, conversation_id, role, content, input_tokens, "
                "output_tokens, total_cost_usd, created_at) "
                "VALUES (:id, :conv, 'user', 'Hello', 0, 0, 0, datetime('now'))"
            ),
            {"id": msg1_id, "conv": conv_id_storage},
        )
        await session.execute(
            sa_text(
                "INSERT INTO messages (id, conversation_id, role, content, provider_type, "
                "model_id, input_tokens, output_tokens, total_cost_usd, created_at) "
                "VALUES (:id, :conv, 'assistant', 'Hi there', 'api', 'test-model', 10, 20, "
                "0.02, datetime('now'))"
            ),
            {"id": msg2_id, "conv": conv_id_storage},
        )
        # One global provider_config row with an "encrypted" api key (opaque bytes for the
        # test — never decrypted by export.py).
        pc_id = str(uuid.uuid4())
        await session.execute(
            sa_text(
                "INSERT INTO provider_config (id, scope, operation, vault_id, provider_type, "
                "model_id, base_url, api_key_encrypted, reasoning_effort, max_iter, "
                "token_budget, is_fallback, created_at, updated_at) "
                "VALUES (:id, 'global', NULL, NULL, 'api', 'claude-sonnet-4-6', NULL, "
                ":key, NULL, 3, 60000, 0, datetime('now'), datetime('now'))"
            ),
            {"id": pc_id, "key": b"\x01\x02fernet-ciphertext-stub\x03\x04"},
        )
        await session.commit()

    # ── Patch get_session ─────────────────────────────────────────────────────
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
    monkeypatch.setattr("app.export.get_session", patched_get_session)

    # ── FastAPI app with mocked lifespan ──────────────────────────────────────
    from app.main import app

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):  # type: ignore[override]
        yield

    app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

    yield {
        "app": app,
        "vault_root": vault_root,
        "vault_id": vault_id,
        "page1_id": page1_id,
        "page2_id": page2_id,
        "conversation_id": conv_id,
        "provider_config_id": pc_id,
    }


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestExportZip:
    """Tests for GET /export (AC-R8-4-1, AC-R8-4-5)."""

    @pytest.mark.asyncio
    async def test_zip_contains_expected_entries(self, export_env: dict[str, Any]) -> None:
        """
        T-R84-001: ZIP archive contains wiki/index.md and schema.md from fixture vault.
        """
        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.get("/export")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"

        # Unpack and inspect the archive
        buf = io.BytesIO(resp.content)
        with zipfile.ZipFile(buf) as zf:
            names = set(zf.namelist())

        assert "wiki/index.md" in names, f"wiki/index.md missing from ZIP; got: {names}"
        assert "schema.md" in names, f"schema.md missing from ZIP; got: {names}"
        assert "purpose.md" in names, f"purpose.md missing from ZIP; got: {names}"
        assert "raw/sources/doc1.txt" in names, f"raw/sources/doc1.txt missing; got: {names}"
        # .obsidian/ lives inside wiki/ (K7: wiki/ is the Obsidian vault)
        assert (
            "wiki/.obsidian/app.json" in names
        ), f"wiki/.obsidian/app.json missing from ZIP; got: {names}"

    @pytest.mark.asyncio
    async def test_content_disposition_filename(self, export_env: dict[str, Any]) -> None:
        """
        T-R84-002: Content-Disposition header carries the expected attachment filename.
        """
        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.get("/export")

        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "attachment" in cd
        assert "synapse-vault-" in cd
        assert export_env["vault_id"] in cd

    @pytest.mark.asyncio
    async def test_429_on_concurrent_export(self, export_env: dict[str, Any]) -> None:
        """
        T-R84-005: A second export request while the lock is held returns 429 (AC-R8-4-5).
        Simulated by manually acquiring the lock before the request.
        """
        from app.export import _get_export_lock

        vault_id = export_env["vault_id"]
        lock = _get_export_lock(vault_id)

        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            # Hold the lock manually
            async with lock:
                resp = await client.get("/export")
                assert resp.status_code == 429
                assert "already running" in resp.json()["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_413_on_oversized_vault(
        self, export_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        T-R84-006: 413 is returned when vault exceeds EXPORT_MAX_BYTES (AC-R8-4-1, I7).
        Monkeypatches EXPORT_MAX_BYTES to 1 byte so any real file triggers the cap.
        """
        import app.export as _exp_mod

        monkeypatch.setattr(_exp_mod, "EXPORT_MAX_BYTES", 1)

        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.get("/export")

        assert resp.status_code == 413
        detail = resp.json()["error"]["message"]
        assert "MB" in detail or "size" in detail.lower()


class TestExportDataJson:
    """Tests for GET /export/data.json (AC-R8-4-2)."""

    @pytest.mark.asyncio
    async def test_top_level_keys_present(self, export_env: dict[str, Any]) -> None:
        """
        T-R84-003: Response contains all required top-level keys (AC-R8-4-2).
        """
        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.get("/export/data.json")

        assert resp.status_code == 200
        data = resp.json()
        required_keys = {
            "pages",
            "links",
            "edges",
            "runs",
            "review_items",
            "exported_at",
            "data_version",
        }
        missing = required_keys - set(data.keys())
        assert not missing, f"Missing keys in data.json: {missing}"

    @pytest.mark.asyncio
    async def test_pages_count_matches_db(self, export_env: dict[str, Any]) -> None:
        """
        T-R84-004: pages count in data.json matches the live (non-deleted) page count.
        The fixture inserts 2 live pages + 1 soft-deleted page; export must return 2.
        """
        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.get("/export/data.json")

        assert resp.status_code == 200
        data = resp.json()
        # 2 live pages were seeded; soft-deleted page must NOT appear
        assert (
            len(data["pages"]) == 2
        ), f"Expected 2 live pages, got {len(data['pages'])}: {data['pages']}"

    @pytest.mark.asyncio
    async def test_edges_and_links_present(self, export_env: dict[str, Any]) -> None:
        """
        T-R84-007: edges and links arrays contain the seeded rows.
        """
        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.get("/export/data.json")

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["edges"]) == 1, f"Expected 1 edge, got {len(data['edges'])}"
        assert len(data["links"]) == 1, f"Expected 1 link, got {len(data['links'])}"

        edge = data["edges"][0]
        assert "weight" in edge
        assert "source_page_id" in edge
        assert "target_page_id" in edge

    @pytest.mark.asyncio
    async def test_data_version_matches_vault_state(self, export_env: dict[str, Any]) -> None:
        """
        T-R84-008: data_version in the response matches the seeded VaultState row (=7).
        """
        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.get("/export/data.json")

        assert resp.status_code == 200
        data = resp.json()
        assert data["data_version"] == 7, f"Expected data_version=7, got {data['data_version']}"


class TestExportFull:
    """
    Tests for GET /export/full (1.9.1 W4, SEC-OPS-2).

    Extends /export/data.json with conversations/messages, provider_config
    (ciphertext untouched), and the full vault_state row.
    """

    @pytest.mark.asyncio
    async def test_top_level_keys(self, export_env: dict[str, Any]) -> None:
        """T-W4-001: /export/full includes data.json's keys plus the 3 new sections."""
        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.get("/export/full")

        assert resp.status_code == 200
        data = resp.json()
        required_keys = {
            "pages",
            "links",
            "edges",
            "runs",
            "review_items",
            "conversations",
            "provider_config",
            "vault_state",
            "exported_at",
            "data_version",
            "secrets_note",
        }
        missing = required_keys - set(data.keys())
        assert not missing, f"Missing keys in /export/full: {missing}"

    @pytest.mark.asyncio
    async def test_conversations_with_messages(self, export_env: dict[str, Any]) -> None:
        """T-W4-002: the seeded conversation + its 2 messages round-trip."""
        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.get("/export/full")

        data = resp.json()
        assert len(data["conversations"]) == 1
        conv = data["conversations"][0]
        assert conv["id"] == export_env["conversation_id"]
        assert conv["title"] == "Test chat"
        assert len(conv["messages"]) == 2
        roles = {m["role"] for m in conv["messages"]}
        assert roles == {"user", "assistant"}

    @pytest.mark.asyncio
    async def test_provider_config_ciphertext_never_decrypted(
        self, export_env: dict[str, Any]
    ) -> None:
        """
        T-W4-003: provider_config row is exported with its api_key ciphertext base64-encoded,
        never plaintext, never decrypted (SEC-OPS-2 invariant).
        """
        import base64

        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.get("/export/full")

        data = resp.json()
        assert len(data["provider_config"]) == 1
        pc = data["provider_config"][0]
        assert pc["id"] == export_env["provider_config_id"]
        assert pc["scope"] == "global"
        assert pc["provider_type"] == "api"
        assert pc["model_id"] == "claude-sonnet-4-6"
        # The stub "ciphertext" bytes round-trip exactly via base64 — proves no decryption
        # (and no re-encoding) happened along the way.
        raw = base64.b64decode(pc["api_key_encrypted_b64"])
        assert raw == b"\x01\x02fernet-ciphertext-stub\x03\x04"
        assert "secrets_note" in data
        assert "SYNAPSE_SECRET_KEY" in data["secrets_note"]

    @pytest.mark.asyncio
    async def test_vault_state_full_row(self, export_env: dict[str, Any]) -> None:
        """T-W4-004: vault_state block carries data_version + MCP toggle fields."""
        async with AsyncClient(
            transport=ASGITransport(app=export_env["app"]), base_url="http://test"
        ) as client:
            resp = await client.get("/export/full")

        data = resp.json()
        assert data["vault_state"] is not None
        assert data["vault_state"]["data_version"] == 7
        assert data["vault_state"]["vault_id"] == export_env["vault_id"]
        assert "remote_mcp_enabled" in data["vault_state"]
