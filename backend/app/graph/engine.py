"""
GraphEngine — 4-signal edge-weight computation + seeded FR layout (F4, I2).

Public API:
  GraphEngine.recompute(vault_id, session?) → GraphSnapshot

Invariant compliance:
  I2 — FR layout runs ONLY here, server-side, via python-igraph (R9, I9).
       Coordinates are persisted in pages.x/y (ADR-0013, AQ-6).
       Never called from any frontend path.
  I1 — Reads only pages + links tables; never walks vault/ filesystem.
  I7 — Single bounded pass; logs node/edge count + wall-clock duration.
  I9 — python-igraph for both Adamic-Adar and force-directed layout (R9).

Edge INCLUSION rule (ADR-0016 — supersedes ADR-0012 §3):
  An edge (A,B) EXISTS iff:
    direct_link_count(A,B) > 0  OR  shared_source_count(A,B) > 0
  AA and same-type are MODULATORS only — they adjust the weight of already-structural
  edges but NEVER create an edge on their own.  This prevents type-cliques (hairball).

Edge-weight formula (ADR-0012 §1/§2, UNCHANGED — applied only to structural pairs):
  w(A,B) = 3.0·direct_link_count(A,B)
          + 4.0·shared_source_count(A,B)
          + 1.5·adamic_adar(A,B)
          + 1.0·same_type(A,B)

Per-edge kind (ADR-0016 §4):
  "link"   — direct_link_count > 0  (wikilink; may also have source/AA/type weight)
  "source" — direct_link_count == 0 AND shared_source_count > 0 (provenance-only)

Node size (ADR-0016 §2):
  size = BASE + GROWTH·sqrt(structural_degree)   BASE=1.0, GROWTH=2.5
  structural_degree = count of distinct incident structural edges.

FR determinism (ADR-0013):
  Fixed seed = 42 (GRAPH_LAYOUT_SEED env override).
  Identical topology+weights → identical coordinates.

Layout post-processing (Feature B — circular envelope):
  After FR: center → polar → compress radii against p95 with exponent 0.7 → cartesian.
  Applied BEFORE pinned-node restoration so pinned coords are untouched.
  Deterministic (uses only the FR output, no extra RNG).

Node pinning (Feature A):
  pages.pinned=true → engine reads stored (x,y) from DB and overwrites FR coords.
  Applied AFTER Feature B post-processing; PATCH /pages/{id}/position sets the flag.
  Pinned nodes stay put across every subsequent recompute.
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
    """One graph edge as returned by GET /graph (ADR-0014 §6, ADR-0016 §4)."""

    source: str
    target: str
    weight: float
    kind: str = "link"  # "link" | "source" (ADR-0016 §4)


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
        # pinned/stored_x/stored_y carried for Feature A (pinned-node preservation).
        node_index: dict[str, dict[str, Any]] = {
            str(row["id"]): {
                "id": str(row["id"]),
                "title": row.get("title"),
                "page_type": row.get("page_type"),
                "sources": set(row.get("sources") or []),
                "pinned": bool(row.get("pinned", False)),
                "stored_x": row.get("stored_x"),
                "stored_y": row.get("stored_y"),
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

        # ── 3. Compute 4-signal weight per structural candidate pair (ADR-0016) ──
        # STRUCTURAL candidate set = (a) direct-link pairs UNION (b) shared-source pairs.
        # ADR-0016 §1: AA and same-type are WEIGHT MODULATORS only — they never create
        # a standalone edge.  Blocks (c) AA-pair enumeration and (d) same-type enumeration
        # are REMOVED from candidate_pairs (they were the source of the type-clique hairball).
        # AA and type terms still contribute to the weight for pairs already in the
        # structural set (the additive formula of ADR-0012 §1/§2 is UNCHANGED).

        # Neighbour sets for Adamic-Adar weight modulation (per vertex)
        neighbours: list[set[int]] = [set(g_unweighted.neighbors(i)) for i in range(n)]

        # Degree array for AA denominator: ln(degree); skip degree-0 nodes
        degrees: list[int] = g_unweighted.degree()

        # STRUCTURAL candidate pairs: only (a) + (b) — ADR-0016 §1
        candidate_pairs: set[tuple[int, int]] = set()

        # (a) direct-link pairs — always structural
        for a, b in seen_undirected:
            candidate_pairs.add((a, b))

        # (b) shared-source pairs — structural (provenance fact, ADR-0016 §1)
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

        # (c) AA-pair enumeration REMOVED — AA is a weight modulator, not an edge generator.
        # (d) same-type enumeration REMOVED — type is a weight modulator, not an edge generator.
        # Both terms still contribute weight for pairs already in candidate_pairs above.

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

        # Build weighted edge list — structural pairs only (ADR-0016 §1)
        # (a_idx, b_idx, weight, signals, kind)
        weighted_edges: list[tuple[int, int, float, dict[str, float], str]] = []

        for a, b in candidate_pairs:
            nid_a = node_ids[a]
            nid_b = node_ids[b]
            key = _canonical_key(nid_a, nid_b)

            direct = float(pair_direct.get(key, 0))
            shared = float(len(node_index[nid_a]["sources"] & node_index[nid_b]["sources"]))

            # Structural gate (ADR-0016 §1): pair must have a real link or shared source.
            # By construction all pairs in candidate_pairs satisfy this, but make it explicit.
            if not (direct > 0 or shared > 0):
                continue  # safety guard — should not be reached after removing (c)/(d)

            aa = _aa(a, b)
            type_a = node_index[nid_a]["page_type"]
            type_b = node_index[nid_b]["page_type"]
            same_type = (
                1.0 if (type_a is not None and type_b is not None and type_a == type_b) else 0.0
            )

            # Weight formula (ADR-0012 §1/§2, UNCHANGED arithmetic)
            w = 3.0 * direct + 4.0 * shared + 1.5 * aa + 1.0 * same_type
            signals = {
                "direct": 3.0 * direct,
                "source": 4.0 * shared,
                "aa": 1.5 * aa,
                "type": same_type,
            }
            # Per-edge kind (ADR-0016 §4): "link" wins when both structural signals present
            kind = "link" if direct > 0 else "source"
            weighted_edges.append((a, b, w, signals, kind))

        # ── 4. Build weighted igraph + seeded FR → coords (I2, ADR-0013) ──────
        weighted_edges_idx = [(a, b) for a, b, _w, _s, _k in weighted_edges]
        edge_weights = [w for _a, _b, w, _s, _k in weighted_edges]

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
        raw_coords: list[tuple[float, float]] = [(pos[0], pos[1]) for pos in layout]

        # ── 4b. Feature B — polar-compression envelope (circular disc) ────────
        # Center coords at centroid; convert to polar; compress the radius
        # distribution so outliers come inward and the boundary is rounder.
        # Formula: r' = R_TARGET * min(1, (r / p95)) ** 0.7
        # R_TARGET=10.0 — canonical radius; client scales via sigma.js camera.
        # Exponent 0.7 is <1 → outliers pulled in (concave mapping); structure
        # and angular positions are preserved exactly.  Deterministic (no RNG).
        # Applied BEFORE pinned-node restoration (Feature A) so pinned nodes
        # keep their manually-set coords untouched.
        coords = _compress_to_disc(raw_coords, r_target=10.0, p_high=95, exponent=0.7)

        # ── 4c. Feature A — preserve pinned nodes ─────────────────────────────
        # For any node with pinned=true and valid stored (x,y), overwrite the
        # FR+post-processed coord with the user-set one so drag-and-drop
        # positions survive every subsequent recompute.
        coords = list(coords)  # make mutable
        for i, nid in enumerate(node_ids):
            nd = node_index[nid]
            if nd["pinned"] and nd["stored_x"] is not None and nd["stored_y"] is not None:
                coords[i] = (float(nd["stored_x"]), float(nd["stored_y"]))

        # ── 5. Assemble result lists ───────────────────────────────────────────
        # structural_degree = count of distinct incident structural edges (ADR-0016 §2).
        # After removing (c)/(d) from candidate_pairs, g_weighted IS the structural graph,
        # so g_weighted.degree() already yields structural_degree.
        structural_degrees = g_weighted.degree()

        # Size formula: BASE + GROWTH·sqrt(structural_degree) (ADR-0016 §2)
        # BASE=1.0 → isolated nodes render at 1.0 (clearly clickable).
        # GROWTH=2.5 → degree-30 hub ≈ 14.7, degree-1 leaf ≈ 3.5, degree-3 ≈ 5.3.
        _BASE = 1.0
        _GROWTH = 2.5

        node_snapshots: list[NodeSnapshot] = []
        for i, nid in enumerate(node_ids):
            nd = node_index[nid]
            deg = structural_degrees[i]
            size = max(1.0, _BASE + _GROWTH * math.sqrt(deg))
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
        for a, b, w, sig, kind in weighted_edges:
            nid_a = node_ids[a]
            nid_b = node_ids[b]
            src_id, tgt_id = _canonical_key_ids(nid_a, nid_b)
            edge_snapshots.append(EdgeSnapshot(source=nid_a, target=nid_b, weight=w, kind=kind))
            edge_db_rows.append(
                {
                    "vault_id": vault_id,
                    "source_page_id": src_id,
                    "target_page_id": tgt_id,
                    "weight": w,
                    "signals": sig,
                    "kind": kind,
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
            # Pages: id, page_type (mapped column 'type'), title, sources, pinned, x, y
            # pinned + x/y are used to preserve manually-positioned nodes (Feature A).
            pages_result = await sess.execute(
                sa_text(
                    "SELECT id, type AS page_type, title, sources, "
                    "       pinned, x AS stored_x, y AS stored_y "
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
                    sa_text(
                        "UPDATE pages SET x = :x, y = :y WHERE id = CAST(:id AS uuid)"
                    ).bindparams(id=str(row["id"]), x=row["x"], y=row["y"])
                )

            # Replace edges for this vault (delete-then-insert)
            await sess.execute(
                sa_text("DELETE FROM edges WHERE vault_id = :vid").bindparams(vid=vault_id)
            )
            _INSERT_EDGE = (
                "INSERT INTO edges "
                "(id, vault_id, source_page_id, target_page_id,"
                " weight, signals, kind, created_at) "
                "VALUES "
                "(CAST(:id AS uuid), :vault_id, CAST(:source_page_id AS uuid),"
                " CAST(:target_page_id AS uuid), :weight, CAST(:signals AS jsonb), :kind, now())"
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
                        kind=row.get("kind", "link"),
                    )
                )

        if session is not None:
            await _run(session)
        else:
            async with get_session() as sess:
                await _run(sess)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _compress_to_disc(
    coords: list[tuple[float, float]],
    *,
    r_target: float = 10.0,
    p_high: int = 95,
    exponent: float = 0.7,
) -> list[tuple[float, float]]:
    """
    Post-process FR coordinates into a rounder disc envelope (Feature B).

    Algorithm (deterministic, bounded, O(n)):
      1. Center at centroid.
      2. Convert to polar (r, theta) per node.
      3. Normalize r against the p_high percentile and apply a concave exponent:
           r' = r_target * min(1, (r / r_p_high)) ** exponent
         exponent < 1 → outliers pulled inward, common nodes spread out;
         min(1, ...) caps the output at r_target (nothing outside the disc).
      4. Convert back to Cartesian.

    Angular positions are preserved exactly — this is purely a radial rescaling.
    Single-node graphs or degenerate (all-zero) layouts are returned unchanged.
    """
    if len(coords) <= 1:
        return coords

    # 1. Centroid
    cx = sum(x for x, _ in coords) / len(coords)
    cy = sum(y for _, y in coords) / len(coords)
    centered = [(x - cx, y - cy) for x, y in coords]

    # 2. Radii
    radii = [math.sqrt(x * x + y * y) for x, y in centered]
    r_max = max(radii)
    if r_max < 1e-9:
        # All nodes at the same point — return as-is (degenerate layout)
        return coords

    # 3. High-percentile radius for normalization
    sorted_r = sorted(radii)
    idx = int(math.ceil(p_high / 100.0 * len(sorted_r))) - 1
    idx = max(0, min(idx, len(sorted_r) - 1))
    r_ref = sorted_r[idx]
    if r_ref < 1e-9:
        r_ref = r_max  # fallback: avoid division by zero

    # 4. Compress and convert back
    result: list[tuple[float, float]] = []
    for (dx, dy), r in zip(centered, radii, strict=True):
        if r < 1e-9:
            result.append((cx, cy))
            continue
        theta = math.atan2(dy, dx)
        r_prime = r_target * min(1.0, (r / r_ref) ** exponent)
        result.append((r_prime * math.cos(theta), r_prime * math.sin(theta)))

    return result


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
