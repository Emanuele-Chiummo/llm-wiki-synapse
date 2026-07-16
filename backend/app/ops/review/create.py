"""
F9 HITL Review Queue — single-page generation engine + Create action (BE-ARCH-2 package split).

Owns the lazy on-demand Create action (ADR-0034 §5) and its two generation routes:
  _run_generation(...)       — bounded on-demand page generation, capability-aware (I6).
                               PARALLEL to the main ingest pipeline (mirrors
                               orchestrator.run_ingest_pipeline's analyze→generate→validate→retry
                               / delegated-agent routing) — see BE-DEBT-1 step 2 (1.9.4) for the
                               planned convergence onto the main engine. Unchanged behaviour here.
  create_page_from_review(...) — stub (default, no provider) / generate (full LLM) routing
                               (WS-C, ADR-0079), including the missing-page candidate fan-out.

MONKEYPATCH-COMPAT NOTE (BE-ARCH-2): create_page_from_review's calls to ``_run_generation`` and
to the fire-and-forget ``sweep_reviews`` (propose.py) are resolved via a DEFERRED
``from app.ops.review import X`` at call time rather than a static top-of-file import, so
``patch("app.ops.review._run_generation", ...)`` / ``patch("app.ops.review.sweep_reviews", ...)``
— written against the pre-split monolithic module — keep working unchanged. See
``propose.py``'s module docstring for the full rationale; do not "simplify" these back to
top-level imports.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app import db as _db
from app.config import settings
from app.ingest.schemas import PageType
from app.models import Page, ReviewItem
from app.ops.review.suggestions import (
    _PURPOSE_SUGGESTION_TYPE,
    _SCHEMA_SUGGESTION_TYPE,
    apply_purpose_suggestion,
    apply_schema_suggestion,
)

if TYPE_CHECKING:
    from app.ingest.schemas import WikiPage

logger = logging.getLogger(__name__)

# Strong task references — a bare create_task() can be GC'd mid-run (CPython weak-ref).
_bg_tasks: set[asyncio.Task[Any]] = set()

_MISSING_PAGE_FANOUT_CAP: int = 5  # max pages from one missing-page fan-out (I7, ADR-0064)

# ── WS-C (ADR-0079): stub-create keyword tables (review-create-page.ts parity) ──────
# detectPageType keyword regex constants (EN + CJK). Order matters in _detect_page_type:
# entity > comparison > synthesis > concept > query (default).
_STUB_ENTITY_KW = re.compile(
    r"\b(entity|entities|person|people|organization|org|company|product|tool|service|"
    r"institution|brand|author|researcher|country|region|city)\b"
)
_STUB_ENTITY_CJK = re.compile(
    r"实体|人物|组织|公司|产品|工具|服务|机构|品牌|作者|研究者|国家|地区|城市"
)  # noqa: E501
_STUB_COMPARISON_KW = re.compile(
    r"\b(comparison|compare|comparing|versus|vs\.?|contrast|contrasting|diff)\b"
)
_STUB_COMPARISON_CJK = re.compile(r"比较|对比|vs|比对|对照")
_STUB_SYNTHESIS_KW = re.compile(
    r"\b(synthesis|synthesize|overview|summary|survey|landscape|digest|roundup|compilation)\b"
)
_STUB_SYNTHESIS_CJK = re.compile(r"综合|合成|概览|总结|调研|概述|汇编|综述")
_STUB_CONCEPT_KW = re.compile(
    r"\b(concept|theory|method|technique|framework|approach|algorithm|model|principle|"
    r"pattern|protocol|standard|process|mechanism|paradigm)\b"
)
_STUB_CONCEPT_CJK = re.compile(r"概念|理论|方法|技术|框架|算法|模型|原理|范式|机制|协议")


def _detect_page_type(item_type: str, title: str) -> PageType:
    """
    Port of nashsu/llm_wiki ``detectPageType`` (review-create-page.ts).

    Determines the wiki page type for a deterministic stub without an LLM call (WS-C).

    Rules (checked in order):
      1. ``missing-page`` → concept
      2. ``contradiction`` / ``suggestion`` → query
      3. Title keyword scan (EN + CJK):
           entity keywords   → entity
           comparison keywords → comparison
           synthesis keywords  → synthesis
           concept keywords    → concept
      4. Default → query
    """
    if item_type == "missing-page":
        return PageType.CONCEPT
    if item_type in ("contradiction", "suggestion"):
        return PageType.QUERY

    lower = title.lower()
    if _STUB_ENTITY_KW.search(lower) or _STUB_ENTITY_CJK.search(title):
        return PageType.ENTITY
    if _STUB_COMPARISON_KW.search(lower) or _STUB_COMPARISON_CJK.search(title):
        return PageType.COMPARISON
    if _STUB_SYNTHESIS_KW.search(lower) or _STUB_SYNTHESIS_CJK.search(title):
        return PageType.SYNTHESIS
    if _STUB_CONCEPT_KW.search(lower) or _STUB_CONCEPT_CJK.search(title):
        return PageType.CONCEPT
    return PageType.QUERY


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
    async with _db.get_session() as session:
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
    generation_key: str | None = None,
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
    from app.ingest.schemas import WikiFrontmatter, WikiPage

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
    if generation_key is not None:
        source_text += (
            "\nThis is an accepted corpus proposal. The page type must match the reserved "
            f"identity and frontmatter synapse_generation_key MUST equal {generation_key!r}."
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
                    generation_key=generation_key,
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
                page_type_counts=(
                    {page_type.value: int(page_type is resolved_type) for page_type in PageType}
                    if created_page_id is not None
                    else None
                ),
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
        if wiki_page is not None and generation_key is not None:
            # Rebuild through Pydantic so the reserved key/type invariant is validated before
            # the single writer sees the page. Provider-authored type drift cannot misfile it.
            frontmatter_data = wiki_page.frontmatter.model_dump()
            frontmatter_data["type"] = resolved_type
            frontmatter_data["synapse_generation_key"] = generation_key
            wiki_page = WikiPage(
                title=wiki_page.title,
                type=resolved_type,
                content=wiki_page.content,
                frontmatter=WikiFrontmatter(**frontmatter_data),
            )
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
            page_type_counts=(
                {page_type.value: int(page_type is resolved_type) for page_type in PageType}
                if error is None and wiki_page is not None
                else None
            ),
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


async def _create_stub_from_review(
    item: ReviewItem,
    origin_source: str,
    candidates: list[str],
) -> ReviewItem:
    """
    Deterministic stub-create path for ``create_page_from_review(mode='stub')`` (WS-C, ADR-0079).

    Ports nashsu/llm_wiki ``review-create-page.ts`` (Create Page button): writes a
    ``# <title>\\n\\n<description>`` draft WITHOUT calling an LLM provider (I6-neutral).

    One write per candidate (same fan-out logic as the generate path). The primary candidate
    failure raises UpstreamError (→ HTTP 502). Secondary candidate failures are logged and
    skipped.
    All writes go through the normal write_wiki_page seam (I1 — one data_version bump per page,
    K3/K4 index+log maintenance).

    Returns the updated ReviewItem (status=created).
    """
    from app.errors import NotFoundError, UpstreamError  # noqa: PLC0415
    from app.ingest.schemas import WikiFrontmatter, WikiPage  # noqa: PLC0415
    from app.ingest.writer import write_wiki_page  # noqa: PLC0415

    item_id_str = str(item.id)
    vault_id = item.vault_id
    proposed_title = item.proposed_title or f"Review: {item.id}"
    created_page_ids: list[str] = []

    for _idx, candidate_title in enumerate(candidates):
        _is_primary = _idx == 0

        # Detect page type via keyword rules (llm_wiki detectPageType parity, ADR-0079 §2).
        page_type = _detect_page_type(item.item_type, candidate_title)

        # Build stub content: `# <title>\n\n<description>` (only if description non-empty).
        _desc = (item.rationale or "").strip()
        body_parts: list[str] = [f"# {candidate_title}"]
        if _desc:
            body_parts += ["", _desc]
        body = "\n".join(body_parts)

        fm = WikiFrontmatter(
            type=page_type,
            title=candidate_title,
            sources=[origin_source] if origin_source else [],
            tags=["stub"],
        )
        wiki_page = WikiPage(
            title=candidate_title,
            type=page_type,
            content=body,
            frontmatter=fm,
        )

        try:
            created_page = await write_wiki_page(None, wiki_page, origin_source)
        except Exception as exc:  # noqa: BLE001
            if _is_primary:
                logger.error(
                    "create_page_from_review: stub write failed for item=%s: %s — item pending",
                    item_id_str,
                    exc,
                )
                raise UpstreamError(
                    f"Failed to write stub page: {exc}. Item left pending — retry or skip.",
                ) from exc
            logger.warning(
                "create_page_from_review: stub secondary candidate %r write failed — skipping: %s",
                candidate_title,
                exc,
            )
            continue

        created_page_ids.append(str(created_page.id))
        logger.debug(
            "create_page_from_review: stub item=%s candidate[%d]=%r → page=%s",
            item_id_str,
            _idx,
            candidate_title,
            str(created_page.id),
        )

    if not created_page_ids:
        raise UpstreamError(
            "No stub pages were created. Item left pending — retry or skip.",
        )

    created_page_id_str = created_page_ids[0]

    # Mark the item as created.
    async with _db.get_session() as session:
        row2 = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item2 = row2.scalar_one_or_none()
        if item2 is None:
            raise NotFoundError(f"Review item {item.id} not found")
        item2.status = "created"
        item2.resolution = "created"
        item2.created_page_id = created_page_id_str  # type: ignore[assignment]  # noqa: PGH003
        item2.reviewed_at = datetime.now(UTC)
        item2.reviewed_by = "web-ui"
        await session.flush()
        await session.refresh(item2)
        session.expunge(item2)

    logger.info(
        "create_page_from_review: stub item=%s → page=%s title=%r vault=%s",
        item_id_str,
        created_page_id_str,
        proposed_title,
        vault_id,
    )

    # Fire-and-forget sweep so sibling proposals this page satisfies are resolved.
    async def _do_stub_sweep() -> None:
        try:
            # Deferred (package-level) import — keeps `patch("app.ops.review.sweep_reviews")`
            # effective post-split (see module docstring).
            from app.ops.review import sweep_reviews  # noqa: PLC0415

            await sweep_reviews(vault_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("create_page_from_review: post-stub sweep failed (non-fatal): %s", exc)

    _t = asyncio.create_task(_do_stub_sweep())
    _bg_tasks.add(_t)
    _t.add_done_callback(_bg_tasks.discard)
    return item2


async def create_page_from_review(item_id: uuid.UUID, *, mode: str = "stub") -> ReviewItem:
    """
    Lazy on-demand Create action (ADR-0034 §5), with stub/generate routing (WS-C, ADR-0079).

    ``mode="stub"`` (DEFAULT): writes a deterministic ``# <title>\\n\\n<description>`` page
    without any provider call. ``mode="generate"``: the existing full-LLM generation path
    (unchanged). The mode can be chosen per-request via the optional JSON body on the
    ``POST /review/queue/{id}/create`` endpoint.

    Flow (mode="stub"):
      1. Load the review item (404 if absent; 409 if status != 'pending').
      2. Route purpose/schema-suggestion items (no page write; unchanged).
      3. Build candidate list + call _create_stub_from_review (no provider, I6-neutral).
      4. Fire-and-forget sweep.

    Flow (mode="generate"):
      1. Load the review item (404 if absent; 409 if status != 'pending').
      2. Resolve the ingest provider (409 if none configured — I6).
      3. Call _run_generation — capability-aware (I6). On any failure → 502, item pending.
      4. Resolve the created page id (I1 — exactly one write).
      5. Set status=created, resolution=created, created_page_id, reviewed_at, reviewed_by.
      6. Fire-and-forget sweep.

    Returns the updated ReviewItem.

    Raises:
      NotFoundError (→ HTTP 404) — item not found.
      ConflictError (→ HTTP 409) — item not pending, or (mode=generate) no ingest
        provider configured.
      UpstreamError (→ HTTP 502) — page write / generation failed; item left pending.
    """
    from app.errors import ConflictError, NotFoundError, UpstreamError
    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    item_id_str = str(item_id)

    # ── 1. Load item ─────────────────────────────────────────────────────────
    async with _db.get_session() as session:
        row = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item = row.scalar_one_or_none()
        if item is None:
            raise NotFoundError(f"Review item {item_id} not found")
        if item.status != "pending":
            raise ConflictError(
                f"Review item {item_id} has status={item.status!r}; "
                "only pending items can be Created."
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
            raise UpstreamError(
                f"Failed to apply purpose.md suggestion: {exc}. "
                "Item left pending — retry or dismiss."
            ) from exc

        async with _db.get_session() as session:
            row_ps = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
            item_ps = row_ps.scalar_one_or_none()
            if item_ps is None:
                raise NotFoundError(f"Review item {item_id} not found")
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
            raise UpstreamError(
                f"Failed to apply schema.md suggestion: {exc}. "
                "Item left pending — retry or dismiss."
            ) from exc

        async with _db.get_session() as session:
            row_ss = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
            item_ss = row_ss.scalar_one_or_none()
            if item_ss is None:
                raise NotFoundError(f"Review item {item_id} not found")
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

    # ── 2. Derive title / origin_source / candidates (shared by stub + generate) ──
    proposed_title = item.proposed_title or f"Review: {item_id}"
    proposed_page_type = item.proposed_page_type  # may be None → heuristic in _run_generation
    corpus_generation_key = (
        item.content_key
        if item.proposal_origin == "corpus"
        and isinstance(item.content_key, str)
        and item.content_key.startswith("corpus:")
        else None
    )

    # origin_source: provenance from source_page_id, else synthetic marker (§5.1)
    if item.source_page_id:
        try:
            async with _db.get_session() as session:
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

    # ── Build candidate list for missing-page fan-out (R1 parity, ADR-0064) ────
    # For missing-page items the proposed_title may encode a comma/、/"and"/"e" separated
    # list of pages to create (one per candidate, each exactly one data_version bump — I1,
    # bounded by _MISSING_PAGE_FANOUT_CAP — I7). All other item types keep the existing
    # single-page path completely unchanged.
    if item.item_type == "missing-page":
        candidates = _extract_missing_page_candidates(proposed_title)
    else:
        # D7 parity: clean the title (strip "Create:"/quote noise) for the generated page too.
        candidates = [_clean_candidate_title(proposed_title) or proposed_title]

    # ── WS-C mode routing (ADR-0079): stub vs generate ───────────────────────
    # "stub" (default): deterministic draft, no provider call (I6-neutral).
    # "generate": existing full-LLM path (unchanged — explicit secondary action).
    if mode == "stub":
        return await _create_stub_from_review(item, origin_source, candidates)

    # ── Generate path: resolve provider (I6 — 409 if none configured) ────────
    try:
        provider_config_row = await resolve_provider_config("ingest", vault_id)
    except ConfigNotFoundError as cnfe:
        raise ConflictError(
            "No ingest provider configured for this vault. "
            "Configure a provider before using the Create action (I6)."
        ) from cnfe
    except Exception as exc:  # noqa: BLE001
        raise ConflictError(f"Provider resolution failed: {exc}") from exc

    # ── Generate each candidate page (I1 — one write per page; bounded fan-out) ──
    # Primary (first) candidate failure → 502, item left pending (identical to pre-fan-out
    # single-page behavior). Secondary candidate failures are logged and skipped; the primary
    # is already committed at that point.
    from app.ingest.orchestrator import write_wiki_page  # lazy; avoids circular at module level

    created_page_ids: list[str] = []

    # Deferred (package-level) import — keeps `patch("app.ops.review._run_generation")` effective
    # post-split (see module docstring).
    from app.ops.review import _run_generation  # noqa: PLC0415

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
                generation_key=corpus_generation_key,
            )
        except NotImplementedError as nie:
            if _is_primary:
                logger.warning(
                    "create_page_from_review: _run_generation raised NotImplementedError"
                    " (ADR-0034 §5): %s",
                    nie,
                )
                raise UpstreamError(
                    "Page generation raised NotImplementedError (ADR-0034 §5). "
                    "Item left pending — retry or skip."
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
                raise UpstreamError(
                    f"Page generation failed: {exc}. " "Item left pending — retry or skip."
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
                    raise UpstreamError(
                        f"Failed to write page to wiki: {exc}. "
                        "Item left pending — retry or skip."
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
                raise UpstreamError(
                    "Page generation produced no page. Item left pending — retry or skip."
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
        raise UpstreamError("No pages were created. Item left pending — retry or skip.")

    # The primary (first) created page is recorded on the review item for API compatibility
    # (existing callers expect a single created_page_id — ADR-0064 §4 / I8).
    created_page_id_str = created_page_ids[0]

    # ── 6. Set item to created ─────────────────────────────────────────────────
    async with _db.get_session() as session:
        row2 = await session.execute(select(ReviewItem).where(ReviewItem.id == item_id_str))
        item2 = row2.scalar_one_or_none()
        if item2 is None:
            # Theoretically impossible at this point, but handle gracefully
            raise NotFoundError(f"Review item {item_id} not found")
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
    async def _do_sweep() -> None:
        try:
            # Deferred (package-level) import — keeps `patch("app.ops.review.sweep_reviews")`
            # effective post-split (see module docstring).
            from app.ops.review import sweep_reviews  # noqa: PLC0415

            await sweep_reviews(vault_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("create_page_from_review: post-create sweep failed (non-fatal): %s", exc)

    _t = asyncio.create_task(_do_sweep())
    _bg_tasks.add(_t)
    _t.add_done_callback(_bg_tasks.discard)

    return item2


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
    Resolve the final PageType for a Create from llm_wiki's ``detectPageType`` cue vocabulary,
    with structural-page cues deliberately taking precedence over generic entity/concept nouns.

      1. comparison cue (comparison/compare/比较) → comparison
      2. synthesis cue (synthesis/综合) → synthesis
      3. entity keyword (entity/entities/实体) → entity
      4. concept keyword (concept/concepts/概念) → concept
      5. use proposed_page_type only as a valid non-source hint when no text cue exists
      6. item_type == 'missing-page' → concept
      7. everything else (suggestion, contradiction, duplicate, unknown) → **query**  ← llm_wiki
         default. (Per owner decision 2026-07-12: query pages are graph-excluded, accepted.)
    'source' is reserved for ingested raw documents — Create NEVER produces a source page.
    """
    haystack = f"{proposed_title} {rationale or ''}"
    hay_lower = haystack.lower()
    # Structural derived-page cues outrank generic nouns in the rationale. Otherwise realistic
    # phrases such as "compare these entities" and "synthesis of concepts" are misfiled.
    if any(cue in hay_lower for cue in _COMPARISON_CUES):
        return PageType.COMPARISON
    if any(cue in hay_lower for cue in _SYNTHESIS_CUES):
        return PageType.SYNTHESIS
    if _ENTITY_KEYWORD_RE.search(haystack):
        return PageType.ENTITY
    if _CONCEPT_KEYWORD_RE.search(haystack):
        return PageType.CONCEPT
    if proposed_page_type:
        try:
            candidate = PageType(proposed_page_type)
            if candidate != PageType.SOURCE:
                return candidate
        except (ValueError, KeyError):
            pass
    if item_type == "missing-page":
        return PageType.CONCEPT
    return PageType.QUERY
