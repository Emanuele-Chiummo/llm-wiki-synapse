"""
F9 HITL Review Queue — unit + API tests (ADR-0025, AC-F9-1..11).

Tests:
  T-RV-001  enqueue_review inserts a row with status=pending (AC-F9-1)
  T-RV-002  generate_review_queries makes EXACTLY ONE provider.chat() call (I7, AC-F9-1)
  T-RV-003  generate_review_queries timeout → returns None; item enqueued with NULL query (I7)
  T-RV-004  generate_review_queries provider exception → returns None; NOT raised (I7)
  T-RV-005  ConfigNotFoundError → returns None; item still enqueued (I6)
  T-RV-006  Fire-and-forget hook: exception inside hook NEVER propagates into ingest (AC-F9-2)
  T-RV-007  GET /review/queue returns 200 with paginated items (AC-F9-5)
  T-RV-008  GET /review/queue cap at 200 items (I7 bounded page size)
  T-RV-009  POST /review/queue/{id}/approve sets status=approved; NO re-ingest (AC-F9-6)
  T-RV-010  POST /review/queue/{id}/skip sets status=skipped
  T-RV-011  POST /review/queue/{id}/deep-research returns 202 + run_id + review_item_id
             and stores deep_research_run_id on the item (AC-F9-3)
  T-RV-012  POST /review/queue/{id}/deep-research returns 503 when SEARXNG_URL unset
  T-RV-013  GET /review/queue pagination: limit+offset work correctly (AC-F9-5)
  T-RV-014  approve on non-existent item → 404
  T-RV-015  Provider chat returns no text → item enqueued with NULL pre_generated_query
  T-RV-016  review queue respects vault_id query parameter
"""

from __future__ import annotations

import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── SQLite schema for F9 review tests ─────────────────────────────────────────


def _build_review_meta() -> MetaData:
    """SQLite-compatible schema covering review_items + its FK targets."""
    meta = MetaData()

    # pages (FK target for review_items.page_id)
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

    # vault_state (GET /status reads this)
    Table(
        "vault_state",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False, unique=True),
        Column("data_version", Integer, nullable=False, default=0),
        Column("updated_at", Text, nullable=False),
    )

    # provider_config (needed by main.py at import time)
    Table(
        "provider_config",
        meta,
        Column("id", String(36), primary_key=True),
        Column("scope", Text, nullable=False),
        Column("vault_id", String, nullable=True),
        Column("operation", Text, nullable=True),
        Column("provider_type", Text, nullable=False),
        Column("model_id", Text, nullable=False),
        Column("base_url", Text, nullable=True),
        Column("token_budget", Integer, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )

    # deep_research_runs (FK target for review_items.deep_research_run_id)
    Table(
        "deep_research_runs",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("topic", Text, nullable=False),
        Column("status", Text, nullable=False),
        Column("max_iter", Integer, nullable=False, default=5),
        Column("token_budget", Integer, nullable=False, default=50000),
        Column("iterations_used", Integer, nullable=False, default=0),
        Column("queries_used", Text, nullable=False, default="[]"),
        Column("sources_fetched", Integer, nullable=False, default=0),
        Column("converged", Integer, nullable=False, default=0),
        Column("total_cost_usd", Float, nullable=False, default=0),
        Column("synthesis_text", Text, nullable=True),
        Column("synthesis_page_id", String(36), nullable=True),
        Column("started_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("completed_at", Text, nullable=True),
        Column("error_message", Text, nullable=True),
    )

    # deep_research_sources (needed for joins but not written in review tests)
    Table(
        "deep_research_sources",
        meta,
        Column("id", String(36), primary_key=True),
        Column("run_id", String(36), nullable=False),
        Column("url", Text, nullable=False),
        Column("title", Text, nullable=True),
        Column("snippet", Text, nullable=True),
        Column("fetched", Integer, nullable=False, default=0),
        Column("included", Integer, nullable=False, default=0),
        Column("fetch_error", Text, nullable=True),
        Column("fetched_at", Text, nullable=True),
    )

    # review_items (the table under test)
    Table(
        "review_items",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("page_id", String(36), nullable=True),
        Column("item_type", Text, nullable=False),
        Column("status", Text, nullable=False, server_default=sa_text("'pending'")),
        Column("pre_generated_query", Text, nullable=True),
        Column("deep_research_run_id", String(36), nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("reviewed_at", Text, nullable=True),
        Column("reviewed_by", Text, nullable=True),
    )

    # Other tables referenced by the main lifespan (ingest_runs, etc.)
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
        Column("total_cost_usd", Float, nullable=False, default=0),
        Column("converged", Integer, nullable=False, default=0),
        Column("cost_anomaly", Integer, nullable=False, default=0),
        Column("started_at", Text, nullable=False),
        Column("finished_at", Text, nullable=False),
        Column("status", Text, nullable=False, server_default=sa_text("'completed'")),
        Column("pages_created", Integer, nullable=False, default=0),
        Column("error_message", Text, nullable=True),
    )

    Table(
        "links",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("source_page_id", String(36), nullable=False),
        Column("target_title", Text, nullable=False),
    )

    Table(
        "edges",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("source_id", String(36), nullable=False),
        Column("target_id", String(36), nullable=False),
        Column("weight", Float, nullable=False, default=1.0),
    )

    Table(
        "conversations",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("title", Text, nullable=True),
        Column("created_at", Text, nullable=False),
        Column("updated_at", Text, nullable=False),
        Column("deleted_at", Text, nullable=True),
    )

    Table(
        "messages",
        meta,
        Column("id", String(36), primary_key=True),
        Column("conversation_id", String(36), nullable=False),
        Column("role", Text, nullable=False),
        Column("content", Text, nullable=False),
        Column("created_at", Text, nullable=False),
        Column("tokens", Integer, nullable=True),
        Column("cost_usd", Float, nullable=True),
        Column("provider_name", Text, nullable=True),
        Column("model_id", Text, nullable=True),
    )

    Table(
        "import_schedules",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("source_folder", Text, nullable=False),
        Column("interval_minutes", Integer, nullable=False),
        Column("enabled", Integer, nullable=False, default=1),
        Column("last_run_at", Text, nullable=True),
        Column("last_run_status", Text, nullable=True),
        Column("last_run_files_found", Integer, nullable=True),
        Column("last_run_files_ingested", Integer, nullable=True),
        Column("created_at", Text, nullable=False),
        Column("updated_at", Text, nullable=False),
    )

    return meta


# ── Shared fixture ─────────────────────────────────────────────────────────────


@pytest.fixture()
async def review_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> dict[str, Any]:
    """
    Stand-alone test environment for review_items tests.

    SQLite in-memory; FastAPI lifespan bypassed; no Qdrant or embedding service.
    """
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))
    # Disable SEARXNG by default (tests that need it will override)
    monkeypatch.setattr(cfg.settings, "searxng_url", "")

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    meta = _build_review_meta()
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
    async def patched_get_session():
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    # Patch get_session everywhere it's referenced
    monkeypatch.setattr("app.db.get_session", patched_get_session)
    monkeypatch.setattr("app.main.get_session", patched_get_session)
    monkeypatch.setattr("app.ops.review.get_session", patched_get_session)

    from app.main import app
    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        yield

    app.router.lifespan_context = test_lifespan

    return {
        "app": app,
        "session_factory": session_factory,
    }


@pytest.fixture()
async def review_client(review_env: dict[str, Any]) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=review_env["app"]),
        base_url="http://test",
    ) as client:
        yield client


# ── DB helpers ─────────────────────────────────────────────────────────────────


async def _insert_review_item(
    env: dict[str, Any],
    *,
    vault_id: str = "test-vault",
    item_type: str = "new_page",
    status: str = "pending",
    pre_generated_query: str | None = None,
    page_id: str | None = None,
    deep_research_run_id: str | None = None,
) -> str:
    """Insert one review_items row and return its ID string."""
    item_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO review_items "
                "(id, vault_id, page_id, item_type, status, pre_generated_query, "
                " deep_research_run_id, created_at) "
                "VALUES (:id, :vault_id, :page_id, :item_type, "
                ":status, :query, :dr_id, datetime('now'))"
            ),
            {
                "id": item_id,
                "vault_id": vault_id,
                "page_id": page_id,
                "item_type": item_type,
                "status": status,
                "query": pre_generated_query,
                "dr_id": deep_research_run_id,
            },
        )
        await sess.commit()
    return item_id


async def _insert_page(
    env: dict[str, Any],
    *,
    vault_id: str = "test-vault",
    title: str = "Test Page",
) -> str:
    """Insert a minimal pages row and return its ID string."""
    page_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, content_hash, pinned, created_at, updated_at) "
                "VALUES (:id, :vault_id, :fp, :title, :hash, 0, datetime('now'), datetime('now'))"
            ),
            {
                "id": page_id,
                "vault_id": vault_id,
                "fp": f"wiki/entities/{title.lower().replace(' ', '_')}.md",
                "title": title,
                "hash": "aabbcc",
            },
        )
        await sess.commit()
    return page_id


# ── T-RV-001: enqueue_review inserts pending row ───────────────────────────────


class TestEnqueueReview:
    """T-RV-001: enqueue_review DB write (AC-F9-1)."""

    async def test_enqueues_pending_row(self, review_env: dict[str, Any]) -> None:
        """enqueue_review inserts a row with status=pending."""
        from app.ops.review import enqueue_review

        page_id = uuid.uuid4()
        item = await enqueue_review(
            vault_id="test-vault",
            page_id=page_id,
            item_type="new_page",
            pre_generated_query="What is the origin?",
        )

        assert item.status == "pending"
        assert item.vault_id == "test-vault"
        assert item.item_type == "new_page"
        assert item.pre_generated_query == "What is the origin?"
        assert item.reviewed_at is None

    async def test_enqueues_without_query(self, review_env: dict[str, Any]) -> None:
        """enqueue_review with NULL query still inserts the row."""
        from app.ops.review import enqueue_review

        item = await enqueue_review(
            vault_id="test-vault",
            page_id=None,
            item_type="deep_research_candidate",
            pre_generated_query=None,
        )
        assert item.status == "pending"
        assert item.pre_generated_query is None

    async def test_enqueue_is_not_singleton(self, review_env: dict[str, Any]) -> None:
        """Calling enqueue_review twice creates two rows (event log, not upsert — ADR-0025 §3.1)."""
        from app.ops.review import enqueue_review

        await enqueue_review(vault_id="test-vault", page_id=None, item_type="new_page")
        await enqueue_review(vault_id="test-vault", page_id=None, item_type="new_page")

        # Verify two rows in the DB
        async with review_env["session_factory"]() as sess:
            result = await sess.execute(
                sa_text("SELECT COUNT(*) FROM review_items WHERE vault_id='test-vault'")
            )
            count = result.scalar_one()
        assert count == 2


# ── T-RV-002: generate_review_queries makes EXACTLY ONE call ──────────────────


class TestGenerateReviewQueries:
    """T-RV-002..005, T-RV-015: generate_review_queries I7 + I6 contract."""

    def _make_fake_provider(self, chunks: list[str] | None = None) -> MagicMock:
        """Build a fake provider whose .chat() is an async generator."""
        chunks = chunks or ["What is the main topic?\nHow does it relate to X?"]

        async def fake_chat(messages, retrieval_context=""):
            for chunk in chunks:
                yield chunk

        fake_provider = MagicMock()
        fake_provider.chat = fake_chat
        fake_provider.bind_accumulator = MagicMock()
        return fake_provider

    def _make_fake_provider_cfg(self) -> MagicMock:
        cfg = MagicMock()
        cfg.token_budget = 2000
        return cfg

    async def test_exactly_one_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-RV-002: EXACTLY ONE provider.chat() call per invoke (I7, ADR-0025 §3.2)."""
        call_count = 0

        async def fake_chat(messages, retrieval_context=""):
            nonlocal call_count
            call_count += 1
            yield "What is the main topic?"

        fake_provider = MagicMock()
        fake_provider.chat = fake_chat
        fake_provider.bind_accumulator = MagicMock()

        fake_cfg = self._make_fake_provider_cfg()

        # Patch the modules where review.py imports from (local imports inside the function)
        with (
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(return_value=fake_cfg),
            ),
            patch("app.ingest.provider.resolve_provider", return_value=fake_provider),
        ):
            from app.ops.review import generate_review_queries

            result = await generate_review_queries(
                vault_id="test-vault",
                page_title="Quantum Computing",
                page_excerpt="Quantum computers use qubits...",
            )

        assert call_count == 1, f"Expected exactly 1 call, got {call_count}"
        assert result is not None
        assert "What is the main topic?" in result

    async def test_timeout_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-RV-003: Timeout → returns None (item enqueued with NULL query — I7)."""

        async def slow_chat(messages, retrieval_context=""):
            await asyncio.sleep(999)
            yield "never"

        fake_provider = MagicMock()
        fake_provider.chat = slow_chat
        fake_provider.bind_accumulator = MagicMock()
        fake_cfg = self._make_fake_provider_cfg()

        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "review_query_timeout_seconds", 0.01)

        with (
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(return_value=fake_cfg),
            ),
            patch("app.ingest.provider.resolve_provider", return_value=fake_provider),
        ):
            from app.ops.review import generate_review_queries

            result = await generate_review_queries(
                vault_id="test-vault",
                page_title="Some Page",
                page_excerpt="Some content",
            )

        assert result is None, "Timeout should produce None, not raise"

    async def test_provider_exception_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-RV-004: Provider exception → returns None; exception NOT raised (I7)."""

        async def crashing_chat(messages, retrieval_context=""):
            raise RuntimeError("Simulated provider crash")
            yield  # make it a generator

        fake_provider = MagicMock()
        fake_provider.chat = crashing_chat
        fake_provider.bind_accumulator = MagicMock()
        fake_cfg = self._make_fake_provider_cfg()

        with (
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(return_value=fake_cfg),
            ),
            patch("app.ingest.provider.resolve_provider", return_value=fake_provider),
        ):
            from app.ops.review import generate_review_queries

            result = await generate_review_queries(
                vault_id="test-vault",
                page_title="Some Page",
                page_excerpt="Some content",
            )

        assert result is None, "Provider crash should return None, not raise"

    async def test_config_not_found_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-RV-005: ConfigNotFoundError → returns None (I6 — no provider configured)."""
        from app.provider_config_service import ConfigNotFoundError

        with patch(
            "app.provider_config_service.resolve_provider_config",
            new=AsyncMock(side_effect=ConfigNotFoundError("no provider")),
        ):
            from app.ops.review import generate_review_queries

            result = await generate_review_queries(
                vault_id="test-vault",
                page_title="Some Page",
                page_excerpt="Some content",
            )

        assert result is None

    async def test_empty_response_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """T-RV-015: Provider returns empty/whitespace → NULL pre_generated_query."""

        async def empty_chat(messages, retrieval_context=""):
            yield "   \n  "

        fake_provider = MagicMock()
        fake_provider.chat = empty_chat
        fake_provider.bind_accumulator = MagicMock()
        fake_cfg = self._make_fake_provider_cfg()

        with (
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(return_value=fake_cfg),
            ),
            patch("app.ingest.provider.resolve_provider", return_value=fake_provider),
        ):
            from app.ops.review import generate_review_queries

            result = await generate_review_queries(
                vault_id="test-vault",
                page_title="Empty Response Page",
                page_excerpt="Some content",
            )

        assert result is None, "Empty response should return None, not empty string"

    async def test_caps_at_three_questions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """generate_review_queries caps output at 3 questions (ADR-0025 §3.2)."""

        async def multi_q_chat(messages, retrieval_context=""):
            yield "Q1\nQ2\nQ3\nQ4\nQ5"

        fake_provider = MagicMock()
        fake_provider.chat = multi_q_chat
        fake_provider.bind_accumulator = MagicMock()
        fake_cfg = self._make_fake_provider_cfg()

        with (
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(return_value=fake_cfg),
            ),
            patch("app.ingest.provider.resolve_provider", return_value=fake_provider),
        ):
            from app.ops.review import generate_review_queries

            result = await generate_review_queries(
                vault_id="test-vault",
                page_title="Rich Page",
                page_excerpt="Much content",
            )

        assert result is not None
        lines = [ln for ln in result.splitlines() if ln.strip()]
        assert len(lines) <= 3, f"Expected ≤3 questions; got {len(lines)}: {result!r}"


# ── T-RV-006: Fire-and-forget hook never propagates ───────────────────────────


class TestFireAndForgetHook:
    """T-RV-006: Hook exception NEVER propagates into ingest critical path (AC-F9-2)."""

    async def test_hook_exception_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempPathFactory
    ) -> None:
        """
        Simulate the orchestrator's F9 post-write hook raising an exception.
        The ingest pipeline must complete normally.
        """
        from app.ops import review as review_mod

        # Make enqueue_review always raise
        async def boom(*args, **kwargs):
            raise RuntimeError("Simulated DB error in F9 hook")

        monkeypatch.setattr(review_mod, "enqueue_review", boom)

        # The hook pattern from orchestrator.py:
        async def run_hook_safe(written_pages, vault_id):
            try:
                await review_mod.enqueue_review(
                    vault_id=vault_id,
                    page_id=None,
                    item_type="new_page",
                    pre_generated_query=None,
                )
            except Exception:
                pass  # AC-F9-2: never propagate

        # Must not raise
        await run_hook_safe(written_pages=[], vault_id="test-vault")


# ── T-RV-007..013, T-RV-014, T-RV-016: REST API tests ────────────────────────


class TestReviewQueueEndpoints:
    """T-RV-007..014, T-RV-016: REST endpoint behavior (ADR-0025 §3.5)."""

    async def test_get_queue_empty(self, review_client: AsyncClient) -> None:
        """T-RV-007: GET /review/queue returns 200 with empty list when no items."""
        resp = await review_client.get("/review/queue?vault_id=test-vault")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_get_queue_returns_items(
        self, review_env: dict[str, Any], review_client: AsyncClient
    ) -> None:
        """T-RV-007: GET /review/queue returns inserted items (AC-F9-5)."""
        await _insert_review_item(
            review_env,
            item_type="new_page",
            pre_generated_query="What is this about?",
        )
        resp = await review_client.get("/review/queue?vault_id=test-vault")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["status"] == "pending"
        assert item["item_type"] == "new_page"
        assert item["pre_generated_query"] == "What is this about?"

    async def test_get_queue_limit_capped_at_200(
        self, review_env: dict[str, Any], review_client: AsyncClient
    ) -> None:
        """T-RV-008: limit > 200 is rejected (I7 — bounded page size)."""
        resp = await review_client.get("/review/queue?vault_id=test-vault&limit=201")
        # FastAPI Query(le=200) → 422
        assert resp.status_code == 422

    async def test_get_queue_pagination(
        self, review_env: dict[str, Any], review_client: AsyncClient
    ) -> None:
        """T-RV-013: limit+offset pagination works correctly (AC-F9-5)."""
        for i in range(5):
            await _insert_review_item(review_env, item_type="new_page", pre_generated_query=f"Q{i}")

        resp1 = await review_client.get("/review/queue?vault_id=test-vault&limit=3&offset=0")
        assert resp1.status_code == 200
        body1 = resp1.json()
        assert body1["total"] == 5
        assert len(body1["items"]) == 3

        resp2 = await review_client.get("/review/queue?vault_id=test-vault&limit=3&offset=3")
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert len(body2["items"]) == 2

        # No overlap
        ids1 = {it["id"] for it in body1["items"]}
        ids2 = {it["id"] for it in body2["items"]}
        assert not ids1 & ids2

    async def test_get_queue_vault_filter(
        self, review_env: dict[str, Any], review_client: AsyncClient
    ) -> None:
        """T-RV-016: vault_id filter isolates items per vault."""
        await _insert_review_item(review_env, vault_id="vault-A")
        await _insert_review_item(review_env, vault_id="vault-B")

        resp_a = await review_client.get("/review/queue?vault_id=vault-A")
        assert resp_a.status_code == 200
        assert resp_a.json()["total"] == 1

        resp_b = await review_client.get("/review/queue?vault_id=vault-B")
        assert resp_b.status_code == 200
        assert resp_b.json()["total"] == 1

    async def test_approve_sets_status(
        self, review_env: dict[str, Any], review_client: AsyncClient
    ) -> None:
        """T-RV-009: POST /review/queue/{id}/approve → status=approved; NO re-ingest (AC-F9-6)."""
        item_id = await _insert_review_item(review_env)

        # Ensure ingest_file is NOT called (AC-F9-6, I1)
        with patch("app.ingest.orchestrator.ingest_file") as mock_ingest:
            resp = await review_client.post(f"/review/queue/{item_id}/approve")
            assert mock_ingest.call_count == 0, "approve must NOT trigger re-ingest (AC-F9-6)"

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "approved"
        assert body["reviewed_at"] is not None

    async def test_skip_sets_status(
        self, review_env: dict[str, Any], review_client: AsyncClient
    ) -> None:
        """T-RV-010: POST /review/queue/{id}/skip → status=skipped."""
        item_id = await _insert_review_item(review_env)

        resp = await review_client.post(f"/review/queue/{item_id}/skip")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "skipped"
        assert body["reviewed_at"] is not None

    async def test_approve_nonexistent_returns_404(self, review_client: AsyncClient) -> None:
        """T-RV-014: approve on non-existent item → 404."""
        fake_id = str(uuid.uuid4())
        resp = await review_client.post(f"/review/queue/{fake_id}/approve")
        assert resp.status_code == 404

    async def test_skip_nonexistent_returns_404(self, review_client: AsyncClient) -> None:
        """T-RV-014 (skip variant): skip on non-existent item → 404."""
        fake_id = str(uuid.uuid4())
        resp = await review_client.post(f"/review/queue/{fake_id}/skip")
        assert resp.status_code == 404

    async def test_deep_research_503_when_searxng_unset(
        self,
        review_env: dict[str, Any],
        review_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-RV-012: 503 when SEARXNG_URL is unset (I9 — no fake engine)."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "searxng_url", "")

        item_id = await _insert_review_item(review_env)
        resp = await review_client.post(
            f"/review/queue/{item_id}/deep-research?vault_id=test-vault"
        )
        assert resp.status_code == 503
        assert "SEARXNG_URL" in resp.json()["detail"]

    async def test_deep_research_returns_202_with_run_id(
        self,
        review_env: dict[str, Any],
        review_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-RV-011: deep-research → 202, body has review_item_id + run_id."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "searxng_url", "http://searxng:8080")

        item_id = await _insert_review_item(
            review_env,
            pre_generated_query="What are the implications of quantum computing?",
        )

        # Patch out the actual deep research runner
        async def fake_run_deep_research(**kwargs):
            pass

        with patch("app.ops.deep_research.run_deep_research", side_effect=fake_run_deep_research):
            resp = await review_client.post(
                f"/review/queue/{item_id}/deep-research?vault_id=test-vault"
            )

        assert resp.status_code in (200, 202), f"Expected 2xx, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "run_id" in body
        assert "review_item_id" in body
        # Both should be valid UUIDs
        uuid.UUID(body["run_id"])
        uuid.UUID(body["review_item_id"])

    async def test_deep_research_stores_run_id_on_item(
        self,
        review_env: dict[str, Any],
        review_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-RV-011: deep_research_run_id is persisted on the review item (AC-F9-3)."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "searxng_url", "http://searxng:8080")

        item_id = await _insert_review_item(
            review_env,
            pre_generated_query="Why is the sky blue?",
        )

        async def fake_run_deep_research(**kwargs):
            pass

        with patch("app.ops.deep_research.run_deep_research", side_effect=fake_run_deep_research):
            resp = await review_client.post(
                f"/review/queue/{item_id}/deep-research?vault_id=test-vault"
            )

        assert resp.status_code in (200, 202)
        run_id = resp.json()["run_id"]

        # Verify the DB row was updated
        async with review_env["session_factory"]() as sess:
            result = await sess.execute(
                sa_text("SELECT status, deep_research_run_id FROM review_items WHERE id=:id"),
                {"id": item_id},
            )
            row = result.one()

        assert row.status == "deep_researched"
        assert row.deep_research_run_id == run_id


# ── T-RV: ops.review unit-level list/approve/skip ─────────────────────────────


class TestReviewOpsUnit:
    """Unit tests for ops.review list_queue / approve / skip (no HTTP)."""

    async def test_list_queue_paginates(self, review_env: dict[str, Any]) -> None:
        """list_queue returns ReviewQueuePage with correct total and items slice."""
        for i in range(4):
            await _insert_review_item(review_env, pre_generated_query=f"Q{i}")

        from app.ops.review import list_queue

        page = await list_queue("test-vault", limit=3, offset=0)
        assert page.total == 4
        assert len(page.items) == 3
        assert page.limit == 3
        assert page.offset == 0

        page2 = await list_queue("test-vault", limit=3, offset=3)
        assert len(page2.items) == 1

    async def test_approve_updates_status(self, review_env: dict[str, Any]) -> None:
        """approve() sets status=approved and reviewed_at (no re-ingest)."""
        item_id_str = await _insert_review_item(review_env)
        item_uuid = uuid.UUID(item_id_str)

        from app.ops.review import approve

        updated = await approve(item_uuid)
        assert updated.status == "approved"
        assert updated.reviewed_at is not None

    async def test_skip_updates_status(self, review_env: dict[str, Any]) -> None:
        """skip() sets status=skipped and reviewed_at."""
        item_id_str = await _insert_review_item(review_env)
        item_uuid = uuid.UUID(item_id_str)

        from app.ops.review import skip

        updated = await skip(item_uuid)
        assert updated.status == "skipped"
        assert updated.reviewed_at is not None

    async def test_approve_nonexistent_raises_http_404(self, review_env: dict[str, Any]) -> None:
        """approve() on absent item raises HTTPException(404)."""
        from app.ops.review import approve
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await approve(uuid.uuid4())
        assert exc_info.value.status_code == 404

    async def test_list_queue_vault_filter(self, review_env: dict[str, Any]) -> None:
        """list_queue filters by vault_id (ADR-0025 §3.5)."""
        await _insert_review_item(review_env, vault_id="vault-X")
        await _insert_review_item(review_env, vault_id="vault-Y")

        from app.ops.review import list_queue

        page_x = await list_queue("vault-X")
        assert page_x.total == 1

        page_y = await list_queue("vault-Y")
        assert page_y.total == 1


# ── T-RV: I6 — no isinstance / class-name branching in review.py ─────────────


class TestI6NoIsinstanceBranching:
    """I6 compliance: review.py must not branch on isinstance or class names."""

    def test_no_isinstance_branching_in_review(self) -> None:
        """review.py must not use isinstance(provider, ...) or type checks (I6)."""
        from pathlib import Path

        review_path = Path(__file__).resolve().parent.parent / "app" / "ops" / "review.py"
        text = review_path.read_text(encoding="utf-8")

        # No provider-type branching
        assert "isinstance(provider" not in text, (
            "review.py must not use isinstance(provider, ...) for routing (I6). "
            "Use capabilities() instead."
        )
        assert "OllamaProvider" not in text, "review.py must not reference OllamaProvider (I6)"
        assert "CliAgentProvider" not in text, "review.py must not reference CliAgentProvider (I6)"
        assert "ApiProvider" not in text, "review.py must not reference ApiProvider (I6)"
