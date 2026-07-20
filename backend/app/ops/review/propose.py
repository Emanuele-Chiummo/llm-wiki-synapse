"""
F9 HITL Review Queue — proposal + sweep LLM seams (BE-ARCH-2 package split).

Owns the two implemented AI seams that surface follow-up work (ADR-0034 §11.2):
  _llm_propose_reviews(...)  — single bounded InferenceProvider call for LLM proposals.
  _llm_sweep_judge(...)      — single bounded conservative LLM pass for sweep Pass-2.

...and their orchestration entry points, called fire-and-forget from run_ingest_pipeline:
  propose_reviews(...)       — rule-based missing-page/duplicate detection, then
                               _llm_propose_reviews for LLM proposals (ADR-0034 §4).
  sweep_reviews(vault_id)    — auto-resolution sweep: Pass-1 (rule-based) + Pass-2
                               (conservative LLM) (ADR-0034 §6).
  propose_corpus_shape_review(...) — additive rule-based corpus synthesis/comparison seeder
                               (SC-D3), delegated to by ops/synthesize.py.

I7 CONTRACT: propose_reviews() and sweep_reviews() NEVER raise into the ingest critical path.
The orchestrator wraps them in try/except (Do-NOT #5, ADR-0034 §10).

I6 CONTRACT: all LLM calls route through resolve_operation_provider — no hardcoded backend.

MONKEYPATCH-COMPAT NOTE (BE-ARCH-2): several call sites below fetch a sibling seam via a
DEFERRED `from app.ops.review import X` (instead of a static top-of-file import) specifically
so that ``unittest.mock.patch("app.ops.review.X", ...)`` / ``monkeypatch.setattr(review_mod, "X",
...)`` — written against the pre-split monolithic module — keep working unchanged after the
split into a package. A deferred import re-resolves the current attribute on the
``app.ops.review`` package (the re-export surface in ``__init__.py``) at CALL time, which is
exactly the object test patches mutate. A plain module-level import would instead bind a
private, unpatchable local name at import time. This is deliberate, not an oversight — do not
"simplify" it back to a top-level import.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import String as _SA_String
from sqlalchemy import cast as _sa_cast
from sqlalchemy import func, select

from app import db as _db
from app.config import settings
from app.ingest.schemas import PageType, type_subdir
from app.models import Page, ReviewItem
from app.ops._llm import coerce_int
from app.ops.review.prompts import (
    ProposalDTO,
    _build_propose_instruction,
    _build_sweep_instruction,
    _parse_proposals,
    _parse_sweep_verdicts,
    _resolve_review_language,
)
from app.ops.review.queue import _content_key, _set_status

if TYPE_CHECKING:
    from app.ingest.schemas import Analysis

logger = logging.getLogger(__name__)

# Caps (I7 — bounded reads/lists)
_SWEEP_PASS1_MAX_ITEMS: int = 200  # max pending items processed per sweep Pass-1
_RULE_PROPOSE_MAX_ITEMS: int = 8  # deterministic proposal quota per run
_AI_PROPOSE_MAX_ITEMS: int = 12  # AI proposal quota per run (config-overridable)
_REVIEW_PROPOSE_TOTAL_HARD_CAP: int = 20

# ── R5 parity: title prefix stripping (review-utils.ts normalizeReviewTitle) ────────
# Common prefixes the LLM may prepend to review titles. Stripped before normalization
# so dedup + sweep agree on what "the same concept" means regardless of prefix presence.
_REVIEW_TITLE_PREFIX_RE: re.Pattern[str] = re.compile(
    r"^(?:missing[\s\-]?page[:：]\s*"
    r"|duplicate[\s\-]?page[:：]\s*"
    r"|possible[\s\-]?duplicate[:：]\s*"
    r"|缺失页面[:：]\s*"
    r"|缺少页面[:：]\s*"
    r"|重复页面[:：]\s*"
    r"|疑似重复[:：]\s*)",
    re.IGNORECASE,
)


def _normalize_title(title: str) -> str:
    """
    Case- and whitespace-normalized title for rule-based sweep matching and dedup.

    R5 parity (review-utils.ts normalizeReviewTitle): strips common LLM prefixes
    (Missing page:, Duplicate page:, possible duplicate:, CJK equivalents) before
    normalizing, so dedup and sweep agree on what 'the same concept' means regardless
    of whether the LLM prepended a prefix to the title.
    """
    stripped = _REVIEW_TITLE_PREFIX_RE.sub("", title.lstrip())
    return re.sub(r"\s+", " ", stripped.strip()).lower()


@dataclass
class SweepResult:
    """Result of a sweep_reviews() run."""

    rule_resolved: int
    llm_resolved: int
    kept: int
    corpus_proposed: int = 0  # SC-D3: synthesis/comparison proposals seeded in Pass 3


# ── AI seam implementations (ADR-0034 §11.2) ─────────────────────────────────


async def _llm_propose_reviews(
    *,
    vault_id: str,
    analysis: Analysis | None,
    written_pages: list[Page],
    existing_titles: list[str],
    source_text: str = "",
) -> list[ProposalDTO]:
    """
    Single bounded provider call (ADR-0034 §4.3, implemented).

    Makes AT MOST ONE InferenceProvider call (operation "ingest", resolved via
    resolve_provider_config("ingest", vault_id) — I6) that, given:
      - analysis (topics, entities, suggested_pages, summary)
      - a compact digest of the written pages (title + short excerpt)
      - the list of existing_titles in the vault (bounded, no full content)
    returns a structured list of ProposalDTO proposals (≤ configured AI proposal cap).

    Bounds (I7):
      - Exactly ONE call; no loop; no retry.
      - asyncio.wait_for(REVIEW_PROPOSE_TIMEOUT_SECONDS).
      - Output capped at review_propose_max_items (truncate; never emit unbounded list).
      - token_budget from the resolved row (or REVIEW_PROPOSE_TOKEN_BUDGET default).
      - Cost pushed through UsageAccumulator; logged (total_cost_usd).
      - On ConfigNotFoundError / timeout / any failure → return [] (log WARNING, never raise).
        The rule-based proposals (if any) will still be emitted by the caller.

    Returns:
      List of ProposalDTO (0..N, capped at review_propose_max_items).
    """
    # Deferred (package-level) import — keeps `patch("app.ops.review.resolve_operation_provider")`
    # / `patch("app.ops.review.bounded_chat_collect")` effective post-split (see module docstring).
    from app.ops.review import bounded_chat_collect, resolve_operation_provider  # noqa: PLC0415

    # ── Resolve provider (I6 — never hardcode; "no provider" → []) ───────────────
    resolved = await resolve_operation_provider(vault_id)
    if resolved is None:
        logger.debug(
            "_llm_propose_reviews: no ingest provider resolved (vault=%s) — "
            "rule-based proposals only (I6: no silent default)",
            vault_id,
        )
        return []
    provider, config_row = resolved

    max_items = min(
        _AI_PROPOSE_MAX_ITEMS,
        max(0, int(getattr(settings, "review_propose_max_items", _AI_PROPOSE_MAX_ITEMS))),
    )
    token_budget = coerce_int(
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
        source_text=source_text,
    )

    # ── ONE bounded call, no loop, no retry (I7) ─────────────────────────────────
    try:
        raw = await asyncio.wait_for(
            bounded_chat_collect(provider, instruction, use_complete=True), timeout=timeout_s
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

    # Deferred (package-level) import — see module docstring (monkeypatch-compat).
    from app.ops.review import bounded_chat_collect, resolve_operation_provider  # noqa: PLC0415

    # ── Resolve provider (I6 — "no provider" → keep all pending) ─────────────────
    resolved = await resolve_operation_provider(vault_id)
    if resolved is None:
        logger.debug(
            "_llm_sweep_judge: no ingest provider resolved (vault=%s) — keep all pending (I6)",
            vault_id,
        )
        return set()
    provider, config_row = resolved

    token_budget = coerce_int(
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
            bounded_chat_collect(provider, instruction, use_complete=True), timeout=timeout_s
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


# ── SC-D3 (ADR-0067 D3): corpus-shape proposal seeder ────────────────────────────
# Additive rule-based seeder for the corpus-level synthesis/comparison shapes detected from the
# 4-signal graph (source-overlap / type-affinity) by ops/synthesize.py. Today those shapes are
# rarely proposed (the LLM propose prompt keys on missing-page/duplicate/contradiction, not on the
# comparison/synthesis SHAPE — audit SC-D3). This surfaces borderline clusters (below synthesize's
# auto-write confidence) to the human with the RIGHT proposed_page_type, so obvious comparisons and
# syntheses are always offered as Create-able review items instead of sitting undiscovered.
#
# Deliberately ADDITIVE: it changes no existing propose_reviews / _llm_propose_reviews behaviour, so
# it cannot break existing review tests. Pure DB write via enqueue_review (NO provider call). Its
# rows are ordinary `suggestion` proposals — the existing Create path (_resolve_create_page_type)
# already honours proposed_page_type=synthesis/comparison, so nothing downstream needs to change.
_CORPUS_SHAPE_TYPES = frozenset({PageType.SYNTHESIS.value, PageType.COMPARISON.value})


async def propose_corpus_shape_review(
    *,
    vault_id: str,
    kind: str,
    proposed_title: str,
    cluster_page_ids: list[str],
    rationale: str,
    generation_key: str,
) -> ReviewItem | None:
    """
    Propose ONE corpus-level synthesis/comparison shape to the F9 review queue (SC-D3).

    Rule-based, provider-free, idempotent (stable content_key → UPSERT-on-re-run). Emits a
    `suggestion` review item whose ``proposed_page_type`` is the cluster kind and whose
    ``referenced_page_ids`` are the cluster members (the [[wikilink]] seeds a human Create will
    integrate). Returns the enqueued ReviewItem, or None on bad input / failure (never raises into
    the caller's bounded loop).

    Args:
      kind: "synthesis" | "comparison" (anything else → None).
      cluster_page_ids: member page ids (str UUIDs) → referenced_page_ids on the proposal.
    """
    if kind not in _CORPUS_SHAPE_TYPES:
        logger.debug("propose_corpus_shape_review: unsupported kind=%r (vault=%s)", kind, vault_id)
        return None
    title = (proposed_title or "").strip()
    if not title:
        return None

    proposed_dir: str | None = None
    try:
        proposed_dir = type_subdir(PageType(kind))
    except (ValueError, KeyError):
        proposed_dir = None

    ref_ids = [str(pid) for pid in (cluster_page_ids or []) if str(pid).strip()] or None
    normalized_generation_key = (generation_key or "").strip().lower()
    expected_prefix = f"corpus:{kind}:"
    if (
        not normalized_generation_key.startswith(expected_prefix)
        or len(normalized_generation_key) != len(expected_prefix) + 64
        or any(
            char not in "0123456789abcdef"
            for char in normalized_generation_key[len(expected_prefix) :]
        )
    ):
        logger.warning(
            "propose_corpus_shape_review: invalid generation key for kind=%s vault=%s",
            kind,
            vault_id,
        )
        return None

    # The opaque content_key doubles as the exact corpus identity. This survives the Review
    # lifecycle without another migration column and lets Create stamp the same key onto the page.
    content_key = normalized_generation_key

    # Deferred (package-level) import — see module docstring (monkeypatch-compat).
    from app.ops.review import enqueue_review  # noqa: PLC0415

    try:
        return await enqueue_review(
            vault_id=vault_id,
            item_type="suggestion",
            proposal_origin="corpus",
            proposed_title=title,
            proposed_page_type=kind,
            proposed_dir=proposed_dir,
            rationale=rationale,
            content_key=content_key,
            referenced_page_ids=ref_ids,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort seeder; never break the caller's loop
        logger.warning(
            "propose_corpus_shape_review: enqueue failed (vault=%s kind=%s title=%r): %s",
            vault_id,
            kind,
            title,
            exc,
        )
        return None


async def _sweep_corpus_shape_proposals(vault_id: str, max_clusters: int) -> int:
    """
    Pass 3 of sweep_reviews (SC-D3): seed synthesis/comparison REVIEW proposals from the
    4-signal graph without requiring the user to explicitly trigger /ops/synthesize.

    Deterministic cluster heuristic (same one synthesize._run_inner uses): 2 indexed SQL reads
    + pure Python.  NO provider call, NO vault walk — purely rule-based and cheap (I1/I2/I9).

    Only the REVIEW band is surfaced here: [REVIEW_CONFIDENCE_FLOOR, AUTO_CONFIDENCE_THRESHOLD).
    * Clusters above AUTO_CONFIDENCE_THRESHOLD belong to /ops/synthesize auto-write.
    * Clusters below REVIEW_CONFIDENCE_FLOOR are noise — skip (never spam the queue).

    Idempotent: each proposal carries ``content_key = generation_key``, so ``enqueue_review``
    upserts-on-(vault_id, content_key) — a second sweep (or a later /ops/synthesize run in
    review-only mode) does NOT create a duplicate row.

    Bounded at ``max_clusters`` (I7). Returns count of proposals successfully enqueued (≥0).
    Never raises — any failure is logged as WARNING (non-fatal complement to the sweep passes).

    Deferred imports from ``app.ops.synthesize`` avoid the circular-at-module-level import
    (synthesize.py imports ``app.ops.review`` at call time too — the same deferred pattern).
    """
    # Deferred import — synthesize imports app.ops.review at call time, so a top-level import
    # here would create a circular dependency at module load. This matches the monkeypatch-compat
    # pattern used throughout this file (see module docstring).
    try:
        from app.ops.synthesize import (  # noqa: PLC0415
            AUTO_CONFIDENCE_THRESHOLD,
            REVIEW_CONFIDENCE_FLOOR,
            _build_clusters,
            _default_title,
            _generation_key,
            _generation_key_exists,
            _load_graph_data,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_sweep_corpus_shape_proposals: synthesize import failed (non-fatal): %s", exc
        )
        return 0

    try:
        pages, links = await _load_graph_data(vault_id)
        clusters = _build_clusters(pages, links)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_sweep_corpus_shape_proposals: cluster seeding failed (vault=%s, non-fatal): %s",
            vault_id,
            exc,
        )
        return 0

    proposed = 0
    evaluated = 0
    for cluster in clusters:
        if evaluated >= max_clusters:
            logger.debug(
                "_sweep_corpus_shape_proposals: reached max_clusters=%d (vault=%s)",
                max_clusters,
                vault_id,
            )
            break
        evaluated += 1

        # Only the REVIEW band — auto-write candidates belong to /ops/synthesize.
        if cluster.confidence < REVIEW_CONFIDENCE_FLOOR:
            continue
        if cluster.confidence >= AUTO_CONFIDENCE_THRESHOLD:
            continue

        # Skip clusters already covered by an existing auto-written synthesis/comparison page
        # (a previous /ops/synthesize run may have written it since the last sweep).
        try:
            gkey = _generation_key(cluster)
            if await _generation_key_exists(vault_id, gkey):
                logger.debug(
                    "_sweep_corpus_shape_proposals: cluster already written (key=%s, vault=%s)",
                    gkey,
                    vault_id,
                )
                continue
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_sweep_corpus_shape_proposals: generation_key_exists failed (non-fatal): %s", exc
            )
            continue

        title = _default_title(cluster)
        rationale = (
            f"Graph signals suggest a {cluster.kind} across "
            f"{', '.join(cluster.titles[:4])}"
            f"{' and others' if len(cluster.titles) > 4 else ''} "
            f"(shared-source overlap; confidence={cluster.confidence:.2f}). "
            "Review and Create to author it, or Skip."
        )

        try:
            item = await propose_corpus_shape_review(
                vault_id=vault_id,
                kind=cluster.kind,
                proposed_title=title,
                cluster_page_ids=list(cluster.page_ids),
                rationale=rationale,
                generation_key=gkey,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "_sweep_corpus_shape_proposals: propose failed (vault=%s kind=%s): %s",
                vault_id,
                cluster.kind,
                exc,
            )
            continue

        if item is not None:
            proposed += 1
            logger.debug(
                "_sweep_corpus_shape_proposals: proposed %s %r (vault=%s conf=%.2f)",
                cluster.kind,
                title,
                vault_id,
                cluster.confidence,
            )

    logger.info(
        "_sweep_corpus_shape_proposals: vault=%s evaluated=%d proposed=%d",
        vault_id,
        evaluated,
        proposed,
    )
    return proposed


def _rule_missing_page_search_queries(
    target_title: str,
    *,
    referrer_title: str | None,
    origin_source: str,
) -> list[str]:
    """Build up to three stable searches for a dangling-link proposal.

    The bare target remains first for backward compatibility. When available, the written page
    that contained the link and the ingested source basename add enough context to disambiguate a
    generic target without asking a provider or reading any additional page.
    """
    target = (target_title or "").strip()
    if not target:
        return []

    queries = [target]
    contextual_terms: list[str] = []
    referrer = (referrer_title or "").strip()
    if referrer and _normalize_title(referrer) != _normalize_title(target):
        contextual_terms.append(referrer)

    source_name = (origin_source or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "." in source_name:
        source_name = source_name.rsplit(".", 1)[0]
    source_name = re.sub(r"[_\-]+", " ", source_name).strip()
    if source_name and _normalize_title(source_name) not in {
        _normalize_title(target),
        _normalize_title(referrer),
    }:
        contextual_terms.append(source_name)

    for context in contextual_terms:
        query = f"{target} {context}".strip()
        if query not in queries:
            queries.append(query)
        if len(queries) >= 3:
            break
    return queries


def _merge_proposals_bounded(
    rule_proposals: list[ProposalDTO], llm_proposals: list[ProposalDTO]
) -> list[ProposalDTO]:
    """Stable de-duplicating merge with independent rule and AI quotas (I7).

    Rule noise can never consume the AI quota. Exact duplicate identities do not consume a quota
    slot, so later unique proposals can still fill it. Input order is preserved within each lane;
    the caller makes deterministic rule inputs stable before this seam.
    """
    rule_cap = min(
        _RULE_PROPOSE_MAX_ITEMS,
        max(
            0,
            int(getattr(settings, "review_rule_propose_max_items", _RULE_PROPOSE_MAX_ITEMS)),
        ),
    )
    ai_cap = min(
        _AI_PROPOSE_MAX_ITEMS,
        max(0, int(getattr(settings, "review_propose_max_items", _AI_PROPOSE_MAX_ITEMS))),
    )
    merged: list[ProposalDTO] = []
    positions: dict[tuple[str, str, str], int] = {}

    def _identity(proposal: ProposalDTO) -> tuple[str, str, str]:
        title_or_rationale = proposal.proposed_title or proposal.rationale or ""
        return (
            proposal.item_type,
            _normalize_title(title_or_rationale),
            _normalize_title(proposal.target_page_title or ""),
        )

    # Seed the deterministic lane first so it remains a complete fallback when AI is absent.
    rule_added = 0
    for proposal in rule_proposals:
        if rule_added >= rule_cap:
            break
        identity = _identity(proposal)
        if identity in positions:
            continue
        positions[identity] = len(merged)
        merged.append(proposal)
        rule_added += 1

    # An AI duplicate replaces the rule DTO in place: richer rationale/queries win while stable
    # ordering and deterministic referenced ids (merged later by title) are preserved. Replacing
    # an occupied identity consumes no AI quota, so later unique AI proposals are not starved.
    ai_seen: set[tuple[str, str, str]] = set()
    ai_added = 0
    for proposal in llm_proposals:
        if ai_cap <= 0:
            break
        identity = _identity(proposal)
        if identity in ai_seen:
            continue
        ai_seen.add(identity)
        existing_position = positions.get(identity)
        if existing_position is not None:
            merged[existing_position] = proposal
            continue
        if ai_added >= ai_cap:
            continue
        positions[identity] = len(merged)
        merged.append(proposal)
        ai_added += 1
    return merged[:_REVIEW_PROPOSE_TOTAL_HARD_CAP]


async def propose_reviews(
    *,
    vault_id: str,
    analysis: Analysis | None,
    written_pages: list[Page],
    origin_source: str,
    source_text: str = "",
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

    Rule and AI proposals have independent bounded quotas (defaults 8 + 12 = 20). This prevents a
    dangling-link burst from starving source-grounded AI proposals.
    """
    if not written_pages:
        logger.debug("propose_reviews: no written pages; skipping (vault=%s)", vault_id)
        return

    # ── Rule-based: dangling wikilinks → missing-page ─────────────────────────
    rule_proposals: list[ProposalDTO] = []
    # v1.5.2: localise rule-based rationales to the vault language (IT/EN supported; else EN),
    # so they don't stay English on a non-English vault like the LLM-proposed items used to.
    _rule_lang = _resolve_review_language(analysis)
    # ADR-0044 §4.1: rule-based referenced ids resolved BY ID (no title round-trip), keyed by
    # proposed_title. Merged into the persist loop's resolved referenced_page_ids.
    _rule_ref_ids: dict[str, list[str]] = {}

    # Find dangling wikilinks for the written pages (bounded indexed read — I1/I2)
    written_page_ids = [str(p.id) for p in written_pages]
    written_title_by_id = {
        str(p.id): (p.title or "").strip() for p in written_pages if (p.title or "").strip()
    }
    dangling_targets: set[str] = set()
    # ADR-0044 §4.1: remember the referencing (written) page per dangling target so the
    # rule-based proposal can carry [referencing page id] as its referenced_page_ids seed.
    dangling_referrer: dict[str, str] = {}
    try:
        from app.models import Link

        async with _db.get_session() as session:
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
            for r in rows:
                # First referring written page wins (stable, bounded).
                dangling_referrer.setdefault(r.target_title, str(r.source_page_id))

        for target_title in sorted(dangling_targets, key=str.casefold):
            # Check if a page with this title already exists (bounded indexed read)
            async with _db.get_session() as session:
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
                referrer = dangling_referrer.get(target_title)
                referrer_title = written_title_by_id.get(referrer or "")
                rule_proposals.append(
                    ProposalDTO(
                        item_type="missing-page",
                        proposed_title=target_title,
                        proposed_page_type=None,  # heuristic at Create time
                        rationale=(
                            f"Wikilink pendente [[{target_title}]] nel contenuto acquisito."
                            if _rule_lang == "it"
                            else f"Dangling wikilink [[{target_title}]] in ingested content."
                        ),
                        target_page_title=None,
                        # ADR-0044 §4.1 rule-based seeds: [referencing page id] + [proposed_title].
                        referenced_page_titles=[],  # resolved via id below, not titles
                        search_queries=_rule_missing_page_search_queries(
                            target_title,
                            referrer_title=referrer_title,
                            origin_source=origin_source,
                        ),
                    )
                )
                if referrer:
                    # Stash the resolved referencing id directly (skip title resolution).
                    _rule_ref_ids[target_title] = [referrer]
    except Exception as exc:  # noqa: BLE001
        logger.warning("propose_reviews: dangling-link detection failed (non-fatal): %s", exc)

    # ── Rule-based: not-written suggested_pages → missing-page ───────────────
    if analysis is not None:
        written_titles_lc = {(p.title or "").lower().strip() for p in written_pages}
        for suggested in analysis.suggested_pages or []:
            if suggested.title.lower().strip() not in written_titles_lc:
                # Suggested but not written → explicit missing-page signal
                already = any(p.proposed_title == suggested.title for p in rule_proposals)
                if not already:
                    rule_proposals.append(
                        ProposalDTO(
                            item_type="missing-page",
                            proposed_title=suggested.title,
                            proposed_page_type=str(suggested.type) if suggested.type else None,
                            rationale=(
                                suggested.rationale
                                or (
                                    f"L'analisi ha proposto '{suggested.title}' "
                                    "ma non è stato generato."
                                    if _rule_lang == "it"
                                    else f"Analysis proposed '{suggested.title}'"
                                    " but it was not generated."
                                )
                            ),
                            target_page_title=None,
                            # ADR-0044 §4.1 rule-based trivial search seed.
                            search_queries=[suggested.title],
                        )
                    )

    # ── Anti-spam gate (ADR-0034 §4.2) ───────────────────────────────────────
    # Real generated-content size (v1.5.2 fix). The old approximation summed TITLE lengths, which
    # never reached review_propose_min_chars — so the detailed LLM propose step was skipped for any
    # run with < review_propose_min_pages pages and no dangling links (common on nashsu-parity
    # single-doc ingests, which emit few dangling wikilinks), leaving only terse rule-based items.
    # Sum the actual on-disk body sizes of the just-written pages (bounded: a handful of files).
    total_chars = 0
    for p in written_pages:
        try:
            total_chars += (settings.vault_root / p.file_path).stat().st_size
        except (
            Exception
        ):  # noqa: BLE001 — best-effort gate: any path/stat issue → title-len fallback
            total_chars += len(p.title or "")
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
            async with _db.get_session() as session:
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

            # Deferred (package-level) import — see module docstring (monkeypatch-compat).
            from app.ops.review import _llm_propose_reviews  # noqa: PLC0415

            llm_proposals = await _llm_propose_reviews(
                vault_id=vault_id,
                analysis=analysis,
                written_pages=written_pages,
                existing_titles=existing_titles,
                source_text=source_text,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "propose_reviews: LLM proposal call failed (non-fatal): %s — "
                "emitting rule-based proposals only",
                exc,
            )
            llm_proposals = []

    # ── Stable de-duplicating merge with independent caps ─────────────────────
    all_proposals = _merge_proposals_bounded(rule_proposals, llm_proposals)
    # The merge returns the original DTO objects, so identity preserves the producer lane even
    # when a rule and AI proposal have identical dataclass values. A duplicate that appears in
    # both lanes is replaced by the AI DTO and is therefore tagged ai.
    rule_proposal_ids = {id(proposal) for proposal in rule_proposals}

    if not all_proposals:
        logger.debug(
            "propose_reviews: no proposals to enqueue (vault=%s written=%d)",
            vault_id,
            len(written_pages),
        )
        return

    # ── Persist proposals ──────────────────────────────────────────────────────
    source_page_id = written_pages[0].id if written_pages else None

    # Deferred (package-level) import — see module docstring (monkeypatch-compat).
    from app.ops.review import enqueue_review  # noqa: PLC0415

    for proposal in all_proposals:
        # For contradiction/duplicate, resolve target_page_title → page_id
        target_page_id: Any = None
        if proposal.target_page_title:
            try:
                async with _db.get_session() as session:
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
                proposed_dir = type_subdir(PageType(proposal.proposed_page_type))
            except (ValueError, KeyError):
                pass

        # ── ADR-0044 §4.1: resolve referenced_page_titles → referenced_page_ids ──
        # Bounded indexed reads (reuse the exact target_page_title lookup pattern). Titles that
        # do not resolve to a live page are DROPPED — the model must not fabricate references
        # (Do-NOT #4: JSON array, no FK). Rule-based ids (resolved by id) are merged in first.
        referenced_ids: list[str] = list(_rule_ref_ids.get(proposal.proposed_title or "", []))
        ref_cap = int(getattr(settings, "review_referenced_pages_max", 8))
        for ref_title in proposal.referenced_page_titles[:ref_cap]:
            if len(referenced_ids) >= ref_cap:
                break
            try:
                async with _db.get_session() as session:
                    ref_row = (
                        await session.execute(
                            select(Page.id)
                            .where(
                                Page.vault_id == vault_id,
                                Page.title == ref_title,
                                Page.deleted_at.is_(None),
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                if ref_row is not None:
                    ref_id_str = str(ref_row)
                    if ref_id_str not in referenced_ids:
                        referenced_ids.append(ref_id_str)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "propose_reviews: referenced_page lookup failed for %r: %s", ref_title, exc
                )
        referenced_ids = referenced_ids[:ref_cap]

        # ── ADR-0044 §3.2: stable content_key (titled confirm now dedups too) ────
        query_cap = int(getattr(settings, "review_search_queries_max", 3))
        search_queries = (proposal.search_queries or [])[:query_cap]
        content_key = _content_key(
            vault_id=vault_id,
            item_type=proposal.item_type,
            proposed_title=proposal.proposed_title,
            target_page_title=proposal.target_page_title,
            page_id=str(target_page_id) if target_page_id is not None else None,
        )

        try:
            await enqueue_review(
                vault_id=vault_id,
                item_type=proposal.item_type,
                proposal_origin="rule" if id(proposal) in rule_proposal_ids else "ai",
                proposed_title=proposal.proposed_title,
                proposed_page_type=proposal.proposed_page_type,
                proposed_dir=proposed_dir,
                rationale=proposal.rationale,
                source_page_id=(
                    uuid.UUID(str(source_page_id)) if source_page_id is not None else None
                ),
                page_id=target_page_id,
                content_key=content_key,
                referenced_page_ids=referenced_ids or None,
                search_queries=search_queries or None,
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
    # ── Pass 1a: missing-page rule resolution ──────────────────────────────────
    # Resolves when a live page now exists matching the proposed_title by:
    #   1. Exact normalised title (existing behaviour)
    #   2. Slug match: proposed_title → spaces-to-dashes slug → file_path basename
    #      (R4 parity: llm_wiki byId slug check, sweep-reviews.ts:110-116)
    # R-bug1 fix: `duplicate` is removed from this pass — it has its own logic below.
    try:
        async with _db.get_session() as session:
            stmt = (
                select(ReviewItem)
                .where(
                    ReviewItem.vault_id == vault_id,
                    ReviewItem.status == "pending",
                    ReviewItem.item_type == "missing-page",
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
            # Check 1: exact normalised title match.
            async with _db.get_session() as session:
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

            # Check 2: slug match (R4 parity — llm_wiki checks byId via slug).
            # Handles the case where the page was created with a different title but
            # the same slug (e.g. proposed_title="Attention Mechanism" created as
            # wiki/concepts/attention-mechanism.md with title "The Attention Mechanism").
            if existing is None:
                slug = normalized_title.replace(" ", "-")
                async with _db.get_session() as session:
                    existing = (
                        await session.execute(
                            select(Page.id)
                            .where(
                                Page.vault_id == vault_id,
                                Page.file_path.like(f"%/{slug}.md"),
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
        logger.warning("sweep_reviews: Pass-1 missing-page failed (non-fatal): %s", exc)

    # ── Pass 1b: duplicate rule resolution (R-bug1 fix) ────────────────────────
    # R-bug1 parity: in llm_wiki (sweep-reviews.ts:376-391), a `duplicate` item is
    # auto-resolved ONLY when an affected page NO LONGER EXISTS — NOT when a page with
    # the proposed title now exists (which was the old, inverted Synapse behaviour).
    # Logic: collect all "affected" page ids (page_id primary FK + referenced_page_ids),
    # then resolve if ANY of them is no longer alive (deleted or removed).
    try:
        async with _db.get_session() as session:
            dup_stmt = (
                select(ReviewItem)
                .where(
                    ReviewItem.vault_id == vault_id,
                    ReviewItem.status == "pending",
                    ReviewItem.item_type == "duplicate",
                )
                .order_by(ReviewItem.created_at.asc())
                .limit(_SWEEP_PASS1_MAX_ITEMS)
            )
            dup_rows = list((await session.execute(dup_stmt)).scalars().all())

        for item in dup_rows:
            # Collect affected page ids: primary conflict (page_id) + referenced set.
            affected_ids: list[str] = []
            if item.page_id is not None:
                affected_ids.append(str(item.page_id))
            ref_ids: list[str] | None = item.referenced_page_ids
            if isinstance(ref_ids, list):
                affected_ids.extend(str(r) for r in ref_ids)
            elif isinstance(ref_ids, str):
                try:
                    parsed = json.loads(ref_ids)
                    if isinstance(parsed, list):
                        affected_ids.extend(str(r) for r in parsed)
                except Exception:  # noqa: BLE001,S110
                    pass  # malformed JSON in referenced_page_ids → skip; non-fatal

            if not affected_ids:
                continue  # no affected pages to check → can't rule-resolve

            # Check if ALL affected pages still exist as live pages.
            # Use CAST(id AS TEXT) == string for SQLite/Postgres portability (raw-SQL test
            # inserts store UUID strings with dashes; UUID(as_uuid=True).hex would strip them).
            all_still_exist = True
            for page_id_str in affected_ids:
                async with _db.get_session() as session:
                    still_alive = (
                        await session.execute(
                            select(Page.id)
                            .where(
                                _sa_cast(Page.id, _SA_String) == page_id_str,
                                Page.deleted_at.is_(None),
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                if still_alive is None:
                    all_still_exist = False
                    break

            # Resolve only when at least one affected page is gone (!allStillExist).
            if not all_still_exist:
                await _set_status(
                    uuid.UUID(str(item.id)),
                    "auto_resolved",
                    resolution="rule_resolved",
                    reviewed_by="auto-sweep",
                )
                rule_resolved += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("sweep_reviews: Pass-1 duplicate failed (non-fatal): %s", exc)

    # ── Pass 2: conservative LLM sweep ───────────────────────────────────────
    sweep_llm_enabled = bool(getattr(settings, "review_sweep_llm_enabled", True))
    if sweep_llm_enabled:
        try:
            # nashsu/llm_wiki parity (sweep-reviews.ts JUDGE_BATCH_SIZE=40, MAX_JUDGE_BATCHES=5):
            # judge pending items in batches of `batch_size`, up to `max_batches` LLM calls per run
            # (I7 — the fetch cap = batch_size × max_batches bounds the loop). Previously a SINGLE
            # call over ≤8 items, so semantic backlog cleanup was far slower than the reference.
            batch_size = max(1, int(getattr(settings, "review_sweep_llm_max_items", 40)))
            max_batches = max(1, int(getattr(settings, "review_sweep_llm_max_batches", 5)))
            async with _db.get_session() as session:
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
                    .limit(batch_size * max_batches)
                )
                remaining = list((await session.execute(remaining_stmt)).scalars().all())

            if remaining:
                async with _db.get_session() as session:
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

                # Deferred (package-level) import — see module docstring (monkeypatch-compat).
                from app.ops.review import _llm_sweep_judge  # noqa: PLC0415

                for start in range(0, len(remaining), batch_size):
                    batch = remaining[start : start + batch_size]
                    # Default-to-keep: _llm_sweep_judge returns set() on any failure (I7)
                    ids_to_resolve = await _llm_sweep_judge(
                        vault_id=vault_id,
                        candidate_items=batch,
                        existing_titles=existing_titles,
                    )

                    # Early-exit (nashsu/llm_wiki parity — sweep-reviews.ts:307-310): a batch that
                    # resolved NOTHING means the conservative judge is keeping everything; later
                    # batches (older, even less resolvable items) will almost certainly do the same.
                    # Stop spending LLM calls (I7 cost control). A provider failure/timeout also
                    # returns set() → we stop rather than burn the budget on likely-failing calls.
                    if not ids_to_resolve:
                        break

                    for item in batch:
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

    # ── Pass 3: corpus-shape proposal seeding (SC-D3) ────────────────────────
    # Deterministic, provider-free: seeds synthesis/comparison review items for the REVIEW
    # band clusters the graph detects, so they surface in normal operation (not only when
    # /ops/synthesize is explicitly triggered). Idempotent via content_key dedup.
    corpus_proposed = 0
    corpus_shape_enabled = bool(getattr(settings, "review_corpus_shape_enabled", True))
    if corpus_shape_enabled:
        try:
            _max_clusters = max(
                1,
                int(getattr(settings, "review_corpus_shape_max_clusters", 40)),
            )
            corpus_proposed = await _sweep_corpus_shape_proposals(vault_id, _max_clusters)
        except Exception as exc:  # noqa: BLE001
            logger.warning("sweep_reviews: Pass-3 corpus-shape failed (non-fatal): %s", exc)

    # Count kept (still pending after both passes)
    try:
        async with _db.get_session() as session:
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
        "sweep_reviews: vault=%s rule_resolved=%d llm_resolved=%d kept=%d corpus_proposed=%d",
        vault_id,
        rule_resolved,
        llm_resolved,
        kept_count,
        corpus_proposed,
    )
    return SweepResult(
        rule_resolved=rule_resolved,
        llm_resolved=llm_resolved,
        kept=kept_count,
        corpus_proposed=corpus_proposed,
    )
