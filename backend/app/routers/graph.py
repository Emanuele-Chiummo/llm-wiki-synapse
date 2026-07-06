"""
Per-domain APIRouter: /graph/* + /overview/regenerate + /links/reresolve.

Covers:
  POST /overview/regenerate            — force overview.md refresh (F3)
  POST /links/reresolve                — reconnect dangling wikilinks (F3/K3)
  GET  /graph                          — precomputed graph (FA2 coords, F4/I2)
  POST /graph/recompute                — force recompute + reconnect (F4/I2)
  GET  /graph/communities/{id}         — community member list (G-P0-2)
  GET  /graph/edges/{src}/{tgt}        — edge detail between two pages
"""

from __future__ import annotations

import logging
import sys as _sys
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy import text as sa_text

from app.config import settings
from app.config_overrides import effective_str
from app.graph.cache import GraphCache
from app.graph.engine import GraphEngine, GraphSnapshot
from app.models import Link, Page, VaultState

logger = logging.getLogger(__name__)

router = APIRouter()


class _LazyMain:
    """Lazy proxy to app.main; enables test patches via app.main.* to propagate."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(_sys.modules["app.main"], name)

    def __setattr__(self, name: str, value: object) -> None:
        setattr(_sys.modules["app.main"], name, value)


_m = _LazyMain()


class RegenerateOverviewResponse(BaseModel):
    """
    Response for POST /overview/regenerate (F3 — force an overview refresh without re-ingesting).

    regenerated: True if the overview.md note was rewritten this call (False = degrade-safe
                 keep-previous, e.g. no provider configured or the provider call failed/timed out).
    detected_language: the language the overview was requested in (ISO-639-1) — from
                       the vault's existing pages; None when undetectable.
    """

    regenerated: bool = Field(description="Whether overview.md was rewritten this call.")
    detected_language: str | None = Field(
        default=None, description="Language the overview was regenerated in (ISO-639-1) or null."
    )

    model_config = {
        "json_schema_extra": {"example": {"regenerated": True, "detected_language": "it"}}
    }


class ReresolveLinksResponse(BaseModel):
    """
    Response for POST /links/reresolve (F3/K3 cross-ingest connectivity backfill).

    reconnected: number of previously-dangling links now bound to a live page.
    remaining_dangling: number of links still dangling after the pass.
    """

    reconnected: int = Field(
        description="Previously-dangling links reconnected to a live page in this pass."
    )
    remaining_dangling: int = Field(
        description="Links still dangling after the pass (target has no matching live page)."
    )

    model_config = {"json_schema_extra": {"example": {"reconnected": 42, "remaining_dangling": 7}}}


class GraphNodeResponse(BaseModel):
    """
    One graph node in the GET /graph response (ADR-0014 §6, AC-F4-3, ADR-0016 §4).

    Required: id, title, type, x, y.
    Optional rendering hints (derived server-side): size, degree.
    community: Louvain community id (G-P0-2); -1 when not yet assigned.
    domain: the node page's own dominant in-vocabulary domain (F18, ADR-0054 §2.2).
      Stripped of the "domain/" prefix.  None when the page has no in-vocab domain tags.
      Tie-break: when ≥2 in-vocab domain tags exist, first in vocabulary order wins.
      Backward-compatible additive field (default None).
    """

    id: str
    title: str | None
    type: str | None
    x: float
    y: float
    size: float = Field(
        default=1.0,
        description="BASE + GROWTH·sqrt(structural_degree); BASE=1.0, GROWTH=2.5 (ADR-0016 §2)",
    )
    degree: int = Field(
        default=0,
        description=(
            "Structural degree: count of distinct incident structural edges "
            "(direct-link or shared-source); drives size (ADR-0016 §2/§4)"
        ),
    )
    community: int = Field(
        default=-1,
        description=(
            "Louvain community id (G-P0-2, I2). Re-numbered by size (0 = largest). "
            "-1 when not yet assigned (first recompute pending)."
        ),
    )
    domain: str | None = Field(
        default=None,
        description=(
            "The node page's own dominant in-vocabulary domain (F18, ADR-0054 §2.2). "
            "Prefix 'domain/' is stripped. None when the page has no in-vocab domain tags. "
            "Tie-break: when ≥2 in-vocab domain tags are present, the one with the "
            "smallest index in the active vocabulary list wins (deterministic). "
            "Computed inside the graph recompute — no extra query (I1), cached with the "
            "snapshot (I2). Additive field — backward-compatible (default None)."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "00000000-0000-0000-0000-000000000001",
                "title": "Alpha",
                "type": "entity",
                "x": 1.23,
                "y": -0.45,
                "size": 2.1,
                "degree": 3,
                "community": 0,
                "domain": "SAM",
            }
        }
    }


class GraphEdgeResponse(BaseModel):
    """
    One graph edge in the GET /graph response (ADR-0014 §6, AC-F4-3, ADR-0016 §4).

    source/target are page-id strings (UUID). Undirected — emitted once per pair.
    kind: structural edge discriminator — "link" (wikilink exists) or "source"
          (shared-source provenance only). ADR-0016 §4.
    """

    source: str
    target: str
    weight: float
    kind: str = Field(
        default="link",
        description=(
            'Structural edge kind: "link" (direct wikilink) | '
            '"source" (shared provenance). ADR-0016 §4'
        ),
    )


class GraphCommunityTopPageResponse(BaseModel):
    """Highest-degree member page of a community — disambiguation + tooltip (F18)."""

    id: str = Field(description="Page UUID")
    title: str | None = Field(None, description="Page title")
    slug: str = Field(description="URL-safe slug derived from title")


class GraphCommunityResponse(BaseModel):
    """
    Per-community summary in the GET /graph response (G-P0-2, F18).

    id             : re-numbered Louvain community id (0 = largest community).
    size           : number of member nodes.
    cohesion       : intra-edge density in [0, 1]; 0 for singleton communities.
                     Low cohesion (<0.1) signals a loosely-connected community
                     suitable for a warning in the client legend.
    label          : human-readable display name for Community Mode (F18).
                     Priority: dominant_domain → top_page.title → "Comunità {id}".
                     Computed server-side in recompute() — no client computation (I2).
    dominant_domain: most-frequent in-vocab "domain/<Name>" tag among members (F18).
                     None when no valid domain tags are present.
    top_page       : highest-degree member page {id, title, slug} for disambiguation
                     and tooltip (F18). None for communities with no members.
    """

    id: int
    size: int
    cohesion: float = Field(description="Intra-edge density [0,1]; 0 for singletons")
    label: str = Field(
        default="",
        description=(
            "Human-readable community name for Community Mode (F18). "
            "Priority: dominant_domain → top_page.title → 'Comunità {id}'. "
            "Computed server-side alongside cohesion (I2)."
        ),
    )
    dominant_domain: str | None = Field(
        default=None,
        description=(
            "Most-frequent in-vocabulary domain/* tag among community members (F18). "
            "Prefix 'domain/' is stripped. None when no valid domain tags are present."
        ),
    )
    top_page: GraphCommunityTopPageResponse | None = Field(
        default=None,
        description=(
            "Highest-degree member page {id, title, slug} — used for label fallback "
            "and tooltip (F18). None when the community has no members."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": 0,
                "size": 12,
                "cohesion": 0.42,
                "label": "SAM",
                "dominant_domain": "SAM",
                "top_page": {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "title": "Software Asset Management",
                    "slug": "software-asset-management",
                },
            }
        }
    }


class GraphResponse(BaseModel):
    """
    GET /graph response payload (ADR-0014 §6, AC-F4-3, AC-D4v3-1).

    cached     : true on a HIT (no FA2 this request), false on a MISS (FA2 ran inline).
    communities: Louvain community summary list (G-P0-2); empty until first recompute.
    Header X-Graph-Cache: hit|miss mirrors cached (ADR-0014 §5).
    """

    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    data_version: int
    cached: bool
    communities: list[GraphCommunityResponse] = Field(
        default_factory=list,
        description=(
            "Per-community summary (G-P0-2, I2): id, size, cohesion. "
            "Ordered by id (0 = largest). Empty until first graph recompute."
        ),
    )
    total_nodes: int = Field(
        default=0,
        description=(
            "Count of ALL live (non-deleted) pages in the vault (the full denominator). "
            "Use len(nodes) for the in-graph numerator: e.g. '816/986 pages' (GR1). "
            "Computed via a bounded indexed COUNT query — I1."
        ),
    )
    total_edges: int = Field(
        default=0,
        description=(
            "Count of ALL link rows for this vault (the full wikilink denominator). "
            "Use len(edges) for the in-graph numerator: e.g. '1024/4213 edges' (GR1). "
            "Computed via a bounded indexed COUNT query — I1."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "nodes": [
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "title": "Alpha",
                        "type": "entity",
                        "x": 1.23,
                        "y": -0.45,
                        "size": 2.1,
                        "degree": 3,
                        "community": 0,
                        "domain": "SAM",
                    }
                ],
                "edges": [
                    {
                        "source": "00000000-0000-0000-0000-000000000001",
                        "target": "00000000-0000-0000-0000-000000000002",
                        "weight": 11.0,
                        "kind": "link",
                    }
                ],
                "data_version": 7,
                "cached": True,
                "communities": [
                    {
                        "id": 0,
                        "size": 2,
                        "cohesion": 1.0,
                        "label": "SAM",
                        "dominant_domain": "SAM",
                        "top_page": {
                            "id": "00000000-0000-0000-0000-000000000001",
                            "title": "Alpha",
                            "slug": "alpha",
                        },
                    }
                ],
                "total_nodes": 986,
                "total_edges": 4213,
            }
        }
    }


class RegenerateGraphResponse(BaseModel):
    """Response for POST /graph/recompute (the 'Regenerate graph' action)."""

    reconnected: int = Field(
        ..., description="Previously-dangling wikilinks reconnected to a live page this pass."
    )
    remaining_dangling: int = Field(
        ..., description="Wikilinks still dangling after the reconnect pass."
    )
    nodes: int = Field(..., description="Node count in the freshly recomputed snapshot.")
    edges: int = Field(..., description="Edge count in the freshly recomputed snapshot.")
    data_version: int = Field(..., description="data_version after the operation.")


@router.post(
    "/overview/regenerate",
    response_model=RegenerateOverviewResponse,
    status_code=200,
    summary="Regenerate the overview note in the vault language (F3)",
    description=(
        "Force a regeneration of the single auto-maintained overview.md note WITHOUT re-ingesting "
        "a source. Same seam as the per-ingest overview regen (nashsu/llm_wiki parity): resolves "
        "the ingest provider (I6), one bounded provider.chat() call (I7), overwrites overview.md "
        "with valid frontmatter (type: overview, I5), indexes it as a Page, and bumps data_version "
        "so the graph/tree refresh (I2). The language is detected from existing pages so the "
        "overview matches the vault content language (not defaulted to English). Degrade-safe: "
        "keeps the previous overview.md on provider failure/timeout."
    ),
    responses={200: {"description": "Overview regeneration attempted"}},
)
async def regenerate_overview() -> RegenerateOverviewResponse:
    """
    POST /overview/regenerate — manual overview refresh (F3).

    I6 — provider resolved via provider_config, never hardcoded. I7 — single bounded call.
    I1/I5 — reuses the shared overview write+index seam. I2 — bumps data_version + notify_bump.
    """
    from app.ingest.orchestrator import (
        OVERVIEW_REL_PATH,
        _detect_vault_language,
        _update_overview,
        bump_version,
    )

    overview_path = settings.vault_root / OVERVIEW_REL_PATH

    def _read_overview() -> str:
        try:
            return overview_path.read_text(encoding="utf-8")
        except OSError:
            return ""

    # Compare the overview.md content before/after: a degrade-safe keep-previous leaves it
    # unchanged, a successful regen rewrites it.
    before = _read_overview()
    # Report the language actually used: the OVERVIEW_LANGUAGE override wins, else auto-detect.
    detected = (
        effective_str("overview_language", settings.overview_language)
        or await _detect_vault_language()
    )
    # analysis=None → _update_overview detects the vault language internally (delegated-style).
    await _update_overview(None, "(manual regenerate)")
    after = _read_overview()

    regenerated = after != before
    if regenerated:
        await bump_version()
        if _m._graph_cache is not None:
            async with _m.get_session() as _vs_sess:
                _vs_row = await _vs_sess.execute(
                    select(VaultState).where(VaultState.vault_id == settings.vault_id)
                )
                _vs = _vs_row.scalar_one_or_none()
                _new_version = _vs.data_version if _vs is not None else 0
            _m._graph_cache.notify_bump(_new_version)

    return RegenerateOverviewResponse(regenerated=regenerated, detected_language=detected)


@router.post(
    "/links/reresolve",
    response_model=ReresolveLinksResponse,
    status_code=200,
    summary="Re-resolve dangling wikilinks against current pages (F3/K3)",
    description=(
        "Backfill for cross-ingest graph connectivity: re-resolves every dangling Link against "
        "the current live pages using the tolerant matcher (exact → case-insensitive → slug). "
        "Links whose target now matches a live page are reconnected (target_page_id set, "
        "dangling cleared). Bounded single pass (I7 — two queries, no per-row round-trips). "
        "Bumps data_version once so the debounced GraphCache recomputes with the new edges (I2). "
        "Returns {reconnected, remaining_dangling}."
    ),
    responses={200: {"description": "Backfill completed"}},
)
async def reresolve_links() -> ReresolveLinksResponse:
    """
    POST /links/reresolve — reconnect historical dangling wikilinks (F3/K3).

    Invariant compliance:
      I1 — incremental: only touches Link rows that resolve; no page rescan.
      I2 — bumps data_version + notify_bump once so FA2 recomputes with the new edges.
      I6 — NO provider call; pure DB resolution.
      I7 — single bounded pass, no loop.
    """
    from sqlalchemy import func

    from app.ingest.orchestrator import bump_version
    from app.models import Link
    from app.wiki.links import reresolve_dangling_links

    async with _m.get_session() as session:
        reconnected = await reresolve_dangling_links(session)
        # Flush the in-place dangling→resolved mutations so the count below reflects them
        # (the session has autoflush off, so an unflushed UPDATE would otherwise be invisible
        # and remaining_dangling would wrongly report the pre-reresolve total).
        await session.flush()
        remaining_row = await session.execute(
            select(func.count()).select_from(Link).where(Link.dangling.is_(True))
        )
        remaining_dangling = int(remaining_row.scalar_one() or 0)
        # Commit the reconnected rows before bumping the graph (session commits on exit).

    # Only bump the graph when something actually changed (avoid a needless FA2 recompute, I2).
    if reconnected:
        await bump_version()
        if _m._graph_cache is not None:
            async with _m.get_session() as _vs_sess:
                _vs_row = await _vs_sess.execute(
                    select(VaultState).where(VaultState.vault_id == settings.vault_id)
                )
                _vs = _vs_row.scalar_one_or_none()
                _new_version = _vs.data_version if _vs is not None else 0
            _m._graph_cache.notify_bump(_new_version)

    return ReresolveLinksResponse(
        reconnected=reconnected,
        remaining_dangling=remaining_dangling,
    )


@router.get(
    "/graph",
    response_model=GraphResponse,
    summary="Precomputed knowledge graph (nodes + edges with FA2 coordinates)",
    description=(
        "Returns the precomputed graph with FA2 layout coordinates (I2, F4, ADR-0014). "
        "HIT (X-Graph-Cache: hit): pure read from persisted coords + edges — no FA2. "
        "MISS (X-Graph-Cache: miss): one inline synchronous recompute, then return. "
        "Synchronous 200 — never 202 (AQ-v0.3-3). "
        "A second request at the same data_version is always a HIT (G2)."
    ),
    responses={
        200: {
            "description": "Graph payload with precomputed coords",
            "headers": {
                "X-Graph-Cache": {
                    "description": "hit|miss — mirrors the cached field (ADR-0014 §5)",
                    "schema": {"type": "string"},
                }
            },
        }
    },
)
async def get_graph() -> Response:
    """
    GET /graph — precomputed knowledge graph with FA2 layout coords (F4, I2, ADR-0014).

    I2 compliance:
      - HIT path: pure read, no FA2 (X-Graph-Cache: hit).
      - MISS path: one inline synchronous recompute (X-Graph-Cache: miss).
      - The background debounce (GraphCache) keeps the common case a HIT.
      - Coords are precomputed server-side via igraph (R9, I9) — never on the client.
    """
    # Read the current data_version (lightweight SELECT)
    async with _m.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        current_version: int = state.data_version if state is not None else 0

        # ── GR1 vault-wide totals — bounded indexed COUNT queries (I1) ─────────
        # total_nodes: all live (non-deleted) pages in this vault
        _pages_count_row = await session.execute(
            select(func.count())
            .select_from(Page)
            .where(Page.vault_id == settings.vault_id, Page.deleted_at.is_(None))
        )
        total_nodes: int = int(_pages_count_row.scalar_one() or 0)

        # total_edges: all link rows whose source page belongs to this vault
        # (Link has no direct vault_id; join to pages on source_page_id — indexed FK)
        _links_count_row = await session.execute(
            select(func.count())
            .select_from(Link)
            .join(Page, Link.source_page_id == Page.id)
            .where(Page.vault_id == settings.vault_id)
        )
        total_edges: int = int(_links_count_row.scalar_one() or 0)

    # Initialise cache lazily (e.g. in test environments that bypass lifespan)
    if _m._graph_cache is None:
        _m._graph_cache = GraphCache(
            engine=GraphEngine(),
            vault_id=settings.vault_id,
        )

    snapshot, cached = await _m._graph_cache.get_graph(current_version)

    # Build response payload (ADR-0014 §6, G-P0-2)
    nodes: list[GraphNodeResponse] = [
        GraphNodeResponse(
            id=n.id,
            title=n.title,
            type=n.page_type,
            x=n.x,
            y=n.y,
            size=n.size,
            degree=n.degree,
            community=n.community,
            domain=n.domain,
        )
        for n in snapshot.nodes
    ]
    edges: list[GraphEdgeResponse] = [
        GraphEdgeResponse(source=e.source, target=e.target, weight=e.weight, kind=e.kind)
        for e in snapshot.edges
    ]
    communities: list[GraphCommunityResponse] = [
        GraphCommunityResponse(
            id=c.id,
            size=c.size,
            cohesion=c.cohesion,
            label=c.label,
            dominant_domain=c.dominant_domain,
            top_page=(
                GraphCommunityTopPageResponse(
                    id=c.top_page.id,
                    title=c.top_page.title,
                    slug=c.top_page.slug,
                )
                if c.top_page is not None
                else None
            ),
        )
        for c in snapshot.communities
    ]
    payload = GraphResponse(
        nodes=nodes,
        edges=edges,
        data_version=current_version,
        cached=cached,
        communities=communities,
        total_nodes=total_nodes,
        total_edges=total_edges,
    )

    cache_header = "hit" if cached else "miss"
    return Response(
        content=payload.model_dump_json(),
        media_type="application/json",
        headers={"X-Graph-Cache": cache_header},
    )


@router.post(
    "/graph/recompute",
    response_model=RegenerateGraphResponse,
    status_code=200,
    summary="Regenerate the graph: reconnect cross-ingest links + force FA2 recompute (F4/I2)",
    description=(
        "The 'Regenerate graph' action. Two bounded steps: "
        "(1) re-resolve dangling [[wikilinks]] against current pages (tolerant matcher: "
        "exact → case-insensitive → slug) so ingests link to each other; "
        "(2) FORCE a fresh server-side ForceAtlas2 recompute (I2) — invalidating the cache "
        "marker so the layout re-runs even when data_version has not changed. The recompute "
        "applies the ADR-0045 §5 outlier clamp, which tames FA2 runaway nodes that would "
        "otherwise collapse the dense core in the viewer. "
        "Layout stays server-side (I2 — never on the client); the recompute is a single inline "
        "FA2 run under the cache in-flight guard (no concurrent FA2). "
        "Returns reconnected/remaining_dangling counts + fresh node/edge counts."
    ),
    responses={200: {"description": "Links reconnected + graph recomputed"}},
)
async def recompute_graph() -> RegenerateGraphResponse:
    """
    POST /graph/recompute — reconnect dangling links + force an FA2 layout recompute (F4/I2).

    Invariant compliance:
      I1 — reresolve only touches Link rows that resolve; no page rescan.
      I2 — layout runs server-side; force_recompute uses the shared in-flight guard (no
           concurrent FA2). Bumps data_version once IFF links were reconnected.
      I6 — no InferenceProvider call.
      I7 — reresolve is a single bounded pass; recompute is one bounded FA2 run.
    """
    from sqlalchemy import func

    from app.ingest.orchestrator import bump_version
    from app.models import Link
    from app.wiki.links import reresolve_dangling_links

    # ── Step 1: reconnect dangling wikilinks (K3/F3 cross-ingest connectivity) ──
    async with _m.get_session() as session:
        reconnected = await reresolve_dangling_links(session)
        await session.flush()
        remaining_row = await session.execute(
            select(func.count()).select_from(Link).where(Link.dangling.is_(True))
        )
        remaining_dangling = int(remaining_row.scalar_one() or 0)

    # Bump data_version once if links changed (so any HIT elsewhere invalidates too).
    if reconnected:
        await bump_version()

    # ── Read the (possibly bumped) current data_version ──────────────────────
    async with _m.get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        current_version = state.data_version if state is not None else 0

    # Initialise cache lazily (e.g. in test environments that bypass lifespan)
    if _m._graph_cache is None:
        _m._graph_cache = GraphCache(engine=GraphEngine(), vault_id=settings.vault_id)

    # ── Step 2: FORCE a fresh FA2 recompute (applies the outlier clamp) ──────
    snapshot = await _m._graph_cache.force_recompute(current_version)

    return RegenerateGraphResponse(
        reconnected=reconnected,
        remaining_dangling=remaining_dangling,
        nodes=len(snapshot.nodes),
        edges=len(snapshot.edges),
        data_version=current_version,
    )


# ── GET /graph/communities/{community_id} (R9-5, AC-R9-5-1) ──────────────────


class GraphCommunityMemberResponse(BaseModel):
    """One member page in a community drill-down response (R9-5)."""

    id: str = Field(..., description="Page UUID")
    title: str | None = Field(None, description="Page title (may be None if not yet set)")
    page_type: str | None = Field(None, description="Frontmatter type (entity/concept/etc.)")
    degree: int = Field(0, description="Structural degree within the full graph")

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "title": "Alpha",
                "page_type": "entity",
                "degree": 4,
            }
        }
    }


class GraphCommunityDetailResponse(BaseModel):
    """
    Response for GET /graph/communities/{community_id} (R9-5, AC-R9-5-1).

    Reads from the cached GraphSnapshot — never triggers a recompute (I2).
    Returns 409 when the snapshot is cold (cache has never run).

    cohesion formula:  cohesion = intraEdges / (size*(size-1)/2)
      intraEdges = number of edges in the snapshot whose both endpoints belong to
                   this community.  possibleEdges = size*(size-1)/2.
      cohesion in [0,1].  Singletons (size=1) -> cohesion=0, no possible edges.
      cohesion_warning = True when cohesion < GRAPH_COHESION_WARN (default 0.2, env
      GRAPH_COHESION_WARN).  See engine.py CommunitySnapshot for the per-recompute
      computation; this endpoint reads the value stored in the snapshot (no re-derive).
    """

    community_id: int = Field(..., description="Louvain community id (0 = largest)")
    size: int = Field(..., description="Number of member pages in this community")
    cohesion: float | None = Field(
        None,
        description=(
            "Intra-edge density [0,1]: intraEdges / (size*(size-1)/2). "
            "0 for singletons (no possible edges). Null until first recompute."
        ),
    )
    cohesion_warning: bool = Field(
        False,
        description=(
            "True when cohesion < GRAPH_COHESION_WARN (default 0.2). "
            "Signals a loosely-connected / potentially fragmented community."
        ),
    )
    members: list[GraphCommunityMemberResponse] = Field(
        default_factory=list,
        description=(
            "Member pages ordered by degree descending, capped at 100. "
            "Each entry carries id, title, page_type, degree."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "community_id": 0,
                "size": 5,
                "cohesion": 0.6,
                "cohesion_warning": False,
                "members": [
                    {
                        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "title": "Alpha",
                        "page_type": "entity",
                        "degree": 4,
                    }
                ],
            }
        }
    }


@router.get(
    "/graph/communities/{community_id}",
    response_model=GraphCommunityDetailResponse,
    summary="Graph community drill-down: members + cohesion score (R9-5)",
    description=(
        "Returns member pages and cohesion for a Louvain community (R9-5, AC-R9-5-1). "
        "Data is read from the cached GraphSnapshot — NEVER triggers a graph recompute (I2). "
        "If the cache is cold (graph has never been computed), returns 409 with a clear message; "
        "the client should trigger POST /graph/recompute first. "
        "Members are ordered by degree descending, capped at 100 entries. "
        "cohesion_warning=true when cohesion < GRAPH_COHESION_WARN (default 0.2, env "
        "GRAPH_COHESION_WARN). "
        "Returns 404 if the community_id does not exist in the current snapshot."
    ),
    responses={
        200: {"description": "Community detail with members and cohesion"},
        404: {"description": "Community id not found in the current snapshot"},
        409: {
            "description": (
                "Graph snapshot is cold — no recompute has run yet. "
                "Call POST /graph/recompute first."
            )
        },
    },
)
async def get_graph_community(community_id: int) -> GraphCommunityDetailResponse:
    """
    GET /graph/communities/{community_id} — community drill-down (R9-5, AC-R9-5-1/5).

    I2 compliance: reads ONLY from _m._graph_cache._snapshot (the last stored result of
    a previous recompute). Does NOT call get_graph() / force_recompute() / recompute().
    Cold cache -> 409 (client must call POST /graph/recompute first).
    """
    # I2 guard: read directly from the in-memory snapshot, never call recompute.
    snapshot: GraphSnapshot | None = (
        _m._graph_cache._snapshot if _m._graph_cache is not None else None
    )
    if snapshot is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Il grafo non e ancora stato calcolato. "
                "Eseguire prima POST /graph/recompute. "
                "(Graph snapshot is cold — run POST /graph/recompute first.)"
            ),
        )

    # Find the community summary in the snapshot for cohesion
    community_snap = next((c for c in snapshot.communities if c.id == community_id), None)
    if community_snap is None:
        # Also check if any node belongs to this community_id (handles edge case
        # where communities list was not populated but nodes were assigned).
        has_members = any(n.community == community_id for n in snapshot.nodes)
        if not has_members:
            raise HTTPException(
                status_code=404,
                detail=f"Community {community_id} not found in the current graph snapshot.",
            )
        # Community exists but no summary (shouldn't happen post-recompute; degrade)
        cohesion_val: float | None = None
    else:
        cohesion_val = community_snap.cohesion

    # Build member list: all nodes with this community id, sorted by degree desc, cap 100
    members_raw = [n for n in snapshot.nodes if n.community == community_id]
    members_raw.sort(key=lambda n: n.degree, reverse=True)
    members_raw = members_raw[:100]

    members = [
        GraphCommunityMemberResponse(
            id=n.id,
            title=n.title,
            page_type=n.page_type,
            degree=n.degree,
        )
        for n in members_raw
    ]

    warn_threshold: float = settings.graph_cohesion_warn
    cohesion_warning = cohesion_val is not None and cohesion_val < warn_threshold

    return GraphCommunityDetailResponse(
        community_id=community_id,
        size=len(members_raw),
        cohesion=cohesion_val,
        cohesion_warning=cohesion_warning,
        members=members,
    )


# ── GET /graph/edges/{source_id}/{target_id} (R9-5, AC-R9-5-4) ────────────────


class GraphEdgeSignalsResponse(BaseModel):
    """
    4-signal weight breakdown for a single graph edge (R9-5, AC-R9-5-4).

    Reads from the persisted edges.signals JSONB column (populated by
    GraphEngine.recompute() at every FA2 run).  Never triggers a recompute (I2).

    signal semantics (ADR-0012):
      direct_links   = 3.0 * direct_link_count   (wikilink coefficient x3)
      shared_sources = 4.0 * shared_source_count  (provenance coefficient x4)
      adamic_adar    = 1.5 * AA(A,B)             (AA coefficient x1.5)
      type_affinity  = type_affinity_matrix(A,B)  (type-affinity, x1 -- see engine.py)
      total weight   = sum of all four signals
    """

    weight: float = Field(..., description="Total 4-signal edge weight (ADR-0012)")
    breakdown: dict[str, float] = Field(
        ...,
        description=(
            "Per-signal weight components: "
            "{direct_links, shared_sources, adamic_adar, type_affinity}. "
            "direct_links = 3*direct_count; shared_sources = 4*shared_count; "
            "adamic_adar = 1.5*AA(A,B); type_affinity = matrix(typeA,typeB)."
        ),
    )
    computed_at: str | None = Field(
        None,
        description="ISO datetime when this edge row was last persisted by the graph engine.",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "weight": 11.2,
                "breakdown": {
                    "direct_links": 6.0,
                    "shared_sources": 4.0,
                    "adamic_adar": 0.7,
                    "type_affinity": 0.5,
                },
                "computed_at": "2026-07-01T12:00:00Z",
            }
        }
    }


@router.get(
    "/graph/edges/{source_id}/{target_id}",
    response_model=GraphEdgeSignalsResponse,
    summary="4-signal edge weight breakdown between two pages (R9-5)",
    description=(
        "Returns the 4-signal edge weight breakdown for the undirected pair "
        "(source_id, target_id) (R9-5, AC-R9-5-4). "
        "Reads from the persisted edges table (signals JSONB column) — no graph recompute (I2). "
        "The pair is matched undirectionally (either endpoint order accepted). "
        "breakdown keys: direct_links (3*direct_count), shared_sources (4*shared_count), "
        "adamic_adar (1.5*AA), type_affinity (matrix value). "
        "Returns 404 if no edge exists for the pair in the current vault."
    ),
    responses={
        200: {"description": "Edge weight breakdown (4 signals)"},
        404: {"description": "No edge found for this pair in the persisted edges table"},
    },
)
async def get_graph_edge(source_id: str, target_id: str) -> GraphEdgeSignalsResponse:
    """
    GET /graph/edges/{source_id}/{target_id} — 4-signal edge breakdown (R9-5, AC-R9-5-4/5).

    I2 compliance: reads edges table only (persisted at recompute time). No FA2.
    Undirected: matches (source_id,target_id) OR (target_id,source_id).
    """

    _EDGE_QUERY = (
        "SELECT weight, signals, created_at "
        "FROM edges "
        "WHERE vault_id = :vid "
        "  AND ("
        "    (CAST(source_page_id AS TEXT) = :src AND CAST(target_page_id AS TEXT) = :tgt)"
        "    OR"
        "    (CAST(source_page_id AS TEXT) = :tgt AND CAST(target_page_id AS TEXT) = :src)"
        "  ) "
        "LIMIT 1"
    )

    async with _m.get_session() as session:
        result = await session.execute(
            sa_text(_EDGE_QUERY).bindparams(
                vid=settings.vault_id,
                src=source_id,
                tgt=target_id,
            )
        )
        row = result.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No edge found between {source_id!r} and {target_id!r} "
                "in the persisted edges table. "
                "The graph may need a recompute (POST /graph/recompute)."
            ),
        )

    import json as _json

    weight: float = float(row[0])
    _raw = row[1]  # JSONB from DB — asyncpg returns dict; aiosqlite returns str
    # Normalise: parse string representation (SQLite test path); pass dict through (Postgres)
    raw_signals: dict[str, float] | None
    if isinstance(_raw, str):
        try:
            raw_signals = _json.loads(_raw)
        except Exception:
            raw_signals = None
    elif isinstance(_raw, dict):
        raw_signals = _raw
    else:
        raw_signals = None
    created_at_raw = row[2]

    # Map internal signal keys {direct, source, aa, type} -> public names
    # (stored by engine.py with short keys; we expose descriptive names for the API)
    breakdown: dict[str, float]
    if raw_signals:
        breakdown = {
            "direct_links": float(raw_signals.get("direct", 0.0)),
            "shared_sources": float(raw_signals.get("source", 0.0)),
            "adamic_adar": float(raw_signals.get("aa", 0.0)),
            "type_affinity": float(raw_signals.get("type", 0.0)),
        }
    else:
        # signals column is NULL (edge written before migration that added signals)
        # reconstruct from weight alone — partial data only
        breakdown = {
            "direct_links": 0.0,
            "shared_sources": 0.0,
            "adamic_adar": 0.0,
            "type_affinity": 0.0,
        }

    computed_at_str: str | None = None
    if created_at_raw is not None:
        try:
            computed_at_str = (
                created_at_raw.isoformat()
                if hasattr(created_at_raw, "isoformat")
                else str(created_at_raw)
            )
        except Exception:
            computed_at_str = str(created_at_raw)

    return GraphEdgeSignalsResponse(
        weight=weight,
        breakdown=breakdown,
        computed_at=computed_at_str,
    )
