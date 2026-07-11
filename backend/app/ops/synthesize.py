"""
Corpus-level synthesis/comparison generator (ADR-0067 D3 · audit P0-3 / SC-D1).

Synapse copied llm_wiki's ingest-time PROHIBITION on emitting synthesis/comparison pages
(``app/ingest/provider/_common.py::GENERATION_SCAFFOLD``) — the correct parity choice — but never
built the compensating generator that llm_wiki runs *after* a bulk import. The result: prod has
0 synthesis + 0 comparison pages vs llm_wiki's 4 + 5. This module is that missing generator.

The ingest-time prohibition STAYS. This op is the sanctioned exception (ADR-0067 D3): the shared
``orchestrator.write_wiki_page`` seam accepts ``PageType.SYNTHESIS`` / ``PageType.COMPARISON``
directly (the ban lives only in the ingest generation prompt/loop). These are legitimate
corpus-level writers, distinct from single-doc ingest.

Architecture MIRRORS ``ops/reclassify_types.py`` exactly (single-flight state, ``clamp_bounds``,
``max_iter``/``token_budget`` bounds, ``total_cost_usd`` logging, one incremental version bump per
written page owned by the write seam):

  I1  — INCREMENTAL: reads only ``pages`` + ``links`` (indexed), writes via the single
        ``write_wiki_page`` seam; never walks the vault or full-rescans. Each written page's
        ``data_version`` bump is owned by ``write_wiki_page`` (the op adds NO extra bump — same
        convention as the review Create path). A run that only proposes to Review bumps nothing.
  I6  — PLUGGABLE: the provider is resolved via ``resolve_provider_config('ingest', vault_id)``;
        NO hardcoded backend, NO ``isinstance``/class-name branching. Provider absent → clean
        no-op (``stopped_reason='no_provider'``).
  I7  — BOUNDED: ``max_pages`` caps written pages/run; ``token_budget`` caps provider spend; ONE
        bounded ``provider.chat`` call per accepted cluster; ``total_cost_usd`` logged + $1
        anomaly check.

Two-band gate (ADR-0067 D3 / P0-3):
  * candidate clusters are seeded DETERMINISTICALLY from the 4-signal graph (source-overlap ×4 is
    the dominant signal; type-affinity — reused from ``graph/engine.py`` — modulates confidence;
    Adamic-Adar is approximated by shared-source co-citation degree). The LLM never picks clusters.
  * confidence ≥ ``AUTO_CONFIDENCE_THRESHOLD``  → AUTO-WRITE one page (I7 bounded provider call).
  * ``REVIEW_CONFIDENCE_FLOOR`` ≤ conf < auto   → PROPOSE to the F9 review queue (no provider
    call, no page) with the right ``proposed_page_type`` (SC-D3).
  * conf < floor                                → skip (defensive lower bound).

Cluster shapes:
  * SYNTHESIS  — a neighbourhood of ≥ ``MIN_SYNTHESIS_CLUSTER`` same-domain entity/concept pages
    with high shared-sources → one cross-cutting thesis + integration page ([[wikilinks]] to the
    members).
  * COMPARISON — ≥ ``MIN_COMPARISON_ENTITIES`` same-class entities that are frequently co-cited
    (share sources) → one side-by-side markdown table.

This module owns ONLY the run function + single-flight state + the deterministic cluster heuristic.
It registers NO endpoint (the backend-owned ``POST /ops/synthesize`` lives in routers/ops.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.db import get_session
from app.ingest.provider import resolve_provider
from app.ingest.provider.base import InferenceProvider, UsageAccumulator
from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

logger = logging.getLogger(__name__)

# $1 cost-anomaly threshold — same as the ingest / domain-backfill / reclassify paths (ADR-0009 §3).
_COST_ANOMALY_THRESHOLD_USD = 1.00

# ── Bounds defaults (I7). max_pages caps AUTO-WRITTEN pages per run (synthesis+comparison). ──
# Synthesis/comparison pages are FEW by nature (llm_wiki: 4 + 5), so the hard cap is small.
DEFAULT_MAX_PAGES = 12
MAX_PAGES_HARD_CAP = 100
DEFAULT_TOKEN_BUDGET = 60_000

# ── Cluster-seeding heuristic constants (deterministic; from the 4-signal graph) ──────────────
# Source-overlap (the ×4 signal in graph/engine.py) is the DOMINANT seeding signal.
MIN_SYNTHESIS_CLUSTER = 3  # ≥3 same-domain pages → a cross-cutting synthesis
MIN_COMPARISON_ENTITIES = 2  # ≥2 co-cited same-class entities → a comparison table
MAX_COMPARISON_ENTITIES = 4  # cap the comparison group (table columns)
MIN_SHARED_SOURCES = 2  # two pages "co-cited" iff they share ≥ this many source docs

# Confidence model (documented in _cluster_confidence). Tuned so a cluster whose members pairwise
# share ≥3 sources auto-writes, and one sharing exactly the minimum (2) lands in the Review band.
_SHARED_SATURATION = 4.0  # avg shared-sources at which the strength term saturates to 1.0
_SIZE_SATURATION = 4.0  # cluster size at which the size term saturates to 1.0
_CONF_W_SHARED = 0.7  # weight of shared-source strength
_CONF_W_SIZE = 0.3  # weight of cluster size
AUTO_CONFIDENCE_THRESHOLD = 0.6  # ≥ → auto-write
REVIEW_CONFIDENCE_FLOOR = 0.35  # [floor, auto) → Review proposal (F9); < floor → skip

# Types eligible to seed clusters (never source/query/overview/index/log/synthesis/comparison).
_SYNTHESIS_MEMBER_TYPES: frozenset[str] = frozenset({PageType.ENTITY.value, PageType.CONCEPT.value})
_COMPARISON_MEMBER_TYPES: frozenset[str] = frozenset({PageType.ENTITY.value})

_DOMAIN_PREFIX = "domain/"


# ── Result + single-flight state ──────────────────────────────────────────────


@dataclass
class SynthesizeSummary:
    """Outcome of one corpus synthesis/comparison run (completion log line)."""

    candidates: int = 0  # clusters seeded from the graph
    processed: int = 0  # clusters attempted via a provider call (auto-write band)
    synthesis_written: int = 0
    comparison_written: int = 0
    proposed: int = 0  # clusters routed to the Review queue (F9) instead of auto-written
    skipped: int = 0  # below the review floor, or a proposal that could not be enqueued
    failed: int = 0  # provider/parse/write failure on an auto-write cluster
    total_cost_usd: float = 0.0
    stopped_reason: str = "complete"  # complete | budget | maxpages | no_provider | error
    max_pages: int = 0
    token_budget: int = 0
    force: bool = False

    @property
    def pages_written(self) -> int:
        return self.synthesis_written + self.comparison_written

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidates": self.candidates,
            "processed": self.processed,
            "synthesis_written": self.synthesis_written,
            "comparison_written": self.comparison_written,
            "pages_written": self.pages_written,
            "proposed": self.proposed,
            "skipped": self.skipped,
            "failed": self.failed,
            "total_cost_usd": round(self.total_cost_usd, 4),
            "stopped_reason": self.stopped_reason,
            "max_pages": self.max_pages,
            "token_budget": self.token_budget,
            "force": self.force,
        }


@dataclass
class _SynthesizeState:
    """Module-level single-flight state (read by the endpoint to 409 / report)."""

    is_running: bool = False
    last_summary: SynthesizeSummary | None = None
    current: dict[str, Any] = field(default_factory=dict)


_state = _SynthesizeState()


def is_running() -> bool:
    """True if a synthesize run is currently in flight (single-flight guard)."""
    return _state.is_running


def get_last_summary() -> SynthesizeSummary | None:
    """Return the summary of the most recently COMPLETED synthesize run (None if never run)."""
    return _state.last_summary


def clamp_bounds(max_pages: int | None, token_budget: int | None) -> tuple[int, int]:
    """
    Freeze + clamp the run bounds (I7). ``None`` → settings/module default; a value over the hard
    cap is clamped (never exceeded). Returns ``(max_pages, token_budget)``.
    """
    default_max = int(getattr(settings, "synthesize_max_pages", DEFAULT_MAX_PAGES))
    default_budget = int(getattr(settings, "synthesize_token_budget", DEFAULT_TOKEN_BUDGET))
    mp = default_max if max_pages is None else int(max_pages)
    mp = max(1, min(mp, MAX_PAGES_HARD_CAP))
    tb = default_budget if token_budget is None else int(token_budget)
    tb = max(1, tb)
    return mp, tb


# ── Candidate cluster ─────────────────────────────────────────────────────────


@dataclass
class Cluster:
    """One deterministic candidate cluster seeded from the 4-signal graph."""

    kind: str  # "synthesis" | "comparison"
    page_ids: list[str]  # member page ids (str UUIDs), sorted by slug
    slugs: list[str]  # member page slugs → frontmatter.related seed
    titles: list[str]  # member page titles
    sources: list[str]  # UNION of member sources → DB pages.sources (F3)
    domain: str | None  # dominant in-vocab domain of the cluster (or None)
    confidence: float  # [0,1] — gate between auto-write / Review / skip


# ── Run ────────────────────────────────────────────────────────────────────────


async def run_synthesize(
    *,
    vault_id: str,
    max_pages: int | None = None,
    token_budget: int | None = None,
    force: bool = False,
) -> SynthesizeSummary:
    """
    Run ONE bounded corpus-level synthesis/comparison pass (ADR-0067 D3). Sets the single-flight
    flag for its whole duration; a concurrent call while :func:`is_running` should be rejected by
    the endpoint (409).

    Resolves the ingest provider (I6 — no hardcoded backend), loads the schema.md/purpose.md
    vault-context once, seeds candidate clusters deterministically from the graph, then processes
    the bounded set. Never raises — a fatal error is recorded as ``stopped_reason="error"``.
    """
    from app.ingest.orchestrator import _load_vault_context  # noqa: PLC0415

    mp, tb = clamp_bounds(max_pages, token_budget)
    summary = SynthesizeSummary(max_pages=mp, token_budget=tb, force=force)

    _state.is_running = True
    _state.current = {"max_pages": mp, "token_budget": tb, "force": force}
    try:
        resolved = await _resolve_provider(vault_id)
        if resolved is None:
            # I6: no provider configured → clean no-op (never a silent default, never an error).
            summary.stopped_reason = "no_provider"
            logger.info(
                "synthesize: no ingest provider resolved (vault=%s) — clean no-op (I6)", vault_id
            )
            _state.last_summary = summary
            return summary
        provider, _config_row = resolved

        vault_context = _load_vault_context()

        accumulator = UsageAccumulator()
        provider.bind_accumulator(accumulator)

        await _run_inner(
            vault_id=vault_id,
            provider=provider,
            vault_context=vault_context,
            max_pages=mp,
            token_budget=tb,
            force=force,
            accumulator=accumulator,
            summary=summary,
        )
        summary.total_cost_usd = round(accumulator.total_cost_usd, 4)
    except Exception as exc:  # noqa: BLE001 — never propagate into the background task
        summary.stopped_reason = "error"
        logger.warning("synthesize: run failed (vault=%s): %s", vault_id, exc)
    finally:
        _state.is_running = False
        _state.last_summary = summary
        _state.current = {}

    logger.info(
        "synthesize: candidates=%d processed=%d synthesis=%d comparison=%d proposed=%d "
        "skipped=%d failed=%d cost_usd=%.4f stopped_reason=%s vault=%s",
        summary.candidates,
        summary.processed,
        summary.synthesis_written,
        summary.comparison_written,
        summary.proposed,
        summary.skipped,
        summary.failed,
        summary.total_cost_usd,
        summary.stopped_reason,
        vault_id,
    )
    if summary.total_cost_usd > _COST_ANOMALY_THRESHOLD_USD:
        logger.warning(
            "COST ANOMALY: synthesize total_cost_usd=%.4f exceeds $%.2f (vault=%s) — "
            "investigate runaway/misconfiguration",
            summary.total_cost_usd,
            _COST_ANOMALY_THRESHOLD_USD,
            vault_id,
        )
    return summary


async def _run_inner(
    *,
    vault_id: str,
    provider: InferenceProvider,
    vault_context: str,
    max_pages: int,
    token_budget: int,
    force: bool,
    accumulator: UsageAccumulator,
    summary: SynthesizeSummary,
) -> None:
    """
    Bounded per-cluster loop (I7). One provider call per AUTO-WRITE cluster; Review proposals are
    provider-free. No explicit ``data_version`` bump — ``write_wiki_page`` owns the per-page bump
    (I1), matching the review Create path.
    """
    auto_threshold = float(
        getattr(settings, "synthesize_auto_confidence", AUTO_CONFIDENCE_THRESHOLD)
    )
    review_floor = float(getattr(settings, "synthesize_review_floor", REVIEW_CONFIDENCE_FLOOR))

    clusters = await _seed_candidates(vault_id, force)
    summary.candidates = len(clusters)

    for cluster in clusters:
        # ── page cap (I7) — caps AUTO-WRITTEN pages, not free Review proposals ──────
        if summary.pages_written >= max_pages:
            summary.stopped_reason = "maxpages"
            break
        # ── budget gate BEFORE spending on this cluster (I7) ────────────────────────
        if accumulator.total_tokens >= token_budget:
            summary.stopped_reason = "budget"
            break

        conf = cluster.confidence
        if conf < review_floor:
            # Below the defensive floor → skip (never write, never spam Review).
            summary.skipped += 1
            continue

        if conf < auto_threshold:
            # ── Borderline → PROPOSE to the F9 review queue (SC-D3) — no provider call ──
            item = await _propose_cluster_review(vault_id, cluster)
            if item is not None:
                summary.proposed += 1
            else:
                summary.skipped += 1
            continue

        # ── High-confidence → AUTO-WRITE — ONE bounded provider call (I6/I7) ─────────
        summary.processed += 1
        try:
            generated = await _generate_cluster_body(provider, cluster, vault_context)
        except Exception as exc:  # noqa: BLE001 — per-cluster non-fatal
            summary.failed += 1
            logger.warning("synthesize: generation failed for %s cluster: %s", cluster.kind, exc)
            continue
        if generated is None:
            summary.failed += 1
            logger.debug("synthesize: %s cluster produced no usable body (skipped)", cluster.kind)
            continue
        title, body = generated
        try:
            await _write_cluster_page(cluster, title, body)
        except Exception as exc:  # noqa: BLE001 — per-cluster non-fatal
            summary.failed += 1
            logger.warning("synthesize: write failed for %s %r: %s", cluster.kind, title, exc)
            continue
        if cluster.kind == "synthesis":
            summary.synthesis_written += 1
        else:
            summary.comparison_written += 1
        logger.debug(
            "synthesize: wrote %s page %r (%d members)", cluster.kind, title, len(cluster.slugs)
        )


# ── Candidate seeding (deterministic — reuses the 4-signal graph) ──────────────


async def _seed_candidates(vault_id: str, force: bool) -> list[Cluster]:
    """
    Seed candidate clusters DETERMINISTICALLY from the 4-signal graph (I1 — indexed reads only).

    Loads live entity/concept pages (+ their sources/tags) and resolved wikilinks, then runs the
    pure :func:`_build_clusters` heuristic. ``force`` is accepted for endpoint-shape parity (a full
    re-seed is already deterministic — the seeder holds no cross-run state).
    """
    pages, links = await _load_graph_data(vault_id)
    return _build_clusters(pages, links)


async def _load_graph_data(
    vault_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Bounded indexed read of graph-eligible pages + resolved links (I1 — no vault walk).

    Pages: live wiki entity/concept pages (id, title, page_type, file_path, sources, tags).
    Links: resolved wikilinks (dangling=False, target_page_id NOT NULL) — the co-reference seed.
    Portable SQLAlchemy Core/ORM selects (SQLite tests stub this seam; Postgres runs it live).
    """
    from sqlalchemy import select

    from app.models import Link, Page

    member_types = tuple(sorted(_SYNTHESIS_MEMBER_TYPES | _COMPARISON_MEMBER_TYPES))
    async with get_session() as session:
        page_rows = list(
            (
                await session.execute(
                    select(
                        Page.id,
                        Page.title,
                        Page.page_type,
                        Page.file_path,
                        Page.sources,
                        Page.tags,
                    ).where(
                        Page.vault_id == vault_id,
                        Page.deleted_at.is_(None),
                        Page.file_path.like("wiki/%"),
                        Page.page_type.in_(member_types),
                    )
                )
            ).all()
        )
        page_ids = [str(r.id) for r in page_rows]
        link_rows: list[Any] = []
        if page_ids:
            link_rows = list(
                (
                    await session.execute(
                        select(Link.source_page_id, Link.target_page_id).where(
                            Link.dangling.is_(False),
                            Link.target_page_id.is_not(None),
                        )
                    )
                ).all()
            )

    pages = [
        {
            "id": str(r.id),
            "title": r.title,
            "page_type": r.page_type,
            "file_path": r.file_path,
            "sources": r.sources,
            "tags": r.tags,
        }
        for r in page_rows
    ]
    links = [
        {"source_page_id": str(r.source_page_id), "target_page_id": str(r.target_page_id)}
        for r in link_rows
    ]
    return pages, links


def _build_clusters(
    pages: list[dict[str, Any]],
    links: list[dict[str, Any]],
    *,
    min_synthesis: int = MIN_SYNTHESIS_CLUSTER,
    min_comparison: int = MIN_COMPARISON_ENTITIES,
    max_comparison: int = MAX_COMPARISON_ENTITIES,
    min_shared: int = MIN_SHARED_SOURCES,
) -> list[Cluster]:
    """
    Pure, deterministic cluster heuristic over the 4-signal graph (no DB, no provider, no RNG).

    Signals (mirroring graph/engine.py):
      * source-overlap ×4 — the DOMINANT signal: two pages are "connected" iff they share
        ≥ ``min_shared`` source documents (``pages.sources`` intersection).
      * type-affinity — reused verbatim from ``graph/engine.py::_type_affinity`` — modulates the
        cluster confidence (cross-type entity↔concept syntheses are rewarded; same-type
        entity↔entity comparisons are lightly damped — engine parity).
      * Adamic-Adar — approximated by shared-source co-citation degree (a page connected to many
        others via shared sources seeds a stronger neighbourhood).

    SYNTHESIS: greedy same-domain neighbourhoods of ≥ ``min_synthesis`` entity/concept pages.
    COMPARISON: greedy co-cited groups of ≥ ``min_comparison`` same-domain entities (capped at
    ``max_comparison``).

    Deterministic: every ordering breaks ties by slug; the returned list is sorted by
    ``(kind, -confidence, first-slug)`` so repeated runs seed identically (idempotent, I1).
    """
    recs: list[dict[str, Any]] = []
    for p in pages:
        ptype = (p.get("page_type") or "").strip().lower()
        recs.append(
            {
                "id": str(p["id"]),
                "title": (p.get("title") or "").strip(),
                "page_type": ptype,
                "slug": _page_slug(p),
                "sources": frozenset(_as_str_list(p.get("sources"))),
                "domain": _dominant_domain(_as_str_list(p.get("tags"))),
            }
        )

    def shared(a: dict[str, Any], b: dict[str, Any]) -> int:
        return len(a["sources"] & b["sources"])

    clusters: list[Cluster] = []
    clusters.extend(_seed_synthesis(recs, shared, min_synthesis, min_shared))
    clusters.extend(_seed_comparison(recs, shared, min_comparison, max_comparison, min_shared))

    clusters.sort(key=lambda c: (c.kind, -c.confidence, c.slugs[0] if c.slugs else ""))
    return clusters


def _seed_synthesis(
    recs: list[dict[str, Any]],
    shared: Any,
    min_synthesis: int,
    min_shared: int,
) -> list[Cluster]:
    """Greedy same-domain source-overlap neighbourhoods → synthesis candidates."""
    members_pool = [r for r in recs if r["page_type"] in _SYNTHESIS_MEMBER_TYPES]

    def src_degree(r: dict[str, Any]) -> int:
        return sum(1 for o in members_pool if o["id"] != r["id"] and shared(r, o) >= min_shared)

    ordered = sorted(members_pool, key=lambda r: (-src_degree(r), r["slug"]))
    used: set[str] = set()
    out: list[Cluster] = []
    for seed in ordered:
        if seed["id"] in used:
            continue
        members = [seed]
        for o in ordered:
            if o["id"] == seed["id"] or o["id"] in used:
                continue
            if shared(seed, o) >= min_shared and _same_domain(seed, o):
                members.append(o)
        if len(members) >= min_synthesis:
            for m in members:
                used.add(m["id"])
            out.append(_make_cluster("synthesis", members, shared, min_shared))
    return out


def _seed_comparison(
    recs: list[dict[str, Any]],
    shared: Any,
    min_comparison: int,
    max_comparison: int,
    min_shared: int,
) -> list[Cluster]:
    """Greedy co-cited same-domain entity groups → comparison candidates."""
    entities = [r for r in recs if r["page_type"] in _COMPARISON_MEMBER_TYPES]
    buckets: dict[str, list[dict[str, Any]]] = {}
    for e in entities:
        buckets.setdefault(e["domain"] or "__none__", []).append(e)

    out: list[Cluster] = []
    for _domain, group in sorted(buckets.items()):

        def co_degree(r: dict[str, Any], grp: list[dict[str, Any]] = group) -> int:
            return sum(1 for o in grp if o["id"] != r["id"] and shared(r, o) >= min_shared)

        ordered = sorted(group, key=lambda r: (-co_degree(r), r["slug"]))
        used: set[str] = set()
        for seed in ordered:
            if seed["id"] in used:
                continue
            members = [seed]
            for o in ordered:
                if o["id"] == seed["id"] or o["id"] in used:
                    continue
                if shared(seed, o) >= min_shared:
                    members.append(o)
                    if len(members) >= max_comparison:
                        break
            if len(members) >= min_comparison:
                for m in members:
                    used.add(m["id"])
                out.append(_make_cluster("comparison", members, shared, min_shared))
    return out


def _make_cluster(
    kind: str,
    members: list[dict[str, Any]],
    shared: Any,
    min_shared: int,
) -> Cluster:
    """Assemble a Cluster from its member recs (deterministic ordering + confidence)."""
    from app.graph.engine import _type_affinity  # noqa: PLC0415 — reuse the 4th graph signal

    members_sorted = sorted(members, key=lambda r: r["slug"])
    union: set[str] = set()
    for m in members_sorted:
        union |= m["sources"]

    pairs_shared: list[int] = []
    affinities: list[float] = []
    for i in range(len(members_sorted)):
        for j in range(i + 1, len(members_sorted)):
            pairs_shared.append(shared(members_sorted[i], members_sorted[j]))
            affinities.append(
                _type_affinity(members_sorted[i]["page_type"], members_sorted[j]["page_type"])
            )

    confidence = _cluster_confidence(pairs_shared, len(members_sorted), affinities)
    domain = next((m["domain"] for m in members_sorted if m["domain"]), None)
    return Cluster(
        kind=kind,
        page_ids=[m["id"] for m in members_sorted],
        slugs=[m["slug"] for m in members_sorted],
        titles=[m["title"] for m in members_sorted],
        sources=sorted(union),
        domain=domain,
        confidence=confidence,
    )


def _cluster_confidence(pairs_shared: list[int], size: int, affinities: list[float]) -> float:
    """
    Confidence in [0,1] from the graph signals. Dominated by shared-source strength (×4 signal),
    with a cluster-size term and a light type-affinity modulator (engine values ∈ [0.5, 1.2]).

    base = 0.7·min(1, avg_shared/4) + 0.3·min(1, size/4)
    conf = base · (0.85 + 0.15·clamp(mean_affinity, 0.5, 1.2))
    """
    if not pairs_shared:
        return 0.0
    avg_shared = sum(pairs_shared) / len(pairs_shared)
    shared_strength = min(1.0, avg_shared / _SHARED_SATURATION)
    size_factor = min(1.0, size / _SIZE_SATURATION)
    base = _CONF_W_SHARED * shared_strength + _CONF_W_SIZE * size_factor
    mean_aff = (sum(affinities) / len(affinities)) if affinities else 1.0
    aff_mod = 0.85 + 0.15 * min(1.2, max(0.5, mean_aff))
    return round(min(1.0, base * aff_mod), 4)


def _same_domain(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """
    Same-domain gate for synthesis membership. Two tagged pages must share a dominant domain; an
    untagged page (domain None) may join any cluster (source overlap already gates membership).
    """
    da, db = a["domain"], b["domain"]
    if da is None or db is None:
        return True
    return bool(da == db)


# ── Generation (ONE bounded provider call per accepted cluster) ────────────────


async def _generate_cluster_body(
    provider: InferenceProvider,
    cluster: Cluster,
    vault_context: str,
) -> tuple[str, str] | None:
    """
    ONE bounded ``provider.chat()`` call (I6/I7 — the same backend-neutral surface reclassify /
    domain-tagger use; no ``isinstance`` branch, no new ABC method). Returns ``(title, body)`` or
    ``None`` when the output is unusable: malformed / empty / a comparison lacking a table (STRICT).
    """
    if cluster.kind == "synthesis":
        instruction = _build_synthesis_instruction(cluster, vault_context)
    else:
        instruction = _build_comparison_instruction(cluster, vault_context)

    raw = await _chat_collect(provider, instruction)
    parsed = _parse_generated(raw)
    if parsed is None:
        return None
    title, body = parsed
    body = body.strip()
    if not body:
        return None
    if cluster.kind == "comparison" and not _looks_like_table(body):
        # STRICT: a comparison MUST carry a markdown table; otherwise reject (counted failed).
        return None
    if not title.strip():
        title = _default_title(cluster)
    return title.strip(), body


def _build_synthesis_instruction(cluster: Cluster, vault_context: str) -> str:
    """Deterministic synthesis prompt: thesis + integration prose with [[wikilinks]] to members."""
    members_block = "\n".join(f"- {t}" for t in cluster.titles) or "(none)"
    links_block = ", ".join(f"[[{t}]]" for t in cluster.titles)
    schema_block = (vault_context or "").strip() or "(no schema.md/purpose.md available)"
    return (
        "You are the corpus-level SYNTHESIS step of a self-organizing wiki. You are given a "
        "cluster of related, already-existing wiki pages (they share many sources and a domain). "
        "Write ONE cross-cutting synthesis page that INTEGRATES them: state a clear thesis, then "
        "weave the related pages together into connected prose (patterns, tensions, the bigger "
        "picture). This is NOT a summary of one document — it is a bridge across the cluster.\n\n"
        "RULES:\n"
        "  - Open with a one-paragraph THESIS, then integration prose.\n"
        f"  - Reference each related page with an Obsidian wikilink: {links_block}\n"
        "  - Do NOT invent facts the pages do not support. Follow the vault schema rules.\n\n"
        f"# Cluster pages to integrate\n{members_block}\n\n"
        f"# Vault schema / purpose\n{schema_block}\n\n"
        'Return ONLY a JSON object {"title": "<page title>", "body": "<markdown body WITHOUT a '
        'frontmatter block>"}. Return no prose outside the JSON object.'
    )


def _build_comparison_instruction(cluster: Cluster, vault_context: str) -> str:
    """Deterministic comparison prompt: intro + a markdown table across the same-class entities."""
    members_block = "\n".join(f"- {t}" for t in cluster.titles) or "(none)"
    links_block = ", ".join(f"[[{t}]]" for t in cluster.titles)
    schema_block = (vault_context or "").strip() or "(no schema.md/purpose.md available)"
    return (
        "You are the corpus-level COMPARISON step of a self-organizing wiki. You are given a set "
        "of same-class entities that are frequently co-cited across the corpus. Write ONE "
        "side-by-side comparison page: a short intro, then a MARKDOWN TABLE with one column per "
        "entity and one row per comparison dimension (what they are, key differences, when to use "
        "each, notable relationships).\n\n"
        "RULES:\n"
        "  - The body MUST contain a GitHub-flavored markdown table (header row + '---' separator "
        "row + data rows).\n"
        f"  - Reference each entity with an Obsidian wikilink at least once: {links_block}\n"
        "  - Do NOT invent facts. Follow the vault schema rules.\n\n"
        f"# Entities to compare\n{members_block}\n\n"
        f"# Vault schema / purpose\n{schema_block}\n\n"
        'Return ONLY a JSON object {"title": "<page title>", "body": "<markdown body WITH the '
        'table, WITHOUT a frontmatter block>"}. Return no prose outside the JSON object.'
    )


def _parse_generated(raw: str) -> tuple[str, str] | None:
    """
    Parse the generation output into ``(title, body)``. Prefers ``{"title","body"}`` JSON; falls
    back to treating raw non-JSON markdown as the body (title derived later). None when empty.
    """
    if not raw or not raw.strip():
        return None
    obj = _loads_json_lenient(raw)
    if isinstance(obj, dict):
        title = obj.get("title")
        body = obj.get("body", obj.get("content"))
        title_str = title.strip() if isinstance(title, str) else ""
        if isinstance(body, str) and body.strip():
            return title_str, body
        return None
    # Non-JSON output → treat the whole thing as the markdown body.
    return "", raw.strip()


def _looks_like_table(body: str) -> bool:
    """True iff *body* contains a markdown table (a pipe header + a '---' separator row)."""
    pipe_lines = [ln for ln in body.splitlines() if "|" in ln]
    if len(pipe_lines) < 2:
        return False
    for ln in pipe_lines:
        stripped = ln.replace("|", "").strip()
        if stripped and "-" in stripped and set(stripped) <= set("-: "):
            return True
    return False


def _default_title(cluster: Cluster) -> str:
    """Deterministic fallback title when the provider omits one."""
    titles = cluster.titles or cluster.slugs
    if cluster.kind == "comparison":
        if len(titles) >= 2:
            head = " vs ".join(titles[:2])
            return f"{head}{' and others' if len(titles) > 2 else ''}: comparison"
        return f"{titles[0] if titles else 'entities'}: comparison"
    head = ", ".join(titles[:3])
    prefix = cluster.domain or "cross-cutting"
    return (
        f"{prefix.capitalize()} synthesis: {head}" if head else f"{prefix.capitalize()} synthesis"
    )


async def _write_cluster_page(cluster: Cluster, title: str, body: str) -> None:
    """
    Write one synthesis/comparison page via the SINGLE shared ``write_wiki_page`` seam
    (ADR-0067 D3 sanctioned exception). ``frontmatter.related`` = cluster slugs; DB
    ``pages.sources`` = union of the cluster's sources (F3). ``origin_source=""`` — a corpus page
    has no single raw origin doc; provenance is the union sources + ``related`` links. The write
    seam owns the incremental ``data_version`` bump (I1).
    """
    from app.ingest.orchestrator import write_wiki_page  # noqa: PLC0415

    page_type = PageType.SYNTHESIS if cluster.kind == "synthesis" else PageType.COMPARISON
    frontmatter = WikiFrontmatter(
        type=page_type,
        title=title,
        sources=list(cluster.sources),
        related=list(cluster.slugs),
    )
    page = WikiPage(title=title, type=page_type, content=body, frontmatter=frontmatter)
    await write_wiki_page(None, page, "")


async def _propose_cluster_review(vault_id: str, cluster: Cluster) -> Any:
    """
    Route a borderline cluster to the F9 review queue with the right ``proposed_page_type``
    (SC-D3). Delegates to the additive ``ops/review.propose_corpus_shape_review`` seeder
    (rule-based, no provider). Returns the enqueued ReviewItem, or None on failure (never raises).
    """
    from app.ops import review  # noqa: PLC0415

    title = _default_title(cluster)
    rationale = (
        f"Graph signals suggest a {cluster.kind} across "
        f"{', '.join(cluster.titles[:4])}"
        f"{' and others' if len(cluster.titles) > 4 else ''} "
        f"(shared-source overlap; confidence={cluster.confidence:.2f}). "
        "Review and Create to author it, or Skip."
    )
    try:
        return await review.propose_corpus_shape_review(
            vault_id=vault_id,
            kind=cluster.kind,
            proposed_title=title,
            cluster_page_ids=list(cluster.page_ids),
            rationale=rationale,
        )
    except Exception as exc:  # noqa: BLE001 — proposal is best-effort (never breaks the run)
        logger.warning("synthesize: review proposal failed for %s cluster: %s", cluster.kind, exc)
        return None


# ── Provider surface + parsing helpers (mirror ops/reclassify_types.py) ────────


async def _chat_collect(provider: InferenceProvider, instruction: str) -> str:
    """ONE capability-agnostic ``provider.chat()`` turn, collecting the full text (I6/I7)."""
    from app.ingest.schemas import Message  # noqa: PLC0415

    chunks: list[str] = []
    stream = await provider.chat(
        messages=[Message(role="user", content=instruction)],
        retrieval_context="",
    )
    async for chunk in stream:
        chunks.append(chunk)
    return "".join(chunks).strip()


def _loads_json_lenient(raw: str) -> Any | None:
    """Best-effort JSON parse tolerant of ```json fences / surrounding prose. None on failure."""
    import json  # noqa: PLC0415

    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                continue
    return None


def _as_str_list(raw: Any) -> list[str]:
    """Normalize a JSONB/list/JSON-string column to list[str] (SQLite Text vs Postgres JSONB)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x is not None and str(x).strip()]
    if isinstance(raw, str):
        import json  # noqa: PLC0415

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return [raw] if raw.strip() else []
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x is not None and str(x).strip()]
        return []
    return []


def _dominant_domain(tags: list[str]) -> str | None:
    """First ``domain/<Name>`` tag's name (mirrors graph/engine.py), or None."""
    for tag in tags:
        if tag.startswith(_DOMAIN_PREFIX):
            name = tag[len(_DOMAIN_PREFIX) :].strip()
            if name:
                return name
    return None


def _page_slug(p: dict[str, Any]) -> str:
    """Slug = the on-disk file stem (``wiki/concepts/foo.md`` → ``foo``), else a title slug."""
    fp = (p.get("file_path") or "").strip()
    if fp:
        stem = fp.rsplit("/", 1)[-1]
        if stem.endswith(".md"):
            stem = stem[:-3]
        if stem:
            return stem
    return _slugify(p.get("title") or "")


def _slugify(text: str) -> str:
    """Lowercase hyphen slug (mirrors orchestrator/_slugify semantics)."""
    import re  # noqa: PLC0415

    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


# ── Provider resolution (I6 — mirror ops/reclassify_types.py) ──────────────────


async def _resolve_provider(vault_id: str) -> tuple[InferenceProvider, Any] | None:
    """
    Resolve the InferenceProvider for operation='ingest' (I6 — no hardcoded backend; "no
    provider" → None). Mirrors ops/reclassify_types.py::_resolve_provider.
    """
    from app.provider_config_service import (  # noqa: PLC0415
        ConfigNotFoundError,
        resolve_provider_config,
    )

    try:
        config_row = await resolve_provider_config("ingest", vault_id)
    except ConfigNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("synthesize: provider resolution failed (vault=%s): %s", vault_id, exc)
        return None

    try:
        provider = resolve_provider(config_row)
    except Exception as exc:  # noqa: BLE001
        logger.warning("synthesize: provider build failed (vault=%s): %s", vault_id, exc)
        return None
    return provider, config_row
