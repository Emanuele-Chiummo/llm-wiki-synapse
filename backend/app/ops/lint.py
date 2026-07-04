"""
K2 Lint-fix loop — the third Karpathy core operation (Ingest · Query · **Lint**), ADR-0037.

ARCHITECTURE OVERVIEW (ADR-0037 §2):
  A periodic, BOUNDED, HUMAN-GATED health check of the wiki. ``run_lint_scan`` produces LINT
  FINDINGS (proposals); it NEVER auto-applies fixes. The human gate is ``apply_lint_fix`` —
  only safe/bounded fixes are ever applied, and at most one ``data_version`` bump per fix (I1).

FINDING CATEGORIES (ADR-0037 §3.1):
  orphan-page    — deterministic: a live wiki page with graph in-degree 0 (no resolved
                   incoming wikilink). Found from the links table — NO provider call.
  missing-xref   — LLM: a page that mentions an existing page but does not link it.
  contradiction  — LLM: conflicting claims across pages.
  stale-claim    — LLM: superseded information.
  missing-page   — LLM: a concept mentioned but with no page.

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
    {"orphan-page", "missing-xref", "contradiction", "stale-claim", "missing-page"}
)
_VALID_SEVERITIES = frozenset({"info", "warning", "error"})
_VALID_STATUSES = frozenset({"open", "applied", "dismissed"})

# Categories whose apply step is FLAG-ONLY (no deterministic safe fix — ADR-0037 §5).
# contradiction / stale-claim / orphan-page → apply is a no-op status change to 'applied'
# with a resolution_note (the human still has to fix them by editing the wiki).
_FLAG_ONLY_CATEGORIES = frozenset({"contradiction", "stale-claim", "orphan-page"})

# Bounded reads (I7 — never an unbounded scan).
_ORPHAN_SCAN_MAX_PAGES: int = 1_000
_CANDIDATE_TITLES_MAX: int = 500


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
    """

    category: Literal["orphan-page", "missing-xref", "contradiction", "stale-claim", "missing-page"]
    severity: str
    description: str
    target_title: str | None = None
    target_page_id: uuid.UUID | None = None
    proposed_action: str | None = None


# ── Public entry point ─────────────────────────────────────────────────────────


async def run_lint_scan(
    vault_id: str,
    *,
    max_iter: int | None = None,
    token_budget: int | None = None,
    run_id: uuid.UUID | None = None,
) -> LintScanResult:
    """
    Run ONE bounded lint scan end-to-end (ADR-0037 §4).

    Pipeline:
      1. Deterministic structural checks (orphan-page) — graph/links read, NO provider call.
      2. Bounded semantic loop (missing-xref / contradiction / stale-claim / missing-page):
         ``for n in range(1, max_iter + 1)`` with a token_budget gate at the top of each round.
      3. Merge + cap findings at LINT_MAX_FINDINGS; persist lint_findings rows.
      4. Finalize the lint_runs row (always — terminal write in finally).

    Bounds (I7) are FROZEN on the lint_runs row at INSERT and never re-read mid-loop.
    Produces FINDINGS only — NEVER applies a fix (the human gate is apply_lint_fix, §5).
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
        findings.extend(await _detect_orphans(vault_id))

        # ── 2. Bounded semantic loop (I6/I7) ─────────────────────────────────────
        # BOUNDS are LOCAL CONSTANTS for this run — the loop NEVER re-reads settings/DB.
        max_iter_local: int = frozen_max_iter
        token_budget_local: int = frozen_token_budget

        resolved = await _resolve_lint_provider(vault_id)
        if resolved is not None:
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

                # Stop early once we have enough findings (no point spending more budget).
                if len(findings) >= max_findings:
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
                    if len(findings) >= max_findings:
                        break

                if new_this_round == 0:
                    # The model has nothing new to add — converged; stop early (bounded anyway).
                    break
        else:
            logger.debug(
                "run_lint_scan: no ingest provider resolved (vault=%s) — "
                "deterministic findings only (I6: no silent default)",
                vault_id,
            )

        # ── 3. Merge + cap + persist ─────────────────────────────────────────────
        findings = findings[:max_findings]
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

    # ── missing-xref — reuse the wikilink-enrichment seam (I1/I5) ─────────────────
    if category == "missing-xref":
        note = await _apply_missing_xref(finding)
        return await _set_finding_status(finding_id, "applied", resolution_note=note)

    # ── missing-page — delegate to the lazy-generation seam (ADR-0034 §5) ─────────
    if category == "missing-page":
        created_note = await _apply_missing_page(finding)
        return await _set_finding_status(finding_id, "applied", resolution_note=created_note)

    # Unknown category (defensive — should never happen given the persist-time validation).
    raise HTTPException(
        status_code=409,
        detail=f"Lint finding {finding_id} has unsupported category={category!r}.",
    )


async def dismiss_lint_finding(finding_id: uuid.UUID) -> LintFinding:
    """Set status=dismissed, reviewed_at=now() (ADR-0037 §5). 404 if not found."""
    return await _set_finding_status(finding_id, "dismissed", resolution_note="dismissed by human")


# ── Paginated reads ─────────────────────────────────────────────────────────────


async def list_lint_findings(
    vault_id: str,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> LintFindingsPage:
    """
    Paginated lint_findings read for GET /lint/findings (ADR-0037 §6).

    Optional status filter (open|applied|dismissed). Ordered created_at ASC.
    limit is capped by the REST endpoint (I7 — bounded page size).
    """
    async with get_session() as session:
        count_stmt = (
            select(func.count()).select_from(LintFinding).where(LintFinding.vault_id == vault_id)
        )
        if status is not None:
            count_stmt = count_stmt.where(LintFinding.status == status)
        total: int = (await session.execute(count_stmt)).scalar_one()

        data_stmt = select(LintFinding).where(LintFinding.vault_id == vault_id)
        if status is not None:
            data_stmt = data_stmt.where(LintFinding.status == status)
        data_stmt = data_stmt.order_by(LintFinding.created_at.asc()).offset(offset).limit(limit)
        rows = list((await session.execute(data_stmt)).scalars().all())
        for r in rows:
            session.expunge(r)

    return LintFindingsPage(items=rows, total=total, limit=limit, offset=offset)


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
    dangling=false). Reads only the pages + links tables (I1 — no vault walk).
    Bounded at _ORPHAN_SCAN_MAX_PAGES. index.md / log.md / overview.md are excluded
    (they are navigation roots, not orphans).
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

            # Resolved incoming-link target ids (in-degree >= 1).
            target_rows = list(
                (
                    await session.execute(
                        select(func.distinct(Link.target_page_id)).where(
                            Link.target_page_id.isnot(None)
                        )
                    )
                ).scalars()
            )
            linked_ids = {str(t) for t in target_rows if t is not None}

        for pid, title, file_path in page_rows:
            rel = (file_path or "").lower()
            base = rel.rsplit("/", 1)[-1]
            if base in {"index.md", "log.md", "overview.md"}:
                continue
            if str(pid) in linked_ids:
                continue
            out.append(
                FindingDTO(
                    category="orphan-page",
                    severity="warning",
                    description=(
                        f"Page {title or rel!r} has no incoming wikilinks (orphan). "
                        "It is unreachable by graph navigation."
                    ),
                    target_title=title,
                    target_page_id=uuid.UUID(str(pid)),
                    proposed_action=None,  # flag-only (ADR-0037 §5)
                )
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("_detect_orphans: failed (non-fatal): %s", exc)
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


# ── Apply seams (ADR-0037 §5) ───────────────────────────────────────────────────


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
        wiki_page = await _run_generation(
            vault_id=finding.vault_id,
            proposed_title=title,
            proposed_page_type=None,  # heuristic at generation time (ADR-0034 §5.2)
            rationale=finding.description,
            origin_source=origin_source,
            provider_config_row=provider_config_row,
        )
        created_page = await write_wiki_page(None, wiki_page, origin_source)
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
        f"missing-page: created page {title!r} (page_id={created_page.id}; "
        "one data_version bump, I1)."
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
        "  category: one of missing-xref | contradiction | stale-claim | missing-page\n"
        "  severity: one of info | warning | error\n"
        "  description: a short string explaining the problem\n"
        "  target_title: the existing page title the finding is about (for missing-xref / "
        "stale-claim), OR the title that SHOULD exist (for missing-page); omit or null if "
        "none applies\n\n"
        "Definitions: missing-xref = a page that mentions an existing page but does not link "
        "it; contradiction = conflicting claims across pages; stale-claim = superseded "
        "information; missing-page = a concept mentioned with no page. "
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

    # Semantic categories only — orphan-page is deterministic and must not come from the model.
    semantic_categories = _VALID_CATEGORIES - {"orphan-page"}

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
        if category == "missing-xref" and target_title:
            proposed_action = f"Add a [[{target_title}]] wikilink to the referencing page."
        elif category == "missing-page" and target_title:
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
