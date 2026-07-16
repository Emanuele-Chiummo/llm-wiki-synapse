"""
Cost aggregation endpoint tests (R9-1, AC-R9-1-1..AC-R9-1-6).

Coverage:
  T-COST-001  summary shape — all required keys present (AC-R9-1-1)
  T-COST-002  by_operation math with seeded rows across 4 tables (AC-R9-1-1)
  T-COST-003  by_provider aggregation from ingest_runs + messages (AC-R9-1-1)
  T-COST-004  month calculation: only rows in the target month counted (AC-R9-1-1)
  T-COST-005  threshold_alert=true when monthly_total >= threshold (AC-R9-1-2)
  T-COST-006  threshold_alert=false when monthly_total < threshold (AC-R9-1-2)
  T-COST-007  threshold disabled when COST_ALERT_THRESHOLD_USD=0 (AC-R9-1-2)
  T-COST-008  empty DB → zeros, no crash (AC-R9-1-1)
  T-COST-009  ?month=YYYY-MM param filters to correct month (AC-R9-1-1)
  T-COST-010  by_day covers last-30-days window (AC-R9-1-1)

Database: SQLite in-memory (portability note — see project MEMORY).
No network, no Postgres, no Qdrant.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._db_fixtures import make_sqlite_engine

# ── Fixture helpers ────────────────────────────────────────────────────────────

VAULT_ID = "test-vault"

# A timestamp that is definitely within the current calendar month
_NOW = datetime.now(tz=UTC)
_THIS_MONTH_TS = datetime(_NOW.year, _NOW.month, 1, 12, 0, 0, tzinfo=UTC).isoformat()
# A timestamp from last month (outside the current month window)
_PREV_YEAR = _NOW.year if _NOW.month > 1 else _NOW.year - 1
_PREV_MONTH = _NOW.month - 1 if _NOW.month > 1 else 12
_LAST_MONTH_TS = datetime(_PREV_YEAR, _PREV_MONTH, 15, 12, 0, 0, tzinfo=UTC).isoformat()

# A timestamp within the last 30 days (for by_day)
_RECENT_TS = (_NOW - timedelta(days=5)).replace(hour=12, minute=0, second=0, microsecond=0)
_RECENT_TS_STR = _RECENT_TS.isoformat()
_RECENT_DATE = _RECENT_TS.date().isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


async def _seed_db(session: AsyncSession) -> None:
    """Seed 4 tables with test rows for the current month + some outside the month."""

    conv_id = _uid()
    await session.execute(
        sa_text(
            "INSERT INTO conversations (id, vault_id, title, created_at, updated_at) "
            "VALUES (:id, :vault_id, 'Test', :ts, :ts)"
        ),
        {"id": conv_id, "vault_id": VAULT_ID, "ts": _THIS_MONTH_TS},
    )

    # ingest_runs: 2 rows in current month, 1 row last month
    for cost, ptype in [(0.05, "api"), (0.03, "local")]:
        await session.execute(
            sa_text(
                "INSERT INTO ingest_runs "
                "(id, vault_id, provider_name, provider_type, model_id, route, "
                "total_cost_usd, started_at, finished_at, status) "
                "VALUES (:id, :vid, 'TestProvider', :pt, 'model', 'orchestrated', "
                ":cost, :ts, :ts, 'completed')"
            ),
            {
                "id": _uid(),
                "vid": VAULT_ID,
                "pt": ptype,
                "cost": cost,
                "ts": _THIS_MONTH_TS,
            },
        )
    # Last-month ingest (should be excluded from monthly total)
    await session.execute(
        sa_text(
            "INSERT INTO ingest_runs "
            "(id, vault_id, provider_name, provider_type, model_id, route, "
            "total_cost_usd, started_at, finished_at, status) "
            "VALUES (:id, :vid, 'TestProvider', 'api', 'model', 'orchestrated', "
            "99.99, :ts, :ts, 'completed')"
        ),
        {"id": _uid(), "vid": VAULT_ID, "ts": _LAST_MONTH_TS},
    )

    # messages: 2 assistant rows in current month with different providers
    for cost, ptype in [(0.02, "api"), (0.01, "cli")]:
        msg_id = _uid()
        await session.execute(
            sa_text(
                "INSERT INTO messages "
                "(id, conversation_id, role, content, provider_type, "
                "total_cost_usd, created_at) "
                "VALUES (:id, :cid, 'assistant', 'text', :pt, :cost, :ts)"
            ),
            {
                "id": msg_id,
                "cid": conv_id,
                "pt": ptype,
                "cost": cost,
                "ts": _THIS_MONTH_TS,
            },
        )
    # User message — should NOT be counted (role check)
    await session.execute(
        sa_text(
            "INSERT INTO messages "
            "(id, conversation_id, role, content, provider_type, "
            "total_cost_usd, created_at) "
            "VALUES (:id, :cid, 'user', 'hello', NULL, 0.0, :ts)"
        ),
        {"id": _uid(), "cid": conv_id, "ts": _THIS_MONTH_TS},
    )

    # deep_research_runs: 1 row, current month
    await session.execute(
        sa_text(
            "INSERT INTO deep_research_runs "
            "(id, vault_id, topic, status, max_iter, token_budget, iterations_used, "
            "queries_used, sources_fetched, converged, total_cost_usd, started_at) "
            "VALUES (:id, :vid, 'test topic', 'converged', 3, 10000, 1, '[]', 0, 1, :cost, :ts)"
        ),
        {"id": _uid(), "vid": VAULT_ID, "cost": 0.10, "ts": _THIS_MONTH_TS},
    )

    # lint_runs: 1 row, current month
    await session.execute(
        sa_text(
            "INSERT INTO lint_runs "
            "(id, vault_id, status, max_iter, token_budget, iterations_used, "
            "findings_count, total_cost_usd, started_at, created_at) "
            "VALUES (:id, :vid, 'completed', 3, 20000, 1, 0, :cost, :ts, :ts)"
        ),
        {"id": _uid(), "vid": VAULT_ID, "cost": 0.04, "ts": _THIS_MONTH_TS},
    )

    await session.commit()


async def _seed_recent(session: AsyncSession, conv_id: str) -> None:
    """Seed a recent (last 5 days) row for by_day tests."""
    await session.execute(
        sa_text(
            "INSERT INTO ingest_runs "
            "(id, vault_id, provider_name, provider_type, model_id, route, "
            "total_cost_usd, started_at, finished_at, status) "
            "VALUES (:id, :vid, 'TestProvider', 'api', 'model', 'orchestrated', "
            "0.07, :ts, :ts, 'completed')"
        ),
        {"id": _uid(), "vid": VAULT_ID, "ts": _RECENT_TS_STR},
    )
    await session.commit()


# ── Fixture: in-memory SQLite + test FastAPI app ───────────────────────────────


@pytest.fixture()
async def cost_env(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    """
    Build an in-memory SQLite DB, patch get_session and settings, and return an
    AsyncClient wired to the costs router.
    """
    engine = await make_sqlite_engine()

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )

    # Patch app.db.async_session_factory
    from app import config as app_cfg
    from app import db as app_db

    monkeypatch.setattr(app_db, "async_session_factory", session_factory)
    monkeypatch.setattr(app_cfg.settings, "vault_id", VAULT_ID)
    monkeypatch.setattr(app_cfg.settings, "cost_alert_threshold_usd", 5.00)

    # Build minimal FastAPI app with costs router
    from app.costs import router as costs_router

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield

    test_app = FastAPI(lifespan=lifespan)
    test_app.include_router(costs_router)

    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as client:
        yield {
            "client": client,
            "session_factory": session_factory,
            "settings": app_cfg.settings,
        }

    await engine.dispose()


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_summary_shape_empty_db(cost_env: dict[str, Any]) -> None:
    """T-COST-008 — empty DB returns zeros with correct shape."""
    client: AsyncClient = cost_env["client"]
    resp = await client.get("/costs/summary")
    assert resp.status_code == 200
    data = resp.json()

    # Required top-level keys (AC-R9-1-1)
    assert "period" in data
    assert "by_provider" in data
    assert "by_operation" in data
    assert "by_day" in data
    assert "monthly_total_usd" in data
    assert "threshold_usd" in data
    assert "threshold_alert" in data
    assert "by_provider_note" in data

    # Empty DB → all zeros
    assert data["monthly_total_usd"] == 0.0
    assert data["threshold_alert"] is False

    # by_operation must be a list of dicts with operation/total_usd/call_count
    for item in data["by_operation"]:
        assert "operation" in item
        assert "total_usd" in item
        assert "call_count" in item
        assert item["total_usd"] == 0.0
        assert item["call_count"] == 0


@pytest.mark.asyncio
async def test_by_operation_math(cost_env: dict[str, Any]) -> None:
    """T-COST-002 — by_operation totals match seeded rows across 4 tables."""
    sf = cost_env["session_factory"]
    async with sf() as session:
        await _seed_db(session)

    client: AsyncClient = cost_env["client"]
    resp = await client.get("/costs/summary")
    assert resp.status_code == 200
    data = resp.json()

    ops = {item["operation"]: item for item in data["by_operation"]}

    # ingest: 0.05 + 0.03 = 0.08  (last-month row excluded)
    assert abs(ops["ingest"]["total_usd"] - 0.08) < 1e-9
    assert ops["ingest"]["call_count"] == 2

    # chat: 0.02 + 0.01 = 0.03  (user message excluded, role='user')
    assert abs(ops["chat"]["total_usd"] - 0.03) < 1e-9
    assert ops["chat"]["call_count"] == 2

    # research: 0.10
    assert abs(ops["research"]["total_usd"] - 0.10) < 1e-9
    assert ops["research"]["call_count"] == 1

    # lint: 0.04
    assert abs(ops["lint"]["total_usd"] - 0.04) < 1e-9
    assert ops["lint"]["call_count"] == 1

    # monthly total: 0.08 + 0.03 + 0.10 + 0.04 = 0.25
    assert abs(data["monthly_total_usd"] - 0.25) < 1e-9


@pytest.mark.asyncio
async def test_by_provider_aggregation(cost_env: dict[str, Any]) -> None:
    """T-COST-003 — by_provider aggregates ingest+messages provider_type correctly."""
    sf = cost_env["session_factory"]
    async with sf() as session:
        await _seed_db(session)

    client: AsyncClient = cost_env["client"]
    resp = await client.get("/costs/summary")
    assert resp.status_code == 200
    data = resp.json()

    by_prov = {item["provider"]: item for item in data["by_provider"]}

    # api: ingest 0.05 + message 0.02 = 0.07
    assert "api" in by_prov
    assert abs(by_prov["api"]["total_usd"] - 0.07) < 1e-9
    assert by_prov["api"]["call_count"] == 2

    # local: ingest 0.03
    assert "local" in by_prov
    assert abs(by_prov["local"]["total_usd"] - 0.03) < 1e-9
    assert by_prov["local"]["call_count"] == 1

    # cli: message 0.01
    assert "cli" in by_prov
    assert abs(by_prov["cli"]["total_usd"] - 0.01) < 1e-9
    assert by_prov["cli"]["call_count"] == 1

    # by_provider_note must mention what's omitted
    note = data["by_provider_note"]
    assert "deep_research" in note.lower() or "research" in note.lower()
    assert "lint" in note.lower()


@pytest.mark.asyncio
async def test_month_calculation_excludes_other_months(cost_env: dict[str, Any]) -> None:
    """T-COST-004 — rows from last month are excluded from monthly total."""
    sf = cost_env["session_factory"]
    async with sf() as session:
        await _seed_db(session)

    client: AsyncClient = cost_env["client"]
    resp = await client.get("/costs/summary")
    data = resp.json()

    ops = {item["operation"]: item for item in data["by_operation"]}
    # Last-month ingest row (99.99) must NOT appear
    assert (
        ops["ingest"]["total_usd"] < 1.0
    ), f"Last-month row leaked into monthly total: ingest={ops['ingest']['total_usd']}"


@pytest.mark.asyncio
async def test_threshold_alert_true(
    cost_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-COST-005 — threshold_alert=true when monthly_total >= threshold."""
    sf = cost_env["session_factory"]
    async with sf() as session:
        await _seed_db(session)

    from app import config as app_cfg

    # Set threshold below the seeded monthly total (0.25)
    monkeypatch.setattr(app_cfg.settings, "cost_alert_threshold_usd", 0.10)

    client: AsyncClient = cost_env["client"]
    resp = await client.get("/costs/summary")
    data = resp.json()

    assert data["threshold_alert"] is True
    assert data["threshold_usd"] == pytest.approx(0.10)


@pytest.mark.asyncio
async def test_threshold_alert_false(
    cost_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-COST-006 — threshold_alert=false when monthly_total < threshold."""
    sf = cost_env["session_factory"]
    async with sf() as session:
        await _seed_db(session)

    from app import config as app_cfg

    # Set threshold well above the seeded monthly total (0.25)
    monkeypatch.setattr(app_cfg.settings, "cost_alert_threshold_usd", 100.00)

    client: AsyncClient = cost_env["client"]
    resp = await client.get("/costs/summary")
    data = resp.json()

    assert data["threshold_alert"] is False


@pytest.mark.asyncio
async def test_threshold_disabled_when_zero(
    cost_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T-COST-007 — threshold=0 means alert is always false (disabled)."""
    sf = cost_env["session_factory"]
    async with sf() as session:
        await _seed_db(session)

    from app import config as app_cfg

    monkeypatch.setattr(app_cfg.settings, "cost_alert_threshold_usd", 0.0)

    client: AsyncClient = cost_env["client"]
    resp = await client.get("/costs/summary")
    data = resp.json()

    assert data["threshold_alert"] is False


@pytest.mark.asyncio
async def test_month_param_filters_to_requested_month(
    cost_env: dict[str, Any],
) -> None:
    """T-COST-009 — ?month=YYYY-MM param correctly scopes aggregation."""
    sf = cost_env["session_factory"]
    async with sf() as session:
        await _seed_db(session)

    client: AsyncClient = cost_env["client"]

    # Request last month (which has 99.99 ingest cost but nothing else)
    last_month_param = f"{_PREV_YEAR}-{_PREV_MONTH:02d}"
    resp = await client.get(f"/costs/summary?month={last_month_param}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["period"] == last_month_param

    ops = {item["operation"]: item for item in data["by_operation"]}
    # The 99.99 row from last month should be included here
    assert abs(ops["ingest"]["total_usd"] - 99.99) < 1e-6
    assert ops["chat"]["total_usd"] == 0.0
    assert ops["research"]["total_usd"] == 0.0
    assert ops["lint"]["total_usd"] == 0.0

    # And the monthly total is just ingest
    assert abs(data["monthly_total_usd"] - 99.99) < 1e-6


@pytest.mark.asyncio
async def test_by_day_includes_recent_row(
    cost_env: dict[str, Any],
) -> None:
    """T-COST-010 — by_day covers last-30-days window and includes recent rows."""
    sf = cost_env["session_factory"]
    async with sf() as session:
        # Seed a recent row
        conv_id = _uid()
        await session.execute(
            sa_text(
                "INSERT INTO conversations (id, vault_id, title, created_at, updated_at) "
                "VALUES (:id, :vault_id, 'Test', :ts, :ts)"
            ),
            {"id": conv_id, "vault_id": VAULT_ID, "ts": _RECENT_TS_STR},
        )
        await _seed_recent(session, conv_id)

    client: AsyncClient = cost_env["client"]
    resp = await client.get("/costs/summary")
    assert resp.status_code == 200
    data = resp.json()

    by_day = data["by_day"]
    assert isinstance(by_day, list)

    # The recent date must appear
    day_dates = [item["date"] for item in by_day]
    assert _RECENT_DATE in day_dates, f"Expected {_RECENT_DATE!r} in by_day dates: {day_dates}"

    # Each item must have date + total_usd
    for item in by_day:
        assert "date" in item
        assert "total_usd" in item
        assert isinstance(item["total_usd"], float)


@pytest.mark.asyncio
async def test_summary_shape_all_keys_present_seeded(cost_env: dict[str, Any]) -> None:
    """T-COST-001 — full shape check with seeded data."""
    sf = cost_env["session_factory"]
    async with sf() as session:
        await _seed_db(session)

    client: AsyncClient = cost_env["client"]
    resp = await client.get("/costs/summary")
    assert resp.status_code == 200
    data = resp.json()

    required_keys = {
        "period",
        "by_provider",
        "by_provider_note",
        "by_operation",
        "by_day",
        "monthly_total_usd",
        "threshold_usd",
        "threshold_alert",
    }
    assert required_keys.issubset(data.keys()), f"Missing keys: {required_keys - data.keys()}"

    # period format YYYY-MM
    assert len(data["period"]) == 7
    assert data["period"][4] == "-"

    # by_operation must have all 4 operations
    op_names = {item["operation"] for item in data["by_operation"]}
    assert op_names == {"ingest", "chat", "research", "lint"}
