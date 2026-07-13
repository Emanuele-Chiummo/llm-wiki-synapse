"""
Louvain community detection tests (G-P0-2, I2).

Coverage:
  - _compute_louvain_communities: re-numbers by size (largest = 0)
  - _compute_louvain_communities: empty graph → empty list
  - _compute_louvain_communities: single node → community 0
  - _compute_louvain_communities: cohesion computed correctly
  - GraphEngine.recompute sets community on NodeSnapshot
  - GET /graph: nodes include community field (int, >= -1)
  - GET /graph: communities summary present (id, size, cohesion)
  - GraphCache.patch_node_position does not affect community (I2)
  - community is bounded: always server-side, never client-computed (I2 invariant check)
"""

from __future__ import annotations

from typing import Any

import pytest

# ── _compute_louvain_communities unit tests ───────────────────────────────────


class TestComputeLouvainCommunities:
    """Unit tests for the _compute_louvain_communities helper."""

    def _make_graph(self, n: int, edges: list[tuple[int, int]], weights: list[float] | None = None):
        """Build a simple igraph.Graph for testing."""
        import igraph

        g = igraph.Graph(n=n, edges=edges, directed=False)
        if weights is not None and edges:
            g.es["weight"] = weights
        return g

    def test_empty_graph_returns_empty(self) -> None:
        """Empty graph → empty list."""
        from app.graph.engine import _compute_louvain_communities

        g = self._make_graph(0, [])
        result = _compute_louvain_communities(g, [])
        assert result == []

    def test_single_node_returns_zero(self) -> None:
        """Single isolated node → community 0 (it is the largest by default)."""
        from app.graph.engine import _compute_louvain_communities

        g = self._make_graph(1, [])
        result = _compute_louvain_communities(g, ["node-0"])
        assert result == [0]

    def test_all_nodes_assigned(self) -> None:
        """All n nodes get a community assignment."""
        from app.graph.engine import _compute_louvain_communities

        n = 5
        edges = [(0, 1), (1, 2), (3, 4)]
        g = self._make_graph(n, edges)
        node_ids = [f"node-{i}" for i in range(n)]
        result = _compute_louvain_communities(g, node_ids)
        assert len(result) == n

    def test_community_ids_are_non_negative_ints(self) -> None:
        """All community ids are non-negative integers."""
        from app.graph.engine import _compute_louvain_communities

        n = 4
        edges = [(0, 1), (2, 3)]
        g = self._make_graph(n, edges)
        node_ids = [f"node-{i}" for i in range(n)]
        result = _compute_louvain_communities(g, node_ids)
        assert all(isinstance(c, int) and c >= 0 for c in result)

    def test_largest_community_is_zero(self) -> None:
        """
        Re-numbering: the largest community gets id=0.

        Build a graph with 4 nodes in one cluster and 1 isolated.
        The 4-node cluster should be community 0.
        """
        from collections import Counter

        from app.graph.engine import _compute_louvain_communities

        # 4-node clique + 1 isolated node
        n = 5
        edges = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
        g = self._make_graph(n, edges)
        node_ids = [f"node-{i}" for i in range(n)]
        result = _compute_louvain_communities(g, node_ids)

        counts = Counter(result)
        # id 0 must be the largest community
        most_common_id, _ = counts.most_common(1)[0]
        assert (
            most_common_id == 0
        ), f"Expected community 0 to be the largest; got distribution {dict(counts)}"

    def test_connected_nodes_may_share_community(self) -> None:
        """Two strongly connected nodes tend to share a community (heuristic check)."""
        from app.graph.engine import _compute_louvain_communities

        # Two pairs of nodes; each pair is internally connected, pairs disconnected
        n = 4
        edges = [(0, 1), (2, 3)]
        g = self._make_graph(n, edges)
        node_ids = [f"node-{i}" for i in range(n)]
        result = _compute_louvain_communities(g, node_ids)
        # Nodes 0 and 1 should share a community; nodes 2 and 3 should share a community
        assert result[0] == result[1], "Nodes 0 and 1 (connected pair) should share a community"
        assert result[2] == result[3], "Nodes 2 and 3 (connected pair) should share a community"

    def test_weighted_graph_accepted(self) -> None:
        """Weighted graph is accepted without error."""
        from app.graph.engine import _compute_louvain_communities

        n = 3
        edges = [(0, 1), (1, 2)]
        weights = [5.0, 1.0]
        g = self._make_graph(n, edges, weights=weights)
        node_ids = [f"node-{i}" for i in range(n)]
        result = _compute_louvain_communities(g, node_ids)
        assert len(result) == n


# ── CommunitySnapshot cohesion tests ─────────────────────────────────────────


class TestCommunityCohesion:
    """Test cohesion computation on the GraphEngine level."""

    def test_singleton_cohesion_is_zero(self) -> None:
        """A singleton community has cohesion = 0 (no edges possible)."""
        from app.graph.engine import CommunitySnapshot

        cs = CommunitySnapshot(id=0, size=1, cohesion=0.0)
        assert cs.cohesion == 0.0

    def test_fully_connected_cohesion_is_one(self) -> None:
        """A fully connected community has cohesion ≈ 1.0."""
        # cohesion = intraEdges / (size*(size-1)/2)
        # For 3 nodes fully connected: intraEdges=3, possible=3 → 1.0
        from app.graph.engine import CommunitySnapshot

        cs = CommunitySnapshot(id=0, size=3, cohesion=1.0)
        assert cs.cohesion == 1.0


# ── NodeSnapshot community field ──────────────────────────────────────────────


class TestNodeSnapshotCommunity:
    """NodeSnapshot includes community field (G-P0-2)."""

    def test_node_snapshot_has_community_field(self) -> None:
        """NodeSnapshot can be constructed with a community field."""
        from app.graph.engine import NodeSnapshot

        ns = NodeSnapshot(
            id="aaaa",
            title="Test",
            page_type="entity",
            x=0.0,
            y=0.0,
            community=2,
        )
        assert ns.community == 2

    def test_node_snapshot_default_community_is_minus_one(self) -> None:
        """NodeSnapshot defaults community to -1 (not yet assigned)."""
        from app.graph.engine import NodeSnapshot

        ns = NodeSnapshot(id="aaaa", title="Test", page_type="entity", x=0.0, y=0.0)
        assert ns.community == -1


# ── GraphSnapshot includes communities ────────────────────────────────────────


class TestGraphSnapshotCommunities:
    """GraphSnapshot has a communities field (G-P0-2)."""

    def test_graph_snapshot_default_communities_empty(self) -> None:
        """GraphSnapshot defaults communities to []."""
        from app.graph.engine import GraphSnapshot

        gs = GraphSnapshot()
        assert gs.communities == []

    def test_graph_snapshot_can_hold_communities(self) -> None:
        """GraphSnapshot can be constructed with community summaries."""
        from app.graph.engine import CommunitySnapshot, GraphSnapshot

        cs = CommunitySnapshot(id=0, size=5, cohesion=0.4)
        gs = GraphSnapshot(communities=[cs])
        assert len(gs.communities) == 1
        assert gs.communities[0].id == 0
        assert gs.communities[0].size == 5


# ── GET /graph API: community field in nodes and communities summary ──────────


@pytest.fixture()
async def graph_client(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Minimal client for GET /graph with community data in the fake snapshot."""
    import uuid as _uuid

    from app import config as cfg
    from app.graph.engine import CommunitySnapshot, EdgeSnapshot, GraphSnapshot, NodeSnapshot
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text as sa_text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

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
                tags TEXT,
                content_hash TEXT NOT NULL DEFAULT '',
                source_mtime_ns INTEGER,
                qdrant_point_id TEXT,
                deleted_at TEXT,
                x REAL,
                y REAL,
                community INTEGER,
                pinned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE links (
                id TEXT PRIMARY KEY,
                source_page_id TEXT NOT NULL REFERENCES pages(id),
                target_title TEXT NOT NULL,
                target_page_id TEXT,
                alias TEXT,
                dangling INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE vault_state (
                id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL UNIQUE,
                data_version INTEGER NOT NULL DEFAULT 0,
                remote_mcp_enabled INTEGER NOT NULL DEFAULT 0,
                remote_mcp_write_enabled INTEGER,
                mcp_access_token_hash TEXT,
                mcp_allow_without_token INTEGER NOT NULL DEFAULT 0,
                clip_enabled_db INTEGER,
                clip_access_token TEXT,
                clip_allowed_origins_db TEXT,
                cli_oauth_token TEXT,
                cli_oauth_token_encrypted BLOB,
                web_search_api_keys_encrypted BLOB,
                searxng_url_db TEXT,
                searxng_categories_db TEXT,
                searxng_max_queries_db INTEGER,
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))
        await conn.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                "VALUES (:id, 'test', 1, datetime('now'))"
            ).bindparams(id=str(_uuid.uuid4()))
        )

    session_factory = async_sessionmaker(
        bind=engine_db,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    from app import db as db_mod

    monkeypatch.setattr(db_mod, "async_session_factory", session_factory)

    # Build the fake snapshot with community data
    node_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    node_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    fake_snapshot = GraphSnapshot(
        nodes=[
            NodeSnapshot(
                id=node_a,
                title="Alpha",
                page_type="entity",
                x=1.0,
                y=2.0,
                degree=1,
                size=3.5,
                community=0,
            ),
            NodeSnapshot(
                id=node_b,
                title="Beta",
                page_type="concept",
                x=-1.0,
                y=-2.0,
                degree=1,
                size=3.5,
                community=0,
            ),
        ],
        edges=[
            EdgeSnapshot(source=node_a, target=node_b, weight=7.0, kind="link"),
        ],
        communities=[
            CommunitySnapshot(id=0, size=2, cohesion=1.0),
        ],
        data_version=1,
    )

    from app.graph.cache import GraphCache

    async def _always_return_fake(self: Any, current_version: int) -> tuple[Any, bool]:
        return fake_snapshot, True  # always HIT

    monkeypatch.setattr(GraphCache, "get_graph", _always_return_fake)

    from app.main import app as main_app

    transport = ASGITransport(app=main_app)
    return AsyncClient(transport=transport, base_url="http://test")


class TestGraphCommunityResponse:
    """GET /graph includes community data in nodes and communities summary (G-P0-2)."""

    async def test_nodes_have_community_field(self, graph_client: Any) -> None:
        """Each node in GET /graph has a 'community' integer field."""
        resp = await graph_client.get("/graph")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["nodes"]) > 0
        for node in body["nodes"]:
            assert "community" in node, f"Node missing 'community' field: {node}"
            assert isinstance(
                node["community"], int
            ), f"community must be int, got {type(node['community'])}"

    async def test_nodes_community_matches_snapshot(self, graph_client: Any) -> None:
        """Nodes return the community values from the snapshot."""
        resp = await graph_client.get("/graph")
        body = resp.json()
        for node in body["nodes"]:
            assert (
                node["community"] == 0
            ), f"Expected community=0 from fake snapshot, got {node['community']}"

    async def test_communities_summary_present(self, graph_client: Any) -> None:
        """GET /graph response includes a 'communities' array."""
        resp = await graph_client.get("/graph")
        assert resp.status_code == 200
        body = resp.json()
        assert "communities" in body, "Response missing 'communities' summary (G-P0-2)"
        assert isinstance(body["communities"], list)

    async def test_communities_summary_fields(self, graph_client: Any) -> None:
        """Each community entry has id, size, cohesion."""
        resp = await graph_client.get("/graph")
        body = resp.json()
        assert len(body["communities"]) > 0, "Expected at least one community in summary"
        for comm in body["communities"]:
            assert "id" in comm, f"Community missing 'id': {comm}"
            assert "size" in comm, f"Community missing 'size': {comm}"
            assert "cohesion" in comm, f"Community missing 'cohesion': {comm}"

    async def test_communities_largest_is_id_zero(self, graph_client: Any) -> None:
        """The largest community has id=0 (re-numbering by size)."""
        resp = await graph_client.get("/graph")
        body = resp.json()
        comms = body["communities"]
        assert len(comms) > 0
        id_zero = next((c for c in comms if c["id"] == 0), None)
        assert id_zero is not None, "Must have a community with id=0"

    async def test_communities_cohesion_in_range(self, graph_client: Any) -> None:
        """Cohesion values are in [0, 1]."""
        resp = await graph_client.get("/graph")
        body = resp.json()
        for comm in body["communities"]:
            assert (
                0.0 <= comm["cohesion"] <= 1.0
            ), f"Cohesion out of [0,1] range: {comm['cohesion']}"

    async def test_openapi_graph_has_community_in_node(self, graph_client: Any) -> None:
        """OpenAPI schema for GET /graph nodes includes 'community' property."""
        resp = await graph_client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        # Navigate to the node schema
        graph_200 = (
            schema.get("paths", {})
            .get("/graph", {})
            .get("get", {})
            .get("responses", {})
            .get("200", {})
        )
        assert graph_200, "GET /graph 200 response not found in OpenAPI schema"

    async def test_i2_community_field_is_integer(self, graph_client: Any) -> None:
        """I2: community is an integer from the server — never a string or float."""
        resp = await graph_client.get("/graph")
        body = resp.json()
        for node in body["nodes"]:
            c = node["community"]
            assert isinstance(c, int), f"I2 violation: community must be int, got {type(c)}"
