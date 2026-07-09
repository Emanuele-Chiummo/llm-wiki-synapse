"""
K2 Lint-fix loop — the third Karpathy core operation (Ingest · Query · **Lint**), ADR-0037.

ARCHITECTURE OVERVIEW (ADR-0037 §2):
  A periodic, BOUNDED, HUMAN-GATED health check of the wiki. ``run_lint_scan`` produces LINT
  FINDINGS (proposals); it NEVER auto-applies fixes. The human gate is ``apply_lint_fix`` —
  only safe/bounded fixes are ever applied, and at most one ``data_version`` bump per fix (I1).

FINDING CATEGORIES (ADR-0037 §3.1):
  orphan-page     — deterministic: a live wiki page with graph in-degree 0 (no resolved
                    incoming wikilink). Found from the links table — NO provider call.
  broken-wikilink — deterministic: a dangling [[link]] in the links table (dangling=True).
                    L1 / ADR-0037 B1.  NO provider call.  suggested_target / suggested_page_id
                    populated via the tolerant resolver (L2).
  missing-xref    — LLM: a page that mentions an existing page but does not link it.
  contradiction   — LLM: conflicting claims across pages.
  stale-claim     — LLM: superseded information.
  missing-page    — LLM: a concept mentioned but with no page.

THE I7 CONTRACT (any violation is a P0 rejection):
  1. The semantic loop is ``for n in range(1, max_iter + 1)`` — structurally capped. NOT a
     while-True. Bounds are FROZEN on the lint_runs row at INSERT and read once into locals.
  2. token_budget checked at the TOP of each round before spending (under-spend, never over).
  3. Findings are capped at LINT_MAX_FINDINGS (truncate; never an unbounded enqueue).
  4. Each semantic provider call is wrapped in ``asyncio.wait_for(LINT_TIMEOUT_SECONDS)``.
  5. status defaults pessimistically; the terminal write is in a ``finally`` block — never
     leaves status 'running'. total_cost_usd accumulated + logged + $1 anomaly WARNING.

I6 CONTRACT (all LLM calls route through resolve_provider_config — no hardcoded backend):
  No isinstance / provider_type / class-name branching anywhere in this module. The semantic
  pass rides ``InferenceProvider.chat()`` (the same surface review/deep-research/enrich use).

I1 CONTRACT:
  The scan reads only the pages + links tables (bounded indexed reads) — NEVER a full vault
  walk / re-scan. Apply edits touch ONLY the referencing page(s).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import func, select, update

from app.config import settings
from app.db import get_session
from app.models import LintFinding, LintRun, Page

logger = logging.getLogger(__name__)

# $1 cost-anomaly threshold — same as the ingest path (ADR-0009 §3 / ADR-0037 §4).
_COST_ANOMALY_THRESHOLD_USD: float = 1.00

# Accepted value sets (app-side enum-by-convention, no DB CHECK — ADR-0037 §3.1).
_VALID_CATEGORIES = frozenset(
    {
        "orphan-page",
        "broken-wikilink",
        "missing-xref",
        "contradiction",
        "stale-claim",
        "missing-page",
        # L1 — no-outlinks: a page with zero outgoing wikilinks (ADR-0058 §L1).
        "no-outlinks",
        # L2 — suggestion: a question or source worth adding to the wiki (ADR-0058 §L2).
        "suggestion",
    }
)
_VALID_SEVERITIES = frozenset({"info", "warning", "error"})
_VALID_STATUSES = frozenset({"open", "applied", "dismissed"})

# Categories whose apply step is FLAG-ONLY (no deterministic safe fix — ADR-0037 §5).
# contradiction / stale-claim → apply is a no-op status change to 'applied'
# with a resolution_note (the human still has to fix them by editing the wiki).
# broken-wikilink WITHOUT a suggestion is also flag-only (no safe fix when target unknown).
# suggestion — always flag-only (semantic category; no deterministic fix).
# no-outlinks and orphan-page are handled specially in apply_lint_fix:
#   - if suggested_target/suggested_page_id present → apply a real fix
#   - otherwise → fall back to flag-only
_FLAG_ONLY_CATEGORIES = frozenset({"contradiction", "stale-claim", "suggestion"})

# Bounded reads (I7 — never an unbounded scan).
_ORPHAN_SCAN_MAX_PAGES: int = 1_000
_BROKEN_SCAN_MAX_LINKS: int = 1_000  # L1 / I7 — cap for broken-wikilink scan
_NO_OUTLINKS_SCAN_MAX_PAGES: int = 1_000  # L1 / I7 — cap for no-outlinks scan
_CANDIDATE_TITLES_MAX: int = 500

# ── Fuzzy-suggestion constants (L3 — port of lint.ts suggestRelatedPage) ──────────
_RELATED_PAGE_SUGGESTION_MIN_SCORE: float = 0.08
_SAME_FOLDER_SCORE_BONUS: float = 0.08
_SINGLE_CJK_TOKEN_WEIGHT: float = 0.35
# Compiled pattern for tokenization: matches Unicode letters/digits, not underscore.
_WORD_RE: re.Pattern[str] = re.compile(r"[^\W_]+", re.UNICODE)
# CJK unified ideographs range (used in single-char expansion for CJK tokens).
_CJK_RE: re.Pattern[str] = re.compile(r"[㐀-鿿]")


# ── Public result types ────────────────────────────────────────────────────────


@dataclass
class LintScanResult:
    """Return value of run_lint_scan (ADR-0037 §3.3)."""

    run_id: uuid.UUID
    status: Literal["completed", "error"]
    iterations_used: int
    findings_count: int
    total_cost_usd: float
    error_message: str | None


@dataclass
class LintFindingsPage:
    """Paginated result for GET /lint/findings."""

    items: list[LintFinding]
    total: int
    limit: int
    offset: int
    severity_totals: dict[str, int]


@dataclass
class LintRunsPage:
    """Paginated result for GET /lint/runs."""

    items: list[LintRun]
    total: int
    limit: int
    offset: int


# ── Finding DTO (semantic provider call contract — ADR-0037 §4.3) ──────────────


@dataclass
class FindingDTO:
    """
    One structured finding emitted by the deterministic checks or the semantic provider call.

    target_title resolves to target_page_id at persist time (for missing-xref / stale-claim).
    suggested_target / suggested_page_id: L2 — the best tolerant-resolver match for
    broken-wikilink findings (NULL for all other categories).
    """

    category: Literal[
        "orphan-page",
        "broken-wikilink",
        "missing-xref",
        "contradiction",
        "stale-claim",
        "missing-page",
        "no-outlinks",
        "suggestion",
    ]
    severity: str
    description: str
    target_title: str | None = None
    target_page_id: uuid.UUID | None = None
    proposed_action: str | None = None
    suggested_target: str | None = None  # L2
    suggested_page_id: uuid.UUID | None = None  # L2


# ── Public entry point ─────────────────────────────────────────────────────────


async def run_lint_scan(
    vault_id: str,
    *,
    max_iter: int | None = None,
    token_budget: int | None = None,
    run_id: uuid.UUID | None = None,
    semantic: bool = True,
) -> LintScanResult:
    """
    Run ONE bounded lint scan end-to-end (ADR-0037 §4).

    Pipeline:
      1. Deterministic structural checks (orphan-page, broken-wikilink) — graph/links read,
         NO provider call. broken-wikilink = links.dangling=True (L1, zero LLM cost, I7).
      2. Bounded semantic loop (missing-xref / contradiction / stale-claim / missing-page):
         ``for n in range(1, max_iter + 1)`` with a token_budget gate at the top of each round.
         L8: when semantic=False, this phase is skipped entirely (free scan).
      3. Merge + cap findings at LINT_MAX_FINDINGS; persist lint_findings rows.
      4. Finalize the lint_runs row (always — terminal write in finally).

    Bounds (I7) are FROZEN on the lint_runs row at INSERT and never re-read mid-loop.
    Produces FINDINGS only — NEVER applies a fix (the human gate is apply_lint_fix, §5).

    Args:
        semantic: When False, skip the provider pass entirely (deterministic findings only;
                  run row records iterations_used=0, cost=0). L8 / ADR-0037 B1.
    """
    # ── Resolve and freeze bounds (I7) ───────────────────────────────────────────
    from app.config_overrides import effective_int

    frozen_max_iter: int = (
        max_iter
        if max_iter is not None
        else effective_int("lint_max_iter", int(settings.lint_max_iter))
    )
    frozen_token_budget: int = (
        token_budget
        if token_budget is not None
        else effective_int("lint_token_budget", int(settings.lint_token_budget))
    )
    max_findings: int = int(settings.lint_max_findings)

    # ── Run row (caller may pre-INSERT; reuse its id — same pattern as deep_research) ──
    if run_id is None:
        run_id = uuid.uuid4()
        await _create_run_row(
            run_id=run_id,
            vault_id=vault_id,
            max_iter=frozen_max_iter,
            token_budget=frozen_token_budget,
        )

    from app.ingest.provider.base import UsageAccumulator

    accumulator = UsageAccumulator()

    status: Literal["completed", "error"] = "completed"
    error_message: str | None = None
    iterations_used: int = 0
    findings: list[FindingDTO] = []

    try:
        # ── 1. Deterministic structural checks (no provider call, I1) ────────────
        # These are FREE (pure Postgres reads) and already bounded by their own scan
        # caps (_ORPHAN_SCAN_MAX_PAGES / _BROKEN_SCAN_MAX_LINKS). They are therefore
        # NOT subject to LINT_MAX_FINDINGS, which bounds the PAID semantic pass only
        # (I7 = cost control). `det_baseline` marks how many free findings precede the
        # semantic tail so the cap counts semantic additions only — otherwise orphans
        # alone fill the 50 slots and broken-wikilink (the dominant real-vault category,
        # llm_wiki's "Broken Link" warnings) is truncated to zero. (ADR-0058 §2.1a.)
        # broken-wikilink FIRST: they are the more actionable category (each carries an
        # Open→referencing-page and, when resolvable, a one-click Fix) and are the visually
        # dominant category in llm_wiki's lint page ("Broken Link" warnings). Orphans follow.
        findings.extend(await _detect_broken_wikilinks(vault_id))
        findings.extend(await _detect_orphans(vault_id))
        findings.extend(await _detect_no_outlinks(vault_id))  # L1
        det_baseline: int = len(findings)

        # ── 2. Bounded semantic loop (I6/I7) — skipped when semantic=False (L8) ──
        # BOUNDS are LOCAL CONSTANTS for this run — the loop NEVER re-reads settings/DB.
        max_iter_local: int = frozen_max_iter
        token_budget_local: int = frozen_token_budget

        if not semantic:
            # L8: deterministic-only scan (free); semantic phase entirely skipped.
            logger.debug(
                "run_lint_scan: semantic=False → deterministic-only scan (vault=%s, L8)",
                vault_id,
            )
        elif (resolved := await _resolve_lint_provider(vault_id)) is not None:
            provider, config_row = resolved
            provider.bind_accumulator(accumulator)
            token_budget_local = _coerce_int(
                getattr(config_row, "token_budget", None), token_budget_local
            )
            timeout_s = float(getattr(settings, "lint_timeout_seconds", 30.0))

            candidate_titles = await _load_candidate_titles(vault_id)
            page_digest = await _load_page_digest(vault_id)

            seen_descriptions: set[str] = {f.description.strip().lower() for f in findings}

            # ── THE BOUNDED LOOP (I7 — structural cap) ───────────────────────────
            for iteration in range(1, max_iter_local + 1):  # ← HARD CAP (ADR-0037 §4)
                iterations_used = iteration

                # budget gate BEFORE spending the round (I7 — under-spend, never over)
                if accumulator.total_tokens >= token_budget_local:
                    logger.info(
                        "run_lint_scan: token_budget reached at round %d (vault=%s) — stop",
                        iteration,
                        vault_id,
                    )
                    break

                # Stop early once the SEMANTIC pass has produced enough (paid) findings
                # (deterministic findings excluded — they are free; ADR-0058 §2.1a).
                if (len(findings) - det_baseline) >= max_findings:
                    break

                already = sorted(seen_descriptions)
                raw = await _semantic_pass(
                    provider=provider,
                    vault_id=vault_id,
                    page_digest=page_digest,
                    candidate_titles=candidate_titles,
                    already_found=already,
                    token_budget=token_budget_local,
                    timeout_s=timeout_s,
                )
                round_findings = _parse_findings(raw)

                # De-dup against everything seen so far; stop when a round adds nothing new.
                new_this_round = 0
                for f in round_findings:
                    key = f.description.strip().lower()
                    if not key or key in seen_descriptions:
                        continue
                    seen_descriptions.add(key)
                    findings.append(f)
                    new_this_round += 1
                    if (len(findings) - det_baseline) >= max_findings:
                        break

                if new_this_round == 0:
                    # The model has nothing new to add — converged; stop early (bounded anyway).
                    break
        else:
            # semantic=True but no provider resolved → deterministic-only (I6: no silent default).
            logger.debug(
                "run_lint_scan: no ingest provider resolved (vault=%s) — "
                "deterministic findings only (I6: no silent default)",
                vault_id,
            )

        # ── 3. Merge + cap + persist ─────────────────────────────────────────────
        # Cap the SEMANTIC tail only; deterministic findings persist in full (they are
        # free and already bounded by their per-scan caps — ADR-0058 §2.1a). Total
        # persisted ≤ det_baseline (≤ _ORPHAN_SCAN_MAX_PAGES + _BROKEN_SCAN_MAX_LINKS)
        # + max_findings — still a hard ceiling (I7), just not one that hides free findings.
        findings = findings[: det_baseline + max_findings]
        await _persist_findings(run_id=run_id, vault_id=vault_id, findings=findings)

    except Exception as exc:  # noqa: BLE001
        status = "error"
        error_message = str(exc)
        logger.exception("run_lint_scan: unhandled error for run_id=%s", run_id)

    finally:
        # ── Finalize the run row (ALWAYS — never leave 'running') ─────────────────
        total_cost_usd = round(accumulator.total_cost_usd, 4)
        await _finalize_run_row(
            run_id=run_id,
            status=status,
            iterations_used=iterations_used,
            findings_count=len(findings),
            total_cost_usd=total_cost_usd,
            error_message=error_message,
        )

        logger.info(
            "lint_scan run_id=%s status=%s iterations=%d findings=%d cost_usd=%.4f vault=%r",
            run_id,
            status,
            iterations_used,
            len(findings),
            total_cost_usd,
            vault_id,
        )
        if total_cost_usd > _COST_ANOMALY_THRESHOLD_USD:
            logger.warning(
                "COST ANOMALY: lint_scan run_id=%s total_cost_usd=%.4f exceeds $%.2f "
                "(vault=%r) — investigate runaway/misconfiguration",
                run_id,
                total_cost_usd,
                _COST_ANOMALY_THRESHOLD_USD,
                vault_id,
            )

    return LintScanResult(
        run_id=run_id,
        status=status,
        iterations_used=iterations_used,
        findings_count=len(findings),
        total_cost_usd=round(accumulator.total_cost_usd, 4),
        error_message=error_message,
    )


# ── Human-gated apply + dismiss (ADR-0037 §5) ──────────────────────────────────


async def apply_lint_fix(finding_id: uuid.UUID) -> LintFinding:
    """
    Human-gated apply step (ADR-0037 §5). Applies ONLY safe/bounded fixes; bumps data_version
    at most ONCE per applied fix (I1). NEVER full-rescans (I1).

    Apply behaviour by category:
      missing-xref  — reuse the wikilink-enrichment seam (ops/enrich_wikilinks.py) to add the
                      [[target]] link into the referencing page's BODY (I5 atomic, K7-valid).
      missing-page  — delegate to the lazy-generation seam used by review.create_page_from_review.
      orphan-page / contradiction / stale-claim — FLAG-ONLY: status→applied + resolution_note
                      (no deterministic safe fix; the human edits the wiki — ADR-0037 §5).

    Raises:
      HTTPException(404) — finding not found.
      HTTPException(409) — finding not 'open', or (for fixable categories) no ingest provider.
      HTTPException(502) — the bounded fix failed; finding left 'open' (retry or dismiss).
    """
    from fastapi import HTTPException

    finding_id_str = str(finding_id)

    # ── Load finding ─────────────────────────────────────────────────────────────
    async with get_session() as session:
        row = await session.execute(select(LintFinding).where(LintFinding.id == finding_id_str))
        finding = row.scalar_one_or_none()
        if finding is None:
            raise HTTPException(status_code=404, detail=f"Lint finding {finding_id} not found")
        if finding.status != "open":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Lint finding {finding_id} has status={finding.status!r}; "
                    "only open findings can be applied."
                ),
            )
        session.expunge(finding)

    category = finding.category

    # ── FLAG-ONLY categories — status change only, no fix, no bump (ADR-0037 §5) ──
    if category in _FLAG_ONLY_CATEGORIES:
        note = (
            f"{category}: flag-only — no automatic fix is safe; resolved by acknowledgement. "
            "Edit the affected wiki page(s) to address the finding."
        )
        return await _set_finding_status(finding_id, "applied", resolution_note=note)

    # ── broken-wikilink — rewrite [[old]] → [[Suggested]] or create stub (L3/L4/I1/I5) ──
    if category == "broken-wikilink":
        note = await _apply_broken_wikilink(finding)
        return await _set_finding_status(finding_id, "applied", resolution_note=note)

    # ── missing-xref — reuse the wikilink-enrichment seam (I1/I5) ─────────────────
    if category == "missing-xref":
        note = await _apply_missing_xref(finding)
        return await _set_finding_status(finding_id, "applied", resolution_note=note)

    # ── missing-page — delegate to the lazy-generation seam (ADR-0034 §5) ─────────
    if category == "missing-page":
        created_note = await _apply_missing_page(finding)
        return await _set_finding_status(finding_id, "applied", resolution_note=created_note)

    # ── no-outlinks — append [[suggested_target]] under ## Related (L4/I1/I5) ──────
    if category == "no-outlinks":
        note = await _apply_no_outlinks(finding)
        return await _set_finding_status(finding_id, "applied", resolution_note=note)

    # ── orphan-page — append [[orphan title]] to suggested source page (L4/I1/I5) ──
    if category == "orphan-page":
        note = await _apply_orphan_page(finding)
        return await _set_finding_status(finding_id, "applied", resolution_note=note)

    # Unknown category (defensive — should never happen given the persist-time validation).
    raise HTTPException(
        status_code=409,
        detail=f"Lint finding {finding_id} has unsupported category={category!r}.",
    )


async def dismiss_lint_finding(finding_id: uuid.UUID) -> LintFinding:
    """Set status=dismissed, reviewed_at=now() (ADR-0037 §5). 404 if not found."""
    return await _set_finding_status(finding_id, "dismissed", resolution_note="dismissed by human")


# ── L6 — lint → review bridge ───────────────────────────────────────────────────

# Mapping: lint category → review item_type (L6 / ADR-0037 B1).
# broken-wikilink → missing-page (the dangling target may not exist; review queue surfaces it).
_CATEGORY_TO_ITEM_TYPE: dict[str, str] = {
    "broken-wikilink": "missing-page",
    "missing-page": "missing-page",
    "contradiction": "contradiction",
    "stale-claim": "suggestion",
    "orphan-page": "suggestion",
    "missing-xref": "suggestion",
    "no-outlinks": "suggestion",  # L1 / ADR-0058 §L1
    "suggestion": "suggestion",  # L2 / ADR-0058 §L2
}


async def send_finding_to_review(finding_id: uuid.UUID) -> LintFinding:
    """
    Bridge a lint finding into the F9 HITL review queue (L6 / ADR-0037 B1).

    Maps category → item_type (see _CATEGORY_TO_ITEM_TYPE), enqueues the review item,
    then sets finding status → 'applied' with resolution_note = "sent to review (<id>)".

    DEDUP CONTRACT (ADR-0044 / ADR-0037 B1 note from ADR review):
      The content_key includes the category so broken-wikilink findings can never collide
      with genuine missing-page review items even when they share the same proposed title.
      Specifically: content_key = enqueue_review(..., proposed_title=...) anchored on the
      finding's category-prefixed rationale. This is implemented by including the category
      in the rationale text (which feeds the FNV-1a key inside enqueue_review).

    Raises:
      HTTPException(404) — finding not found.
      HTTPException(409) — finding not open.
    """
    from fastapi import HTTPException

    from app.ops.review import enqueue_review

    finding_id_str = str(finding_id)

    # ── Load the finding ─────────────────────────────────────────────────────────
    async with get_session() as session:
        row = await session.execute(select(LintFinding).where(LintFinding.id == finding_id_str))
        finding = row.scalar_one_or_none()
        if finding is None:
            raise HTTPException(status_code=404, detail=f"Lint finding {finding_id} not found")
        if finding.status != "open":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Lint finding {finding_id} has status={finding.status!r}; "
                    "only open findings can be sent to review."
                ),
            )
        session.expunge(finding)

    category = finding.category
    item_type = _CATEGORY_TO_ITEM_TYPE.get(category, "suggestion")

    # proposed_title: for broken-wikilink use suggested_target if present (L6).
    proposed_title: str | None
    if category == "broken-wikilink" and finding.suggested_target:
        proposed_title = finding.suggested_target
    else:
        proposed_title = finding.target_title

    # Rationale includes the category so the FNV-1a content_key inside enqueue_review is
    # category-scoped — prevents collision between broken-wikilink and genuine missing-page
    # items (ADR review note, Do-NOT #17/#18).
    rationale = (
        f"[lint:{category}] {finding.description}"
        if finding.description
        else f"[lint:{category}] finding_id={finding_id}"
    )

    review_item = await enqueue_review(
        vault_id=finding.vault_id,
        item_type=item_type,
        proposed_title=proposed_title,
        rationale=rationale,
        source_page_id=(uuid.UUID(str(finding.target_page_id)) if finding.target_page_id else None),
    )

    note = f"sent to review ({review_item.id})"
    return await _set_finding_status(finding_id, "applied", resolution_note=note)


# ── L5 — batch result types ──────────────────────────────────────────────────────


@dataclass
class BatchFindingResult:
    """Per-item result within a batch operation response (L5)."""

    id: str
    status: str  # "ok" | "error"
    detail: str | None


@dataclass
class BatchFindingsResponse:
    """Response for POST /lint/findings/batch (L5)."""

    results: list[BatchFindingResult]
    ok_count: int
    error_count: int


# Maximum ids per batch (I7 — bounded operation).
_BATCH_MAX_IDS: int = 200


async def apply_batch(
    finding_ids: list[uuid.UUID],
    action: str,
) -> BatchFindingsResponse:
    """
    Apply *action* to each finding in *finding_ids* sequentially (L5 / ADR-0037 B1).

    Actions: "apply" | "dismiss" | "send-to-review"
    Cap: len(finding_ids) ≤ _BATCH_MAX_IDS (I7 — bounded; caller validates before calling).
    Per-id failure does NOT abort the batch — all ids are attempted; results accumulated.

    Returns BatchFindingsResponse with per-id status + aggregate ok/error counts.
    """
    from fastapi import HTTPException

    results: list[BatchFindingResult] = []
    ok_count = 0
    error_count = 0

    for fid in finding_ids:
        try:
            if action == "apply":
                await apply_lint_fix(fid)
            elif action == "dismiss":
                await dismiss_lint_finding(fid)
            elif action == "send-to-review":
                await send_finding_to_review(fid)
            else:
                raise ValueError(f"Unknown batch action: {action!r}")
            results.append(BatchFindingResult(id=str(fid), status="ok", detail=None))
            ok_count += 1
        except HTTPException as exc:
            results.append(BatchFindingResult(id=str(fid), status="error", detail=exc.detail))
            error_count += 1
        except Exception as exc:  # noqa: BLE001
            results.append(BatchFindingResult(id=str(fid), status="error", detail=str(exc)))
            error_count += 1

    return BatchFindingsResponse(results=results, ok_count=ok_count, error_count=error_count)


# ── Paginated reads ─────────────────────────────────────────────────────────────


async def list_lint_findings(
    vault_id: str,
    *,
    status: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> LintFindingsPage:
    """
    Paginated lint_findings read for GET /lint/findings (ADR-0037 §6).

    Optional filters:
      status   (open|applied|dismissed). Ordered created_at ASC.
      category (any _VALID_CATEGORIES value — L10).
      severity (info|warning|error — L10).
    limit is capped by the REST endpoint (I7 — bounded page size).

    ``severity_totals`` is a COUNT(*) GROUP BY severity over the same vault + status +
    category predicate as ``total``, but IGNORING the severity filter and pagination.
    This lets the UI show accurate per-severity group headers regardless of the active
    severity filter. One indexed GROUP BY query (I1/I7).
    """
    async with get_session() as session:
        # ── Total (respects all active filters including severity) ──────────────
        count_stmt = (
            select(func.count()).select_from(LintFinding).where(LintFinding.vault_id == vault_id)
        )
        if status is not None:
            count_stmt = count_stmt.where(LintFinding.status == status)
        if category is not None:
            count_stmt = count_stmt.where(LintFinding.category == category)
        if severity is not None:
            count_stmt = count_stmt.where(LintFinding.severity == severity)
        total: int = (await session.execute(count_stmt)).scalar_one()

        # ── Per-severity totals: same vault + status + category; NO severity filter ──
        # One bounded indexed GROUP BY — never a full table scan (I1/I7).
        sev_stmt = select(LintFinding.severity, func.count().label("n")).where(
            LintFinding.vault_id == vault_id
        )
        if status is not None:
            sev_stmt = sev_stmt.where(LintFinding.status == status)
        if category is not None:
            sev_stmt = sev_stmt.where(LintFinding.category == category)
        sev_stmt = sev_stmt.group_by(LintFinding.severity)
        severity_totals: dict[str, int] = {
            sev: int(n) for sev, n in (await session.execute(sev_stmt)).all() if sev is not None
        }

        # ── Page data ───────────────────────────────────────────────────────────
        data_stmt = select(LintFinding).where(LintFinding.vault_id == vault_id)
        if status is not None:
            data_stmt = data_stmt.where(LintFinding.status == status)
        if category is not None:
            data_stmt = data_stmt.where(LintFinding.category == category)
        if severity is not None:
            data_stmt = data_stmt.where(LintFinding.severity == severity)
        data_stmt = data_stmt.order_by(LintFinding.created_at.asc()).offset(offset).limit(limit)
        rows = list((await session.execute(data_stmt)).scalars().all())
        for r in rows:
            session.expunge(r)

    return LintFindingsPage(
        items=rows,
        total=total,
        limit=limit,
        offset=offset,
        severity_totals=severity_totals,
    )


async def list_lint_runs(
    vault_id: str | None = None,
    *,
    limit: int = 20,
    offset: int = 0,
) -> LintRunsPage:
    """Paginated lint_runs read for GET /lint/runs (ADR-0037 §6). Ordered created_at DESC."""
    async with get_session() as session:
        count_stmt = select(func.count()).select_from(LintRun)
        if vault_id is not None:
            count_stmt = count_stmt.where(LintRun.vault_id == vault_id)
        total: int = (await session.execute(count_stmt)).scalar_one()

        data_stmt = select(LintRun)
        if vault_id is not None:
            data_stmt = data_stmt.where(LintRun.vault_id == vault_id)
        data_stmt = data_stmt.order_by(LintRun.created_at.desc()).offset(offset).limit(limit)
        rows = list((await session.execute(data_stmt)).scalars().all())
        for r in rows:
            session.expunge(r)

    return LintRunsPage(items=rows, total=total, limit=limit, offset=offset)


# ── Deterministic structural checks (NO provider call — I1) ─────────────────────


async def _detect_orphans(vault_id: str) -> list[FindingDTO]:
    """
    Detect orphan pages: live wiki pages with graph in-degree 0 (ADR-0037 §3.1).

    in-degree 0 = no RESOLVED incoming wikilink (links.target_page_id == page.id,
    dangling=false) from a content wiki page. Reads only the pages + links tables
    (I1 — no vault walk). Bounded at _ORPHAN_SCAN_MAX_PAGES.

    L-bug1 parity fix: inbound links are counted ONLY from content pages (source page
    must be a live wiki/* page whose basename is NOT index.md or log.md). Links from
    index.md/log.md do NOT count as inbound — they are navigation roots and linking
    nearly everything, which made almost nothing appear as an orphan under the old
    unfiltered query. overview.md is intentionally NOT excluded (L4 parity).

    index.md / log.md are excluded from the candidate set (they are navigation roots).
    overview.md is eligible (L4 parity with lint.ts:160-162 which only excludes index/log).

    L3: each orphan finding includes a `suggested_target` + `suggested_page_id` pointing
    to the page that *should* link to the orphan (token-overlap fuzzy scorer, port of
    lint.ts suggestRelatedPage, direction="source" — bounded to _CANDIDATE_TITLES_MAX).

    L5: severity is `info` (matches the reference lint.ts orphan category).
    """
    out: list[FindingDTO] = []
    try:
        from app.models import Link

        async with get_session() as session:
            # Live wiki pages (exclude raw/* tracking rows and navigation roots).
            page_rows = list(
                (
                    await session.execute(
                        select(Page.id, Page.title, Page.file_path)
                        .where(
                            Page.vault_id == vault_id,
                            Page.deleted_at.is_(None),
                            Page.file_path.like("wiki/%"),
                        )
                        .order_by(Page.created_at.asc())
                        .limit(_ORPHAN_SCAN_MAX_PAGES)
                    )
                ).all()
            )

            # Resolved incoming-link target ids (in-degree >= 1). Only count links whose
            # SOURCE page is a live wiki content page in THIS vault that is not index.md or
            # log.md (basename-based exclusion so subdirectory index/log variants are also
            # excluded). The vault join stops a cross-vault same-id link from masking an
            # orphan (from 1.3.12); the index/log exclusion stops index.md — which links
            # nearly everything — from masking true orphans (llm_wiki parity, L-bug1).
            target_rows = list(
                (
                    await session.execute(
                        select(func.distinct(Link.target_page_id))
                        .join(Page, Link.source_page_id == Page.id)
                        .where(
                            Page.vault_id == vault_id,
                            Page.deleted_at.is_(None),
                            Page.file_path.like("wiki/%"),
                            Page.file_path.not_like("%/index.md"),
                            Page.file_path.not_like("%/log.md"),
                            Link.target_page_id.isnot(None),
                        )
                    )
                ).scalars()
            )
            linked_ids = {str(t) for t in target_rows if t is not None}

            # L3: load candidate pages for fuzzy suggestion (bounded, I7).
            candidates = await _load_candidate_pages_fuzzy(vault_id, session)

        for pid, title, file_path in page_rows:
            rel = (file_path or "").lower()
            base = rel.rsplit("/", 1)[-1]
            # L4 parity: exclude only index.md and log.md (navigation roots).
            # overview.md is now eligible for orphan detection (lint.ts:160-162).
            if base in {"index.md", "log.md"}:
                continue
            if str(pid) in linked_ids:
                continue

            # L3: suggest a SOURCE page that should link to this orphan.
            suggested_target: str | None = None
            suggested_page_id: uuid.UUID | None = None
            proposed_action: str | None = None
            suggestion = _fuzzy_suggest_page(
                page_title=title or "",
                page_fp=file_path or "",
                candidates=candidates,
                exclude_page_fp=file_path or "",  # never suggest self
                exclude_titles=None,  # direction="source" — no outlink exclusion
            )
            if suggestion is not None:
                suggested_target, sugg_id_str = suggestion
                suggested_page_id = uuid.UUID(sugg_id_str)
                proposed_action = f"Add [[{title or base}]] to ## Related in {suggested_target!r}."

            out.append(
                FindingDTO(
                    category="orphan-page",
                    severity="info",  # L5 — info, matching reference
                    description=(
                        f"Page {title or rel!r} has no incoming wikilinks (orphan). "
                        "It is unreachable by graph navigation."
                    ),
                    target_title=title,
                    target_page_id=uuid.UUID(str(pid)),
                    proposed_action=proposed_action,
                    suggested_target=suggested_target,  # L3 — source page title
                    suggested_page_id=suggested_page_id,  # L3 — source page id
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_detect_orphans: failed (non-fatal): %s", exc)
    return out


# ── L1 — broken-wikilink detection (deterministic, NO provider call) ────────────


async def _detect_broken_wikilinks(vault_id: str) -> list[FindingDTO]:
    """
    Detect broken wikilinks: Link rows with dangling=True for the vault (L1 / ADR-0037 B1).

    For each dangling link:
      - category = "broken-wikilink", severity = "warning"
      - target_page_id = the REFERENCING page id (so the UI "Open" opens the page
        containing the broken link — inverted vs other categories per ADR review note)
      - target_title = the dangling target text (the [[broken]] part)
      - suggested_target / suggested_page_id: tolerant resolver result (L2)
      - proposed_action: "Rewrite [[old]] → [[Suggested]]" when suggestion found, else None

    DEDUP (within-scan):
      (a) one finding per (referencing_page_id, target_text) — enforced via seen set
      (b) skip if an OPEN finding with same category+target_page_id+target_title already in DB

    Bounded at _BROKEN_SCAN_MAX_LINKS (I7). Reads links + pages tables only (I1).
    """
    out: list[FindingDTO] = []
    try:
        from app.models import Link

        async with get_session() as session:
            # Load dangling links for this vault via the source page's vault_id (I1).
            # Join to the referencing page so we can filter by vault_id and get the title.
            dangling_rows = list(
                (
                    await session.execute(
                        select(
                            Link.id,
                            Link.source_page_id,
                            Link.target_title,
                            Page.title.label("referencing_title"),
                        )
                        .join(Page, Link.source_page_id == Page.id)
                        .where(
                            Link.dangling.is_(True),
                            Page.vault_id == vault_id,
                            Page.deleted_at.is_(None),
                        )
                        .order_by(Link.created_at.asc())
                        .limit(_BROKEN_SCAN_MAX_LINKS)
                    )
                ).all()
            )

            if not dangling_rows:
                return out

            # Build resolver maps ONCE for all suggestions (I1 — no N+1).
            from app.wiki.links import resolve_suggested_target

            # Load existing OPEN broken-wikilink findings for dedup (b).
            existing_open = {
                (str(r[0]), str(r[1]))
                for r in (
                    await session.execute(
                        select(LintFinding.target_page_id, LintFinding.target_title).where(
                            LintFinding.vault_id == vault_id,
                            LintFinding.category == "broken-wikilink",
                            LintFinding.status == "open",
                        )
                    )
                ).all()
                if r[0] is not None and r[1] is not None
            }

            # within-scan dedup set: (source_page_id_str, target_text)
            seen_within_scan: set[tuple[str, str]] = set()

            for _link_id, source_page_id, target_text, referencing_title in dangling_rows:
                if not target_text:
                    continue
                src_str = str(source_page_id)
                dedup_key = (src_str, target_text)

                # (a) within-scan dedup
                if dedup_key in seen_within_scan:
                    continue
                seen_within_scan.add(dedup_key)

                # (b) existing OPEN finding with same (referencing_page_id, target_title)
                if dedup_key in existing_open:
                    continue

                ref_title = referencing_title or src_str
                description = (
                    f"Broken link: [[{target_text}]] — target page not found. " f"(in {ref_title})"
                )

                # L2: tolerant resolver for suggestion
                suggestion = await resolve_suggested_target(target_text, session)
                suggested_target: str | None = None
                suggested_page_id: uuid.UUID | None = None
                proposed_action: str | None = None

                if suggestion is not None:
                    suggested_page_id, suggested_target = suggestion
                    proposed_action = f"Rewrite [[{target_text}]] → [[{suggested_target}]]"

                out.append(
                    FindingDTO(
                        category="broken-wikilink",
                        severity="warning",
                        description=description,
                        # target_page_id = referencing page (so "Open" opens it — ADR review note)
                        target_page_id=uuid.UUID(src_str),
                        target_title=target_text,  # the dangling [[Target]] text
                        proposed_action=proposed_action,
                        suggested_target=suggested_target,
                        suggested_page_id=suggested_page_id,
                    )
                )

    except Exception as exc:  # noqa: BLE001
        logger.warning("_detect_broken_wikilinks: failed (non-fatal): %s", exc)
    return out


# ── L1 — no-outlinks detection (deterministic, NO provider call) ─────────────────


async def _detect_no_outlinks(vault_id: str) -> list[FindingDTO]:
    """
    Detect pages with zero outgoing wikilinks: live wiki pages with NO links rows
    where source_page_id == page.id (L1 / ADR-0058 §L1, reference lint.ts:267-276).

    Reads only the pages + links tables (I1 — no vault walk). Bounded at
    _NO_OUTLINKS_SCAN_MAX_PAGES. index.md / log.md / overview.md excluded (same
    exclusions as _detect_orphans).

    L3: each finding includes a `suggested_target` pointing to the best related page
    the no-outlinks page should link to (fuzzy token-overlap scorer, bounded to
    _CANDIDATE_TITLES_MAX, direction="target").

    L5: severity is `info` (matches the reference lint.ts no-outlinks category).
    """
    out: list[FindingDTO] = []
    try:
        from sqlalchemy import exists as sa_exists
        from sqlalchemy import not_

        from app.models import Link

        async with get_session() as session:
            # Subquery: page ids that HAVE at least one outgoing link.
            has_outlink_sq = select(Link.source_page_id).where(Link.source_page_id == Page.id)

            # Live wiki pages with ZERO outgoing links (no row in links where source=page).
            page_rows = list(
                (
                    await session.execute(
                        select(Page.id, Page.title, Page.file_path)
                        .where(
                            Page.vault_id == vault_id,
                            Page.deleted_at.is_(None),
                            Page.file_path.like("wiki/%"),
                            not_(sa_exists(has_outlink_sq)),
                        )
                        .order_by(Page.created_at.asc())
                        .limit(_NO_OUTLINKS_SCAN_MAX_PAGES)
                    )
                ).all()
            )

            if not page_rows:
                return out

            # L3: load candidate pages for fuzzy suggestion (bounded, I7).
            candidates = await _load_candidate_pages_fuzzy(vault_id, session)

        for pid, title, file_path in page_rows:
            rel = (file_path or "").lower()
            base = rel.rsplit("/", 1)[-1]
            # L4 parity: exclude only index.md and log.md (navigation roots).
            # overview.md is now eligible for no-outlinks detection (lint.ts:160-162).
            if base in {"index.md", "log.md"}:
                continue

            # L3: suggest a TARGET page to link to (direction="target").
            suggested_target: str | None = None
            suggested_page_id: uuid.UUID | None = None
            proposed_action: str | None = None
            suggestion = _fuzzy_suggest_page(
                page_title=title or "",
                page_fp=file_path or "",
                candidates=candidates,
                exclude_page_fp=file_path or "",  # never suggest self
                exclude_titles=None,  # page has no outlinks → nothing to exclude
            )
            if suggestion is not None:
                suggested_target, sugg_id_str = suggestion
                suggested_page_id = uuid.UUID(sugg_id_str)
                proposed_action = f"Add [[{suggested_target}]] to ## Related in {title or base!r}."

            out.append(
                FindingDTO(
                    category="no-outlinks",
                    severity="info",  # L5 — info, matching reference
                    description=(
                        f"Page {title or rel!r} has no [[wikilink]] references to other pages."
                    ),
                    target_title=title,
                    target_page_id=uuid.UUID(str(pid)),
                    proposed_action=proposed_action,
                    suggested_target=suggested_target,  # L3
                    suggested_page_id=suggested_page_id,  # L3
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_detect_no_outlinks: failed (non-fatal): %s", exc)
    return out


# ── Semantic pass (ONE bounded provider call per round — I6/I7) ─────────────────


async def _semantic_pass(
    *,
    provider: Any,
    vault_id: str,
    page_digest: str,
    candidate_titles: list[str],
    already_found: list[str],
    token_budget: int,
    timeout_s: float,
) -> str:
    """
    ONE bounded provider.chat() turn for the semantic checks (ADR-0037 §4.3).

    Rides the chat() seam (backend-neutral — I6); cost recorded out of band on the bound
    accumulator. On timeout / error → returns "" (degrade; the deterministic findings stand).
    """
    instruction = _build_semantic_instruction(
        page_digest=page_digest,
        candidate_titles=candidate_titles,
        already_found=already_found,
        token_budget=token_budget,
    )
    try:
        return await asyncio.wait_for(_chat_collect(provider, instruction), timeout=timeout_s)
    except TimeoutError:
        logger.warning(
            "_semantic_pass: provider call timed out after %.1fs (vault=%s) — "
            "deterministic findings only",
            timeout_s,
            vault_id,
        )
        return ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("_semantic_pass: provider call failed (vault=%s): %s", vault_id, exc)
        return ""


# ── L3 — Fuzzy suggestion helpers (port of lint.ts suggestRelatedPage) ───────────


def _tokenize_for_suggestion(text: str) -> frozenset[str]:
    """
    Tokenize *text* for fuzzy page-suggestion scoring (L3).

    Port of lint.ts::tokenizeForSuggestion. NFKC-normalises, lower-cases, extracts
    word-tokens (letters + digits, no underscores, len >= 2). For CJK tokens, also adds
    each individual character (single-char CJK weight applied at scoring time).
    Returns a frozenset so it is hashable and safe to cache.
    """
    tokens: set[str] = set()
    normalized = unicodedata.normalize("NFKC", text).lower()
    for m in _WORD_RE.finditer(normalized):
        token = m.group(0)
        if len(token) >= 2:
            tokens.add(token)
        if _CJK_RE.search(token):
            for char in token:
                tokens.add(char)
    return frozenset(tokens)


def _fuzzy_score(
    source_tokens: frozenset[str],
    candidate_tokens: frozenset[str],
    same_folder: bool,
) -> float:
    """
    Token-overlap relevance score between two pages (L3, port of lint.ts suggestRelatedPage).

    overlap / sqrt(|A| * |B|) + same-folder bonus. CJK single chars weighted lower.
    Returns 0.0 when there is no token overlap.
    """
    if not source_tokens or not candidate_tokens:
        return 0.0
    overlap: float = 0.0
    for token in source_tokens:
        if token in candidate_tokens:
            overlap += 1.0 if len(token) > 1 else _SINGLE_CJK_TOKEN_WEIGHT
    if overlap == 0.0:
        return 0.0
    score = overlap / math.sqrt(max(1, len(source_tokens)) * max(1, len(candidate_tokens)))
    if same_folder:
        score += _SAME_FOLDER_SCORE_BONUS
    return score


async def _load_candidate_pages_fuzzy(
    vault_id: str,
    session: Any,
) -> list[tuple[str, str, str]]:
    """
    Bounded load of (id_str, title, file_path) for all live wiki pages in the vault
    (L3 — fuzzy suggestion candidate pool; capped at _CANDIDATE_TITLES_MAX, I7).

    Reads only the pages table (I1 — no vault walk). Ordered by updated_at DESC so
    the most recently edited pages lead the pool (better suggestions for active vaults).
    """
    rows = await session.execute(
        select(Page.id, Page.title, Page.file_path)
        .where(
            Page.vault_id == vault_id,
            Page.deleted_at.is_(None),
            Page.file_path.like("wiki/%"),
            Page.title.isnot(None),
        )
        .order_by(Page.updated_at.desc())
        .limit(_CANDIDATE_TITLES_MAX)
    )
    return [(str(r[0]), r[1] or "", r[2] or "") for r in rows.all()]


def _fuzzy_suggest_page(
    *,
    page_title: str,
    page_fp: str,
    candidates: list[tuple[str, str, str]],
    exclude_page_fp: str,
    exclude_titles: set[str] | None = None,
) -> tuple[str, str] | None:
    """
    Return (best_title, best_id_str) for the candidate most relevant to *page_title*/*page_fp*
    using token-overlap scoring (L3, port of lint.ts::suggestRelatedPage).

    Args:
        page_title: title of the page being scored.
        page_fp: file_path of the page being scored (used for folder bonus + self-exclusion).
        candidates: list of (id_str, title, file_path) from _load_candidate_pages_fuzzy.
        exclude_page_fp: skip any candidate whose file_path equals this (avoids self-reference).
        exclude_titles: optional set of titles to skip (for direction="target": pages already
                        linked from the source page; for direction="source": not needed).

    Returns None when no candidate reaches _RELATED_PAGE_SUGGESTION_MIN_SCORE.
    """
    # Tokenize the source page using title + filename stem for richer overlap.
    path_stem = page_fp.rsplit("/", 1)[-1].replace(".md", "").replace("-", " ").replace("_", " ")
    source_text = f"{page_title}\n{path_stem}"
    source_tokens = _tokenize_for_suggestion(source_text)
    if not source_tokens:
        return None

    source_folder = page_fp.rsplit("/", 1)[0] if "/" in page_fp else ""
    exclude_norm: set[str] = {t.lower() for t in (exclude_titles or set())}

    best_id: str | None = None
    best_title: str | None = None
    best_score: float = 0.0

    for cand_id, cand_title, cand_fp in candidates:
        if cand_fp == exclude_page_fp:
            continue
        if cand_title.lower() in exclude_norm:
            continue

        cand_stem = (
            cand_fp.rsplit("/", 1)[-1].replace(".md", "").replace("-", " ").replace("_", " ")
        )
        cand_tokens = _tokenize_for_suggestion(f"{cand_title}\n{cand_stem}")
        cand_folder = cand_fp.rsplit("/", 1)[0] if "/" in cand_fp else ""
        score = _fuzzy_score(source_tokens, cand_tokens, same_folder=(cand_folder == source_folder))

        if score > best_score:
            best_score = score
            best_id = cand_id
            best_title = cand_title

    if best_score >= _RELATED_PAGE_SUGGESTION_MIN_SCORE and best_id and best_title:
        return (best_title, best_id)
    return None


# ── Shared apply helper — append wikilink under ## Related heading (L4) ──────────


def _append_wikilink_to_body(body: str, link_target: str) -> str:
    """
    Append ``- [[link_target]]`` under the ``## Related`` heading in *body*, creating the
    heading if absent. Idempotent: no-ops if the link already exists (L4/I5).

    Port of lint-fixes.ts::appendWikilink.
    """
    # Idempotency: skip if the link already exists anywhere in the body.
    link_norm = link_target.lower()
    for m in re.finditer(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", body):
        if m.group(1).strip().lower() == link_norm:
            return body  # already present

    link_line = f"- [[{link_target}]]"
    heading_match = re.search(r"^##\s+Related\s*$", body, re.IGNORECASE | re.MULTILINE)
    if heading_match:
        insert_at = heading_match.end()
        return body[:insert_at] + "\n" + link_line + body[insert_at:]
    # No ## Related heading → append one at the end of the body.
    return body.rstrip("\n") + "\n\n## Related\n" + link_line + "\n"


async def _read_page_file_for_apply(
    page_id_str: str,
) -> tuple[str, str, str, str, bool] | None:
    """
    Load a page for the apply path and return (file_path, abs_path_str, fm_block, body, have_fm).

    Returns None when the page no longer exists (caller raises 404/502 as appropriate).
    Uses portable CAST(id AS TEXT) for SQLite/Postgres parity.
    """
    from sqlalchemy import text as sa_text

    async with get_session() as session:
        row = (
            await session.execute(
                sa_text(
                    "SELECT id, vault_id, file_path, title "
                    "FROM pages WHERE CAST(id AS TEXT) = :pid AND deleted_at IS NULL"
                ).bindparams(pid=page_id_str)
            )
        ).first()

    if row is None:
        return None

    file_path: str = row.file_path
    abs_path = settings.vault_root / file_path

    try:
        raw = abs_path.read_text(encoding="utf-8")
    except OSError:
        return None

    if raw.startswith("---\n"):
        parts = raw.split("---\n", maxsplit=2)
        if len(parts) == 3:
            return file_path, str(abs_path), parts[1], parts[2], True

    return file_path, str(abs_path), "", raw, False


async def _write_body_back(
    *,
    file_path: str,
    abs_path_str: str,
    fm_block: str,
    new_body: str,
    have_frontmatter: bool,
    source_page_id: uuid.UUID,
) -> None:
    """
    Write the updated body back to disk, re-persist links, and bump data_version once (I1/I5).
    Used by _apply_no_outlinks and _apply_orphan_page.
    """
    import frontmatter as _fm
    from fastapi import HTTPException

    from app.wiki.links import parse_wikilinks, persist_links

    new_raw = ("---\n" + fm_block + "---\n" + new_body) if have_frontmatter else new_body

    try:
        import pathlib

        pathlib.Path(abs_path_str).write_text(new_raw, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"apply write failed for {file_path}: {exc}",
        ) from exc

    try:
        post = _fm.loads(new_raw)
        parsed = parse_wikilinks(post.content)
        async with get_session() as session:
            await persist_links(session, source_page_id, parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_write_body_back: persist_links failed for %s: %s", file_path, exc)

    try:
        from app.ingest.orchestrator import bump_version

        await bump_version()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_write_body_back: bump_version failed: %s", exc)


# ── Apply seams (ADR-0037 §5) ───────────────────────────────────────────────────


async def _apply_broken_wikilink(finding: LintFinding) -> str:
    """
    Apply a broken-wikilink fix (L3 / ADR-0037 B1 / I1/I5).

    When a suggestion exists (finding.suggested_target is not None):
      1. Load the referencing page file (finding.target_page_id is the REFERENCING page).
      2. Rewrite occurrences of [[old]] and [[old|label]] to [[Suggested]] / [[Suggested|label]]
         in the BODY ONLY (split on leading --- frontmatter fence — I5).
      3. Write the file, re-run persist_links, bump data_version ONCE (I1).

    When no suggestion exists → flag-only acknowledgement (same as orphan-page).

    Raises:
      HTTPException(404) — referencing page no longer exists.
      HTTPException(409) — finding has no target_page_id (defensive).
      HTTPException(502) — file write / link persist failed.
    """
    import re as _re

    from fastapi import HTTPException
    from sqlalchemy import text as sa_text

    # ── No suggestion → create a stub page for the missing target (L4/ADR-0058 §L4) ──
    if not finding.suggested_target:
        return await _create_broken_link_stub(finding)

    if finding.target_page_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "broken-wikilink apply failed: the finding carries no referencing page id. "
                "Dismiss it or re-run lint."
            ),
        )

    old_target = finding.target_title or ""
    new_target = finding.suggested_target

    if not old_target:
        return (
            f"broken-wikilink: target_title empty; acknowledged. " f"Suggestion was {new_target!r}."
        )

    # ── Load the referencing page ─────────────────────────────────────────────────
    async with get_session() as session:
        row = (
            await session.execute(
                sa_text(
                    "SELECT id, vault_id, file_path, title "
                    "FROM pages WHERE CAST(id AS TEXT) = :pid AND deleted_at IS NULL"
                ).bindparams(pid=str(finding.target_page_id))
            )
        ).first()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "broken-wikilink apply failed: the referencing page no longer exists. "
                "Finding left open — dismiss or re-run lint."
            ),
        )

    file_path: str = row.file_path
    abs_path = settings.vault_root / file_path

    # ── Read + split frontmatter / body (I5 — NEVER touch frontmatter) ───────────
    try:
        raw = abs_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"broken-wikilink apply failed: cannot read {file_path}: {exc}",
        ) from exc

    if raw.startswith("---\n"):
        parts = raw.split("---\n", maxsplit=2)
        if len(parts) == 3:
            fm_block, body = parts[1], parts[2]
            have_frontmatter = True
        else:
            fm_block, body = "", raw
            have_frontmatter = False
    else:
        fm_block, body = "", raw
        have_frontmatter = False

    # ── Anchored regex rewrite in body only ───────────────────────────────────────
    # Match [[old_target]] and [[old_target|label]] (escaped for regex safety).
    old_escaped = _re.escape(old_target)
    pattern = _re.compile(r"\[\[" + old_escaped + r"(?:\|([^\[\]]*))?\]\]")

    def _replace(m: _re.Match[str]) -> str:
        label = m.group(1)  # None if no alias
        if label is not None:
            return f"[[{new_target}|{label}]]"
        return f"[[{new_target}]]"

    new_body = pattern.sub(_replace, body)

    if new_body == body:
        return (
            f"broken-wikilink: no occurrences of [[{old_target}]] found in body of {file_path!r}; "
            "acknowledged without edit."
        )

    # ── Write the file back (I5 — frontmatter preserved byte-for-byte) ───────────
    if have_frontmatter:
        new_raw = "---\n" + fm_block + "---\n" + new_body
    else:
        new_raw = new_body

    try:
        abs_path.write_text(new_raw, encoding="utf-8")
    except OSError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"broken-wikilink apply failed: cannot write {file_path}: {exc}",
        ) from exc

    # ── Re-persist links for the rewritten file (I1) ──────────────────────────────
    try:
        import frontmatter as _fm

        from app.wiki.links import parse_wikilinks, persist_links

        post = _fm.loads(new_raw)
        parsed = parse_wikilinks(post.content)
        async with get_session() as session:
            await persist_links(session, uuid.UUID(str(finding.target_page_id)), parsed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("_apply_broken_wikilink: persist_links failed for %s: %s", file_path, exc)

    # ── Bump data_version ONCE (I1) ───────────────────────────────────────────────
    try:
        from app.ingest.orchestrator import bump_version

        await bump_version()
    except Exception as exc:  # noqa: BLE001
        logger.warning("_apply_broken_wikilink: bump_version failed: %s", exc)

    return (
        f"broken-wikilink: rewrote [[{old_target}]] → [[{new_target}]] "
        f"in body of {file_path!r} (data_version bumped once, I1)."
    )


async def _apply_missing_xref(finding: LintFinding) -> str:
    """
    Apply a missing-xref fix by reusing the wikilink-enrichment seam (I1/I5).

    Runs the bounded ops/enrich_wikilinks.enrich_wikilinks pass over the referencing page,
    which adds [[target]] links into the BODY only and bumps data_version ONCE (I1). The pass
    is provider-agnostic (I6) and fully bounded (I7). Returns a resolution note.
    """
    from fastapi import HTTPException

    from app.ops.enrich_wikilinks import enrich_wikilinks

    if finding.target_page_id is None:
        # No concrete referencing page → fall back to flag-only acknowledgement.
        return (
            "missing-xref: no referencing page recorded; acknowledged without edit "
            "(re-run lint after editing)."
        )

    # Load the referencing page by id. CAST to text for SQLite/Postgres parity (mirrors
    # graph/engine.py) so the lookup works regardless of the id column's native type.
    from sqlalchemy import text as sa_text

    async with get_session() as session:
        row = (
            await session.execute(
                sa_text(
                    "SELECT id, vault_id, file_path, title, type AS page_type "
                    "FROM pages WHERE CAST(id AS TEXT) = :pid"
                ).bindparams(pid=str(finding.target_page_id))
            )
        ).first()
    page = None
    if row is not None:
        page = Page(
            id=uuid.UUID(str(row.id)),
            vault_id=row.vault_id,
            file_path=row.file_path,
            title=row.title,
            page_type=row.page_type,
            content_hash="",
        )

    if page is None:
        raise HTTPException(
            status_code=502,
            detail=(
                "missing-xref apply failed: the referencing page no longer exists. "
                "Finding left open — dismiss or re-run lint."
            ),
        )

    result = await enrich_wikilinks([page], finding.vault_id)
    return (
        f"missing-xref: ran wikilink-enrichment over {page.title!r} — "
        f"links_added={result.links_added} (data_version bumped once on edit, I1)."
    )


async def _apply_missing_page(finding: LintFinding) -> str:
    """
    Apply a missing-page fix by delegating to the lazy-generation seam used by
    review.create_page_from_review (ADR-0034 §5) — bounded orchestrated loop, one
    data_version bump via write_wiki_page (I1). Provider-agnostic (I6).
    """
    from fastapi import HTTPException

    from app.ingest.orchestrator import write_wiki_page
    from app.ops.review import _run_generation
    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    title = finding.target_title or _title_from_description(finding.description)
    if not title:
        raise HTTPException(
            status_code=409,
            detail=(
                "missing-page apply failed: the finding carries no target title to create. "
                "Dismiss it or edit the wiki manually."
            ),
        )

    # Resolve the ingest provider (I6 — 409 if none configured).
    try:
        provider_config_row = await resolve_provider_config("ingest", finding.vault_id)
    except ConfigNotFoundError as cnfe:
        raise HTTPException(
            status_code=409,
            detail=(
                "No ingest provider configured for this vault. Configure a provider before "
                "applying a missing-page fix (I6)."
            ),
        ) from cnfe

    origin_source = f"lint:{finding.id}"
    try:
        # Capability-aware (I6): _run_generation delegates to an agentic provider (which writes
        # the page itself via MCP write_page) or runs the orchestrated loop (returning a WikiPage
        # this caller writes once). Exactly one write per page either way (I1) — never double.
        outcome = await _run_generation(
            vault_id=finding.vault_id,
            proposed_title=title,
            proposed_page_type=None,  # heuristic at generation time (ADR-0034 §5.2)
            rationale=finding.description,
            origin_source=origin_source,
            provider_config_row=provider_config_row,
        )
        if outcome.created_page_id is not None:
            # Delegated route: the agent already wrote the page — do NOT write again (I1).
            created_page_id = outcome.created_page_id
        elif outcome.wiki_page is not None:
            # Orchestrated route: write the produced page once via the single incremental seam.
            created_page = await write_wiki_page(None, outcome.wiki_page, origin_source)
            created_page_id = str(created_page.id)
        else:
            # Defensive: _run_generation raises rather than returning an empty outcome.
            raise RuntimeError("page generation produced no page")
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "_apply_missing_page: generation/write failed for finding=%s: %s — left open",
            finding.id,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail=(f"missing-page apply failed: {exc}. Finding left open — retry or dismiss."),
        ) from exc

    return (
        f"missing-page: created page {title!r} (page_id={created_page_id}; "
        "one data_version bump, I1)."
    )


async def _apply_no_outlinks(finding: LintFinding) -> str:
    """
    Apply a no-outlinks fix (L4 / ADR-0058 §L4 / I1/I5).

    Appends ``- [[suggested_target]]`` under ``## Related`` in the finding's page body
    (creates the heading if absent). Idempotent: no-ops if the link already exists.
    Re-persists links and bumps data_version ONCE (I1). Body-only edit (I5).

    Falls back to flag-only acknowledgement when no suggested_target is recorded.
    """
    from fastapi import HTTPException

    if not finding.suggested_target:
        return (
            "no-outlinks: no suggested target available; acknowledged as flag-only. "
            "Add a [[wikilink]] to the page manually."
        )

    if finding.target_page_id is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "no-outlinks apply failed: the finding carries no target page id. "
                "Dismiss it or re-run lint."
            ),
        )

    result = await _read_page_file_for_apply(str(finding.target_page_id))
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "no-outlinks apply failed: the target page no longer exists. "
                "Finding left open — dismiss or re-run lint."
            ),
        )

    file_path, abs_path_str, fm_block, body, have_frontmatter = result
    new_body = _append_wikilink_to_body(body, finding.suggested_target)

    if new_body == body:
        return (
            f"no-outlinks: [[{finding.suggested_target}]] already present in {file_path!r}; "
            "acknowledged without edit."
        )

    await _write_body_back(
        file_path=file_path,
        abs_path_str=abs_path_str,
        fm_block=fm_block,
        new_body=new_body,
        have_frontmatter=have_frontmatter,
        source_page_id=uuid.UUID(str(finding.target_page_id)),
    )
    return (
        f"no-outlinks: appended [[{finding.suggested_target}]] under ## Related in "
        f"{file_path!r} (data_version bumped once, I1)."
    )


async def _apply_orphan_page(finding: LintFinding) -> str:
    """
    Apply an orphan-page fix (L4 / ADR-0058 §L4 / I1/I5).

    When finding.suggested_page_id is set: appends ``- [[<orphan title>]]`` under
    ``## Related`` in the SUGGESTED SOURCE PAGE (the page that should link to the orphan).
    Re-persists links and bumps data_version ONCE (I1). Body-only edit (I5).

    Falls back to flag-only acknowledgement when no suggested_page_id is recorded,
    matching the pre-L4 behaviour for suggestion-less orphan-page findings.
    """
    from fastapi import HTTPException

    if not finding.suggested_page_id:
        return (
            "orphan-page: no suggested source page available; acknowledged as flag-only. "
            "Add a [[wikilink]] to this page from another page manually."
        )

    orphan_title = finding.target_title or "untitled"

    result = await _read_page_file_for_apply(str(finding.suggested_page_id))
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "orphan-page apply failed: the suggested source page no longer exists. "
                "Finding left open — dismiss or re-run lint."
            ),
        )

    file_path, abs_path_str, fm_block, body, have_frontmatter = result
    new_body = _append_wikilink_to_body(body, orphan_title)

    if new_body == body:
        return (
            f"orphan-page: [[{orphan_title}]] already present in suggested source "
            f"{file_path!r}; acknowledged without edit."
        )

    await _write_body_back(
        file_path=file_path,
        abs_path_str=abs_path_str,
        fm_block=fm_block,
        new_body=new_body,
        have_frontmatter=have_frontmatter,
        source_page_id=uuid.UUID(str(finding.suggested_page_id)),
    )
    return (
        f"orphan-page: appended [[{orphan_title}]] under ## Related in suggested source "
        f"{file_path!r} (data_version bumped once, I1)."
    )


async def _create_broken_link_stub(finding: LintFinding) -> str:
    """
    Create a stub page for a broken-wikilink finding that has no suggested_target (L4).

    Writes a type=query, tags=[stub, lint] stub page under queries/ via the normal
    write_wiki_page seam, then re-resolves links for the referencing page so the
    previously-dangling link connects to the new stub.  One data_version bump (I1).

    Port of lint-fixes.ts::ensureBrokenLinkStub.
    Falls back to flag-only acknowledgement on any failure (502 path).
    """
    from fastapi import HTTPException

    broken_target = finding.target_title or ""
    if not broken_target:
        return (
            "broken-wikilink: no broken target title recorded; acknowledged as flag-only. "
            "Dismiss and re-run lint."
        )

    # Derive a stub title from the broken target text.
    stub_title = (
        broken_target.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").strip()
        or "Missing Page"
    )

    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

    stub_page = WikiPage(
        title=stub_title,
        type=PageType.QUERY,
        content=(
            f"# {stub_title}\n\n"
            "Created by Wiki Lint as a placeholder for a missing wikilink target.\n"
        ),
        frontmatter=WikiFrontmatter(
            type=PageType.QUERY,
            title=stub_title,
            sources=[f"lint:{finding.id}"],
            lang="en",
            tags=["stub", "lint"],
        ),
    )

    try:
        created_page = await write_wiki_page(None, stub_page, f"lint:{finding.id}")
        created_page_id = str(created_page.id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "_create_broken_link_stub: write_wiki_page failed for %r: %s — left open",
            stub_title,
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail=(
                f"broken-wikilink stub creation failed for {stub_title!r}: {exc}. "
                "Finding left open — retry or dismiss."
            ),
        ) from exc

    # Re-resolve links for the referencing page so the now-existing stub connects.
    if finding.target_page_id is not None:
        try:
            from app.wiki.links import reresolve_dangling_links

            async with get_session() as session:
                reconnected = await reresolve_dangling_links(session)
            logger.debug(
                "_create_broken_link_stub: reresolve_dangling_links reconnected %d links",
                reconnected,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("_create_broken_link_stub: reresolve_dangling_links failed: %s", exc)

    return (
        f"broken-wikilink: created stub page {stub_title!r} (page_id={created_page_id}) "
        f"under queries/ (data_version bumped once via write_wiki_page, I1)."
    )


# ── DB helpers ─────────────────────────────────────────────────────────────────


async def _create_run_row(
    *,
    run_id: uuid.UUID,
    vault_id: str,
    max_iter: int,
    token_budget: int,
) -> None:
    """INSERT a lint_runs row with status='running' and frozen bounds (mirrors deep_research)."""
    async with get_session() as session:
        run = LintRun(
            id=str(run_id),
            vault_id=vault_id,
            status="running",
            max_iter=max_iter,
            token_budget=token_budget,
            iterations_used=0,
            findings_count=0,
            total_cost_usd=0,
            started_at=datetime.now(UTC),
            completed_at=None,
            error_message=None,
            created_at=datetime.now(UTC),
        )
        session.add(run)


async def _finalize_run_row(
    *,
    run_id: uuid.UUID,
    status: str,
    iterations_used: int,
    findings_count: int,
    total_cost_usd: float,
    error_message: str | None,
) -> None:
    """Write the terminal lint_runs state (always from finally — never left 'running')."""
    now = datetime.now(UTC)
    async with get_session() as session:
        await session.execute(
            update(LintRun)
            .where(LintRun.id == str(run_id))
            .values(
                status=status,
                iterations_used=iterations_used,
                findings_count=findings_count,
                total_cost_usd=total_cost_usd,
                completed_at=now,
                error_message=error_message,
            )
        )


async def _persist_findings(
    *,
    run_id: uuid.UUID,
    vault_id: str,
    findings: list[FindingDTO],
) -> None:
    """INSERT one lint_findings row per finding (ADR-0037 §3.2). Drops invalid categories."""
    if not findings:
        return
    async with get_session() as session:
        for f in findings:
            if f.category not in _VALID_CATEGORIES:
                continue
            severity = f.severity if f.severity in _VALID_SEVERITIES else "warning"
            target_page_id_str = str(f.target_page_id) if f.target_page_id is not None else None
            suggested_page_id_str = (
                str(f.suggested_page_id) if f.suggested_page_id is not None else None
            )
            finding_row = LintFinding(
                id=str(uuid.uuid4()),
                lint_run_id=str(run_id),
                vault_id=vault_id,
                category=f.category,
                severity=severity,
                target_page_id=target_page_id_str,
                target_title=f.target_title,
                description=f.description,
                proposed_action=f.proposed_action,
                suggested_target=f.suggested_target,
                suggested_page_id=suggested_page_id_str,
                status="open",
                resolution_note=None,
                created_at=datetime.now(UTC),
                reviewed_at=None,
            )
            session.add(finding_row)


async def _set_finding_status(
    finding_id: uuid.UUID,
    status: str,
    *,
    resolution_note: str | None = None,
) -> LintFinding:
    """Update status (+ reviewed_at + optional resolution_note) on a finding. 404 if absent."""
    from fastapi import HTTPException

    finding_id_str = str(finding_id)
    async with get_session() as session:
        row = await session.execute(select(LintFinding).where(LintFinding.id == finding_id_str))
        finding = row.scalar_one_or_none()
        if finding is None:
            raise HTTPException(status_code=404, detail=f"Lint finding {finding_id} not found")
        finding.status = status
        finding.reviewed_at = datetime.now(UTC)
        if resolution_note is not None:
            finding.resolution_note = resolution_note
        await session.flush()
        await session.refresh(finding)
        session.expunge(finding)
    return finding


# ── Bounded reads for the semantic prompt (I1) ──────────────────────────────────


async def _load_candidate_titles(vault_id: str) -> list[str]:
    """Bounded indexed read of live wiki page titles for the vault (I1 — no vault walk)."""
    async with get_session() as session:
        rows = await session.execute(
            select(Page.title)
            .where(
                Page.vault_id == vault_id,
                Page.deleted_at.is_(None),
                Page.title.isnot(None),
                Page.file_path.like("wiki/%"),
            )
            .order_by(Page.updated_at.desc())
            .limit(_CANDIDATE_TITLES_MAX)
        )
        return [t for (t,) in rows.all() if t and t.strip()]


async def _load_page_digest(vault_id: str, *, max_pages: int = 60) -> str:
    """Compact title+type digest of live wiki pages for the semantic prompt (bounded — I1)."""
    async with get_session() as session:
        rows = await session.execute(
            select(Page.title, Page.page_type)
            .where(
                Page.vault_id == vault_id,
                Page.deleted_at.is_(None),
                Page.title.isnot(None),
                Page.file_path.like("wiki/%"),
            )
            .order_by(Page.updated_at.desc())
            .limit(max_pages)
        )
        lines: list[str] = []
        for title, ptype in rows.all():
            t = (title or "").strip() or "(untitled)"
            pt = (ptype or "?").strip()
            lines.append(f"- {t} [{pt}]")
    return "\n".join(lines) if lines else "(none)"


# ── Provider resolution (I6) ────────────────────────────────────────────────────


async def _resolve_lint_provider(vault_id: str) -> tuple[Any, Any] | None:
    """
    Resolve the InferenceProvider for operation='ingest' (I6) for the semantic lint pass.

    Returns (provider, config_row) or None when no provider_config resolves / DB unavailable.
    NEVER hardcodes a backend; NEVER branches on isinstance/type/class-name. Mirrors
    ops/review.py::_resolve_review_provider and ops/enrich_wikilinks.py::_resolve_provider.
    """
    from app.ingest.provider import resolve_provider
    from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

    try:
        config_row = await resolve_provider_config("ingest", vault_id)
    except ConfigNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_resolve_lint_provider: provider resolution failed (vault=%s): %s", vault_id, exc
        )
        return None

    try:
        provider = resolve_provider(config_row)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "_resolve_lint_provider: provider build failed (vault=%s): %s", vault_id, exc
        )
        return None
    return provider, config_row


async def _chat_collect(provider: Any, instruction: str) -> str:
    """
    ONE capability-agnostic provider.chat() turn, collecting the full text (I6).

    Same surface ops/review.py, ops/deep_research.py and ops/enrich_wikilinks.py use —
    backend-neutral, no new ABC method, no isinstance/type branching.
    """
    from app.ingest.schemas import Message

    chunks: list[str] = []
    async for chunk in await provider.chat(
        messages=[Message(role="user", content=instruction)],
        retrieval_context="",
    ):
        chunks.append(chunk)
    return "".join(chunks).strip()


# ── Prompt + parse ──────────────────────────────────────────────────────────────


def _build_semantic_instruction(
    *,
    page_digest: str,
    candidate_titles: list[str],
    already_found: list[str],
    token_budget: int,
) -> str:
    """
    Build the single semantic-lint prompt (ADR-0037 §4.3).

    Asks for a JSON object {"findings": [...]} of health issues across the wiki. The model is
    told to return ONLY JSON and to NOT repeat any already-found description.
    """
    titles_block = "\n".join(f"- {t}" for t in candidate_titles[:_CANDIDATE_TITLES_MAX]) or "(none)"
    already_block = "\n".join(f"- {d}" for d in already_found[:200]) or "(none)"
    return (
        "You are the LINT step of a self-organizing wiki (the third Karpathy operation: "
        "Ingest, Query, Lint). Health-check the wiki and report problems for a human to "
        "review. Do NOT fix anything — only report findings.\n\n"
        f"# Existing wiki page titles\n{titles_block}\n\n"
        f"# Page digest (title [type])\n{page_digest}\n\n"
        f"# Already-reported findings (do NOT repeat these)\n{already_block}\n\n"
        'Return ONLY a JSON object with a single key "findings" whose value is a list of '
        "objects. Each object has keys:\n"
        "  category: one of contradiction | stale-claim | missing-page | suggestion\n"
        "  severity: one of info | warning | error\n"
        "  description: a short string explaining the problem\n"
        "  target_title: the existing page title the finding is about (for stale-claim), "
        "OR the title that SHOULD exist (for missing-page); omit or null if "
        "none applies\n\n"
        "Definitions: contradiction = conflicting claims across pages; stale-claim = superseded "
        "information; missing-page = a concept mentioned with no page; "
        "suggestion = a question or source worth adding to the wiki. "
        "(Note: missing-xref is handled deterministically; do NOT emit it.) "
        f"Keep the output well under {token_budget} tokens. Return no prose, only the JSON "
        "object."
    )


def _parse_findings(raw: str) -> list[FindingDTO]:
    """
    Parse the semantic findings JSON into FindingDTO list. Tolerant of code fences / prose;
    silently drops malformed entries (degrade, never raise). Unknown categories are dropped;
    orphan-page is NEVER accepted from the model (it is deterministic-only — ADR-0037 §3.1).
    """
    if not raw:
        return []
    obj = _loads_json_lenient(raw)
    if obj is None:
        return []
    if isinstance(obj, dict):
        items_raw = obj.get("findings", obj.get("items", []))
    elif isinstance(obj, list):
        items_raw = obj
    else:
        return []
    if not isinstance(items_raw, list):
        return []

    # Semantic categories accepted from the model.
    # Excluded (deterministic-only — must never come from the model):
    #   orphan-page  — ADR-0037 §3.1 / ADR-0058 §L1
    #   no-outlinks  — ADR-0058 §L1 (Do-NOT #21)
    #   missing-xref — L2 parity fix: llm_wiki does not have this category; it is
    #                  handled deterministically via links.dangling in the enrich seam.
    #                  Silently drop it if a model emits it anyway.
    semantic_categories = _VALID_CATEGORIES - {"orphan-page", "no-outlinks", "missing-xref"}

    out: list[FindingDTO] = []
    for entry in items_raw:
        if not isinstance(entry, dict):
            continue
        category = entry.get("category") or entry.get("type")
        if category not in semantic_categories:
            continue
        description = _clean_str(entry.get("description"))
        if not description:
            continue
        severity = _clean_str(entry.get("severity")) or "warning"
        if severity not in _VALID_SEVERITIES:
            severity = "warning"
        target_title = _clean_str(entry.get("target_title"))
        proposed_action: str | None = None
        if category == "missing-page" and target_title:
            proposed_action = f"Create a wiki page titled {target_title!r}."
        out.append(
            FindingDTO(
                category=category,
                severity=severity,
                description=description,
                target_title=target_title,
                proposed_action=proposed_action,
            )
        )
    return out


def _title_from_description(description: str) -> str | None:
    """Best-effort title extraction from a description for missing-page apply fallback."""
    # Look for a quoted phrase first.
    for quote in ("'", '"', "“", "”"):
        if quote in description:
            parts = description.split(quote)
            if len(parts) >= 3 and parts[1].strip():
                return parts[1].strip()
    return None


def _loads_json_lenient(raw: str) -> Any | None:
    """Best-effort JSON parse tolerant of ```json fences / surrounding prose. None on failure."""
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


def _clean_str(value: Any) -> str | None:
    """Return a stripped non-empty string, or None."""
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return None


def _coerce_int(raw: Any, fallback: int) -> int:
    """Coerce a provider-row token_budget (possibly None/Any) to int, else *fallback*."""
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value or fallback
