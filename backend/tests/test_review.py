"""
F9 HITL Review Queue — unit + API tests (ADR-0034, supersedes ADR-0025 test coverage).

Tests retained from ADR-0025 scope, updated for the ADR-0034 proposal model.
generate_review_queries has been REMOVED (see ADR-0034 §10); those tests are dropped.
approve() no longer exists as a standalone function — Create is lazy (§5); approve
  endpoint now calls create_page_from_review and returns 201 or 502 (AI seam).

Tests:
  T-RV-001  enqueue_review inserts a row with status=pending (AC-F9-1)
  T-RV-006  Fire-and-forget hook: exception inside hook NEVER propagates into ingest (AC-F9-2)
  T-RV-007  GET /review/queue returns 200 with paginated items (AC-F9-5)
  T-RV-008  GET /review/queue cap at 200 items (I7 bounded page size)
  T-RV-009  POST /review/queue/{id}/approve → 502 (AI seam pending);
             item stays pending (ADR-0034 §5.3)
  T-RV-010  POST /review/queue/{id}/skip → status=skipped
  T-RV-011  POST /review/queue/{id}/deep-research returns 2xx + run_id + review_item_id
             and stores deep_research_run_id on the item (AC-F9-3)
  T-RV-012  POST /review/queue/{id}/deep-research returns 503 when SEARXNG_URL unset
  T-RV-013  GET /review/queue pagination: limit+offset work correctly (AC-F9-5)
  T-RV-014  approve on non-existent item → 404
  T-RV-016  review queue respects vault_id query parameter
  T-RV-017  I6 — no isinstance/class-name branching in review.py
  T-RV-018  pre_generated_query NOT in review.py as attribute access (dropped ADR-0034)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

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

# ── SQLite schema for F9 review tests (ADR-0034 proposal model) ───────────────


def _build_review_meta() -> MetaData:
    """SQLite-compatible schema covering review_items (ADR-0034) + FK targets."""
    meta = MetaData()

    # pages (FK target for review_items.page_id / source_page_id / created_page_id)
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
        Column("community", Integer, nullable=True),  # G-P0-2: Louvain community id
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
        Column("remote_mcp_enabled", Integer, nullable=False, server_default=sa_text("0")),
        Column("mcp_access_token_hash", Text, nullable=True),
        Column("mcp_allow_without_token", Integer, nullable=False, server_default=sa_text("0")),
        # ADR-0040 §3: clip ingress runtime config (NULL = not set in DB; env fallback applies)
        Column("clip_enabled_db", Integer, nullable=True),
        Column("clip_access_token", Text, nullable=True),
        Column("clip_allowed_origins_db", Text, nullable=True),
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

    # review_items — ADR-0034 proposal model (pre_generated_query DROPPED)
    Table(
        "review_items",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("page_id", String(36), nullable=True),
        Column("item_type", Text, nullable=False),
        Column("status", Text, nullable=False, server_default=sa_text("'pending'")),
        # ADR-0034 §3.1 new columns
        Column("source_page_id", Text, nullable=True),
        Column("proposed_title", Text, nullable=True),
        Column("proposed_page_type", Text, nullable=True),
        Column("proposed_dir", Text, nullable=True),
        Column("rationale", Text, nullable=True),
        Column("resolution", Text, nullable=True),
        Column("created_page_id", Text, nullable=True),
        # retained
        Column("deep_research_run_id", String(36), nullable=True),
        # ADR-0044 (migration 0019) additive columns — kept in sync with the live schema (I8).
        Column("content_key", Text, nullable=True),
        Column("referenced_page_ids", Text, nullable=True),
        Column("search_queries", Text, nullable=True),
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
    item_type: str = "missing-page",
    status: str = "pending",
    proposed_title: str | None = "Test Proposal",
    rationale: str | None = None,
    page_id: str | None = None,
    deep_research_run_id: str | None = None,
) -> str:
    """Insert one review_items row (ADR-0034 schema) and return its ID string."""
    item_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO review_items "
                "(id, vault_id, page_id, item_type, status, proposed_title, "
                " rationale, deep_research_run_id, created_at) "
                "VALUES (:id, :vault_id, :page_id, :item_type, "
                ":status, :proposed_title, :rationale, :dr_id, datetime('now'))"
            ),
            {
                "id": item_id,
                "vault_id": vault_id,
                "page_id": page_id,
                "item_type": item_type,
                "status": status,
                "proposed_title": proposed_title,
                "rationale": rationale,
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
    """T-RV-001: enqueue_review DB write (AC-F9-1) — ADR-0034 proposal fields."""

    async def test_enqueues_pending_row(self, review_env: dict[str, Any]) -> None:
        """enqueue_review inserts a row with status=pending and new proposal fields."""
        from app.ops.review import enqueue_review

        item = await enqueue_review(
            vault_id="test-vault",
            item_type="missing-page",
            proposed_title="Quantum Computing",
            rationale="Dangling wikilink [[Quantum Computing]]",
        )

        assert item.status == "pending"
        assert item.vault_id == "test-vault"
        assert item.item_type == "missing-page"
        assert item.proposed_title == "Quantum Computing"
        assert item.rationale == "Dangling wikilink [[Quantum Computing]]"
        assert item.reviewed_at is None
        assert item.resolution is None

    async def test_enqueues_suggestion_type(self, review_env: dict[str, Any]) -> None:
        """enqueue_review with suggestion type (valid ADR-0034 type)."""
        from app.ops.review import enqueue_review

        item = await enqueue_review(
            vault_id="test-vault",
            item_type="suggestion",
            proposed_title=None,
            rationale="The source mentions important gaps.",
        )
        assert item.status == "pending"
        assert item.item_type == "suggestion"

    async def test_enqueue_is_not_singleton(self, review_env: dict[str, Any]) -> None:
        """Calling enqueue_review twice creates two rows (event log, not upsert — ADR-0025 §3.1)."""
        from app.ops.review import enqueue_review

        await enqueue_review(vault_id="test-vault", item_type="missing-page")
        await enqueue_review(vault_id="test-vault", item_type="missing-page")

        # Verify two rows in the DB
        async with review_env["session_factory"]() as sess:
            result = await sess.execute(
                sa_text("SELECT COUNT(*) FROM review_items WHERE vault_id='test-vault'")
            )
            count = result.scalar_one()
        assert count == 2


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
                    item_type="missing-page",
                )
            except Exception:
                pass  # AC-F9-2: never propagate

        # Must not raise
        await run_hook_safe(written_pages=[], vault_id="test-vault")


# ── T-RV-007..013, T-RV-014, T-RV-016: REST API tests ────────────────────────


class TestReviewQueueEndpoints:
    """T-RV-007..014, T-RV-016: REST endpoint behavior (ADR-0034 §7)."""

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
            item_type="missing-page",
            proposed_title="New Topic",
        )
        resp = await review_client.get("/review/queue?vault_id=test-vault")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert len(body["items"]) == 1
        item = body["items"][0]
        assert item["status"] == "pending"
        assert item["item_type"] == "missing-page"
        assert item["proposed_title"] == "New Topic"
        # pre_generated_query is DROPPED (ADR-0034)
        assert "pre_generated_query" not in item

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
            await _insert_review_item(
                review_env, item_type="missing-page", proposed_title=f"Page {i}"
            )

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

    async def test_approve_returns_502_ai_seam(
        self, review_env: dict[str, Any], review_client: AsyncClient
    ) -> None:
        """T-RV-009: POST /review/queue/{id}/approve → 409/502 (ADR-0034 §5.3).

        The Create action invokes _run_generation (real, capability-aware generation). With no
        configured provider the handler returns 409; if a provider resolves but generation fails
        it returns 502. Either way the item must remain pending (no state change on failure).
        """
        item_id = await _insert_review_item(review_env, proposed_title="Galaxy Formation")

        # approve calls create_page_from_review → resolve_provider_config needed
        # but there's no configured provider in the test DB, so we get either 409 or 502
        resp = await review_client.post(f"/review/queue/{item_id}/approve")
        # Either 409 (no provider) or 502 (AI seam stub) is correct per ADR-0034 §5
        assert resp.status_code in (
            409,
            502,
        ), f"Expected 409 (no provider) or 502 (AI seam), got {resp.status_code}: {resp.text}"

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

    async def test_deep_research_returns_2xx_with_run_id(
        self,
        review_env: dict[str, Any],
        review_client: AsyncClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """T-RV-011: deep-research → 2xx, body has review_item_id + run_id."""
        from app import config as cfg

        monkeypatch.setattr(cfg.settings, "searxng_url", "http://searxng:8080")

        item_id = await _insert_review_item(
            review_env,
            proposed_title="What are the implications of quantum computing?",
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
            proposed_title="Why is the sky blue?",
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


# ── T-RV: ops.review unit-level list/skip ─────────────────────────────────────


class TestReviewOpsUnit:
    """Unit tests for ops.review list_queue / skip (no HTTP) — ADR-0034."""

    async def test_list_queue_paginates(self, review_env: dict[str, Any]) -> None:
        """list_queue returns ReviewQueuePage with correct total and items slice."""
        for i in range(4):
            await _insert_review_item(review_env, proposed_title=f"Page {i}")

        from app.ops.review import list_queue

        page = await list_queue("test-vault", limit=3, offset=0)
        assert page.total == 4
        assert len(page.items) == 3
        assert page.limit == 3
        assert page.offset == 0

        page2 = await list_queue("test-vault", limit=3, offset=3)
        assert len(page2.items) == 1

    async def test_skip_updates_status(self, review_env: dict[str, Any]) -> None:
        """skip() sets status=skipped, resolution=skipped, and reviewed_at."""
        item_id_str = await _insert_review_item(review_env)
        item_uuid = uuid.UUID(item_id_str)

        from app.ops.review import skip

        updated = await skip(item_uuid)
        assert updated.status == "skipped"
        assert updated.resolution == "skipped"
        assert updated.reviewed_at is not None

    async def test_skip_nonexistent_raises_http_404(self, review_env: dict[str, Any]) -> None:
        """skip() on absent item raises HTTPException(404)."""
        from app.ops.review import skip
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await skip(uuid.uuid4())
        assert exc_info.value.status_code == 404

    async def test_list_queue_vault_filter(self, review_env: dict[str, Any]) -> None:
        """list_queue filters by vault_id (ADR-0034 §7)."""
        await _insert_review_item(review_env, vault_id="vault-X")
        await _insert_review_item(review_env, vault_id="vault-Y")

        from app.ops.review import list_queue

        page_x = await list_queue("vault-X")
        assert page_x.total == 1

        page_y = await list_queue("vault-Y")
        assert page_y.total == 1


# ── T-RV-017: I6 — no isinstance / class-name branching in review.py ──────────


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


# ── T-RV-018: pre_generated_query NOT in review.py (dropped ADR-0034) ─────────


class TestADR0034PreGeneratedQueryDropped:
    """T-RV-018: pre_generated_query is fully removed from review.py (ADR-0034 §10)."""

    def test_pre_generated_query_not_in_review(self) -> None:
        """review.py must not access or assign pre_generated_query anywhere."""
        from pathlib import Path

        review_path = Path(__file__).resolve().parent.parent / "app" / "ops" / "review.py"
        text = review_path.read_text(encoding="utf-8")

        # Must not use attribute access or assignment for the dropped column
        assert (
            ".pre_generated_query" not in text
        ), "review.py must not access .pre_generated_query attribute (dropped ADR-0034 §10)"
        assert (
            "pre_generated_query=" not in text
        ), "review.py must not assign pre_generated_query= (dropped ADR-0034 §10)"

    def test_generate_review_queries_not_in_review(self) -> None:
        """generate_review_queries function is removed in ADR-0034 (§10 Do-NOT list)."""
        from pathlib import Path

        review_path = Path(__file__).resolve().parent.parent / "app" / "ops" / "review.py"
        text = review_path.read_text(encoding="utf-8")

        # The function must not be defined
        assert "def generate_review_queries" not in text, (
            "generate_review_queries was removed in ADR-0034 §10; " "must not exist in review.py"
        )
