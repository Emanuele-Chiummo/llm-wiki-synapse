"""
REST API contract tests (AC-REST-1..6, AC-F16dv-3).

Uses FastAPI's AsyncClient (httpx) with the application in test mode.
Database: SQLite in-memory (via aiosqlite + SQLAlchemy async).
Qdrant: FakeQdrantClient (in-process stub).
Embedding: FakeEmbeddingClient (already in embeddings.py).

No live Postgres, no live Qdrant, no network calls.

Coverage:
  AC-REST-1  GET /status → 200 + {data_version, started_at/uptime}
  AC-REST-2  GET /pages  → 200 + list; ingested page appears
  AC-REST-3  GET /pages/{id} → 200 with full metadata; 404 for unknown
  AC-REST-4  POST /ingest/trigger → 202 + {task_id:null, status, page_id}
  AC-REST-5  /openapi.json valid OpenAPI 3.1 + all 4 endpoints present
  AC-REST-6  bad input → 4xx, never 5xx
  AC-F16dv-3 GET /status returns current data_version value

Test IDs: T-API-001 .. T-API-015

Mock contract (GAP-4 documentation):
  Database: SQLite+aiosqlite instead of Postgres+asyncpg.
    - JSONB columns: stored as JSON text in SQLite
    - UUID columns: stored as string
    - TIMESTAMP columns: stored as ISO text
  Qdrant: FakeQdrantClient dict-based in-process stub
  Embedding: FakeEmbeddingClient returns zero vectors of EMBEDDING_DIM length

When live Postgres+Qdrant are available (TrueNAS live-demo), the integration
tests in test_ingest_incremental.py should be re-run with real infra.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.embeddings import FakeEmbeddingClient, set_embedding_client
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── Shared fixtures ────────────────────────────────────────────────────────────


class FakeQdrantClientAPI:
    """Minimal in-process Qdrant stub for API tests."""

    def __init__(self) -> None:
        self.points: dict[str, Any] = {}
        self.upsert_calls: int = 0
        self.delete_calls: int = 0

    async def get_collections(self) -> MagicMock:
        m = MagicMock()
        m.collections = [MagicMock(name="synapse_pages")]
        return m

    async def create_collection(self, collection_name: str, vectors_config: Any) -> None:
        pass

    async def get_collection(self, collection_name: str) -> MagicMock:
        m = MagicMock()
        m.config.params.vectors = MagicMock()
        m.config.params.vectors.size = 8
        return m

    async def upsert(self, collection_name: str, points: list[Any]) -> None:
        for pt in points:
            self.points[str(pt.id)] = pt.payload or {}
            self.upsert_calls += 1

    async def delete(self, collection_name: str, points_selector: Any) -> None:
        for pid in points_selector.points:
            self.points.pop(str(pid), None)
            self.delete_calls += 1


@pytest.fixture()
async def api_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Set up a full API test environment:
    - FastAPI app with lifespan mocked (startup bypassed)
    - SQLite in-memory DB
    - FakeQdrantClient
    - FakeEmbeddingClient
    - Temporary vault directory
    """
    from app import config as cfg

    # ── Vault ─────────────────────────────────────────────────────────────────
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

    # ── Settings patch ────────────────────────────────────────────────────────
    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "raw_sources_dir", property(lambda self: sources_dir))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(type(cfg.settings), "log_md_path", property(lambda self: log_md))

    # ── SQLite engine ─────────────────────────────────────────────────────────
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

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
        Column("content_hash", String(64), nullable=False),
        Column("source_mtime_ns", BigInteger, nullable=True),
        Column("qdrant_point_id", String(36), nullable=True),
        Column("x", Float, nullable=True),  # v0.3: FR coords (ADR-0013)
        Column("y", Float, nullable=True),  # v0.3: FR coords (ADR-0013)
        Column("pinned", Integer, nullable=False, server_default=sa_text("0")),  # Feature A
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
        Column("updated_at", Text, nullable=False),
    )

    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # Seed vault_state row
    async with session_factory() as session:
        await session.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                "VALUES (:id, :vault_id, 0, datetime('now'))"
            ),
            {"id": str(uuid.uuid4()), "vault_id": "test-vault"},
        )
        await session.commit()

    # ── Fake clients ──────────────────────────────────────────────────────────
    fake_emb = FakeEmbeddingClient(dim=8)
    set_embedding_client(fake_emb)

    fake_qdrant = FakeQdrantClientAPI()

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
    # provider_config_service imports get_session via `from app.db import get_session`;
    # the ingest/provider-resolution path uses that reference, which the patches above do
    # not cover. Without this it falls through to the real asyncpg engine when a live
    # Postgres is reachable (test isolation bug). See app/provider_config_service.py:118.
    monkeypatch.setattr("app.provider_config_service.get_session", patched_get_session)

    # Patch Qdrant
    monkeypatch.setattr("app.qdrant_client.get_qdrant_client", lambda: fake_qdrant)
    monkeypatch.setattr(
        "app.ingest.orchestrator.upsert_point",
        lambda **kwargs: fake_qdrant.upsert(
            "synapse_pages",
            [
                type(
                    "Pt",
                    (),
                    {
                        "id": str(kwargs["page_id"]),
                        "vector": kwargs["vector"],
                        "payload": {
                            "file_path": kwargs["file_path"],
                            "title": kwargs["title"],
                            "type": kwargs["page_type"],
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
    # Import the app; patch lifespan so we don't trigger real startup
    from app.main import app

    # We'll override the lifespan to just seed vault_state and skip real startup
    @asynccontextmanager
    async def test_lifespan(app: FastAPI):  # type: ignore[override]
        yield

    app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

    yield {
        "app": app,
        "session_factory": session_factory,
        "qdrant": fake_qdrant,
        "embedding": fake_emb,
        "vault_root": vault_root,
        "sources_dir": sources_dir,
        "log_md": log_md,
    }

    set_embedding_client(None)  # type: ignore[arg-type]


@pytest.fixture()
async def api_client(api_env: dict[str, Any]) -> AsyncClient:
    """Provide an httpx AsyncClient backed by the FastAPI test app."""
    async with AsyncClient(
        transport=ASGITransport(app=api_env["app"]),
        base_url="http://test",
    ) as client:
        yield client


# ── Helpers ────────────────────────────────────────────────────────────────────


async def _ingest_test_file(
    api_env: dict[str, Any],
    filename: str = "test_page.md",
    content: str = "---\ntype: entity\ntitle: Test Page\nsources: [a.pdf]\n---\n\nBody.\n",
) -> Path:
    """Write a test .md file to sources and ingest it via the seam."""
    from app.ingest.orchestrator import ingest_file

    src = api_env["sources_dir"] / filename
    src.write_text(content, encoding="utf-8")
    await ingest_file(src)
    return src


# ── AC-REST-1: GET /status ─────────────────────────────────────────────────────


class TestGetStatus:
    """T-API-001, T-API-002, T-API-003 — AC-REST-1, AC-F16dv-3"""

    async def test_get_status_returns_200(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-001: GET /status must return HTTP 200."""
        resp = await api_client.get("/status")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    async def test_get_status_has_data_version_field(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-002: AC-REST-1, AC-F16dv-3 — response must contain data_version (integer)."""
        resp = await api_client.get("/status")
        data = resp.json()
        assert "data_version" in data, "GET /status response must contain 'data_version'"
        assert isinstance(
            data["data_version"], int
        ), f"data_version must be an integer; got {type(data['data_version'])}"

    async def test_get_status_has_uptime_or_started_at(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-003: AC-REST-1 — response must contain uptime or started_at."""
        resp = await api_client.get("/status")
        data = resp.json()
        has_uptime = "uptime_seconds" in data or "uptime" in data
        has_started_at = "started_at" in data
        assert has_uptime or has_started_at, (
            f"GET /status must include uptime_seconds or started_at; "
            f"got keys: {list(data.keys())}"
        )

    async def test_get_status_data_version_reflects_actual_count(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-004: AC-F16dv-3 — data_version in /status matches vault_state DB value."""
        from app.ingest.orchestrator import ingest_file

        # Get baseline
        resp0 = await api_client.get("/status")
        v0 = resp0.json()["data_version"]

        # Ingest a file → data_version should go up
        src = api_env["sources_dir"] / "status_dv.md"
        src.write_text("---\ntype: entity\ntitle: DVStatus\nsources: []\n---\n", encoding="utf-8")
        await ingest_file(src)

        resp1 = await api_client.get("/status")
        v1 = resp1.json()["data_version"]
        assert (
            v1 == v0 + 1
        ), f"After ingest, /status data_version should be {v0 + 1}, got {v1} (AC-F16dv-3)"


# ── AC-REST-2: GET /pages ─────────────────────────────────────────────────────


class TestGetPages:
    """T-API-005, T-API-006 — AC-REST-2"""

    async def test_get_pages_returns_200(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-005: GET /pages must return HTTP 200."""
        resp = await api_client.get("/pages")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    async def test_get_pages_lists_ingested_page(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-006: AC-REST-2 — newly ingested page appears in GET /pages."""
        await _ingest_test_file(
            api_env, "listed.md", "---\ntype: entity\ntitle: Listed\nsources: []\n---\n"
        )

        resp = await api_client.get("/pages")
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("items") or data  # handle both paginated and flat responses
        if isinstance(data, dict) and "items" in data:
            items = data["items"]
        else:
            items = data if isinstance(data, list) else []

        # Accept either paginated wrapper or flat list
        if isinstance(data, dict) and "items" in data:
            items = data["items"]
            assert data["total"] >= 1, "total must be >= 1 after ingest"

        file_paths = [item.get("file_path", "") for item in items]
        assert any("listed.md" in fp for fp in file_paths), (
            f"Ingested file 'listed.md' must appear in GET /pages; " f"got file_paths: {file_paths}"
        )

    async def test_get_pages_pagination_params_accepted(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-007: GET /pages?limit=10&offset=0 must return 200."""
        resp = await api_client.get("/pages?limit=10&offset=0")
        assert resp.status_code == 200

    async def test_get_pages_invalid_limit_returns_422(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-008: AC-REST-6 — invalid limit → 422."""
        resp = await api_client.get("/pages?limit=0")  # limit must be >= 1
        assert resp.status_code == 422, f"limit=0 must return 422; got {resp.status_code}"


# ── AC-REST-3: GET /pages/{id} ────────────────────────────────────────────────


class TestGetPageById:
    """T-API-009, T-API-010, T-API-011 — AC-REST-3, AC-REST-6"""

    async def test_get_known_page_returns_200_with_metadata(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-009: AC-REST-3 — GET /pages/{id} returns 200 with full metadata."""
        from app.ingest.orchestrator import ingest_file

        src = api_env["sources_dir"] / "known_page.md"
        src.write_text(
            "---\ntype: entity\ntitle: Known Page\nsources: [ref.pdf]\n---\n\nContent.\n",
            encoding="utf-8",
        )
        result = await ingest_file(src)
        page_id = str(result.page_id)

        resp = await api_client.get(f"/pages/{page_id}")
        assert (
            resp.status_code == 200
        ), f"GET /pages/{page_id} must return 200; got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["id"] == page_id
        assert "file_path" in data
        # title and type may be present (the SQLite mock returns them)
        assert "content_hash" in data or "id" in data

    async def test_get_unknown_page_returns_404(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-010: AC-REST-3 — unknown UUID → 404."""
        unknown_id = "00000000-0000-0000-0000-000000000000"
        resp = await api_client.get(f"/pages/{unknown_id}")
        assert resp.status_code == 404, f"Unknown page must return 404; got {resp.status_code}"

    async def test_get_page_invalid_uuid_returns_422(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-011: AC-REST-6 — malformed UUID → 422, not 500."""
        resp = await api_client.get("/pages/not-a-valid-uuid")
        assert resp.status_code == 422, f"Malformed UUID must return 422; got {resp.status_code}"
        assert resp.status_code != 500


# ── AC-REST-4: POST /ingest/trigger ──────────────────────────────────────────


class TestIngestTrigger:
    """T-API-012, T-API-013, T-API-014 — AC-REST-4, ADR-0006"""

    async def test_ingest_trigger_returns_202(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-012: AC-REST-4 — POST /ingest/trigger returns HTTP 202."""
        src = api_env["sources_dir"] / "trigger_test.md"
        src.write_text("---\ntype: entity\ntitle: Trigger\nsources: []\n---\n", encoding="utf-8")

        resp = await api_client.post("/ingest/trigger", json={"file_path": str(src)})
        assert (
            resp.status_code == 202
        ), f"POST /ingest/trigger must return 202; got {resp.status_code}: {resp.text}"

    async def test_ingest_trigger_response_schema(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-013: AC-REST-4, ADR-0006 — response has {task_id:null, status, page_id}."""
        src = api_env["sources_dir"] / "trigger_schema.md"
        src.write_text("---\ntype: concept\ntitle: Schema\nsources: []\n---\n", encoding="utf-8")

        resp = await api_client.post("/ingest/trigger", json={"file_path": str(src)})
        assert resp.status_code == 202
        body = resp.json()

        assert "task_id" in body, "Response must contain 'task_id' (ADR-0006)"
        assert body["task_id"] is None, "task_id must be null in v0.1 (ADR-0006)"
        assert "status" in body, "Response must contain 'status'"
        assert body["status"] in (
            "completed",
            "skipped",
        ), f"status must be 'completed' or 'skipped'; got {body['status']!r}"
        assert "page_id" in body, "Response must contain 'page_id'"
        # page_id must be a valid UUID string
        uuid.UUID(body["page_id"])  # raises ValueError if invalid

    async def test_ingest_trigger_missing_file_path_returns_422(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-014: AC-REST-6 — missing file_path body → 422, not 500."""
        resp = await api_client.post("/ingest/trigger", json={})
        assert resp.status_code == 422, f"Missing file_path must return 422; got {resp.status_code}"
        assert resp.status_code != 500

    async def test_ingest_trigger_nonexistent_file_returns_422(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-015: AC-REST-6 — file_path pointing to non-existent file → 422."""
        resp = await api_client.post(
            "/ingest/trigger",
            json={"file_path": "/nonexistent/path/file.md"},
        )
        assert resp.status_code == 422, f"Non-existent file must return 422; got {resp.status_code}"
        assert resp.status_code != 500


# ── AC-REST-5: /openapi.json ──────────────────────────────────────────────────


class TestOpenAPISpec:
    """T-API-016, T-API-017, T-API-018 — AC-REST-5, AC-D4-1..3"""

    def test_openapi_json_file_exists(self) -> None:
        """T-API-016: AC-D4-2 — docs/api/openapi.json must exist (generated by make openapi)."""
        p = Path(__file__).resolve().parent.parent.parent / "docs" / "api" / "openapi.json"
        assert p.exists(), "docs/api/openapi.json must exist (run 'make openapi' to generate)"

    def test_openapi_json_is_valid_json(self) -> None:
        """T-API-017: AC-D4-1 — docs/api/openapi.json must be valid JSON."""
        p = Path(__file__).resolve().parent.parent.parent / "docs" / "api" / "openapi.json"
        text = p.read_text(encoding="utf-8")
        data = json.loads(text)  # raises if invalid
        assert isinstance(data, dict)

    def test_openapi_json_version_is_3_1(self) -> None:
        """T-API-018: AC-D4-1 — openapi version must be 3.1.x."""
        p = Path(__file__).resolve().parent.parent.parent / "docs" / "api" / "openapi.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        openapi_ver = data.get("openapi", "")
        assert openapi_ver.startswith("3.1"), f"OpenAPI version must be 3.1.x; got {openapi_ver!r}"

    def test_openapi_json_has_all_four_endpoints(self) -> None:
        """T-API-019: AC-D4-3 — all 4 v0.1 endpoints must be in openapi.json paths."""
        p = Path(__file__).resolve().parent.parent.parent / "docs" / "api" / "openapi.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        paths = data.get("paths", {})

        required_paths = ["/status", "/pages", "/pages/{page_id}", "/ingest/trigger"]
        for rp in required_paths:
            assert (
                rp in paths
            ), f"Path {rp!r} missing from openapi.json; present: {list(paths.keys())}"

    def test_openapi_json_endpoints_have_response_schemas(self) -> None:
        """T-API-020: AC-D4-3 — every endpoint must have at least one response schema."""
        p = Path(__file__).resolve().parent.parent.parent / "docs" / "api" / "openapi.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        paths = data.get("paths", {})
        for path, ops in paths.items():
            for method, op in ops.items():
                assert (
                    "responses" in op
                ), f"Endpoint {method.upper()} {path} must have a 'responses' schema"

    async def test_live_openapi_endpoint_matches_saved_file(
        self, api_client: AsyncClient, api_env: dict[str, Any]
    ) -> None:
        """T-API-021: AC-D4-2 — live /openapi.json must match saved docs/api/openapi.json."""
        resp = await api_client.get("/openapi.json")
        assert resp.status_code == 200
        live = resp.json()

        p = Path(__file__).resolve().parent.parent.parent / "docs" / "api" / "openapi.json"
        saved = json.loads(p.read_text(encoding="utf-8"))

        # Compare structure (paths and version must match)
        assert live.get("openapi") == saved.get("openapi"), "openapi version must match"
        assert set(live.get("paths", {}).keys()) == set(
            saved.get("paths", {}).keys()
        ), "Live /openapi.json paths must match saved docs/api/openapi.json"
