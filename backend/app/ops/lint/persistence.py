"""
lint_findings / lint_runs persistence — reads + writes (ADR-0037 §3.2/§6).

Owns the lint_runs row lifecycle (INSERT running → terminal write in run_lint_scan's
``finally``), lint_findings INSERT/UPDATE, the category-aware supersede sweep (llm_wiki
fresh-recompute parity), and the paginated GET /lint/findings + GET /lint/runs reads.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CursorResult, func, select, update

from app.db import get_session
from app.models import LintFinding, LintRun
from app.ops.lint._shared import (
    VALID_CATEGORIES,
    VALID_SEVERITIES,
    FindingDTO,
    LintFindingsPage,
    LintRunsPage,
)

logger = logging.getLogger(__name__)


# ── lint_runs row lifecycle ──────────────────────────────────────────────────────


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


# ── lint_findings writes ──────────────────────────────────────────────────────────


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
            if f.category not in VALID_CATEGORIES:
                continue
            severity = f.severity if f.severity in VALID_SEVERITIES else "warning"
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


async def _supersede_prior_open_findings(
    *,
    vault_id: str,
    current_run_id: uuid.UUID,
    categories: frozenset[str],
) -> int:
    """
    Close (status='superseded') the prior runs' still-OPEN findings that THIS scan recomputed,
    so the queue reflects the fresh scan instead of accumulating (llm_wiki clearLintItems parity).

    Scope (safety):
      - Only OPEN findings are touched — human-`applied`/`dismissed`/already-`superseded` are
        preserved (their outcome is durable; a re-scan must not resurrect or re-close them).
      - Only findings in ``categories`` are touched — a deterministic-only scan (semantic=False)
        passes the deterministic set only, so it never closes semantic findings it did not
        re-check.
      - Only OTHER runs are touched (``lint_run_id != current_run_id``) — never this scan's own
        just-persisted rows.

    Returns the number of findings superseded. Never raises into the scan (caller wraps it).
    """
    if not categories:
        return 0
    async with get_session() as session:
        result = await session.execute(
            update(LintFinding)
            .where(
                LintFinding.vault_id == vault_id,
                LintFinding.status == "open",
                LintFinding.lint_run_id != str(current_run_id),
                LintFinding.category.in_(tuple(categories)),
            )
            .values(
                status="superseded",
                reviewed_at=datetime.now(UTC),
                resolution_note=f"superseded by lint run {current_run_id}",
            )
        )
        await session.commit()
        # session.execute(update(...)) returns a CursorResult (has rowcount); the base
        # Result[Any] type mypy infers does not declare it. Cast to read the affected-row count.
        return int(cast("CursorResult[Any]", result).rowcount or 0)


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
      category (any VALID_CATEGORIES value — L10).
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
