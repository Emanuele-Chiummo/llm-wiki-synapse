"""
F9 HITL Review Queue — proposal model (ADR-0034, supersedes ADR-0025 F9 parts).

ARCHITECTURE OVERVIEW (ADR-0034 §2):
  Rows are PROPOSALS for follow-up work — NOT confirmations of auto-created pages.
  Five proposal types: missing-page | suggestion | contradiction | duplicate | confirm.
  Pages are created on-demand ONLY when the human clicks Create (lazy generation, §5).

KEY CONTRACTS:

  enqueue_review(...)        — pure DB write for one proposal row; no provider call.
  propose_reviews(...)       — orchestration entry point (called from run_ingest_pipeline):
                               rule-based missing-page/duplicate detection, then
                               _llm_propose_reviews for LLM proposals.
  sweep_reviews(vault_id)    — auto-resolution sweep: Pass-1 (rule-based) + Pass-2 (conservative LLM).
  create_page_from_review(item_id) — lazy on-demand Create handler [AI seam for generation].
  list_queue(...)            — paginated read for GET /review/queue.
  skip(item_id)              — status write → skipped.
  deep_research(item_id)     — delegates to F10; stores run_id.

AI SEAMS (implemented — ADR-0034 §11.2):
  _llm_propose_reviews(...)  — single bounded InferenceProvider call for LLM proposals.
  _llm_sweep_judge(...)      — single bounded conservative LLM pass for sweep Pass-2.
  _run_generation(...)       — bounded run_orchestrated_loop invocation for Create.

I7 CONTRACT (fire-and-forget wrappers in orchestrator — not here):
  propose_reviews() and sweep_reviews() NEVER raise into the ingest critical path.
  The orchestrator wraps them in try/except (Do-NOT #5, ADR-0034 §10).

I6 CONTRACT (all LLM calls route through resolve_provider_config — no hardcoded backend):
  No isinstance / provider_type / class-name branching anywhere in this module.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import func, select

from app.config import settings
from app.db import get_session
from app.ingest.schemas import PageType
from app.models import Page, ReviewItem

if TYPE_CHECKING:
    from app.ingest.schemas import Analysis, WikiPage

logger = logging.getLogger(__name__)

# ── Accepted value sets (app-side enum-by-convention, no DB CHECK — ADR-0034 §3.1) ──
_VALID_ITEM_TYPES = frozenset(
    {"missing-page", "suggestion", "contradiction", "duplicate", "confirm"}
)
_VALID_STATUSES = frozenset(
    {"pending", "created", "skipped", "deep_researched", "auto_resolved"}
)
_VALID_RESOLUTIONS = frozenset(
    {"created", "skipped", "researched", "rule_resolved", "llm_resolved"}
)

# Caps (I7 — bounded reads/lists)
_SWEEP_PASS1_MAX_ITEMS: int = 200  # max pending items processed per sweep Pass-1
_PROPOSE_MAX_ITEMS: int = 8  # max proposals emitted per run (ADR-0034 §4.3)


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
class SweepResult:
    """Result of a sweep_reviews() run."""

    rule_resolved: int
    llm_resolved: int
    kept: int


# ── Proposal DTO (LLM call contract — ADR-0034 §4.3) ────────────────────────


@dataclass
class ProposalDTO:
    """
    Structured proposal returned by _llm_propose_reviews().

    Fields mirror the review_items columns (ADR-0034 §3.1).
    target_page_title: for contradiction/duplicate, the existing page title in conflict.
    """

    item_type: Literal["missing-page", "suggestion", "contradiction", "duplicate", "confirm"]
    proposed_title: str | None
    proposed_page_type: str | None  # entity|concept|source|synthesis|comparison|None
    rationale: str | None
    target_page_title: str | None = None  # resolved to page_id at enqueue time


# ── AI seam implementations (ADR-0034 §11.2) ─────────────────────────────────


async def _llm_propose_reviews(
    *,
    vault_id: str,
    analysis: Analysis,
    written_pages: list[Page],
    existing_titles: list[str],
) -> list[ProposalDTO]:
    """
    Single bounded provider call (ADR-0034 §4.3, implemented).

    Makes AT MOST ONE InferenceProvider call (operation "ingest", resolved via
    resolve_provider_config("ingest", vault_id) — I6) that, given:
      - analysis (topics, entities, suggested_pages, summary)
      - a compact digest of the written pages (title + short excerpt)
      - the list of existing_titles in the vault (bounded, no full content)
    returns a structured list of ProposalDTO proposals (≤ _PROPOSE_MAX_ITEMS).

    Bounds (I7):
      - Exactly ONE call; no loop; no retry.
      - asyncio.wait_for(REVIEW_PROPOSE_TIMEOUT_SECONDS).
      - Output capped at _PROPOSE_MAX_ITEMS (truncate; never emit unbounded list).
      - token_budget from the resolved row (or REVIEW_PROPOSE_TOKEN_BUDGET default).
      - Cost pushed through UsageAccumulator; logged (total_cost_usd).
      - On ConfigNotFoundError / timeout / any failure → return [] (log WARNING, never raise).
        The rule-based proposals (if any) will still be emitted by the caller.

    Returns:
      List of ProposalDTO (0..N, capped at _PROPOSE_MAX_ITEMS).
    """
    # ── Resolve provider (I6 — never hardcode; "no provider" → []) ───────────────
    resolved = await _resolve_review_provider(vault_id)
    if resolved is None:
        logger.debug(
            "_llm_propose_reviews: no ingest provider resolved (vault=%s) — "
            "rule-based proposals only (I6: no silent default)",
            vault_id,
        )
        return []
    provider, config_row = resolved

    max_items = int(getattr(settings, "review_propose_max_items", _PROPOSE_MAX_ITEMS))
    token_budget = _coerce_token_budget(
        getattr(config_row, "token_budget", None),
        int(getattr(settings, "review_propose_token_budget", 4_000)),
    )
    timeout_s = float(getattr(settings, "review_propose_timeout_seconds", 30.0))

    # ── Bind a run-scoped Usage ledger (I7 — cost logged out of band) ─────────────
    from app.ingest.provider.base import UsageAccumulator

    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)

    instruction = _build_propose_instruction(
        analysis=analysis,
        written_pages=written_pages,
        existing_titles=existing_titles,
        max_items=max_items,
        token_budget=token_budget,
    )

    # ── ONE bounded call, no loop, no retry (I7) ─────────────────────────────────
    try:
        raw = await asyncio.wait_for(
            _chat_collect(provider, instruction), timeout=timeout_s
        )
    except TimeoutError:
        logger.warning(
            "_llm_propose_reviews: provider call timed out after %.1fs (vault=%s) — "
            "emitting rule-based proposals only (degrade, never fail ingest)",
            timeout_s,
            vault_id,
        )
        return []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_llm_propose_reviews: provider call failed (vault=%s): %s — "
            "rule-based proposals only",
            vault_id,
            exc,
        )
        return []
    finally:
        # I7: cost logged per call regardless of outcome (truthful ledger).
        logger.info(
            "review_propose provider call: vault=%s tokens=%d cost_usd=%.4f calls=%d",
            vault_id,
            accumulator.total_tokens,
            round(accumulator.total_cost_usd, 4),
            accumulator.calls,
        )

    proposals = _parse_proposals(raw)
    if not proposals:
        logger.debug(
            "_llm_propose_reviews: provider returned no parseable proposals (vault=%s)", vault_id
        )
    # Hard cap (Do-NOT #9): truncate; never emit an unbounded list.
    return proposals[:max_items]


async def _llm_sweep_judge(
    *,
    vault_id: str,
    candidate_items: list[ReviewItem],
    existing_titles: list[str | None],
) -> set[str]:
    """
    Conservative bounded LLM pass, default-to-keep (ADR-0034 §6.3, implemented).

    Makes AT MOST ONE InferenceProvider call (operation "ingest", resolved via
    resolve_provider_config("ingest", vault_id) — I6) batching the candidate_items
    (capped at REVIEW_SWEEP_LLM_MAX_ITEMS). Given each item's rationale/proposed_title
    and the current vault existing_titles list (+ for contradictions: the conflicting
    page's content digest), return a set of item ID STRINGS to resolve.

    CONSERVATIVE BIAS (ADR-0034 §6.3 / Do-NOT #7):
      - Prompt instructs: "only resolve if you are confident the concern no longer applies;
        otherwise keep pending."
      - Any parse ambiguity, timeout, or provider error → return set() (keep all pending).
        NEVER auto-close on uncertainty.
      - `confirm` items MUST NOT appear in the returned set (never auto-resolve confirm).
      - `suggestion` and `contradiction` MAY be resolved only with high confidence.

    Bounds (I7):
      - Exactly ONE call; batched; capped at REVIEW_SWEEP_LLM_MAX_ITEMS.
      - asyncio.wait_for(REVIEW_SWEEP_TIMEOUT_SECONDS).
      - token_budget from the resolved row; cost logged.
      - On ConfigNotFoundError / timeout / any failure → return set() (default-to-keep).
      - REVIEW_SWEEP_LLM_ENABLED=false → caller does not invoke this; return set() anyway.

    Returns:
      Set of item id STRINGS (str(uuid)) to auto-resolve.
      Empty set = keep all pending (the safe default).
    """
    # Gate (defensive — caller also checks): if disabled, keep all pending.
    if not bool(getattr(settings, "review_sweep_llm_enabled", True)):
        return set()

    # Filter out confirm BEFORE the call (Do-NOT #7 — never even ask about confirm).
    judgeable = [it for it in candidate_items if it.item_type != "confirm"]
    if not judgeable:
        return set()

    max_items = int(getattr(settings, "review_sweep_llm_max_items", 8))
    judgeable = judgeable[:max_items]

    # ── Resolve provider (I6 — "no provider" → keep all pending) ─────────────────
    resolved = await _resolve_review_provider(vault_id)
    if resolved is None:
        logger.debug(
            "_llm_sweep_judge: no ingest provider resolved (vault=%s) — keep all pending (I6)",
            vault_id,
        )
        return set()
    provider, config_row = resolved

    token_budget = _coerce_token_budget(
        getattr(config_row, "token_budget", None),
        int(getattr(settings, "review_sweep_llm_token_budget", 4_000)),
    )
    timeout_s = float(getattr(settings, "review_sweep_timeout_seconds", 30.0))

    from app.ingest.provider.base import UsageAccumulator

    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)

    # id → item for safe verdict mapping (we map the model's verdicts back by exact id).
    by_id: dict[str, ReviewItem] = {str(it.id): it for it in judgeable}
    instruction = _build_sweep_instruction(
        judgeable=judgeable,
        existing_titles=existing_titles,
        token_budget=token_budget,
    )

    # ── ONE bounded batched call, no loop (I7) ───────────────────────────────────
    try:
        raw = await asyncio.wait_for(
            _chat_collect(provider, instruction), timeout=timeout_s
        )
    except TimeoutError:
        logger.warning(
            "_llm_sweep_judge: provider call timed out after %.1fs (vault=%s) — "
            "keep all pending (default-to-keep, Do-NOT #7)",
            timeout_s,
            vault_id,
        )
        return set()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_llm_sweep_judge: provider call failed (vault=%s): %s — keep all pending",
            vault_id,
            exc,
        )
        return set()
    finally:
        logger.info(
            "review_sweep provider call: vault=%s tokens=%d cost_usd=%.4f calls=%d",
            vault_id,
            accumulator.total_tokens,
            round(accumulator.total_cost_usd, 4),
            accumulator.calls,
        )

    resolve_ids = _parse_sweep_verdicts(raw, by_id)
    # Final safety net: never return a confirm id (defence-in-depth, Do-NOT #7).
    return {
        item_id
        for item_id in resolve_ids
        if item_id in by_id and by_id[item_id].item_type != "confirm"
    }


async def _run_generation(
    *,
    vault_id: str,
    proposed_title: str,
    proposed_page_type: str | None,
    rationale: str | None,
    origin_source: str,
    provider_config_row: object,
) -> WikiPage:
    """
    Bounded run_orchestrated_loop on-demand for lazy Create (ADR-0034 §5, implemented).

    Runs the bounded orchestrated loop (ingest/loop.py::run_orchestrated_loop) with a
    single-page-target prompt: "generate the wiki page titled <proposed_title> of type
    <resolved_type>, grounded in the vault context + the proposal rationale."

    Skeleton type resolution (§5.2, done here or by caller):
      1. Use proposed_page_type if set and != 'source'.
      2. Otherwise apply heuristic over title + rationale:
         - comparison cues ("vs", "versus", "compared") → comparison
         - synthesis cues ("overview of", "summary of", "survey") → synthesis
         - proper-noun / named-entity shape → entity
         - default → concept
      'source' is reserved for ingested raw documents; Create NEVER produces a source page.

    Bounds (I7):
      - max_iter + token_budget from provider_config_row (I7).
      - Wrapped in asyncio.wait_for(timeout).
      - An ingest_runs row records tokens + total_cost_usd + the $1 anomaly check
        (reusing the existing finalize path — same as any other orchestrated run).
      - On loop failure / provider error → raise (the caller handles → 502; item stays pending).

    Returns:
      WikiPage (the produced page; caller writes it via write_wiki_page).
      Or raises on failure (caller converts to 502; item left pending — no partial write).
    """
    from app.ingest.loop import run_orchestrated_loop
    from app.ingest.orchestrator import (
        COST_ANOMALY_THRESHOLD_USD,
        _ensure_source_summary,
        _load_vault_context,
        _write_ingest_run,
    )
    from app.ingest.provider import resolve_provider
    from app.ingest.provider.base import UsageAccumulator

    # ── Resolve type / dir heuristic (§5.2) ──────────────────────────────────────
    resolved_type = _resolve_create_page_type(proposed_title, proposed_page_type, rationale)

    # ── Build the provider + run-scoped ledger ───────────────────────────────────
    provider = resolve_provider(provider_config_row)
    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)
    caps = provider.capabilities()

    # Bounds (I7) from the resolved row.
    max_iter = int(getattr(provider_config_row, "max_iter", None) or 3)
    token_budget = int(getattr(provider_config_row, "token_budget", None) or 60_000)
    timeout_s = float(getattr(settings, "review_propose_timeout_seconds", 30.0)) * max(
        1, max_iter
    )

    vault_context = _load_vault_context()
    # The single-page-target prompt is delivered through the source_text channel of the
    # bounded loop (analyze→generate→validate), so the produced page is grounded in the
    # vault context + the proposal rationale (§5).
    rationale_text = (rationale or "").strip() or "(no additional rationale provided)"
    source_text = (
        f"Create a single wiki page titled {proposed_title!r} of type "
        f"{resolved_type.value!r}.\n\n"
        f"Why this page is needed (proposal rationale):\n{rationale_text}\n\n"
        "Ground the page in the vault context. Produce exactly one schema-valid page for "
        f"the title above; cite {origin_source!r} in its frontmatter sources[] (F3)."
    )

    started_at = datetime.now(UTC)
    converged = False
    iterations = 0
    wiki_page: WikiPage | None = None
    error: BaseException | None = None

    try:
        loop_result = await asyncio.wait_for(
            run_orchestrated_loop(
                provider=provider,
                accumulator=accumulator,
                source_text=source_text,
                vault_context=vault_context,
                retrieval_context="",
                origin_source=origin_source,
                max_iter=max_iter,
                token_budget=token_budget,
            ),
            timeout=timeout_s,
        )
        converged = loop_result.converged
        iterations = loop_result.iterations
        # _ensure_source_summary guarantees a valid WikiPage even on non-convergence (§5).
        pages = _ensure_source_summary(
            loop_result.pages, loop_result.analysis, origin_source
        )
        wiki_page = pages[0] if pages else None
    except TimeoutError as exc:
        error = exc
    except Exception as exc:  # noqa: BLE001
        error = exc

    finished_at = datetime.now(UTC)
    total_tokens = accumulator.total_tokens
    total_cost_usd = round(accumulator.total_cost_usd, 4)
    cost_anomaly = total_cost_usd > COST_ANOMALY_THRESHOLD_USD

    # ── Record ONE ingest_runs row (route='orchestrated') — reuse the finalize path ─
    try:
        await _write_ingest_run(
            page_id=None,
            provider_name=caps.name,
            provider_type=caps.mode,
            model_id=str(getattr(provider_config_row, "model_id", "")),
            route="orchestrated",
            max_iter_used=iterations,
            total_tokens=total_tokens,
            total_cost_usd=total_cost_usd,
            converged=converged,
            cost_anomaly=cost_anomaly,
            started_at=started_at,
            finished_at=finished_at,
            pages_created=1 if (error is None and wiki_page is not None) else 0,
            error_message=(str(error) or error.__class__.__name__) if error is not None else None,
        )
    except Exception as run_exc:  # noqa: BLE001
        # Audit-row write failing must not mask the (success/failure) outcome.
        logger.warning(
            "_run_generation: ingest_runs audit write failed (non-fatal): %s", run_exc
        )

    logger.info(
        "review_create run: provider=%s route=orchestrated converged=%s tokens=%d "
        "cost_usd=%.4f title=%r",
        caps.name,
        converged,
        total_tokens,
        total_cost_usd,
        proposed_title,
    )
    # Inline $1 cost-anomaly WARNING (AQ-v0.2-8), same as the orchestrator.
    if cost_anomaly:
        logger.warning(
            "COST ANOMALY: review Create run total_cost_usd=%.4f exceeds $%.2f "
            "(provider=%s title=%r) — investigate runaway/misconfiguration",
            total_cost_usd,
            COST_ANOMALY_THRESHOLD_USD,
            caps.name,
            proposed_title,
        )

    # ── Failure → raise (caller → 502, item left pending; no partial write, §5.3) ─
    if error is not None:
        raise error
    if wiki_page is None:
        raise RuntimeError(
            "orchestrated loop produced no page and no fallback (unexpected — §5)"
        )
    return wiki_page


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
) -> ReviewItem:
    """
    Insert one pending review_items proposal row (ADR-0034 §3.2).

    Pure DB write — NEVER calls a provider (fire-and-forget from propose_reviews,
    which is itself called fire-and-forget from the orchestrator).

    item_type must be one of: missing-page | suggestion | contradiction | duplicate | confirm.
    Idempotency is NOT required: the queue is an event log, not a per-page singleton
    (ADR-0034 §3.2 / ADR-0025 §3.1 note).

    page_id / source_page_id / created_page_id are stored as string UUIDs for
    SQLite/Postgres compat (with_variant pattern).
    """
    item_id = uuid.uuid4()
    item_id_str = str(item_id)
    page_id_str = str(page_id) if page_id is not None else None
    source_page_id_str = str(source_page_id) if source_page_id is not None else None

    async with get_session() as session:
        item = ReviewItem(
            id=item_id_str,
            vault_id=vault_id,
            item_type=item_type,
            status="pending",
            page_id=page_id_str,
            source_page_id=source_page_id_str,
            proposed_title=proposed_title,
            proposed_page_type=proposed_page_type,
            proposed_dir=proposed_dir,
            rationale=rationale,
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
        "enqueue_review: item_id=%s type=%s vault=%s proposed_title=%r",
        item_id_str,
        item_type,
        vault_id,
        proposed_title,
    )
    return loaded


async def propose_reviews(
    *,
    vault_id: str,
    analysis: Analysis,
    written_pages: list[Page],
    origin_source: str,
) -> None:
    """
    Run the proposal emission stage once per orchestrated ingest run (ADR-0034 §4).

    Replaces _enqueue_review_items + generate_review_queries from ADR-0025.
    Called FIRE-AND-FORGET from run_ingest_pipeline after _update_overview.
    Exceptions must be caught by the caller — NEVER propagate into ingest (Do-NOT #5).

    Pass 1 — rule-based (no LLM, deterministic, I1):
      Detects dangling wikilinks and not-written suggested_pages → emits missing-page
      proposals directly. No provider call, no cost.

    Pass 2 — LLM call (1 bounded call, I6/I7):
      Anti-spam gate first (ADR-0034 §4.2): only runs if generation was substantial
      OR there is at least one dangling-link signal.
      Calls _llm_propose_reviews (implemented, ADR-0034 §4.3).
      On gate failure or provider failure → zero LLM proposals (rule-based only).

    Total proposals are capped at _PROPOSE_MAX_ITEMS across both passes.
    """
    if not written_pages:
        logger.debug("propose_reviews: no written pages; skipping (vault=%s)", vault_id)
        return

    # ── Rule-based: dangling wikilinks → missing-page ─────────────────────────
    rule_proposals: list[ProposalDTO] = []

    # Find dangling wikilinks for the written pages (bounded indexed read — I1/I2)
    written_page_ids = [str(p.id) for p in written_pages]
    dangling_targets: set[str] = set()
    try:
        from app.models import Link

        async with get_session() as session:
            dangling_stmt = (
                select(Link.target_title, Link.source_page_id)
                .where(
                    Link.source_page_id.in_(written_page_ids),
                    Link.dangling.is_(True),
                )
                .limit(_SWEEP_PASS1_MAX_ITEMS)
            )
            rows = list((await session.execute(dangling_stmt)).all())
            dangling_targets = {r.target_title for r in rows}

            # Get the provenance (first written page or None)
            source_page_id = written_pages[0].id if written_pages else None

        for target_title in dangling_targets:
            # Check if a page with this title already exists (bounded indexed read)
            async with get_session() as session:
                existing = (
                    await session.execute(
                        select(Page.id)
                        .where(
                            Page.vault_id == vault_id,
                            Page.title == target_title,
                            Page.deleted_at.is_(None),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()
            if existing is None:
                rule_proposals.append(
                    ProposalDTO(
                        item_type="missing-page",
                        proposed_title=target_title,
                        proposed_page_type=None,  # heuristic at Create time
                        rationale=f"Dangling wikilink [[{target_title}]] in ingested content.",
                        target_page_title=None,
                    )
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "propose_reviews: dangling-link detection failed (non-fatal): %s", exc
        )

    # ── Rule-based: not-written suggested_pages → missing-page ───────────────
    if analysis is not None:
        written_titles_lc = {(p.title or "").lower().strip() for p in written_pages}
        for suggested in (analysis.suggested_pages or []):
            if suggested.title.lower().strip() not in written_titles_lc:
                # Suggested but not written → explicit missing-page signal
                already = any(
                    p.proposed_title == suggested.title for p in rule_proposals
                )
                if not already:
                    rule_proposals.append(
                        ProposalDTO(
                            item_type="missing-page",
                            proposed_title=suggested.title,
                            proposed_page_type=str(suggested.type) if suggested.type else None,
                            rationale=(
                                suggested.rationale
                                or (
                                    f"Analysis proposed '{suggested.title}'"
                                    " but it was not generated."
                                )
                            ),
                            target_page_title=None,
                        )
                    )

    # ── Anti-spam gate (ADR-0034 §4.2) ───────────────────────────────────────
    total_chars = sum(
        len(p.title or "") for p in written_pages
    )  # approximate; real content is on disk
    spam_gate_passes = (
        len(written_pages) >= int(getattr(settings, "review_propose_min_pages", 4))
        or total_chars >= int(getattr(settings, "review_propose_min_chars", 10_000))
        or bool(dangling_targets)
        or bool(
            analysis is not None
            and any(
                (s.title or "").lower() not in {(p.title or "").lower() for p in written_pages}
                for s in (analysis.suggested_pages or [])
            )
        )
    )

    # ── LLM call (only if gate passes) ───────────────────────────────────────
    llm_proposals: list[ProposalDTO] = []
    if spam_gate_passes:
        try:
            # Load bounded title list for the vault (no full content — I1)
            async with get_session() as session:
                title_rows = list(
                    (
                        await session.execute(
                            select(Page.title)
                            .where(
                                Page.vault_id == vault_id,
                                Page.deleted_at.is_(None),
                                Page.title.isnot(None),
                            )
                            .limit(500)
                        )
                    ).scalars()
                )
            existing_titles = [t for t in title_rows if t]

            llm_proposals = await _llm_propose_reviews(
                vault_id=vault_id,
                analysis=analysis,
                written_pages=written_pages,
                existing_titles=existing_titles,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "propose_reviews: LLM proposal call failed (non-fatal): %s — "
                "emitting rule-based proposals only",
                exc,
            )
            llm_proposals = []

    # ── Merge and cap ─────────────────────────────────────────────────────────
    all_proposals = (rule_proposals + llm_proposals)[:_PROPOSE_MAX_ITEMS]

    if not all_proposals:
        logger.debug(
            "propose_reviews: no proposals to enqueue (vault=%s written=%d)",
            vault_id,
            len(written_pages),
        )
        return

    # ── Persist proposals ──────────────────────────────────────────────────────
    source_page_id = written_pages[0].id if written_pages else None

    for proposal in all_proposals:
        # For contradiction/duplicate, resolve target_page_title → page_id
        target_page_id: uuid.UUID | None = None
        if proposal.target_page_title:
            try:
                async with get_session() as session:
                    tgt_row = (
                        await session.execute(
                            select(Page.id)
                            .where(
                                Page.vault_id == vault_id,
                                Page.title == proposal.target_page_title,
                                Page.deleted_at.is_(None),
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                if tgt_row is not None:
                    target_page_id = uuid.UUID(str(tgt_row))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "propose_reviews: target_page lookup failed for %r: %s",
                    proposal.target_page_title,
                    exc,
                )

        # Compute proposed_dir from proposed_page_type (display only)
        proposed_dir: str | None = None
        if proposal.proposed_page_type:
            try:
                from app.ingest.schemas import PageType, type_subdir

                proposed_dir = type_subdir(PageType(proposal.proposed_page_type))
            except (ValueError, KeyError):
                pass

        try:
            await enqueue_review(
                vault_id=vault_id,
                item_type=proposal.item_type,
                proposed_title=proposal.proposed_title,
                proposed_page_type=proposal.proposed_page_type,
                proposed_dir=proposed_dir,
                rationale=proposal.rationale,
                source_page_id=(
                    uuid.UUID(str(source_page_id)) if source_page_id is not None else None
                ),
                page_id=target_page_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "propose_reviews: failed to enqueue proposal type=%s title=%r: %s",
                proposal.item_type,
                proposal.proposed_title,
                exc,
            )

    logger.info(
        "propose_reviews: vault=%s emitted=%d (rule=%d llm=%d)",
        vault_id,
        len(all_proposals),
        len(rule_proposals),
        len(llm_proposals),
    )


async def sweep_reviews(vault_id: str) -> SweepResult:
    """
    Auto-resolution sweep (ADR-0034 §6). Fire-and-forget from run_ingest_pipeline
    and after a successful Create. Also callable via POST /review/queue/sweep.

    NEVER raises (caller wraps in try/except, Do-NOT #5).

    Pass 1 — rule-based (deterministic, no LLM — ADR-0034 §6.2):
      For each pending missing-page or duplicate item (bounded read, capped at
      _SWEEP_PASS1_MAX_ITEMS), checks whether a live page now exists whose title
      matches proposed_title (case/whitespace-normalized).
      Resolves matches → status=auto_resolved, resolution=rule_resolved.
      contradiction / suggestion / confirm are NEVER touched by Pass 1 (Do-NOT #7).

    Pass 2 — conservative LLM sweep (optional — ADR-0034 §6.3):
      Batches the remaining pending items and calls _llm_sweep_judge.
      default-to-keep: any parse error / timeout / provider failure → keep all pending.
      confirm items are NEVER auto-resolved (Do-NOT #7).

    Returns SweepResult(rule_resolved, llm_resolved, kept).
    """
    rule_resolved = 0
    llm_resolved = 0

    # ── Pass 1: rule-based ─────────────────────────────────────────────────────
    try:
        async with get_session() as session:
            stmt = (
                select(ReviewItem)
                .where(
                    ReviewItem.vault_id == vault_id,
                    ReviewItem.status == "pending",
                    ReviewItem.item_type.in_(["missing-page", "duplicate"]),
                    ReviewItem.proposed_title.isnot(None),
                )
                .order_by(ReviewItem.created_at.asc())
                .limit(_SWEEP_PASS1_MAX_ITEMS)
            )
            candidate_rows = list((await session.execute(stmt)).scalars().all())

        for item in candidate_rows:
            if not item.proposed_title:
                continue
            normalized_title = _normalize_title(item.proposed_title)
            # Bounded indexed read: does a live page with this title now exist?
            async with get_session() as session:
                existing = (
                    await session.execute(
                        select(Page.id)
                        .where(
                            Page.vault_id == vault_id,
                            # CAST to TEXT for SQLite/Postgres compat (mirrors retrieval.py)
                            func.lower(func.trim(Page.title)) == normalized_title,
                            Page.deleted_at.is_(None),
                        )
                        .limit(1)
                    )
                ).scalar_one_or_none()

            if existing is not None:
                await _set_status(
                    uuid.UUID(str(item.id)),
                    "auto_resolved",
                    resolution="rule_resolved",
                    reviewed_by="auto-sweep",
                )
                rule_resolved += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("sweep_reviews: Pass-1 failed (non-fatal): %s", exc)

    # ── Pass 2: conservative LLM sweep ───────────────────────────────────────
    sweep_llm_enabled = bool(getattr(settings, "review_sweep_llm_enabled", True))
    if sweep_llm_enabled:
        try:
            async with get_session() as session:
                remaining_stmt = (
                    select(ReviewItem)
                    .where(
                        ReviewItem.vault_id == vault_id,
                        ReviewItem.status == "pending",
                        # Never pass confirm to LLM sweep (Do-NOT #7)
                        ReviewItem.item_type.in_(
                            ["missing-page", "duplicate", "suggestion", "contradiction"]
                        ),
                    )
                    .order_by(ReviewItem.created_at.asc())
                    .limit(int(getattr(settings, "review_sweep_llm_max_items", 8)))
                )
                remaining = list((await session.execute(remaining_stmt)).scalars().all())

            if remaining:
                async with get_session() as session:
                    existing_titles = list(
                        (
                            await session.execute(
                                select(Page.title)
                                .where(
                                    Page.vault_id == vault_id,
                                    Page.deleted_at.is_(None),
                                    Page.title.isnot(None),
                                )
                                .limit(500)
                            )
                        ).scalars()
                    )

                # Default-to-keep: _llm_sweep_judge returns set() on any failure (I7)
                ids_to_resolve = await _llm_sweep_judge(
                    vault_id=vault_id,
                    candidate_items=remaining,
                    existing_titles=existing_titles,
                )

                for item in remaining:
                    item_id_str = str(item.id)
                    if item_id_str in ids_to_resolve:
                        # Safety: never auto-resolve confirm (Do-NOT #7)
                        if item.item_type == "confirm":
                            logger.warning(
                                "sweep_reviews: LLM sweep tried to resolve a 'confirm' item "
                                "%s — blocked (Do-NOT #7)",
                                item_id_str,
                            )
                            continue
                        await _set_status(
                            uuid.UUID(item_id_str),
                            "auto_resolved",
                            resolution="llm_resolved",
                            reviewed_by="auto-sweep",
                        )
                        llm_resolved += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("sweep_reviews: Pass-2 failed (non-fatal): %s", exc)

    # Count kept (still pending after both passes)
    try:
        async with get_session() as session:
            kept_count = (
                await session.execute(
                    select(func.count())
                    .select_from(ReviewItem)
                    .where(
                        ReviewItem.vault_id == vault_id,
                        ReviewItem.status == "pending",
                    )
                )
            ).scalar_one()
    except Exception:  # noqa: BLE001
        kept_count = 0

    logger.info(
        "sweep_reviews: vault=%s rule_resolved=%d llm_resolved=%d kept=%d",
        vault_id,
        rule_resolved,
        llm_resolved,
        kept_count,
    )
    return SweepResult(rule_resolved=rule_resolved, llm_resolved=llm_resolved, kept=kept_count)


async def create_page_from_review(item_id: uuid.UUID) -> ReviewItem:
    """
    Lazy on-demand Create action (ADR-0034 §5).

    Flow:
      1. Load the review item (404 if absent; 409 if status != 'pending').
      2. Resolve the ingest provider (409 if none configured — I6).
      3. Call _run_generation (NotImplementedError → 502, item stays pending).
      4. Write the produced WikiPage via write_wiki_page (I1 — one data_version bump).
      5. Set status=created, resolution=created, created_page_id, reviewed_at, reviewed_by.
      6. Fire-and-forget sweep so sibling proposals that this page satisfies are closed.

    Returns the updated ReviewItem.

    Raises:
      HTTPException(404) — item not found.
      HTTPException(409) — item not pending, or no ingest provider configured (I6).
      HTTPException(502) — generation failed; item left pending (§5.3).
    """
    from fastapi import HTTPException

    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    item_id_str = str(item_id)

    # ── 1. Load item ─────────────────────────────────────────────────────────
    async with get_session() as session:
        row = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item = row.scalar_one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")
        if item.status != "pending":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Review item {item_id} has status={item.status!r}; "
                    "only pending items can be Created."
                ),
            )
        session.expunge(item)

    vault_id = item.vault_id

    # ── 2. Resolve provider (I6 — 409 if none configured) ────────────────────
    try:
        provider_config_row = await resolve_provider_config("ingest", vault_id)
    except ConfigNotFoundError as cnfe:
        raise HTTPException(
            status_code=409,
            detail=(
                "No ingest provider configured for this vault. "
                "Configure a provider before using the Create action (I6)."
            ),
        ) from cnfe
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=409,
            detail=f"Provider resolution failed: {exc}",
        ) from exc

    # ── 3. Derive title / type / origin_source (§5.2) ────────────────────────
    proposed_title = item.proposed_title or f"Review: {item_id}"
    proposed_page_type = item.proposed_page_type  # may be None → heuristic in _run_generation

    # origin_source: provenance from source_page_id, else synthetic marker (§5.1)
    if item.source_page_id:
        try:
            async with get_session() as session:
                src_row = (
                    await session.execute(
                        select(Page.file_path).where(
                            Page.id == str(item.source_page_id)
                        )
                    )
                ).scalar_one_or_none()
            origin_source = src_row or f"review:{item_id_str}"
        except Exception:  # noqa: BLE001
            origin_source = f"review:{item_id_str}"
    else:
        origin_source = f"review:{item_id_str}"

    # ── 4. Run generation (AI seam — NotImplementedError propagates as 502) ───
    try:
        wiki_page = await _run_generation(
            vault_id=vault_id,
            proposed_title=proposed_title,
            proposed_page_type=proposed_page_type,
            rationale=item.rationale,
            origin_source=origin_source,
            provider_config_row=provider_config_row,
        )
    except NotImplementedError as nie:
        logger.warning(
            "create_page_from_review: _run_generation raised NotImplementedError (ADR-0034 §5): %s",
            nie,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                "Page generation raised NotImplementedError (ADR-0034 §5). "
                "Item left pending — retry or skip."
            ),
        ) from nie
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "create_page_from_review: generation failed for item=%s: %s — item left pending",
            item_id_str,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                f"Page generation failed: {exc}. "
                "Item left pending — retry or skip."
            ),
        ) from exc

    # ── 5. Write the page via the single incremental seam (I1) ───────────────
    from app.ingest.orchestrator import write_wiki_page

    try:
        created_page = await write_wiki_page(None, wiki_page, origin_source)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "create_page_from_review: write_wiki_page failed for item=%s: %s — item left pending",
            item_id_str,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                f"Failed to write page to wiki: {exc}. "
                "Item left pending — retry or skip."
            ),
        ) from exc

    # ── 6. Set item to created ─────────────────────────────────────────────────
    created_page_id_str = str(created_page.id)
    async with get_session() as session:
        row2 = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item2 = row2.scalar_one_or_none()
        if item2 is None:
            # Theoretically impossible at this point, but handle gracefully
            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")
        item2.status = "created"
        item2.resolution = "created"
        # Assign as str for SQLite/Postgres compat: with_variant(String(36),"sqlite") stores
        # the value as TEXT in SQLite; aiosqlite cannot bind raw uuid.UUID objects in UPDATEs
        # via the ORM flush path.  On Postgres the asyncpg driver handles UUID natively, and
        # SQLAlchemy's native UUID type coerces str→UUID on write.  The type: ignore is
        # unavoidable because Mapped[uuid.UUID | None] does not declare str as an input type.
        item2.created_page_id = created_page_id_str  # type: ignore[assignment]  # noqa: PGH003
        item2.reviewed_at = datetime.now(UTC)
        item2.reviewed_by = "web-ui"
        await session.flush()
        await session.refresh(item2)
        session.expunge(item2)

    logger.info(
        "create_page_from_review: item=%s → page=%s title=%r vault=%s",
        item_id_str,
        created_page_id_str,
        proposed_title,
        vault_id,
    )

    # ── 7. Fire-and-forget sweep (§6.1 trigger 2) ────────────────────────────
    import asyncio

    async def _do_sweep() -> None:
        try:
            await sweep_reviews(vault_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("create_page_from_review: post-create sweep failed (non-fatal): %s", exc)

    asyncio.create_task(_do_sweep())

    return item2


async def list_queue(
    vault_id: str,
    *,
    limit: int = 50,
    offset: int = 0,
) -> ReviewQueuePage:
    """
    Return a paginated ReviewQueuePage for GET /review/queue (ADR-0034 §7).

    Queries all statuses so the UI can show the full queue.
    Ordered by created_at ASC.
    limit is capped at 200 by the REST endpoint (I7 — bounded page size).
    """
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


async def skip(item_id: uuid.UUID) -> ReviewItem:
    """Set status=skipped, resolution=skipped, reviewed_at=now() (ADR-0034 §7).
    404 if the item is not found."""
    return await _set_status(item_id, "skipped", resolution="skipped")


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
    # 503 guard (I9 — no fake run, no fallback engine)
    if not settings.searxng_url:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="SEARXNG_URL is not configured. Set SEARXNG_URL to enable deep research (I9).",
        )

    item_id_str = str(item_id)

    async with get_session() as session:
        row = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item = row.scalar_one_or_none()
        if item is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")

        # Extract topic: proposed_title → rationale first line → page.title → fallback
        # (ADR-0034 §7 — topic derived from new fields, NOT pre_generated_query)
        topic: str
        if item.proposed_title:
            topic = item.proposed_title
        elif item.rationale:
            first_line = item.rationale.splitlines()[0].strip()
            topic = first_line if first_line else f"Review: {item_id}"
        elif item.page_id:
            pg_row = await session.execute(
                select(Page).where(Page.id == str(item.page_id))
            )
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
        item2.resolution = "researched"
        item2.reviewed_at = datetime.now(UTC)
        item2.reviewed_by = "web-ui"
        item2.deep_research_run_id = run_id_str  # type: ignore[assignment]

        await session.flush()
        await session.refresh(item2)
        session.expunge(item2)

    # Schedule the background task
    _asyncio.create_task(
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


# ── AI seam private helpers (ai-agent-engineer) ───────────────────────────────


async def _resolve_review_provider(vault_id: str) -> tuple[Any, Any] | None:
    """
    Resolve the InferenceProvider for operation='ingest' (I6) for the proposal/sweep calls.

    Returns (provider, config_row) or None when no provider_config resolves / DB unavailable.
    NEVER hardcodes a backend; NEVER branches on isinstance/type/class-name (I6).
    Mirrors the resolution in ops/deep_research.py and ingest/orchestrator.py.
    """
    from app.ingest.provider import resolve_provider
    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    try:
        config_row = await resolve_provider_config("ingest", vault_id)
    except ConfigNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_resolve_review_provider: provider resolution failed (vault=%s): %s", vault_id, exc
        )
        return None

    try:
        provider = resolve_provider(config_row)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_resolve_review_provider: provider build failed (vault=%s): %s", vault_id, exc
        )
        return None
    return provider, config_row


def _coerce_token_budget(raw: Any, fallback: int) -> int:
    """Coerce a provider-row token_budget (possibly None/Any) to int, else *fallback*."""
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value or fallback


async def _chat_collect(provider: Any, instruction: str) -> str:
    """
    Run ONE capability-agnostic provider.chat() turn and collect the full text (I6).

    Rides the existing chat() seam (same surface ops/deep_research.py uses) so the call is
    backend-neutral — no new ABC method, no isinstance/type branching. Usage is recorded out
    of band onto the bound accumulator by the provider. Returns the concatenated text.
    """
    from app.ingest.schemas import Message

    chunks: list[str] = []
    async for chunk in await provider.chat(
        messages=[Message(role="user", content=instruction)],
        retrieval_context="",
    ):
        chunks.append(chunk)
    return "".join(chunks).strip()


def _digest_written_pages(written_pages: list[Page], *, max_pages: int = 20) -> str:
    """Compact title-only digest of the written pages (bounded; no full content — I1)."""
    lines: list[str] = []
    for page in written_pages[:max_pages]:
        title = (page.title or "").strip() or "(untitled)"
        ptype = (page.page_type or "").strip() or "?"
        lines.append(f"- {title} [{ptype}]")
    return "\n".join(lines) if lines else "(none)"


def _build_propose_instruction(
    *,
    analysis: Analysis,
    written_pages: list[Page],
    existing_titles: list[str],
    max_items: int,
    token_budget: int,
) -> str:
    """
    Build the single structured-proposal prompt (ADR-0034 §4.3).

    Asks for a JSON object {"proposals": [...]} of ≤ max_items items, each one of the five
    review types. The model is told to return ONLY JSON. token_budget is surfaced so the model
    keeps the output compact (the call is also wrapped in wait_for + capped on parse).
    """
    analysis_json = "{}"
    if analysis is not None:
        try:
            analysis_json = analysis.model_dump_json(indent=2)
        except Exception:  # noqa: BLE001
            analysis_json = "{}"

    pages_digest = _digest_written_pages(written_pages)
    titles_block = "\n".join(f"- {t}" for t in existing_titles[:200]) or "(none)"

    return (
        "You are the review-proposal step of a self-organizing wiki ingest pipeline.\n"
        "Given the ingest analysis, the pages just written, and the existing vault titles, "
        "propose follow-up work the human should review. Propose ONLY genuinely useful items "
        "(missing pages, research gaps, conflicts, possible duplicates, or things to confirm).\n\n"
        f"# Ingest analysis\n{analysis_json}\n\n"
        f"# Pages written this run\n{pages_digest}\n\n"
        f"# Existing vault page titles\n{titles_block}\n\n"
        "Return ONLY a JSON object with a single key \"proposals\" whose value is a list of at "
        f"most {max_items} objects. Each object has keys:\n"
        "  type: one of missing-page | suggestion | contradiction | duplicate | confirm\n"
        "  proposed_title: string (the page to create; required for missing-page)\n"
        "  proposed_page_type: one of entity | concept | synthesis | comparison (optional; "
        "NEVER 'source')\n"
        "  rationale: short string explaining why this matters\n"
        "  target_page_title: string (REQUIRED for contradiction/duplicate — the existing "
        "page in conflict; otherwise omit or null)\n\n"
        "Do NOT propose a page whose title already exists. Keep the output well under "
        f"{token_budget} tokens. Return no prose, only the JSON object."
    )


def _parse_proposals(raw: str) -> list[ProposalDTO]:
    """
    Parse the proposal JSON into ProposalDTO list. Tolerant of code fences / prose wrapping;
    silently drops malformed entries (degrade, never raise). Unknown types are dropped.
    """
    if not raw:
        return []
    obj = _loads_json_lenient(raw)
    if obj is None:
        return []

    if isinstance(obj, dict):
        items_raw = obj.get("proposals", obj.get("items", []))
    elif isinstance(obj, list):
        items_raw = obj
    else:
        return []
    if not isinstance(items_raw, list):
        return []

    out: list[ProposalDTO] = []
    for entry in items_raw:
        if not isinstance(entry, dict):
            continue
        item_type = entry.get("type") or entry.get("item_type")
        if item_type not in _VALID_ITEM_TYPES:
            continue
        proposed_type = entry.get("proposed_page_type")
        # 'source' is never a valid Create target (§5.2) — drop it to the heuristic (None).
        if proposed_type == "source":
            proposed_type = None
        out.append(
            ProposalDTO(
                item_type=item_type,
                proposed_title=_clean_str(entry.get("proposed_title")),
                proposed_page_type=_clean_str(proposed_type),
                rationale=_clean_str(entry.get("rationale")),
                target_page_title=_clean_str(entry.get("target_page_title")),
            )
        )
    return out


def _build_sweep_instruction(
    *,
    judgeable: list[ReviewItem],
    existing_titles: list[str | None],
    token_budget: int,
) -> str:
    """
    Build the single conservative default-to-keep sweep prompt (ADR-0034 §6.3).

    Lists each candidate item by id + type + title + rationale; asks the model to return the
    ids it is CONFIDENT can be resolved. The default is to keep; ambiguity → keep.
    """
    item_lines: list[str] = []
    for it in judgeable:
        item_lines.append(
            f"- id={it.id} type={it.item_type} "
            f"title={(it.proposed_title or '')!r} "
            f"rationale={(it.rationale or '')!r}"
        )
    items_block = "\n".join(item_lines) or "(none)"
    titles_block = "\n".join(f"- {t}" for t in existing_titles[:200] if t) or "(none)"

    return (
        "You are the conservative auto-resolution judge of a wiki review queue.\n"
        "For each review item below, decide whether the concern NO LONGER APPLIES given the "
        "current vault. BE CONSERVATIVE: only resolve an item if you are CONFIDENT the concern "
        "is already satisfied (e.g. the page now exists, the duplicate is gone, the gap is "
        "filled). When in doubt, KEEP it pending.\n\n"
        f"# Current vault page titles\n{titles_block}\n\n"
        f"# Review items to judge\n{items_block}\n\n"
        "Return ONLY a JSON object with a single key \"resolve\" whose value is the list of item "
        "id strings you are confident can be resolved. Resolve NOTHING you are unsure about. "
        f"Keep the output well under {token_budget} tokens. Return no prose, only the JSON object."
    )


def _parse_sweep_verdicts(raw: str, by_id: dict[str, ReviewItem]) -> set[str]:
    """
    Parse the sweep verdict JSON into a set of ids to resolve. Any ambiguity / parse failure /
    unrecognized shape → empty set (default-to-keep, Do-NOT #7). Only ids present in *by_id*
    (i.e. the items we actually asked about) are accepted.
    """
    if not raw:
        return set()
    obj = _loads_json_lenient(raw)
    if obj is None:
        return set()

    if isinstance(obj, dict):
        ids_raw = obj.get("resolve", obj.get("resolve_ids", []))
    elif isinstance(obj, list):
        ids_raw = obj
    else:
        return set()
    if not isinstance(ids_raw, list):
        return set()

    return {str(x) for x in ids_raw if str(x) in by_id}


def _loads_json_lenient(raw: str) -> Any | None:
    """
    Best-effort JSON parse tolerant of ```json fences / surrounding prose. Returns the parsed
    value (dict/list/...) or None on failure. Never raises (degrade-safe for the AI seams).
    """
    text = raw.strip()
    if text.startswith("```"):
        # Strip the first fenced block.
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
    # Try object slice, then array slice.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                continue
    return None


def _clean_str(value: Any) -> str | None:
    """Return a stripped non-empty string, or None."""
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return None


_COMPARISON_CUES = ("vs", "versus", "compared", "comparison")
_SYNTHESIS_CUES = ("overview of", "summary of", "survey", "landscape")


def _resolve_create_page_type(
    proposed_title: str,
    proposed_page_type: str | None,
    rationale: str | None,
) -> PageType:
    """
    Resolve the final PageType for a Create (ADR-0034 §5.2).

      1. Use proposed_page_type if it is a valid non-'source' PageType.
      2. Otherwise heuristic over title + rationale:
         comparison cues → comparison; synthesis cues → synthesis;
         proper-noun / multi-word capitalized shape → entity; default → concept.
    'source' is reserved for ingested raw documents — Create NEVER produces a source page.
    """
    if proposed_page_type:
        try:
            candidate = PageType(proposed_page_type)
            if candidate != PageType.SOURCE:
                return candidate
        except (ValueError, KeyError):
            pass

    haystack = f"{proposed_title} {rationale or ''}".lower()
    if any(re.search(rf"\b{re.escape(cue)}\b", haystack) for cue in _COMPARISON_CUES):
        return PageType.COMPARISON
    if any(cue in haystack for cue in _SYNTHESIS_CUES):
        return PageType.SYNTHESIS

    # Proper-noun / named-entity shape: ≥2 words AND ≥2 capitalized tokens in the title.
    title_words = proposed_title.split()
    capitalized = sum(1 for w in title_words if w[:1].isupper())
    if len(title_words) >= 2 and capitalized >= 2:
        return PageType.ENTITY

    return PageType.CONCEPT


# ── Private helpers ────────────────────────────────────────────────────────────


def _normalize_title(title: str) -> str:
    """Case- and whitespace-normalized title for rule-based sweep matching."""
    return re.sub(r"\s+", " ", title.strip()).lower()


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

    async with get_session() as session:
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
