"""
F9 HITL Review Queue — advisory post-ingest queue (ADR-0025, AC-F9-1..8).

THE I7 CONTRACT (violation is P0 rejection):
  1. generate_review_queries() makes EXACTLY ONE InferenceProvider.chat() call per item.
     No loop, no retry, no second call.
  2. That single call is wrapped in asyncio.wait_for(timeout=REVIEW_QUERY_TIMEOUT_SECONDS).
  3. On ConfigNotFoundError / timeout / any provider error → return None (item still enqueued).
  4. enqueue_review() is a pure DB write — it NEVER calls a provider.

THE I6 CONTRACT:
  The single provider call resolves via resolve_provider_config("ingest") — never isinstance /
  provider_type / class-name branching (ADR-0025 §3.2).

FIRE-AND-FORGET CONTRACT (AC-F9-2):
  The caller (orchestrator post-write hook) wraps the entire call in try/except and NEVER
  allows any exception here to propagate into the ingest critical path. The page is already
  written; the queue is advisory.

Module scope:
  enqueue_review(...)         — DB write only; called from the orchestrator hook.
  generate_review_queries(...)— exactly one bounded provider call; returns str|None.
  list_queue(...)             — paginated read for GET /review/queue.
  approve(item_id)            — status write only (NO re-ingest, AC-F9-6, I1).
  skip(item_id)               — status write.
  deep_research(item_id)      — delegates to POST /research/start seam; stores run_id.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from app.config import settings
from app.db import get_session
from app.models import ReviewItem

if TYPE_CHECKING:
    from app.ingest.provider.base import InferenceProvider
    from app.ingest.schemas import Message

# char/4 token heuristic (same convention as chat/stream.py) — used only to bound the
# single review-query generation call at the orchestration layer (token_budget is NEVER
# passed into provider.chat(), whose signature is locked: chat(messages, retrieval_context)).
_CHARS_PER_TOKEN = 4

logger = logging.getLogger(__name__)

# ── I7 bounds for the single query-gen call ────────────────────────────────────
# Read from env at call time (Settings singleton); defaults used when env is absent.
_DEFAULT_QUERY_TIMEOUT_SECONDS = 30
_DEFAULT_QUERY_TOKEN_BUDGET = 2_000


def _query_timeout() -> float:
    """Timeout for the single query-gen provider call (I7). From REVIEW_QUERY_TIMEOUT_SECONDS."""
    return float(getattr(settings, "review_query_timeout_seconds", _DEFAULT_QUERY_TIMEOUT_SECONDS))


def _query_token_budget() -> int:
    """Token budget for the single query-gen call (I7). From REVIEW_QUERY_TOKEN_BUDGET."""
    return int(getattr(settings, "review_query_token_budget", _DEFAULT_QUERY_TOKEN_BUDGET))


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


# ── Core operations ────────────────────────────────────────────────────────────


async def enqueue_review(
    *,
    vault_id: str,
    page_id: uuid.UUID | None,
    item_type: Literal["new_page", "update_page", "deep_research_candidate"],
    pre_generated_query: str | None = None,
) -> ReviewItem:
    """
    Insert one pending review_items row (ADR-0025 §3.2).

    Pure DB write — never calls a provider (the provider call, if any, was already made by
    generate_review_queries before calling this). Idempotency is NOT required: the queue is
    an event log, not a per-page singleton (ADR-0025 §3.1 note).

    page_id is stored as a string UUID for SQLite/Postgres compat (with_variant pattern).
    """
    item_id = uuid.uuid4()
    item_id_str = str(item_id)
    page_id_str = str(page_id) if page_id is not None else None

    async with get_session() as session:
        item = ReviewItem(
            id=item_id_str,
            vault_id=vault_id,
            page_id=page_id_str,
            item_type=item_type,
            status="pending",
            pre_generated_query=pre_generated_query,
            deep_research_run_id=None,
            created_at=datetime.now(UTC),
            reviewed_at=None,
            reviewed_by=None,
        )
        session.add(item)
        await session.flush()
        # Re-load so the returned object is detached from the session
        from sqlalchemy import select

        row = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        loaded = row.scalar_one()
        session.expunge(loaded)

    logger.debug(
        "enqueue_review: item_id=%s type=%s vault=%s page_id=%s query_len=%s",
        item_id_str,
        item_type,
        vault_id,
        page_id_str,
        len(pre_generated_query) if pre_generated_query else None,
    )
    return loaded


async def generate_review_queries(
    *,
    vault_id: str,
    page_title: str,
    page_excerpt: str,
) -> str | None:
    """
    Make EXACTLY ONE InferenceProvider.chat() call to produce 1–3 follow-up research questions.

    Returns the questions as a newline-separated string, or None on any failure.

    INVARIANT GUARANTEES (I7 + ADR-0025 §3.2):
      - Exactly ONE call; no loop, no retry.
      - Wrapped in asyncio.wait_for(timeout=_query_timeout()).
      - token_budget from the resolved provider row (or _query_token_budget() default).
      - On ConfigNotFoundError / timeout / any provider error → returns None (item still
        enqueued with pre_generated_query=NULL by the caller).
      - Cost is logged via the provider's accumulator (I7 audit trail).

    INVARIANT GUARANTEES (I6):
      - Resolves via resolve_provider_config("ingest") only.
      - Routes by capabilities — no isinstance/type/class-name check.
    """
    from app.ingest.provider import resolve_provider
    from app.ingest.provider.base import UsageAccumulator
    from app.ingest.schemas import Message
    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    # Resolve the provider (operation='ingest', I6)
    try:
        provider_cfg = await resolve_provider_config("ingest", vault_id)
    except ConfigNotFoundError:
        logger.debug(
            "generate_review_queries: no ingest provider configured for vault=%s — returning None",
            vault_id,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_review_queries: provider resolution failed for vault=%s: %s — returning None",
            vault_id,
            exc,
        )
        return None

    provider = resolve_provider(provider_cfg)
    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)

    # Build the single prompt (concise; bounded by token_budget)
    token_budget = int(getattr(provider_cfg, "token_budget", None) or _query_token_budget())
    # Keep the prompt well under token_budget — use a small fixed cap
    excerpt_cap = min(len(page_excerpt), 800)
    prompt = (
        f"You are a research assistant. A new wiki page titled '{page_title}' was just created. "
        f"Based on this excerpt:\n\n{page_excerpt[:excerpt_cap]}\n\n"
        "Generate 1 to 3 concise follow-up research questions that would deepen understanding "
        "of this topic. Output ONLY the questions, one per line, no numbering, no preamble."
    )
    messages = [Message(role="user", content=prompt)]

    # Single bounded call (I7)
    timeout = _query_timeout()
    try:
        result_text = await asyncio.wait_for(
            _single_chat_call(provider, messages, token_budget),
            timeout=timeout,
        )
    except TimeoutError:
        logger.warning(
            "generate_review_queries: provider call timed out after %.1fs for page=%r — "
            "returning None (item will be enqueued with NULL query)",
            timeout,
            page_title,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_review_queries: provider call failed for page=%r: %s — "
            "returning None (item will be enqueued with NULL query)",
            page_title,
            exc,
        )
        return None

    # Log cost (I7)
    cost = round(accumulator.total_cost_usd, 4)
    logger.info(
        "generate_review_queries: page=%r tokens=%d cost_usd=%.4f",
        page_title,
        accumulator.total_tokens,
        cost,
    )

    # Sanitize: strip empty lines, take up to 3
    lines = [ln.strip() for ln in (result_text or "").splitlines() if ln.strip()]
    if not lines:
        return None
    return "\n".join(lines[:3])


async def _single_chat_call(
    provider: InferenceProvider,
    messages: list[Message],
    token_budget: int,
) -> str:
    """
    Consume provider.chat() once and return the concatenated text.

    Calls chat() with its LOCKED signature — chat(messages, retrieval_context) — never passing
    token_budget into it (I6, ADR-0025 Do-NOT). token_budget is enforced HERE at the
    orchestration layer by capping accumulated output (char/4). Supports both provider shapes
    (an async-generator fn OR a coroutine returning one), exactly like chat/stream.py.

    The caller (generate_review_queries) wraps this whole call in asyncio.wait_for(timeout),
    so the timeout covers network latency, not just the first token.
    """
    budget_chars = max(0, token_budget) * _CHARS_PER_TOKEN
    chunks: list[str] = []
    total = 0
    maybe = provider.chat(messages, "")
    agen = await maybe if inspect.isawaitable(maybe) else maybe
    async for delta in agen:
        if not delta:
            continue
        chunks.append(delta)
        total += len(delta)
        if budget_chars and total >= budget_chars:
            aclose = getattr(agen, "aclose", None)
            if aclose is not None:
                await aclose()
            break
    return "".join(chunks)


# ── REST action helpers ────────────────────────────────────────────────────────


async def list_queue(
    vault_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> ReviewQueuePage:
    """
    Return a paginated ReviewQueuePage for GET /review/queue (ADR-0025 §3.5).

    Queries all statuses (not just pending) so the UI can show the full queue.
    Ordered by created_at ASC.
    limit is capped at 200 by the REST endpoint (I7 — bounded page size).
    """
    from sqlalchemy import func, select

    async with get_session() as session:
        count_stmt = (
            select(func.count()).select_from(ReviewItem).where(ReviewItem.vault_id == vault_id)
        )
        total: int = (await session.execute(count_stmt)).scalar_one()

        data_stmt = (
            select(ReviewItem)
            .where(ReviewItem.vault_id == vault_id)
            .order_by(ReviewItem.created_at.asc())
            .offset(offset)
            .limit(limit)
        )
        rows = list((await session.execute(data_stmt)).scalars().all())
        for r in rows:
            session.expunge(r)

    return ReviewQueuePage(items=rows, total=total, limit=limit, offset=offset)


async def approve(item_id: uuid.UUID) -> ReviewItem:
    """
    Set status=approved, reviewed_at=now() (ADR-0025 §3.5).

    Status write ONLY — does NOT re-trigger ingest (AC-F9-6, I1).
    404 if the item is not found.
    """
    return await _set_status(item_id, "approved")


async def skip(item_id: uuid.UUID) -> ReviewItem:
    """Set status=skipped, reviewed_at=now() (ADR-0025 §3.5)."""
    return await _set_status(item_id, "skipped")


async def deep_research(
    item_id: uuid.UUID,
    *,
    vault_id: str | None = None,
) -> DeepResearchResult:
    """
    Deep-research action (ADR-0025 §3.5, AC-F9-3, AC-F10-5).

    1. Load the review item (404 if absent).
    2. Extract the topic: first line of pre_generated_query, or the page title (from a pages
       join), or the item_id string as fallback.
    3. Call POST /research/start (reuse the internal seam: run_deep_research via
       the same background-task pattern used by the REST endpoint).
    4. Set status=deep_researched, reviewed_at=now(), deep_research_run_id=run_id.
    5. Return DeepResearchResult(review_item_id, run_id).

    503 if SEARXNG_URL is unset (inherits F10's guard, ADR-0025 §3.5).
    """
    from sqlalchemy import select

    # 503 guard (I9 — no fake run, no fallback engine)
    if not settings.searxng_url:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail=("SEARXNG_URL is not configured. Set SEARXNG_URL to enable deep research (I9)."),
        )

    item_id_str = str(item_id)

    async with get_session() as session:
        row = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item = row.scalar_one_or_none()
        if item is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        # Extract topic: first line of pre_generated_query OR page title OR fallback
        topic: str
        if item.pre_generated_query:
            first_line = item.pre_generated_query.splitlines()[0].strip()
            topic = first_line if first_line else f"Review: {item_id}"
        else:
            # Try to load the page title
            if item.page_id:
                from app.models import Page

                pg_row = await session.execute(select(Page).where(Page.id == item.page_id))
                pg = pg_row.scalar_one_or_none()
                topic = pg.title if (pg and pg.title) else f"Review: {item_id}"
            else:
                topic = f"Review: {item_id}"

        effective_vault_id = vault_id or item.vault_id

    # Delegate to deep_research seam (same as POST /research/start)
    from app.ops.deep_research import run_deep_research

    run_id = uuid.uuid4()
    run_id_str = str(run_id)
    frozen_max_iter = settings.deep_research_max_iter
    frozen_token_budget = settings.deep_research_token_budget

    # Pre-INSERT the run row (fire-and-poll pattern, same as research_start endpoint)
    from app.models import DeepResearchRun

    async with get_session() as session:
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
    async with get_session() as session:
        row2 = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item2 = row2.scalar_one_or_none()
        if item2 is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        item2.status = "deep_researched"
        item2.reviewed_at = datetime.now(UTC)
        item2.reviewed_by = "web-ui"
        # Store the string form: the column is UUID(as_uuid=True).with_variant(String(36),
        # "sqlite") — Postgres accepts the str, and SQLite cannot bind a raw UUID object.
        item2.deep_research_run_id = run_id_str  # type: ignore[assignment]

        await session.flush()
        await session.refresh(item2)
        session.expunge(item2)

    # Schedule the background task (fire-and-poll, same as research_start endpoint)
    asyncio.create_task(
        run_deep_research(
            vault_id=effective_vault_id,
            topic=topic,
            max_iter=frozen_max_iter,
            token_budget=frozen_token_budget,
            run_id=run_id,
        )
    )

    logger.info(
        "deep_research action: review_item_id=%s → run_id=%s vault=%s topic=%r",
        item_id_str,
        run_id_str,
        effective_vault_id,
        topic,
    )
    return DeepResearchResult(review_item_id=item_id, run_id=run_id)


# ── Private helpers ────────────────────────────────────────────────────────────


async def _set_status(
    item_id: uuid.UUID,
    status: Literal["approved", "skipped", "deep_researched"],
) -> ReviewItem:
    """Update status + reviewed_at on a review item. 404 if not found."""
    from sqlalchemy import select

    item_id_str = str(item_id)

    async with get_session() as session:
        row = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item = row.scalar_one_or_none()
        if item is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        item.status = status
        item.reviewed_at = datetime.now(UTC)
        item.reviewed_by = "web-ui"

        await session.flush()
        await session.refresh(item)
        session.expunge(item)

    return item
