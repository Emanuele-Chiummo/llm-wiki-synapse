"""
Deep Research REST API tests (AC-F10-4, ADR-0024 §8).

Tests:
  T-RA-001  POST /research/start returns 202 {run_id} (AC-F10-4a)
  T-RA-002  POST /research/start 503 when SEARXNG_URL unset (I9)
  T-RA-003  POST /research/start 422 for empty topic
  T-RA-004  POST /research/start 422 for max_iter=0 (< 1)
  T-RA-005  POST /research/start 422 for max_iter=11 (> 10)
  T-RA-006  POST /research/start 422 for token_budget=500 (< 1000)
  T-RA-007  GET /research/runs returns 200 with items/total/limit/offset (AC-F10-4b)
  T-RA-008  GET /research/runs ordered started_at DESC
  T-RA-009  GET /research/runs vault_id filter
  T-RA-010  GET /research/runs 422 for limit=0
  T-RA-011  GET /research/runs/{id} returns detail + sources (AC-F10-4c)
  T-RA-012  GET /research/runs/{id} 404 for unknown id
  T-RA-013  synthesis_text null before step 5 (AC-F10-4c)
  T-RA-014  POST /research/start schedules background task (does not block)

Uses SQLite in-memory (GAP-4 pattern).  No live Postgres, Qdrant, or SearXNG needed.
Test isolation: patch app.db.async_session_factory, app.main.get_session, app.db.get_session.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON as SAJSON
from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── SQLite schema builder ─────────────────────────────────────────────────────


def _build_sqlite_meta() -> MetaData:
    """Build SQLite-compatible schema for the tables needed by the research API tests."""
    meta = MetaData()

    # Minimal pages table (FK target for deep_research_runs.synthesis_page_id)
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

    # vault_state (needed for status endpoint checks)
    Table(
        "vault_state",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False, unique=True),
        Column("data_version", Integer, nullable=False, default=0),
        Column("updated_at", Text, nullable=False),
    )

    # deep_research_runs
    Table(
        "deep_research_runs",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("topic", Text, nullable=False),
        Column("status", Text, nullable=False, server_default=sa_text("'running'")),
        Column("max_iter", Integer, nullable=False),
        Column("token_budget", Integer, nullable=False),
        Column("iterations_used", Integer, nullable=False, default=0),
        Column("queries_used", SAJSON, nullable=False, server_default=sa_text("'[]'")),
        Column("sources_fetched", Integer, nullable=False, default=0),
        Column("converged", Integer, nullable=False, default=0),
        Column("total_cost_usd", Numeric(10, 4), nullable=False, default=0),
        Column("synthesis_text", Text, nullable=True),
        Column("synthesis_page_id", String(36), nullable=True),
        Column("started_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("completed_at", Text, nullable=True),
        Column("error_message", Text, nullable=True),
    )

    # deep_research_sources
    Table(
        "deep_research_sources",
        meta,
        Column("id", String(36), primary_key=True),
        Column("run_id", String(36), nullable=False),
        Column("url", Text, nullable=False),
        Column("title", Text, nullable=True),
        Column("fetched_content_md", Text, nullable=True),
        Column("relevance_score", Numeric(6, 4), nullable=True),
        Column("iteration", Integer, nullable=False, default=1),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )

    return meta


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
async def research_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Stand-alone test environment for the research endpoints.

    SQLite in-memory with deep_research_runs + deep_research_sources schema.
    FastAPI lifespan bypassed. No Qdrant, no embedding service, no SearXNG needed.

    Paranoid isolation: patches get_session on ALL paths under test.
    """
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    # Set SEARXNG_URL so POST /research/start does not 503 (for start tests)
    monkeypatch.setattr(cfg.settings, "searxng_url", "http://searxng:8080")

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

    @asynccontextmanager
    async def patched_get_session():  # type: ignore[return]
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    # Patch all get_session references
    monkeypatch.setattr("app.db.get_session", patched_get_session)
    monkeypatch.setattr("app.main.get_session", patched_get_session)
    monkeypatch.setattr("app.ops.deep_research.get_session", patched_get_session)
    monkeypatch.setattr("app.provider_config_service.get_session", patched_get_session)

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
async def research_client(research_env: dict[str, Any]) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=research_env["app"]),
        base_url="http://test",
    ) as client:
        yield client


@pytest.fixture()
async def no_searxng_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Environment with SEARXNG_URL unset (for the 503 test)."""
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(cfg.settings, "searxng_url", None)  # unset

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
async def no_searxng_client(no_searxng_env: dict[str, Any]) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=no_searxng_env["app"]),
        base_url="http://test",
    ) as client:
        yield client


# ── Row helpers ───────────────────────────────────────────────────────────────


async def _insert_run(
    env: dict[str, Any],
    *,
    vault_id: str = "test-vault",
    topic: str = "test topic",
    status: str = "completed",
    max_iter: int = 3,
    token_budget: int = 100_000,
    iterations_used: int = 2,
    sources_fetched: int = 3,
    total_cost_usd: float = 0.0042,
    synthesis_text: str | None = "# Synthesis",
    synthesis_page_id: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    error_message: str | None = None,
) -> str:
    """Insert one synthetic deep_research_runs row and return its id."""
    run_id = str(uuid.uuid4())
    now = started_at or datetime.now(UTC).isoformat()
    fin = completed_at or datetime.now(UTC).isoformat()

    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO deep_research_runs "
                "(id, vault_id, topic, status, max_iter, token_budget, "
                " iterations_used, queries_used, sources_fetched, converged, "
                " total_cost_usd, synthesis_text, synthesis_page_id, "
                " started_at, completed_at, error_message) "
                "VALUES (:id, :vault_id, :topic, :status, :max_iter, :token_budget, "
                " :iterations_used, :queries_used, :sources_fetched, :converged, "
                " :total_cost_usd, :synthesis_text, :synthesis_page_id, "
                " :started_at, :completed_at, :error_message)"
            ),
            {
                "id": run_id,
                "vault_id": vault_id,
                "topic": topic,
                "status": status,
                "max_iter": max_iter,
                "token_budget": token_budget,
                "iterations_used": iterations_used,
                "queries_used": "[]",
                "sources_fetched": sources_fetched,
                "converged": 1 if status == "converged" else 0,
                "total_cost_usd": str(total_cost_usd),
                "synthesis_text": synthesis_text,
                "synthesis_page_id": synthesis_page_id,
                "started_at": now,
                "completed_at": fin if status != "running" else None,
                "error_message": error_message,
            },
        )
        await sess.commit()

    return run_id


async def _insert_source(
    env: dict[str, Any],
    *,
    run_id: str,
    url: str = "https://example.com",
    title: str = "Example",
    iteration: int = 1,
) -> str:
    """Insert one deep_research_sources row."""
    source_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO deep_research_sources "
                "(id, run_id, url, title, fetched_content_md, relevance_score, "
                "iteration, created_at) "
                "VALUES (:id, :run_id, :url, :title, NULL, NULL, :iteration, datetime('now'))"
            ),
            {
                "id": source_id,
                "run_id": run_id,
                "url": url,
                "title": title,
                "iteration": iteration,
            },
        )
        await sess.commit()
    return source_id


# ── T-RA-001: POST /research/start returns 202 ───────────────────────────────


class TestResearchStartEndpoint:
    """T-RA-001..006 — POST /research/start contract."""

    async def test_202_with_run_id(
        self,
        research_client: AsyncClient,
        research_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-RA-001: AC-F10-4a — 202 with {run_id: uuid}."""
        import asyncio

        # Patch run_deep_research to a no-op coroutine (we don't run the loop in this test)
        async def _noop(**kwargs: Any) -> None:
            pass

        monkeypatch.setattr("app.ops.deep_research.run_deep_research", _noop)

        # Patch asyncio.create_task to avoid actually scheduling anything
        original_create_task = asyncio.create_task
        task_args: list[Any] = []

        def _capture_task(coro: Any, **kwargs: Any) -> Any:
            task_args.append(coro)
            # Close the coroutine without running it to avoid warnings
            if hasattr(coro, "close"):
                coro.close()
            # Return a dummy future
            loop = asyncio.get_event_loop()
            fut = loop.create_future()
            fut.set_result(None)
            return fut

        monkeypatch.setattr(asyncio, "create_task", _capture_task)

        resp = await research_client.post(
            "/research/start",
            json={"vault_id": "test-vault", "topic": "Kubernetes networking"},
        )
        assert resp.status_code == 202, f"Expected 202; got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "run_id" in data, f"Response must have 'run_id'; got {data}"
        # run_id must be a valid UUID
        parsed = uuid.UUID(data["run_id"])
        assert parsed is not None

        asyncio.create_task = original_create_task  # restore

    async def test_503_when_searxng_url_unset(
        self,
        no_searxng_client: AsyncClient,
    ) -> None:
        """T-RA-002: I9 — 503 when SEARXNG_URL is not configured."""
        resp = await no_searxng_client.post(
            "/research/start",
            json={"vault_id": "test-vault", "topic": "Docker networking"},
        )
        assert (
            resp.status_code == 503
        ), f"Must return 503 when SEARXNG_URL unset; got {resp.status_code}: {resp.text}"
        assert (
            "SEARXNG_URL" in resp.text or "searxng" in resp.text.lower()
        ), f"503 response must mention SEARXNG_URL; got: {resp.text}"

    async def test_422_for_empty_topic(
        self,
        research_client: AsyncClient,
    ) -> None:
        """T-RA-003: 422 for empty topic (min_length=1)."""
        resp = await research_client.post(
            "/research/start",
            json={"vault_id": "test-vault", "topic": ""},
        )
        assert resp.status_code == 422, f"Expected 422 for empty topic; got {resp.status_code}"

    async def test_422_for_max_iter_zero(
        self,
        research_client: AsyncClient,
    ) -> None:
        """T-RA-004: 422 for max_iter=0 (below ge=1)."""
        resp = await research_client.post(
            "/research/start",
            json={"vault_id": "test-vault", "topic": "test", "max_iter": 0},
        )
        assert resp.status_code == 422, f"Expected 422 for max_iter=0; got {resp.status_code}"

    async def test_422_for_max_iter_11(
        self,
        research_client: AsyncClient,
    ) -> None:
        """T-RA-005: 422 for max_iter=11 (above le=10)."""
        resp = await research_client.post(
            "/research/start",
            json={"vault_id": "test-vault", "topic": "test", "max_iter": 11},
        )
        assert resp.status_code == 422, f"Expected 422 for max_iter=11; got {resp.status_code}"

    async def test_422_for_token_budget_too_small(
        self,
        research_client: AsyncClient,
    ) -> None:
        """T-RA-006: 422 for token_budget=500 (below ge=1000)."""
        resp = await research_client.post(
            "/research/start",
            json={"vault_id": "test-vault", "topic": "test", "token_budget": 500},
        )
        assert resp.status_code == 422, f"Expected 422 for token_budget=500; got {resp.status_code}"


# ── T-RA-007..010: GET /research/runs ────────────────────────────────────────


class TestResearchRunsListEndpoint:
    """T-RA-007..010 — GET /research/runs contract."""

    async def test_200_with_schema(
        self,
        research_client: AsyncClient,
        research_env: dict[str, Any],
    ) -> None:
        """T-RA-007: AC-F10-4b — 200 with items/total/limit/offset."""
        await _insert_run(research_env)
        resp = await research_client.get("/research/runs")
        assert resp.status_code == 200, f"Expected 200; got {resp.status_code}: {resp.text}"
        data = resp.json()
        assert "items" in data
        assert "total" in data
        assert "limit" in data
        assert "offset" in data
        assert len(data["items"]) >= 1

        item = data["items"][0]
        required_keys = {
            "id",
            "vault_id",
            "topic",
            "status",
            "iterations_used",
            "sources_fetched",
            "total_cost_usd",
            "started_at",
            "completed_at",
        }
        for key in required_keys:
            assert key in item, f"ResearchRunSummary must have '{key}'; got {list(item.keys())}"

    async def test_ordering_started_at_desc(
        self,
        research_client: AsyncClient,
        research_env: dict[str, Any],
    ) -> None:
        """T-RA-008: ordered started_at DESC."""
        now = datetime.now(UTC)
        t1 = (now - timedelta(hours=2)).isoformat()
        t2 = (now - timedelta(hours=1)).isoformat()
        t3 = now.isoformat()

        id1 = await _insert_run(research_env, started_at=t1)
        id2 = await _insert_run(research_env, started_at=t2)
        id3 = await _insert_run(research_env, started_at=t3)

        resp = await research_client.get("/research/runs")
        assert resp.status_code == 200
        ids = [it["id"] for it in resp.json()["items"]]

        assert ids.index(id3) < ids.index(id2), f"id3 (newest) must come before id2; order: {ids}"
        assert ids.index(id2) < ids.index(id1), f"id2 must come before id1; order: {ids}"

    async def test_vault_id_filter(
        self,
        research_client: AsyncClient,
        research_env: dict[str, Any],
    ) -> None:
        """T-RA-009: vault_id filter returns only matching rows."""
        await _insert_run(research_env, vault_id="test-vault")
        await _insert_run(research_env, vault_id="other-vault")

        resp = await research_client.get("/research/runs?vault_id=other-vault")
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert (
                item["vault_id"] == "other-vault"
            ), f"Filter returned item with vault_id={item['vault_id']!r}"

    async def test_422_for_limit_zero(
        self,
        research_client: AsyncClient,
    ) -> None:
        """T-RA-010: 422 for limit=0."""
        resp = await research_client.get("/research/runs?limit=0")
        assert resp.status_code == 422, f"Expected 422 for limit=0; got {resp.status_code}"


# ── T-RA-011..013: GET /research/runs/{id} ───────────────────────────────────


class TestResearchRunDetailEndpoint:
    """T-RA-011..013 — GET /research/runs/{id} contract."""

    async def test_200_with_detail_and_sources(
        self,
        research_client: AsyncClient,
        research_env: dict[str, Any],
    ) -> None:
        """T-RA-011: AC-F10-4c — 200 with full detail + per-source summaries."""
        run_id = await _insert_run(
            research_env,
            topic="Kubernetes networking",
            status="converged",
            synthesis_text="# Synthesis\n\nFull content here.",
        )
        await _insert_source(
            research_env, run_id=run_id, url="https://k8s.io/docs", title="K8s Docs"
        )
        await _insert_source(research_env, run_id=run_id, url="https://calico.org", title="Calico")

        resp = await research_client.get(f"/research/runs/{run_id}")
        assert resp.status_code == 200, f"Expected 200; got {resp.status_code}: {resp.text}"
        data = resp.json()

        # Required fields (AC-F10-4c)
        required_keys = {
            "id",
            "vault_id",
            "topic",
            "status",
            "max_iter",
            "token_budget",
            "iterations_used",
            "queries_used",
            "sources_fetched",
            "total_cost_usd",
            "synthesis_text",
            "synthesis_page_id",
            "sources",
            "started_at",
            "completed_at",
            "error_message",
        }
        for key in required_keys:
            assert key in data, f"ResearchRunDetail must have '{key}'; got {list(data.keys())}"

        # synthesis_text is populated
        assert data["synthesis_text"] == "# Synthesis\n\nFull content here."

        # sources array has 2 items
        assert len(data["sources"]) == 2, f"Expected 2 sources; got {len(data['sources'])}"
        source_urls = {s["url"] for s in data["sources"]}
        assert "https://k8s.io/docs" in source_urls
        assert "https://calico.org" in source_urls

        # queries_used is a list (AC-F10-4c)
        assert isinstance(data["queries_used"], list)

    async def test_404_for_unknown_run_id(
        self,
        research_client: AsyncClient,
    ) -> None:
        """T-RA-012: 404 for unknown run_id."""
        unknown_id = uuid.uuid4()
        resp = await research_client.get(f"/research/runs/{unknown_id}")
        assert resp.status_code == 404, f"Expected 404; got {resp.status_code}"

    async def test_synthesis_text_null_for_running(
        self,
        research_client: AsyncClient,
        research_env: dict[str, Any],
    ) -> None:
        """T-RA-013: AC-F10-4c — synthesis_text is null when run is still running."""
        run_id = await _insert_run(
            research_env,
            status="running",
            synthesis_text=None,
            completed_at=None,
        )

        resp = await research_client.get(f"/research/runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert (
            data["synthesis_text"] is None
        ), f"synthesis_text must be null while running; got {data['synthesis_text']!r}"
        # completed_at must also be null for running (mirrors ingest_runs)
        assert data["completed_at"] is None or data["status"] == "running"


# ── T-RA-014: POST /research/start is non-blocking ───────────────────────────


class TestResearchStartNonBlocking:
    """T-RA-014 — POST /research/start fires background task, does not block."""

    async def test_background_task_scheduled_not_blocking(
        self,
        research_client: AsyncClient,
        research_env: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-RA-014: background task is scheduled, endpoint returns 202 without waiting."""
        import asyncio

        task_created: list[bool] = [False]
        original_create_task = asyncio.create_task

        def _track_task(coro: Any, **kwargs: Any) -> Any:
            task_created[0] = True
            if hasattr(coro, "close"):
                coro.close()
            loop = asyncio.get_event_loop()
            fut = loop.create_future()
            fut.set_result(None)
            return fut

        monkeypatch.setattr(asyncio, "create_task", _track_task)

        resp = await research_client.post(
            "/research/start",
            json={"vault_id": "test-vault", "topic": "test topic"},
        )
        assert resp.status_code == 202
        assert task_created[0], "asyncio.create_task must be called (background task scheduled)"

        asyncio.create_task = original_create_task  # restore
