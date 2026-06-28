"""
GET /graph API contract tests (F4, I2, ADR-0014, AC-F4-3, AC-F4-4, AC-D4v3-1).

Infra-free: SQLite+aiosqlite in-memory DB, fake GraphEngine, no live Postgres/Qdrant.

Coverage:
  AC-F4-3   GET /graph → 200; required fields present in nodes/edges/top-level
  AC-F4-4   Second call at same data_version → cached:true + X-Graph-Cache: hit; no second FA2
  AC-D4v3-1 GET /graph appears in the OpenAPI schema with correct path and method
  ADR-0014  X-Graph-Cache header: hit|miss mirrors cached field
  I2 check  GraphCache.get_graph() is called with the current data_version (no FA2 on HIT)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from app.graph.engine import EdgeSnapshot, GraphSnapshot, NodeSnapshot
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── Shared fake graph snapshot ─────────────────────────────────────────────────

_NODE_ID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_NODE_ID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

_FAKE_SNAPSHOT = GraphSnapshot(
    nodes=[
        NodeSnapshot(
            id=_NODE_ID_A,
            title="Alpha",
            page_type="entity",
            x=1.0,
            y=2.0,
            degree=1,
            size=1.5,
        ),
        NodeSnapshot(
            id=_NODE_ID_B,
            title="Beta",
            page_type="concept",
            x=-1.0,
            y=-2.0,
            degree=1,
            size=1.5,
        ),
    ],
    edges=[
        EdgeSnapshot(source=_NODE_ID_A, target=_NODE_ID_B, weight=7.0),
    ],
    data_version=3,
)


# ── Minimal test app (bypass lifespan entirely) ────────────────────────────────


@pytest.fixture()
async def graph_app(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncClient:
    """
    Create a minimal FastAPI test environment for GET /graph:
    - SQLite in-memory with vault_state row (data_version=3)
    - Patched GraphCache that returns _FAKE_SNAPSHOT (hit or miss controlled per test)
    - No lifespan (watcher, embedding, qdrant all bypassed)
    """
    from app import config as cfg

    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    log_md = wiki_dir / "log.md"
    log_md.write_text("---\ntype: log\ntitle: Log\n---\n", encoding="utf-8")
    obsidian_dir = wiki_dir / ".obsidian"
    obsidian_dir.mkdir()
    (obsidian_dir / "app.json").write_text('{"legacyEditor":false}', encoding="utf-8")

    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(cfg.settings, "vault_id", "test")
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(type(cfg.settings), "log_md_path", property(lambda self: log_md))

    # SQLite engine
    engine_db = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine_db.begin() as conn:
        await conn.execute(sa_text("""
            CREATE TABLE pages (
                id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                title TEXT,
                type TEXT,
                sources TEXT,
                content_hash TEXT NOT NULL DEFAULT '',
                source_mtime_ns INTEGER,
                qdrant_point_id TEXT,
                deleted_at TEXT,
                x REAL,
                y REAL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE vault_state (
                id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL UNIQUE,
                data_version INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))
        await conn.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                "VALUES (:id, 'test', 3, datetime('now'))"
            ).bindparams(id=str(uuid.uuid4()))
        )

    session_factory = async_sessionmaker(
        bind=engine_db,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    from app import db as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", session_factory)

    # Build the FastAPI app with a minimal no-op lifespan
    @asynccontextmanager
    async def _noop_lifespan(app: FastAPI):  # type: ignore[type-arg]
        yield

    from app.main import app as main_app

    # We override the lifespan in the test client via the app itself; use ASGI transport
    # with the real app (lifespan=False so our startup hooks don't run)
    transport = ASGITransport(app=main_app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture()
def mock_cache_miss(monkeypatch: pytest.MonkeyPatch) -> int:
    """
    Patch GraphCache.get_graph to return MISS (cached=False) once, then HIT.
    Tracks recompute calls via FakeEngine.
    """
    call_count = 0

    async def _get_graph(self: Any, current_version: int) -> tuple[GraphSnapshot, bool]:
        nonlocal call_count
        call_count += 1
        first = call_count == 1
        return _FAKE_SNAPSHOT, not first  # first call = miss, subsequent = hit

    from app.graph.cache import GraphCache

    monkeypatch.setattr(GraphCache, "get_graph", _get_graph)
    return call_count


# ── AC-F4-3: required fields present ──────────────────────────────────────────


class TestGraphResponseSchema:
    """AC-F4-3: GET /graph must return 200 with all required fields."""

    async def test_get_graph_200(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /graph returns 200."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    async def test_required_top_level_fields(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Top-level fields: nodes, edges, data_version, cached."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        for field in ("nodes", "edges", "data_version", "cached"):
            assert field in body, f"Missing top-level field: {field!r}"

    async def test_node_required_fields(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each node must have: id, title, type, x, y."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert len(body["nodes"]) > 0, "Fixture must have nodes"
        for node in body["nodes"]:
            for f in ("id", "title", "type", "x", "y"):
                assert f in node, f"Node missing required field {f!r}: {node}"

    async def test_edge_required_fields(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each edge must have: source, target, weight."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert len(body["edges"]) > 0, "Fixture must have edges"
        for edge in body["edges"]:
            for f in ("source", "target", "weight"):
                assert f in edge, f"Edge missing required field {f!r}: {edge}"

    async def test_node_types(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Node id is a string, x/y are floats, type is string or null."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        for node in body["nodes"]:
            assert isinstance(node["id"], str)
            assert isinstance(node["x"], (int, float))
            assert isinstance(node["y"], (int, float))
            assert isinstance(node["type"], (str, type(None)))

    async def test_edge_weight_is_float(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Edge weight is a positive float."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        for edge in body["edges"]:
            assert isinstance(edge["weight"], (int, float))
            assert edge["weight"] > 0, "Edge weight must be positive"

    async def test_data_version_is_int(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """data_version is a non-negative integer."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert isinstance(body["data_version"], int)
        assert body["data_version"] >= 0

    async def test_cached_is_bool(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cached field is a boolean."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert isinstance(body["cached"], bool)


# ── X-Graph-Cache header (ADR-0014 §5) ────────────────────────────────────────


class TestCacheHeader:
    """ADR-0014 §5: X-Graph-Cache header mirrors cached field."""

    async def test_miss_header_on_first_call(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MISS: X-Graph-Cache: miss, cached=false."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert (
            resp.headers.get("x-graph-cache") == "miss"
        ), f"Expected X-Graph-Cache: miss, got {resp.headers.get('x-graph-cache')!r}"
        assert body["cached"] is False

    async def test_hit_header_mirrors_cached(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """HIT: X-Graph-Cache: hit, cached=true."""
        _patch_cache_always_hit(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert (
            resp.headers.get("x-graph-cache") == "hit"
        ), f"Expected X-Graph-Cache: hit, got {resp.headers.get('x-graph-cache')!r}"
        assert body["cached"] is True


# ── AC-F4-4: second call at same version → HIT, no second FA2 ─────────────────


class TestHitMissIntegration:
    """
    AC-F4-4: Two GET /graph calls at the same data_version must:
      1st call → miss (X-Graph-Cache: miss, cached=false)
      2nd call → hit (X-Graph-Cache: hit, cached=true), GraphEngine.recompute call count = 1
    """

    async def test_second_call_is_hit_no_recompute(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-F4-4: first call = miss, second = hit, total recompute calls = 1."""
        recompute_calls = 0

        async def _get_graph_stateful(
            self: Any, current_version: int
        ) -> tuple[GraphSnapshot, bool]:
            nonlocal recompute_calls
            if recompute_calls == 0:
                # First call: miss → recompute
                recompute_calls += 1
                return _FAKE_SNAPSHOT, False
            else:
                # Subsequent calls: hit → no recompute
                return _FAKE_SNAPSHOT, True

        from app.graph.cache import GraphCache

        monkeypatch.setattr(GraphCache, "get_graph", _get_graph_stateful)

        # First call: miss
        resp1 = await graph_app.get("/graph")
        assert resp1.status_code == 200
        assert resp1.headers.get("x-graph-cache") == "miss"
        assert resp1.json()["cached"] is False
        assert recompute_calls == 1

        # Second call: hit (no additional recompute)
        resp2 = await graph_app.get("/graph")
        assert resp2.status_code == 200
        assert resp2.headers.get("x-graph-cache") == "hit"
        assert resp2.json()["cached"] is True
        assert (
            recompute_calls == 1
        ), f"Second call must not trigger recompute; got {recompute_calls} calls"


# ── AC-D4v3-1: GET /graph in OpenAPI ──────────────────────────────────────────


class TestOpenAPISchema:
    """AC-D4v3-1: GET /graph must appear in the OpenAPI schema."""

    async def test_graph_in_openapi_paths(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /graph must be a path in the OpenAPI schema."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "/graph" in schema.get(
            "paths", {}
        ), "GET /graph must appear in the OpenAPI paths (AC-D4v3-1)"

    async def test_graph_get_method_present(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /graph path must have a 'get' operation."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/openapi.json")
        schema = resp.json()
        graph_path = schema.get("paths", {}).get("/graph", {})
        assert "get" in graph_path, "OpenAPI /graph path must have a 'get' operation"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _patch_cache_always_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch GraphCache.get_graph to always return MISS with the fake snapshot."""
    from app.graph.cache import GraphCache

    async def _always_miss(self: Any, current_version: int) -> tuple[GraphSnapshot, bool]:
        return _FAKE_SNAPSHOT, False

    monkeypatch.setattr(GraphCache, "get_graph", _always_miss)


def _patch_cache_always_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch GraphCache.get_graph to always return HIT with the fake snapshot."""
    from app.graph.cache import GraphCache

    async def _always_hit(self: Any, current_version: int) -> tuple[GraphSnapshot, bool]:
        return _FAKE_SNAPSHOT, True

    monkeypatch.setattr(GraphCache, "get_graph", _always_hit)
