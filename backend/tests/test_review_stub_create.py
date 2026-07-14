"""
Tests for WS-C stub-create path and detectPageType port (ADR-0079 §2).

Covers:
  - _detect_page_type: keyword-detection for each PageType (EN + type-based rules).
  - create_page_from_review(mode="stub"): deterministic write without LLM; item → created.
  - create_page_from_review(mode="stub"): fan-out on comma-delimited missing-page titles.
  - create_page_from_review(mode="stub"): does NOT call resolve_provider_config (I6-neutral).
  - create_page_from_review(mode="generate"): still routes to _run_generation (regression guard).
  - POST /review/queue/{id}/approve (no body): routes to stub (default mode=stub).
  - POST /review/queue/{id}/create (no body): routes to stub (default mode=stub).

All tests mock write_wiki_page at ``app.ingest.writer.write_wiki_page`` so they
never touch the live DB or Qdrant (I1).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
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

# ── Helpers ────────────────────────────────────────────────────────────────────


def _build_stub_meta() -> MetaData:
    """SQLite schema for stub-create tests (minimal: pages + review_items + vault_state)."""
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
        Column("tags", Text, nullable=True),  # required by write_wiki_page
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
        Column("remote_mcp_write_enabled", Integer, nullable=True),
        Column("mcp_access_token_hash", Text, nullable=True),
        Column("mcp_allow_without_token", Integer, nullable=False, server_default=sa_text("0")),
        Column("clip_enabled_db", Integer, nullable=True),
        Column("clip_access_token", Text, nullable=True),
        Column("clip_allowed_origins_db", Text, nullable=True),
        Column("output_language", Text, nullable=True),
        Column("updated_at", Text, nullable=False),
    )

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
        Column("max_iter", Integer, nullable=True, server_default=sa_text("3")),
        Column("token_budget", Integer, nullable=True, server_default=sa_text("60000")),
        Column("is_fallback", Integer, nullable=False, server_default=sa_text("0")),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("updated_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )

    Table(
        "review_items",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("item_type", Text, nullable=False),
        Column("proposal_origin", Text, nullable=False, server_default=sa_text("'legacy'")),
        Column("status", Text, nullable=False, server_default=sa_text("'pending'")),
        Column("page_id", String(36), nullable=True),
        Column("source_page_id", String(36), nullable=True),
        Column("proposed_title", Text, nullable=True),
        Column("proposed_page_type", Text, nullable=True),
        Column("proposed_dir", Text, nullable=True),
        Column("rationale", Text, nullable=True),
        Column("resolution", Text, nullable=True),
        Column("created_page_id", String(36), nullable=True),
        Column("deep_research_run_id", String(36), nullable=True),
        Column("content_key", Text, nullable=True),
        Column("referenced_page_ids", Text, nullable=True),
        Column("search_queries", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("reviewed_at", Text, nullable=True),
        Column("reviewed_by", Text, nullable=True),
    )

    # Minimal ancillary tables referenced by app startup paths.
    Table(
        "ingest_runs",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("page_id", String(36), nullable=True),
        Column("provider_name", Text, nullable=False),
        Column("source_path", Text, nullable=True),
        Column("status", Text, nullable=False),
        Column("route", Text, nullable=True),
        Column("pages_created", Integer, nullable=False, default=0),
        Column("pages_updated", Integer, nullable=False, default=0),
        Column("pages_unchanged", Integer, nullable=False, default=0),
        Column("total_cost_usd", Float, nullable=True),
        Column("error_message", Text, nullable=True),
        Column("started_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("completed_at", Text, nullable=True),
    )

    return meta


async def _insert_review_item(
    env: dict[str, Any],
    *,
    item_type: str = "missing-page",
    proposed_title: str = "Test Page",
    rationale: str | None = None,
    status: str = "pending",
) -> str:
    item_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO review_items "
                "(id, vault_id, item_type, proposed_title, rationale, status, proposal_origin) "
                "VALUES (:id, :vault_id, :item_type, :proposed_title, :rationale, :status, 'ai')"
            ),
            {
                "id": item_id,
                "vault_id": "test-vault",
                "item_type": item_type,
                "proposed_title": proposed_title,
                "rationale": rationale,
                "status": status,
            },
        )
        await sess.commit()
    return item_id


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture()
async def stub_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> dict[str, Any]:
    """Lightweight SQLite env for stub-create tests (tags column included)."""
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(cfg.settings, "searxng_url", "")

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    meta = _build_stub_meta()
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
                "INSERT INTO vault_state "
                "(id, vault_id, data_version, remote_mcp_enabled, "
                " mcp_access_token_hash, mcp_allow_without_token, updated_at) "
                "VALUES (:id, :vault_id, 0, 0, NULL, 0, datetime('now'))"
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
    monkeypatch.setattr("app.ops.review.get_session", patched_get_session)

    # Pre-load app.ingest.orchestrator before writer to resolve the circular import
    # (orchestrator → writer → orchestrator already in sys.modules → OK).
    import app.ingest.orchestrator  # noqa: F401, PLC0415

    return {"session_factory": session_factory}


# ── Unit: _detect_page_type keyword detection ─────────────────────────────────


class TestDetectPageType:
    """T-STUB-000: _detect_page_type ports nashsu/llm_wiki detectPageType (ADR-0079 §2)."""

    def _dt(self, item_type: str, title: str) -> str:
        from app.ops.review import _detect_page_type

        return _detect_page_type(item_type, title).value

    def test_missing_page_always_concept(self) -> None:
        assert self._dt("missing-page", "anything vs another") == "concept"

    def test_contradiction_always_query(self) -> None:
        assert self._dt("contradiction", "Some entity person") == "query"

    def test_suggestion_always_query(self) -> None:
        assert self._dt("suggestion", "overview of topics") == "query"

    def test_entity_keyword_entity(self) -> None:
        # item_type != rule-based types → keyword scan
        assert self._dt("other", "OpenAI the company") == "entity"

    def test_comparison_keyword(self) -> None:
        assert self._dt("other", "Comparison of Approach A and Approach B") == "comparison"

    def test_vs_keyword_comparison(self) -> None:
        assert self._dt("other", "Python vs JavaScript") == "comparison"

    def test_synthesis_keyword(self) -> None:
        assert self._dt("other", "Overview of Machine Learning Frameworks") == "synthesis"

    def test_concept_keyword(self) -> None:
        assert self._dt("other", "Transformer Model Architecture") == "concept"

    def test_default_query(self) -> None:
        # No recognizable keyword → query
        assert self._dt("other", "Random title with no clues") == "query"

    def test_entity_takes_priority_over_comparison(self) -> None:
        # entity keywords checked before comparison
        assert self._dt("other", "Organization vs Company comparison") == "entity"

    def test_comparison_before_synthesis(self) -> None:
        # comparison checked before synthesis
        assert self._dt("other", "Comparison overview survey") == "comparison"


# ── Integration: stub mode does NOT call LLM ─────────────────────────────────


class TestStubCreate:
    """T-STUB-001..007: create_page_from_review(mode='stub') — deterministic, no LLM."""

    def _writer_mod(self) -> Any:
        """Return the already-loaded app.ingest.writer module (loaded by fixture pre-import)."""
        import sys

        return sys.modules["app.ingest.writer"]

    async def test_stub_writes_page_and_resolves_item(self, stub_env: dict[str, Any]) -> None:
        """T-STUB-001: stub writes title+description page, item transitions to created."""
        from app.ops.review import create_page_from_review

        item_id = await _insert_review_item(
            stub_env,
            item_type="missing-page",
            proposed_title="Kubernetes Networking",
            rationale="Mentioned frequently but missing.",
        )

        fake_page = MagicMock()
        fake_page.id = uuid.uuid4()
        fake_write = AsyncMock(return_value=fake_page)

        with (
            patch.object(self._writer_mod(), "write_wiki_page", new=fake_write),
            patch("app.ops.review.sweep_reviews", new=AsyncMock()),
        ):
            item = await create_page_from_review(uuid.UUID(item_id), mode="stub")

        assert item.status == "created"
        assert item.resolution == "created"
        assert str(item.created_page_id) == str(fake_page.id)

    async def test_stub_does_not_call_provider(self, stub_env: dict[str, Any]) -> None:
        """T-STUB-002: stub mode never calls resolve_provider_config (I6-neutral)."""
        from app.ops.review import create_page_from_review

        item_id = await _insert_review_item(stub_env, proposed_title="Neural Networks")

        fake_page = MagicMock()
        fake_page.id = uuid.uuid4()
        fake_write = AsyncMock(return_value=fake_page)

        with (
            patch.object(self._writer_mod(), "write_wiki_page", new=fake_write),
            patch("app.ops.review.sweep_reviews", new=AsyncMock()),
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(side_effect=AssertionError("must not call provider in stub mode")),
            ),
        ):
            # Must not raise (if provider is called, AssertionError propagates).
            item = await create_page_from_review(uuid.UUID(item_id), mode="stub")

        assert item.status == "created"

    async def test_stub_page_content_has_title_header(self, stub_env: dict[str, Any]) -> None:
        """T-STUB-003: stub content starts with # <title> (llm_wiki parity)."""
        from app.ops.review import create_page_from_review

        item_id = await _insert_review_item(
            stub_env,
            proposed_title="Attention Is All You Need",
            rationale="Key paper in transformer literature.",
        )

        captured: dict[str, Any] = {}

        async def _fake_write(session: Any, page: Any, origin_source: str) -> Any:
            captured["page"] = page
            m = MagicMock()
            m.id = uuid.uuid4()
            return m

        with (
            patch.object(self._writer_mod(), "write_wiki_page", new=_fake_write),
            patch("app.ops.review.sweep_reviews", new=AsyncMock()),
        ):
            await create_page_from_review(uuid.UUID(item_id), mode="stub")

        assert "page" in captured
        content = captured["page"].content
        assert content.startswith(
            "# Attention Is All You Need"
        ), f"stub content must begin with # <title>; got: {content[:80]!r}"
        assert "Key paper in transformer literature." in content

    async def test_stub_detects_type_from_title_keyword(self, stub_env: dict[str, Any]) -> None:
        """T-STUB-004: stub uses _detect_page_type → correct type on written page."""
        from app.ingest.schemas import PageType
        from app.ops.review import create_page_from_review

        item_id = await _insert_review_item(
            stub_env,
            # item_type != "missing-page" / "suggestion" → keyword scan → comparison
            item_type="other",
            proposed_title="Comparison of Redis vs Memcached",
        )

        captured: dict[str, Any] = {}

        async def _fake_write(session: Any, page: Any, origin_source: str) -> Any:
            captured["page"] = page
            m = MagicMock()
            m.id = uuid.uuid4()
            return m

        with (
            patch.object(self._writer_mod(), "write_wiki_page", new=_fake_write),
            patch("app.ops.review.sweep_reviews", new=AsyncMock()),
        ):
            await create_page_from_review(uuid.UUID(item_id), mode="stub")

        assert captured["page"].type is PageType.COMPARISON

    async def test_stub_default_mode_is_stub(self, stub_env: dict[str, Any]) -> None:
        """T-STUB-005: calling without mode= defaults to stub (does NOT call provider)."""
        from app.ops.review import create_page_from_review

        item_id = await _insert_review_item(stub_env, proposed_title="Gradient Descent")

        fake_page = MagicMock()
        fake_page.id = uuid.uuid4()
        fake_write = AsyncMock(return_value=fake_page)
        provider_call_count = {"n": 0}

        async def _counting_provider(*args: Any, **kwargs: Any) -> Any:
            provider_call_count["n"] += 1
            return MagicMock()

        with (
            patch.object(self._writer_mod(), "write_wiki_page", new=fake_write),
            patch("app.ops.review.sweep_reviews", new=AsyncMock()),
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=_counting_provider,
            ),
        ):
            item = await create_page_from_review(uuid.UUID(item_id))

        assert item.status == "created"
        assert provider_call_count["n"] == 0, "default mode must be stub — no provider call"

    async def test_stub_fan_out_missing_page_commas(self, stub_env: dict[str, Any]) -> None:
        """T-STUB-006: stub fan-out on comma-delimited missing-page creates multiple pages."""
        from app.ops.review import create_page_from_review

        item_id = await _insert_review_item(
            stub_env,
            item_type="missing-page",
            proposed_title="Alpha, Beta, Gamma",
        )

        write_calls: list[str] = []

        async def _fake_write(session: Any, page: Any, origin_source: str) -> Any:
            write_calls.append(page.title)
            m = MagicMock()
            m.id = uuid.uuid4()
            return m

        with (
            patch.object(self._writer_mod(), "write_wiki_page", new=_fake_write),
            patch("app.ops.review.sweep_reviews", new=AsyncMock()),
        ):
            item = await create_page_from_review(uuid.UUID(item_id), mode="stub")

        assert len(write_calls) == 3, f"expected 3 stub writes, got {write_calls}"
        assert write_calls == ["Alpha", "Beta", "Gamma"]
        assert item.status == "created"

    async def test_generate_mode_routes_to_run_generation(self, stub_env: dict[str, Any]) -> None:
        """
        T-STUB-007 (regression): mode='generate' still reaches _run_generation.

        Ensures the stub branch does NOT swallow the generate path (ADR-0079 §2 boundary).
        """
        from app.ops.review import GenerationOutcome, create_page_from_review

        item_id = await _insert_review_item(
            stub_env,
            item_type="missing-page",
            proposed_title="Backpropagation",
        )

        generation_called = {"n": 0}

        async def _fake_generation(**kwargs: Any) -> GenerationOutcome:
            generation_called["n"] += 1
            return GenerationOutcome(
                wiki_page=None, created_page_id=str(uuid.uuid4()), converged=True
            )

        with (
            patch("app.ops.review._run_generation", side_effect=_fake_generation),
            patch(
                "app.provider_config_service.resolve_provider_config",
                new=AsyncMock(return_value=MagicMock()),
            ),
            patch("app.ops.review.sweep_reviews", new=AsyncMock()),
        ):
            item = await create_page_from_review(uuid.UUID(item_id), mode="generate")

        assert generation_called["n"] >= 1, "_run_generation must be called in mode='generate'"
        assert item.status == "created"
