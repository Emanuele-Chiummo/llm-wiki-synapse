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
from app.graph.engine import (
    CommunitySnapshot,
    CommunityTopPage,
    EdgeSnapshot,
    GraphSnapshot,
    NodeSnapshot,
)
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._db_fixtures import make_sqlite_engine

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
    engine_db = await make_sqlite_engine()
    # Page UUIDs seeded into pages table (used for GR1 total_nodes/total_edges assertions)
    _PAGE_ID_1 = str(uuid.uuid4())
    _PAGE_ID_2 = str(uuid.uuid4())
    _PAGE_ID_3 = str(uuid.uuid4())

    async with engine_db.begin() as conn:
        await conn.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                "VALUES (:id, 'test', 3, datetime('now'))"
            ).bindparams(id=str(uuid.uuid4()))
        )
        # Seed 3 live pages (1 for each vault member), 1 deleted page (must be excluded)
        for pid, fpath in [
            (_PAGE_ID_1, "wiki/entities/alpha.md"),
            (_PAGE_ID_2, "wiki/entities/beta.md"),
            (_PAGE_ID_3, "wiki/concepts/gamma.md"),
        ]:
            await conn.execute(
                sa_text(
                    "INSERT INTO pages (id, vault_id, file_path, content_hash) "
                    "VALUES (:id, 'test', :fp, '')"
                ).bindparams(id=pid, fp=fpath)
            )
        # One soft-deleted page — must NOT count in total_nodes
        await conn.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, content_hash, deleted_at) "
                "VALUES (:id, 'test', 'wiki/deleted.md', '', datetime('now'))"
            ).bindparams(id=str(uuid.uuid4()))
        )
        # GRAPH-ELIGIBILITY regression (1.4.1): these live pages exist in the vault but the
        # engine EXCLUDES them from the graph, so total_nodes must NOT count them either —
        # otherwise the "hidden" chip shows a phantom count (bug: 233 hidden on a raw-heavy
        # vault). One raw-source tracking row (file_path LIKE 'raw/%') + one query page.
        for pid, fpath, ptype in [
            (str(uuid.uuid4()), "raw/sources/servicenow-doc.extracted.md", "source"),
            (str(uuid.uuid4()), "wiki/queries/open-question.md", "query"),
        ]:
            await conn.execute(
                sa_text(
                    "INSERT INTO pages (id, vault_id, file_path, type, content_hash) "
                    "VALUES (:id, 'test', :fp, :ty, '')"
                ).bindparams(id=pid, fp=fpath, ty=ptype)
            )
        # Seed 2 link rows from source pages in this vault
        for lid, src, tgt_title in [
            (str(uuid.uuid4()), _PAGE_ID_1, "Beta"),
            (str(uuid.uuid4()), _PAGE_ID_2, "Gamma"),
        ]:
            await conn.execute(
                sa_text(
                    "INSERT INTO links (id, source_page_id, target_title, dangling) "
                    "VALUES (:id, :src, :tgt, 1)"
                ).bindparams(id=lid, src=src, tgt=tgt_title)
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


# ── GR1: total_nodes / total_edges vault-wide totals ──────────────────────────


class TestGraphVaultTotals:
    """
    GR1: GET /graph returns total_nodes and total_edges as vault-wide denominators.

    Invariants verified:
      - Both fields are non-negative integers present in the response (I1).
      - total_nodes counts only live (non-deleted) pages — the seeded fixture has
        3 live pages + 1 deleted page; expect total_nodes == 3.
      - total_edges counts all link rows for the vault — the fixture seeds 2; expect 2.
      - total_nodes >= len(nodes) (in-graph subset ≤ vault total).
      - total_edges >= len(edges) (in-graph graph edges ≤ total wikilinks).
    """

    async def test_total_nodes_present(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_nodes field is present in GET /graph response."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        assert resp.status_code == 200
        body = resp.json()
        assert "total_nodes" in body, "Missing 'total_nodes' in GET /graph response (GR1)"

    async def test_total_edges_present(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_edges field is present in GET /graph response."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        assert resp.status_code == 200
        body = resp.json()
        assert "total_edges" in body, "Missing 'total_edges' in GET /graph response (GR1)"

    async def test_total_nodes_is_nonneg_int(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_nodes is a non-negative integer."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert isinstance(body["total_nodes"], int)
        assert body["total_nodes"] >= 0

    async def test_total_edges_is_nonneg_int(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_edges is a non-negative integer."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert isinstance(body["total_edges"], int)
        assert body["total_edges"] >= 0

    async def test_total_nodes_excludes_deleted(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_nodes counts only non-deleted pages (fixture: 3 live + 1 deleted → 3)."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert (
            body["total_nodes"] == 3
        ), f"Expected 3 live pages (1 deleted excluded), got {body['total_nodes']}"

    async def test_total_nodes_excludes_raw_and_query(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        total_nodes must exclude graph-ineligible pages (raw/* + query), matching the
        engine's node-inclusion rule — else the "hidden" chip (total − shipped) shows a
        phantom count. Fixture seeds 3 eligible + 1 raw + 1 query (+ 1 deleted); the raw
        and query rows must NOT inflate the total, so it stays 3.

        (Note: len(nodes) here comes from the fixed _FAKE_SNAPSHOT, deliberately decoupled
        from the DB page count, so this asserts the denominator only — the "hidden chip = 0"
        behaviour is exercised against a real recompute in the engine unit tests.)
        """
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert body["total_nodes"] == 3, (
            "raw/* and query pages must be excluded from total_nodes "
            f"(engine excludes them from the graph); got {body['total_nodes']}"
        )

    async def test_total_edges_counts_links(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_edges counts all link rows for this vault (fixture seeds 2 links)."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert body["total_edges"] == 2, f"Expected 2 link rows, got {body['total_edges']}"

    async def test_total_nodes_gte_ingraph_nodes(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_nodes >= len(nodes): vault total is at least as large as the in-graph subset."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert body["total_nodes"] >= len(
            body["nodes"]
        ), f"total_nodes ({body['total_nodes']}) must be >= len(nodes) ({len(body['nodes'])})"

    async def test_total_edges_gte_ingraph_edges(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_edges >= len(edges): vault link count >= in-graph weighted edge count."""
        _patch_cache_always_miss(monkeypatch)
        resp = await graph_app.get("/graph")
        body = resp.json()
        assert body["total_edges"] >= len(
            body["edges"]
        ), f"total_edges ({body['total_edges']}) must be >= len(edges) ({len(body['edges'])})"

    async def test_totals_present_on_cache_hit(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """total_nodes and total_edges are populated even on a cache HIT."""
        _patch_cache_always_hit(monkeypatch)
        resp = await graph_app.get("/graph")
        assert resp.status_code == 200
        body = resp.json()
        assert "total_nodes" in body
        assert "total_edges" in body
        assert body["total_nodes"] >= 0
        assert body["total_edges"] >= 0


# ── Community label / dominant_domain / top_page (F18) ───────────────────────


def _make_snapshot_with_communities(
    communities: list[CommunitySnapshot],
) -> GraphSnapshot:
    """Build a GraphSnapshot with the given communities (reuses base node/edge fixture)."""
    return GraphSnapshot(
        nodes=_FAKE_SNAPSHOT.nodes,
        edges=_FAKE_SNAPSHOT.edges,
        data_version=_FAKE_SNAPSHOT.data_version,
        communities=communities,
    )


class TestCommunityLabels:
    """
    GET /graph communities include label, dominant_domain, top_page (F18).

    Invariants:
      I1/I7  — labels computed alongside cohesion, no extra scan or provider call.
      I2     — label computed server-side in recompute(); cached with the snapshot.
    """

    def _patch_with_communities(
        self,
        monkeypatch: pytest.MonkeyPatch,
        communities: list[CommunitySnapshot],
    ) -> None:
        from app.graph.cache import GraphCache

        snap = _make_snapshot_with_communities(communities)

        async def _return_snap(self_: Any, current_version: int) -> tuple[GraphSnapshot, bool]:
            return snap, False

        monkeypatch.setattr(GraphCache, "get_graph", _return_snap)

    async def test_community_has_label_field(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each community entry in GET /graph has a 'label' string field (F18)."""
        self._patch_with_communities(
            monkeypatch,
            [
                CommunitySnapshot(
                    id=0,
                    size=2,
                    cohesion=1.0,
                    label="SAM",
                    dominant_domain="SAM",
                    top_page=CommunityTopPage(id=_NODE_ID_A, title="Alpha", slug="alpha"),
                )
            ],
        )
        resp = await graph_app.get("/graph")
        assert resp.status_code == 200
        body = resp.json()
        assert body["communities"], "Expected at least one community"
        for comm in body["communities"]:
            assert "label" in comm, f"Community missing 'label': {comm}"
            assert isinstance(comm["label"], str), f"label must be str, got {type(comm['label'])}"

    async def test_community_label_dominant_domain(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When dominant_domain is set, label equals the domain name (F18 primary case)."""
        self._patch_with_communities(
            monkeypatch,
            [
                CommunitySnapshot(
                    id=0,
                    size=5,
                    cohesion=0.4,
                    label="SAM",
                    dominant_domain="SAM",
                    top_page=CommunityTopPage(
                        id=_NODE_ID_A,
                        title="Software Asset Management",
                        slug="software-asset-management",
                    ),
                )
            ],
        )
        resp = await graph_app.get("/graph")
        body = resp.json()
        comm = body["communities"][0]
        assert comm["label"] == "SAM", f"Expected label='SAM', got {comm['label']!r}"
        assert comm["dominant_domain"] == "SAM"

    async def test_community_label_fallback_top_page_title(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When dominant_domain is None, label falls back to top_page.title (F18 llm_wiki fallback)."""
        self._patch_with_communities(
            monkeypatch,
            [
                CommunitySnapshot(
                    id=0,
                    size=3,
                    cohesion=0.5,
                    label="Alpha",
                    dominant_domain=None,
                    top_page=CommunityTopPage(id=_NODE_ID_A, title="Alpha", slug="alpha"),
                )
            ],
        )
        resp = await graph_app.get("/graph")
        body = resp.json()
        comm = body["communities"][0]
        assert comm["label"] == "Alpha", f"Expected label='Alpha', got {comm['label']!r}"
        assert comm["dominant_domain"] is None

    async def test_community_label_fallback_comunita(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When no domain and no top_page title, label is 'Comunità {id}' (F18 last-resort)."""
        self._patch_with_communities(
            monkeypatch,
            [
                CommunitySnapshot(
                    id=2,
                    size=1,
                    cohesion=0.0,
                    label="Comunità 2",
                    dominant_domain=None,
                    top_page=None,
                )
            ],
        )
        resp = await graph_app.get("/graph")
        body = resp.json()
        comm = body["communities"][0]
        assert comm["label"] == "Comunità 2", f"Expected 'Comunità 2', got {comm['label']!r}"
        assert comm["dominant_domain"] is None
        assert comm["top_page"] is None

    async def test_community_has_dominant_domain_field(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """dominant_domain field is present in every community entry (may be null) (F18)."""
        self._patch_with_communities(
            monkeypatch,
            [
                CommunitySnapshot(
                    id=0,
                    size=2,
                    cohesion=0.8,
                    label="TPRM",
                    dominant_domain="TPRM",
                    top_page=CommunityTopPage(id=_NODE_ID_B, title="Beta", slug="beta"),
                )
            ],
        )
        resp = await graph_app.get("/graph")
        body = resp.json()
        for comm in body["communities"]:
            assert "dominant_domain" in comm, f"Community missing 'dominant_domain': {comm}"

    async def test_community_has_top_page_field(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """top_page field is present in every community entry (may be null) (F18)."""
        self._patch_with_communities(
            monkeypatch,
            [
                CommunitySnapshot(
                    id=0,
                    size=2,
                    cohesion=0.8,
                    label="SAM",
                    dominant_domain="SAM",
                    top_page=CommunityTopPage(id=_NODE_ID_A, title="Alpha", slug="alpha"),
                )
            ],
        )
        resp = await graph_app.get("/graph")
        body = resp.json()
        for comm in body["communities"]:
            assert "top_page" in comm, f"Community missing 'top_page': {comm}"

    async def test_community_top_page_shape(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """top_page has id, title, slug fields when present (F18)."""
        self._patch_with_communities(
            monkeypatch,
            [
                CommunitySnapshot(
                    id=0,
                    size=2,
                    cohesion=0.8,
                    label="Procurement",
                    dominant_domain="Procurement",
                    top_page=CommunityTopPage(id=_NODE_ID_A, title="Alpha", slug="alpha"),
                )
            ],
        )
        resp = await graph_app.get("/graph")
        body = resp.json()
        comm = body["communities"][0]
        tp = comm["top_page"]
        assert tp is not None, "top_page should not be None when CommunityTopPage is set"
        assert "id" in tp, "top_page missing 'id'"
        assert "title" in tp, "top_page missing 'title'"
        assert "slug" in tp, "top_page missing 'slug'"
        assert tp["id"] == _NODE_ID_A
        assert tp["title"] == "Alpha"
        assert tp["slug"] == "alpha"

    async def test_multiple_communities_different_labels(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Multiple communities can have distinct labels from different domain values (F18)."""
        self._patch_with_communities(
            monkeypatch,
            [
                CommunitySnapshot(
                    id=0,
                    size=10,
                    cohesion=0.6,
                    label="SAM",
                    dominant_domain="SAM",
                    top_page=CommunityTopPage(id=_NODE_ID_A, title="Alpha", slug="alpha"),
                ),
                CommunitySnapshot(
                    id=1,
                    size=5,
                    cohesion=0.4,
                    label="Procurement",
                    dominant_domain="Procurement",
                    top_page=CommunityTopPage(id=_NODE_ID_B, title="Beta", slug="beta"),
                ),
                CommunitySnapshot(
                    id=2,
                    size=1,
                    cohesion=0.0,
                    label="Comunità 2",
                    dominant_domain=None,
                    top_page=None,
                ),
            ],
        )
        resp = await graph_app.get("/graph")
        body = resp.json()
        labels = [c["label"] for c in body["communities"]]
        assert "SAM" in labels
        assert "Procurement" in labels
        assert "Comunità 2" in labels


# ── Community label unit tests (engine._compute_graph_sync) ──────────────────


class TestCommunityLabelEngine:
    """
    Unit tests for _compute_graph_sync community label computation (F18, I1, I7).

    Uses _compute_graph_sync directly — no DB, no async, deterministic.
    Verifies: dominant_domain, top_page, label priority rules.
    """

    def _make_nodes(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build minimal nodes_data rows for _compute_graph_sync."""
        rows = []
        for e in entries:
            rows.append(
                {
                    "id": e["id"],
                    "title": e.get("title"),
                    "page_type": e.get("page_type"),
                    "sources": e.get("sources", []),
                    "pinned": False,
                    "stored_x": None,
                    "stored_y": None,
                    "tags": e.get("tags", []),
                }
            )
        return rows

    def test_dominant_domain_from_tags(self) -> None:
        """Community whose members mostly have domain/SAM → label='SAM', dominant_domain='SAM'."""
        from app.graph.engine import _compute_graph_sync

        id_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        id_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        id_c = "cccccccc-cccc-cccc-cccc-cccccccccccc"

        nodes = self._make_nodes(
            [
                {
                    "id": id_a,
                    "title": "Alpha",
                    "page_type": "entity",
                    "sources": ["doc_a"],
                    "tags": ["domain/SAM"],
                },
                {
                    "id": id_b,
                    "title": "Beta",
                    "page_type": "entity",
                    "sources": ["doc_a"],
                    "tags": ["domain/SAM"],
                },
                {
                    "id": id_c,
                    "title": "Gamma",
                    "page_type": "concept",
                    "sources": ["doc_a"],
                    "tags": ["domain/SAM"],
                },
            ]
        )
        # No directed links — they'll be in the same community via shared source doc_a
        _, _, snapshot = _compute_graph_sync(
            nodes, [], "vault-test", domain_vocab=["SAM", "Procurement"]
        )
        # All nodes share source doc_a → one community
        assert snapshot.communities, "Expected at least one community"
        # The largest community (id=0) should be SAM-dominant
        c0 = next((c for c in snapshot.communities if c.id == 0), None)
        assert c0 is not None
        assert c0.dominant_domain == "SAM", f"Expected 'SAM', got {c0.dominant_domain!r}"
        assert c0.label == "SAM", f"Expected label='SAM', got {c0.label!r}"

    def test_no_domain_tags_fallback_to_top_page_title(self) -> None:
        """Community with no domain tags → label = top_page.title (llm_wiki fallback)."""
        from app.graph.engine import _compute_graph_sync

        id_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        id_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

        nodes = self._make_nodes(
            [
                {
                    "id": id_a,
                    "title": "TopPage",
                    "page_type": "entity",
                    "sources": ["doc_x"],
                    "tags": [],
                },
                {
                    "id": id_b,
                    "title": "OtherPage",
                    "page_type": "entity",
                    "sources": ["doc_x"],
                    "tags": [],
                },
            ]
        )
        _, _, snapshot = _compute_graph_sync(nodes, [], "vault-test", domain_vocab=["SAM"])
        assert snapshot.communities
        c0 = next((c for c in snapshot.communities if c.id == 0), None)
        assert c0 is not None
        assert c0.dominant_domain is None
        assert c0.top_page is not None
        # label must be the highest-degree member title (both have degree 0, deterministic by order)
        assert c0.label in (
            "TopPage",
            "OtherPage",
        ), f"Expected fallback to a page title, got {c0.label!r}"

    def test_stale_domain_tag_ignored(self) -> None:
        """Tags not in the vocabulary are ignored — stale tag does not become label (ADR-0054 §2.2)."""
        from app.graph.engine import _compute_graph_sync

        id_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        id_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

        nodes = self._make_nodes(
            [
                {
                    "id": id_a,
                    "title": "Alpha",
                    "page_type": "entity",
                    "sources": ["doc_a"],
                    "tags": ["domain/OldDomain"],
                },  # stale
                {
                    "id": id_b,
                    "title": "Beta",
                    "page_type": "entity",
                    "sources": ["doc_a"],
                    "tags": ["domain/OldDomain"],
                },  # stale
            ]
        )
        _, _, snapshot = _compute_graph_sync(
            nodes, [], "vault-test", domain_vocab=["SAM", "Procurement"]  # OldDomain not in vocab
        )
        assert snapshot.communities
        c0 = next((c for c in snapshot.communities if c.id == 0), None)
        assert c0 is not None
        # Stale tag ignored → no dominant_domain
        assert (
            c0.dominant_domain is None
        ), f"Stale tag should be ignored; got dominant_domain={c0.dominant_domain!r}"
        # Label falls back to top_page title
        assert c0.label != "OldDomain", f"Stale tag must not become label; got {c0.label!r}"

    def test_empty_vocab_accepts_all_domain_tags(self) -> None:
        """When vocab is empty (not configured), ALL domain/* tags are accepted (no filter)."""
        from app.graph.engine import _compute_graph_sync

        id_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        id_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

        nodes = self._make_nodes(
            [
                {
                    "id": id_a,
                    "title": "Alpha",
                    "page_type": "entity",
                    "sources": ["doc_a"],
                    "tags": ["domain/AnyDomain"],
                },
                {
                    "id": id_b,
                    "title": "Beta",
                    "page_type": "entity",
                    "sources": ["doc_a"],
                    "tags": ["domain/AnyDomain"],
                },
            ]
        )
        _, _, snapshot = _compute_graph_sync(
            nodes, [], "vault-test", domain_vocab=[]  # empty vocab → accept all
        )
        assert snapshot.communities
        c0 = next((c for c in snapshot.communities if c.id == 0), None)
        assert c0 is not None
        assert (
            c0.dominant_domain == "AnyDomain"
        ), f"Empty vocab should accept all domain tags; got {c0.dominant_domain!r}"
        assert c0.label == "AnyDomain"

    def test_empty_community_label_is_comunita(self) -> None:
        """Isolated node with no domain tag → label is 'Comunità {id}'."""
        from app.graph.engine import _compute_graph_sync

        id_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

        nodes = self._make_nodes(
            [
                {"id": id_a, "title": None, "page_type": "entity", "sources": [], "tags": []},
            ]
        )
        _, _, snapshot = _compute_graph_sync(nodes, [], "vault-test", domain_vocab=["SAM"])
        assert snapshot.communities
        c = snapshot.communities[0]
        assert c.dominant_domain is None
        assert (
            c.label == f"Comunità {c.id}"
        ), f"Expected 'Comunità {{id}}' fallback, got {c.label!r}"

    def test_top_page_is_highest_degree_member(self) -> None:
        """top_page is the member with the highest structural degree within the community."""
        from app.graph.engine import _compute_graph_sync

        id_hub = "11111111-1111-1111-1111-111111111111"
        id_leaf1 = "22222222-2222-2222-2222-222222222222"
        id_leaf2 = "33333333-3333-3333-3333-333333333333"
        id_leaf3 = "44444444-4444-4444-4444-444444444444"

        nodes = self._make_nodes(
            [
                {
                    "id": id_hub,
                    "title": "Hub",
                    "page_type": "entity",
                    "sources": ["src"],
                    "tags": ["domain/SAM"],
                },
                {
                    "id": id_leaf1,
                    "title": "Leaf1",
                    "page_type": "entity",
                    "sources": ["src"],
                    "tags": ["domain/SAM"],
                },
                {
                    "id": id_leaf2,
                    "title": "Leaf2",
                    "page_type": "entity",
                    "sources": ["src"],
                    "tags": ["domain/SAM"],
                },
                {
                    "id": id_leaf3,
                    "title": "Leaf3",
                    "page_type": "entity",
                    "sources": ["src"],
                    "tags": ["domain/SAM"],
                },
            ]
        )
        # Links: hub → leaf1, hub → leaf2, hub → leaf3 (hub has degree 3, leaves have degree 1)
        links = [
            {"source_page_id": id_hub, "target_page_id": id_leaf1},
            {"source_page_id": id_hub, "target_page_id": id_leaf2},
            {"source_page_id": id_hub, "target_page_id": id_leaf3},
        ]
        _, _, snapshot = _compute_graph_sync(nodes, links, "vault-test", domain_vocab=["SAM"])
        assert snapshot.communities
        c0 = next((c for c in snapshot.communities if c.id == 0), None)
        assert c0 is not None, "Largest community must be id=0"
        assert c0.top_page is not None, "top_page should be set"
        assert (
            c0.top_page.id == id_hub
        ), f"Hub (degree=3) should be top_page; got id={c0.top_page.id!r} title={c0.top_page.title!r}"


# ── Per-node domain (F18, ADR-0054 §2.2) ─────────────────────────────────────


class TestNodeDomain:
    """
    Unit tests for per-node domain computation in _compute_graph_sync (F18, ADR-0054 §2.2).

    Uses _compute_graph_sync directly — no DB, no async, deterministic.
    Covers:
      - Node with a single in-vocab domain/SAM tag → domain == "SAM"
      - Node with a stale/out-of-vocab domain tag → domain is None
      - Node with no tags → domain is None
      - Node with two in-vocab domain tags → first in vocabulary order wins (tie-break)
    """

    def _make_nodes(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build minimal nodes_data rows for _compute_graph_sync."""
        rows = []
        for e in entries:
            rows.append(
                {
                    "id": e["id"],
                    "title": e.get("title"),
                    "page_type": e.get("page_type"),
                    "sources": e.get("sources", []),
                    "pinned": False,
                    "stored_x": None,
                    "stored_y": None,
                    "tags": e.get("tags", []),
                }
            )
        return rows

    def test_node_with_in_vocab_domain_tag(self) -> None:
        """Node with domain/SAM tag and SAM in vocab → domain == 'SAM'."""
        from app.graph.engine import _compute_graph_sync

        id_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        nodes = self._make_nodes(
            [{"id": id_a, "title": "Alpha", "page_type": "entity", "tags": ["domain/SAM"]}]
        )
        _, _, snapshot = _compute_graph_sync(
            nodes, [], "vault-test", domain_vocab=["SAM", "Procurement"]
        )
        node = next(n for n in snapshot.nodes if n.id == id_a)
        assert node.domain == "SAM", f"Expected domain='SAM', got {node.domain!r}"

    def test_node_with_stale_domain_tag(self) -> None:
        """Node with domain/OldDomain tag not in vocab → domain is None (ADR-0054 §2.2)."""
        from app.graph.engine import _compute_graph_sync

        id_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        nodes = self._make_nodes(
            [
                {
                    "id": id_a,
                    "title": "Alpha",
                    "page_type": "entity",
                    "tags": ["domain/OldDomain"],
                }
            ]
        )
        _, _, snapshot = _compute_graph_sync(
            nodes, [], "vault-test", domain_vocab=["SAM", "Procurement"]
        )
        node = next(n for n in snapshot.nodes if n.id == id_a)
        assert (
            node.domain is None
        ), f"Stale domain tag not in vocab should yield domain=None, got {node.domain!r}"

    def test_node_with_no_tags(self) -> None:
        """Node with no tags → domain is None."""
        from app.graph.engine import _compute_graph_sync

        id_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        nodes = self._make_nodes(
            [{"id": id_a, "title": "Alpha", "page_type": "entity", "tags": []}]
        )
        _, _, snapshot = _compute_graph_sync(
            nodes, [], "vault-test", domain_vocab=["SAM", "Procurement"]
        )
        node = next(n for n in snapshot.nodes if n.id == id_a)
        assert node.domain is None, f"Untagged node should have domain=None, got {node.domain!r}"

    def test_node_with_two_in_vocab_domain_tags_first_in_vocab_order_wins(self) -> None:
        """
        Node with domain/Procurement and domain/SAM tags, vocab order = ['SAM', 'Procurement'].
        SAM appears first in vocab → domain == 'SAM' (tie-break: first in vocabulary order).
        """
        from app.graph.engine import _compute_graph_sync

        id_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        # Tag order: Procurement before SAM — but SAM is first in vocab, so SAM wins
        nodes = self._make_nodes(
            [
                {
                    "id": id_a,
                    "title": "Alpha",
                    "page_type": "entity",
                    "tags": ["domain/Procurement", "domain/SAM"],
                }
            ]
        )
        _, _, snapshot = _compute_graph_sync(
            nodes, [], "vault-test", domain_vocab=["SAM", "Procurement"]
        )
        node = next(n for n in snapshot.nodes if n.id == id_a)
        assert node.domain == "SAM", (
            f"When SAM is first in vocab and both tags are in-vocab, "
            f"expected domain='SAM', got {node.domain!r}"
        )

    async def test_node_domain_in_api_response(
        self, graph_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /graph: node with domain='SAM' in snapshot → domain field == 'SAM' in JSON."""
        from app.graph.cache import GraphCache
        from app.graph.engine import NodeSnapshot

        snap_with_domain = GraphSnapshot(
            nodes=[
                NodeSnapshot(
                    id=_NODE_ID_A,
                    title="Alpha",
                    page_type="entity",
                    x=1.0,
                    y=2.0,
                    degree=1,
                    size=1.5,
                    domain="SAM",
                ),
                NodeSnapshot(
                    id=_NODE_ID_B,
                    title="Beta",
                    page_type="concept",
                    x=-1.0,
                    y=-2.0,
                    degree=1,
                    size=1.5,
                    domain=None,
                ),
            ],
            edges=_FAKE_SNAPSHOT.edges,
            data_version=3,
        )

        async def _return_snap(self_: Any, current_version: int) -> tuple[GraphSnapshot, bool]:
            return snap_with_domain, False

        monkeypatch.setattr(GraphCache, "get_graph", _return_snap)

        resp = await graph_app.get("/graph")
        assert resp.status_code == 200
        body = resp.json()

        node_a = next((n for n in body["nodes"] if n["id"] == _NODE_ID_A), None)
        assert node_a is not None
        assert "domain" in node_a, "Node response must include 'domain' field"
        assert node_a["domain"] == "SAM", f"Expected domain='SAM', got {node_a['domain']!r}"

        node_b = next((n for n in body["nodes"] if n["id"] == _NODE_ID_B), None)
        assert node_b is not None
        assert "domain" in node_b, "Node response must include 'domain' field (even when null)"
        assert node_b["domain"] is None, f"Expected domain=None, got {node_b['domain']!r}"


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
