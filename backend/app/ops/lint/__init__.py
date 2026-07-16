"""
K2 Lint-fix loop — the third Karpathy core operation (Ingest · Query · **Lint**), ADR-0037.

ARCHITECTURE OVERVIEW (ADR-0037 §2):
  A periodic, BOUNDED, HUMAN-GATED health check of the wiki. ``run_lint_scan`` produces LINT
  FINDINGS (proposals); it NEVER auto-applies fixes. The human gate is ``apply_lint_fix`` —
  only safe/bounded fixes are ever applied, and at most one ``data_version`` bump per fix (I1).

PACKAGE LAYOUT (BE-REFAC-2 — pure refactor split of the former ~2650-line module):
  detectors.py    — the 3 deterministic structural detectors (orphan/broken-link/no-outlinks)
                    + the L3 fuzzy-suggestion helpers. NO provider call (I1).
  fixes.py        — the human-gated deterministic fix appliers (ADR-0037 §5) + the
                    category → handler registry + contradiction open-question authoring.
  semantic.py      — the LLM-backed opt-in semantic lint pass (missing-xref/contradiction/
                    stale-claim/missing-page/suggestion). Rides app.ops._llm (I6/I7).
  persistence.py  — lint_findings / lint_runs reads + writes.
  _shared.py       — dataclasses + constants shared across the submodules above.

  This module re-exports the full public surface so existing imports of
  ``app.ops.lint.<name>`` (routers/lint.py, ops_scheduler.py, migrate_lint_query_stubs.py, and
  the test suite) continue to work unchanged.

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
  No isinstance / provider_type / class-name branching anywhere in this package. The semantic
  pass rides ``InferenceProvider.chat()`` (the same surface review/deep-research/enrich use).

I1 CONTRACT:
  The scan reads only the pages + links tables (bounded indexed reads) — NEVER a full vault
  walk / re-scan. Apply edits touch ONLY the referencing page(s).
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from app.config import settings
from app.models import LintFinding
from app.ops._llm import resolve_operation_provider
from app.ops.lint._shared import (
    BATCH_MAX_IDS,
    CATEGORY_TO_ITEM_TYPE,
    COST_ANOMALY_THRESHOLD_USD,
    DETERMINISTIC_CATEGORIES,
    FLAG_ONLY_CATEGORIES,
    SEMANTIC_CATEGORIES,
    VALID_CATEGORIES,
    VALID_SEVERITIES,
    VALID_STATUSES,
    BatchFindingResult,
    BatchFindingsResponse,
    FindingDTO,
    LintFindingsPage,
    LintRunsPage,
    LintScanResult,
)
from app.ops.lint.detectors import (
    _detect_broken_wikilinks,
    _detect_no_outlinks,
    _detect_orphans,
    _fuzzy_score,
    _fuzzy_suggest_page,
    _load_candidate_pages_fuzzy,
    _load_candidate_titles,
    _load_page_digest,
    _tokenize_for_suggestion,
)
from app.ops.lint.fixes import (
    _APPLY_HANDLERS,
    _append_wikilink_to_body,
    _apply_broken_wikilink,
    _apply_contradiction,
    _apply_missing_page,
    _apply_missing_xref,
    _apply_no_outlinks,
    _apply_orphan_page,
    _create_broken_link_stub,
    _infer_stub_page_type,
    _title_from_description,
)
from app.ops.lint.persistence import (
    _create_run_row,
    _finalize_run_row,
    _persist_findings,
    _set_finding_status,
    _supersede_prior_open_findings,
    list_lint_findings,
    list_lint_runs,
)
from app.ops.lint.semantic import (
    _build_semantic_instruction,
    _norm_title_for_match,
    _parse_findings,
    _semantic_pass,
)

logger = logging.getLogger(__name__)

# Legacy underscore aliases for the constants — some call sites/tests import the
# module-private spelling that existed before the package split (BE-REFAC-2).
_VALID_CATEGORIES = VALID_CATEGORIES
_VALID_SEVERITIES = VALID_SEVERITIES
_VALID_STATUSES = VALID_STATUSES
_DETERMINISTIC_CATEGORIES = DETERMINISTIC_CATEGORIES
_SEMANTIC_CATEGORIES = SEMANTIC_CATEGORIES
_FLAG_ONLY_CATEGORIES = FLAG_ONLY_CATEGORIES
_COST_ANOMALY_THRESHOLD_USD = COST_ANOMALY_THRESHOLD_USD
_CATEGORY_TO_ITEM_TYPE = CATEGORY_TO_ITEM_TYPE
_BATCH_MAX_IDS = BATCH_MAX_IDS

__all__ = [
    # ── Public API (routers/lint.py, ops_scheduler.py) ──────────────────────────
    "LintScanResult",
    "LintFindingsPage",
    "LintRunsPage",
    "FindingDTO",
    "BatchFindingResult",
    "BatchFindingsResponse",
    "run_lint_scan",
    "apply_lint_fix",
    "dismiss_lint_finding",
    "send_finding_to_review",
    "apply_batch",
    "list_lint_findings",
    "list_lint_runs",
    # ── Re-exported for migrate_lint_query_stubs.py (explicit D1 reuse) ─────────
    "_infer_stub_page_type",
    # ── Re-exported for backward-compat direct imports (test suite — BE-REFAC-2) ──
    "_VALID_CATEGORIES",
    "_VALID_SEVERITIES",
    "_VALID_STATUSES",
    "_DETERMINISTIC_CATEGORIES",
    "_SEMANTIC_CATEGORIES",
    "_FLAG_ONLY_CATEGORIES",
    "_COST_ANOMALY_THRESHOLD_USD",
    "_CATEGORY_TO_ITEM_TYPE",
    "_BATCH_MAX_IDS",
    "_detect_orphans",
    "_detect_broken_wikilinks",
    "_detect_no_outlinks",
    "_fuzzy_score",
    "_fuzzy_suggest_page",
    "_load_candidate_pages_fuzzy",
    "_load_candidate_titles",
    "_load_page_digest",
    "_tokenize_for_suggestion",
    "_APPLY_HANDLERS",
    "_append_wikilink_to_body",
    "_apply_broken_wikilink",
    "_apply_contradiction",
    "_apply_missing_page",
    "_apply_missing_xref",
    "_apply_no_outlinks",
    "_apply_orphan_page",
    "_create_broken_link_stub",
    "_title_from_description",
    "_create_run_row",
    "_finalize_run_row",
    "_persist_findings",
    "_set_finding_status",
    "_supersede_prior_open_findings",
    "_build_semantic_instruction",
    "_norm_title_for_match",
    "_parse_findings",
    "_semantic_pass",
]


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
        # caps (ORPHAN_SCAN_MAX_PAGES / BROKEN_SCAN_MAX_LINKS). They are therefore
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
        elif (resolved := await resolve_operation_provider(vault_id)) is not None:
            from app.ops._llm import coerce_int

            provider, config_row = resolved
            provider.bind_accumulator(accumulator)
            token_budget_local = coerce_int(
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
                # Pass existing titles so the missing-page false-positive guard can drop
                # "create" findings for pages that already exist (llm_wiki parity).
                round_findings = _parse_findings(raw, existing_titles=candidate_titles)

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
        # persisted ≤ det_baseline (≤ ORPHAN_SCAN_MAX_PAGES + BROKEN_SCAN_MAX_LINKS)
        # + max_findings — still a hard ceiling (I7), just not one that hides free findings.
        findings = findings[: det_baseline + max_findings]
        await _persist_findings(run_id=run_id, vault_id=vault_id, findings=findings)

        # ── 4. Supersede prior runs' stale OPEN findings (llm_wiki fresh-recompute parity) ──
        # A new scan REPLACES the prior scan's open findings for the categories it recomputed,
        # so the queue never accumulates duplicates (48→90→107) and a fixed issue vanishes on
        # the next run. Category-aware: deterministic always runs; semantic only when enabled.
        # Runs AFTER persist so this scan's fresh rows are never touched. Non-fatal.
        supersede_categories = DETERMINISTIC_CATEGORIES | (
            SEMANTIC_CATEGORIES if semantic else frozenset()
        )
        try:
            n_superseded = await _supersede_prior_open_findings(
                vault_id=vault_id,
                current_run_id=run_id,
                categories=supersede_categories,
            )
            if n_superseded:
                logger.info(
                    "run_lint_scan: superseded %d stale open finding(s) from prior runs "
                    "(vault=%s, run_id=%s)",
                    n_superseded,
                    vault_id,
                    run_id,
                )
        except Exception as sup_exc:  # noqa: BLE001 — never fail the scan on cleanup
            logger.warning(
                "run_lint_scan: supersede of prior findings failed (non-fatal): %s", sup_exc
            )

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
        if total_cost_usd > COST_ANOMALY_THRESHOLD_USD:
            logger.warning(
                "COST ANOMALY: lint_scan run_id=%s total_cost_usd=%.4f exceeds $%.2f "
                "(vault=%r) — investigate runaway/misconfiguration",
                run_id,
                total_cost_usd,
                COST_ANOMALY_THRESHOLD_USD,
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

    Dispatch is via the category → handler registry (``fixes._APPLY_HANDLERS``), replacing the
    former if/elif chain (BE-REFAC-2):
      missing-xref  — reuse the wikilink-enrichment seam (ops/enrich_wikilinks.py) to add the
                      [[target]] link into the referencing page's BODY (I5 atomic, K7-valid).
      missing-page  — delegate to the lazy-generation seam used by review.create_page_from_review.
      contradiction — AUTHOR a genuine open-question `type=query` page (ADR-0067 D4): question
                      title + ## Question/## Hypothesis/## Open Points/## Impact/## References,
                      related[]=both conflicting pages, DB sources[]=union of both. Bounded
                      provider call (I6/I7) with a deterministic template fallback.
      orphan-page / stale-claim — FLAG-ONLY: status→applied + resolution_note
                      (no deterministic safe fix; the human edits the wiki — ADR-0037 §5).

    Raises:
      HTTPException(404) — finding not found.
      HTTPException(409) — finding not 'open', or (for fixable categories) no ingest provider.
      HTTPException(502) — the bounded fix failed; finding left 'open' (retry or dismiss).
    """
    from fastapi import HTTPException
    from sqlalchemy import select

    from app.db import get_session

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
    if category in FLAG_ONLY_CATEGORIES:
        note = (
            f"{category}: flag-only — no automatic fix is safe; resolved by acknowledgement. "
            "Edit the affected wiki page(s) to address the finding."
        )
        return await _set_finding_status(finding_id, "applied", resolution_note=note)

    # ── Registered categories — dispatch through the handler registry ────────────
    handler = _APPLY_HANDLERS.get(category)
    if handler is not None:
        note = await handler(finding)
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


async def send_finding_to_review(finding_id: uuid.UUID) -> LintFinding:
    """
    Bridge a lint finding into the F9 HITL review queue (L6 / ADR-0037 B1).

    Maps category → item_type (see CATEGORY_TO_ITEM_TYPE), enqueues the review item,
    then sets finding status → 'applied' with resolution_note = "sent to review (<id>)".

    DEDUP CONTRACT (ADR-0044 §3.2 / R7 parity fix):
      The review content_key is FNV-1a over (vault_id, item_type, normalize(proposed_title))
      ONLY — the finding category is NOT part of the key (see review._content_key). So a
      broken-wikilink finding mapped to item_type 'missing-page' and a genuine missing-page
      suggestion with the SAME title INTENTIONALLY collapse into one review row (this mirrors
      llm_wiki's normalizeReviewTitle keying on type+title). 'confirm' items get content_key
      = NULL and are never deduped.

    Raises:
      HTTPException(404) — finding not found.
      HTTPException(409) — finding not open.
    """
    from fastapi import HTTPException
    from sqlalchemy import select

    from app.db import get_session
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
    item_type = CATEGORY_TO_ITEM_TYPE.get(category, "suggestion")

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
        proposal_origin="lint",
        proposed_title=proposed_title,
        rationale=rationale,
        source_page_id=(uuid.UUID(str(finding.target_page_id)) if finding.target_page_id else None),
    )

    note = f"sent to review ({review_item.id})"
    return await _set_finding_status(finding_id, "applied", resolution_note=note)


async def apply_batch(
    finding_ids: list[uuid.UUID],
    action: str,
) -> BatchFindingsResponse:
    """
    Apply *action* to each finding in *finding_ids* sequentially (L5 / ADR-0037 B1).

    Actions: "apply" | "dismiss" | "send-to-review"
    Cap: len(finding_ids) ≤ BATCH_MAX_IDS (I7 — bounded; caller validates before calling).
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
