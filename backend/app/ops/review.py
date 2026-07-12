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
  sweep_reviews(vault_id)    — auto-resolution sweep: Pass-1 (rule-based) + Pass-2
                               (conservative LLM).
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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast

from sqlalchemy import String as _SA_String
from sqlalchemy import cast as _sa_cast
from sqlalchemy import func, select
from sqlalchemy.engine import CursorResult

from app.config import settings
from app.db import get_session
from app.ingest.schemas import PageType, type_subdir
from app.models import Page, ReviewItem, VaultState

if TYPE_CHECKING:
    from app.ingest.schemas import Analysis, WikiPage

logger = logging.getLogger(__name__)

# ── Accepted value sets (app-side enum-by-convention, no DB CHECK — ADR-0034 §3.1) ──
# R9-3 (v0.9): `purpose-suggestion` added. R9-4 (v0.9): `schema-suggestion` added. item_type is
# a free Text column (no DB CHECK constraint — ADR-0034 §3.1), so extending this app-side set is
# sufficient for BOTH; NO migration.
_VALID_ITEM_TYPES = frozenset(
    {
        "missing-page",
        "suggestion",
        "contradiction",
        "duplicate",
        "confirm",
        "purpose-suggestion",
        "schema-suggestion",
    }
)
_VALID_STATUSES = frozenset(
    {"pending", "created", "skipped", "dismissed", "deep_researched", "auto_resolved"}
)
_VALID_RESOLUTIONS = frozenset(
    {"created", "skipped", "dismissed", "researched", "rule_resolved", "llm_resolved"}
)

# Terminal statuses (ADR-0044): an item is closed and never re-mutated by re-ingest / bulk.
_TERMINAL_STATUSES = frozenset(
    {"created", "skipped", "dismissed", "deep_researched", "auto_resolved"}
)
# The "resolved" tab set (ADR-0044 §6): terminal-resolved (excludes skipped/dismissed).
_RESOLVED_STATUSES = frozenset({"created", "auto_resolved", "deep_researched"})

# Caps (I7 — bounded reads/lists)
_SWEEP_PASS1_MAX_ITEMS: int = 200  # max pending items processed per sweep Pass-1
_PROPOSE_MAX_ITEMS: int = 8  # max proposals emitted per run (ADR-0034 §4.3)
_MISSING_PAGE_FANOUT_CAP: int = 5  # max pages from one missing-page fan-out (I7, ADR-0064)

# ── R9-3 (v0.9): purpose.md drift suggestion ─────────────────────────────────────
# The `rationale` column carries BOTH the human-readable "why" AND the exact markdown block
# to append to purpose.md on approve. ADR-0034 §3.1: `resolution` is a small closed enum
# (created|skipped|…, NULL while pending) — the WRONG place for a diff. `rationale` is the
# card body shown to the human, so it is the clean fit. The apply step splits on this marker;
# everything after it is appended VERBATIM to purpose.md.
_PURPOSE_ADDITION_MARKER = "\n\n--- SUGGESTED purpose.md ADDITION ---\n\n"
_PURPOSE_SUGGESTION_TYPE = "purpose-suggestion"

# ── R9-4 (v0.9): schema.md co-evolution (K6, beyond llm_wiki) ────────────────────
# Same architecture as R9-3: the `rationale` column carries BOTH the human-readable "why" AND
# the exact markdown rule block to append to schema.md on approve. The apply step splits on this
# marker; everything after it is appended VERBATIM to schema.md. A DISTINCT marker string (vs.
# the purpose one) is used so the shared apply helper is unambiguous about which file it targets
# and so a future migration could tell the two apart.
_SCHEMA_ADDITION_MARKER = "\n\n--- SUGGESTED schema.md ADDITION ---\n\n"
_SCHEMA_SUGGESTION_TYPE = "schema-suggestion"

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

    `confirm` items get content_key = NULL (never deduped — every confirmation is a distinct
    human ask; ADR-0044 §3.2, Do-NOT #10). normalize() reuses _normalize_title (I9).
    """
    if item_type == "confirm":
        return None
    norm_title = _normalize_title(proposed_title) if proposed_title else ""
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
    # ADR-0044 §4.1: contextual depth — both ride the SAME single proposal call (no extra call).
    referenced_page_titles: list[str] = field(default_factory=list)
    """Existing-vault titles this proposal is about (resolved → referenced_page_ids)."""
    search_queries: list[str] = field(default_factory=list)
    """≤ REVIEW_SEARCH_QUERIES_MAX web-search queries; search_queries[0] seeds Deep Research."""


# ── AI seam implementations (ADR-0034 §11.2) ─────────────────────────────────


async def _llm_propose_reviews(
    *,
    vault_id: str,
    analysis: Analysis,
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
        source_text=source_text,
    )

    # ── ONE bounded call, no loop, no retry (I7) ─────────────────────────────────
    try:
        raw = await asyncio.wait_for(_chat_collect(provider, instruction), timeout=timeout_s)
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
        raw = await asyncio.wait_for(_chat_collect(provider, instruction), timeout=timeout_s)
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


@dataclass
class GenerationOutcome:
    """
    Result of _run_generation — capability-aware (I6). Exactly ONE of the two page channels
    is populated so the caller writes each page exactly once (I1):

      - Orchestrated route (supports_agentic_loop is False): `wiki_page` is the produced page
        and the CALLER writes it via write_wiki_page (`created_page_id` is None).
      - Delegated route (supports_agentic_loop is True): the agentic provider ALREADY wrote the
        page via MCP write_page (→ write_wiki_page); `created_page_id` is that page id and the
        caller MUST NOT write again (`wiki_page` is None).
    """

    wiki_page: WikiPage | None
    created_page_id: str | None
    converged: bool


async def _resolve_delegated_created_page_id(
    written_page_ids: list[str], proposed_title: str
) -> str | None:
    """
    Resolve which page the delegated (agentic) agent created for a Create action.

    The CLI agent writes one or more pages via MCP write_page (recorded ids, ADR-0044 §4.2).
    Prefer the page whose title equals `proposed_title` (casefold+strip compare); otherwise fall
    back to the first written id. Returns None ONLY when the agent wrote nothing — the caller
    then raises → 502, item left pending (no partial create).

    Dialect-portable read (cast id → TEXT) mirrors _propose_reviews_for_delegated (SQLite tests
    store id as TEXT; Postgres native-UUID columns stay matchable). Bounded indexed read by id
    (I1 — no vault re-scan).
    """
    if not written_page_ids:
        return None

    from sqlalchemy import String as _SAString
    from sqlalchemy import cast as _sa_cast

    id_strs = [str(i) for i in written_page_ids]
    async with get_session() as session:
        rows = list(
            (
                await session.execute(
                    select(Page.id, Page.title).where(
                        _sa_cast(Page.id, _SAString).in_(id_strs),
                        Page.deleted_at.is_(None),
                    )
                )
            ).all()
        )
    title_by_id = {str(pid): (title or "") for pid, title in rows}

    norm_target = proposed_title.casefold().strip()
    for pid in id_strs:
        title = title_by_id.get(pid)
        if title is not None and title.casefold().strip() == norm_target:
            return pid
    # No title match → the first page the agent wrote (its ids are already all real writes).
    return id_strs[0]


async def _run_generation(
    *,
    vault_id: str,
    proposed_title: str,
    proposed_page_type: str | None,
    rationale: str | None,
    origin_source: str,
    provider_config_row: object,
    item_type: str | None = None,
) -> GenerationOutcome:
    """
    Bounded on-demand page generation for lazy Create (ADR-0034 §5, implemented).

    CAPABILITY-AWARE ROUTING (I6 — mirrors orchestrator.run_ingest_pipeline):
      caps = provider.capabilities()
      - caps.supports_agentic_loop is True  → DELEGATED: hand the whole single-page create to the
        agentic provider via orchestrator._delegate_ingest(); the agent writes the page through
        MCP write_page (→ write_wiki_page, I1). Resolve the created page id from the written ids;
        the caller MUST NOT write again. Returns GenerationOutcome(created_page_id=...).
      - otherwise                            → ORCHESTRATED: run the bounded
        analyze→generate→validate→retry loop; the CALLER writes the produced WikiPage via
        write_wiki_page. Returns GenerationOutcome(wiki_page=...).
    NEVER isinstance/provider_type/class-name branching (I6) — route ONLY via capabilities().

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

    Bounds (I7 — both routes):
      - max_iter + token_budget from provider_config_row (I7).
      - Wrapped in asyncio.wait_for(timeout).
      - ONE ingest_runs row per run records tokens + total_cost_usd + the $1 anomaly check
        (route='orchestrated' or 'delegated' — same standalone finalize seam, _write_ingest_run).
      - On loop / delegate failure / provider error → raise (the caller handles → 502; item stays
        pending). Delegated route producing zero pages also raises (nothing to mark created).

    Returns:
      GenerationOutcome — orchestrated → wiki_page set (caller writes it via write_wiki_page);
      delegated → created_page_id set (already written by the agent; caller skips the write).
      Raises on failure (caller converts to 502; item left pending — no partial write).
    """
    from app.ingest.loop import run_orchestrated_loop
    from app.ingest.orchestrator import (
        COST_ANOMALY_THRESHOLD_USD,
        _delegate_ingest,
        _ensure_source_summary,
        _load_vault_context,
        _write_ingest_run,
    )
    from app.ingest.provider import resolve_provider
    from app.ingest.provider.base import UsageAccumulator

    # ── Resolve type / dir heuristic (§5.2) ──────────────────────────────────────
    resolved_type = _resolve_create_page_type(
        proposed_title, proposed_page_type, rationale, item_type
    )

    # ── Build the provider + run-scoped ledger ───────────────────────────────────
    provider = resolve_provider(provider_config_row)
    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)
    caps = provider.capabilities()

    # Bounds (I7) from the resolved row.
    max_iter = int(getattr(provider_config_row, "max_iter", None) or 3)
    token_budget = int(getattr(provider_config_row, "token_budget", None) or 60_000)
    timeout_s = float(getattr(settings, "review_propose_timeout_seconds", 30.0)) * max(1, max_iter)

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

    model_id = str(getattr(provider_config_row, "model_id", ""))
    started_at = datetime.now(UTC)

    # ── ROUTE: the single capability check (I6) — mirrors run_ingest_pipeline ─────
    if caps.supports_agentic_loop:
        # DELEGATED (CLI/agentic): the provider runs its own bounded agent loop and writes the
        # page through MCP write_page (→ write_wiki_page, I1). We pass the same vault context the
        # orchestrator's delegated path passes as system_prompt so the agent links to existing
        # pages. The caller MUST NOT write again — the agent already wrote (I1: one write/page).
        delegated_converged = False
        written_page_ids: list[str] = []
        delegate_error: BaseException | None = None
        try:
            delegated_converged, _pages_written, written_page_ids = await asyncio.wait_for(
                _delegate_ingest(
                    provider=provider,
                    source_text=source_text,
                    origin_source=origin_source,
                    system_prompt=vault_context,
                ),
                timeout=timeout_s,
            )
        except TimeoutError as exc:
            delegate_error = exc
        except Exception as exc:  # noqa: BLE001
            delegate_error = exc

        finished_at = datetime.now(UTC)
        # Cost/tokens come from the run-scoped accumulator bound above — the CLI provider records
        # its SDK-reported usage into it (DelegatedIngestResult.usage → _record_usage), exactly
        # as run_ingest_pipeline's delegated route folds cost into the same ledger (I7).
        total_tokens = accumulator.total_tokens
        total_cost_usd = round(accumulator.total_cost_usd, 4)
        cost_anomaly = total_cost_usd > COST_ANOMALY_THRESHOLD_USD

        created_page_id: str | None = None
        if delegate_error is None:
            created_page_id = await _resolve_delegated_created_page_id(
                written_page_ids, proposed_title
            )

        # ── Record ONE ingest_runs row (route='delegated') — same standalone seam ─
        try:
            await _write_ingest_run(
                page_id=None,
                provider_name=caps.name,
                provider_type=caps.mode,
                model_id=model_id,
                route="delegated",
                max_iter_used=0,  # delegated: the agent owns its own (opaque) loop count (I6)
                total_tokens=total_tokens,
                total_cost_usd=total_cost_usd,
                converged=delegated_converged,
                cost_anomaly=cost_anomaly,
                started_at=started_at,
                finished_at=finished_at,
                pages_created=1 if created_page_id is not None else 0,
                error_message=(
                    (str(delegate_error) or delegate_error.__class__.__name__)
                    if delegate_error is not None
                    else None
                ),
            )
        except Exception as run_exc:  # noqa: BLE001
            logger.warning(
                "_run_generation: ingest_runs audit write failed (non-fatal): %s", run_exc
            )

        logger.info(
            "review_create run: provider=%s route=delegated converged=%s tokens=%d "
            "cost_usd=%.4f title=%r",
            caps.name,
            delegated_converged,
            total_tokens,
            total_cost_usd,
            proposed_title,
        )
        if cost_anomaly:
            logger.warning(
                "COST ANOMALY: review Create run total_cost_usd=%.4f exceeds $%.2f "
                "(provider=%s title=%r) — investigate runaway/misconfiguration",
                total_cost_usd,
                COST_ANOMALY_THRESHOLD_USD,
                caps.name,
                proposed_title,
            )

        # Failure → raise (caller → 502, item left pending; no partial write, §5.3).
        if delegate_error is not None:
            raise delegate_error
        if created_page_id is None:
            raise RuntimeError(
                "delegated agent wrote no pages for the Create action (I6 delegated path) — "
                "nothing to mark created (item left pending)"
            )
        return GenerationOutcome(
            wiki_page=None, created_page_id=created_page_id, converged=delegated_converged
        )

    # ── ORCHESTRATED (API/Local): bounded analyze→generate→validate→retry (§5) ────
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
        pages = _ensure_source_summary(loop_result.pages, loop_result.analysis, origin_source)
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
            model_id=model_id,
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
        logger.warning("_run_generation: ingest_runs audit write failed (non-fatal): %s", run_exc)

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
        raise RuntimeError("orchestrated loop produced no page and no fallback (unexpected — §5)")
    return GenerationOutcome(wiki_page=wiki_page, created_page_id=None, converged=converged)


# ── R9-3: purpose.md scope-drift suggestion (v0.9) ───────────────────────────────


async def generate_purpose_suggestion(
    *,
    vault_id: str,
    analysis: Analysis | None,
    written_pages: list[Page],
    origin_source: str,
) -> ReviewItem | None:
    """
    Post-ingest scope-drift check (R9-3). Compare this run's analysis topics/summary against
    the vault purpose.md; if the model judges scope drift (a new recurring theme not covered by
    purpose), emit ONE `purpose-suggestion` ReviewItem. Called fire-and-forget from the
    orchestrator — the caller wraps this in try/except; a failure here NEVER breaks ingest (I7).

    BOUNDS (I7 / R9-3 AC "bounded provider call max_tokens 300, no retry"):
      - Exactly ONE provider.chat() call, no loop, no retry.
      - max_tokens = PURPOSE_SUGGESTION_MAX_TOKENS (300) enforced at the call site.
      - asyncio.wait_for(PURPOSE_SUGGESTION_TIMEOUT_SECONDS).
      - Cost logged to the run ledger via the bound UsageAccumulator (total_cost_usd).
      - On no-provider / timeout / any error / empty / in-scope verdict → return None.

    THROTTLE (R9-3):
      1. Skip if a `purpose-suggestion` is already pending for the vault (max 1 pending at a
         time — no queue spam).
      2. Fire only when ≥ PURPOSE_SUGGESTION_MIN_SOURCES (3) `source` pages have been ingested
         since the newest existing purpose-suggestion item (of any status). Cheap counter: a
         bounded indexed COUNT over pages.created_at vs. the last suggestion's created_at — no
         new column, no migration.

    Returns the created ReviewItem, or None when no suggestion is emitted (in-scope, throttled,
    disabled, or any failure).
    """
    if not bool(getattr(settings, "purpose_suggestion_enabled", True)):
        return None
    if not written_pages:
        return None

    # ── Throttle 1: at most one pending purpose-suggestion per vault ─────────────
    try:
        async with get_session() as session:
            pending_existing = (
                await session.execute(
                    select(ReviewItem.id)
                    .where(
                        ReviewItem.vault_id == vault_id,
                        ReviewItem.item_type == _PURPOSE_SUGGESTION_TYPE,
                        ReviewItem.status == "pending",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            # Newest purpose-suggestion of ANY status → drift counter watermark.
            last_created = (
                await session.execute(
                    select(func.max(ReviewItem.created_at)).where(
                        ReviewItem.vault_id == vault_id,
                        ReviewItem.item_type == _PURPOSE_SUGGESTION_TYPE,
                    )
                )
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.warning("generate_purpose_suggestion: throttle read failed (non-fatal): %s", exc)
        return None

    if pending_existing is not None:
        logger.debug(
            "generate_purpose_suggestion: a purpose-suggestion is already pending (vault=%s) — "
            "skip (throttle 1, zero cost)",
            vault_id,
        )
        return None

    # ── Throttle 2: ≥ N source pages ingested since the last suggestion watermark ─
    min_sources = int(getattr(settings, "purpose_suggestion_min_sources", 3))
    try:
        async with get_session() as session:
            count_stmt = (
                select(func.count())
                .select_from(Page)
                .where(
                    Page.vault_id == vault_id,
                    Page.page_type == PageType.SOURCE.value,
                    Page.deleted_at.is_(None),
                )
            )
            if last_created is not None:
                count_stmt = count_stmt.where(Page.created_at > last_created)
            sources_since = int((await session.execute(count_stmt)).scalar_one() or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_purpose_suggestion: source-counter read failed (non-fatal): %s", exc
        )
        return None

    if sources_since < min_sources:
        logger.debug(
            "generate_purpose_suggestion: only %d source(s) since last check < %d (vault=%s) — "
            "skip (throttle 2, zero cost)",
            sources_since,
            min_sources,
            vault_id,
        )
        return None

    # ── Read purpose.md (tolerant — missing file → empty purpose) ────────────────
    purpose_text = ""
    try:
        purpose_path = settings.vault_root / "purpose.md"
        if purpose_path.exists():
            purpose_text = purpose_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("generate_purpose_suggestion: purpose.md read failed (non-fatal): %s", exc)
        purpose_text = ""

    # ── Resolve provider (I6 — no provider → None, zero cost) ────────────────────
    resolved = await _resolve_review_provider(vault_id)
    if resolved is None:
        logger.debug(
            "generate_purpose_suggestion: no ingest provider resolved (vault=%s) — skip (I6)",
            vault_id,
        )
        return None
    provider, _config_row = resolved

    max_tokens = int(getattr(settings, "purpose_suggestion_max_tokens", 300))
    timeout_s = float(getattr(settings, "purpose_suggestion_timeout_seconds", 20.0))

    from app.ingest.provider.base import UsageAccumulator

    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)

    instruction = _build_purpose_drift_instruction(
        analysis=analysis,
        written_pages=written_pages,
        purpose_text=purpose_text,
        max_tokens=max_tokens,
    )

    # ── ONE bounded call, no loop, no retry (I7) ─────────────────────────────────
    try:
        raw = await asyncio.wait_for(
            _chat_collect(provider, instruction, max_tokens=max_tokens),
            timeout=timeout_s,
        )
    except TimeoutError:
        logger.warning(
            "generate_purpose_suggestion: provider call timed out after %.1fs (vault=%s) — "
            "no suggestion (never fail ingest)",
            timeout_s,
            vault_id,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_purpose_suggestion: provider call failed (vault=%s): %s — no suggestion",
            vault_id,
            exc,
        )
        return None
    finally:
        # I7: cost logged to the run ledger regardless of outcome.
        logger.info(
            "purpose_suggestion provider call: vault=%s tokens=%d cost_usd=%.4f calls=%d",
            vault_id,
            accumulator.total_tokens,
            round(accumulator.total_cost_usd, 4),
            accumulator.calls,
        )

    parsed = _parse_purpose_drift(raw)
    if parsed is None:
        logger.debug(
            "generate_purpose_suggestion: model judged in-scope / no parseable drift (vault=%s)",
            vault_id,
        )
        return None

    theme, why, addition = parsed

    # ── Persist ONE purpose-suggestion ReviewItem ───────────────────────────────
    # rationale = human "why" + delimited exact markdown to append on approve.
    rationale = f"{why}{_PURPOSE_ADDITION_MARKER}{addition}"
    source_page_id = written_pages[0].id if written_pages else None
    content_key = _content_key(
        vault_id=vault_id,
        item_type=_PURPOSE_SUGGESTION_TYPE,
        proposed_title=theme,
    )
    try:
        item = await enqueue_review(
            vault_id=vault_id,
            item_type=_PURPOSE_SUGGESTION_TYPE,
            proposed_title=theme,
            rationale=rationale,
            source_page_id=(uuid.UUID(str(source_page_id)) if source_page_id is not None else None),
            content_key=content_key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_purpose_suggestion: failed to enqueue suggestion (vault=%s): %s",
            vault_id,
            exc,
        )
        return None

    logger.info(
        "generate_purpose_suggestion: vault=%s emitted purpose-suggestion theme=%r item=%s",
        vault_id,
        theme,
        item.id,
    )
    return item


# ── R9-4: schema.md co-evolution suggestion (v0.9, K6) ───────────────────────────


async def generate_schema_suggestion(
    *,
    vault_id: str,
    written_pages: list[Page],
    origin_source: str,
) -> ReviewItem | None:
    """
    Post-ingest schema co-evolution check (R9-4, K6 — beyond llm_wiki). Compare the ingested
    pages' ACTUAL frontmatter/type/tag usage patterns against the vault schema.md rules; if the
    model detects a RECURRING convention that is not yet codified (a tag family, a frontmatter
    field consistently used, a type misfit), emit ONE `schema-suggestion` ReviewItem with the
    exact markdown rule block to append to schema.md. Called fire-and-forget from the orchestrator
    (right after the R9-3 purpose check) — the caller wraps this in try/except; a failure here
    NEVER breaks ingest (I7).

    Architecture MIRRORS generate_purpose_suggestion exactly. Deliberate deltas (documented):
      1. DEFAULT OFF (schema_suggestion_enabled=False). schema.md is the formal frontmatter
         contract (K6); an approved change alters FUTURE ingest classification/validation, so the
         blast radius is larger than a purpose.md note — operator must opt in. (R9-3 defaults ON.)
      2. max_tokens=400 (vs. 300) — the model restates the convention AND emits the rule block.
      3. min_sources default 5 (vs. 3) — a convention should be seen across more material.
      4. Compares real frontmatter (type/tags), not just topics — schema.md governs frontmatter.

    BOUNDS (I7 / R9-4 AC "bounded call max_tokens 400, no retry"):
      - Exactly ONE provider.chat() call, no loop, no retry.
      - max_tokens = SCHEMA_SUGGESTION_MAX_TOKENS (400) enforced at the call site (_chat_collect).
      - asyncio.wait_for(SCHEMA_SUGGESTION_TIMEOUT_SECONDS).
      - Cost logged to the run ledger via the bound UsageAccumulator (total_cost_usd).
      - On disabled / no-provider / timeout / any error / empty / no-pattern → return None.

    THROTTLE (R9-4, identical shape to R9-3):
      1. Skip if a `schema-suggestion` is already pending for the vault (max 1 pending — no spam).
      2. Fire only when ≥ SCHEMA_SUGGESTION_MIN_SOURCES (5) `source` pages have been ingested
         since the newest existing schema-suggestion item (of any status). Cheap bounded COUNT
         over pages.created_at vs. the last suggestion's created_at — no new column, no migration.

    Returns the created ReviewItem, or None (disabled, in-schema, throttled, or any failure).
    """
    if not bool(getattr(settings, "schema_suggestion_enabled", False)):
        return None
    if not written_pages:
        return None

    # ── Throttle 1: at most one pending schema-suggestion per vault ──────────────
    try:
        async with get_session() as session:
            pending_existing = (
                await session.execute(
                    select(ReviewItem.id)
                    .where(
                        ReviewItem.vault_id == vault_id,
                        ReviewItem.item_type == _SCHEMA_SUGGESTION_TYPE,
                        ReviewItem.status == "pending",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            # Newest schema-suggestion of ANY status → drift counter watermark.
            last_created = (
                await session.execute(
                    select(func.max(ReviewItem.created_at)).where(
                        ReviewItem.vault_id == vault_id,
                        ReviewItem.item_type == _SCHEMA_SUGGESTION_TYPE,
                    )
                )
            ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.warning("generate_schema_suggestion: throttle read failed (non-fatal): %s", exc)
        return None

    if pending_existing is not None:
        logger.debug(
            "generate_schema_suggestion: a schema-suggestion is already pending (vault=%s) — "
            "skip (throttle 1, zero cost)",
            vault_id,
        )
        return None

    # ── Throttle 2: ≥ N source pages ingested since the last suggestion watermark ─
    min_sources = int(getattr(settings, "schema_suggestion_min_sources", 5))
    try:
        async with get_session() as session:
            count_stmt = (
                select(func.count())
                .select_from(Page)
                .where(
                    Page.vault_id == vault_id,
                    Page.page_type == PageType.SOURCE.value,
                    Page.deleted_at.is_(None),
                )
            )
            if last_created is not None:
                count_stmt = count_stmt.where(Page.created_at > last_created)
            sources_since = int((await session.execute(count_stmt)).scalar_one() or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_schema_suggestion: source-counter read failed (non-fatal): %s", exc
        )
        return None

    if sources_since < min_sources:
        logger.debug(
            "generate_schema_suggestion: only %d source(s) since last check < %d (vault=%s) — "
            "skip (throttle 2, zero cost)",
            sources_since,
            min_sources,
            vault_id,
        )
        return None

    # ── Read schema.md (tolerant — missing file → empty schema) ──────────────────
    schema_text = ""
    try:
        schema_path = settings.vault_root / "schema.md"
        if schema_path.exists():
            schema_text = schema_path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.warning("generate_schema_suggestion: schema.md read failed (non-fatal): %s", exc)
        schema_text = ""

    # ── Resolve provider (I6 — no provider → None, zero cost) ────────────────────
    resolved = await _resolve_review_provider(vault_id)
    if resolved is None:
        logger.debug(
            "generate_schema_suggestion: no ingest provider resolved (vault=%s) — skip (I6)",
            vault_id,
        )
        return None
    provider, _config_row = resolved

    max_tokens = int(getattr(settings, "schema_suggestion_max_tokens", 400))
    timeout_s = float(getattr(settings, "schema_suggestion_timeout_seconds", 20.0))

    from app.ingest.provider.base import UsageAccumulator

    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)

    instruction = _build_schema_pattern_instruction(
        written_pages=written_pages,
        schema_text=schema_text,
        max_tokens=max_tokens,
    )

    # ── ONE bounded call, no loop, no retry (I7) ─────────────────────────────────
    try:
        raw = await asyncio.wait_for(
            _chat_collect(provider, instruction, max_tokens=max_tokens),
            timeout=timeout_s,
        )
    except TimeoutError:
        logger.warning(
            "generate_schema_suggestion: provider call timed out after %.1fs (vault=%s) — "
            "no suggestion (never fail ingest)",
            timeout_s,
            vault_id,
        )
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_schema_suggestion: provider call failed (vault=%s): %s — no suggestion",
            vault_id,
            exc,
        )
        return None
    finally:
        # I7: cost logged to the run ledger regardless of outcome.
        logger.info(
            "schema_suggestion provider call: vault=%s tokens=%d cost_usd=%.4f calls=%d",
            vault_id,
            accumulator.total_tokens,
            round(accumulator.total_cost_usd, 4),
            accumulator.calls,
        )

    parsed = _parse_schema_pattern(raw)
    if parsed is None:
        logger.debug(
            "generate_schema_suggestion: model found no new codifiable convention (vault=%s)",
            vault_id,
        )
        return None

    convention, why, addition = parsed

    # ── Persist ONE schema-suggestion ReviewItem ────────────────────────────────
    # rationale = human "why" + delimited exact markdown rule block to append on approve.
    rationale = f"{why}{_SCHEMA_ADDITION_MARKER}{addition}"
    source_page_id = written_pages[0].id if written_pages else None
    content_key = _content_key(
        vault_id=vault_id,
        item_type=_SCHEMA_SUGGESTION_TYPE,
        proposed_title=convention,
    )
    try:
        item = await enqueue_review(
            vault_id=vault_id,
            item_type=_SCHEMA_SUGGESTION_TYPE,
            proposed_title=convention,
            rationale=rationale,
            source_page_id=(uuid.UUID(str(source_page_id)) if source_page_id is not None else None),
            content_key=content_key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "generate_schema_suggestion: failed to enqueue suggestion (vault=%s): %s",
            vault_id,
            exc,
        )
        return None

    logger.info(
        "generate_schema_suggestion: vault=%s emitted schema-suggestion convention=%r item=%s",
        vault_id,
        convention,
        item.id,
    )
    return item


def _build_schema_pattern_instruction(
    *,
    written_pages: list[Page],
    schema_text: str,
    max_tokens: int,
) -> str:
    """
    Build the single bounded schema co-evolution prompt (R9-4). Asks the model to compare the
    ACTUAL frontmatter/type/tag usage of the newly ingested pages against the vault's schema.md
    rules; if a recurring convention is not yet codified, name it and propose the exact markdown
    rule block to add to schema.md. Model returns ONLY JSON.
    """
    frontmatter_digest = _digest_frontmatter(written_pages)
    schema_block = schema_text.strip() or "(schema.md is empty or missing)"

    return (
        "You maintain the schema.md of a self-organizing wiki. schema.md is the FORMAL contract "
        "for page frontmatter: required fields, allowed `type` values, tag conventions, and "
        "wikilink style. Given the current schema.md and the ACTUAL frontmatter (type, tags, and "
        "which fields are present) of a batch of newly ingested pages, judge whether the pages "
        "reveal a RECURRING convention that is NOT yet codified in schema.md — for example: a tag "
        "family used consistently, a frontmatter field present on most pages but not required by "
        "schema.md, or a `type` value that is over/under-used in a way the rules do not describe.\n"
        "\n"
        "Be conservative: propose a change ONLY for a genuine, recurring, useful convention. If "
        "the pages already conform to schema.md, or the pattern is incidental / one-off, report "
        "no change. Do NOT invent conventions the pages do not actually exhibit.\n\n"
        f"# Current schema.md\n{schema_block}\n\n"
        f"# Frontmatter of pages written this run\n{frontmatter_digest}\n\n"
        "Return ONLY a JSON object. If no schema change is warranted, return "
        '{"needs_change": false}. If a new convention SHOULD be codified, return '
        '{"needs_change": true, "convention": "<short name of the convention, ≤6 words>", '
        '"why": "<one sentence: what recurring pattern you observed and why schema.md should '
        'capture it>", "addition": "<a short markdown section (heading + the exact rule text) to '
        'APPEND to schema.md codifying this convention>"}.\n'
        f"Keep the output well under {max_tokens} tokens. Return no prose, only the JSON object."
    )


def _parse_schema_pattern(raw: str) -> tuple[str, str, str] | None:
    """
    Parse the schema-pattern JSON. Returns (convention, why, addition) on a valid change verdict,
    else None (no change, empty, or unparseable — degrade-safe, never raises).
    """
    if not raw:
        return None
    obj = _loads_json_lenient(raw)
    if not isinstance(obj, dict):
        return None
    # Explicit no-change, or missing change fields → no suggestion.
    if obj.get("needs_change") is False:
        return None
    convention = _clean_str(obj.get("convention"))
    addition = _clean_str(obj.get("addition"))
    if not convention or not addition:
        return None
    why = (
        _clean_str(obj.get("why"))
        or f"Recurring frontmatter convention not in schema: {convention}."
    )
    return convention, why, addition


def _digest_frontmatter(written_pages: list[Page], *, max_pages: int = 20) -> str:
    """
    Compact frontmatter digest of the written pages for the schema-pattern prompt (R9-4).

    Unlike _digest_written_pages (title + type only), this surfaces the fields schema.md actually
    governs: `type`, `tags[]`, and whether `sources[]` is present. Bounded (max_pages); no full
    page content (I1). Used only by the schema check.
    """
    lines: list[str] = []
    for page in written_pages[:max_pages]:
        title = (page.title or "").strip() or "(untitled)"
        ptype = (page.page_type or "").strip() or "?"
        tags = getattr(page, "tags", None) or []
        tags_str = ", ".join(str(t) for t in tags[:10]) if tags else "(none)"
        has_sources = "yes" if (getattr(page, "sources", None) or []) else "no"
        lines.append(f"- {title} | type={ptype} | tags=[{tags_str}] | sources={has_sources}")
    return "\n".join(lines) if lines else "(none)"


async def _apply_suggestion_to_file(
    item: ReviewItem,
    *,
    target_filename: str,
    marker: str,
    label: str,
) -> None:
    """
    Shared apply helper for the two vault-file co-evolution suggestions (R9-3 purpose.md /
    R9-4 schema.md). Appends the suggested block (the text after *marker* in `item.rationale`,
    falling back to proposed_title) to `vault/<target_filename>`, then bumps data_version and
    notifies the graph cache (the same seam write_wiki_page uses). Idempotency is the caller's
    concern (the item is marked `created` in the same transaction path); this function only
    performs the filesystem append + version bump.

    Parameterized by target file so purpose.md and schema.md share ONE code path (R9-4 AC:
    "factor the shared apply logic … into one helper parameterized by target file"). The only
    per-type inputs are the filename, the rationale marker, and a log label.

    Raises on write failure — the caller (create_page_from_review) converts to 502 and leaves
    the item pending (no partial state).
    """
    addition = _extract_addition(item.rationale, marker) or (item.proposed_title or "").strip()
    if not addition:
        raise RuntimeError(f"{label} has no addition text to apply")

    target_path = settings.vault_root / target_filename
    # Read existing (tolerant), append with a clean separator, write back.
    existing = ""
    if target_path.exists():
        existing = target_path.read_text(encoding="utf-8")
    target_path.parent.mkdir(parents=True, exist_ok=True)

    if not existing or existing.endswith("\n\n"):
        sep = ""
    elif existing.endswith("\n"):
        sep = "\n"
    else:
        sep = "\n\n"
    new_content = f"{existing}{sep}{addition.rstrip()}\n"
    target_path.write_text(new_content, encoding="utf-8")

    # Bump data_version (same monotonic +1 as the ingest write seam, AC-F16dv-2). Column-scoped
    # UPDATE (portable, no ORM full-entity select) so a partial vault_state schema still bumps.
    from sqlalchemy import update as _sa_update

    async with get_session() as session:
        await session.execute(
            _sa_update(VaultState)
            .where(VaultState.vault_id == settings.vault_id)
            .values(
                data_version=VaultState.data_version + 1,
                updated_at=datetime.now(UTC),
            )
        )

    # Notify graph cache of the bump (best-effort; skipped when the cache is not ready).
    try:
        from app.main import _graph_cache

        if _graph_cache is not None:
            # Read ONLY data_version (portable column-scoped select — avoids ORM full-entity
            # selects that would require every VaultState column in narrow test schemas).
            async with get_session() as session:
                new_version = (
                    await session.execute(
                        select(VaultState.data_version).where(
                            VaultState.vault_id == settings.vault_id
                        )
                    )
                ).scalar_one_or_none() or 0
            _graph_cache.notify_bump(new_version)
    except Exception:  # noqa: BLE001
        logger.debug("%s: graph cache notify_bump skipped (cache not ready)", label)

    logger.info(
        "%s: appended %d chars to %s (vault=%s item=%s)",
        label,
        len(addition),
        target_filename,
        item.vault_id,
        item.id,
    )


async def apply_purpose_suggestion(item: ReviewItem) -> None:
    """
    Apply a `purpose-suggestion` to vault/purpose.md (R9-3 approve/create action).

    Thin wrapper over the shared _apply_suggestion_to_file helper (R9-4 refactor): appends the
    suggested section (the block after _PURPOSE_ADDITION_MARKER in `rationale`) to purpose.md,
    bumps data_version, and notifies the graph cache. Raises on write failure.
    """
    await _apply_suggestion_to_file(
        item,
        target_filename="purpose.md",
        marker=_PURPOSE_ADDITION_MARKER,
        label="apply_purpose_suggestion",
    )


async def apply_schema_suggestion(item: ReviewItem) -> None:
    """
    Apply a `schema-suggestion` to vault/schema.md (R9-4 approve/create action).

    Thin wrapper over the shared _apply_suggestion_to_file helper: appends the suggested rule
    block (the text after _SCHEMA_ADDITION_MARKER in `rationale`) to schema.md, bumps
    data_version, and notifies the graph cache. Raises on write failure. schema.md changes affect
    FUTURE ingest classification/validation — see settings.schema_suggestion_enabled docstring.
    """
    await _apply_suggestion_to_file(
        item,
        target_filename="schema.md",
        marker=_SCHEMA_ADDITION_MARKER,
        label="apply_schema_suggestion",
    )


def _extract_addition(rationale: str | None, marker: str) -> str | None:
    """Return the exact markdown addition stored after *marker* in *rationale*, else None."""
    if not rationale or marker not in rationale:
        return None
    addition = rationale.split(marker, 1)[1].strip()
    return addition or None


def _extract_purpose_addition(rationale: str | None) -> str | None:
    """Purpose-specific wrapper over _extract_addition (kept for the R9-3 test surface)."""
    return _extract_addition(rationale, _PURPOSE_ADDITION_MARKER)


def _extract_schema_addition(rationale: str | None) -> str | None:
    """Schema-specific wrapper over _extract_addition (R9-4 apply/test surface)."""
    return _extract_addition(rationale, _SCHEMA_ADDITION_MARKER)


def _build_purpose_drift_instruction(
    *,
    analysis: Analysis | None,
    written_pages: list[Page],
    purpose_text: str,
    max_tokens: int,
) -> str:
    """
    Build the single bounded scope-drift prompt (R9-3). Asks the model to judge whether the newly
    ingested content is within the vault's stated purpose/scope; if NOT, to name the recurring
    theme and propose a short markdown section to add to purpose.md. Model returns ONLY JSON.
    """
    topics: list[str] = []
    summary = ""
    if analysis is not None:
        topics = list(getattr(analysis, "topics", []) or [])
        summary = (getattr(analysis, "summary", None) or "").strip()
    topics_block = ", ".join(topics[:20]) or "(none)"
    pages_digest = _digest_written_pages(written_pages)
    purpose_block = purpose_text.strip() or "(purpose.md is empty or missing)"

    return (
        "You maintain the purpose.md of a self-organizing wiki. purpose.md declares the vault's "
        "goal, scope, key questions, and thesis. Given the vault's current purpose.md and the "
        "topics/summary of newly ingested content, judge whether the new content represents a "
        "RECURRING THEME that is NOT already covered by the stated purpose (scope drift).\n\n"
        "Be conservative: if the new content clearly fits the existing scope, report in-scope.\n\n"
        f"# Current purpose.md\n{purpose_block}\n\n"
        f"# Newly ingested topics\n{topics_block}\n\n"
        f"# Newly ingested summary\n{summary or '(none)'}\n\n"
        f"# Pages written this run\n{pages_digest}\n\n"
        'Return ONLY a JSON object. If the content is within scope, return {"in_scope": true}. '
        'If there IS scope drift, return {"in_scope": false, "theme": "<short theme name, ≤6 '
        'words>", "why": "<one sentence: why this is outside current scope>", "addition": '
        '"<a short markdown section (heading + 1-3 sentences) to append to purpose.md that '
        'widens the scope to cover this theme>"}.\n'
        f"Keep the output well under {max_tokens} tokens. Return no prose, only the JSON object."
    )


def _parse_purpose_drift(raw: str) -> tuple[str, str, str] | None:
    """
    Parse the drift JSON. Returns (theme, why, addition) on a valid drift verdict, else None
    (in-scope, empty, or unparseable — degrade-safe, never raises).
    """
    if not raw:
        return None
    obj = _loads_json_lenient(raw)
    if not isinstance(obj, dict):
        return None
    # Explicit in-scope, or missing drift fields → no suggestion.
    if obj.get("in_scope") is True:
        return None
    theme = _clean_str(obj.get("theme"))
    addition = _clean_str(obj.get("addition"))
    if not theme or not addition:
        return None
    why = _clean_str(obj.get("why")) or f"New recurring theme not covered by purpose: {theme}."
    return theme, why, addition


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
) -> ReviewItem:
    """
    Idempotent upsert of one review_items proposal row (ADR-0044 §3.4, supersedes ADR-0034 §3.2).

    Pure DB write — NEVER calls a provider (fire-and-forget from propose_reviews,
    which is itself called fire-and-forget from the orchestrator).

    item_type must be one of: missing-page | suggestion | contradiction | duplicate | confirm.

    IDEMPOTENCY (ADR-0044 §3.4 / Do-NOT #2):
      When content_key is non-NULL, this is an UPSERT-on-(vault_id, content_key):
        - no existing row              → INSERT a new pending row (first sighting)
        - existing row is 'pending'    → refresh rationale/referenced_page_ids/search_queries
                                         IN PLACE (keep id + created_at; the human hasn't acted)
        - existing row is terminal     → NO-OP (respect the human's prior skip/dismiss/create)
      A single bounded indexed read (the new partial-unique index) — the portable contract that
      the Postgres partial-unique index enforces at the DB level (SQLite emulates via this read).

    When content_key is NULL (i.e. `confirm`, or legacy/rule with no key) → always INSERT
    (no dedup — every confirmation is a distinct human ask; Do-NOT #10).

    page_id / source_page_id / created_page_id are stored as string UUIDs for
    SQLite/Postgres compat (with_variant pattern).
    """
    page_id_str = str(page_id) if page_id is not None else None
    source_page_id_str = str(source_page_id) if source_page_id is not None else None
    ref_ids = list(referenced_page_ids) if referenced_page_ids else None
    queries = list(search_queries) if search_queries else None

    async with get_session() as session:
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
    content_key = _content_key(vault_id=vault_id, item_type="suggestion", proposed_title=title)

    try:
        return await enqueue_review(
            vault_id=vault_id,
            item_type="suggestion",
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


async def propose_reviews(
    *,
    vault_id: str,
    analysis: Analysis,
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

    Total proposals are capped at _PROPOSE_MAX_ITEMS across both passes.
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
    dangling_targets: set[str] = set()
    # ADR-0044 §4.1: remember the referencing (written) page per dangling target so the
    # rule-based proposal can carry [referencing page id] as its referenced_page_ids seed.
    dangling_referrer: dict[str, str] = {}
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
            for r in rows:
                # First referring written page wins (stable, bounded).
                dangling_referrer.setdefault(r.target_title, str(r.source_page_id))

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
                referrer = dangling_referrer.get(target_title)
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
                        search_queries=[target_title],
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
                source_text=source_text,
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
                async with get_session() as session:
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

        # ── ADR-0044 §3.2: stable content_key (confirm → NULL, never deduped) ────
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
        async with get_session() as session:
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

            # Check 2: slug match (R4 parity — llm_wiki checks byId via slug).
            # Handles the case where the page was created with a different title but
            # the same slug (e.g. proposed_title="Attention Mechanism" created as
            # wiki/concepts/attention-mechanism.md with title "The Attention Mechanism").
            if existing is None:
                slug = normalized_title.replace(" ", "-")
                async with get_session() as session:
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
        async with get_session() as session:
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
                async with get_session() as session:
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
                    .limit(batch_size * max_batches)
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


# nashsu/llm_wiki cleanCandidateTitle regexes (review-create-page.ts:11-23) — port for D7 parity.
_ACTION_PREFIX_RE = re.compile(
    r"^(Create|Save|Add|Missing page|Missing pages|缺失页面|缺少页面|创建|保存|新增)[:：\s-]*",
    re.IGNORECASE,
)
_MISSING_PREFIX_RE = re.compile(r"^(missing|缺失|缺少)\s*", re.IGNORECASE)
_PAGE_SUFFIX_RE = re.compile(r"\s*(page|pages|页面|页)\s*$", re.IGNORECASE)
_TYPE_SUFFIX_RE = re.compile(
    r"\s*(entity|entities|concept|concepts|实体|概念)\s*(page|pages|页面|页)?\s*$", re.IGNORECASE
)
# Wrap chars stripped from both ends (trailing set also strips colons/periods), mirroring the JS.
_WRAP = "\\s\"'“”‘’`\\[\\]【】()（）"
_WRAP_CHARS_RE = re.compile(f"^[{_WRAP}]+|[{_WRAP}:：.。]+$")


def _clean_candidate_title(value: str) -> str:
    """
    Port of nashsu/llm_wiki ``cleanCandidateTitle`` (review-create-page.ts:15-23): strip action
    prefixes ("Create:", "Missing page:"), a leading "missing", trailing page/entity/concept
    nouns, and wrapping quotes/brackets/punctuation — so a review-created page title is as clean
    as the reference's (was: Synapse kept prefixes like "Missing page: …" in the generated title).
    """
    s = _ACTION_PREFIX_RE.sub("", value)
    s = _MISSING_PREFIX_RE.sub("", s)
    s = _PAGE_SUFFIX_RE.sub("", s)
    s = _TYPE_SUFFIX_RE.sub("", s)
    s = _WRAP_CHARS_RE.sub("", s)
    return s.strip()


def _extract_missing_page_candidates(proposed_title: str) -> list[str]:
    """
    Split a missing-page proposed_title into individual candidate page titles (R1 parity).

    Ports the extractMissingPageCandidates / splitCandidateList logic from the reference
    (nashsu/llm_wiki src/lib/review-create-page.ts:34), adapted for the Python data model
    where the input is the item's proposed_title string.

    Split rules (applied in sequence):
      1. Replace standalone " and " (whole-word, case-insensitive) with ","
      2. Replace " e " surrounded by whitespace (Italian conjunction) with ","
         Note: uses whitespace-bounded matching (not word-boundary) so single-letter
         list items like "A, B, C, D, E, F" are never consumed by this rule.
      3. Split on: "," / "，" / "、" / ";" / "；"
    After splitting: strip whitespace; drop empty strings; deduplicate case-insensitively
    (first-seen casing preserved). Cap at _MISSING_PAGE_FANOUT_CAP (I7 — bounded fan-out).
    If the result has ≤1 usable candidate, return [proposed_title] unchanged — this preserves
    existing single-page Create behavior with no regression for ordinary single-title items.

    NOTE on lint/review queue separation: Synapse keeps lint findings OUT of the review
    queue. The explicit send-to-review bridge already gives parity of capability without the
    reference's noise. See ADR-0064 §3.
    """
    # Normalize list conjunctions to commas.
    # \band\b: whole-word safe — won't split "Android" or "handle".
    # \s+e\s+: Italian "e" only when surrounded by whitespace — won't fire on single-letter
    # list items like "A, B, C, D, E, F" where "E" is adjacent to commas, not spaces.
    text = re.sub(r"\band\b", ",", proposed_title, flags=re.IGNORECASE)
    text = re.sub(r"\s+e\s+", ",", text, flags=re.IGNORECASE)  # Italian "e" (surrounded by spaces)

    # Split on list delimiters
    parts = re.split(r"[,，、;；]+", text)

    # Strip, filter empties, deduplicate (case-insensitive; first-seen casing preserved)
    seen_lower: set[str] = set()
    candidates: list[str] = []
    for part in parts:
        cleaned = _clean_candidate_title(part)
        if not cleaned:
            continue
        lower = cleaned.lower()
        if lower in seen_lower:
            continue
        seen_lower.add(lower)
        candidates.append(cleaned)

    # Cap (I7 — bounded fan-out; avoids runaway provider calls for pathological titles)
    candidates = candidates[:_MISSING_PAGE_FANOUT_CAP]

    # Preserve single-page behavior when no real split occurred — but still clean the title (D7).
    if len(candidates) <= 1:
        return [_clean_candidate_title(proposed_title) or proposed_title]

    return candidates


async def create_page_from_review(item_id: uuid.UUID) -> ReviewItem:
    """
    Lazy on-demand Create action (ADR-0034 §5).

    Flow:
      1. Load the review item (404 if absent; 409 if status != 'pending').
      2. Resolve the ingest provider (409 if none configured — I6).
      3. Call _run_generation — capability-aware (I6). On any failure (generation error, or the
         delegated agent writing zero pages) → 502, item stays pending (no partial write).
      4. Resolve the created page id (I1 — exactly one write):
         - delegated route → the agent ALREADY wrote via MCP write_page; use its returned id.
         - orchestrated route → write the produced WikiPage via write_wiki_page now.
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

    # ── R9-3: purpose-suggestion routing — apply to purpose.md, NOT a wiki page ──
    # This item type does not create a wiki page; approve appends the suggested section to
    # vault/purpose.md and bumps data_version, then marks the item created. No provider call.
    if item.item_type == _PURPOSE_SUGGESTION_TYPE:
        try:
            await apply_purpose_suggestion(item)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "create_page_from_review: apply_purpose_suggestion failed for item=%s: %s "
                "— item left pending",
                item_id_str,
                exc,
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Failed to apply purpose.md suggestion: {exc}. "
                    "Item left pending — retry or dismiss."
                ),
            ) from exc

        async with get_session() as session:
            row_ps = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
            item_ps = row_ps.scalar_one_or_none()
            if item_ps is None:
                raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")
            item_ps.status = "created"
            item_ps.resolution = "created"
            item_ps.reviewed_at = datetime.now(UTC)
            item_ps.reviewed_by = "web-ui"
            await session.flush()
            await session.refresh(item_ps)
            session.expunge(item_ps)

        logger.info(
            "create_page_from_review: applied purpose-suggestion item=%s to purpose.md vault=%s",
            item_id_str,
            vault_id,
        )
        return item_ps

    # ── R9-4: schema-suggestion routing — apply to schema.md, NOT a wiki page ────
    # Same shape as the purpose-suggestion branch above: this item type does not create a wiki
    # page; approve appends the suggested rule block to vault/schema.md and bumps data_version,
    # then marks the item created. No provider call. schema.md changes affect FUTURE ingest.
    if item.item_type == _SCHEMA_SUGGESTION_TYPE:
        try:
            await apply_schema_suggestion(item)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "create_page_from_review: apply_schema_suggestion failed for item=%s: %s "
                "— item left pending",
                item_id_str,
                exc,
            )
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Failed to apply schema.md suggestion: {exc}. "
                    "Item left pending — retry or dismiss."
                ),
            ) from exc

        async with get_session() as session:
            row_ss = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
            item_ss = row_ss.scalar_one_or_none()
            if item_ss is None:
                raise HTTPException(status_code=404, detail=f"Review item {item_id} not found")
            item_ss.status = "created"
            item_ss.resolution = "created"
            item_ss.reviewed_at = datetime.now(UTC)
            item_ss.reviewed_by = "web-ui"
            await session.flush()
            await session.refresh(item_ss)
            session.expunge(item_ss)

        logger.info(
            "create_page_from_review: applied schema-suggestion item=%s to schema.md vault=%s",
            item_id_str,
            vault_id,
        )
        return item_ss

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
                        select(Page.file_path).where(Page.id == str(item.source_page_id))
                    )
                ).scalar_one_or_none()
            origin_source = src_row or f"review:{item_id_str}"
        except Exception:  # noqa: BLE001
            origin_source = f"review:{item_id_str}"
    else:
        origin_source = f"review:{item_id_str}"

    # ── 4. Build candidate list for missing-page fan-out (R1 parity, ADR-0064) ────
    # For missing-page items the proposed_title may encode a comma/、/"and"/"e" separated
    # list of pages to create (one per candidate, each exactly one data_version bump — I1,
    # bounded by _MISSING_PAGE_FANOUT_CAP — I7). All other item types keep the existing
    # single-page path completely unchanged.
    if item.item_type == "missing-page":
        candidates = _extract_missing_page_candidates(proposed_title)
    else:
        # D7 parity: clean the title (strip "Create:"/quote noise) for the generated page too.
        candidates = [_clean_candidate_title(proposed_title) or proposed_title]

    # ── 5. Generate each candidate page (I1 — one write per page; bounded fan-out) ──
    # Primary (first) candidate failure → 502, item left pending (identical to pre-fan-out
    # single-page behavior). Secondary candidate failures are logged and skipped; the primary
    # is already committed at that point.
    from app.ingest.orchestrator import write_wiki_page  # lazy; avoids circular at module level

    created_page_ids: list[str] = []

    for _candidate_idx, candidate_title in enumerate(candidates):
        _is_primary = _candidate_idx == 0

        # Generation AI seam — capability-aware (I6)
        try:
            outcome = await _run_generation(
                vault_id=vault_id,
                proposed_title=candidate_title,
                proposed_page_type=proposed_page_type,
                rationale=item.rationale,
                origin_source=origin_source,
                provider_config_row=provider_config_row,
                item_type=item.item_type,
            )
        except NotImplementedError as nie:
            if _is_primary:
                logger.warning(
                    "create_page_from_review: _run_generation raised NotImplementedError"
                    " (ADR-0034 §5): %s",
                    nie,
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        "Page generation raised NotImplementedError (ADR-0034 §5). "
                        "Item left pending — retry or skip."
                    ),
                ) from nie
            logger.warning(
                "create_page_from_review: secondary candidate %r NotImplementedError"
                " — skipping: %s",
                candidate_title,
                nie,
            )
            continue
        except Exception as exc:  # noqa: BLE001
            if _is_primary:
                logger.error(
                    "create_page_from_review: generation failed for item=%s: %s"
                    " — item left pending",
                    item_id_str,
                    exc,
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"Page generation failed: {exc}. " "Item left pending — retry or skip."
                    ),
                ) from exc
            logger.warning(
                "create_page_from_review: secondary candidate %r generation failed"
                " — skipping: %s",
                candidate_title,
                exc,
            )
            continue

        # Resolve the created page id for this candidate (I1 — exactly one write per page).
        # Delegated route: agentic provider ALREADY wrote via MCP write_page — use its id,
        # do NOT write again (never double-write, I1).
        # Orchestrated route: write the produced WikiPage now via the single incremental seam.
        if outcome.created_page_id is not None:
            candidate_page_id_str = outcome.created_page_id
        elif outcome.wiki_page is not None:
            try:
                created_page = await write_wiki_page(None, outcome.wiki_page, origin_source)
                candidate_page_id_str = str(created_page.id)
            except Exception as exc:  # noqa: BLE001
                if _is_primary:
                    logger.error(
                        "create_page_from_review: write_wiki_page failed for item=%s: %s"
                        " — item pending",
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
                logger.warning(
                    "create_page_from_review: secondary candidate %r write failed"
                    " — skipping: %s",
                    candidate_title,
                    exc,
                )
                continue
        else:
            # Defensive: _run_generation raises rather than returning an empty outcome.
            # Guard anyway so a future regression surfaces as a clean 502 — never a partial.
            if _is_primary:
                logger.error(
                    "create_page_from_review: generation produced no page for item=%s"
                    " — item pending",
                    item_id_str,
                )
                raise HTTPException(
                    status_code=502,
                    detail="Page generation produced no page. Item left pending — retry or skip.",
                )
            logger.warning(
                "create_page_from_review: secondary candidate %r produced no page — skipping",
                candidate_title,
            )
            continue

        created_page_ids.append(candidate_page_id_str)
        logger.debug(
            "create_page_from_review: item=%s candidate[%d]=%r → page=%s",
            item_id_str,
            _candidate_idx,
            candidate_title,
            candidate_page_id_str,
        )

    # Defensive guard: the primary branch always raises above, so this is unreachable in
    # practice; kept so a future regression surfaces as a clean 502.
    if not created_page_ids:
        logger.error(
            "create_page_from_review: no pages created for item=%s — item pending",
            item_id_str,
        )
        raise HTTPException(
            status_code=502,
            detail="No pages were created. Item left pending — retry or skip.",
        )

    # The primary (first) created page is recorded on the review item for API compatibility
    # (existing callers expect a single created_page_id — ADR-0064 §4 / I8).
    created_page_id_str = created_page_ids[0]

    # ── 6. Set item to created ─────────────────────────────────────────────────
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
) -> ReviewQueuePage:
    """
    Return a paginated ReviewQueuePage for GET /review/queue (ADR-0034 §7, ADR-0044 §6 filter).

    The ?status= filter (ADR-0044 §6) partitions the queue:
      pending (default) | resolved | dismissed | all.
    Ordered by created_at ASC. limit is capped at 200 by the REST endpoint (I7).
    """
    status_values = _status_filter_values(status)

    async with get_session() as session:
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

        total: int = (await session.execute(count_stmt)).scalar_one()
        data_stmt = data_stmt.offset(offset).limit(limit)
        rows = list((await session.execute(data_stmt)).scalars().all())
        for r in rows:
            session.expunge(r)

    return ReviewQueuePage(items=rows, total=total, limit=limit, offset=offset)


@dataclass
class BulkResult:
    """Result of bulk_update_reviews (ADR-0044 §6)."""

    updated: int
    skipped_terminal: int


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
    async with get_session() as session:
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

    async with get_session() as session:
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
        s = _clean_str(entry)
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
        s = _clean_str(entry)
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

    async with get_session() as session:
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

    # Schedule the background task. R7-5: pass the curated seed_queries so the first research
    # round uses them verbatim (no re-generation) — AC-R7-5-2.
    _asyncio.create_task(
        run_deep_research(
            vault_id=effective_vault_id,
            topic=topic,
            max_iter=frozen_max_iter,
            token_budget=frozen_token_budget,
            run_id=run_id,
            seed_queries=seed_queries or None,
        )
    )

    logger.info(
        "deep_research action: review_item_id=%s → run_id=%s vault=%s topic=%r seeds=%d",
        item_id_str,
        run_id_str,
        effective_vault_id,
        topic,
        len(seed_queries),
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


async def _chat_collect(provider: Any, instruction: str, *, max_tokens: int | None = None) -> str:
    """
    Run ONE capability-agnostic provider.chat() turn and collect the full text (I6).

    Rides the existing chat() seam (same surface ops/deep_research.py uses) so the call is
    backend-neutral — no new ABC method, no isinstance/type branching. Usage is recorded out
    of band onto the bound accumulator by the provider. Returns the concatenated text.

    max_tokens (R9-3 / UXB-1 pattern): the chat() ABC intentionally has ONE neutral 2-arg
    surface (I6 — no per-backend max_tokens plumbing). The output bound is therefore enforced
    HERE at the collection site: streaming stops once ~max_tokens worth of text is collected
    (~4 chars/token). Combined with the single call + no retry + wait_for timeout, this caps
    the call cost regardless of backend.
    """
    from app.ingest.schemas import Message

    char_cap: int | None = max_tokens * 4 if max_tokens else None
    chunks: list[str] = []
    collected = 0
    async for chunk in await provider.chat(
        messages=[Message(role="user", content=instruction)],
        retrieval_context="",
    ):
        chunks.append(chunk)
        collected += len(chunk)
        if char_cap is not None and collected >= char_cap:
            break
    text = "".join(chunks).strip()
    if char_cap is not None and len(text) > char_cap:
        text = text[:char_cap]
    return text


def _digest_written_pages(written_pages: list[Page], *, max_pages: int = 20) -> str:
    """Compact title-only digest of the written pages (bounded; no full content — I1)."""
    lines: list[str] = []
    for page in written_pages[:max_pages]:
        title = (page.title or "").strip() or "(untitled)"
        ptype = (page.page_type or "").strip() or "?"
        lines.append(f"- {title} [{ptype}]")
    return "\n".join(lines) if lines else "(none)"


def _review_lang_directive(lang: str) -> str:
    """
    Return a mandatory output-language block for review prompts, or "" when no language is known.

    Mirrors the generation directive (provider/_common.py:build_generate_prompt) so review items
    (proposed_title + rationale) come out in the VAULT language instead of defaulting to English —
    the review propose/sweep prompts were never language-aware, so on an Italian vault the reviews
    came out in English (v1.5.2 fix). JSON keys stay English; only human-facing text is localised.
    """
    lang = (lang or "").strip()
    if not lang:
        return ""
    return (
        "# MANDATORY OUTPUT LANGUAGE\n"
        f"Write every proposal's `proposed_title` and `rationale` in {lang} (ISO-639-1) — the "
        f"vault's language. Do NOT translate to English unless {lang!r} is 'en'. The JSON keys "
        "themselves stay in English.\n\n"
    )


def _resolve_review_language(analysis: Analysis | None = None) -> str:
    """Resolve the review output language: analysis.language → settings.overview_language."""
    lang = (getattr(analysis, "language", "") or "").strip() if analysis is not None else ""
    return lang or (getattr(settings, "overview_language", "") or "").strip()


def _trim_source_excerpt(text: str, cap: int) -> str:
    """
    Head+tail excerpt of a raw source, bounded to ``cap`` characters (llm_wiki trimLongText
    parity). Keeps the opening (scope / intro / assumptions usually live there) AND the closing
    (out-of-scope / exclusions / next-steps sections often land at the end), with an elision
    marker in between. Returns the whole text when it already fits. ``cap<=0`` → empty (disabled).
    """
    text = (text or "").strip()
    if cap <= 0 or not text:
        return ""
    if len(text) <= cap:
        return text
    head = cap * 2 // 3
    tail = cap - head
    return f"{text[:head].rstrip()}\n\n…[source trimmed]…\n\n{text[-tail:].lstrip()}"


def _build_propose_instruction(
    *,
    analysis: Analysis,
    written_pages: list[Page],
    existing_titles: list[str],
    max_items: int,
    token_budget: int,
    source_text: str = "",
) -> str:
    """
    Build the single structured-proposal prompt (ADR-0034 §4.3 + llm_wiki
    buildReviewSuggestionPrompt parity).

    Asks for a JSON object {"proposals": [...]} of ≤ max_items items, each one of the five
    review types. The model is told to return ONLY JSON. token_budget is surfaced so the model
    keeps the output compact (the call is also wrapped in wait_for + capped on parse).

    llm_wiki parity: the RAW source text is included (bounded head+tail excerpt) alongside the
    analysis and written pages. Feeding the source content — not just the analysis — is what lets
    the model quote the document ("the doc excludes X as out-of-scope") and identify concrete
    in-scope/out-of-scope handoff gaps, yielding source-grounded suggestions with precise,
    descriptive titles rather than generic "missing from vault" slugs.
    """
    analysis_json = "{}"
    if analysis is not None:
        try:
            analysis_json = analysis.model_dump_json(indent=2)
        except Exception:  # noqa: BLE001
            analysis_json = "{}"

    pages_digest = _digest_written_pages(written_pages)
    titles_block = "\n".join(f"- {t}" for t in existing_titles[:200]) or "(none)"

    ref_max = int(getattr(settings, "review_referenced_pages_max", 8))
    query_max = int(getattr(settings, "review_search_queries_max", 3))
    source_cap = int(getattr(settings, "review_propose_source_chars", 6_000))
    source_excerpt = _trim_source_excerpt(source_text, source_cap)
    # Only emit the section (and its instruction) when we actually have source content.
    source_block = (
        f"# Source content (raw excerpt of the document just ingested)\n{source_excerpt}\n\n"
        if source_excerpt
        else ""
    )

    return (
        _review_lang_directive(_resolve_review_language(analysis))
        + "You are identifying high-value follow-up work for a self-organizing personal wiki. "
        "The wiki pages for this source have ALREADY been generated — your job is NOT to write "
        "pages, but to surface unresolved knowledge gaps a human should review or send to Deep "
        "Research.\n\n"
        "Propose ONLY genuinely useful, high-signal items. Prefer quality over quantity: a few "
        "sharp proposals beat many shallow ones, and proposing nothing is correct when the "
        "source is fully covered. Each type means:\n"
        "  - missing-page: an important entity/concept the source references but that still lacks "
        "its own page.\n"
        "  - suggestion: a research question, a source type to look for, a comparison, or an "
        "in-scope/out-of-scope handoff that would MATERIALLY improve the wiki. Ground it in the "
        "source: name the specific passage, exclusion, assumption, or boundary that motivates it "
        "(e.g. the document marks something out-of-scope but doesn't say how it connects to the "
        "in-scope work).\n"
        "  - contradiction: a conflict or tension between this source and existing pages that "
        "needs human judgment.\n"
        "  - duplicate: a page/name that likely already exists under a different name.\n"
        "  - confirm: a claim worth a human's explicit confirmation.\n\n"
        f"{source_block}"
        f"# Ingest analysis\n{analysis_json}\n\n"
        f"# Pages written this run\n{pages_digest}\n\n"
        f"# Existing vault page titles\n{titles_block}\n\n"
        'Return ONLY a JSON object with a single key "proposals" whose value is a list of at '
        f"most {max_items} objects. Each object has keys:\n"
        "  type: one of missing-page | suggestion | contradiction | duplicate | confirm\n"
        "  proposed_title: a PRECISE, DESCRIPTIVE page title in Title Case — a real title a "
        "reader would recognize (e.g. 'ELP Analysis and Downstream Workflow'), NOT a slug or a "
        "single keyword. Required for missing-page and suggestion.\n"
        "  proposed_page_type: one of entity | concept | query | synthesis | comparison "
        "(optional; NEVER 'source'). Use 'query' for a page that answers a research question or "
        "documents how workstreams/scopes connect; 'comparison' for a head-to-head.\n"
        "  rationale: 1-3 sentences that describe the gap AND why it matters. When it comes from "
        "the source, reference the specific passage/exclusion/assumption that motivates it.\n"
        "  target_page_title: string (REQUIRED for contradiction/duplicate — the existing "
        "page in conflict; otherwise omit or null)\n"
        f"  referenced_page_titles: list of up to {ref_max} EXISTING vault page titles (taken "
        "VERBATIM from the 'Existing vault page titles' list above) that this proposal is "
        "contextually about. Use ONLY titles from that list — never invent a title. Omit or [] "
        "if none apply.\n"
        f"  search_queries: list of up to {query_max} keyword-rich web-search queries (specific, "
        "suitable for a search engine — NOT titles or sentences) that would advance this item; "
        "the first seeds Deep Research. Required for suggestion and missing-page; omit or [] "
        "otherwise.\n\n"
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
        # ADR-0044 §4.1: tolerant extraction of the two new per-proposal lists (drop non-strings;
        # cap lengths). These ride the SAME single call — no extra provider round-trip.
        ref_max = int(getattr(settings, "review_referenced_pages_max", 8))
        query_max = int(getattr(settings, "review_search_queries_max", 3))
        referenced = _clean_str_list(
            entry.get("referenced_page_titles") or entry.get("referenced_pages"),
            cap=ref_max,
        )
        queries = _clean_str_list(entry.get("search_queries"), cap=query_max)
        out.append(
            ProposalDTO(
                item_type=item_type,
                proposed_title=_clean_str(entry.get("proposed_title")),
                proposed_page_type=_clean_str(proposed_type),
                rationale=_clean_str(entry.get("rationale")),
                target_page_title=_clean_str(entry.get("target_page_title")),
                referenced_page_titles=referenced,
                search_queries=queries,
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
        'Return ONLY a JSON object with a single key "resolve" whose value is the list of item '
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


def _clean_str_list(value: Any, *, cap: int) -> list[str]:
    """
    Tolerant parse of a JSON list into a bounded list of stripped non-empty strings (ADR-0044).

    Drops non-strings and empties; de-dups preserving order; truncates to *cap* (I7).
    Anything that is not a list → []. Never raises (degrade-safe for the AI seam).
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in value:
        s = _clean_str(entry)
        if s is None or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= cap:
            break
    return out


# nashsu/llm_wiki detectPageType cue vocabulary (review-create-page.ts:57-67). Substring match
# over the lowercased title+rationale — "compare" also catches compared/compares/comparison.
_COMPARISON_CUES = ("comparison", "compare", "比较")
_SYNTHESIS_CUES = ("synthesis", "综合")
_ENTITY_KEYWORD_RE = re.compile(r"\b(?:entity|entities)\b|实体", re.IGNORECASE)
_CONCEPT_KEYWORD_RE = re.compile(r"\b(?:concept|concepts)\b|概念", re.IGNORECASE)


def _resolve_create_page_type(
    proposed_title: str,
    proposed_page_type: str | None,
    rationale: str | None,
    item_type: str | None = None,
) -> PageType:
    """
    Resolve the final PageType for a Create — 1:1 with nashsu/llm_wiki ``detectPageType``
    (review-create-page.ts:57-67), so review-created pages land in the SAME folders/types.

      0. (Synapse superset, D6) Use proposed_page_type verbatim when it is a valid non-'source'
         PageType — the LLM proposal already emitted a type; llm_wiki has no such field, so this
         only refines the ambiguous cases and never contradicts the text rules below.
      1. entity keyword (entity/entities/实体) → entity
      2. concept keyword (concept/concepts/概念) → concept
      3. comparison cue (comparison/compare/比较) → comparison
      4. synthesis cue (synthesis/综合) → synthesis
      5. item_type == 'missing-page' → concept
      6. everything else (suggestion, contradiction, duplicate, unknown) → **query**  ← llm_wiki
         default. (Per owner decision 2026-07-12: query pages are graph-excluded, accepted.)
    'source' is reserved for ingested raw documents — Create NEVER produces a source page.
    """
    if proposed_page_type:
        try:
            candidate = PageType(proposed_page_type)
            if candidate != PageType.SOURCE:
                return candidate
        except (ValueError, KeyError):
            pass

    haystack = f"{proposed_title} {rationale or ''}"
    hay_lower = haystack.lower()
    if _ENTITY_KEYWORD_RE.search(haystack):
        return PageType.ENTITY
    if _CONCEPT_KEYWORD_RE.search(haystack):
        return PageType.CONCEPT
    if any(cue in hay_lower for cue in _COMPARISON_CUES):
        return PageType.COMPARISON
    if any(cue in hay_lower for cue in _SYNTHESIS_CUES):
        return PageType.SYNTHESIS
    if item_type == "missing-page":
        return PageType.CONCEPT
    return PageType.QUERY


# ── Private helpers ────────────────────────────────────────────────────────────


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
