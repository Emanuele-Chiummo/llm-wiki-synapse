"""
GraphEngine — 4-signal edge-weight computation + ForceAtlas2 layout (F4, I2).

Public API:
  GraphEngine.recompute(vault_id, session?) → GraphSnapshot

Invariant compliance:
  I2 — ForceAtlas2 layout runs ONLY here, server-side, via fa2_modified + python-igraph.
       Coordinates are persisted in pages.x/y (ADR-0013, ADR-0045, AQ-6).
       Never called from any frontend path.
  I1 — Reads only pages + links tables; never walks vault/ filesystem.
  I7 — Single bounded pass; logs node/edge count + wall-clock duration.
  I9 — python-igraph for Adamic-Adar; fa2_modified for ForceAtlas2 layout (R9).

Edge INCLUSION rule (ADR-0016 — supersedes ADR-0012 §3):
  An edge (A,B) EXISTS iff:
    direct_link_count(A,B) > 0  OR  shared_source_count(A,B) > 0
  AA and type-affinity are MODULATORS only — they adjust the weight of already-structural
  edges but NEVER create an edge on their own.  This prevents type-cliques (hairball).

Edge-weight formula (ADR-0012 §1/§2 coefficients — applied only to structural pairs):
  w(A,B) = 3.0·direct_link_count(A,B)
          + 4.0·shared_source_count(A,B)
          + 1.5·adamic_adar(A,B)
          + 1.0·type_affinity(A,B)      # G-P1-7: cross-type matrix (llm_wiki parity),
                                        # replaces the old binary same_type signal

Per-edge kind (ADR-0016 §4):
  "link"   — direct_link_count > 0  (wikilink; may also have source/AA/type weight)
  "source" — direct_link_count == 0 AND shared_source_count > 0 (provenance-only)

Node size (ADR-0016 §2):
  size = BASE + GROWTH·sqrt(structural_degree)   BASE=1.0, GROWTH=2.5
  structural_degree = count of distinct incident structural edges.

ForceAtlas2 layout (ADR-0045 — supersedes FR in ADR-0013 §1/§2):
  Algorithm: fa2_modified.ForceAtlas2 with gravity=1.0, strongGravityMode=True,
    scalingRatio=2.0 (3.0 when n>400), barnesHutOptimize=(n>50), verbose=False.
  Iterations taper by node count (see FA2_ITERS_* constants below).
  Determinism strategy (ADR-0045 §2 — ADR-0013 §1/§2 superseded for layout):
    1. Initial positions from igraph layout_circle() — pure deterministic, no RNG.
    2. numpy.random.seed(FA2_SEED) called immediately before fa2_modified call
       as belt-and-suspenders (FA2 internals may call numpy RNG).
    Identical topology+weights → identical coordinates across any two recomputes.
  Layout post-processing (Feature B — disc compression) REMOVED (ADR-0045 §3):
    FA2's organic spread is the desired output; disc compression was fighting it.
    Raw FA2 output is used directly, EXCEPT for the outlier clamp below.
  Outlier clamp (ADR-0045 §5 — _clamp_outliers, runs LAST):
    Pulls a few runaway nodes (|x|,|y| that would collapse the viewer's fit-to-view onto
    a dot) radially onto a cap = 3× the p90 radius from the median center. In-cap nodes are
    untouched, so the organic core spread is preserved. Runs AFTER pinning so it also tames
    runaway pinned coords; the clamped coords are persisted, so bad coords self-heal.

Node pinning (Feature A):
  pages.pinned=true → engine reads stored (x,y) from DB and overwrites FA2 coords.
  Applied AFTER FA2 layout (then the outlier clamp runs); PATCH /pages/{id}/position sets
  the flag. Pinned nodes stay put across every subsequent recompute (within the clamp cap).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session

logger = logging.getLogger(__name__)

# ── Seed (ADR-0013 §2 / ADR-0045 §2) ─────────────────────────────────────────
_DEFAULT_SEED = 42
FA2_SEED: int = int(os.environ.get("GRAPH_LAYOUT_SEED", str(_DEFAULT_SEED)))

# ── ForceAtlas2 iteration taper by node count (ADR-0045 §1) ──────────────────
# Mirrors nashsu/llm_wiki layoutIterations tuning: small graphs run more
# iterations for quality; large graphs are capped to keep recompute bounded (I7).
FA2_ITERS_SMALL: int = 140  # n <= 100
FA2_ITERS_MEDIUM: int = 100  # 100 < n <= 400
FA2_ITERS_LARGE: int = 60  # 400 < n <= 1000
FA2_ITERS_XLARGE: int = 40  # 1000 < n <= 2500
FA2_ITERS_HUGE: int = 28  # n > 2500

# scalingRatio increases for larger graphs to counter crowding (ADR-0045 §1)
FA2_SCALING_RATIO_SMALL: float = 2.0  # n <= 400
FA2_SCALING_RATIO_LARGE: float = 3.0  # n > 400

# ── Outlier clamp (ADR-0045 §5 — tames FA2 runaway nodes without squashing spread) ──
# FA2 (esp. large graphs with few iterations) can fling a handful of loosely-connected
# nodes to extreme coordinates (|x|,|y| in the millions).  Sigma's fit-to-view then zooms
# out so far that the dense core collapses to a dot ("everything collapsed at the center").
# We clamp ONLY nodes whose radius exceeds FA2_CLAMP_FACTOR × the FA2_CLAMP_PERCENTILE-th
# percentile radius (measured from the MEDIAN center — robust to the outliers themselves),
# pulling them radially onto that cap.  Every node inside the cap is left EXACTLY where FA2
# put it, so the organic core spread (the reason disc-compression was removed) is preserved.
#
# Params: the reference is the p90 radius (the EDGE of the dense core — robust to a minority
# of runaway nodes, which are what we want to tame; a higher percentile like p98 would itself
# land on an outlier when >2% of nodes run away, making the cap useless). factor=3 keeps a
# healthy organic layout untouched (nothing sits at 3× the core-edge radius there) while
# crushing millions-scale runaways down to ~3× the core radius, so the core fills the view.
FA2_CLAMP_PERCENTILE: float = 90.0
FA2_CLAMP_FACTOR: float = 3.0

# ── Type-affinity matrix — 4th weight signal (G-P1-7, llm_wiki parity) ────────
# Mirrors nashsu/llm_wiki src/lib/graph-relevance.ts TYPE_AFFINITY: a MODULATOR of
# already-structural edges (never creates an edge — ADR-0016 §1). It REWARDS cross-type
# pairs (entity↔concept = 1.2, concept↔synthesis = 1.2) and PENALIZES same-type pairs
# (entity↔entity 0.8, source↔source / query↔query 0.5). This shapes ForceAtlas2 clustering
# so related-but-different notes attract, replacing the previous binary same_type signal
# which had the OPPOSITE directional effect. Symmetric; unknown/None type pairs → 0.5.
_TYPE_AFFINITY: dict[str, dict[str, float]] = {
    "entity": {"concept": 1.2, "entity": 0.8, "source": 1.0, "synthesis": 1.0, "query": 0.8},
    "concept": {"entity": 1.2, "concept": 0.8, "source": 1.0, "synthesis": 1.2, "query": 1.0},
    "source": {"entity": 1.0, "concept": 1.0, "source": 0.5, "query": 0.8, "synthesis": 1.0},
    "query": {"concept": 1.0, "entity": 0.8, "synthesis": 1.0, "source": 0.8, "query": 0.5},
    "synthesis": {"concept": 1.2, "entity": 1.0, "source": 1.0, "query": 1.0, "synthesis": 0.8},
}
_TYPE_AFFINITY_DEFAULT: float = 0.5


def _type_affinity(type_a: str | None, type_b: str | None) -> float:
    """
    Type-affinity modulator for the 4th weight signal (G-P1-7, llm_wiki parity).

    Looks up the (type_a, type_b) pair in _TYPE_AFFINITY (case-insensitive). The matrix
    is symmetric, so a missing outer key is retried with the operands swapped before
    falling back to _TYPE_AFFINITY_DEFAULT (0.5). Types outside the llm_wiki 5-type set
    (e.g. comparison/overview/index/log) or None resolve to the 0.5 default — matching
    llm_wiki's `?? 0.5` fallback.
    """
    if type_a is None or type_b is None:
        return _TYPE_AFFINITY_DEFAULT
    a = type_a.lower()
    b = type_b.lower()
    row = _TYPE_AFFINITY.get(a)
    if row is not None and b in row:
        return row[b]
    row_b = _TYPE_AFFINITY.get(b)
    if row_b is not None and a in row_b:
        return row_b[a]
    return _TYPE_AFFINITY_DEFAULT


# ── Snapshot dataclass ─────────────────────────────────────────────────────────


@dataclass
class NodeSnapshot:
    """One graph node as returned by GET /graph (ADR-0014 §6).

    domain: the node page's own dominant in-vocabulary domain, or None.
      Computed per node from its domain/* tags in _compute_graph_sync step 5.
      Tie-break rule (deterministic): when a page has ≥2 in-vocab domain tags,
      `domain` is the one that appears FIRST in vocabulary order (i.e. earliest
      index in the domain_vocab list passed to _compute_graph_sync). This matches
      the community dominant_domain convention and keeps the assignment stable
      across recomputes as long as the vocabulary order does not change.
    """

    id: str
    title: str | None
    page_type: str | None
    x: float
    y: float
    degree: int = 0
    size: float = 1.0
    community: int = -1  # -1 = unassigned; set by Louvain (G-P0-2)
    domain: str | None = None  # own dominant in-vocab domain (F18, ADR-0054 §2.2)


@dataclass
class CommunityTopPage:
    """Highest-degree member page of a community — used for label fallback and tooltip."""

    id: str
    title: str | None
    slug: str


@dataclass
class CommunitySnapshot:
    """
    Per-community summary for GET /graph (G-P0-2).

    id             : re-numbered id (0 = largest community)
    size           : number of nodes in this community
    cohesion       : intra-edge density = intraEdges / (size*(size-1)/2), in [0,1].
                     Used for low-cohesion warnings on the client-side legend.
    label          : human-readable display name.  Priority: dominant_domain (when a
                     domain/* tag in the active vocab dominates) → top_page.title
                     (llm_wiki fallback) → "Comunità {id}".  Computed in recompute()
                     alongside cohesion — no extra scan (I1), no extra provider call (I7).
    dominant_domain: the most-frequent in-vocab "domain/<Name>" tag (prefix stripped)
                     among community members, or None when no domain tags are present.
    top_page       : highest-degree member page {id, title, slug} for disambiguation /
                     tooltip; None for empty communities.
    """

    id: int
    size: int
    cohesion: float
    label: str = ""
    dominant_domain: str | None = None
    top_page: CommunityTopPage | None = None


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
    communities: per-community summary list (G-P0-2); empty until Louvain runs.
    """

    nodes: list[NodeSnapshot] = field(default_factory=list)
    edges: list[EdgeSnapshot] = field(default_factory=list)
    data_version: int = 0
    communities: list[CommunitySnapshot] = field(default_factory=list)


# ── GraphEngine ────────────────────────────────────────────────────────────────


class GraphEngine:
    """
    Computes 4-signal weighted edges and ForceAtlas2 layout from Postgres tables.

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
          1. Async DB read: load nodes (pages) + resolved links from Postgres (I1 — no vault walk).
          2. CPU-bound graph compute in thread executor (B1 fix — I2: never block event loop):
               igraph build + ForceAtlas2 layout + Louvain communities + 4-signal weighting.
          3. Async DB write: persist edges + coords in ONE transaction (ADR-0013 §algorithm step 6).
          4. Return GraphSnapshot (GraphCache stamps data_version).
          5. Log node/edge count + wall-clock duration (I7 observability).
        """
        t0 = time.perf_counter()
        logger.info("GraphEngine.recompute: start vault_id=%r seed=%d", vault_id, FA2_SEED)

        # ── 1. Async DB read: load pages + links (I1 — tables only, no vault walk) ──
        nodes_data, links_data = await self._load_data(vault_id, session)

        if not nodes_data:
            logger.info("GraphEngine.recompute: no live pages — empty snapshot")
            await self._persist_results(vault_id, [], [], session)
            return GraphSnapshot()

        # ── 2. CPU-bound graph compute in thread executor (B1 fix — I2) ────────────
        # All igraph/FA2/numpy work is offloaded so the event loop stays free for
        # incoming requests, chat streams, and watcher events during the layout.
        # Seam: plain Python dicts in (no AsyncSession), plain Python dicts out.
        # domain_vocab is read once here (O(1) in-memory, I7) and passed as a plain
        # list into the thread — no async I/O inside _compute_graph_sync (I2).
        from app.config_overrides import effective_domain_vocabulary  # noqa: PLC0415

        domain_vocab: list[str] = effective_domain_vocabulary()
        coord_rows, edge_db_rows, snapshot = await asyncio.to_thread(
            _compute_graph_sync, nodes_data, links_data, vault_id, domain_vocab
        )

        # ── 3. Async DB write: persist coords + edges + community in ONE transaction ──
        await self._persist_results(vault_id, coord_rows, edge_db_rows, session)

        elapsed = time.perf_counter() - t0
        logger.info(
            "GraphEngine.recompute: done vault_id=%r nodes=%d edges=%d"
            " communities=%d elapsed=%.3fs",
            vault_id,
            len(snapshot.nodes),
            len(snapshot.edges),
            len(snapshot.communities),
            elapsed,
        )

        return snapshot

    # ── Private helpers ────────────────────────────────────────────────────────

    async def _load_data(
        self,
        vault_id: str,
        session: AsyncSession | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Load live pages and resolved links from Postgres (I1 — no vault walk)."""

        async def _run(sess: AsyncSession) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
            # Pages: id, page_type (mapped column 'type'), title, sources, pinned, x, y, tags
            # pinned + x/y are used to preserve manually-positioned nodes (Feature A).
            # tags: carried for community domain-label computation (F18) — no extra query (I1).
            pages_result = await sess.execute(
                sa_text(
                    "SELECT id, type AS page_type, title, sources, "
                    "       pinned, x AS stored_x, y AS stored_y, tags "
                    "FROM pages "
                    # Exclude raw-source tracking rows (raw/sources/*): they exist only for
                    # I1 incremental hashing + Qdrant retrieval and carry no title/type, so
                    # they must never surface as titleless 'other' graph nodes (the knowledge
                    # graph is wiki pages only — F4/I2).
                    "WHERE vault_id = :vid AND deleted_at IS NULL "
                    "  AND file_path NOT LIKE 'raw/%'"
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
            # Update coords + community per node (G-P0-2: community persisted alongside x/y).
            # One executemany instead of one round-trip per node — the SQL text is byte-for-byte
            # unchanged (Postgres semantics identical), only the DBAPI batches it. At 1000 nodes
            # this collapses ~1000 sequential asyncpg round-trips into a single executemany.
            if coord_rows:
                coord_params = [
                    {
                        "id": str(row["id"]),
                        "x": row["x"],
                        "y": row["y"],
                        "community": row.get("community", -1),
                    }
                    for row in coord_rows
                ]
                await sess.execute(
                    sa_text(
                        "UPDATE pages SET x = :x, y = :y, community = :community"
                        " WHERE id = CAST(:id AS uuid)"
                    ),
                    coord_params,
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
            if edge_rows:
                edge_params = [
                    {
                        "id": str(uuid.uuid4()),
                        "vault_id": row["vault_id"],
                        "source_page_id": str(row["source_page_id"]),
                        "target_page_id": str(row["target_page_id"]),
                        "weight": row["weight"],
                        "signals": _json_dumps(row["signals"]),
                        "kind": row.get("kind", "link"),
                    }
                    for row in edge_rows
                ]
                await sess.execute(sa_text(_INSERT_EDGE), edge_params)

        if session is not None:
            await _run(session)
        else:
            async with get_session() as sess:
                await _run(sess)


# ── CPU-bound graph computation (runs in thread via asyncio.to_thread) ────────


def _compute_graph_sync(
    nodes_data: list[dict[str, Any]],
    links_data: list[dict[str, Any]],
    vault_id: str,
    domain_vocab: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], GraphSnapshot]:
    """
    Pure CPU graph computation: 4-signal weighting + FA2 layout + Louvain communities.

    Designed to run in a thread via asyncio.to_thread() so the event loop is never
    blocked during heavy igraph/FA2/numpy work (B1 fix, I2).

    Inputs are plain Python data from the async DB read phase of recompute().
    No DB sessions, no async code; all module-level helpers are pure functions.

    Returns:
        coord_rows   — list of {id, x, y, community} dicts for UPDATE pages SET x,y,community
        edge_db_rows — list of edge dicts for INSERT INTO edges
        snapshot     — GraphSnapshot (nodes, edges, communities)
    """

    # Build node index: str(uuid) → dict of page attributes
    # pinned/stored_x/stored_y carried for Feature A (pinned-node preservation).
    # tags: carried for community domain-label computation (F18, I1 — same query, no extra scan).
    # tags may arrive as a JSON string (SQLite/aiosqlite) or as a list (asyncpg/Postgres).
    def _parse_tags(raw: object) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, list):
            return [t for t in raw if isinstance(t, str)]
        if isinstance(raw, str):
            import json as _json  # noqa: PLC0415

            try:
                parsed = _json.loads(raw)
                return [t for t in parsed if isinstance(t, str)] if isinstance(parsed, list) else []
            except Exception:
                return []
        return []

    node_index: dict[str, dict[str, Any]] = {
        str(row["id"]): {
            "id": str(row["id"]),
            "title": row.get("title"),
            "page_type": row.get("page_type"),
            "sources": set(row.get("sources") or []),
            "pinned": bool(row.get("pinned", False)),
            "stored_x": row.get("stored_x"),
            "stored_y": row.get("stored_y"),
            "tags": _parse_tags(row.get("tags")),
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
        # 4th signal: type-affinity matrix (G-P1-7, llm_wiki parity) — rewards
        # cross-type pairs, penalizes same-type. Replaces the old binary same_type.
        type_affinity = _type_affinity(type_a, type_b)

        # Weight formula (ADR-0012 §1/§2 coefficients UNCHANGED; 4th term is now
        # the type-affinity modulator instead of binary same_type — G-P1-7)
        w = 3.0 * direct + 4.0 * shared + 1.5 * aa + 1.0 * type_affinity
        signals: dict[str, float] = {
            "direct": 3.0 * direct,
            "source": 4.0 * shared,
            "aa": 1.5 * aa,
            "type": type_affinity,
        }
        # Per-edge kind (ADR-0016 §4): "link" wins when both structural signals present
        kind = "link" if direct > 0 else "source"
        weighted_edges.append((a, b, w, signals, kind))

    # ── 4. Build weighted igraph + ForceAtlas2 → coords (I2, ADR-0045) ──────
    weighted_edges_idx = [(a, b) for a, b, _w, _s, _k in weighted_edges]
    edge_weights = [w for _a, _b, w, _s, _k in weighted_edges]

    g_weighted = igraph.Graph(
        n=n,
        edges=weighted_edges_idx if weighted_edges_idx else [],
        directed=False,
    )
    if edge_weights:
        g_weighted.es["weight"] = edge_weights

    # Seed the igraph RNG (still used by Louvain community_multilevel below)
    igraph.set_random_number_generator(_SeedableRNG(FA2_SEED))

    # Run ForceAtlas2 layout (ADR-0045).  Determinism: circle-init pos + numpy seed.
    raw_coords: list[tuple[float, float]] = _forceatlas2_layout(g_weighted, edge_weights, n)

    # ── 4b. Feature B (disc-compression) REMOVED (ADR-0045 §3) ──────────
    # FA2's organic spread is the desired output — raw FA2 coords used directly.
    coords = list(raw_coords)

    # ── 4c. Feature A — preserve pinned nodes ─────────────────────────────
    # For any node with pinned=true and valid stored (x,y), overwrite the
    # FA2-computed coord with the user-set one so drag-and-drop positions
    # survive every subsequent recompute.
    for i, nid in enumerate(node_ids):
        nd = node_index[nid]
        if nd["pinned"] and nd["stored_x"] is not None and nd["stored_y"] is not None:
            coords[i] = (float(nd["stored_x"]), float(nd["stored_y"]))

    # ── 4d. Outlier clamp (ADR-0045 §5) — LAST, so it also tames runaway PINNED
    # coords, not just FA2 outliers.  A handful of flung-out nodes (or nodes
    # accidentally pinned at runaway coords, e.g. a mobile tap-jitter drag) would
    # otherwise dominate sigma's fit-to-view and collapse the dense core to a dot.
    # The clamp leaves every in-cap node EXACTLY where it was (organic spread + legit
    # in-view pins preserved); only nodes beyond the cap are pulled radially onto it.
    # Running it here (after pinning) makes the layout self-healing: the clamped coords
    # are what get persisted, so runaway stored coords are repaired on the next recompute.
    coords = _clamp_outliers(coords)

    # ── 4e. Louvain community detection (G-P0-2, I2) ─────────────────────
    # Run community_multilevel (Louvain) on the weighted structural graph.
    # Re-number communities by size (largest = 0) for stable coloring, matching
    # the nashsu/llm_wiki convention (R1). Bounded: single O(n log n) pass on the
    # server-side graph; never called on the client (I2).
    # Isolated nodes (degree-0 in g_weighted) form their own singleton communities.
    community_assignments: list[int] = _compute_louvain_communities(g_weighted, node_ids)

    # Per-community cohesion: intra-edge density (intraEdges / possibleEdges).
    # possibleEdges for a community of size s = s*(s-1)/2.  Zero for singletons.
    # Collect which structural edge pairs share a community for cohesion calc.
    community_intra_edges: dict[int, int] = {}
    community_sizes: dict[int, int] = {}
    for cid in community_assignments:
        community_sizes[cid] = community_sizes.get(cid, 0) + 1
    for a, b, _w, _s, _k in weighted_edges:
        ca = community_assignments[a]
        cb = community_assignments[b]
        if ca == cb:
            community_intra_edges[ca] = community_intra_edges.get(ca, 0) + 1

    community_snapshots: list[CommunitySnapshot] = []
    for cid, size in sorted(community_sizes.items()):
        possible = size * (size - 1) / 2
        intra = community_intra_edges.get(cid, 0)
        cohesion = (intra / possible) if possible > 0 else 0.0
        community_snapshots.append(
            CommunitySnapshot(id=cid, size=size, cohesion=round(cohesion, 4))
        )
    # Sort by id ascending (id 0 = largest community, already ordered by _compute_louvain)
    community_snapshots.sort(key=lambda c: c.id)

    # ── 4f. Community labels: dominant_domain + top_page (F18, I1, I7) ───────
    # Computed here alongside cohesion — no extra DB scan, no provider call.
    # vocab_set: the active domain vocabulary (passed from recompute via domain_vocab).
    # A domain/* tag not in vocab_set is treated as stale and ignored (ADR-0054 §2.2).
    #
    # structural_degrees_for_labels: g_weighted.degree() gives structural degree per node
    # (count of distinct incident structural edges — direct-link + shared-source).
    # We compute it here so it can be reused in step 5 below without a second .degree() call.
    structural_degrees_pre: list[int] = g_weighted.degree()

    vocab_set: set[str] = set(domain_vocab) if domain_vocab else set()
    _DOMAIN_PREFIX = "domain/"
    _DOMAIN_PREFIX_LEN = len(_DOMAIN_PREFIX)

    # Map: community_id → Counter[domain_name] (for dominant_domain)
    from collections import Counter as _Counter  # noqa: PLC0415

    community_domain_counts: dict[int, _Counter[str]] = {}
    # community_top: community_id → (structural_degree, node_idx) — track highest-degree member
    community_top: dict[int, tuple[int, int]] = {}

    for i, nid in enumerate(node_ids):
        cid = community_assignments[i]
        nd = node_index[nid]
        deg = structural_degrees_pre[i]  # structural degree from g_weighted

        # Track top-degree member
        prev_deg, _prev_i = community_top.get(cid, (-1, -1))
        if deg > prev_deg:
            community_top[cid] = (deg, i)

        # Tally in-vocab domain tags
        for tag in nd["tags"]:
            if tag.startswith(_DOMAIN_PREFIX):
                domain_name = tag[_DOMAIN_PREFIX_LEN:]
                if vocab_set and domain_name not in vocab_set:
                    continue  # stale tag — ignore (ADR-0054 §2.2)
                if not vocab_set:
                    # No vocab configured: accept all domain/* tags as-is
                    pass
                ctr = community_domain_counts.setdefault(cid, _Counter())
                ctr[domain_name] += 1

    def _slugify(text: str | None) -> str:
        """Simple slug for top_page (mirrors the stats.py slugify pattern)."""
        if not text:
            return ""
        import re as _re  # noqa: PLC0415

        return _re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")

    for cs in community_snapshots:
        cid = cs.id

        # dominant_domain: most-frequent in-vocab domain tag
        domain_ctr: _Counter[str] | None = community_domain_counts.get(cid)
        dominant: str | None = None
        if domain_ctr:
            dominant = domain_ctr.most_common(1)[0][0]
        cs.dominant_domain = dominant

        # top_page: highest-degree member
        top_entry = community_top.get(cid)
        if top_entry is not None:
            _deg, top_i = top_entry
            top_nid = node_ids[top_i]
            top_nd = node_index[top_nid]
            cs.top_page = CommunityTopPage(
                id=top_nid,
                title=top_nd["title"],
                slug=_slugify(top_nd["title"]),
            )

        # label: dominant_domain → top_page.title → "Comunità {id}"
        if dominant:
            cs.label = dominant
        elif cs.top_page and cs.top_page.title:
            cs.label = cs.top_page.title
        else:
            cs.label = f"Comunità {cid}"

    # ── 5. Assemble result lists ───────────────────────────────────────────
    # structural_degree = count of distinct incident structural edges (ADR-0016 §2).
    # After removing (c)/(d) from candidate_pairs, g_weighted IS the structural graph,
    # so g_weighted.degree() already yields structural_degree.
    # structural_degrees_pre was already computed in 4f — reuse to avoid a second call.
    structural_degrees = structural_degrees_pre

    # Size formula: BASE + GROWTH·sqrt(structural_degree) (ADR-0016 §2)
    # BASE=1.0 → isolated nodes render at 1.0 (clearly clickable).
    # GROWTH=2.5 → degree-30 hub ≈ 14.7, degree-1 leaf ≈ 3.5, degree-3 ≈ 5.3.
    _BASE = 1.0
    _GROWTH = 2.5

    # ── 5a. Per-node domain (F18, ADR-0054 §2.2, no extra query — I1) ────────
    # Reuses the tags already loaded per node (I1) and vocab_set/domain_vocab already
    # in scope from step 4f.  No extra DB scan, no provider call (I7).
    #
    # Tie-break rule (documented in NodeSnapshot docstring):
    #   When a node page has ≥2 in-vocab domain tags, `domain` is the one that appears
    #   FIRST in vocabulary order (earliest index in domain_vocab). This is deterministic
    #   across recomputes as long as the vocabulary list order is unchanged, and mirrors
    #   the intent of the community dominant_domain convention.
    #   When domain_vocab is empty (no vocabulary configured), all domain/* tags are
    #   accepted and the first one found in iteration order is used (same as community path).

    def _node_domain(tags: list[str]) -> str | None:
        """
        Return the first in-vocab domain name from *tags* in vocabulary order, or None.

        Vocabulary order: domain_vocab list index (smallest index wins on a tie).
        Empty vocab: accept all domain/* tags, return the first one found in tag order.
        """
        candidate_names: list[str] = []
        for tag in tags:
            if tag.startswith(_DOMAIN_PREFIX):
                name = tag[_DOMAIN_PREFIX_LEN:]
                if vocab_set:
                    if name in vocab_set:
                        candidate_names.append(name)
                else:
                    # No vocab configured: accept any domain/* tag
                    candidate_names.append(name)
        if not candidate_names:
            return None
        if len(candidate_names) == 1:
            return candidate_names[0]
        # Tie-break: pick the one with the smallest index in domain_vocab
        if domain_vocab:
            # Build index map once (O(v) where v = vocab length); v ≤ 100 (ADR-0054 §2.1)
            vocab_index: dict[str, int] = {name: idx for idx, name in enumerate(domain_vocab)}
            return min(candidate_names, key=lambda n: vocab_index.get(n, len(domain_vocab)))
        # No vocab → keep first encountered in tag order
        return candidate_names[0]

    node_snapshots: list[NodeSnapshot] = []
    for i, nid in enumerate(node_ids):
        nd = node_index[nid]
        deg = structural_degrees[i]
        node_size: float = max(1.0, _BASE + _GROWTH * math.sqrt(deg))
        node_snapshots.append(
            NodeSnapshot(
                id=nid,
                title=nd["title"],
                page_type=nd["page_type"],
                x=coords[i][0],
                y=coords[i][1],
                degree=deg,
                size=node_size,
                community=community_assignments[i],
                domain=_node_domain(nd["tags"]),
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

    # ── 6. Coord rows for async DB write ──────────────────────────────────
    coord_rows: list[dict[str, Any]] = [
        {"id": ns.id, "x": ns.x, "y": ns.y, "community": ns.community} for ns in node_snapshots
    ]

    return (
        coord_rows,
        edge_db_rows,
        GraphSnapshot(
            nodes=node_snapshots,
            edges=edge_snapshots,
            communities=community_snapshots,
        ),
    )


# ── Helpers ────────────────────────────────────────────────────────────────────


def _forceatlas2_layout(
    g_weighted: Any,
    edge_weights: list[float],
    n: int,
) -> list[tuple[float, float]]:
    """
    Run ForceAtlas2 on g_weighted and return a list of (x, y) coordinates.

    Determinism strategy (ADR-0045 §2):
      1. Initial positions built from igraph layout_circle() — pure deterministic,
         no RNG involved.  Passed as `pos` to fa2_modified so FA2 does not randomize.
      2. numpy.random.seed(FA2_SEED) called immediately before the FA2 call as
         belt-and-suspenders (fa2_modified may draw from numpy's global RNG internally).
      Two calls on identical (g_weighted, edge_weights, n) MUST yield identical output.

    Iteration taper (ADR-0045 §1):
      n<=100 → FA2_ITERS_SMALL (140), n<=400 → FA2_ITERS_MEDIUM (100),
      n<=1000 → FA2_ITERS_LARGE (60), n<=2500 → FA2_ITERS_XLARGE (40), else FA2_ITERS_HUGE (28).

    Single-node / no-edge graphs return trivial coords without running FA2.
    """
    import igraph
    from fa2_modified import ForceAtlas2

    if n == 0:
        return []
    if n == 1:
        return [(0.0, 0.0)]

    # Choose iteration count by node count (taper for I7 bounding)
    if n <= 100:
        iterations = FA2_ITERS_SMALL
    elif n <= 400:
        iterations = FA2_ITERS_MEDIUM
    elif n <= 1000:
        iterations = FA2_ITERS_LARGE
    elif n <= 2500:
        iterations = FA2_ITERS_XLARGE
    else:
        iterations = FA2_ITERS_HUGE

    # scalingRatio: larger graphs need more spread to avoid crowding
    scaling_ratio = FA2_SCALING_RATIO_LARGE if n > 400 else FA2_SCALING_RATIO_SMALL

    # More organic ("flow") layout toward the Obsidian look. NOTE: fa2_modified does NOT
    # implement linLogMode (asserts False), so the strongest Obsidian lever is unavailable
    # without a layout-lib swap (GL6, declined). We keep strongGravity for a cohesive core
    # and add outboundAttractionDistribution (hubs pushed outward / leaves pulled in) for a
    # more organic distribution. The real "not detached" fix is reducing edge culling (FE).
    # Determinism (ADR-0045 §2) preserved: circle-init + numpy seed, params fixed.
    fa = ForceAtlas2(
        outboundAttractionDistribution=True,
        edgeWeightInfluence=1.0,
        gravity=1.0,
        scalingRatio=scaling_ratio,
        strongGravityMode=True,
        barnesHutOptimize=(n > 50),
        verbose=False,
    )

    # Deterministic initial positions: circle layout (no RNG, pure math)
    circle_layout: igraph.Layout = g_weighted.layout_circle()
    init_pos: list[tuple[float, float]] = [(p[0], p[1]) for p in circle_layout]

    # Belt-and-suspenders numpy seed before FA2 call (ADR-0045 §2)
    np.random.seed(FA2_SEED)  # noqa: NPY002 — seeded for determinism, not security

    weight_attr: str | None = "weight" if edge_weights else None
    layout = fa.forceatlas2_igraph_layout(
        g_weighted,
        pos=init_pos,
        iterations=iterations,
        weight_attr=weight_attr,
    )

    return [(pos[0], pos[1]) for pos in layout]


def _clamp_outliers(
    coords: list[tuple[float, float]],
    *,
    percentile: float = FA2_CLAMP_PERCENTILE,
    factor: float = FA2_CLAMP_FACTOR,
) -> list[tuple[float, float]]:
    """
    Pull FA2 runaway outliers inward without touching the organic core (ADR-0045 §5).

    Unlike _compress_to_disc (which radially rescales EVERY node and squashes the spread),
    this leaves every node inside the cap EXACTLY where FA2 placed it and only clamps the
    few extreme outliers that would otherwise dominate sigma's fit-to-view.

    Algorithm (deterministic, bounded, O(n log n) for the percentile sort):
      1. Center on the MEDIAN (x, y) — robust to the outliers we are trying to tame
         (the centroid would be dragged toward them).
      2. radius_i = dist(node_i, median_center).
      3. r_ref = the `percentile`-th percentile radius; cap = factor * r_ref.
      4. For nodes with radius > cap: rescale radially onto the cap (angle preserved).
         All other nodes returned unchanged.

    Degenerate inputs (<=2 nodes, all-coincident, zero reference radius) are returned as-is.
    """
    if len(coords) <= 2:
        return coords

    xs_sorted = sorted(x for x, _ in coords)
    ys_sorted = sorted(y for _, y in coords)
    mid = len(coords) // 2
    cx = xs_sorted[mid]
    cy = ys_sorted[mid]

    radii = [math.hypot(x - cx, y - cy) for x, y in coords]
    sorted_r = sorted(radii)
    idx = int(math.ceil(percentile / 100.0 * len(sorted_r))) - 1
    idx = max(0, min(idx, len(sorted_r) - 1))
    r_ref = sorted_r[idx]
    if r_ref < 1e-9:
        return coords

    cap = factor * r_ref
    out: list[tuple[float, float]] = []
    for (x, y), r in zip(coords, radii, strict=True):
        if r > cap and r > 1e-9:
            scale = cap / r
            out.append((cx + (x - cx) * scale, cy + (y - cy) * scale))
        else:
            out.append((x, y))
    return out


def _compress_to_disc(
    coords: list[tuple[float, float]],
    *,
    r_target: float = 10.0,
    p_high: int = 95,
    exponent: float = 0.7,
) -> list[tuple[float, float]]:
    """
    Post-process coordinates into a rounder disc envelope (Feature B — now unused).

    NOTE: This function is NO LONGER called by GraphEngine.recompute() as of ADR-0045.
    The disc-compression post-pass was removed because it fought FA2's organic spread.
    The function is retained here because existing unit tests in TestFeatureBDiscEnvelope
    import and test it in isolation; those tests remain valid as unit tests of this
    standalone function.  The engine no longer invokes it.

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


def _compute_louvain_communities(
    g_weighted: Any,
    node_ids: list[str],
) -> list[int]:
    """
    Run Louvain community detection on g_weighted and return a re-numbered
    community-id list (index = node index, value = community id).

    Re-numbering convention (matching nashsu/llm_wiki, R1):
      community 0 = the LARGEST community (most members).
      Ties broken by the natural ordering from igraph (stable within one run).

    Isolated nodes (no edges) are each placed in their own singleton community.

    Bounded: single O(n log n) pass (igraph community_multilevel). Never called
    on the client (I2). No RNG seeding needed — Louvain result is deterministic
    enough for our use (color stability within a run; re-runs may differ by 1-2
    nodes on the boundary but that is acceptable for graph coloring).
    """
    from collections import Counter

    n = g_weighted.vcount()
    if n == 0:
        return []

    has_edges = g_weighted.ecount() > 0
    has_weight_attr = has_edges and "weight" in g_weighted.es.attributes()
    weights = g_weighted.es["weight"] if has_weight_attr else None
    # community_multilevel = Louvain algorithm (Blondel et al., 2008)
    membership: list[int] = g_weighted.community_multilevel(weights=weights).membership

    # Re-number by descending community size (largest → 0)
    # membership is a list[int] of length n; values are raw igraph community ids.
    counts: Counter[int] = Counter(membership)
    # sorted descending by count → assign new id 0, 1, 2, …
    ranked = {old_id: new_id for new_id, (old_id, _) in enumerate(counts.most_common())}
    return [ranked[m] for m in membership]


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
