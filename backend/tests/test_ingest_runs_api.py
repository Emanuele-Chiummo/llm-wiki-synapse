"""
GET /ingest/runs endpoint tests (ADR-0018 §7, AC-BE-IR-1..5).

Tests:
  T-IR-001  200 with empty list when no runs exist
  T-IR-002  response schema matches IngestRunListResponse contract (items/total/limit/offset)
  T-IR-003  items ordered by started_at DESC (AC-BE-IR-3)
  T-IR-004  limit/offset pagination — second page is correct
  T-IR-005  vault_id filter returns only matching rows (AC-BE-IR-2)
  T-IR-006  422 for limit=0 (AC-BE-IR-5)
  T-IR-007  422 for limit=101 (AC-BE-IR-5)
  T-IR-008  422 for negative offset (AC-BE-IR-5)
  T-IR-009  response fields alias correctly: iterations_used (not max_iter_used),
            completed_at (not finished_at)
  T-IR-010  completed_at is None when status='running'
  T-IR-011  total_cost_usd is a float (not Decimal/string)
  T-IR-012  all status values accepted (running/completed/failed/converged_false)

Uses SQLite in-memory (same GAP-4 pattern as test_api.py).
No live Postgres or Qdrant required.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import BigInteger, Boolean, Column, Float, Integer, MetaData, Numeric, String, Table, Text
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


# ── SQLite schema for ingest_runs tests ───────────────────────────────────────


def _build_sqlite_meta() -> MetaData:
    """Build SQLite-compatible schema matching Postgres tables we need."""
    meta = MetaData()

    # Minimal pages table (needed for FK reference — SQLite doesn't enforce FKs
    # so we just need the table to exist for insert compatibility)
    Table(
        "pages",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("file_path", Text, nullable=False),
        Column("title", Text, nullable=True),
        Column("type", Text, nullable=True),
        Column("sources", Text, nullable=True),
        Column("content_hash", String(64), nullable=False),
        Column("source_mtime_ns", BigInteger, nullable=True),
        Column("qdrant_point_id", String(36), nullable=True),
        Column("x", Float, nullable=True),
        Column("y", Float, nullable=True),
        Column("pinned", Integer, nullable=False, server_default=sa_text("0")),
        Column("deleted_at", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("updated_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )

    # vault_state (needed for GET /status which get_session might touch)
    Table(
        "vault_state",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False, unique=True),
        Column("data_version", Integer, nullable=False, default=0),
        Column("updated_at", Text, nullable=False),
    )

    # Full ingest_runs table including migration 0006 columns
    Table(
        "ingest_runs",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("page_id", String(36), nullable=True),
        Column("provider_name", Text, nullable=False),
        Column("provider_type", Text, nullable=False),
        Column("model_id", Text, nullable=False),
        Column("route", Text, nullable=False),
        Column("max_iter_used", Integer, nullable=False, default=0),
        Column("total_tokens", Integer, nullable=False, default=0),
        Column("total_cost_usd", Numeric(10, 4), nullable=False, default=0),
        Column("converged", Integer, nullable=False, default=0),  # SQLite has no bool
        Column("cost_anomaly", Integer, nullable=False, default=0),
        Column("started_at", Text, nullable=False),
        Column("finished_at", Text, nullable=False),
        # migration 0006 fields
        Column("status", Text, nullable=False, server_default=sa_text("'completed'")),
        Column("pages_created", Integer, nullable=False, default=0),
        Column("error_message", Text, nullable=True),
    )

    return meta


# ── Shared fixture ─────────────────────────────────────────────────────────────


@pytest.fixture()
async def ingest_runs_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Stand-alone test environment for GET /ingest/runs.

    SQLite in-memory with the ingest_runs schema (including 0006 columns).
    FastAPI lifespan bypassed. No Qdrant, no embedding service needed.
    """
    from app import config as cfg

    # Patch settings so the app doesn't try to hit real infra
    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")

    # SQLite engine
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    meta = _build_sqlite_meta()
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # Seed vault_state
    async with session_factory() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                "VALUES (:id, :vault_id, 0, datetime('now'))"
            ),
            {"id": str(uuid.uuid4()), "vault_id": "test-vault"},
        )
        await sess.commit()

    # Patch get_session everywhere it's used in main.py
    @asynccontextmanager
    async def patched_get_session():  # type: ignore[return]
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    monkeypatch.setattr("app.db.get_session", patched_get_session)
    monkeypatch.setattr("app.main.get_session", patched_get_session)

    from app.main import app
    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):  # type: ignore[override]
        yield

    app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

    yield {
        "app": app,
        "session_factory": session_factory,
    }


@pytest.fixture()
async def ingest_runs_client(ingest_runs_env: dict[str, Any]) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=ingest_runs_env["app"]),
        base_url="http://test",
    ) as client:
        yield client


# ── Helpers ────────────────────────────────────────────────────────────────────

_RUN_COUNTER = 0


async def _insert_run(
    env: dict[str, Any],
    *,
    vault_id: str = "test-vault",
    status: str = "completed",
    provider_type: str = "api",
    pages_created: int = 2,
    iter_used: int = 2,
    cost_usd: float = 0.0042,
    error_message: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> str:
    """Insert one synthetic ingest_runs row and return its id string."""
    global _RUN_COUNTER
    _RUN_COUNTER += 1
    run_id = str(uuid.uuid4())
    now_iso = started_at or datetime.now(UTC).isoformat()
    fin_iso = finished_at or datetime.now(UTC).isoformat()

    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO ingest_runs "
                "(id, vault_id, page_id, provider_name, provider_type, model_id, "
                " route, max_iter_used, total_tokens, total_cost_usd, converged, "
                " cost_anomaly, started_at, finished_at, status, pages_created, error_message) "
                "VALUES (:id, :vault_id, NULL, :provider_name, :provider_type, :model_id, "
                " :route, :max_iter_used, :total_tokens, :total_cost_usd, :converged, "
                " :cost_anomaly, :started_at, :finished_at, :status, :pages_created, :error_message)"
            ),
            {
                "id": run_id,
                "vault_id": vault_id,
                "provider_name": "ApiProvider",
                "provider_type": provider_type,
                "model_id": "claude-sonnet-4-6",
                "route": "orchestrated",
                "max_iter_used": iter_used,
                "total_tokens": 1000,
                "total_cost_usd": str(cost_usd),
                "converged": 1 if status == "completed" else 0,
                "cost_anomaly": 0,
                "started_at": now_iso,
                "finished_at": fin_iso,
                "status": status,
                "pages_created": pages_created,
                "error_message": error_message,
            },
        )
        await sess.commit()

    return run_id


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestIngestRunsEndpoint:
    """T-IR-001..012 — GET /ingest/runs contract (ADR-0018 §7, AC-BE-IR-1..5)"""

    async def test_empty_list_returns_200(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-001: 200 with empty items when no runs exist."""
        resp = await ingest_runs_client.get("/ingest/runs")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    async def test_response_schema(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-002: AC-BE-IR-1 — response has items/total/limit/offset keys."""
        await _insert_run(ingest_runs_env)
        resp = await ingest_runs_client.get("/ingest/runs")
        assert resp.status_code == 200
        data = resp.json()

        # Envelope keys
        assert "items" in data, "response must have 'items'"
        assert "total" in data, "response must have 'total'"
        assert "limit" in data, "response must have 'limit'"
        assert "offset" in data, "response must have 'offset'"

        # Item keys
        assert len(data["items"]) >= 1
        item = data["items"][0]
        required_keys = {
            "id", "vault_id", "status", "provider_type",
            "pages_created", "iterations_used", "total_cost_usd",
            "started_at", "completed_at", "error_message",
        }
        for key in required_keys:
            assert key in item, f"IngestRunResponse must have '{key}'; got keys: {list(item.keys())}"

    async def test_ordering_started_at_desc(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-003: AC-BE-IR-3 — items ordered by started_at DESC."""
        now = datetime.now(UTC)
        # Insert three runs with distinct started_at values
        t1 = (now - timedelta(hours=2)).isoformat()
        t2 = (now - timedelta(hours=1)).isoformat()
        t3 = now.isoformat()

        id1 = await _insert_run(ingest_runs_env, started_at=t1, finished_at=t1)
        id2 = await _insert_run(ingest_runs_env, started_at=t2, finished_at=t2)
        id3 = await _insert_run(ingest_runs_env, started_at=t3, finished_at=t3)

        resp = await ingest_runs_client.get("/ingest/runs")
        assert resp.status_code == 200
        items = resp.json()["items"]

        ids_in_order = [it["id"] for it in items]
        # Most recent (t3) should come first
        assert ids_in_order.index(id3) < ids_in_order.index(id2), (
            f"id3 (most recent) must come before id2; order was {ids_in_order}"
        )
        assert ids_in_order.index(id2) < ids_in_order.index(id1), (
            f"id2 must come before id1; order was {ids_in_order}"
        )

    async def test_pagination_second_page(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-004: pagination — limit=2&offset=2 returns the third row."""
        # Insert 3 rows
        for _ in range(3):
            await _insert_run(ingest_runs_env)

        resp_page1 = await ingest_runs_client.get("/ingest/runs?limit=2&offset=0")
        assert resp_page1.status_code == 200
        data1 = resp_page1.json()
        assert len(data1["items"]) == 2
        assert data1["limit"] == 2
        assert data1["offset"] == 0

        resp_page2 = await ingest_runs_client.get("/ingest/runs?limit=2&offset=2")
        assert resp_page2.status_code == 200
        data2 = resp_page2.json()
        assert len(data2["items"]) >= 1, "Second page must have at least one item"
        assert data2["offset"] == 2

        # IDs must be distinct across pages
        ids1 = {it["id"] for it in data1["items"]}
        ids2 = {it["id"] for it in data2["items"]}
        assert ids1.isdisjoint(ids2), "Pages must not share rows"

    async def test_vault_id_filter(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-005: AC-BE-IR-2 — vault_id filter returns only matching rows."""
        await _insert_run(ingest_runs_env, vault_id="test-vault")
        await _insert_run(ingest_runs_env, vault_id="other-vault")

        resp = await ingest_runs_client.get("/ingest/runs?vault_id=other-vault")
        assert resp.status_code == 200
        data = resp.json()
        for item in data["items"]:
            assert item["vault_id"] == "other-vault", (
                f"Filter vault_id=other-vault returned row with vault_id={item['vault_id']!r}"
            )

    async def test_422_for_limit_zero(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-006: AC-BE-IR-5 — limit=0 must return 422."""
        resp = await ingest_runs_client.get("/ingest/runs?limit=0")
        assert resp.status_code == 422, (
            f"limit=0 must return 422 (AC-BE-IR-5); got {resp.status_code}: {resp.text}"
        )

    async def test_422_for_limit_101(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-007: AC-BE-IR-5 — limit=101 must return 422."""
        resp = await ingest_runs_client.get("/ingest/runs?limit=101")
        assert resp.status_code == 422, (
            f"limit=101 must return 422 (AC-BE-IR-5); got {resp.status_code}: {resp.text}"
        )

    async def test_422_for_negative_offset(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-008: AC-BE-IR-5 — offset=-1 must return 422."""
        resp = await ingest_runs_client.get("/ingest/runs?offset=-1")
        assert resp.status_code == 422, (
            f"offset=-1 must return 422 (AC-BE-IR-5); got {resp.status_code}: {resp.text}"
        )

    async def test_field_aliases(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-009: ADR-0018 §7 — response uses iterations_used and completed_at (not max_iter_used/finished_at)."""
        await _insert_run(ingest_runs_env, iter_used=3, status="completed")

        resp = await ingest_runs_client.get("/ingest/runs?limit=1")
        assert resp.status_code == 200
        item = resp.json()["items"][0]

        assert "iterations_used" in item, (
            "Response must use alias 'iterations_used' (not max_iter_used)"
        )
        assert "completed_at" in item, (
            "Response must use alias 'completed_at' (not finished_at)"
        )
        assert "max_iter_used" not in item, "max_iter_used must NOT appear in response"
        assert "finished_at" not in item, "finished_at must NOT appear in response"

        assert item["iterations_used"] == 3, (
            f"iterations_used must reflect max_iter_used=3; got {item['iterations_used']}"
        )

    async def test_completed_at_none_for_running(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-010: completed_at must be null when status='running'."""
        await _insert_run(ingest_runs_env, status="running")

        resp = await ingest_runs_client.get("/ingest/runs?limit=1")
        assert resp.status_code == 200
        items = resp.json()["items"]

        running = [it for it in items if it["status"] == "running"]
        assert running, "Expected at least one running item"
        assert running[0]["completed_at"] is None, (
            f"completed_at must be null for running runs; got {running[0]['completed_at']!r}"
        )

    async def test_total_cost_usd_is_float(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-011: total_cost_usd must be a JSON number (not a string)."""
        await _insert_run(ingest_runs_env, cost_usd=0.0125)

        resp = await ingest_runs_client.get("/ingest/runs?limit=1")
        assert resp.status_code == 200
        item = resp.json()["items"][0]

        assert isinstance(item["total_cost_usd"], (int, float)), (
            f"total_cost_usd must be a number; got {type(item['total_cost_usd'])}: {item['total_cost_usd']!r}"
        )

    async def test_all_status_values_returned(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-012: all four status values (running/completed/failed/converged_false) can be returned."""
        for status in ("running", "completed", "failed", "converged_false"):
            await _insert_run(
                ingest_runs_env,
                status=status,
                error_message="test error" if status == "failed" else None,
            )

        resp = await ingest_runs_client.get("/ingest/runs?limit=100")
        assert resp.status_code == 200
        statuses_returned = {it["status"] for it in resp.json()["items"]}

        for expected_status in ("running", "completed", "failed", "converged_false"):
            assert expected_status in statuses_returned, (
                f"Status {expected_status!r} must be returned; got {statuses_returned}"
            )

    async def test_error_message_on_failed_run(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-013: failed run has error_message populated."""
        await _insert_run(
            ingest_runs_env,
            status="failed",
            error_message="InferenceProvider returned empty batch after max_iter",
        )

        resp = await ingest_runs_client.get("/ingest/runs?limit=100")
        assert resp.status_code == 200
        failed_items = [it for it in resp.json()["items"] if it["status"] == "failed"]
        assert failed_items, "Expected at least one failed item"
        assert failed_items[0]["error_message"] is not None, "failed run must have error_message"

    async def test_default_limit_and_offset_in_response(
        self,
        ingest_runs_client: AsyncClient,
        ingest_runs_env: dict[str, Any],
    ) -> None:
        """T-IR-014: default limit=20 and offset=0 reflected in response envelope."""
        resp = await ingest_runs_client.get("/ingest/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 20, f"Default limit must be 20; got {data['limit']}"
        assert data["offset"] == 0, f"Default offset must be 0; got {data['offset']}"
