"""
Dashboard stats API (R12-1 / ADR-0054 §5, F18).

Endpoints:
  GET /stats/overview  — global KPI snapshot (ADR-0054 §5.1)
  GET /stats/sections  — per-domain section breakdown (ADR-0054 §5.2)

Design:
  - Read-only aggregation over existing tables; no InferenceProvider call (I1/I6).
  - Both endpoints are memoised keyed on vault_state.data_version (+ vocabulary hash for
    sections) using the ADR-0014 debounce-signal pattern (ADR-0054 §5 caching decision).
    A cache miss falls back to a direct bounded query — caching is an optimisation, not a
    correctness dependency.
  - Auth by construction: SynapseAuthMiddleware (ADR-0052) gates these routes; no per-route
    Depends (ADR-0054 §1 / Do-NOT #9).
  - monthly_cost_usd: REUSED from costs.get_monthly_cost_usd (I9 / AC-R12-1-3 — never
    duplicated SQL; identical totals to GET /costs/summary guaranteed by shared helper).
  - slug derivation: re [^a-z0-9]+ → "-" lowercased (ADR-0054 §5.1 spec); no DB column.
  - top_pages by degree: COUNT(DISTINCT incident edges) per page in the section; capped at 5
    (ADR-0054 §5.2, ADR-0016 degree = distinct incident structural edges).
  - untagged bucket: always present; domain="untagged"; emitted last (ADR-0054 §5.2).
  - dormant vocabulary ([]): sections returns only the untagged bucket (owner-lock #4).

Invariants:
  I1 — read-only; no vault mutation, no index re-scan, no Qdrant call.
  I2 — no graph recompute triggered; reads cached snapshot degree from edges table.
  I3 — cheap COUNT/GROUP reads, memoised; no heavy computation on the hot path.
  I6 — zero InferenceProvider calls.
  I7 — all queries are bounded (LIMIT 10 recent_activity, LIMIT 5 top_pages).
  I8 — I8: route registered; OpenAPI auto-regenerated; BearerAuth by construction.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import func, literal_column, select

from app.config import settings
from app.config_overrides import effective_domain_vocabulary
from app.db import get_session
from app.models import Edge, LintFinding, Page, ReviewItem, VaultState

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stats"])

# ── Slug helper (ADR-0054 §5.1) ───────────────────────────────────────────────
_NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    """Derive a URL slug from a page title: lowercase, collapse non-alnum to '-'."""
    return _NON_SLUG_RE.sub("-", title.lower()).strip("-")


# ── In-process memo cache (data_version keyed) ────────────────────────────────
# Each cache entry is a tuple: (key, payload_dict).
# "key" for overview is data_version (int); for sections it is (data_version, vocab_hash).
# Cache is invalidated on mismatch — a bump or vocabulary change causes one recompute.

_overview_cache: tuple[int, dict[str, Any]] | None = None
_sections_cache: tuple[tuple[int, str], dict[str, Any]] | None = None


def _vocab_hash(vocab: list[str]) -> str:
    """Stable hash of the current vocabulary for sections cache invalidation."""
    joined = "\x00".join(vocab)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


# ── Month boundary helpers (shared with costs.get_monthly_cost_usd) ───────────


def _current_month_bounds() -> tuple[datetime, datetime]:
    """Return (month_start, month_end) in UTC for the current calendar month."""
    now = datetime.now(tz=UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    if now.month == 12:
        month_end = datetime(now.year + 1, 1, 1, tzinfo=UTC)
    else:
        month_end = datetime(now.year, now.month + 1, 1, tzinfo=UTC)
    return month_start, month_end


# ── GET /stats/overview ────────────────────────────────────────────────────────


@router.get(
    "/stats/overview",
    summary="Global vault KPI snapshot",
    description=(
        "Returns global vault KPIs: pages_total, pages_by_type, links_total, "
        "communities_count, review_pending, lint_open, monthly_cost_usd, data_version, "
        "and recent_activity (last 10 pages by updated_at). "
        "Memoised on data_version; cache miss triggers a fresh bounded DB read. "
        "monthly_cost_usd reuses the costs.get_monthly_cost_usd shared helper "
        "(AC-R12-1-3, ADR-0054 §5.1). Auth: SynapseAuthMiddleware by construction. "
        "(R12-1 / F18 / ADR-0054 §5.1)"
    ),
    responses={200: {"description": "Global KPI snapshot"}},
)
async def get_stats_overview() -> JSONResponse:
    """GET /stats/overview — bounded reads from existing tables; memoised (ADR-0054 §5.1)."""
    global _overview_cache  # noqa: PLW0603

    from app.costs import get_monthly_cost_usd  # noqa: PLC0415 — deferred to avoid circular import

    vault_id = settings.vault_id

    # ── Read current data_version for cache key ───────────────────────────────
    async with get_session() as session:
        vs_row = await session.execute(
            select(VaultState.data_version).where(VaultState.vault_id == vault_id)
        )
        vs_result = vs_row.scalar_one_or_none()
        current_version: int = vs_result if vs_result is not None else 0

    # ── Cache check (data_version key + current month) ────────────────────────
    # Include month so the cost slice auto-updates on month rollover without a version bump.
    now_utc = datetime.now(tz=UTC)
    month_key = now_utc.strftime("%Y-%m")
    cache_key = current_version * 1000 + hash(month_key) % 1000  # compact int cache key
    if _overview_cache is not None and _overview_cache[0] == cache_key:
        logger.debug(
            "stats/overview: cache HIT (data_version=%d month=%s)", current_version, month_key
        )
        return JSONResponse(content=_overview_cache[1])

    logger.debug(
        "stats/overview: cache MISS (data_version=%d month=%s)", current_version, month_key
    )

    async with get_session() as session:
        # ── pages_total + pages_by_type ───────────────────────────────────────
        type_rows = (
            await session.execute(
                select(
                    Page.page_type,
                    func.count().label("cnt"),
                )
                .where(
                    Page.vault_id == vault_id,
                    Page.deleted_at.is_(None),
                )
                .group_by(Page.page_type)
            )
        ).all()

        pages_by_type: dict[str, int] = {}
        pages_total = 0
        for row in type_rows:
            key = row.page_type if row.page_type is not None else "untyped"
            pages_by_type[key] = row.cnt
            pages_total += row.cnt

        # ── links_total (count of structural edges — ADR-0016) ────────────────
        links_total_row = await session.execute(
            select(func.count()).select_from(Edge).where(Edge.vault_id == vault_id)
        )
        links_total: int = links_total_row.scalar_one()

        # ── communities_count (COUNT DISTINCT pages.community) ────────────────
        communities_row = await session.execute(
            select(func.count(func.distinct(Page.community))).where(
                Page.vault_id == vault_id,
                Page.deleted_at.is_(None),
                Page.community.is_not(None),
            )
        )
        communities_count: int = communities_row.scalar_one()

        # ── review_pending ────────────────────────────────────────────────────
        review_row = await session.execute(
            select(func.count())
            .select_from(ReviewItem)
            .where(
                ReviewItem.vault_id == vault_id,
                ReviewItem.status == "pending",
            )
        )
        review_pending: int = review_row.scalar_one()

        # ── lint_open ─────────────────────────────────────────────────────────
        lint_row = await session.execute(
            select(func.count())
            .select_from(LintFinding)
            .where(
                LintFinding.vault_id == vault_id,
                LintFinding.status == "open",
            )
        )
        lint_open: int = lint_row.scalar_one()

        # ── recent_activity (last 10 by updated_at) ───────────────────────────
        recent_rows = (
            await session.execute(
                select(Page.id, Page.title, Page.updated_at)
                .where(
                    Page.vault_id == vault_id,
                    Page.deleted_at.is_(None),
                )
                .order_by(Page.updated_at.desc())
                .limit(10)
            )
        ).all()

        recent_activity = [
            {
                "page_id": str(row.id),
                "title": row.title or "",
                "slug": _slugify(row.title or ""),
                "updated_at": row.updated_at.isoformat() if row.updated_at is not None else None,
            }
            for row in recent_rows
        ]

        # ── monthly_cost_usd — REUSED shared helper (AC-R12-1-3) ─────────────
        month_start, month_end = _current_month_bounds()
        monthly_cost_usd = await get_monthly_cost_usd(session, vault_id, month_start, month_end)

    payload: dict[str, Any] = {
        "pages_total": pages_total,
        "pages_by_type": pages_by_type,
        "links_total": links_total,
        "communities_count": communities_count,
        "review_pending": review_pending,
        "lint_open": lint_open,
        "monthly_cost_usd": monthly_cost_usd,
        "data_version": current_version,
        "recent_activity": recent_activity,
    }

    _overview_cache = (cache_key, payload)
    return JSONResponse(content=payload)


# ── GET /stats/sections ────────────────────────────────────────────────────────


@router.get(
    "/stats/sections",
    summary="Per-domain section breakdown",
    description=(
        "Returns one entry per active vocabulary domain (in vocabulary order) plus an "
        "untagged bucket (always last). Each section has: domain, pages_total, pages_by_type, "
        "last_activity (ISO-8601 or null), and top_pages (top 5 by degree DESC). "
        "Dormant vocabulary ([]) returns only the untagged bucket (owner-lock #4). "
        "Memoised on data_version + vocabulary hash. "
        "Auth: SynapseAuthMiddleware by construction. "
        "(R12-1 / F18 / ADR-0054 §5.2)"
    ),
    responses={200: {"description": "Per-domain section breakdown"}},
)
async def get_stats_sections() -> JSONResponse:
    """GET /stats/sections — per-vocabulary-domain aggregation; memoised (ADR-0054 §5.2)."""
    global _sections_cache  # noqa: PLW0603

    vault_id = settings.vault_id
    vocab = effective_domain_vocabulary()
    v_hash = _vocab_hash(vocab)

    # ── Read current data_version for cache key ───────────────────────────────
    async with get_session() as session:
        vs_row = await session.execute(
            select(VaultState.data_version).where(VaultState.vault_id == vault_id)
        )
        vs_result = vs_row.scalar_one_or_none()
        current_version = vs_result if vs_result is not None else 0

    cache_key: tuple[int, str] = (current_version, v_hash)
    if _sections_cache is not None and _sections_cache[0] == cache_key:
        logger.debug(
            "stats/sections: cache HIT (data_version=%d vocab_hash=%s)", current_version, v_hash
        )
        return JSONResponse(content=_sections_cache[1])

    logger.debug(
        "stats/sections: cache MISS (data_version=%d vocab_hash=%s)", current_version, v_hash
    )

    async with get_session() as session:
        # ── Fetch all live pages with their tags and updated_at ───────────────
        # We read pages into memory (bounded by vault size; typical vault << 10k pages)
        # and do Python-side grouping — same portability rationale as costs.py.
        page_rows: Sequence[Any] = (
            await session.execute(
                select(Page.id, Page.page_type, Page.tags, Page.updated_at).where(
                    Page.vault_id == vault_id,
                    Page.deleted_at.is_(None),
                )
            )
        ).all()

        # ── Fetch degree per page (COUNT incident edges) ──────────────────────
        # degree = count of distinct structural edges (source_page_id OR target_page_id)
        # ADR-0016: one edge per undirected pair stored canonically.
        # We union both endpoint columns for each page to count incident edges.
        degree_map: dict[str, int] = {}

        # Count edges where page appears as source
        # literal_column supports .label(); TextClause (sa_text) does not (SQLAlchemy).
        src_rows: Sequence[Any] = (
            await session.execute(
                select(
                    literal_column("CAST(source_page_id AS TEXT)").label("pid"),
                    func.count().label("cnt"),
                )
                .select_from(Edge)
                .where(Edge.vault_id == vault_id)
                .group_by(literal_column("source_page_id"))
            )
        ).all()
        for row in src_rows:
            degree_map[row.pid] = degree_map.get(row.pid, 0) + row.cnt

        # Count edges where page appears as target
        tgt_rows: Sequence[Any] = (
            await session.execute(
                select(
                    literal_column("CAST(target_page_id AS TEXT)").label("pid"),
                    func.count().label("cnt"),
                )
                .select_from(Edge)
                .where(Edge.vault_id == vault_id)
                .group_by(literal_column("target_page_id"))
            )
        ).all()
        for row in tgt_rows:
            degree_map[row.pid] = degree_map.get(row.pid, 0) + row.cnt

        # ── Fetch page titles for top_pages lookup ────────────────────────────
        title_rows = (
            await session.execute(
                select(Page.id, Page.title).where(
                    Page.vault_id == vault_id,
                    Page.deleted_at.is_(None),
                )
            )
        ).all()
        title_map: dict[str, str] = {str(r.id): (r.title or "") for r in title_rows}

    # ── Build membership sets ─────────────────────────────────────────────────
    # For each domain D, collect page ids where "domain/"+D ∈ page.tags.
    # A page is "untagged" iff its tags have NO element starting with "domain/".
    # Stale domain/* tags (name not in current vocab) are IGNORED (ADR-0054 §2.2).

    domain_prefix = "domain/"

    # domain_name → set of page row indices
    domain_pages: dict[str, list[Any]] = {d: [] for d in vocab}
    untagged_pages: list[Any] = []

    for row in page_rows:
        tags: list[str] = row.tags if isinstance(row.tags, list) else []
        domain_tags_in_vocab: list[str] = [
            t[len(domain_prefix) :]
            for t in tags
            if t.startswith(domain_prefix) and t[len(domain_prefix) :] in set(vocab)
        ]
        has_any_domain = any(t.startswith(domain_prefix) for t in tags)

        if not has_any_domain:
            # No domain/* tag at all → untagged bucket
            untagged_pages.append(row)
        else:
            # Assign to matched vocabulary domains; if all domain/* tags are stale → untagged
            matched = False
            for d in domain_tags_in_vocab:
                domain_pages[d].append(row)
                matched = True
            if not matched:
                # All domain/* tags are stale (not in current vocab)
                untagged_pages.append(row)

    # ── Build section objects ─────────────────────────────────────────────────

    def _build_section(domain_name: str, rows: list[Any]) -> dict[str, Any]:
        pbt: dict[str, int] = {}
        last_ts: datetime | None = None
        for row in rows:
            key = row.page_type if row.page_type is not None else "untyped"
            pbt[key] = pbt.get(key, 0) + 1
            if row.updated_at is not None:
                if last_ts is None or row.updated_at > last_ts:
                    last_ts = row.updated_at

        # top_pages: sort by degree DESC then updated_at DESC, cap 5
        def _sort_key(r: Any) -> tuple[int, float]:
            ts = r.updated_at.timestamp() if r.updated_at is not None else 0.0
            return (-degree_map.get(str(r.id), 0), -ts)

        sorted_rows = sorted(rows, key=_sort_key)[:5]

        top_pages = [
            {
                "id": str(r.id),
                "title": title_map.get(str(r.id), ""),
                "slug": _slugify(title_map.get(str(r.id), "")),
                "degree": degree_map.get(str(r.id), 0),
            }
            for r in sorted_rows
        ]

        return {
            "domain": domain_name,
            "pages_total": len(rows),
            "pages_by_type": pbt,
            "last_activity": last_ts.isoformat() if last_ts is not None else None,
            "top_pages": top_pages,
        }

    # Emit vocabulary sections in order, then untagged last
    sections: list[dict[str, Any]] = [_build_section(d, domain_pages[d]) for d in vocab]
    sections.append(_build_section("untagged", untagged_pages))

    payload: dict[str, Any] = {"sections": sections}
    _sections_cache = (cache_key, payload)
    return JSONResponse(content=payload)
