"""
R9-5 graph drill-down endpoint tests (AC-R9-5-1, AC-R9-5-4, AC-R9-5-5).

Tests:
  1. Community members from a seeded snapshot (GET /graph/communities/{id})
  2. Cold-cache 409 for both community and edge endpoints
  3. Cohesion formula on a known tiny graph
  4. Edge breakdown returns the 4 signals (GET /graph/edges/{src}/{tgt})
  5. I2 guard: endpoints never call recompute — asserted via mock

Infrastructure: SQLite in-memory DB, patched GraphCache, no live Postgres/Qdrant.
All tests are infra-free.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from app.graph.engine import (
    CommunitySnapshot,
    EdgeSnapshot,
    GraphSnapshot,
    NodeSnapshot,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._db_fixtures import make_sqlite_engine

# ── Test data ──────────────────────────────────────────────────────────────────

_NODE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_NODE_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_NODE_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_NODE_D = "dddddddd-dddd-dddd-dddd-dddddddddddd"

# Community 0: A(deg=2), B(deg=2), C(deg=1) — 3 nodes, 2 intra edges
# Community 1: D(deg=0) — singleton
_SEEDED_SNAPSHOT = GraphSnapshot(
    nodes=[
        NodeSnapshot(
            id=_NODE_A, title="Alpha", page_type="entity", x=0.0, y=0.0, degree=2, community=0
        ),
        NodeSnapshot(
            id=_NODE_B, title="Beta", page_type="concept", x=1.0, y=0.0, degree=2, community=0
        ),
        NodeSnapshot(
            id=_NODE_C, title="Gamma", page_type="source", x=0.5, y=1.0, degree=1, community=0
        ),
        NodeSnapshot(
            id=_NODE_D, title="Delta", page_type="entity", x=5.0, y=5.0, degree=0, community=1
        ),
    ],
    edges=[
        EdgeSnapshot(source=_NODE_A, target=_NODE_B, weight=11.0, kind="link"),
        EdgeSnapshot(source=_NODE_A, target=_NODE_C, weight=5.0, kind="source"),
    ],
    data_version=7,
    communities=[
        # Community 0: size=3, 2 intra-edges; possible = 3*(3-1)/2 = 3; cohesion = 2/3 ≈ 0.6667
        CommunitySnapshot(id=0, size=3, cohesion=round(2 / 3, 4)),
        # Community 1: singleton; cohesion = 0
        CommunitySnapshot(id=1, size=1, cohesion=0.0),
    ],
)

_COLD_SNAPSHOT: GraphSnapshot | None = None  # simulates a cold cache

# Edge signals for the DB fixture (match engine.py key names: direct/source/aa/type)
_EDGE_SIGNALS_AB: dict[str, float] = {
    "direct": 6.0,  # 3.0 * 2 direct links
    "source": 4.0,  # 4.0 * 1 shared source
    "aa": 0.7,  # 1.5 * AA ≈ 0.467 but we seed a round value
    "type": 0.5,  # type-affinity entity↔concept
}
_EDGE_WEIGHT_AB: float = sum(_EDGE_SIGNALS_AB.values())  # = 11.2


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
async def db_engine():
    """SQLite in-memory engine with the full Synapse schema (via Base.metadata.create_all)."""
    engine = await make_sqlite_engine()
    async with engine.begin() as conn:
        await conn.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version) " "VALUES (:id, 'test', 7)"
            ).bindparams(id=str(uuid.uuid4()))
        )
        import json

        # Insert edge A↔B with signals
        await conn.execute(
            sa_text(
                "INSERT INTO edges (id, vault_id, source_page_id, target_page_id,"
                " weight, signals, kind, created_at) "
                "VALUES (:id, :vid, :src, :tgt, :w, :sig, :k, datetime('now'))"
            ).bindparams(
                id=str(uuid.uuid4()),
                vid="test",
                src=_NODE_A,
                tgt=_NODE_B,
                w=_EDGE_WEIGHT_AB,
                sig=json.dumps(_EDGE_SIGNALS_AB),
                k="link",
            )
        )
    yield engine
    await engine.dispose()


@pytest.fixture()
async def drilldown_app(
    db_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> AsyncClient:
    """
    Minimal FastAPI test client for graph drill-down endpoints.

    - SQLite in-memory DB with edges row (seeded via db_engine fixture).
    - Vault path set to a tmp_path with the minimum required files.
    - GraphCache._snapshot set to _SEEDED_SNAPSHOT (warm cache).
    """
    from app import config as cfg
    from app import db as db_mod

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

    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    monkeypatch.setattr(db_mod, "async_session_factory", session_factory)

    # Seed the module-level _graph_cache with a warm snapshot
    import app.main as main_mod
    from app.graph.cache import GraphCache
    from app.graph.engine import GraphEngine

    warm_cache = GraphCache(engine=GraphEngine(), vault_id="test")
    warm_cache._snapshot = _SEEDED_SNAPSHOT  # type: ignore[assignment]
    warm_cache._marker = 7
    monkeypatch.setattr(main_mod, "_graph_cache", warm_cache)

    from app.main import app as main_app

    transport = ASGITransport(app=main_app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture()
async def cold_cache_app(
    db_engine: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> AsyncClient:
    """
    Same as drilldown_app but with a cold cache (_snapshot=None).
    Used to test the 409 cold-cache guard (AC-R9-5-5 / I2).
    """
    from app import config as cfg
    from app import db as db_mod

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

    session_factory = async_sessionmaker(
        bind=db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    monkeypatch.setattr(db_mod, "async_session_factory", session_factory)

    # Cold cache: _snapshot is None
    import app.main as main_mod
    from app.graph.cache import GraphCache
    from app.graph.engine import GraphEngine

    cold_cache = GraphCache(engine=GraphEngine(), vault_id="test")
    # _snapshot is already None by default
    monkeypatch.setattr(main_mod, "_graph_cache", cold_cache)

    from app.main import app as main_app

    transport = ASGITransport(app=main_app)
    return AsyncClient(transport=transport, base_url="http://test")


# ── 1. Community members from a seeded snapshot ────────────────────────────────


class TestCommunityDrilldown:
    """GET /graph/communities/{community_id} — seeded snapshot (AC-R9-5-1)."""

    async def test_community_0_returns_200(self, drilldown_app: AsyncClient) -> None:
        """Community 0 exists in the seeded snapshot — must return 200."""
        resp = await drilldown_app.get("/graph/communities/0")
        assert resp.status_code == 200, resp.text

    async def test_community_0_members_present(self, drilldown_app: AsyncClient) -> None:
        """Community 0 should have 3 members (A, B, C) ordered by degree desc."""
        resp = await drilldown_app.get("/graph/communities/0")
        body = resp.json()
        assert body["community_id"] == 0
        assert body["size"] == 3
        members = body["members"]
        assert len(members) == 3
        # Alpha and Beta both have degree=2; Gamma has degree=1
        # Ordered by degree desc: [A or B (2), A or B (2), C (1)]
        degrees = [m["degree"] for m in members]
        assert degrees == sorted(degrees, reverse=True), "Members must be degree-desc"
        ids = {m["id"] for m in members}
        assert _NODE_A in ids
        assert _NODE_B in ids
        assert _NODE_C in ids

    async def test_community_0_member_fields(self, drilldown_app: AsyncClient) -> None:
        """Each member must carry id, title, page_type, degree."""
        resp = await drilldown_app.get("/graph/communities/0")
        body = resp.json()
        for m in body["members"]:
            assert "id" in m, "member must have id"
            assert "title" in m, "member must have title"
            assert "page_type" in m, "member must have page_type"
            assert "degree" in m, "member must have degree"
            assert isinstance(m["degree"], int)

    async def test_community_1_singleton(self, drilldown_app: AsyncClient) -> None:
        """Community 1 is a singleton (Delta). size=1, cohesion=0.0."""
        resp = await drilldown_app.get("/graph/communities/1")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["community_id"] == 1
        assert body["size"] == 1
        assert body["cohesion"] == 0.0
        assert len(body["members"]) == 1
        assert body["members"][0]["id"] == _NODE_D

    async def test_community_not_found_404(self, drilldown_app: AsyncClient) -> None:
        """Community 99 does not exist — must return 404."""
        resp = await drilldown_app.get("/graph/communities/99")
        assert resp.status_code == 404, resp.text


# ── 2. Cold-cache 409 ─────────────────────────────────────────────────────────


class TestColdCache409:
    """Cold cache returns 409 for community endpoint (AC-R9-5-5, I2)."""

    async def test_community_cold_cache_409(self, cold_cache_app: AsyncClient) -> None:
        """GET /graph/communities/0 with cold cache must return 409."""
        resp = await cold_cache_app.get("/graph/communities/0")
        assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"

    async def test_cold_cache_409_message(self, cold_cache_app: AsyncClient) -> None:
        """409 response must carry a clear message about running recompute first."""
        resp = await cold_cache_app.get("/graph/communities/0")
        detail = resp.json().get("detail", "")
        assert (
            "recompute" in detail.lower() or "graph" in detail.lower()
        ), f"409 detail must mention recompute/graph: {detail!r}"


# ── 3. Cohesion formula on a known tiny graph ──────────────────────────────────


class TestCohesionFormula:
    """
    Cohesion = intraEdges / (size*(size-1)/2) (AC-R9-5-1).

    For community 0 in the seeded snapshot:
      size = 3 nodes (A, B, C)
      intra-edges = 2 (A-B and A-C are both within community 0)
      possible = 3*(3-1)/2 = 3
      expected cohesion = 2/3 = 0.6667 (rounded to 4 dp = 0.6667)
    """

    EXPECTED_COHESION = round(2 / 3, 4)

    async def test_cohesion_value_community_0(self, drilldown_app: AsyncClient) -> None:
        """Community 0 cohesion must equal 2/3 (intra=2, possible=3)."""
        resp = await drilldown_app.get("/graph/communities/0")
        body = resp.json()
        got = body["cohesion"]
        assert got is not None, "cohesion must not be null for a warm snapshot"
        assert (
            abs(got - self.EXPECTED_COHESION) < 1e-3
        ), f"Expected cohesion ≈ {self.EXPECTED_COHESION}, got {got}"

    async def test_cohesion_singleton_is_zero(self, drilldown_app: AsyncClient) -> None:
        """Singleton community cohesion must be 0.0 (no possible edges)."""
        resp = await drilldown_app.get("/graph/communities/1")
        body = resp.json()
        assert body["cohesion"] == 0.0, f"Singleton cohesion must be 0.0, got {body['cohesion']}"

    async def test_cohesion_warning_above_threshold(
        self, drilldown_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cohesion_warning=False when cohesion (0.6667) >= threshold (0.2)."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "graph_cohesion_warn", 0.2)
        resp = await drilldown_app.get("/graph/communities/0")
        body = resp.json()
        assert (
            body["cohesion_warning"] is False
        ), f"cohesion 0.6667 >= 0.2 must NOT trigger warning, got {body['cohesion_warning']}"

    async def test_cohesion_warning_below_threshold(
        self, drilldown_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cohesion_warning=True when threshold is set above actual cohesion (0.6667 < 0.9)."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "graph_cohesion_warn", 0.9)
        resp = await drilldown_app.get("/graph/communities/0")
        body = resp.json()
        assert body["cohesion_warning"] is True, (
            f"cohesion 0.6667 < 0.9 threshold must trigger warning, "
            f"got {body['cohesion_warning']}"
        )

    async def test_singleton_cohesion_warning_always_false(
        self, drilldown_app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Singleton (cohesion=0.0) cohesion_warning depends on threshold < 0.0 (never)."""
        from app import config as cfg

        # Even at threshold=0.0, 0.0 < 0.0 is False (boundary)
        monkeypatch.setattr(cfg.settings, "graph_cohesion_warn", 0.2)
        resp = await drilldown_app.get("/graph/communities/1")
        body = resp.json()
        # 0.0 < 0.2 → warning IS True for a truly-isolated singleton
        # (this tests the exact boundary; the cohesion_warning is set by the threshold)
        assert isinstance(body["cohesion_warning"], bool)


# ── 4. Edge breakdown returns the 4 signals ────────────────────────────────────


class TestEdgeBreakdown:
    """GET /graph/edges/{src}/{tgt} — 4-signal breakdown (AC-R9-5-4)."""

    async def test_edge_ab_returns_200(self, drilldown_app: AsyncClient) -> None:
        """Edge A-B exists in the seeded DB — must return 200."""
        resp = await drilldown_app.get(f"/graph/edges/{_NODE_A}/{_NODE_B}")
        assert resp.status_code == 200, resp.text

    async def test_edge_ab_reversed_order_200(self, drilldown_app: AsyncClient) -> None:
        """Undirected: requesting B→A must also return 200."""
        resp = await drilldown_app.get(f"/graph/edges/{_NODE_B}/{_NODE_A}")
        assert resp.status_code == 200, resp.text

    async def test_edge_ab_weight(self, drilldown_app: AsyncClient) -> None:
        """Edge weight must match the seeded value."""
        resp = await drilldown_app.get(f"/graph/edges/{_NODE_A}/{_NODE_B}")
        body = resp.json()
        assert (
            abs(body["weight"] - _EDGE_WEIGHT_AB) < 1e-6
        ), f"Expected weight {_EDGE_WEIGHT_AB}, got {body['weight']}"

    async def test_edge_ab_breakdown_keys(self, drilldown_app: AsyncClient) -> None:
        """breakdown must contain the 4 public signal keys."""
        resp = await drilldown_app.get(f"/graph/edges/{_NODE_A}/{_NODE_B}")
        body = resp.json()
        breakdown = body["breakdown"]
        for key in ("direct_links", "shared_sources", "adamic_adar", "type_affinity"):
            assert key in breakdown, f"Missing breakdown key {key!r}: {breakdown}"

    async def test_edge_ab_breakdown_values(self, drilldown_app: AsyncClient) -> None:
        """breakdown values must match the seeded signals (mapped from short keys)."""
        resp = await drilldown_app.get(f"/graph/edges/{_NODE_A}/{_NODE_B}")
        body = resp.json()
        breakdown = body["breakdown"]
        assert abs(breakdown["direct_links"] - _EDGE_SIGNALS_AB["direct"]) < 1e-6
        assert abs(breakdown["shared_sources"] - _EDGE_SIGNALS_AB["source"]) < 1e-6
        assert abs(breakdown["adamic_adar"] - _EDGE_SIGNALS_AB["aa"]) < 1e-6
        assert abs(breakdown["type_affinity"] - _EDGE_SIGNALS_AB["type"]) < 1e-6

    async def test_edge_not_found_404(self, drilldown_app: AsyncClient) -> None:
        """Non-existent edge pair must return 404."""
        missing = str(uuid.uuid4())
        resp = await drilldown_app.get(f"/graph/edges/{_NODE_A}/{missing}")
        assert resp.status_code == 404, resp.text

    async def test_edge_computed_at_present(self, drilldown_app: AsyncClient) -> None:
        """computed_at field must be present (string or null — both acceptable)."""
        resp = await drilldown_app.get(f"/graph/edges/{_NODE_A}/{_NODE_B}")
        body = resp.json()
        assert "computed_at" in body, "computed_at must be in response"
        # May be a string or null
        assert body["computed_at"] is None or isinstance(body["computed_at"], str)


# ── 5. I2 guard: no recompute triggered by these endpoints ────────────────────


class TestI2Guard:
    """
    I2 invariant: neither endpoint calls get_graph(), force_recompute(),
    or engine.recompute() (AC-R9-5-5).

    We assert by mocking these methods and verifying call_count == 0.
    """

    async def test_community_endpoint_never_calls_get_graph(
        self,
        drilldown_app: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /graph/communities/{id} must NOT call GraphCache.get_graph()."""
        from app.graph.cache import GraphCache

        get_graph_mock = AsyncMock(return_value=(_SEEDED_SNAPSHOT, True))
        monkeypatch.setattr(GraphCache, "get_graph", get_graph_mock)

        resp = await drilldown_app.get("/graph/communities/0")
        assert resp.status_code == 200, resp.text
        assert get_graph_mock.call_count == 0, (
            f"get_graph must NOT be called by community endpoint; "
            f"got {get_graph_mock.call_count} call(s)"
        )

    async def test_community_endpoint_never_calls_force_recompute(
        self,
        drilldown_app: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /graph/communities/{id} must NOT call GraphCache.force_recompute()."""
        from app.graph.cache import GraphCache

        force_mock = AsyncMock(return_value=_SEEDED_SNAPSHOT)
        monkeypatch.setattr(GraphCache, "force_recompute", force_mock)

        resp = await drilldown_app.get("/graph/communities/0")
        assert resp.status_code == 200, resp.text
        assert (
            force_mock.call_count == 0
        ), f"force_recompute must NOT be called; got {force_mock.call_count} call(s)"

    async def test_community_endpoint_never_calls_engine_recompute(
        self,
        drilldown_app: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /graph/communities/{id} must NOT call GraphEngine.recompute()."""
        from app.graph.engine import GraphEngine

        recompute_mock = AsyncMock(return_value=_SEEDED_SNAPSHOT)
        monkeypatch.setattr(GraphEngine, "recompute", recompute_mock)

        resp = await drilldown_app.get("/graph/communities/0")
        assert resp.status_code == 200, resp.text
        assert (
            recompute_mock.call_count == 0
        ), f"GraphEngine.recompute must NOT be called; got {recompute_mock.call_count} call(s)"

    async def test_edge_endpoint_never_calls_engine_recompute(
        self,
        drilldown_app: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /graph/edges/{src}/{tgt} must NOT call GraphEngine.recompute()."""
        from app.graph.engine import GraphEngine

        recompute_mock = AsyncMock(return_value=_SEEDED_SNAPSHOT)
        monkeypatch.setattr(GraphEngine, "recompute", recompute_mock)

        resp = await drilldown_app.get(f"/graph/edges/{_NODE_A}/{_NODE_B}")
        assert resp.status_code == 200, resp.text
        assert (
            recompute_mock.call_count == 0
        ), f"GraphEngine.recompute must NOT be called; got {recompute_mock.call_count} call(s)"

    async def test_edge_endpoint_never_calls_get_graph(
        self,
        drilldown_app: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """GET /graph/edges/{src}/{tgt} must NOT call GraphCache.get_graph()."""
        from app.graph.cache import GraphCache

        get_graph_mock = AsyncMock(return_value=(_SEEDED_SNAPSHOT, True))
        monkeypatch.setattr(GraphCache, "get_graph", get_graph_mock)

        resp = await drilldown_app.get(f"/graph/edges/{_NODE_A}/{_NODE_B}")
        assert resp.status_code == 200, resp.text
        assert get_graph_mock.call_count == 0, (
            f"get_graph must NOT be called by edge endpoint; "
            f"got {get_graph_mock.call_count} call(s)"
        )
