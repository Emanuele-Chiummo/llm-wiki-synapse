"""
Tests for DELETE /ingest/{run_id} endpoint (R13-3).

Covers:
  T-CI-001  cancel a QUEUED (pending) run → 200 {"status": "cancelled"}
  T-CI-002  cancel a RUNNING (active) run → 202 {"status": "cancelling"}
  T-CI-003  double-cancel (already terminal in queue) → 409
  T-CI-004  unknown run_id → 404
  T-CI-005  snapshot() returns non-null run_id for pending entries (R13-3 pre-issue)
  T-CI-006  cancel_pending() removes entry from _pending and _pending_by_run_id
  T-CI-007  cancel_pending() is idempotent (second call returns None)

All tests are either pure-unit (queue_manager) or use SQLite in-memory for the
endpoint tests. No live Postgres or Qdrant required.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from app.ingest.queue_manager import IngestQueueManager

# ── Queue manager unit tests ────────────────────────────────────────────────────


def make_manager() -> IngestQueueManager:
    return IngestQueueManager()


class TestPendingRunId:
    """T-CI-005: pending entries get a pre-issued run_id."""

    def test_admit_parks_entry_with_run_id(self) -> None:
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/a.md", "ingest")
        entry = mgr._pending["/vault/raw/sources/a.md"]
        assert entry.run_id is not None
        assert isinstance(entry.run_id, uuid.UUID)

    def test_pending_by_run_id_reverse_index_populated(self) -> None:
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/a.md", "ingest")
        entry = mgr._pending["/vault/raw/sources/a.md"]
        assert entry.run_id in mgr._pending_by_run_id
        assert mgr._pending_by_run_id[entry.run_id] == "/vault/raw/sources/a.md"

    def test_last_writer_wins_cleans_stale_run_id(self) -> None:
        """When admit() overwrites an existing pending entry, the old run_id is removed."""
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/a.md", "ingest")
        old_entry = mgr._pending["/vault/raw/sources/a.md"]
        old_run_id = old_entry.run_id
        # Second admit on the same path should evict the old run_id
        mgr.admit("/vault/raw/sources/a.md", "delete")
        assert old_run_id not in mgr._pending_by_run_id
        new_entry = mgr._pending["/vault/raw/sources/a.md"]
        assert new_entry.run_id in mgr._pending_by_run_id

    def test_snapshot_returns_run_id_for_pending(self) -> None:
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/a.md", "ingest")
        snap = mgr.snapshot()
        task = next(t for t in snap["tasks"] if t["status"] == "pending")
        assert task["run_id"] is not None
        # Should be a valid UUID string
        uuid.UUID(task["run_id"])

    def test_resume_clears_pending_by_run_id(self) -> None:
        mgr = make_manager()
        mock_handler = MagicMock()
        mgr.set_watcher_handler(mock_handler)
        mgr.pause()
        mgr.admit("/vault/raw/sources/a.md", "ingest")
        entry = mgr._pending["/vault/raw/sources/a.md"]
        run_id = entry.run_id
        mgr.resume()
        assert run_id not in mgr._pending_by_run_id
        assert len(mgr._pending_by_run_id) == 0

    def test_is_run_pending_true_when_pending(self) -> None:
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/a.md", "ingest")
        entry = mgr._pending["/vault/raw/sources/a.md"]
        assert mgr.is_run_pending(entry.run_id) is True

    def test_is_run_pending_false_for_unknown(self) -> None:
        mgr = make_manager()
        assert mgr.is_run_pending(uuid.uuid4()) is False


class TestCancelPending:
    """T-CI-006 / T-CI-007: cancel_pending() removes entry correctly."""

    def test_cancel_pending_removes_entry(self) -> None:
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/a.md", "ingest")
        entry = mgr._pending["/vault/raw/sources/a.md"]
        run_id = entry.run_id

        result = mgr.cancel_pending(run_id)
        assert result == "/vault/raw/sources/a.md"
        assert "/vault/raw/sources/a.md" not in mgr._pending
        assert run_id not in mgr._pending_by_run_id

    def test_cancel_pending_idempotent(self) -> None:
        """Second cancel_pending call returns None — entry already removed."""
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/a.md", "ingest")
        entry = mgr._pending["/vault/raw/sources/a.md"]
        run_id = entry.run_id

        mgr.cancel_pending(run_id)
        result = mgr.cancel_pending(run_id)
        assert result is None

    def test_cancel_pending_unknown_run_id_returns_none(self) -> None:
        mgr = make_manager()
        assert mgr.cancel_pending(uuid.uuid4()) is None


# ── HTTP endpoint tests ─────────────────────────────────────────────────────────

from sqlalchemy import (  # noqa: E402
    BigInteger,
    Column,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)
from sqlalchemy import text as sa_text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402


def _build_sqlite_meta() -> MetaData:
    """Build SQLite-compatible schema for endpoint tests."""
    meta = MetaData()

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
        Column("community", Integer, nullable=True),
        Column("pinned", Integer, nullable=False, server_default=sa_text("0")),
        Column("deleted_at", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("updated_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )

    Table(
        "vault_state",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False, unique=True),
        Column("data_version", Integer, nullable=False, default=0),
        Column("remote_mcp_enabled", Integer, nullable=False, server_default=sa_text("0")),
        Column("mcp_access_token_hash", Text, nullable=True),
        Column("mcp_allow_without_token", Integer, nullable=False, server_default=sa_text("0")),
        Column("clip_enabled_db", Integer, nullable=True),
        Column("clip_access_token", Text, nullable=True),
        Column("clip_allowed_origins_db", Text, nullable=True),
        Column("cli_oauth_token", Text, nullable=True),
        Column("cli_oauth_token_encrypted", LargeBinary, nullable=True),
        Column("web_search_api_keys_encrypted", LargeBinary, nullable=True),
        Column("searxng_url_db", Text, nullable=True),
        Column("searxng_categories_db", Text, nullable=True),
        Column("searxng_max_queries_db", Integer, nullable=True),
        Column("updated_at", Text, nullable=False),
    )

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
        Column("converged", Integer, nullable=False, default=0),
        Column("cost_anomaly", Integer, nullable=False, default=0),
        Column("started_at", Text, nullable=False),
        Column("finished_at", Text, nullable=False),
        Column("status", Text, nullable=False, server_default=sa_text("'completed'")),
        Column("pages_created", Integer, nullable=False, default=0),
        Column("error_message", Text, nullable=True),
        Column("source_path", Text, nullable=True),
        Column("retry_count", Integer, nullable=False, server_default=sa_text("0")),
    )

    Table(
        "app_config",
        meta,
        Column("key", String, primary_key=True),
        Column("value", Text, nullable=False),
        Column("updated_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )

    return meta


@pytest.fixture()
async def cancel_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """
    Test environment for DELETE /ingest/{run_id} endpoint tests.

    SQLite in-memory, lifespan bypassed, ingest_queue patched per-test.
    """
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")

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

    monkeypatch.setattr("app.db.get_session", patched_get_session)
    monkeypatch.setattr("app.main.get_session", patched_get_session)
    monkeypatch.setattr(
        "app.routers.ingest._m",
        type("_M", (), {"get_session": staticmethod(patched_get_session)})(),
    )

    from app.main import app
    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):  # type: ignore[override]
        yield

    app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

    yield {
        "app": app,
        "session_factory": session_factory,
        "get_session": patched_get_session,
    }


@pytest.fixture()
async def cancel_client(cancel_env: dict[str, Any]):  # type: ignore[return]
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(
        transport=ASGITransport(app=cancel_env["app"]),
        base_url="http://test",
    ) as client:
        yield client


class TestDeleteIngestRunQueued:
    """T-CI-001: cancel a QUEUED (pending) run → 200."""

    async def test_cancel_queued_run_returns_200(
        self,
        cancel_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Build a fresh manager with a pending entry
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/test.md", "ingest")
        entry = mgr._pending["/vault/raw/sources/test.md"]
        run_id = entry.run_id

        # Patch the singleton used by the router
        monkeypatch.setattr("app.routers.ingest.ingest_queue", mgr, raising=False)
        import app.ingest.queue_manager as qm_mod

        monkeypatch.setattr(qm_mod, "ingest_queue", mgr)

        resp = await cancel_client.delete(f"/ingest/{run_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cancelled"

    async def test_cancel_queued_removes_from_pending(
        self,
        cancel_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mgr = make_manager()
        mgr.pause()
        mgr.admit("/vault/raw/sources/test.md", "ingest")
        entry = mgr._pending["/vault/raw/sources/test.md"]
        run_id = entry.run_id

        monkeypatch.setattr("app.routers.ingest.ingest_queue", mgr, raising=False)
        import app.ingest.queue_manager as qm_mod

        monkeypatch.setattr(qm_mod, "ingest_queue", mgr)

        await cancel_client.delete(f"/ingest/{run_id}")
        assert "/vault/raw/sources/test.md" not in mgr._pending
        assert run_id not in mgr._pending_by_run_id


class TestDeleteIngestRunRunning:
    """T-CI-002: cancel a RUNNING (active) run → 202."""

    async def test_cancel_running_run_returns_202(
        self,
        cancel_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/running.md")

        monkeypatch.setattr("app.routers.ingest.ingest_queue", mgr, raising=False)
        import app.ingest.queue_manager as qm_mod

        monkeypatch.setattr(qm_mod, "ingest_queue", mgr)

        resp = await cancel_client.delete(f"/ingest/{run_id}")
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "cancelling"

    async def test_cancel_running_sets_cancel_event(
        self,
        cancel_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mgr = make_manager()
        run_id = uuid.uuid4()
        handle = mgr.open_run(run_id, "/vault/raw/sources/running.md")

        monkeypatch.setattr("app.routers.ingest.ingest_queue", mgr, raising=False)
        import app.ingest.queue_manager as qm_mod

        monkeypatch.setattr(qm_mod, "ingest_queue", mgr)

        await cancel_client.delete(f"/ingest/{run_id}")
        assert handle.cancel_event.is_set()
        assert handle.status == "cancelling"


class TestDeleteIngestRunDoubleCancel:
    """T-CI-003: double-cancel of an already-terminal run → 409."""

    async def test_double_cancel_terminal_in_memory_returns_409(
        self,
        cancel_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A run that has been finalized (moved to _recent_failed) → 409 on second cancel."""
        mgr = make_manager()
        run_id = uuid.uuid4()
        mgr.open_run(run_id, "/vault/raw/sources/done.md")
        # Finalize the run (moves it to _recent_failed)
        mgr.finalize(run_id, "failed", error="intentional test failure")

        monkeypatch.setattr("app.routers.ingest.ingest_queue", mgr, raising=False)
        import app.ingest.queue_manager as qm_mod

        monkeypatch.setattr(qm_mod, "ingest_queue", mgr)

        resp = await cancel_client.delete(f"/ingest/{run_id}")
        assert resp.status_code == 409

    async def test_double_cancel_terminal_in_db_returns_409(
        self,
        cancel_env: dict[str, Any],
        cancel_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A run that is in the DB with terminal status → 409."""
        mgr = make_manager()  # fresh manager — run not in memory
        monkeypatch.setattr("app.routers.ingest.ingest_queue", mgr, raising=False)
        import app.ingest.queue_manager as qm_mod

        monkeypatch.setattr(qm_mod, "ingest_queue", mgr)

        run_id = uuid.uuid4()
        # Insert a completed row into the DB
        async with cancel_env["get_session"]() as sess:
            now = datetime.now(UTC).isoformat()
            await sess.execute(
                sa_text(
                    "INSERT INTO ingest_runs "
                    "(id, vault_id, provider_name, provider_type, model_id, route, "
                    " max_iter_used, total_tokens, total_cost_usd, converged, cost_anomaly, "
                    " started_at, finished_at, status, pages_created, retry_count) "
                    "VALUES (:id, 'test-vault', 'test', 'api', 'test-model', 'orchestrated', "
                    "0, 0, 0, 0, 0, :now, :now, 'completed', 0, 0)"
                ),
                {"id": str(run_id), "now": now},
            )

        resp = await cancel_client.delete(f"/ingest/{run_id}")
        assert resp.status_code == 409


class TestDeleteIngestRunUnknown:
    """T-CI-004: unknown run_id → 404."""

    async def test_unknown_run_id_returns_404(
        self,
        cancel_client: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mgr = make_manager()
        monkeypatch.setattr("app.routers.ingest.ingest_queue", mgr, raising=False)
        import app.ingest.queue_manager as qm_mod

        monkeypatch.setattr(qm_mod, "ingest_queue", mgr)

        resp = await cancel_client.delete(f"/ingest/{uuid.uuid4()}")
        assert resp.status_code == 404
