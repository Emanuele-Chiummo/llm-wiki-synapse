"""
GraphEngine unit tests -- 4-signal edge weights + FR determinism (F4, ADR-0012, ADR-0013,
ADR-0016).

Infra-free: SQLite+aiosqlite in-memory DB, no live Postgres, no Qdrant, no Ollama.

Coverage:
  AC-F4-1  4-signal additive formula: exact edge weights on a hand-computable fixture
            - direct_link x3 (two directions count separately)
            - source_overlap x4 (set intersection of JSONB sources arrays)
            - adamic_adar x1.5 (igraph similarity_inverse_log_weighted analogue)
            - type_affinity x1 (same non-NULL type)
  AC-F4-1(e) independent signal assertions (zero out 3, assert 1 term in isolation)
  AC-F4-2  edge persistence -- ADR-0016 structural gate (direct>0 OR shared>0);
            type-only pair is ABSENT (not persisted); zero-weight absent.
  ADR-0016  structural edges, kind field, structural_degree size formula.
  ADR-0013  FR determinism: same topology + weights + seed -> identical coords (x2 runs)

Fixture (5-node, hand-computable, architect-corrected per AQ-1 note):
  P1 Alpha   entity  sources=[doc_a]
  P2 Beta    entity  sources=[doc_a]
  P3 Gamma   concept sources=[doc_b]
  P4 Delta   entity  sources=[doc_a, doc_b]
  P5 Epsilon person  sources=[doc_c]  -- isolated: no links, no shared source -> NO edge

Resolved links (directed):
  P1 -> P2 (source_page_id=P1, target_page_id=P2, dangling=false)
  P2 -> P1 (source_page_id=P2, target_page_id=P1, dangling=false)
  P3 -> P4 (source_page_id=P3, target_page_id=P4, dangling=false)
  P4 -> P1 (source_page_id=P4, target_page_id=P1, dangling=false)

Structural edges (ADR-0016 sec 1 -- only direct link OR shared source creates an edge):
  P1-P2: direct(P1->P2, P2->P1)=2 AND shared([doc_a])=1 -> structural (kind="link")
  P1-P4: direct(P4->P1)=1 AND shared([doc_a])=1 -> structural (kind="link")
  P2-P4: direct=0 BUT shared([doc_a])=1 -> structural (kind="source")
  P3-P4: direct(P3->P4)=1 AND shared([doc_b])=1 -> structural (kind="link")
  P3-P5: NO link, NO shared source -> NOT structural -> ABSENT (ADR-0016 sec 1)
  P1-P2 and P1-P4: type signal adds +1 to WEIGHT but does NOT create edges alone.

Expected weights (ADR-0012 sec 1/sec 2, arithmetic UNCHANGED, structural pairs only):
  P1-P2: direct=2 -> 3x2=6; source=1 -> 4; AA=0; type=1 -> total >= 11.0
  P1-P4: direct=1 -> 3; source=1 -> 4; AA=0; type=1 -> total >= 8.0
  P2-P4: direct=0; source=1 -> 4; AA>0 via P1; type=1 -> total >= 5.0
  P3-P4: direct=1 -> 3; source=1 -> 4; AA=0; type=0 -> total = 7.0
  P3-P5: ABSENT -- no structural tie (ADR-0016 sec 6, fixes P3-P5 type-only hairball edge)

ADR-0016 sec 6 note (supersedes ADR-0012 sec 3):
  Under old rule P3-P5 would have been weight=0 (different types). Under ADR-0016 it is
  also absent. This test case is unchanged in outcome but the REASON changes: not
  "weight=0" but "no structural tie". A new test explicitly checks a SAME-type pair
  with no link/source is absent (the real hairball fix).
"""

from __future__ import annotations

import json
import math
import uuid
from typing import Any

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── Fixture helpers ────────────────────────────────────────────────────────────


def _uid() -> str:
    return str(uuid.uuid4())


async def _setup_sqlite(engine: Any) -> None:
    """Create the minimal pages + links + edges tables in SQLite for graph tests."""
    async with engine.begin() as conn:
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
                pinned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE links (
                id TEXT PRIMARY KEY,
                source_page_id TEXT NOT NULL,
                target_title TEXT NOT NULL,
                target_page_id TEXT,
                alias TEXT,
                dangling INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE edges (
                id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                source_page_id TEXT NOT NULL,
                target_page_id TEXT NOT NULL,
                weight REAL NOT NULL,
                signals TEXT,
                kind TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """))


async def _insert_page(
    sess: AsyncSession,
    *,
    page_id: str,
    vault_id: str,
    title: str,
    page_type: str | None,
    sources: list[str],
) -> None:
    await sess.execute(
        sa_text(
            "INSERT INTO pages (id, vault_id, file_path, title, type, sources, content_hash) "
            "VALUES (:id, :vid, :fp, :title, :ptype, :sources, 'hash')"
        ).bindparams(
            id=page_id,
            vid=vault_id,
            fp=f"wiki/{title}.md",
            title=title,
            ptype=page_type,
            sources=json.dumps(sources),
        )
    )


async def _insert_link(
    sess: AsyncSession,
    *,
    link_id: str,
    source_page_id: str,
    target_page_id: str,
    target_title: str,
) -> None:
    await sess.execute(
        sa_text(
            "INSERT INTO links (id, source_page_id, target_title, target_page_id, dangling) "
            "VALUES (:id, :src, :tgt_title, :tgt, 0)"
        ).bindparams(
            id=link_id,
            src=source_page_id,
            tgt_title=target_title,
            tgt=target_page_id,
        )
    )


# ── Shared fixture ─────────────────────────────────────────────────────────────


@pytest.fixture()
async def graph_db(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, str], str]:
    """
    Create an in-memory SQLite DB, populate the 5-node fixture, and patch
    GraphEngine._load_data to read from it.

    Returns (engine, page_ids dict, vault_id).
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await _setup_sqlite(engine)

    vault_id = "test-vault"

    # Page UUIDs (fixed for determinism)
    p: dict[str, str] = {
        "P1": "00000000-0000-0000-0000-000000000001",
        "P2": "00000000-0000-0000-0000-000000000002",
        "P3": "00000000-0000-0000-0000-000000000003",
        "P4": "00000000-0000-0000-0000-000000000004",
        "P5": "00000000-0000-0000-0000-000000000005",
    }

    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with session_factory() as sess:
        # Insert pages
        vid = vault_id
        await _insert_page(
            sess,
            page_id=p["P1"],
            vault_id=vid,
            title="Alpha",
            page_type="entity",
            sources=["doc_a"],
        )
        await _insert_page(
            sess, page_id=p["P2"], vault_id=vid, title="Beta", page_type="entity", sources=["doc_a"]
        )
        await _insert_page(
            sess,
            page_id=p["P3"],
            vault_id=vid,
            title="Gamma",
            page_type="concept",
            sources=["doc_b"],
        )
        await _insert_page(
            sess,
            page_id=p["P4"],
            vault_id=vid,
            title="Delta",
            page_type="entity",
            sources=["doc_a", "doc_b"],
        )
        # P5: type=person, sources=[doc_c] -- isolated (no links to/from fixture, no shared src)
        await _insert_page(
            sess,
            page_id=p["P5"],
            vault_id=vid,
            title="Epsilon",
            page_type="person",
            sources=["doc_c"],
        )

        # Insert resolved links
        await _insert_link(
            sess,
            link_id=_uid(),
            source_page_id=p["P1"],
            target_page_id=p["P2"],
            target_title="Beta",
        )
        await _insert_link(
            sess,
            link_id=_uid(),
            source_page_id=p["P2"],
            target_page_id=p["P1"],
            target_title="Alpha",
        )
        await _insert_link(
            sess,
            link_id=_uid(),
            source_page_id=p["P3"],
            target_page_id=p["P4"],
            target_title="Delta",
        )
        await _insert_link(
            sess,
            link_id=_uid(),
            source_page_id=p["P4"],
            target_page_id=p["P1"],
            target_title="Alpha",
        )

        await sess.commit()

    # Patch GraphEngine._load_data to use our SQLite session
    from app.graph.engine import GraphEngine

    async def _fake_load(
        self: GraphEngine,
        vid: str,
        session: AsyncSession | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        async with session_factory() as s:
            pages_r = await s.execute(
                sa_text(
                    "SELECT id, type AS page_type, title, sources, "
                    "       pinned, x AS stored_x, y AS stored_y "
                    "FROM pages WHERE vault_id = :vid AND deleted_at IS NULL"
                ).bindparams(vid=vid)
            )
            nodes = []
            for row in pages_r:
                d = dict(row._mapping)
                # Parse JSON sources (SQLite stores as text)
                if isinstance(d.get("sources"), str):
                    import json as _json

                    d["sources"] = _json.loads(d["sources"])
                # SQLite stores booleans as integers
                d["pinned"] = bool(d.get("pinned", 0))
                nodes.append(d)

            links_r = await s.execute(
                sa_text(
                    "SELECT source_page_id, target_page_id FROM links "
                    "WHERE dangling = 0 AND target_page_id IS NOT NULL"
                )
            )
            links = [dict(row._mapping) for row in links_r]
        return nodes, links

    async def _fake_persist(
        self: GraphEngine,
        vid: str,
        coord_rows: list[dict[str, Any]],
        edge_rows: list[dict[str, Any]],
        session: AsyncSession | None,
    ) -> None:
        async with session_factory() as s:
            for row in coord_rows:
                await s.execute(
                    sa_text("UPDATE pages SET x = :x, y = :y WHERE id = :id").bindparams(
                        id=str(row["id"]), x=row["x"], y=row["y"]
                    )
                )
            await s.execute(sa_text("DELETE FROM edges WHERE vault_id = :vid").bindparams(vid=vid))
            _EDGE_INS = (
                "INSERT INTO edges "
                "(id, vault_id, source_page_id, target_page_id, weight, signals, kind) "
                "VALUES (:id, :vid, :src, :tgt, :w, :sig, :kind)"
            )
            for row in edge_rows:
                await s.execute(
                    sa_text(_EDGE_INS).bindparams(
                        id=str(uuid.uuid4()),
                        vid=row["vault_id"],
                        src=str(row["source_page_id"]),
                        tgt=str(row["target_page_id"]),
                        w=row["weight"],
                        sig=json.dumps(row["signals"]) if row.get("signals") else None,
                        kind=row.get("kind", "link"),
                    )
                )
            await s.commit()

    monkeypatch.setattr(GraphEngine, "_load_data", _fake_load)
    monkeypatch.setattr(GraphEngine, "_persist_results", _fake_persist)

    return engine, p, vault_id


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestFourSignalWeights:
    """AC-F4-1: exact 4-signal additive weight assertions on the hand-computable fixture."""

    async def test_p1_p2_weight_ge_11(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """P1-P2: 2 directed links + 1 shared source + entity==entity -> base >= 11."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P1"], p["P2"])
        assert edge is not None, "P1-P2 edge must be present (structural: 2 direct links)"
        assert edge.weight >= 11.0, f"P1-P2 weight {edge.weight} < 11 (base without AA)"

    async def test_p1_p4_weight_ge_8(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """P1-P4: 1 link (P4->P1) + 1 shared source (doc_a) + entity==entity -> base >= 8."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P1"], p["P4"])
        assert edge is not None, "P1-P4 edge must be present (structural: direct link)"
        assert edge.weight >= 8.0, f"P1-P4 weight {edge.weight} < 8"

    async def test_p2_p4_weight_ge_5(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """P2-P4: 0 links + 1 shared source (doc_a) + entity==entity + AA via P1 -> base >= 5."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P2"], p["P4"])
        assert edge is not None, "P2-P4 edge must be present (structural: shared source doc_a)"
        assert edge.weight >= 5.0, f"P2-P4 weight {edge.weight} < 5"

    async def test_p3_p4_weight_is_7(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """P3-P4: 1 link (P3->P4) x 3 = 3; 1 shared source (doc_b) x 4 = 4; AA=0; type=0 -> 7."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P3"], p["P4"])
        assert edge is not None, "P3-P4 edge must be present (structural: direct link)"
        assert abs(edge.weight - 7.0) < 0.01, (
            f"P3-P4 weight {edge.weight} != 7.0 (direct 1x3=3 + source 1x4=4 + AA 0 + type 0=0)"
        )

    async def test_p3_p5_absent(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """
        P3-P5: no link, no shared source -> NOT structural -> ABSENT (ADR-0016 sec 1).
        They have different types, but even if they were same-type this would still be absent
        under ADR-0016 (type is a weight modulator, never an edge generator).
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P3"], p["P5"])
        assert edge is None, "P3-P5 must NOT be present (no structural tie -- ADR-0016 sec 1)"

    async def test_direct_signal_isolation(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """
        AC-F4-1(e): signals JSONB breakdown allows independent inspection.
        P3-P4 should have direct signal = 3.0 (one link x 3), source signal = 4.0.
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P3"], p["P4"])
        assert edge is not None

        # Read the signals from the persisted DB row
        session_factory = _get_session_factory(engine_obj)
        async with session_factory() as s:
            src, tgt = _canonical(p["P3"], p["P4"])
            row = await s.execute(
                sa_text(
                    "SELECT signals FROM edges WHERE source_page_id=:src AND target_page_id=:tgt"
                ).bindparams(src=src, tgt=tgt)
            )
            db_row = row.fetchone()

        assert db_row is not None, "P3-P4 edge row should be in the DB"
        sigs = json.loads(db_row[0]) if db_row[0] else {}
        assert (
            abs(sigs.get("direct", -1) - 3.0) < 0.01
        ), f"direct signal should be 3.0, got {sigs.get('direct')}"
        assert (
            abs(sigs.get("source", -1) - 4.0) < 0.01
        ), f"source signal should be 4.0, got {sigs.get('source')}"
        assert (
            abs(sigs.get("aa", -1) - 0.0) < 0.01
        ), f"aa signal should be 0.0 (no shared neighbours), got {sigs.get('aa')}"
        assert (
            abs(sigs.get("type", -1) - 0.0) < 0.01
        ), f"type signal should be 0.0 (concept vs entity), got {sigs.get('type')}"

    async def test_type_signal_contributes(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """
        AC-F4-1(e): type term adds +1 for same-type pairs THAT ARE ALREADY STRUCTURAL.
        P1 and P2 are both 'entity' and have a direct link -> type signal = 1.0 in signals.
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        await GraphEngine().recompute(vault_id)
        session_factory = _get_session_factory(engine_obj)
        _SIG_Q = "SELECT signals FROM edges WHERE source_page_id=:src AND target_page_id=:tgt"
        async with session_factory() as s:
            src, tgt = _canonical(p["P1"], p["P2"])
            row = await s.execute(sa_text(_SIG_Q).bindparams(src=src, tgt=tgt))
            db_row = row.fetchone()

        assert db_row is not None
        sigs = json.loads(db_row[0]) if db_row[0] else {}
        assert (
            abs(sigs.get("type", -1) - 1.0) < 0.01
        ), f"P1-P2 type signal should be 1.0 (both entity), got {sigs.get('type')}"

    async def test_null_type_no_match(
        self, graph_db: tuple[Any, dict[str, str], str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        type term: NULL type does NOT match NULL -- two pages with NULL type get same_type=0.
        Verified by checking type signal in signals JSONB for a NULL-type pair.
        """
        engine_obj, p, vault_id = graph_db
        session_factory = _get_session_factory(engine_obj)

        # Insert two pages with NULL type and a shared source (so they get an edge)
        pn1 = _uid()
        pn2 = _uid()
        async with session_factory() as s:
            await _insert_page(
                s,
                page_id=pn1,
                vault_id=vault_id,
                title="NullType1",
                page_type=None,
                sources=["shared_null"],
            )
            await _insert_page(
                s,
                page_id=pn2,
                vault_id=vault_id,
                title="NullType2",
                page_type=None,
                sources=["shared_null"],
            )
            await s.commit()

        from app.graph.engine import GraphEngine

        snap_null = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snap_null.edges, pn1, pn2)
        _SIG_Q2 = "SELECT signals FROM edges WHERE source_page_id=:src AND target_page_id=:tgt"
        if edge is not None:
            src_c, tgt_c = _canonical(pn1, pn2)
            async with session_factory() as s:
                row = await s.execute(sa_text(_SIG_Q2).bindparams(src=src_c, tgt=tgt_c))
                db_row = row.fetchone()
            if db_row:
                sigs = json.loads(db_row[0]) if db_row[0] else {}
                assert (
                    sigs.get("type", 0.0) == 0.0
                ), "Two NULL-type pages must NOT get type signal=1 (NULL != NULL per ADR-0012)"


class TestFRDeterminism:
    """ADR-0013: identical topology + weights + seed -> identical coordinates."""

    async def test_same_coords_two_runs(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """Two recompute() calls on the same data produce identical node coordinates."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snap1 = await GraphEngine().recompute(vault_id)
        snap2 = await GraphEngine().recompute(vault_id)

        assert len(snap1.nodes) == len(snap2.nodes), "Node count must be identical"
        coords1 = {n.id: (n.x, n.y) for n in snap1.nodes}
        coords2 = {n.id: (n.x, n.y) for n in snap2.nodes}
        for nid, (x1, y1) in coords1.items():
            x2, y2 = coords2[nid]
            assert abs(x1 - x2) < 1e-9, f"x mismatch for node {nid}: {x1} vs {x2}"
            assert abs(y1 - y2) < 1e-9, f"y mismatch for node {nid}: {y1} vs {y2}"

    async def test_coords_not_all_zero(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """Layout actually produces non-trivial coordinates (not all at origin)."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        coords = [(n.x, n.y) for n in snapshot.nodes]
        # At least some coords should differ (force layout separates nodes)
        xs = [x for x, _ in coords]
        ys = [y for _, y in coords]
        assert (
            max(xs) - min(xs) > 1e-6 or max(ys) - min(ys) > 1e-6
        ), "FR layout should spread nodes apart (non-zero spread)"


class TestEdgeInclusionRule:
    """AC-F4-2 + ADR-0016: structural gate (direct>0 OR shared>0) controls persistence."""

    async def test_weight_positive_edges_present(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """All edges in the snapshot have weight > 0."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        for edge in snapshot.edges:
            assert edge.weight > 0, f"Edge {edge.source}-{edge.target} has weight <= 0"

    async def test_snapshot_nodes_equal_pages(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """All 5 live pages appear as nodes in the snapshot."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        node_ids = {n.id for n in snapshot.nodes}
        for pid in p.values():
            assert pid in node_ids, f"Page {pid} missing from snapshot nodes"

    async def test_same_type_only_pair_absent(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        ADR-0016 sec 1 hairball fix: two pages of SAME type with NO link and NO shared
        source produce NO edge.  Type is a weight modulator, not an edge generator.
        This is the core defect that caused the 4-clique hairball on the 200-node fixture.
        """
        engine_obj, p, vault_id = graph_db
        session_factory = _get_session_factory(engine_obj)

        # Insert two same-type (entity) pages with no links and no shared sources
        pa = _uid()
        pb = _uid()
        async with session_factory() as s:
            await _insert_page(
                s,
                page_id=pa,
                vault_id=vault_id,
                title="SameTypeA",
                page_type="entity",
                sources=[],  # no sources
            )
            await _insert_page(
                s,
                page_id=pb,
                vault_id=vault_id,
                title="SameTypeB",
                page_type="entity",
                sources=[],  # no sources
            )
            # NO links inserted between them
            await s.commit()

        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, pa, pb)
        assert edge is None, (
            "Same-type-only pair (entity+entity, no link, no shared source) must NOT produce "
            "an edge under ADR-0016 sec 1 (type is a weight modulator, not an edge generator)"
        )


class TestADR0016KindAndSize:
    """ADR-0016 sec 4: per-edge kind field + structural_degree drives size monotonically."""

    async def test_direct_link_edge_kind_is_link(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        P1-P2 has direct wikilinks (P1->P2 and P2->P1) -> kind must be 'link'.
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P1"], p["P2"])
        assert edge is not None, "P1-P2 must be present"
        assert edge.kind == "link", (
            f"P1-P2 has direct wikilinks -> kind must be 'link', got {edge.kind!r}"
        )

    async def test_direct_link_edge_kind_link_wins_over_source(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        P3-P4 has BOTH a direct link (P3->P4) AND a shared source (doc_b).
        kind='link' must win (ADR-0016 sec 4: 'link' wins when both structural signals present).
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P3"], p["P4"])
        assert edge is not None, "P3-P4 must be present"
        assert edge.kind == "link", (
            f"P3-P4: direct link exists -> kind='link' wins, got {edge.kind!r}"
        )

    async def test_shared_source_only_edge_kind_is_source(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        P2-P4: NO direct link, but shared source (doc_a) -> kind must be 'source'.
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P2"], p["P4"])
        assert edge is not None, "P2-P4 must be present (shared source doc_a)"
        assert edge.kind == "source", (
            f"P2-P4: no direct link, shared source -> kind='source', got {edge.kind!r}"
        )

    async def test_structural_degree_drives_size_monotonically(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        ADR-0016 sec 2: size = BASE + GROWTH * sqrt(structural_degree).
        Higher-degree nodes must have strictly larger size than lower-degree nodes.
        P1 has direct links to P2 (both directions) and P4 (one direction), plus
        shared sources -> high degree. P5 is isolated -> degree=0, size=1.0.
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        node_map = {n.id: n for n in snapshot.nodes}

        # P5 is isolated (no structural ties) -> degree 0, size = BASE = 1.0
        p5 = node_map[p["P5"]]
        assert p5.degree == 0, f"P5 (isolated) should have degree 0, got {p5.degree}"
        assert abs(p5.size - 1.0) < 0.01, f"P5 isolated node size should be 1.0, got {p5.size}"

        # P1 is connected (structural degree >= 2 from P1-P2 + P1-P4) -> larger than P5
        p1 = node_map[p["P1"]]
        assert p1.degree >= 2, f"P1 should have structural_degree >= 2, got {p1.degree}"
        assert p1.size > p5.size, (
            f"P1 (degree={p1.degree}) must have larger size than P5 (degree=0): "
            f"{p1.size} vs {p5.size}"
        )

        # Verify size formula: BASE + GROWTH * sqrt(deg) with BASE=1.0, GROWTH=2.5
        for node in snapshot.nodes:
            expected = max(1.0, 1.0 + 2.5 * math.sqrt(node.degree))
            assert abs(node.size - expected) < 0.001, (
                f"Node {node.id} size {node.size} != expected {expected:.3f} "
                f"(degree={node.degree})"
            )

    async def test_all_edges_have_valid_kind(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """All edges in the snapshot have kind in {'link', 'source'}."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        for edge in snapshot.edges:
            assert edge.kind in {"link", "source"}, (
                f"Edge {edge.source}-{edge.target} has invalid kind {edge.kind!r}"
            )


class TestStructuralEdgeCount:
    """
    Structural edge count is far smaller than the old type-clique count (ADR-0016).
    5-node fixture has 5 pages; old rule with 3 entity types would give many clique
    edges; new rule gives only structural edges from links + shared sources.
    """

    async def test_only_structural_edges_present(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        The fixture has exactly 4 structural pairs:
          P1-P2 (direct + shared source), P1-P4 (direct + shared source),
          P2-P4 (shared source only), P3-P4 (direct + shared source).
        P5 is isolated. No type-only edges (the hairball fix).
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge_pairs = {frozenset([e.source, e.target]) for e in snapshot.edges}

        expected = {
            frozenset([p["P1"], p["P2"]]),
            frozenset([p["P1"], p["P4"]]),
            frozenset([p["P2"], p["P4"]]),
            frozenset([p["P3"], p["P4"]]),
        }
        assert edge_pairs == expected, (
            f"Structural edges mismatch.\n"
            f"  Expected: {expected}\n"
            f"  Got:      {edge_pairs}"
        )


class TestPinnedNodePreservation:
    """Feature A: pinned=true nodes keep their stored coords across FR recomputes."""

    async def test_pinned_node_coords_preserved_after_recompute(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        A node with pinned=true and stored x/y must get those coords in the snapshot,
        not the FR-computed ones.  This verifies PATCH /pages/{id}/position survives recompute.
        """
        engine_obj, p, vault_id = graph_db
        session_factory = _get_session_factory(engine_obj)

        # Pin P1 to a specific position far from any FR result
        pinned_x = 999.0
        pinned_y = -888.0
        async with session_factory() as s:
            await s.execute(
                sa_text(
                    "UPDATE pages SET pinned = 1, x = :x, y = :y WHERE id = :id"
                ).bindparams(id=p["P1"], x=pinned_x, y=pinned_y)
            )
            await s.commit()

        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        node_map = {n.id: n for n in snapshot.nodes}

        p1 = node_map[p["P1"]]
        assert abs(p1.x - pinned_x) < 0.001, (
            f"Pinned P1 x should be {pinned_x}, got {p1.x} (FR must not overwrite pinned coords)"
        )
        assert abs(p1.y - pinned_y) < 0.001, (
            f"Pinned P1 y should be {pinned_y}, got {p1.y} (FR must not overwrite pinned coords)"
        )

    async def test_unpinned_nodes_coords_are_fr_output(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        Unpinned nodes (pinned=false / 0) get FR-computed coords; their x/y in the
        snapshot must NOT be the sentinel stored value (NULL or 0).
        This verifies Feature B post-processing applies to unpinned nodes.
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        # All nodes start unpinned (default=0 in SQLite fixture)
        snapshot = await GraphEngine().recompute(vault_id)
        # Not all zero or identical — layout must have spread them out
        xs = [n.x for n in snapshot.nodes]
        ys = [n.y for n in snapshot.nodes]
        assert max(xs) - min(xs) > 1e-6 or max(ys) - min(ys) > 1e-6, (
            "Unpinned nodes should have non-trivial spread (FR layout ran)"
        )

    async def test_mixed_pinned_and_free_nodes(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        P1 pinned; P2/P3/P4/P5 free.  P1 must be at pinned coords; others get FR coords
        (different from each other, since FR separates nodes).
        """
        engine_obj, p, vault_id = graph_db
        session_factory = _get_session_factory(engine_obj)

        pinned_x, pinned_y = 500.0, 500.0
        async with session_factory() as s:
            await s.execute(
                sa_text(
                    "UPDATE pages SET pinned = 1, x = :x, y = :y WHERE id = :id"
                ).bindparams(id=p["P1"], x=pinned_x, y=pinned_y)
            )
            await s.commit()

        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        node_map = {n.id: n for n in snapshot.nodes}

        # Pinned node at exact coords
        assert abs(node_map[p["P1"]].x - pinned_x) < 0.001
        assert abs(node_map[p["P1"]].y - pinned_y) < 0.001

        # Free nodes are NOT all at the same spot (FR spread them)
        free_coords = [(node_map[p[k]].x, node_map[p[k]].y) for k in ("P2", "P3", "P4", "P5")]
        xs = [x for x, _ in free_coords]
        ys = [y for _, y in free_coords]
        assert max(xs) - min(xs) > 1e-6 or max(ys) - min(ys) > 1e-6, (
            "Free nodes must have non-trivial spread"
        )


class TestFeatureBDiscEnvelope:
    """Feature B: polar-compression post-process produces a rounder disc envelope."""

    def test_compress_to_disc_basic(self) -> None:
        """_compress_to_disc compresses far-outlier radii while preserving angles."""
        from app.graph.engine import _compress_to_disc

        # Create points along X axis at various radii
        coords = [(0.0, 0.0), (1.0, 0.0), (5.0, 0.0), (20.0, 0.0)]
        result = _compress_to_disc(coords, r_target=10.0, p_high=95, exponent=0.7)

        # Origin stays near origin (radius 0 -> output x,y near centroid)
        # Far outlier (20.0) gets pulled in (radius < 20.0 in result)
        # All are along positive X (angles preserved for non-origin points)
        for (rx, ry) in result:
            # All results within the target disc
            r_out = math.sqrt(rx * rx + ry * ry)
            assert r_out <= 10.01, f"Point outside r_target: ({rx:.3f}, {ry:.3f}), r={r_out:.3f}"

    def test_compress_to_disc_preserves_angles(self) -> None:
        """Angles (polar theta) are preserved exactly by _compress_to_disc."""
        from app.graph.engine import _compress_to_disc

        import math as _math

        # Points at four cardinal directions
        coords = [(3.0, 0.0), (-3.0, 0.0), (0.0, 3.0), (0.0, -3.0)]
        result = _compress_to_disc(coords, r_target=10.0, p_high=95, exponent=0.7)

        # Centroid of input is (0,0) so angles are unchanged
        original_angles = [_math.atan2(y, x) for x, y in coords]
        result_angles = [_math.atan2(y, x) for x, y in result if x != 0.0 or y != 0.0]
        for orig, res in zip(original_angles, result_angles):
            assert abs(orig - res) < 0.001, (
                f"Angle not preserved: original={orig:.4f} result={res:.4f}"
            )

    def test_compress_to_disc_single_node(self) -> None:
        """Single-node input is returned unchanged (degenerate case)."""
        from app.graph.engine import _compress_to_disc

        coords = [(3.14, -2.72)]
        result = _compress_to_disc(coords, r_target=10.0, p_high=95, exponent=0.7)
        assert result == coords

    def test_compress_to_disc_all_same_point(self) -> None:
        """All nodes at same point is returned unchanged (degenerate layout)."""
        from app.graph.engine import _compress_to_disc

        coords = [(1.0, 1.0), (1.0, 1.0), (1.0, 1.0)]
        result = _compress_to_disc(coords, r_target=10.0, p_high=95, exponent=0.7)
        # All at same point -> radius=0 after centering -> returned as-is
        for rx, ry in result:
            r = math.sqrt((rx - 1.0) ** 2 + (ry - 1.0) ** 2)
            assert r < 0.01  # all still near the input centroid


class TestGraphCachePatchNodePosition:
    """Feature A: GraphCache.patch_node_position mutates in-memory snapshot."""

    def test_patch_existing_node_returns_true(self) -> None:
        """patch_node_position returns True and updates coords for known node."""
        from app.graph.cache import GraphCache
        from app.graph.engine import GraphEngine, GraphSnapshot, NodeSnapshot

        cache = GraphCache(engine=GraphEngine(), vault_id="test")
        cache._snapshot = GraphSnapshot(
            nodes=[NodeSnapshot(id="node-1", title="N", page_type=None, x=0.0, y=0.0)],
            edges=[],
        )
        cache._marker = 1

        found = cache.patch_node_position("node-1", 5.5, -3.3)
        assert found is True
        node = cache._snapshot.nodes[0]
        assert abs(node.x - 5.5) < 0.001
        assert abs(node.y - (-3.3)) < 0.001

    def test_patch_unknown_node_returns_false(self) -> None:
        """patch_node_position returns False for a node not in snapshot."""
        from app.graph.cache import GraphCache
        from app.graph.engine import GraphEngine, GraphSnapshot, NodeSnapshot

        cache = GraphCache(engine=GraphEngine(), vault_id="test")
        cache._snapshot = GraphSnapshot(
            nodes=[NodeSnapshot(id="node-1", title="N", page_type=None, x=0.0, y=0.0)],
            edges=[],
        )
        found = cache.patch_node_position("unknown-id", 1.0, 2.0)
        assert found is False

    def test_patch_no_snapshot_returns_false(self) -> None:
        """patch_node_position is a no-op (returns False) when no snapshot exists yet."""
        from app.graph.cache import GraphCache
        from app.graph.engine import GraphEngine

        cache = GraphCache(engine=GraphEngine(), vault_id="test")
        # _snapshot is None (initial state)
        found = cache.patch_node_position("any-id", 1.0, 2.0)
        assert found is False

    def test_patch_does_not_change_marker_or_version(self) -> None:
        """patch_node_position must NOT change _marker, _fire_at, or trigger recompute."""
        from app.graph.cache import GraphCache
        from app.graph.engine import GraphEngine, GraphSnapshot, NodeSnapshot

        cache = GraphCache(engine=GraphEngine(), vault_id="test")
        cache._snapshot = GraphSnapshot(nodes=[NodeSnapshot(id="n1", title=None, page_type=None, x=0.0, y=0.0)], edges=[])
        cache._marker = 42

        cache.patch_node_position("n1", 1.0, 2.0)

        assert cache._marker == 42, "patch_node_position must not change _marker"
        assert cache._fire_at is None, "patch_node_position must not schedule a debounce"
        assert not cache._in_flight, "patch_node_position must not set _in_flight"


# ── Helpers ────────────────────────────────────────────────────────────────────


def _find_edge(edges: list[Any], id_a: str, id_b: str) -> Any | None:
    """Find an undirected edge between id_a and id_b in the snapshot edge list."""
    for e in edges:
        if {e.source, e.target} == {id_a, id_b}:
            return e
    return None


def _canonical(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _get_session_factory(engine: Any) -> Any:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autoflush=False)
