"""
Cost aggregation endpoint (R9-1, AC-R9-1-1..AC-R9-1-6).

Endpoint:
  GET /costs/summary  — aggregated spend across all cost-bearing tables.

Shared helper (used by GET /stats/overview — AC-R12-1-3, ADR-0054 §5.1):
  get_monthly_cost_usd(session, vault_id, month_start, month_end) → float
  Extracts the current-month total from the 4 cost tables. Called by both
  GET /costs/summary and GET /stats/overview so NO duplicate SQL exists.

Sources of cost data (I7 — cost is logged per run/message on each table):
  ingest_runs.total_cost_usd   — per-file ingest runs
  messages.total_cost_usd      — per-chat-message cost (chat operation)
  deep_research_runs.total_cost_usd — deep-research loop runs
  lint_runs.total_cost_usd     — lint-fix loop runs

Aggregation strategy — Python-side grouping after a bounded date-filtered SELECT:
  The last-30-days window is applied as a WHERE clause so the result set stays small.
  Python aggregation is used instead of SQL GROUP BY for portability between SQLite
  (unit tests) and Postgres (runtime). SQLite and Postgres have different date/time
  function syntax (strftime vs date_trunc), and CAST(col AS TEXT) patterns differ in
  context. Aggregating in Python from a bounded SELECT is the safe portable approach
  (see project MEMORY: "Raw SQL: SQLite tests vs Postgres runtime").

by_provider decision:
  Only ingest_runs and messages carry a provider_type column. deep_research_runs and
  lint_runs do NOT have a provider_type column (the models show no such field). Adding
  a nullable provider_type migration is explicitly NOT required for this sprint item.
  Therefore by_provider is aggregated from ingest_runs + messages only, and the
  response includes a note_field documenting the omission. DR and lint costs appear in
  by_operation but NOT in by_provider — the caller must be aware of this partial coverage.

Invariants:
  I1 — read-only; no file mutations, no index re-scan.
  I2 — does NOT trigger graph recompute.
  I6 — zero InferenceProvider calls.
  I7 — bounded SELECT (date-filtered last 30 days for by_day; month filter for month total).

Config:
  COST_ALERT_THRESHOLD_USD — float (default 5.00). 0 = disabled. Defined in config.py.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import ChatMessage, DeepResearchRun, IngestRun, LintRun

logger = logging.getLogger(__name__)

router = APIRouter(tags=["costs"])


# ── Response schema helpers (dicts — no Pydantic to keep it surgical) ─────────


def _safe_float(val: Any) -> float:
    """Convert Decimal/None/float to float safely."""
    if val is None:
        return 0.0
    if isinstance(val, Decimal):
        return float(val)
    return float(val)


# ── Shared monthly-cost helper (ADR-0054 §5.1 / AC-R12-1-3) ──────────────────


async def get_monthly_cost_usd(
    session: AsyncSession,
    vault_id: str,
    month_start: datetime,
    month_end: datetime,
) -> float:
    """
    Return the total AI inference cost for *vault_id* in [month_start, month_end).

    Queries the same 4 cost tables as GET /costs/summary with an identical month filter
    so GET /stats/overview.monthly_cost_usd is ALWAYS identical to the current-month
    total returned by GET /costs/summary (AC-R12-1-3 — no duplicate SQL, I9 no reinvent).

    Portable: Python-side sum over bounded SELECTs (SQLite unit tests + Postgres runtime).
    Pure I/O — no InferenceProvider call (I1/I6).
    """
    from app.models import Conversation  # noqa: PLC0415

    # ── ingest_runs ──────────────────────────────────────────────────────────
    ingest_rows = (
        await session.execute(
            select(IngestRun.total_cost_usd).where(
                IngestRun.vault_id == vault_id,
                IngestRun.started_at >= month_start,
                IngestRun.started_at < month_end,
            )
        )
    ).all()

    # ── messages ─────────────────────────────────────────────────────────────
    msg_rows = (
        await session.execute(
            select(ChatMessage.total_cost_usd).where(
                ChatMessage.conversation_id.in_(
                    select(Conversation.id).where(
                        Conversation.vault_id == vault_id,
                        Conversation.deleted_at.is_(None),
                    )
                ),
                ChatMessage.role == "assistant",
                ChatMessage.created_at >= month_start,
                ChatMessage.created_at < month_end,
            )
        )
    ).all()

    # ── deep_research_runs ────────────────────────────────────────────────────
    dr_rows = (
        await session.execute(
            select(DeepResearchRun.total_cost_usd).where(
                DeepResearchRun.vault_id == vault_id,
                DeepResearchRun.started_at >= month_start,
                DeepResearchRun.started_at < month_end,
            )
        )
    ).all()

    # ── lint_runs ─────────────────────────────────────────────────────────────
    lint_rows = (
        await session.execute(
            select(LintRun.total_cost_usd).where(
                LintRun.vault_id == vault_id,
                LintRun.started_at >= month_start,
                LintRun.started_at < month_end,
            )
        )
    ).all()

    total = sum(
        _safe_float(r[0]) for rows in (ingest_rows, msg_rows, dr_rows, lint_rows) for r in rows
    )
    return round(total, 4)


# ── GET /costs/summary ────────────────────────────────────────────────────────


@router.get(
    "/costs/summary",
    summary="Cost & usage summary",
    description=(
        "Return aggregated AI inference costs across all cost-bearing tables "
        "(ingest_runs, messages, deep_research_runs, lint_runs) for the requested "
        "calendar month (default: current month). Includes by_operation, by_provider "
        "(ingest + chat only — see by_provider_note), by_day (last 30 days), "
        "monthly_total_usd, and threshold_alert. "
        "Query param ?month=YYYY-MM selects an alternative month."
    ),
    responses={
        200: {"description": "Cost summary"},
    },
)
async def get_costs_summary(
    month: str | None = Query(
        default=None,
        description=(
            "Calendar month to aggregate (YYYY-MM). " "Defaults to the current UTC month."
        ),
        pattern=r"^\d{4}-\d{2}$",
    ),
) -> JSONResponse:
    """
    GET /costs/summary — bounded aggregation over the 4 cost tables (AC-R9-1-1).

    Steps:
      1. Resolve the target month (param or current UTC month).
      2. Compute month_start / month_end boundaries.
      3. SELECT rows from each cost table filtered by started_at / created_at
         within [month_start, month_end) for monthly totals.
      4. SELECT rows from ingest_runs + messages + deep_research_runs + lint_runs
         in the last-30-days window for by_day.
      5. Aggregate in Python (portable — SQLite + Postgres).
      6. Build and return the JSON response.
    """
    from app.config_overrides import effective_float  # noqa: PLC0415

    vault_id = settings.vault_id
    threshold = effective_float("cost_alert_threshold_usd", settings.cost_alert_threshold_usd)

    # ── 1. Resolve target month ───────────────────────────────────────────────
    now_utc = datetime.now(tz=UTC)

    if month is not None:
        try:
            year, mon = (int(x) for x in month.split("-"))
            period_date = date(year, mon, 1)
        except (ValueError, TypeError):
            period_date = date(now_utc.year, now_utc.month, 1)
    else:
        period_date = date(now_utc.year, now_utc.month, 1)

    period_str = period_date.strftime("%Y-%m")

    # ── 2. Month boundaries (UTC) ─────────────────────────────────────────────
    month_start = datetime(period_date.year, period_date.month, 1, tzinfo=UTC)
    # First day of next month:
    if period_date.month == 12:
        month_end = datetime(period_date.year + 1, 1, 1, tzinfo=UTC)
    else:
        month_end = datetime(period_date.year, period_date.month + 1, 1, tzinfo=UTC)

    # ── 3. Last-30-days boundary for by_day ───────────────────────────────────
    day30_start = now_utc - timedelta(days=30)

    async with get_session() as session:
        # ── ingest_runs (month window) ────────────────────────────────────────
        ingest_month_rows: Sequence[Any] = (
            await session.execute(
                select(
                    IngestRun.started_at,
                    IngestRun.total_cost_usd,
                    IngestRun.provider_type,
                ).where(
                    IngestRun.vault_id == vault_id,
                    IngestRun.started_at >= month_start,
                    IngestRun.started_at < month_end,
                )
            )
        ).all()

        # ── messages (month window, assistant only — user/system cost = 0) ────
        # messages has no direct vault_id; filter via conversation.vault_id would
        # need a join. Messages store provider_type per row. We do a direct select
        # with a subquery to scope to this vault's conversations.
        from app.models import Conversation

        msg_month_rows: Sequence[Any] = (
            await session.execute(
                select(
                    ChatMessage.created_at,
                    ChatMessage.total_cost_usd,
                    ChatMessage.provider_type,
                ).where(
                    ChatMessage.conversation_id.in_(
                        select(Conversation.id).where(
                            Conversation.vault_id == vault_id,
                            Conversation.deleted_at.is_(None),
                        )
                    ),
                    ChatMessage.role == "assistant",
                    ChatMessage.created_at >= month_start,
                    ChatMessage.created_at < month_end,
                )
            )
        ).all()

        # ── deep_research_runs (month window) ─────────────────────────────────
        dr_month_rows: Sequence[Any] = (
            await session.execute(
                select(
                    DeepResearchRun.started_at,
                    DeepResearchRun.total_cost_usd,
                ).where(
                    DeepResearchRun.vault_id == vault_id,
                    DeepResearchRun.started_at >= month_start,
                    DeepResearchRun.started_at < month_end,
                )
            )
        ).all()

        # ── lint_runs (month window) ──────────────────────────────────────────
        lint_month_rows: Sequence[Any] = (
            await session.execute(
                select(
                    LintRun.started_at,
                    LintRun.total_cost_usd,
                ).where(
                    LintRun.vault_id == vault_id,
                    LintRun.started_at >= month_start,
                    LintRun.started_at < month_end,
                )
            )
        ).all()

        # ── last-30-days rows for by_day ──────────────────────────────────────
        ingest_30d: Sequence[Any] = (
            await session.execute(
                select(IngestRun.started_at, IngestRun.total_cost_usd).where(
                    IngestRun.vault_id == vault_id,
                    IngestRun.started_at >= day30_start,
                )
            )
        ).all()

        msg_30d: Sequence[Any] = (
            await session.execute(
                select(ChatMessage.created_at, ChatMessage.total_cost_usd).where(
                    ChatMessage.conversation_id.in_(
                        select(Conversation.id).where(
                            Conversation.vault_id == vault_id,
                            Conversation.deleted_at.is_(None),
                        )
                    ),
                    ChatMessage.role == "assistant",
                    ChatMessage.created_at >= day30_start,
                )
            )
        ).all()

        dr_30d: Sequence[Any] = (
            await session.execute(
                select(DeepResearchRun.started_at, DeepResearchRun.total_cost_usd).where(
                    DeepResearchRun.vault_id == vault_id,
                    DeepResearchRun.started_at >= day30_start,
                )
            )
        ).all()

        lint_30d: Sequence[Any] = (
            await session.execute(
                select(LintRun.started_at, LintRun.total_cost_usd).where(
                    LintRun.vault_id == vault_id,
                    LintRun.started_at >= day30_start,
                )
            )
        ).all()

    # ── 4. Python-side aggregation ─────────────────────────────────────────────

    # by_operation: {op_name: {total_usd, call_count}}
    op_totals: dict[str, dict[str, Any]] = {
        "ingest": {"total_usd": 0.0, "call_count": 0},
        "chat": {"total_usd": 0.0, "call_count": 0},
        "research": {"total_usd": 0.0, "call_count": 0},
        "lint": {"total_usd": 0.0, "call_count": 0},
    }
    for row in ingest_month_rows:
        op_totals["ingest"]["total_usd"] += _safe_float(row.total_cost_usd)
        op_totals["ingest"]["call_count"] += 1
    for row in msg_month_rows:
        op_totals["chat"]["total_usd"] += _safe_float(row.total_cost_usd)
        op_totals["chat"]["call_count"] += 1
    for row in dr_month_rows:
        op_totals["research"]["total_usd"] += _safe_float(row.total_cost_usd)
        op_totals["research"]["call_count"] += 1
    for row in lint_month_rows:
        op_totals["lint"]["total_usd"] += _safe_float(row.total_cost_usd)
        op_totals["lint"]["call_count"] += 1

    by_operation = [
        {
            "operation": op,
            "total_usd": round(data["total_usd"], 4),
            "call_count": data["call_count"],
        }
        for op, data in op_totals.items()
    ]

    # by_provider: only ingest_runs + messages carry provider_type (see module docstring)
    prov_totals: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"total_usd": 0.0, "call_count": 0}
    )
    for row in ingest_month_rows:
        ptype = row.provider_type or "unknown"
        prov_totals[ptype]["total_usd"] += _safe_float(row.total_cost_usd)
        prov_totals[ptype]["call_count"] += 1
    for row in msg_month_rows:
        ptype = row.provider_type or "unknown"
        prov_totals[ptype]["total_usd"] += _safe_float(row.total_cost_usd)
        prov_totals[ptype]["call_count"] += 1

    by_provider = [
        {
            "provider": ptype,
            "total_usd": round(data["total_usd"], 4),
            "call_count": data["call_count"],
        }
        for ptype, data in prov_totals.items()
    ]

    # by_day: last 30 days; date string YYYY-MM-DD keyed
    day_totals: dict[str, float] = defaultdict(float)

    def _extract_date(ts: Any) -> str:
        """Extract YYYY-MM-DD from a datetime or ISO string."""
        if isinstance(ts, str):
            # SQLite stores timestamps as ISO strings; take the date part
            return ts[:10]
        if hasattr(ts, "date"):
            return cast("str", ts.date().isoformat())
        return str(ts)[:10]

    for row in ingest_30d:
        day_totals[_extract_date(row.started_at)] += _safe_float(row.total_cost_usd)
    for row in msg_30d:
        day_totals[_extract_date(row.created_at)] += _safe_float(row.total_cost_usd)
    for row in dr_30d:
        day_totals[_extract_date(row.started_at)] += _safe_float(row.total_cost_usd)
    for row in lint_30d:
        day_totals[_extract_date(row.started_at)] += _safe_float(row.total_cost_usd)

    # Emit sorted, last-30-days only (already bounded by the query window)
    by_day = [{"date": d, "total_usd": round(v, 4)} for d, v in sorted(day_totals.items())]

    # monthly_total_usd: delegated to the shared helper so GET /stats/overview returns
    # the SAME value (AC-R12-1-3 / ADR-0054 §5.1 — no duplicate SQL).
    # The helper runs a fresh bounded SELECT within a new session (simple, portable).
    async with get_session() as helper_session:
        monthly_total = await get_monthly_cost_usd(helper_session, vault_id, month_start, month_end)

    # threshold_alert (AC-R9-1-2)
    threshold_alert: bool = False
    if threshold > 0.0:
        threshold_alert = monthly_total >= threshold

    payload: dict[str, Any] = {
        "period": period_str,
        "by_provider": by_provider,
        "by_provider_note": (
            "by_provider covers ingest_runs and messages only. "
            "deep_research_runs and lint_runs do not carry a provider_type column "
            "and are therefore not included in by_provider totals. "
            "Their costs appear in by_operation under 'research' and 'lint'."
        ),
        "by_operation": by_operation,
        "by_day": by_day,
        "monthly_total_usd": monthly_total,
        "threshold_usd": threshold,
        "threshold_alert": threshold_alert,
    }

    logger.info(
        "costs/summary: vault=%s period=%s monthly_total=$%.4f threshold_alert=%s",
        vault_id,
        period_str,
        monthly_total,
        threshold_alert,
    )

    return JSONResponse(content=payload)
