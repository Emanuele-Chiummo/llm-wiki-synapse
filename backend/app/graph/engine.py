"""
GraphEngine — 4-signal edge-weight computation + seeded FA2 layout (F4, I2).

Public API:
  GraphEngine.recompute(vault_id, session?) → GraphSnapshot

Invariant compliance:
  I2 — FA2 runs ONLY here, server-side, via python-igraph (R9, I9).
       Coordinates are persisted in pages.x/y (ADR-0013, AQ-6).
       Never called from any frontend path.
  I1 — Reads only pages + links tables; never walks vault/ filesystem.
  I7 — Single bounded pass; logs node/edge count + wall-clock duration.
  I9 — python-igraph for both Adamic-Adar and force-directed layout (R9).

Edge-weight formula (ADR-0012, LOCKED):
  w(A,B) = 3.0·direct_link_count(A,B)
          + 4.0·shared_source_count(A,B)
          + 1.5·adamic_adar(A,B)
          + 1.0·same_type(A,B)
  Persisted iff w > 0.

FA2 determinism (ADR-0013):
  Fixed seed = 42 (GRAPH_LAYOUT_SEED env override).
  Identical topology+weights → identical coordinates.
"""

from __future__ import annotations

import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

logger = logging.getLogger(__name__)

# ── Seed (ADR-0013 §2) ────────────────────────────────────────────────────────
_DEFAULT_SEED = 42
FA2_SEED: int = int(os.environ.get("GRAPH_LAYOUT_SEED", str(_DEFAULT_SEED)))


# ── Snapshot dataclass ─────────────────────────────────────────────────────────


@dataclass
class NodeSnapshot:
    """One graph node as returned by GET /graph (ADR-0014 §6)."""

    id: str
    title: str | None
    page_type: str | None
    x: float
    y: float
    degree: int = 0
    size: float = 1.0


@dataclass
class EdgeSnapshot:
    """One graph edge as returned by GET /graph (ADR-0014 §6)."""

    source: str
    target: str
    weight: float


@dataclass
class GraphSnapshot:
    """
    Complete graph payload produced by one recompute (ADR-0014 §6).

    Returned by GraphCache.get_graph(); serialised into the GET /graph response.
    """

    nodes: list[NodeSnapshot] = field(default_factory=list)
    edges: list[EdgeSnapshot] = field(default_factory=list)
    data_version: int = 0


# ── GraphEngine ────────────────────────────────────────────────────────────────


class GraphEngine:
    """
    Computes 4-signal weighted edges and seeded FA2 layout from Postgres tables.

    Usage::

        engine = GraphEngine()
        snapshot = await engine.recompute(vault_id="default")

    The engine is stateless; it can be called multiple times (GraphCache controls
    frequency via the debounced queue — ADR-0014).
    """

    async def recompute(
        self,
        vault_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> GraphSnapshot:
        """
        Single bounded pass (I7):
          1. Load nodes (pages) and resolved links from Postgres (I1 — no vault walk).
          2. Build undirected unweighted igraph for Adamic-Adar.
          3. Compute 4-signal weight per candidate pair (ADR-0012).
          4. Build weighted igraph; run seeded FA2 → coords (I2, ADR-0013).
          5. Persist edges + coords in ONE transaction (ADR-0013 §algorithm step 6).
          6. Return GraphSnapshot (GraphCache stamps data_version).
          7. Log node/edge count + wall-clock duration (I7 observability).
        """
        t0 = time.perf_counter()
        logger.info("GraphEngine.recompute: start vault_id=%r seed=%d", vault_id, FA2_SEED)

        # ── 1. Load pages and links from DB (I1 — tables only, no vault walk) ──
        nodes_data, links_data = await self._load_data(vault_id, session)

        if not nodes_data:
            logger.info("GraphEngine.recompute: no live pages — empty snapshot")
            await self._persist_results(vault_id, [], [], session)
            return GraphSnapshot()

        # Build node index: str(uuid) → dict of page attributes
        node_index: dict[str, dict[str, Any]] = {
            str(row["id"]): {
                "id": str(row["id"]),
                "title": row.get("title"),
                "page_type": row.get("page_type"),
                "sources": set(row.get("sources") or []),
            }
            for row in nodes_data
        }
        node_ids: list[str] = list(node_index.keys())
        id_to_idx: dict[str, int] = {nid: i for i, nid in enumerate(node_ids)}

        # ── 2. Build undirected unweighted igraph for Adamic-Adar ──────────────
        # Resolved directed links → undirected edges (deduplicated)
        directed_links: list[tuple[str, str]] = []
        for row in links_data:
            src = str(row["source_page_id"])
            tgt = str(row["target_page_id"])
            if src in id_to_idx and tgt in id_to_idx:
                directed_links.append((src, tgt))

        # Undirected adjacency as set of frozensets for direct-link counting
        # direct_link_count: count BOTH directions separately (each directed link = 1)
        pair_direct: dict[tuple[str, str], int] = {}
        for src, tgt in directed_links:
            key = _canonical_key(src, tgt)
            pair_direct[key] = pair_direct.get(key, 0) + 1

        # Build undirected unweighted igraph (deduplicated edges)
        import igraph

        n = len(node_ids)
        unweighted_edges_idx: list[tuple[int, int]] = []
        seen_undirected: set[tuple[int, int]] = set()
        for src, tgt in directed_links:
            a, b = id_to_idx[src], id_to_idx[tgt]
            pair = (min(a, b), max(a, b))
            if pair not in seen_undirected:
                seen_undirected.add(pair)
                unweighted_edges_idx.append(pair)

        g_unweighted = igraph.Graph(n=n, edges=unweighted_edges_idx, directed=False)

        # ── 3. Compute 4-signal weight per candidate pair (ADR-0012) ──────────
        # Candidate pairs: any pair sharing (a) a direct link, (b) a source, (c) a
        # resolved-link neighbour (AA > 0), or (d) the same non-NULL type.
        # We enumerate all candidate pairs efficiently, then compute weights.

        # Neighbour sets for Adamic-Adar (per vertex)
        neighbours: list[set[int]] = [set(g_unweighted.neighbors(i)) for i in range(n)]

        # Degree array for AA denominator: ln(degree); skip degree-0 nodes
        degrees: list[int] = g_unweighted.degree()

        # Pre-compute candidate pairs
        candidate_pairs: set[tuple[int, int]] = set()

        # (a) direct-link pairs
        for a, b in seen_undirected:
            candidate_pairs.add((a, b))

        # (b) shared-source pairs
        # Group pages by each source to find overlap efficiently
        source_to_pages: dict[str, list[int]] = {}
        for i, nid in enumerate(node_ids):
            for src in node_index[nid]["sources"]:
                source_to_pages.setdefault(src, []).append(i)
        for _src, page_list in source_to_pages.items():
            for i_pos in range(len(page_list)):
                for j_pos in range(i_pos + 1, len(page_list)):
                    a, b = page_list[i_pos], page_list[j_pos]
                    candidate_pairs.add((min(a, b), max(a, b)))

        # (c) AA-contributing pairs: two pages sharing a resolved-link neighbour
        for c in range(n):
            nb = list(neighbours[c])
            for i_pos in range(len(nb)):
                for j_pos in range(i_pos + 1, len(nb)):
                    a, b = nb[i_pos], nb[j_pos]
                    candidate_pairs.add((min(a, b), max(a, b)))

        # (d) same-type pairs — enumerate per type group
        type_to_pages: dict[str, list[int]] = {}
        for i, nid in enumerate(node_ids):
            ptype = node_index[nid]["page_type"]
            if ptype is not None:
                type_to_pages.setdefault(ptype, []).append(i)
        for _ptype, page_list in type_to_pages.items():
            for i_pos in range(len(page_list)):
                for j_pos in range(i_pos + 1, len(page_list)):
                    a, b = page_list[i_pos], page_list[j_pos]
                    candidate_pairs.add((min(a, b), max(a, b)))

        # Compute Adamic-Adar for all candidate pairs manually
        # AA(A,B) = Σ_{c ∈ N(A)∩N(B)} 1/ln(deg(c))
        # igraph.similarity_inverse_log_weighted computes this for all pairs but is O(N²).
        # We compute only for our candidate set, which is much smaller.
        def _aa(a: int, b: int) -> float:
            """Adamic-Adar index for pair (a, b) on the unweighted undirected graph."""
            common = neighbours[a] & neighbours[b]
            total = 0.0
            for c in common:
                deg_c = degrees[c]
                if deg_c > 1:
                    total += 1.0 / math.log(deg_c)
            return total

        # Build weighted edge list
        weighted_edges: list[tuple[int, int, float, dict[str, float]]] = []
        # (a_idx, b_idx, weight, signals)

        for a, b in candidate_pairs:
            nid_a = node_ids[a]
            nid_b = node_ids[b]
            key = _canonical_key(nid_a, nid_b)

            direct = float(pair_direct.get(key, 0))
            shared = float(len(node_index[nid_a]["sources"] & node_index[nid_b]["sources"]))
            aa = _aa(a, b)
            type_a = node_index[nid_a]["page_type"]
            type_b = node_index[nid_b]["page_type"]
            same_type = (
                1.0 if (type_a is not None and type_b is not None and type_a == type_b) else 0.0
            )

            w = 3.0 * direct + 4.0 * shared + 1.5 * aa + 1.0 * same_type
            if w > 0:
                signals = {
                    "direct": 3.0 * direct,
                    "source": 4.0 * shared,
                    "aa": 1.5 * aa,
                    "type": same_type,
                }
                weighted_edges.append((a, b, w, signals))

        # ── 4. Build weighted igraph + seeded FA2 → coords (I2, ADR-0013) ─────
        weighted_edges_idx = [(a, b) for a, b, _w, _s in weighted_edges]
        edge_weights = [w for _a, _b, w, _s in weighted_edges]

        g_weighted = igraph.Graph(
            n=n,
            edges=weighted_edges_idx if weighted_edges_idx else [],
            directed=False,
        )
        if edge_weights:
            g_weighted.es["weight"] = edge_weights

        # Seed the igraph RNG for deterministic layout (ADR-0013 §2)
        igraph.set_random_number_generator(_SeedableRNG(FA2_SEED))

        # Use Fruchterman-Reingold layout (force-directed; satisfies I2 "FA2-family" intent).
        # igraph exposes layout_fruchterman_reingold which accepts weights and is
        # deterministic when the RNG is seeded (ADR-0013 §1 architect note).
        layout_kwargs: dict[str, Any] = {
            "weights": "weight" if edge_weights else None,
            "niter": 500,
        }
        layout = g_weighted.layout_fruchterman_reingold(**layout_kwargs)
        coords: list[tuple[float, float]] = [(pos[0], pos[1]) for pos in layout]

        # ── 5. Assemble result lists ───────────────────────────────────────────
        # Degree from weighted graph
        w_degrees = g_weighted.degree()

        node_snapshots: list[NodeSnapshot] = []
        for i, nid in enumerate(node_ids):
            nd = node_index[nid]
            deg = w_degrees[i]
            size = max(1.0, 1.0 + math.log1p(deg))
            node_snapshots.append(
                NodeSnapshot(
                    id=nid,
                    title=nd["title"],
                    page_type=nd["page_type"],
                    x=coords[i][0],
                    y=coords[i][1],
                    degree=deg,
                    size=size,
                )
            )

        edge_snapshots: list[EdgeSnapshot] = []
        edge_db_rows: list[dict[str, Any]] = []
        for a, b, w, sig in weighted_edges:
            nid_a = node_ids[a]
            nid_b = node_ids[b]
            src_id, tgt_id = _canonical_key_ids(nid_a, nid_b)
            edge_snapshots.append(EdgeSnapshot(source=nid_a, target=nid_b, weight=w))
            edge_db_rows.append(
                {
                    "vault_id": vault_id,
                    "source_page_id": src_id,
                    "target_page_id": tgt_id,
                    "weight": w,
                    "signals": sig,
                }
            )

        # ── 6. Persist coords + edges in ONE transaction ───────────────────────
        coord_rows: list[dict[str, Any]] = [
            {"id": ns.id, "x": ns.x, "y": ns.y} for ns in node_snapshots
        ]
        await self._persist_results(vault_id, coord_rows, edge_db_rows, session)

        elapsed = time.perf_counter() - t0
        logger.info(
            "GraphEngine.recompute: done vault_id=%r nodes=%d edges=%d elapsed=%.3fs",
            vault_id,
            len(node_snapshots),
            len(edge_snapshots),
            elapsed,
        )

        return GraphSnapshot(nodes=node_snapshots, edges=edge_snapshots)

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _load_data(
        self,
        vault_id: str,
        session: AsyncSession | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Load live pages and resolved links from Postgres (I1 — no vault walk)."""

        async def _run(sess: AsyncSession) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            # Pages: id, page_type (mapped column 'type'), title, sources
            pages_result = await sess.execute(
                sa_text(
                    "SELECT id, type AS page_type, title, sources "
                    "FROM pages "
                    "WHERE vault_id = :vid AND deleted_at IS NULL"
                ).bindparams(vid=vault_id)
            )
            nodes = [dict(row._mapping) for row in pages_result]

            # Resolved links only (dangling=false, target_page_id IS NOT NULL)
            links_result = await sess.execute(
                sa_text(
                    "SELECT source_page_id, target_page_id "
                    "FROM links "
                    "WHERE dangling = false AND target_page_id IS NOT NULL"
                )
            )
            links = [dict(row._mapping) for row in links_result]
            return nodes, links

        if session is not None:
            return await _run(session)
        async with get_session() as sess:
            return await _run(sess)

    async def _persist_results(
        self,
        vault_id: str,
        coord_rows: list[dict[str, Any]],
        edge_rows: list[dict[str, Any]],
        session: AsyncSession | None,
    ) -> None:
        """
        Persist coords (UPDATE pages SET x,y) and edges (delete-then-insert) in one txn.
        (ADR-0013 §algorithm step 6)
        """

        async def _run(sess: AsyncSession) -> None:
            # Update coords for each node
            for row in coord_rows:
                await sess.execute(
                    sa_text("UPDATE pages SET x = :x, y = :y WHERE id = :id").bindparams(
                        id=str(row["id"]), x=row["x"], y=row["y"]
                    )
                )

            # Replace edges for this vault (delete-then-insert)
            await sess.execute(
                sa_text("DELETE FROM edges WHERE vault_id = :vid").bindparams(vid=vault_id)
            )
            _INSERT_EDGE = (
                "INSERT INTO edges "
                "(id, vault_id, source_page_id, target_page_id,"
                " weight, signals, created_at) "
                "VALUES "
                "(:id, :vault_id, :source_page_id, :target_page_id,"
                " :weight, :signals::jsonb, now())"
            )
            for row in edge_rows:
                await sess.execute(
                    sa_text(_INSERT_EDGE).bindparams(
                        id=str(uuid.uuid4()),
                        vault_id=row["vault_id"],
                        source_page_id=str(row["source_page_id"]),
                        target_page_id=str(row["target_page_id"]),
                        weight=row["weight"],
                        signals=_json_dumps(row["signals"]),
                    )
                )

        if session is not None:
            await _run(session)
        else:
            async with get_session() as sess:
                await _run(sess)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _canonical_key(a: str, b: str) -> tuple[str, str]:
    """Return (smaller, larger) UUID string pair for canonical undirected edge storage."""
    return (a, b) if a < b else (b, a)


def _canonical_key_ids(a: str, b: str) -> tuple[str, str]:
    """Same as _canonical_key — alias for clarity in callers."""
    return _canonical_key(a, b)


def _json_dumps(obj: object) -> str:
    """Serialize a dict to JSON string for :jsonb bind param."""
    import json

    return json.dumps(obj)


# ── Seedable RNG adapter for igraph ──────────────────────────────────────────
# igraph.set_random_number_generator() accepts an object with .random() and
# .gauss() methods (compatible with the Python random.Random interface).
# We use Python's built-in random.Random with a fixed seed for determinism (ADR-0013 §2).


class _SeedableRNG:
    """
    Adapter wrapping Python's random.Random so igraph can use it as its RNG.

    igraph's set_random_number_generator() expects an object with the full
    Python random.Random interface: .random(), .gauss(), .randint(), .getrandbits().
    We delegate all calls to a seeded random.Random instance (ADR-0013 §2).
    """

    def __init__(self, seed: int) -> None:
        import random

        # S311: not used for crypto — this is a seeded deterministic RNG for FA2 (ADR-0013)
        self._rng = random.Random(seed)  # noqa: S311

    def random(self) -> float:
        return self._rng.random()

    def gauss(self, mu: float, sigma: float) -> float:
        return self._rng.gauss(mu, sigma)

    def randint(self, a: int, b: int) -> int:
        return self._rng.randint(a, b)

    def getrandbits(self, k: int) -> int:
        return self._rng.getrandbits(k)

    def shuffle(self, lst: list) -> None:  # type: ignore[type-arg]
        self._rng.shuffle(lst)

    def choice(self, seq: list) -> object:  # type: ignore[type-arg]
        return self._rng.choice(seq)

    def sample(self, population: list, k: int) -> list:  # type: ignore[type-arg]
        return self._rng.sample(population, k)

    def uniform(self, a: float, b: float) -> float:
        return self._rng.uniform(a, b)

    def seed(
        self,
        a: int | float | str | bytes | bytearray | None = None,
        version: int = 2,
    ) -> None:
        self._rng.seed(a, version)
