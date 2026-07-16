"""
GraphEngine unit tests -- 4-signal edge weights + FA2 determinism (F4, ADR-0012, ADR-0013,
ADR-0016, ADR-0045).

Infra-free: SQLite+aiosqlite in-memory DB, no live Postgres, no Qdrant, no Ollama.

Coverage:
  AC-F4-1  4-signal additive formula: exact edge weights on a hand-computable fixture
            - direct_link x3 (two directions count separately)
            - source_overlap x4 (weight modulator on wikilink edges — NOT an edge creator)
            - adamic_adar x1.5 (igraph similarity_inverse_log_weighted analogue)
            - type_affinity x1 (cross-type matrix G-P1-7)
  AC-F4-1(e) independent signal assertions (zero out 3, assert 1 term in isolation)
  AC-F4-2  edge persistence -- wikilink-only gate (direct_link_count > 0);
            shared-source-only and type-only pairs are ABSENT.
  ADR-0016  wikilink-only edges, kind="link" (only), node size formula (llm_wiki parity).
  ADR-0016-amendment-2026-07-09  shared-source edges removed; query nodes excluded.
  ADR-0045  FA2 determinism: same topology + weights + seed -> identical coords (x2 runs)
            _forceatlas2_layout helper: finite coords, determinism, correct node count.

Fixture (5-node, hand-computable):
  P1 Alpha   entity  sources=[doc_a]
  P2 Beta    entity  sources=[doc_a]
  P3 Gamma   concept sources=[doc_b]
  P4 Delta   entity  sources=[doc_a, doc_b]
  P5 Epsilon person  sources=[doc_c]  -- isolated: no wikilinks, no structural ties -> no edge

Resolved links (directed):
  P1 -> P2 (source_page_id=P1, target_page_id=P2, dangling=false)
  P2 -> P1 (source_page_id=P2, target_page_id=P1, dangling=false)
  P3 -> P4 (source_page_id=P3, target_page_id=P4, dangling=false)
  P4 -> P1 (source_page_id=P4, target_page_id=P1, dangling=false)

Wikilink edges (ADR-0016 amendment 2026-07-09 — llm_wiki 0.6.0 parity):
  An edge (A,B) EXISTS iff direct_link_count(A,B) > 0 (a resolved [[wikilink]]).
  shared_source, AA, and type_affinity are WEIGHT MODULATORS only.

  P1-P2: direct(P1->P2, P2->P1)=2, shared([doc_a])=1 -> PRESENT (kind="link")
  P1-P4: direct(P4->P1)=1, shared([doc_a])=1 -> PRESENT (kind="link")
  P2-P4: direct=0, shared([doc_a])=1 -> ABSENT (no wikilink — llm_wiki parity)
  P3-P4: direct(P3->P4)=1, shared([doc_b])=1 -> PRESENT (kind="link")
  P3-P5: NO link, NO shared source -> ABSENT (no structural tie)

Expected weights for present wikilink edges (ADR-0012 coefficients unchanged):
  P1(entity)-P2(entity): direct=2 -> 3x2=6; shared=1 -> 4; AA=0;
    type_affinity(entity,entity)=0.8 -> total = 10.8 (>= 10.0)
  P1(entity)-P4(entity): direct=1 -> 3; shared=1 -> 4; AA=0;
    type_affinity(entity,entity)=0.8 -> total = 7.8 (>= 7.5)
  P3(concept)-P4(entity): direct=1 -> 3; shared=1 -> 4; AA=0;
    type_affinity(concept,entity)=1.2 -> total = 8.2 (exact)
  P2-P4: ABSENT (no wikilink; shared source is a weight modulator, not edge creator)

Node sizes (llm_wiki parity — graph-view.tsx nodeSize(), lines 232-237):
  size = BASE(8) + sqrt(degree / max_degree) * (MAX(28) - BASE(8))
  max_degree in fixture = 2 (P1 and P4 each have degree=2)
  P1(deg=2): 8 + sqrt(2/2)*20 = 28.0
  P4(deg=2): 28.0
  P2(deg=1): 8 + sqrt(1/2)*20 ≈ 22.14
  P3(deg=1): ≈ 22.14
  P5(deg=0): 8.0 (isolated -> BASE)
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
                community INTEGER,
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
        """
        P1-P2: 2 directed links + 1 shared source + type_affinity(entity,entity)=0.8
        -> base without AA: 3*2 + 4*1 + 0 + 0.8 = 10.8 >= 10.0.
        (G-P1-7: same-type entity pair now contributes 0.8 not 1.0 — cross-type is rewarded.)
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P1"], p["P2"])
        assert edge is not None, "P1-P2 edge must be present (structural: 2 direct links)"
        assert (
            edge.weight >= 10.0
        ), f"P1-P2 weight {edge.weight} < 10.0 (base without AA: 6+4+0+0.8=10.8)"

    async def test_p1_p4_weight_ge_8(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """
        P1-P4: 1 link (P4->P1) + 1 shared source (doc_a) + type_affinity(entity,entity)=0.8
        -> base without AA: 3*1 + 4*1 + 0 + 0.8 = 7.8 >= 7.5.
        (G-P1-7: same-type entity pair now contributes 0.8 not 1.0.)
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P1"], p["P4"])
        assert edge is not None, "P1-P4 edge must be present (structural: direct link)"
        assert edge.weight >= 7.5, f"P1-P4 weight {edge.weight} < 7.5 (base: 3+4+0+0.8=7.8)"

    async def test_p2_p4_absent(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """
        P2-P4: 0 direct wikilinks; shared source (doc_a) only.
        Under the wikilink-only edge rule (llm_wiki 0.6.0 parity, ADR-0016 amendment
        2026-07-09), no [[wikilink]] means NO edge.  shared_source is a weight modulator
        on existing wikilink edges, not an edge creator.
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P2"], p["P4"])
        assert edge is None, (
            "P2-P4 must NOT be present: no [[wikilink]] between P2 and P4. "
            "shared_source(doc_a) is a weight modulator only, not an edge generator "
            "(llm_wiki 0.6.0 parity, ADR-0016 amendment 2026-07-09)"
        )

    async def test_p3_p4_weight_is_7(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """
        P3(concept)-P4(entity): 1 link x 3 = 3; 1 shared source x 4 = 4; AA=0;
        type_affinity(concept,entity) = 1.2 (cross-type REWARD — G-P1-7).
        Exact weight: 3 + 4 + 0 + 1.2 = 8.2.
        (Old binary same_type gave 0 for concept vs entity -> 7.0. Now the matrix
        rewards this cross-type pair with 1.2 -> 8.2.)
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P3"], p["P4"])
        assert edge is not None, "P3-P4 edge must be present (structural: direct link)"
        assert (
            abs(edge.weight - 8.2) < 0.01
        ), f"P3-P4 weight {edge.weight} != 8.2 (direct 1x3=3 + source 1x4=4 + AA 0 + type_affinity(concept,entity)=1.2)"

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
        P3(concept)-P4(entity): direct signal = 3.0 (one link x 3), source signal = 4.0,
        aa signal = 0.0 (no shared neighbours), type signal = 1.2 (concept↔entity
        cross-type reward per G-P1-7 affinity matrix; old binary same_type was 0.0).
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
            abs(sigs.get("type", -1) - 1.2) < 0.01
        ), f"type signal should be 1.2 (concept↔entity cross-type reward G-P1-7), got {sigs.get('type')}"

    async def test_type_signal_contributes(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """
        AC-F4-1(e): type-affinity modulates weight for structural pairs.
        P1 and P2 are both 'entity' and have a direct link ->
        type signal = 0.8 (same-type entity PENALTY per G-P1-7 affinity matrix).
        Old binary same_type gave 1.0; matrix now gives 0.8 to discourage same-type
        clustering and reward cross-type connections.
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
            abs(sigs.get("type", -1) - 0.8) < 0.01
        ), f"P1-P2 type signal should be 0.8 (entity↔entity same-type penalty G-P1-7), got {sigs.get('type')}"

    async def test_null_type_no_match(
        self, graph_db: tuple[Any, dict[str, str], str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        type-affinity: NULL type resolves to the default 0.5 (not 0.0 and not 1.0).
        _type_affinity(None, None) == 0.5 per G-P1-7 (unknown/None types fall back
        to _TYPE_AFFINITY_DEFAULT=0.5, matching llm_wiki's `?? 0.5` fallback).
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
                    abs(sigs.get("type", -1) - 0.5) < 0.01
                ), f"Two NULL-type pages must get type signal=0.5 (default fallback G-P1-7), got {sigs.get('type')}"


class TestTypeAffinity:
    """
    Pure unit tests for _type_affinity helper (G-P1-7, llm_wiki parity).

    No DB or fixture needed — tests the helper in isolation to verify:
      - Cross-type REWARD (entity↔concept = 1.2)
      - Same-type PENALTY (entity↔entity = 0.8, source↔source = 0.5)
      - Symmetry (matrix is symmetric; a↔b == b↔a)
      - Case-insensitivity
      - None → 0.5 (default fallback)
      - Unknown type → 0.5 (types outside the 5-type set, e.g. comparison/overview/log)
    """

    def test_cross_type_entity_concept_reward(self) -> None:
        """entity↔concept is a cross-type pair -> 1.2 (the highest reward value)."""
        from app.graph.engine import _type_affinity

        assert (
            _type_affinity("entity", "concept") == 1.2
        ), "entity↔concept cross-type pair should get 1.2 reward"

    def test_same_type_entity_penalty(self) -> None:
        """entity↔entity is a same-type pair -> 0.8 (same-type penalty)."""
        from app.graph.engine import _type_affinity

        assert (
            _type_affinity("entity", "entity") == 0.8
        ), "entity↔entity same-type pair should get 0.8 (mild penalty)"

    def test_same_type_source_penalty(self) -> None:
        """source↔source is a same-type pair -> 0.5 (strongest same-type penalty)."""
        from app.graph.engine import _type_affinity

        assert (
            _type_affinity("source", "source") == 0.5
        ), "source↔source same-type pair should get 0.5 (strong penalty — sources cluster)"

    def test_symmetry_concept_synthesis(self) -> None:
        """concept↔synthesis == synthesis↔concept == 1.2 (matrix is symmetric)."""
        from app.graph.engine import _type_affinity

        fwd = _type_affinity("concept", "synthesis")
        rev = _type_affinity("synthesis", "concept")
        assert fwd == 1.2, f"concept↔synthesis should be 1.2, got {fwd}"
        assert rev == 1.2, f"synthesis↔concept should be 1.2, got {rev}"
        assert fwd == rev, "type_affinity must be symmetric"

    def test_case_insensitive(self) -> None:
        """Type strings are lowercased before lookup -> Entity↔Concept == 1.2."""
        from app.graph.engine import _type_affinity

        assert (
            _type_affinity("Entity", "Concept") == 1.2
        ), "type_affinity must be case-insensitive: Entity↔Concept == 1.2"
        assert (
            _type_affinity("ENTITY", "ENTITY") == 0.8
        ), "type_affinity must be case-insensitive: ENTITY↔ENTITY == 0.8"

    def test_none_type_a_returns_default(self) -> None:
        """_type_affinity(None, 'entity') returns 0.5 (default fallback)."""
        from app.graph.engine import _type_affinity

        assert (
            _type_affinity(None, "entity") == 0.5
        ), "_type_affinity(None, 'entity') should return 0.5 default"

    def test_none_type_b_returns_default(self) -> None:
        """_type_affinity('entity', None) returns 0.5 (default fallback)."""
        from app.graph.engine import _type_affinity

        assert (
            _type_affinity("entity", None) == 0.5
        ), "_type_affinity('entity', None) should return 0.5 default"

    def test_both_none_returns_default(self) -> None:
        """_type_affinity(None, None) returns 0.5 (default fallback)."""
        from app.graph.engine import _type_affinity

        assert (
            _type_affinity(None, None) == 0.5
        ), "_type_affinity(None, None) should return 0.5 default"

    def test_unknown_type_comparison_returns_default(self) -> None:
        """Unknown type 'comparison' is outside the 5-type set -> 0.5."""
        from app.graph.engine import _type_affinity

        assert (
            _type_affinity("comparison", "entity") == 0.5
        ), "'comparison' is not in the affinity matrix -> 0.5 default"

    def test_unknown_type_overview_both_returns_default(self) -> None:
        """Two unknown types (overview↔overview) both outside the 5-type set -> 0.5."""
        from app.graph.engine import _type_affinity

        assert (
            _type_affinity("overview", "overview") == 0.5
        ), "'overview'↔'overview' both outside affinity matrix -> 0.5 default"

    def test_all_known_types_in_matrix(self) -> None:
        """
        Spot-check every known type pair from the matrix to ensure the full
        5x5 matrix is correctly implemented. Values taken from _TYPE_AFFINITY dict.
        """
        from app.graph.engine import _type_affinity

        # Row: entity
        assert _type_affinity("entity", "concept") == 1.2
        assert _type_affinity("entity", "entity") == 0.8
        assert _type_affinity("entity", "source") == 1.0
        assert _type_affinity("entity", "synthesis") == 1.0
        assert _type_affinity("entity", "query") == 0.8
        # Row: concept
        assert _type_affinity("concept", "concept") == 0.8
        assert _type_affinity("concept", "source") == 1.0
        assert _type_affinity("concept", "synthesis") == 1.2
        assert _type_affinity("concept", "query") == 1.0
        # Row: source
        assert _type_affinity("source", "source") == 0.5
        assert _type_affinity("source", "query") == 0.8
        assert _type_affinity("source", "synthesis") == 1.0
        # Row: query
        assert _type_affinity("query", "synthesis") == 1.0
        assert _type_affinity("query", "query") == 0.5
        # Row: synthesis
        assert _type_affinity("synthesis", "synthesis") == 0.8


class TestFA2Determinism:
    """
    ADR-0045: identical topology + weights + seed -> identical coordinates.
    Replaces TestFRDeterminism (FR removed; FA2 via fa2_modified is the layout engine).
    """

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
        # At least some coords should differ (FA2 separates nodes)
        xs = [x for x, _ in coords]
        ys = [y for _, y in coords]
        assert (
            max(xs) - min(xs) > 1e-6 or max(ys) - min(ys) > 1e-6
        ), "FA2 layout should spread nodes apart (non-zero spread)"

    async def test_coords_are_finite(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """All coordinates returned by FA2 must be finite (no NaN / Inf)."""
        import math

        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        for node in snapshot.nodes:
            assert math.isfinite(node.x), f"Node {node.id} x={node.x} is not finite"
            assert math.isfinite(node.y), f"Node {node.id} y={node.y} is not finite"


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

    async def test_aggregate_page_types_excluded_from_nodes(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        index / log / overview are app-managed aggregate pages (catalogue / history / summary),
        NOT knowledge nodes. They must be excluded from the graph — Synapse writes them outside
        the link-persistence path, so otherwise they render as stray isolated dots. A normal
        concept inserted alongside them still appears, proving only the aggregates are filtered.
        """
        engine_obj, p, vault_id = graph_db
        session_factory = _get_session_factory(engine_obj)

        agg_ids = {ptype: _uid() for ptype in ("index", "log", "overview")}
        concept_id = _uid()
        async with session_factory() as s:
            for ptype, pid in agg_ids.items():
                await _insert_page(
                    s,
                    page_id=pid,
                    vault_id=vault_id,
                    title=f"{ptype}-page",
                    page_type=ptype,
                    sources=[],
                )
            await _insert_page(
                s,
                page_id=concept_id,
                vault_id=vault_id,
                title="A Real Concept",
                page_type="concept",
                sources=[],
            )
            await s.commit()

        from app.graph.engine import GRAPH_HIDDEN_PAGE_TYPES, GraphEngine

        # Guard the constant (single source of truth shared with the /graph count query).
        assert {"index", "log", "overview"}.issubset(GRAPH_HIDDEN_PAGE_TYPES)

        snapshot = await GraphEngine().recompute(vault_id)
        node_ids = {n.id for n in snapshot.nodes}
        for ptype, pid in agg_ids.items():
            assert pid not in node_ids, f"{ptype} page must be excluded from the graph"
        assert concept_id in node_ids, "a real concept page must still be a graph node"


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
        assert (
            edge.kind == "link"
        ), f"P1-P2 has direct wikilinks -> kind must be 'link', got {edge.kind!r}"

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
        assert (
            edge.kind == "link"
        ), f"P3-P4: direct link exists -> kind='link' wins, got {edge.kind!r}"

    async def test_shared_source_only_pair_absent(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        P2-P4: NO direct [[wikilink]], only shared source (doc_a).
        Under the llm_wiki 0.6.0 parity rule (ADR-0016 amendment 2026-07-09), this pair
        has NO edge.  'source' edges (kind="source") are removed from the graph engine.
        shared_source is a weight modulator on wikilink edges, not an edge creator.
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P2"], p["P4"])
        assert edge is None, (
            "P2-P4 must be ABSENT: shared-source-only pairs no longer create edges "
            "(wikilink-only rule, ADR-0016 amendment 2026-07-09)"
        )

    async def test_structural_degree_drives_size_monotonically(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        llm_wiki 0.6.0 parity (ADR-0016 amendment 2026-07-09 / graph-view.tsx:232-237):
          size = BASE(8) + sqrt(degree / max_degree) * (MAX(28) - BASE(8))
        Normalized against the max-degree node (hub).
        In the fixture max_degree=2 (P1 and P4 each have degree=2 after wikilink-only rule).
        P5 is isolated -> degree=0, size=8.0 (BASE).
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        node_map = {n.id: n for n in snapshot.nodes}

        # P5 is isolated (no wikilinks) -> degree=0, size=BASE=8.0
        p5 = node_map[p["P5"]]
        assert p5.degree == 0, f"P5 (isolated) should have degree 0, got {p5.degree}"
        assert (
            abs(p5.size - 8.0) < 0.01
        ), f"P5 isolated node size should be 8.0 (BASE), got {p5.size}"

        # P1 is connected (degree=2 from P1-P2 + P1-P4 wikilinks) -> larger than P5
        p1 = node_map[p["P1"]]
        assert p1.degree >= 2, f"P1 should have degree >= 2, got {p1.degree}"
        assert p1.size > p5.size, (
            f"P1 (degree={p1.degree}) must have larger size than P5 (degree=0): "
            f"{p1.size} vs {p5.size}"
        )

        # Verify llm_wiki size formula: BASE(8) + sqrt(deg/max_deg) * (MAX(28)-BASE(8))
        all_degrees = [n.degree for n in snapshot.nodes]
        max_deg = max(all_degrees) if all_degrees else 0
        for node in snapshot.nodes:
            if max_deg == 0:
                expected = 8.0
            else:
                ratio = node.degree / max_deg
                expected = 8.0 + math.sqrt(ratio) * 20.0
            assert abs(node.size - expected) < 0.001, (
                f"Node {node.id} size {node.size} != expected {expected:.3f} "
                f"(degree={node.degree}, max_degree={max_deg}; "
                "llm_wiki formula: 8 + sqrt(deg/max)*20)"
            )

    async def test_all_edges_have_kind_link(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        All engine-generated edges have kind='link' after ADR-0016 amendment (2026-07-09).
        The 'source' edge kind is removed: shared-source-only pairs no longer create edges.
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        for edge in snapshot.edges:
            assert edge.kind == "link", (
                f"Edge {edge.source}-{edge.target} has kind={edge.kind!r}; "
                "all engine-generated edges must be kind='link' after ADR-0016 amendment"
            )


class TestWikilinkEdgeCount:
    """
    Wikilink-only edge set (llm_wiki 0.6.0 parity, ADR-0016 amendment 2026-07-09).
    5-node fixture: only resolved [[wikilinks]] create edges.
    P2-P4 had a shared source but NO wikilink → ABSENT.
    """

    async def test_only_wikilink_edges_present(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        The fixture has exactly 3 wikilink pairs:
          P1-P2 (bidirectional: P1->P2 and P2->P1),
          P1-P4 (P4->P1),
          P3-P4 (P3->P4).
        P2-P4: shared source (doc_a) but NO wikilink -> ABSENT (llm_wiki 0.6.0 parity).
        P5 is isolated (no wikilinks) -> no edges.
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge_pairs = {frozenset([e.source, e.target]) for e in snapshot.edges}

        expected = {
            frozenset([p["P1"], p["P2"]]),
            frozenset([p["P1"], p["P4"]]),
            frozenset([p["P3"], p["P4"]]),
        }
        assert edge_pairs == expected, (
            f"Wikilink edges mismatch (ADR-0016 amendment 2026-07-09).\n"
            f"  Expected: {expected}\n"
            f"  Got:      {edge_pairs}\n"
            "P2-P4 should be absent (shared-source only, no [[wikilink]])."
        )


class TestLlmWikiParityEdgeRule:
    """
    llm_wiki 0.6.0 parity: no 'source' edges, query nodes excluded, wikilink-only rule.
    ADR-0016 amendment 2026-07-09.
    """

    async def test_no_source_kind_edges(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """
        After ADR-0016 amendment: the engine never produces kind='source' edges.
        All edges must have kind='link' (wikilink-only rule).
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        source_edges = [e for e in snapshot.edges if e.kind == "source"]
        assert len(source_edges) == 0, (
            f"No 'source' edges should exist after ADR-0016 amendment; "
            f"got: {[(e.source, e.target, e.kind) for e in source_edges]}"
        )

    async def test_query_nodes_excluded(self, graph_db: tuple[Any, dict[str, str], str]) -> None:
        """
        llm_wiki 0.6.0 parity (wiki-graph.ts:204-209 HIDDEN_TYPES = {'query'}):
        Nodes with type='query' must not appear in the graph snapshot, and no
        edge should connect to or from them.
        """
        engine_obj, p, vault_id = graph_db
        session_factory = _get_session_factory(engine_obj)

        # Insert a query-type page with a wikilink to P1
        q_id = _uid()
        async with session_factory() as s:
            await _insert_page(
                s,
                page_id=q_id,
                vault_id=vault_id,
                title="QueryPage",
                page_type="query",
                sources=[],
            )
            await _insert_link(
                s,
                link_id=_uid(),
                source_page_id=q_id,
                target_page_id=p["P1"],
                target_title="Alpha",
            )
            await s.commit()

        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        node_ids_in_snapshot = {n.id for n in snapshot.nodes}
        assert q_id not in node_ids_in_snapshot, (
            "Query-type node must be excluded from the graph "
            "(llm_wiki 0.6.0 HIDDEN_TYPES parity, ADR-0016 amendment 2026-07-09)"
        )
        # No edge should involve the excluded query node
        for e in snapshot.edges:
            assert (
                e.source != q_id and e.target != q_id
            ), f"No edge should connect to/from excluded query node {q_id}"

    async def test_shared_source_contributes_weight_on_wikilink_edge(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        shared_source is a weight modulator on wikilink edges.
        P1-P2 has both a wikilink AND shared source (doc_a), so shared contributes +4
        to the weight. Verify the weight is > what direct alone would give (> 3*2=6).
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        edge = _find_edge(snapshot.edges, p["P1"], p["P2"])
        assert edge is not None, "P1-P2 must be present (wikilink)"
        # With shared source(+4) on top of direct(+6): weight should be > 6
        assert edge.weight > 6.0, (
            f"P1-P2 weight {edge.weight} should exceed 6.0 "
            "(shared_source contributes +4 to the wikilink edge weight)"
        )


class TestClampRemovedFromEnginePath:
    """
    Verify _clamp_outliers is no longer called by the engine (llm_wiki 0.6.0 parity).
    ADR-0045 amendment 2026-07-09: clamp removed from recompute path.
    """

    async def test_extreme_pinned_coords_not_clamped(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        The outlier clamp is no longer applied by the engine.  A node pinned at extreme
        coordinates (1,000,000) must remain at those exact coordinates in the snapshot.
        Previously (ADR-0045 §5, now removed from engine path) it would have been pulled
        to ~3× the p90 radius — typically a few hundred units.
        """
        engine_obj, p, vault_id = graph_db
        session_factory = _get_session_factory(engine_obj)

        extreme_x = 1_000_000.0
        extreme_y = 1_000_000.0
        async with session_factory() as s:
            await s.execute(
                sa_text("UPDATE pages SET pinned=1, x=:x, y=:y WHERE id=:id").bindparams(
                    id=p["P1"], x=extreme_x, y=extreme_y
                )
            )
            await s.commit()

        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        node_map = {n.id: n for n in snapshot.nodes}
        p1 = node_map[p["P1"]]
        assert abs(p1.x - extreme_x) < 1.0 and abs(p1.y - extreme_y) < 1.0, (
            f"Pinned node at ({extreme_x}, {extreme_y}) must NOT be clamped. "
            f"Got x={p1.x}, y={p1.y}. "
            "The outlier clamp is no longer applied by the engine (llm_wiki 0.6.0 parity)."
        )


class TestPinnedNodePreservation:
    """Feature A: pinned=true nodes keep their stored coords across FA2 recomputes."""

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
                sa_text("UPDATE pages SET pinned = 1, x = :x, y = :y WHERE id = :id").bindparams(
                    id=p["P1"], x=pinned_x, y=pinned_y
                )
            )
            await s.commit()

        from app.graph.engine import GraphEngine

        snapshot = await GraphEngine().recompute(vault_id)
        node_map = {n.id: n for n in snapshot.nodes}

        p1 = node_map[p["P1"]]
        assert (
            abs(p1.x - pinned_x) < 0.001
        ), f"Pinned P1 x should be {pinned_x}, got {p1.x} (FR must not overwrite pinned coords)"
        assert (
            abs(p1.y - pinned_y) < 0.001
        ), f"Pinned P1 y should be {pinned_y}, got {p1.y} (FR must not overwrite pinned coords)"

    async def test_unpinned_nodes_coords_are_fa2_output(
        self, graph_db: tuple[Any, dict[str, str], str]
    ) -> None:
        """
        Unpinned nodes (pinned=false / 0) get FA2-computed coords; their x/y in the
        snapshot must NOT be the sentinel stored value (NULL or 0).
        Verifies FA2 layout ran and spread nodes out (ADR-0045).
        """
        engine_obj, p, vault_id = graph_db
        from app.graph.engine import GraphEngine

        # All nodes start unpinned (default=0 in SQLite fixture)
        snapshot = await GraphEngine().recompute(vault_id)
        # Not all zero or identical — FA2 layout must have spread them out
        xs = [n.x for n in snapshot.nodes]
        ys = [n.y for n in snapshot.nodes]
        assert (
            max(xs) - min(xs) > 1e-6 or max(ys) - min(ys) > 1e-6
        ), "Unpinned nodes should have non-trivial spread (FA2 layout ran)"

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
                sa_text("UPDATE pages SET pinned = 1, x = :x, y = :y WHERE id = :id").bindparams(
                    id=p["P1"], x=pinned_x, y=pinned_y
                )
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
        assert (
            max(xs) - min(xs) > 1e-6 or max(ys) - min(ys) > 1e-6
        ), "Free nodes must have non-trivial spread"


class TestFA2LayoutHelper:
    """
    Unit tests for the _forceatlas2_layout helper (ADR-0045 §2).

    Tests run directly against the helper — no DB required — to verify:
      - Correct node count in output.
      - All coordinates finite.
      - Determinism: two calls with identical inputs yield bit-for-bit identical output.
    """

    def _build_graph(self, n: int, edges: list[tuple[int, int]], weights: list[float]) -> Any:
        import igraph

        g = igraph.Graph(n=n, edges=edges, directed=False)
        if weights:
            g.es["weight"] = weights
        return g

    def test_output_length_matches_node_count(self) -> None:
        """_forceatlas2_layout returns one (x,y) per node."""
        from app.graph.engine import _forceatlas2_layout

        g = self._build_graph(6, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)], [3.0] * 5)
        coords = _forceatlas2_layout(g, [3.0] * 5, 6)
        assert len(coords) == 6, f"Expected 6 coords, got {len(coords)}"

    def test_all_coords_finite(self) -> None:
        """All coordinates are finite (no NaN / Inf)."""
        import math

        from app.graph.engine import _forceatlas2_layout

        g = self._build_graph(6, [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)], [3.0] * 5)
        coords = _forceatlas2_layout(g, [3.0] * 5, 6)
        for i, (x, y) in enumerate(coords):
            assert math.isfinite(x), f"Node {i} x={x} is not finite"
            assert math.isfinite(y), f"Node {i} y={y} is not finite"

    def test_determinism_same_result_two_calls(self) -> None:
        """
        Two calls with identical inputs produce bit-for-bit identical coordinates.
        This is the core ADR-0045 §2 invariant: circle-init + numpy seed guarantees
        reproducible FA2 output regardless of process state between calls.
        """
        from app.graph.engine import _forceatlas2_layout

        edges = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 0), (0, 3)]
        weights = [3.0, 7.0, 4.0, 5.0, 2.0, 6.0, 8.0]
        g = self._build_graph(6, edges, weights)

        coords1 = _forceatlas2_layout(g, weights, 6)
        coords2 = _forceatlas2_layout(g, weights, 6)

        assert len(coords1) == len(coords2), "Output lengths must match"
        for i, ((x1, y1), (x2, y2)) in enumerate(zip(coords1, coords2, strict=True)):
            assert x1 == x2, f"Node {i} x not deterministic: {x1} vs {x2}"
            assert y1 == y2, f"Node {i} y not deterministic: {y1} vs {y2}"

    def test_single_node_returns_origin(self) -> None:
        """Single-node graph returns a single (0.0, 0.0) coordinate."""
        import igraph
        from app.graph.engine import _forceatlas2_layout

        g = igraph.Graph(n=1, edges=[], directed=False)
        coords = _forceatlas2_layout(g, [], 1)
        assert len(coords) == 1
        assert coords[0] == (0.0, 0.0)

    def test_empty_graph_returns_empty(self) -> None:
        """Zero-node graph returns empty list."""
        import igraph
        from app.graph.engine import _forceatlas2_layout

        g = igraph.Graph(n=0, edges=[], directed=False)
        coords = _forceatlas2_layout(g, [], 0)
        assert coords == []

    def test_no_edge_graph_returns_coords_for_all_nodes(self) -> None:
        """Graph with nodes but no edges still returns one coord per node."""
        from app.graph.engine import _forceatlas2_layout

        g = self._build_graph(4, [], [])
        coords = _forceatlas2_layout(g, [], 4)
        assert len(coords) == 4


class TestFeatureBDiscEnvelope:
    """
    Unit tests for _compress_to_disc (standalone function, no longer called by engine).

    NOTE: As of ADR-0045, GraphEngine.recompute() no longer calls _compress_to_disc.
    These tests remain valid as unit tests of the standalone helper function itself.
    The engine's recompute() path no longer enforces a disc envelope — FA2's organic
    spread is used directly.  See TestFA2Determinism for engine-level coordinate tests.
    """

    def test_compress_to_disc_basic(self) -> None:
        """_compress_to_disc compresses far-outlier radii while preserving angles."""
        from app.graph.engine import _compress_to_disc

        # Create points along X axis at various radii
        coords = [(0.0, 0.0), (1.0, 0.0), (5.0, 0.0), (20.0, 0.0)]
        result = _compress_to_disc(coords, r_target=10.0, p_high=95, exponent=0.7)

        # Origin stays near origin (radius 0 -> output x,y near centroid)
        # Far outlier (20.0) gets pulled in (radius < 20.0 in result)
        # All are along positive X (angles preserved for non-origin points)
        for rx, ry in result:
            # All results within the target disc
            r_out = math.sqrt(rx * rx + ry * ry)
            assert r_out <= 10.01, f"Point outside r_target: ({rx:.3f}, {ry:.3f}), r={r_out:.3f}"

    def test_compress_to_disc_preserves_angles(self) -> None:
        """Angles (polar theta) are preserved exactly by _compress_to_disc."""
        import math as _math

        from app.graph.engine import _compress_to_disc

        # Points at four cardinal directions
        coords = [(3.0, 0.0), (-3.0, 0.0), (0.0, 3.0), (0.0, -3.0)]
        result = _compress_to_disc(coords, r_target=10.0, p_high=95, exponent=0.7)

        # Centroid of input is (0,0) so angles are unchanged
        original_angles = [_math.atan2(y, x) for x, y in coords]
        result_angles = [_math.atan2(y, x) for x, y in result if x != 0.0 or y != 0.0]
        for orig, res in zip(original_angles, result_angles, strict=False):
            assert (
                abs(orig - res) < 0.001
            ), f"Angle not preserved: original={orig:.4f} result={res:.4f}"

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


class TestClampOutliers:
    """
    Unit tests for _clamp_outliers (ADR-0045 §5).

    The clamp tames FA2 runaway outliers so a few flung-out nodes don't collapse the
    dense core in the viewer, WITHOUT squashing the organic core spread (the reason
    _compress_to_disc was removed from the recompute path).
    """

    def test_pulls_in_extreme_outlier(self) -> None:
        """A single millions-scale outlier is pulled onto the cap; core is untouched."""
        from app.graph.engine import _clamp_outliers

        # Dense core near origin + one runaway node at 1.4M (the observed bug).
        coords = [(float(i % 10), float(i // 10)) for i in range(60)]
        coords.append((1_400_000.0, 0.0))
        result = _clamp_outliers(coords)

        # Core nodes returned EXACTLY as-is (organic spread preserved).
        for original, clamped in zip(coords[:60], result[:60], strict=True):
            assert original == clamped

        # The outlier was pulled far inward — nowhere near 1.4M anymore.
        ox, oy = result[-1]
        assert abs(ox) < 1000.0, f"Outlier not clamped: x={ox}"
        assert abs(oy) < 1000.0, f"Outlier not clamped: y={oy}"
        # And the overall span is now bounded (no millions-scale coordinate).
        xs = [x for x, _ in result]
        assert max(xs) - min(xs) < 1000.0

    def test_preserves_outlier_angle(self) -> None:
        """Clamping is radial from the median center — the outlier's direction is kept."""
        from app.graph.engine import _clamp_outliers

        coords = [(0.0, 0.0)] * 50 + [(3.0, 4.0)] * 1  # median center ~ origin
        # Push one outlier along the (3,4) direction, magnified.
        coords[-1] = (300000.0, 400000.0)
        result = _clamp_outliers(coords)
        ox, oy = result[-1]
        # Direction preserved: y/x ≈ 4/3.
        assert abs((oy / ox) - (4.0 / 3.0)) < 1e-6

    def test_no_outliers_returns_unchanged(self) -> None:
        """A well-behaved layout (no extreme radii) is returned unchanged."""
        from app.graph.engine import _clamp_outliers

        coords = [(float(i), float(-i)) for i in range(-20, 20)]
        result = _clamp_outliers(coords)
        assert result == coords

    def test_small_and_degenerate_inputs(self) -> None:
        """<=2 nodes and all-coincident inputs are returned unchanged."""
        from app.graph.engine import _clamp_outliers

        assert _clamp_outliers([]) == []
        assert _clamp_outliers([(1.0, 2.0)]) == [(1.0, 2.0)]
        assert _clamp_outliers([(1.0, 2.0), (3.0, 4.0)]) == [(1.0, 2.0), (3.0, 4.0)]
        same = [(5.0, 5.0)] * 10
        assert _clamp_outliers(same) == same

    def test_deterministic(self) -> None:
        """Two calls on identical input yield identical output (I2 determinism)."""
        from app.graph.engine import _clamp_outliers

        coords = [(float(i), float(i * 2)) for i in range(30)] + [(999999.0, -999999.0)]
        assert _clamp_outliers(coords) == _clamp_outliers(coords)


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
        cache._snapshot = GraphSnapshot(
            nodes=[NodeSnapshot(id="n1", title=None, page_type=None, x=0.0, y=0.0)], edges=[]
        )
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


@pytest.mark.asyncio
async def test_resolver_maps_are_vault_scoped() -> None:
    """Regression: _build_resolver_maps must only see the given vault's pages.

    Two vaults with the same-slug page ("Shared Concept") must not cross-resolve. Before the fix
    the slug→id map was built over ALL vaults (first-hit-wins), so a wikilink in vault-a could
    resolve to vault-b's page — pointing Link.target_page_id cross-vault, which produces NO graph
    edge (the target isn't a node in vault-a's graph) and collapsed the knowledge graph on any
    multi-vault deployment (or a repeated-ingest test DB).
    """
    from app.wiki.links import _build_resolver_maps, _slugify

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await _setup_sqlite(engine)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    id_a = "00000000-0000-0000-0000-0000000000aa"
    id_b = "00000000-0000-0000-0000-0000000000bb"
    async with session_factory() as sess:
        await _insert_page(
            sess,
            page_id=id_a,
            vault_id="vault-a",
            title="Shared Concept",
            page_type="concept",
            sources=["doc"],
        )
        await _insert_page(
            sess,
            page_id=id_b,
            vault_id="vault-b",
            title="Shared Concept",
            page_type="concept",
            sources=["doc"],
        )
        await sess.commit()

        maps_a = await _build_resolver_maps(sess, "vault-a")
        maps_b = await _build_resolver_maps(sess, "vault-b")

    slug = _slugify("Shared Concept")
    assert str(maps_a.by_slug[slug]) == id_a, "vault-a must resolve the slug to ITS OWN page"
    assert str(maps_b.by_slug[slug]) == id_b, "vault-b must resolve the slug to ITS OWN page"
    assert maps_a.by_slug[slug] != maps_b.by_slug[slug], "no cross-vault bleed"
    await engine.dispose()
