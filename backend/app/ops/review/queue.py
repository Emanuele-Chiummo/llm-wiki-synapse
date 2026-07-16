"""
F9 HITL Review Queue — CRUD + status transitions (BE-ARCH-2 package split).

Pure DB read/write operations for the review_items table: the idempotent proposal upsert
(enqueue_review), the paginated queue read (list_queue), status-transition actions (skip,
dismiss, bulk_update_reviews, _set_status), the terminal-row cleanup (clear_resolved_reviews),
and the deep-research delegation action (deep_research) — which is itself a status transition
(pending → deep_researched) rather than a generation seam, so it lives here alongside
skip/dismiss.

enqueue_review is a pure DB write — NEVER calls a provider. It is the single idempotent upsert
seam used by every producer in the package (propose.py, suggestions.py) and by external callers
(ops/dedup_entities.py, ops/lint.py, ingest/pipeline.py).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.engine import CursorResult

from app import db as _db
from app.config import settings
from app.models import DeepResearchRun, Page, ReviewItem
from app.ops._llm import clean_str

logger = logging.getLogger(__name__)

# Strong task references — a bare create_task() can be GC'd mid-run (CPython weak-ref).
_bg_tasks: set[asyncio.Task[Any]] = set()

_VALID_PROPOSAL_ORIGINS = frozenset({"rule", "ai", "corpus", "system", "lint", "legacy"})

# Terminal statuses (ADR-0044): an item is closed and never re-mutated by re-ingest / bulk.
_TERMINAL_STATUSES = frozenset(
    {"created", "skipped", "dismissed", "deep_researched", "auto_resolved"}
)
# The "resolved" tab set (ADR-0044 §6): terminal-resolved (excludes skipped/dismissed).
_RESOLVED_STATUSES = frozenset({"created", "auto_resolved", "deep_researched"})

# ── ADR-0044 §3.2: stable content-derived idempotency key (FNV-1a, no new dep) ──

_FNV1A_64_OFFSET = 0xCBF29CE484222325
_FNV1A_64_PRIME = 0x100000001B3
_FNV1A_64_MASK = 0xFFFFFFFFFFFFFFFF
_CONTENT_KEY_SEP = "\x1f"  # unit-separator; won't collide with normalized title content


def _fnv1a_16hex(text: str) -> str:
    """
    64-bit FNV-1a of *text* (UTF-8), rendered as 16 lowercase hex chars (ADR-0044 §3.2).

    Chosen over sha256 to match the nashsu reference: this is a dedup HANDLE, not a security
    digest. Pure-Python one-liner — no new dependency (I9).
    """
    h = _FNV1A_64_OFFSET
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * _FNV1A_64_PRIME) & _FNV1A_64_MASK
    return format(h, "016x")


def _content_key(
    *,
    vault_id: str,
    item_type: str,
    proposed_title: str | None,
    target_page_title: str | None = None,  # kept for call-site compat; NOT included in key (R7)
    page_id: str | None = None,  # kept for call-site compat; NOT included in key (R7)
) -> str | None:
    """
    Stable content-derived idempotency key (ADR-0044 §3.2).

    Returns a 16-hex FNV-1a digest over vault_id + item_type + normalize(proposed_title).

    R7 parity fix: target_page_title / page_id are intentionally NOT included.
    llm_wiki (review-utils.ts normalizeReviewTitle) keys only on type + normalizedTitle;
    including the conflict anchor caused different items about the same concept (but
    different target pages) to appear as distinct and never dedup. Parameters are kept
    for backward call-site compatibility but are silently ignored.

    `confirm` items ARE deduped on (type + normalizedTitle) like every other type — this is
    llm_wiki parity (review-store.ts reviewIdFor keys ALL types, confirm included, on
    `${type}::${normalizeReviewTitle(title)}`). This SUPERSEDES the earlier ADR-0044 Do-NOT #10
    ("confirm never deduped"): re-ingesting the same source re-surfaced identical confirmations as
    fresh pending rows and bloated the queue. The enqueue UPSERT still respects a human's terminal
    decision (resolved/skipped confirm → NO-OP, "resolved wins"), so dedup never re-opens a handled
    confirmation. A title-less confirm keeps content_key=NULL (always-insert) — with no concept
    handle it must not collapse every anonymous confirmation into one row. normalize() reuses
    _normalize_title (I9, propose.py).
    """
    from app.ops.review.propose import _normalize_title  # noqa: PLC0415 — avoid import cycle

    norm_title = _normalize_title(proposed_title) if proposed_title else ""
    if item_type == "confirm" and not norm_title:
        return None
    payload = _CONTENT_KEY_SEP.join([vault_id, item_type, norm_title])
    return _fnv1a_16hex(payload)


# ── Public result types ────────────────────────────────────────────────────────


@dataclass
class ReviewQueuePage:
    """Paginated result for GET /review/queue."""

    items: list[ReviewItem]
    total: int
    limit: int
    offset: int


@dataclass
class DeepResearchResult:
    """Result of the deep-research action: review item + delegated run_id."""

    review_item_id: uuid.UUID
    run_id: uuid.UUID


@dataclass
class BulkResult:
    """Result of bulk_update_reviews (ADR-0044 §6)."""

    updated: int
    skipped_terminal: int


# ── Core public operations ────────────────────────────────────────────────────


async def enqueue_review(
    *,
    vault_id: str,
    item_type: str,
    proposed_title: str | None = None,
    proposed_page_type: str | None = None,
    proposed_dir: str | None = None,
    rationale: str | None = None,
    source_page_id: uuid.UUID | None = None,
    page_id: uuid.UUID | None = None,
    content_key: str | None = None,
    referenced_page_ids: list[str] | None = None,
    search_queries: list[str] | None = None,
    proposal_origin: str = "legacy",
) -> ReviewItem:
    """
    Idempotent upsert of one review_items proposal row (ADR-0044 §3.4, supersedes ADR-0034 §3.2).

    Pure DB write — NEVER calls a provider (fire-and-forget from propose_reviews,
    which is itself called fire-and-forget from the orchestrator).

    item_type must be one of: missing-page | suggestion | contradiction | duplicate | confirm.
    proposal_origin defaults to ``legacy`` for backward-compatible callers and is validated as
    one of rule | ai | corpus | system | lint | legacy before any database work.

    IDEMPOTENCY (ADR-0044 §3.4 / Do-NOT #2):
      When content_key is non-NULL, this is an UPSERT-on-(vault_id, content_key):
        - no existing row              → INSERT a new pending row (first sighting)
        - existing row is 'pending'    → refresh rationale/referenced_page_ids/search_queries
                                         IN PLACE (keep id + created_at; the human hasn't acted)
        - existing row is terminal     → NO-OP (respect the human's prior skip/dismiss/create)
      A single bounded indexed read (the new partial-unique index) — the portable contract that
      the Postgres partial-unique index enforces at the DB level (SQLite emulates via this read).

    When content_key is NULL (a title-less confirm, or a legacy/rule row with no key) → always
    INSERT (no dedup handle). Titled `confirm` items now carry a content_key and dedup like every
    other type (llm_wiki reviewIdFor parity — supersedes the old Do-NOT #10).

    page_id / source_page_id / created_page_id are stored as string UUIDs for
    SQLite/Postgres compat (with_variant pattern).
    """
    if proposal_origin not in _VALID_PROPOSAL_ORIGINS:
        raise ValueError(
            "proposal_origin must be one of: " + " | ".join(sorted(_VALID_PROPOSAL_ORIGINS))
        )

    page_id_str = str(page_id) if page_id is not None else None
    source_page_id_str = str(source_page_id) if source_page_id is not None else None
    ref_ids = list(referenced_page_ids) if referenced_page_ids else None
    queries = list(search_queries) if search_queries else None

    async with _db.get_session() as session:
        # ── UPSERT branch (ADR-0044 §3.4) — only when we have a dedup handle ──────
        if content_key is not None:
            existing_row = await session.execute(
                select(ReviewItem)
                .where(
                    ReviewItem.vault_id == vault_id,
                    ReviewItem.content_key == content_key,
                )
                .order_by(ReviewItem.created_at.desc())
                .limit(1)
            )
            existing = existing_row.scalar_one_or_none()

            if existing is not None:
                if existing.status == "pending":
                    # Refresh context in place, keep id + created_at + queue position.
                    existing.rationale = rationale
                    existing.proposal_origin = proposal_origin
                    if ref_ids is not None:
                        existing.referenced_page_ids = ref_ids
                    if queries is not None:
                        existing.search_queries = queries
                    await session.flush()
                    await session.refresh(existing)
                    session.expunge(existing)
                    logger.debug(
                        "enqueue_review: refreshed pending item_id=%s key=%s vault=%s title=%r",
                        existing.id,
                        content_key,
                        vault_id,
                        proposed_title,
                    )
                    return existing
                # Terminal row with the same key → NO-OP (respect the human's decision).
                session.expunge(existing)
                logger.debug(
                    "enqueue_review: no-op (terminal %s) key=%s vault=%s title=%r",
                    existing.status,
                    content_key,
                    vault_id,
                    proposed_title,
                )
                return existing

        # ── INSERT branch (first sighting, or content_key is NULL) ───────────────
        item_id = uuid.uuid4()
        item_id_str = str(item_id)
        item = ReviewItem(
            id=item_id_str,
            vault_id=vault_id,
            item_type=item_type,
            proposal_origin=proposal_origin,
            status="pending",
            page_id=page_id_str,
            source_page_id=source_page_id_str,
            proposed_title=proposed_title,
            proposed_page_type=proposed_page_type,
            proposed_dir=proposed_dir,
            rationale=rationale,
            content_key=content_key,
            referenced_page_ids=ref_ids,
            search_queries=queries,
            resolution=None,
            created_page_id=None,
            deep_research_run_id=None,
            created_at=datetime.now(UTC),
            reviewed_at=None,
            reviewed_by=None,
        )
        session.add(item)
        await session.flush()
        row = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        loaded = row.scalar_one()
        session.expunge(loaded)

    logger.debug(
        "enqueue_review: inserted item_id=%s type=%s vault=%s key=%s proposed_title=%r",
        item_id_str,
        item_type,
        vault_id,
        content_key,
        proposed_title,
    )
    return loaded


def _status_filter_values(status: str | None) -> frozenset[str] | None:
    """
    Map the GET /review/queue ?status= filter (ADR-0044 §6) to a status value set.

      pending (default) → {'pending'}
      resolved          → the terminal-resolved set (created/auto_resolved/deep_researched)
      dismissed         → {'dismissed'}
      all / None-'all'  → None (no status filter — return everything)

    Any unrecognized value falls back to the pending set (safe default — the live queue).
    """
    normalized = (status or "pending").strip().lower()
    if normalized == "all":
        return None
    if normalized == "resolved":
        return _RESOLVED_STATUSES
    if normalized == "dismissed":
        return frozenset({"dismissed"})
    # default + unrecognized → live/pending set
    return frozenset({"pending"})


async def list_queue(
    vault_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
    status: str | None = "pending",
    item_type: str | None = None,
    proposal_origin: str | None = None,
    proposed_page_type: str | None = None,
) -> ReviewQueuePage:
    """
    Return a paginated ReviewQueuePage for GET /review/queue (ADR-0034 §7, ADR-0044 §6 filter).

    The ?status= filter (ADR-0044 §6) partitions the queue:
      pending (default) | resolved | dismissed | all.
    Optional item_type, proposal_origin and proposed_page_type filters are exact-match and
    composable; each is applied identically to the count and page-data queries.
    Ordered by created_at ASC. limit is capped at 200 by the REST endpoint (I7).
    """
    status_values = _status_filter_values(status)

    async with _db.get_session() as session:
        count_stmt = (
            select(func.count()).select_from(ReviewItem).where(ReviewItem.vault_id == vault_id)
        )
        data_stmt = (
            select(ReviewItem)
            .where(ReviewItem.vault_id == vault_id)
            .order_by(ReviewItem.created_at.asc())
        )
        if status_values is not None:
            count_stmt = count_stmt.where(ReviewItem.status.in_(list(status_values)))
            data_stmt = data_stmt.where(ReviewItem.status.in_(list(status_values)))
        optional_filters = (
            (ReviewItem.item_type, item_type),
            (ReviewItem.proposal_origin, proposal_origin),
            (ReviewItem.proposed_page_type, proposed_page_type),
        )
        for column, value in optional_filters:
            if value is not None:
                count_stmt = count_stmt.where(column == value)
                data_stmt = data_stmt.where(column == value)

        total: int = (await session.execute(count_stmt)).scalar_one()
        data_stmt = data_stmt.offset(offset).limit(limit)
        rows = list((await session.execute(data_stmt)).scalars().all())
        for r in rows:
            session.expunge(r)

    return ReviewQueuePage(items=rows, total=total, limit=limit, offset=offset)


async def bulk_update_reviews(
    *,
    vault_id: str,
    action: str,
    ids: list[uuid.UUID],
) -> BulkResult:
    """
    Bounded bulk status write (ADR-0044 §6, Do-NOT #5/#6).

    action ∈ {skip, dismiss, mark-resolved}. Only PENDING ids (scoped to *vault_id*) are
    mutated; already-terminal ids are counted in skipped_terminal and NEVER re-mutated.
    `confirm` items are NEVER auto-resolved by mark-resolved (Do-NOT #6/#10) — they are counted
    as skipped_terminal-style no-ops for mark-resolved (kept pending). No provider call.

    Caller (REST) enforces len(ids) ≤ REVIEW_BULK_MAX_IDS (I7 — 400 otherwise).
    """
    status_for_action = {
        "skip": ("skipped", "skipped"),
        "dismiss": ("dismissed", "dismissed"),
        # Human-marked terminal: reuse the auto_resolved lifecycle value + llm_resolved resolution
        # (ADR-0044 §6 — "human-marked terminal", same shape the sweep produces).
        "mark-resolved": ("auto_resolved", "llm_resolved"),
    }
    if action not in status_for_action:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=f"Unknown bulk action {action!r}")
    new_status, new_resolution = status_for_action[action]

    id_strs = [str(i) for i in ids]
    if not id_strs:
        return BulkResult(updated=0, skipped_terminal=0)

    updated = 0
    skipped_terminal = 0
    async with _db.get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(ReviewItem).where(
                        ReviewItem.vault_id == vault_id,
                        ReviewItem.id.in_(id_strs),
                    )
                )
            )
            .scalars()
            .all()
        )
        for item in rows:
            if item.status != "pending":
                skipped_terminal += 1
                continue
            # NEVER auto-resolve a confirm via mark-resolved (Do-NOT #6/#10) — keep it pending.
            if action == "mark-resolved" and item.item_type == "confirm":
                skipped_terminal += 1
                continue
            item.status = new_status
            item.resolution = new_resolution
            item.reviewed_at = datetime.now(UTC)
            item.reviewed_by = "web-ui"
            updated += 1
        await session.flush()

    logger.info(
        "bulk_update_reviews: vault=%s action=%s requested=%d updated=%d skipped_terminal=%d",
        vault_id,
        action,
        len(id_strs),
        updated,
        skipped_terminal,
    )
    return BulkResult(updated=updated, skipped_terminal=skipped_terminal)


async def clear_resolved_reviews(vault_id: str) -> int:
    """
    Hard-delete terminal review rows for a vault ("Clear resolved" — ADR-0044 §6, Do-NOT #5/#6).

    Deletes rows whose status is terminal (skipped/dismissed/created/auto_resolved/
    deep_researched) for *vault_id* in ONE bounded vault-scoped statement. Pending rows are
    NEVER touched. Idempotent. Returns the number of rows deleted.

    These rows are advisory metadata (not vault content); created_page_id points at a page that
    persists independently (ADR-0044 §9.5). No cascade risk (the pages FK is nullable).
    """
    from sqlalchemy import delete

    async with _db.get_session() as session:
        result = cast(
            "CursorResult[Any]",
            await session.execute(
                delete(ReviewItem).where(
                    ReviewItem.vault_id == vault_id,
                    ReviewItem.status.in_(list(_TERMINAL_STATUSES)),
                )
            ),
        )
        deleted = int(result.rowcount or 0)

    logger.info("clear_resolved_reviews: vault=%s deleted=%d", vault_id, deleted)
    return deleted


def _first_search_query(raw: Any) -> str | None:
    """Return the first non-empty string in a search_queries JSON value, else None (ADR-0044)."""
    if not isinstance(raw, list):
        return None
    for entry in raw:
        s = clean_str(entry)
        if s:
            return s
    return None


def _all_search_queries(raw: Any) -> list[str]:
    """
    Return every non-empty, de-duplicated string in a search_queries JSON value (R7-5).

    Used to seed Deep Research with the FULL curated query list (AC-R7-5-2), not just the first.
    Bounded to REVIEW_SEARCH_QUERIES_MAX (I7). Non-list / empty → [] (never raises).
    """
    if not isinstance(raw, list):
        return []
    cap = int(getattr(settings, "review_search_queries_max", 3))
    out: list[str] = []
    seen: set[str] = set()
    for entry in raw:
        s = clean_str(entry)
        if s is None or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= cap:
            break
    return out


async def skip(item_id: uuid.UUID) -> ReviewItem:
    """Set status=skipped, resolution=skipped, reviewed_at=now() (ADR-0034 §7).
    404 if the item is not found."""
    return await _set_status(item_id, "skipped", resolution="skipped")


async def dismiss(item_id: uuid.UUID) -> ReviewItem:
    """
    Dismiss action (ADR-0044 §6): status=dismissed, resolution=dismissed, reviewed_at=now().

    Terminal, distinct from skip: "hide this, I'm not acting" vs skip's "considered and declined".
    404 if the item is not found.
    """
    return await _set_status(item_id, "dismissed", resolution="dismissed")


async def deep_research(
    item_id: uuid.UUID,
    *,
    vault_id: str | None = None,
) -> DeepResearchResult:
    """
    Deep-research action (ADR-0034 §7, AC-F9-3, AC-F10-5).

    1. Load the review item (404 if absent).
    2. Extract topic: proposed_title → rationale (first line) → page.title → fallback.
       (Was pre_generated_query in ADR-0025; that column is DROPPED — ADR-0034 §3.1.)
    3. Pre-INSERT the deep_research_runs row.
    4. Set status=deep_researched, resolution=researched, deep_research_run_id=run_id.
    5. Schedule the background task (fire-and-poll, same as research_start endpoint).
    6. Return DeepResearchResult(review_item_id, run_id).

    503 if SEARXNG_URL is unset (I9).
    404 if item not found.
    """
    # 503 guard (I9 — no fake run): the SELECTED web-search provider must be configured (ADR-0070).
    from app.ops.web_search import get_web_search_provider

    _provider = get_web_search_provider()
    if not _provider.configured():
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail=(
                f"The selected web-search provider {_provider.name!r} is not configured. "
                "Configure it (SEARXNG_URL for searxng; the matching API key for the opt-in "
                "cloud backends; OLLAMA_URL for ollama_web) or switch via "
                "PUT /config/app/web_search_provider to enable deep research (I9, ADR-0070)."
            ),
        )

    item_id_str = str(item_id)

    async with _db.get_session() as session:
        row = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item = row.scalar_one_or_none()
        if item is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        # Extract the FULL curated seed query list (R7-5, AC-R7-5-2) — passed to deep_research so
        # iteration 1 uses them verbatim (no re-generation). topic falls back to the first.
        seed_queries: list[str] = _all_search_queries(item.search_queries)

        # Extract topic: search_queries[0] (ADR-0044 §2.3 curated seed) → proposed_title →
        # rationale first line → page.title → fallback (ADR-0034 order when no seed).
        topic: str
        seed_query = _first_search_query(item.search_queries)
        if seed_query:
            topic = seed_query
        elif item.proposed_title:
            topic = item.proposed_title
        elif item.rationale:
            first_line = item.rationale.splitlines()[0].strip()
            topic = first_line if first_line else f"Review: {item_id}"
        elif item.page_id:
            pg_row = await session.execute(select(Page).where(Page.id == str(item.page_id)))
            pg = pg_row.scalar_one_or_none()
            topic = pg.title if (pg and pg.title) else f"Review: {item_id}"
        else:
            topic = f"Review: {item_id}"

        effective_vault_id = vault_id or item.vault_id

    # Delegate to deep_research seam (same as POST /research/start)
    import asyncio as _asyncio

    from app.ops.deep_research import run_deep_research

    run_id = uuid.uuid4()
    run_id_str = str(run_id)
    frozen_max_iter = settings.deep_research_max_iter
    frozen_token_budget = settings.deep_research_token_budget

    # Pre-INSERT the run row
    async with _db.get_session() as session:
        run = DeepResearchRun(
            id=run_id_str,
            vault_id=effective_vault_id,
            topic=topic,
            status="running",
            max_iter=frozen_max_iter,
            token_budget=frozen_token_budget,
            iterations_used=0,
            queries_used=[],
            sources_fetched=0,
            converged=False,
            total_cost_usd=0,
            synthesis_text=None,
            synthesis_page_id=None,
            started_at=datetime.now(UTC),
            completed_at=None,
            error_message=None,
        )
        session.add(run)

    # Update the review item row
    async with _db.get_session() as session:
        row2 = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item2 = row2.scalar_one_or_none()
        if item2 is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        item2.status = "deep_researched"
        item2.resolution = "researched"
        item2.reviewed_at = datetime.now(UTC)
        item2.reviewed_by = "web-ui"
        item2.deep_research_run_id = run_id_str  # type: ignore[assignment]

        await session.flush()
        await session.refresh(item2)
        session.expunge(item2)

    # Schedule the background task. R7-5: pass the curated seed_queries so the first research
    # round uses them verbatim (no re-generation) — AC-R7-5-2.
    _t = _asyncio.create_task(
        run_deep_research(
            vault_id=effective_vault_id,
            topic=topic,
            max_iter=frozen_max_iter,
            token_budget=frozen_token_budget,
            run_id=run_id,
            seed_queries=seed_queries or None,
        )
    )
    _bg_tasks.add(_t)
    _t.add_done_callback(_bg_tasks.discard)

    logger.info(
        "deep_research action: review_item_id=%s → run_id=%s vault=%s topic=%r seeds=%d",
        item_id_str,
        run_id_str,
        effective_vault_id,
        topic,
        len(seed_queries),
    )
    return DeepResearchResult(review_item_id=item_id, run_id=run_id)


async def _set_status(
    item_id: uuid.UUID,
    status: str,
    *,
    resolution: str | None = None,
    reviewed_by: str = "web-ui",
) -> ReviewItem:
    """
    Update status + reviewed_at (+ optional resolution) on a review item.

    Extended for ADR-0034 statuses: pending | created | skipped | deep_researched | auto_resolved.
    404 if not found.
    """
    from fastapi import HTTPException

    item_id_str = str(item_id)

    async with _db.get_session() as session:
        row = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item = row.scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        item.status = status
        item.reviewed_at = datetime.now(UTC)
        item.reviewed_by = reviewed_by
        if resolution is not None:
            item.resolution = resolution

        await session.flush()
        await session.refresh(item)
        session.expunge(item)

    return item
