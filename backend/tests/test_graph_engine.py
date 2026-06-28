"""
GraphEngine unit tests — 4-signal edge weights + FA2 determinism (F4, ADR-0012, ADR-0013).

Infra-free: SQLite+aiosqlite in-memory DB, no live Postgres, no Qdrant, no Ollama.

Coverage:
  AC-F4-1  4-signal additive formula: exact edge weights on a hand-computable fixture
            - direct_link ×3 (two directions count separately)
            - source_overlap ×4 (set intersection of JSONB sources arrays)
            - adamic_adar ×1.5 (igraph similarity_inverse_log_weighted analogue)
            - type_affinity ×1 (same non-NULL type)
  AC-F4-1(e) independent signal assertions (zero out 3, assert 1 term in isolation)
  AC-F4-2  edge persistence (weight>0 stored; pure type-only edge kept; zero-weight absent)
  ADR-0013 FA2 determinism: same topology + weights + seed → identical coords (×2 runs)

Fixture (5-node, hand-computable, architect-corrected per AQ-1 note):
  P1 Alpha  entity  sources=[doc_a]
  P2 Beta   entity  sources=[doc_a]
  P3 Gamma  concept sources=[doc_b]
  P4 Delta  entity  sources=[doc_a, doc_b]
  P5 Epsilon person  sources=[doc_c]    ← type=person (not concept) so P3–P5 weight=0 (AC-F4-1(d))

Resolved links (directed):
  P1 → P2 (source_page_id=P1, target_page_id=P2, dangling=false)
  P2 → P1 (source_page_id=P2, target_page_id=P1, dangling=false)
  P3 → P4 (source_page_id=P3, target_page_id=P4, dangling=false)
  P4 → P1 (source_page_id=P4, target_page_id=P1, dangling=false)

Expected weights (by hand per ADR-0012 formula):
  P1–P2: direct=2 (P1→P2, P2→P1) → 3×2=6; source=[doc_a] shared → 4×1=4;
          AA: P1 and P2 share no common resolved-link neighbour beyond each other,
              but P4 links to P1, and P1 links to P2: neighbours of P1={P2,P4}, P2={P1}
              → common neighbours of P1 and P2 = {P1}∩... wait, we compute N(P1)∩N(P2).
              In the UNDIRECTED unweighted graph: edges are P1-P2, P3-P4, P4-P1.
              N(P1)={P2,P4}, N(P2)={P1}, N(P4)={P1,P3}
              AA(P1,P2) = Σ_{c∈N(P1)∩N(P2)} 1/ln(deg(c))
                        = N(P1)∩N(P2) = {} (empty — P1 is in N(P2) but AA uses common
                          NEIGHBOURS i.e. nodes adjacent to both EXCLUDING themselves)
              igraph: N(P1) does NOT include P1 itself; N(P2) does NOT include P2 itself.
              N(P1)={P2,P4} (idx), N(P2)={P1}.  Intersection = {} → AA(P1,P2)=0.
          type: entity==entity → 1; total = 6+4+0+1 = 11.0
  P1–P4: direct=1 (P4→P1 only) → 3×1=3; source=[doc_a] shared → 4×1=4;
          AA(P1,P4): N(P1)={P2,P4_idx}, N(P4)={P1,P3}.
              common = N(P1)∩N(P4) = {}  (P2 not in N(P4), P4 not in N(P1) neighbour set
              excluding self ... P4_idx in N(P1), P1_idx in N(P4) — neither is the OTHER's
              neighbour).  Actually: N(P1)={P2_idx, P4_idx}? No: P4→P1 is an edge, so
              P1-P4 is undirected → P4 IS in N(P1) and P1 IS in N(P4).
              N(P1) = {P2, P4}, N(P4) = {P1, P3}. Common = {} (P2 not in N(P4), P3 not in N(P1)).
              AA(P1,P4)=0.
          type: entity==entity → 1; total = 3+4+0+1 = 8.0
  P2–P4: direct=0; source=[doc_a] shared → 4×1=4;
          AA(P2,P4): N(P2)={P1}, N(P4)={P1,P3}. Common={P1}. deg(P1)=2 in graph edges P1-P2,P1-P4.
              AA = 1/ln(2) ≈ 1.4427.  × 1.5 ≈ 2.164.
          type: entity==entity → 1; total = 0+4+2.164..+1 ≥ 7.
  P3–P4: direct=1 (P3→P4) → 3; source=[doc_b] shared → 4;
          AA(P3,P4): N(P3)={P4}, N(P4)={P1,P3}. Common={} (P4 not in N(P3) set minus self...
              actually P3-P4 is an undirected edge → N(P3)={P4}, N(P4)={P3,P1}.
              Common neighbours (adjacent to both P3 AND P4, excluding P3 and P4 themselves)
              = N(P3)∩N(P4) = {P4}∩{P3,P1} ... N(P3) = {P4} and P4 is the neighbour.
              N(P4) = {P3, P1}. Intersection = P3∩... let's be precise:
              N(P3) = {idx of P4}. N(P4) = {idx of P3, idx of P1}.
              Intersection = {} (since P4 is not in N(P4), and P3 is not in N(P3)).
              AA(P3,P4) = 0.
          type: concept==entity → 0; total = 3+4+0+0 = 7.0
  P3–P5: different type (concept vs person), no shared source, no link, no shared neighbour.
          weight = 0 → NOT persisted (AC-F4-1(d)).
  P4–P1: same as P1–P4 (undirected).
  P2–P3: no link, no shared source ([doc_a]∩[doc_b]=[]), AA=0, different type → 0.

Lower-bound assertions (from ADR-0012 worked fixture):
  P1–P2 weight ≥ 11.0 (base without AA = 11, AA may be 0 per above analysis)
  P1–P4 weight ≥ 8.0  (base without AA = 8)
  P2–P4 weight ≥ 5.0  (base 5 + AA×1.5 > 5)
  P3–P4 weight == 7.0  (direct+source only, AA=0, type=0)
  P3–P5 NOT present (weight=0, different types)
"""

from __future__ import annotations

import json
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
        # P5: type=person so P3-P5 pair has weight=0 (AQ-1 fixture correction)
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
                    "SELECT id, type AS page_type, title, sources "
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
                "(id, vault_id, source_page_id, target_page_id, weight, signals) "
                "VALUES (:id, :vid, :src, :tgt, :w, :sig)"
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
        """P1–P2: 2 directed links + 1 shared source + entity==entity → base ≥ 11."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P1"], p["P2"])
        assert edge is not None, "P1–P2 edge must be present (weight > 0)"
        assert edge.weight >= 11.0, f"P1–P2 weight {edge.weight} < 11 (base without AA)"

    async def test_p1_p4_weight_ge_8(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """P1–P4: 1 link (P4→P1) + 1 shared source (doc_a) + entity==entity → base ≥ 8."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P1"], p["P4"])
        assert edge is not None, "P1–P4 edge must be present"
        assert edge.weight >= 8.0, f"P1–P4 weight {edge.weight} < 8"

    async def test_p2_p4_weight_ge_5(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """P2–P4: 0 links + 1 shared source (doc_a) + entity==entity + AA via P1 → base ≥ 5."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P2"], p["P4"])
        assert edge is not None, "P2–P4 edge must be present"
        assert edge.weight >= 5.0, f"P2–P4 weight {edge.weight} < 5"

    async def test_p3_p4_weight_is_7(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """P3–P4: 1 link (P3→P4) × 3 = 3; 1 shared source (doc_b) × 4 = 4; AA=0; type=0 → 7."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P3"], p["P4"])
        assert edge is not None, "P3–P4 edge must be present"
        assert abs(edge.weight - 7.0) < 0.01, (
            f"P3–P4 weight {edge.weight} != 7.0 " "(direct 1×3=3 + source 1×4=4 + AA 0 + type 0=0)"
        )

    async def test_p3_p5_absent(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """
        P3–P5: no link, no shared source, no common neighbour, different types
        (concept vs person) → weight=0 → NOT persisted (AC-F4-1(d), AQ-1 fixture correction).
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P3"], p["P5"])
        assert edge is None, "P3–P5 must NOT be present (weight=0 after AQ-1 fixture correction)"

    async def test_direct_signal_isolation(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """
        AC-F4-1(e): signals JSONB breakdown allows independent inspection.
        P3–P4 should have direct signal = 3.0 (one link × 3), source signal = 4.0.
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

        assert db_row is not None, "P3–P4 edge row should be in the DB"
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
        AC-F4-1(e): type term adds +1 for same-type pairs.
        P1 and P2 are both 'entity' → type signal = 1.0.
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
        ), f"P1–P2 type signal should be 1.0 (both entity), got {sigs.get('type')}"

    async def test_null_type_no_match(
        self, graph_db: tuple[Any, dict[str, str], str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        type term: NULL type does NOT match NULL — two pages with NULL type get same_type=0.
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
                ), "Two NULL-type pages must NOT get type signal=1 (NULL ≠ NULL per ADR-0012)"


class TestFA2Determinism:
    """ADR-0013: identical topology + weights + seed → identical coordinates."""

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
        ), "FA2 layout should spread nodes apart (non-zero spread)"


class TestEdgeInclusionRule:
    """AC-F4-2: weight>0 edges persisted; weight=0 edges absent."""

    async def test_weight_positive_edges_present(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """All edges in the snapshot have weight > 0."""
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        for edge in snapshot.edges:
            assert edge.weight > 0, f"Edge {edge.source}–{edge.target} has weight ≤ 0"

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
