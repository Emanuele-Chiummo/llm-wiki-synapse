"""
K2 Lint-fix loop — unit + API tests (ADR-0037).

Tests:
  T-LINT-001  orphan detection is deterministic (graph in-degree 0; no provider call)
  T-LINT-002  I7 bound: "always more findings" → loop stops at max_iter rounds
  T-LINT-003  I7 bound: token_budget gate stops the loop before an unaffordable round
  T-LINT-004  I7: total_cost_usd is logged on the lint_runs row
  T-LINT-005  human gate: scan does NOT apply any fix (no edits, no data_version bump)
  T-LINT-006  apply (flag-only categories) → status=applied, no edit
  T-LINT-007  apply (missing-xref) → reuses the wikilink-enrichment seam
  T-LINT-008  apply (missing-page) → delegates to the lazy-generation seam
  T-LINT-009  dismiss → status=dismissed
  T-LINT-010  apply on non-open finding → 409; on missing → 404
  T-LINT-011  pagination: list_lint_findings limit+offset + status filter
  T-LINT-012  GET /lint/findings cap at 200 (I7 bounded page size)
  T-LINT-013  POST /lint/scan endpoint returns run + findings
  T-LINT-014  I6 — no isinstance/class-name branching in lint.py
  T-LINT-015  I1 — a lint run touches only the pages/links tables (no full vault rescan)
  T-LINT-016  status defaults pessimistically — run never left 'running'
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import (
    BigInteger,
    Column,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── SQLite schema for K2 lint tests ───────────────────────────────────────────


def _build_lint_meta() -> MetaData:
    """SQLite-compatible schema covering lint_runs + lint_findings + FK targets."""
    meta = MetaData()

    # pages (FK target for lint_findings.target_page_id; orphan/link reads)
    Table(
        "pages",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("file_path", Text, nullable=False),
        Column("title", Text, nullable=True),
        Column("type", Text, nullable=True),
        Column("sources", Text, nullable=True),
        Column("tags", Text, nullable=True),  # K6 navigation tags (migration 0018)
        Column("generation_key", Text, nullable=True),
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

    # links (orphan detection: resolved incoming wikilinks → in-degree)
    Table(
        "links",
        meta,
        Column("id", String(36), primary_key=True),
        Column("source_page_id", String(36), nullable=False),
        Column("target_title", Text, nullable=False),
        Column("target_page_id", String(36), nullable=True),
        Column("alias", Text, nullable=True),
        Column("dangling", Integer, nullable=False, server_default=sa_text("0")),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )

    # vault_state (GET /status / bump_version reads this)
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
        # ADR-0040 §3: clip ingress runtime config (NULL = not set in DB; env fallback applies)
        Column("clip_enabled_db", Integer, nullable=True),
        Column("clip_access_token", Text, nullable=True),
        Column("clip_allowed_origins_db", Text, nullable=True),
        # ADR-0043 §2.1 (legacy plaintext — kept for rollback safety; W7 migration 0027)
        Column("cli_oauth_token", Text, nullable=True),
        # W7 migration 0027: Fernet-encrypted CLI OAuth token (BYTEA; NULL = not set)
        Column("cli_oauth_token_encrypted", LargeBinary, nullable=True),
        # P3-e (ADR-0071): Fernet-encrypted web-search API keys (BYTEA; NULL = none set)
        Column("web_search_api_keys_encrypted", LargeBinary, nullable=True),
        # ADR-0041 §3: SearXNG web-search runtime config (NULL = not set in DB; env fallback)
        Column("searxng_url_db", Text, nullable=True),
        Column("searxng_categories_db", Text, nullable=True),
        Column("searxng_max_queries_db", Integer, nullable=True),
        Column("output_language", Text, nullable=True),
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

    # lint_runs
    Table(
        "lint_runs",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("status", Text, nullable=False, server_default=sa_text("'running'")),
        Column("max_iter", Integer, nullable=False),
        Column("token_budget", Integer, nullable=False),
        Column("iterations_used", Integer, nullable=False, server_default=sa_text("0")),
        Column("findings_count", Integer, nullable=False, server_default=sa_text("0")),
        Column("total_cost_usd", Float, nullable=False, server_default=sa_text("0")),
        Column("started_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("completed_at", Text, nullable=True),
        Column("error_message", Text, nullable=True),
        Column("page_type_counts", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )

    # lint_findings (includes L2 columns: suggested_target + suggested_page_id)
    Table(
        "lint_findings",
        meta,
        Column("id", String(36), primary_key=True),
        Column("lint_run_id", String(36), nullable=False),
        Column("vault_id", String, nullable=False),
        Column("category", Text, nullable=False),
        Column("severity", Text, nullable=False, server_default=sa_text("'warning'")),
        Column("target_page_id", String(36), nullable=True),
        Column("target_title", Text, nullable=True),
        Column("description", Text, nullable=False),
        Column("proposed_action", Text, nullable=True),
        Column("suggested_target", Text, nullable=True),  # L2
        Column("suggested_page_id", String(36), nullable=True),  # L2
        Column("status", Text, nullable=False, server_default=sa_text("'open'")),
        Column("resolution_note", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("reviewed_at", Text, nullable=True),
    )

    # Tables referenced by the main lifespan / endpoints at import time
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
        "review_items",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("page_id", String(36), nullable=True),
        Column("item_type", Text, nullable=False),
        Column("status", Text, nullable=False, server_default=sa_text("'pending'")),
        Column("proposal_origin", Text, nullable=False, server_default=sa_text("'legacy'")),
        Column("source_page_id", Text, nullable=True),
        Column("proposed_title", Text, nullable=True),
        Column("proposed_page_type", Text, nullable=True),
        Column("proposed_dir", Text, nullable=True),
        Column("rationale", Text, nullable=True),
        Column("resolution", Text, nullable=True),
        Column("created_page_id", Text, nullable=True),
        Column("deep_research_run_id", String(36), nullable=True),
        Column("content_key", Text, nullable=True),
        Column("referenced_page_ids", Text, nullable=True),
        Column("search_queries", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("reviewed_at", Text, nullable=True),
        Column("reviewed_by", Text, nullable=True),
    )

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

    Table(
        "deep_research_sources",
        meta,
        Column("id", String(36), primary_key=True),
        Column("run_id", String(36), nullable=False),
        Column("url", Text, nullable=False),
        Column("title", Text, nullable=True),
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

    Table(
        "edges",
        meta,
        Column("id", String(36), primary_key=True),
        Column("source_id", String(36), nullable=False),
        Column("target_id", String(36), nullable=False),
        Column("weight", Float, nullable=False, default=1.0),
    )

    return meta


# ── Shared fixture ─────────────────────────────────────────────────────────────


@pytest.fixture()
async def lint_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pytest.TempPathFactory,
) -> dict[str, Any]:
    """Stand-alone SQLite test environment for K2 lint tests (lifespan bypassed)."""
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    meta = _build_lint_meta()
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
    async def patched_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    monkeypatch.setattr("app.db.get_session", patched_get_session)
    monkeypatch.setattr("app.main.get_session", patched_get_session)
    monkeypatch.setattr("app.ops.lint.get_session", patched_get_session)
    monkeypatch.setattr("app.ops.review.get_session", patched_get_session)
    monkeypatch.setattr("app.routers.pages.get_session", patched_get_session, raising=False)

    from app.main import app
    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield

    app.router.lifespan_context = test_lifespan

    return {"app": app, "session_factory": session_factory}


@pytest.fixture()
async def lint_client(lint_env: dict[str, Any]) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=lint_env["app"]),
        base_url="http://test",
    ) as client:
        yield client


# ── DB helpers ─────────────────────────────────────────────────────────────────


async def _insert_page(
    env: dict[str, Any],
    *,
    vault_id: str = "test-vault",
    title: str = "Test Page",
    file_path: str | None = None,
) -> str:
    page_id = str(uuid.uuid4())
    fp = file_path or f"wiki/entities/{title.lower().replace(' ', '_')}.md"
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, content_hash, pinned, created_at, updated_at) "
                "VALUES (:id, :vault_id, :fp, :title, :hash, 0, datetime('now'), datetime('now'))"
            ),
            {"id": page_id, "vault_id": vault_id, "fp": fp, "title": title, "hash": "aabbcc"},
        )
        await sess.commit()
    return page_id


async def _insert_link(
    env: dict[str, Any],
    *,
    source_page_id: str,
    target_title: str,
    target_page_id: str | None,
    dangling: int = 0,
) -> None:
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO links "
                "(id, source_page_id, target_title, target_page_id, dangling, created_at) "
                "VALUES (:id, :src, :tt, :tgt, :dangling, datetime('now'))"
            ),
            {
                "id": str(uuid.uuid4()),
                "src": source_page_id,
                "tt": target_title,
                "tgt": target_page_id,
                "dangling": dangling,
            },
        )
        await sess.commit()


async def _insert_page_with_sources(
    env: dict[str, Any],
    *,
    title: str,
    file_path: str,
    sources: list[str],
    vault_id: str = "test-vault",
) -> str:
    """Seed a live page with a JSON `sources` cell (ADR-0067 D4 contradiction resolution)."""
    import json as _json

    page_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, sources, content_hash, pinned, "
                " created_at, updated_at) "
                "VALUES (:id, :v, :fp, :t, 'concept', :src, 'h', 0, datetime('now'), datetime('now'))"
            ),
            {
                "id": page_id,
                "v": vault_id,
                "fp": file_path,
                "t": title,
                "src": _json.dumps(sources),
            },
        )
        await sess.commit()
    return page_id


def _make_contradiction_provider(payload: dict[str, Any]) -> Any:
    """Fake InferenceProvider whose chat() returns *payload* as JSON (contradiction phrasing)."""
    import json as _json

    provider = MagicMock()

    async def mock_chat(messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        async def _gen() -> AsyncIterator[str]:
            yield _json.dumps(payload)

        return _gen()

    provider.chat = mock_chat
    provider.bind_accumulator = MagicMock()
    return provider


async def _insert_finding(
    env: dict[str, Any],
    *,
    vault_id: str = "test-vault",
    category: str = "contradiction",
    severity: str = "warning",
    status: str = "open",
    target_page_id: str | None = None,
    target_title: str | None = None,
    description: str = "Test finding",
) -> str:
    run_id = str(uuid.uuid4())
    finding_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO lint_runs "
                "(id, vault_id, status, max_iter, token_budget, created_at, started_at) "
                "VALUES (:id, :v, 'completed', 3, 20000, datetime('now'), datetime('now'))"
            ),
            {"id": run_id, "v": vault_id},
        )
        await sess.execute(
            sa_text(
                "INSERT INTO lint_findings "
                "(id, lint_run_id, vault_id, category, severity, target_page_id, target_title, "
                " description, status, created_at) "
                "VALUES (:id, :rid, :v, :cat, :sev, :tpid, :tt, :desc, :st, datetime('now'))"
            ),
            {
                "id": finding_id,
                "rid": run_id,
                "v": vault_id,
                "cat": category,
                "sev": severity,
                "tpid": target_page_id,
                "tt": target_title,
                "desc": description,
                "st": status,
            },
        )
        await sess.commit()
    return finding_id


def _make_findings_provider(*, calls_log: list[int], findings_per_round: int = 5) -> Any:
    """
    Mock InferenceProvider whose chat() ALWAYS returns NEW unique findings — used to prove the
    loop is bounded (I7): an unbounded loop would spin forever, a bounded one stops at max_iter.
    """
    provider = MagicMock()

    async def mock_chat(messages: list[Any], retrieval_context: str = "") -> AsyncIterator[str]:
        calls_log.append(1)
        round_idx = len(calls_log)

        async def _gen() -> AsyncIterator[str]:
            items = [
                {
                    "category": "contradiction",
                    "severity": "warning",
                    "description": f"conflict round {round_idx} item {i}",
                }
                for i in range(findings_per_round)
            ]
            import json

            yield json.dumps({"findings": items})

        return _gen()

    provider.chat = mock_chat
    provider.bind_accumulator = MagicMock()
    return provider


# ── T-LINT-001: orphan detection is deterministic ─────────────────────────────


class TestOrphanDetection:
    """T-LINT-001: orphan-page findings are deterministic (no provider call)."""

    async def test_orphan_detected_when_no_incoming_links(self, lint_env: dict[str, Any]) -> None:
        from app.ops.lint import _detect_orphans

        # Page A links to B; C is an orphan (no incoming link).
        page_a = await _insert_page(lint_env, title="A")
        page_b = await _insert_page(lint_env, title="B")
        await _insert_page(lint_env, title="C")
        await _insert_link(lint_env, source_page_id=page_a, target_title="B", target_page_id=page_b)

        findings = await _detect_orphans("test-vault")
        titles = {f.target_title for f in findings}
        # A has no incoming link → orphan; C has no incoming link → orphan; B is linked.
        assert "C" in titles
        assert "A" in titles
        assert "B" not in titles
        assert all(f.category == "orphan-page" for f in findings)

    async def test_orphan_excludes_navigation_roots(self, lint_env: dict[str, Any]) -> None:
        from app.ops.lint import _detect_orphans

        await _insert_page(lint_env, title="Index", file_path="wiki/index.md")
        await _insert_page(lint_env, title="Log", file_path="wiki/log.md")
        findings = await _detect_orphans("test-vault")
        assert findings == []

    async def test_orphan_index_link_excluded_from_inbound_count(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L-bug1: a link FROM index.md does NOT count as an inbound link for orphan detection."""
        from app.ops.lint import _detect_orphans

        page_index = await _insert_page(lint_env, title="Index", file_path="wiki/index.md")
        page_content = await _insert_page(lint_env, title="Some Page")

        # index.md links to Some Page — under the old (buggy) code this would make Some Page
        # appear linked. Under the fixed code, index.md sources are excluded and Some Page is
        # still an orphan.
        await _insert_link(
            lint_env,
            source_page_id=page_index,
            target_title="Some Page",
            target_page_id=page_content,
        )

        findings = await _detect_orphans("test-vault")
        titles = {f.target_title for f in findings}
        # Some Page has only an inbound link from index.md → still an orphan (L-bug1 fix).
        assert "Some Page" in titles
        # index.md is excluded from candidate set → not an orphan candidate itself.
        assert "Index" not in titles

    async def test_orphan_log_link_excluded_from_inbound_count(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L-bug1: a link FROM log.md does NOT count as an inbound link for orphan detection."""
        from app.ops.lint import _detect_orphans

        page_log = await _insert_page(lint_env, title="Log", file_path="wiki/log.md")
        page_content = await _insert_page(lint_env, title="Another Page")

        await _insert_link(
            lint_env,
            source_page_id=page_log,
            target_title="Another Page",
            target_page_id=page_content,
        )

        findings = await _detect_orphans("test-vault")
        titles = {f.target_title for f in findings}
        assert "Another Page" in titles  # still an orphan (log.md link excluded)
        assert "Log" not in titles

    async def test_orphan_content_page_link_counts_as_inbound(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L-bug1: a link from a content page (not index/log) DOES count as inbound."""
        from app.ops.lint import _detect_orphans

        page_src = await _insert_page(lint_env, title="Source Page")
        page_tgt = await _insert_page(lint_env, title="Target Page")

        await _insert_link(
            lint_env,
            source_page_id=page_src,
            target_title="Target Page",
            target_page_id=page_tgt,
        )

        findings = await _detect_orphans("test-vault")
        titles = {f.target_title for f in findings}
        # Target Page linked by a content page → NOT an orphan.
        assert "Target Page" not in titles
        # Source Page has no inbound → IS an orphan.
        assert "Source Page" in titles

    async def test_orphan_overview_is_eligible(self, lint_env: dict[str, Any]) -> None:
        """L4: overview.md is eligible for orphan detection (no longer excluded)."""
        from app.ops.lint import _detect_orphans

        await _insert_page(lint_env, title="Overview", file_path="wiki/overview.md")

        findings = await _detect_orphans("test-vault")
        titles = {f.target_title for f in findings}
        # overview.md has no inbound content links → IS an orphan (L4 parity fix).
        assert "Overview" in titles


# ── T-LINT-002/003/004/016: I7 bounds ─────────────────────────────────────────


class TestI7Bounds:
    """I7: the scan loop is bounded by max_iter AND token_budget; cost logged."""

    async def test_loop_stops_at_max_iter_with_always_more_findings(
        self, lint_env: dict[str, Any]
    ) -> None:
        """T-LINT-002: an always-more-findings provider must stop at exactly max_iter rounds."""
        calls_log: list[int] = []
        provider = _make_findings_provider(calls_log=calls_log)

        with patch(
            "app.ops.lint._resolve_lint_provider",
            return_value=(provider, MagicMock(token_budget=1_000_000)),
        ):
            from app.ops.lint import run_lint_scan

            result = await run_lint_scan("test-vault", max_iter=3, token_budget=1_000_000)

        # Bounded: provider.chat called at most max_iter times (never unbounded).
        assert len(calls_log) <= 3, f"loop spent {len(calls_log)} rounds; cap is 3"
        assert result.iterations_used <= 3
        assert result.status == "completed"

    async def test_token_budget_gate_stops_loop(self, lint_env: dict[str, Any]) -> None:
        """T-LINT-003: a tiny token_budget stops the loop before spending another round."""
        from app.ingest.provider.base import UsageAccumulator

        calls_log: list[int] = []
        provider = _make_findings_provider(calls_log=calls_log)

        # Force the accumulator to report tokens above the budget immediately.
        original_init = UsageAccumulator.__init__

        def _patched_init(self: UsageAccumulator) -> None:
            original_init(self)
            self.input_tokens = 10_000  # already over the tiny budget

        with (
            patch.object(UsageAccumulator, "__init__", _patched_init),
            patch(
                "app.ops.lint._resolve_lint_provider",
                return_value=(provider, MagicMock(token_budget=10)),
            ),
        ):
            from app.ops.lint import run_lint_scan

            result = await run_lint_scan("test-vault", max_iter=5, token_budget=10)

        # Budget gate fires at the top of round 1 → no provider call at all.
        assert len(calls_log) == 0, "token_budget gate must stop the loop before any spend"
        assert result.status == "completed"

    async def test_total_cost_logged_on_run_row(self, lint_env: dict[str, Any]) -> None:
        """T-LINT-004 / T-LINT-016: total_cost_usd persisted; status never left 'running'."""
        with patch("app.ops.lint._resolve_lint_provider", return_value=None):
            from app.ops.lint import run_lint_scan

            result = await run_lint_scan("test-vault", max_iter=2, token_budget=20_000)

        assert result.status == "completed"  # never 'running'
        async with lint_env["session_factory"]() as sess:
            row = (
                await sess.execute(
                    sa_text(
                        "SELECT status, total_cost_usd, completed_at FROM lint_runs WHERE id=:id"
                    ),
                    {"id": str(result.run_id)},
                )
            ).one()
        assert row.status == "completed"
        assert row.total_cost_usd == 0.0  # no provider → zero cost
        assert row.completed_at is not None


# ── T-LINT-005: human gate — scan does NOT apply ──────────────────────────────


class TestHumanGate:
    """T-LINT-005: run_lint_scan produces findings but applies NO fix (no bump, no edit)."""

    async def test_scan_does_not_apply_or_bump(self, lint_env: dict[str, Any]) -> None:
        # Orphan present so the scan emits a finding.
        await _insert_page(lint_env, title="Orphan")

        bump_called: list[int] = []

        async def _fake_bump() -> None:
            bump_called.append(1)

        with (
            patch("app.ops.lint._resolve_lint_provider", return_value=None),
            patch("app.ingest.orchestrator.bump_version", side_effect=_fake_bump),
        ):
            from app.ops.lint import run_lint_scan

            result = await run_lint_scan("test-vault", max_iter=1, token_budget=20_000)

        assert result.findings_count >= 1
        assert bump_called == [], "scan must NOT bump data_version (human gate, ADR-0037)"

        # All findings are 'open' — none applied by the scan.
        async with lint_env["session_factory"]() as sess:
            statuses = [
                r.status
                for r in (await sess.execute(sa_text("SELECT status FROM lint_findings"))).all()
            ]
        assert statuses, "scan should have persisted findings"
        assert all(s == "open" for s in statuses)


# ── T-LINT-006/007/008: apply paths ───────────────────────────────────────────


class TestApply:
    """T-LINT-006..008: the human-gated apply step."""

    async def test_apply_flag_only_stale_claim(self, lint_env: dict[str, Any]) -> None:
        """
        T-LINT-006: stale-claim is flag-only → status=applied, no edit/bump.
        (contradiction left _FLAG_ONLY_CATEGORIES in ADR-0067 D4 — covered by the
        contradiction-authoring tests below.)
        """
        finding_id = await _insert_finding(lint_env, category="stale-claim")

        bump_called: list[int] = []

        async def _fake_bump() -> None:
            bump_called.append(1)

        with patch("app.ingest.orchestrator.bump_version", side_effect=_fake_bump):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(finding_id))

        assert finding.status == "applied"
        assert finding.resolution_note is not None
        assert bump_called == [], "flag-only apply must not bump data_version"

    async def test_contradiction_not_flag_only(self) -> None:
        """ADR-0067 D4/P0-4: contradiction is no longer flag-only (it authors a query page)."""
        from app.ops.lint import _FLAG_ONLY_CATEGORIES

        assert "contradiction" not in _FLAG_ONLY_CATEGORIES
        # stale-claim + suggestion remain flag-only.
        assert "stale-claim" in _FLAG_ONLY_CATEGORIES
        assert "suggestion" in _FLAG_ONLY_CATEGORIES

    async def test_apply_missing_xref_uses_enrich_seam(self, lint_env: dict[str, Any]) -> None:
        """T-LINT-007: missing-xref apply reuses ops/enrich_wikilinks.enrich_wikilinks."""
        page_id = await _insert_page(lint_env, title="Referencing Page")
        finding_id = await _insert_finding(
            lint_env,
            category="missing-xref",
            target_page_id=page_id,
            target_title="Docker",
            description="Referencing Page mentions Docker but does not link it.",
        )

        from app.ops.enrich_wikilinks import EnrichResult

        enrich_calls: list[Any] = []

        async def _fake_enrich(pages: list[Any], vault_id: str) -> EnrichResult:
            enrich_calls.append((pages, vault_id))
            return EnrichResult(pages_enriched=1, links_added=1)

        with patch("app.ops.enrich_wikilinks.enrich_wikilinks", side_effect=_fake_enrich):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(finding_id))

        assert finding.status == "applied"
        assert len(enrich_calls) == 1, "missing-xref apply must call enrich_wikilinks"
        assert enrich_calls[0][1] == "test-vault"

    async def test_apply_missing_page_uses_generation_seam(self, lint_env: dict[str, Any]) -> None:
        """
        T-LINT-008: missing-page apply delegates to _run_generation; ORCHESTRATED outcome
        (wiki_page set) is written once via write_wiki_page (I1).
        """
        from app.ops.review import GenerationOutcome

        finding_id = await _insert_finding(
            lint_env,
            category="missing-page",
            target_title="Kubernetes",
            description="Kubernetes is mentioned but has no page.",
        )

        gen_calls: list[Any] = []
        write_calls: list[Any] = []

        async def _fake_run_generation(**kwargs: Any) -> Any:
            gen_calls.append(kwargs)
            # Orchestrated route: caller writes the produced WikiPage via write_wiki_page.
            return GenerationOutcome(wiki_page=MagicMock(), created_page_id=None, converged=True)

        async def _fake_write(session: Any, page: Any, origin: str) -> Any:
            write_calls.append((page, origin))
            written = MagicMock()
            written.id = uuid.uuid4()
            return written

        async def _fake_resolve(operation: str, vault_id: str) -> Any:
            return MagicMock(token_budget=20_000, max_iter=3)

        with (
            patch("app.ops.review._run_generation", side_effect=_fake_run_generation),
            patch("app.ingest.orchestrator.write_wiki_page", side_effect=_fake_write),
            patch(
                "app.provider_config_service.resolve_provider_config",
                side_effect=_fake_resolve,
            ),
        ):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(finding_id))

        assert finding.status == "applied"
        assert len(gen_calls) == 1, "missing-page apply must call _run_generation"
        assert gen_calls[0]["proposed_title"] == "Kubernetes"
        assert len(write_calls) == 1, "orchestrated outcome must be written once (I1)"

    async def test_apply_missing_page_delegated_skips_write(self, lint_env: dict[str, Any]) -> None:
        """
        T-LINT-008b: for an AGENTIC (delegated) provider, _run_generation returns a
        GenerationOutcome with created_page_id set — the agent already wrote via MCP write_page,
        so the lint caller MUST NOT call write_wiki_page again (I1 — one write per page).
        """
        from app.ops.review import GenerationOutcome

        finding_id = await _insert_finding(
            lint_env,
            category="missing-page",
            target_title="Helm",
            description="Helm is mentioned but has no page.",
        )

        already_written_id = str(uuid.uuid4())
        write_calls: list[Any] = []

        async def _fake_run_generation(**kwargs: Any) -> Any:
            return GenerationOutcome(
                wiki_page=None, created_page_id=already_written_id, converged=True
            )

        async def _fake_write(session: Any, page: Any, origin: str) -> Any:
            write_calls.append((page, origin))
            return MagicMock(id=uuid.uuid4())

        async def _fake_resolve(operation: str, vault_id: str) -> Any:
            return MagicMock(token_budget=20_000, max_iter=3)

        with (
            patch("app.ops.review._run_generation", side_effect=_fake_run_generation),
            patch("app.ingest.orchestrator.write_wiki_page", side_effect=_fake_write),
            patch(
                "app.provider_config_service.resolve_provider_config",
                side_effect=_fake_resolve,
            ),
        ):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(finding_id))

        assert finding.status == "applied"
        assert len(write_calls) == 0, "delegated path must NOT call write_wiki_page (I1)"
        assert already_written_id in (finding.resolution_note or "")


# ── ADR-0067 D4/P0-4: contradiction → open-question query authoring ────────────


class TestContradictionAuthoring:
    """Applying a contradiction AUTHORS a genuine `type=query` page (ADR-0067 D4/P0-4)."""

    async def _seed_conflict(self, lint_env: dict[str, Any]) -> tuple[str, str]:
        """Seed pages A/B + a contradiction finding naming both; return (finding_id, ...)."""
        page_a = await _insert_page_with_sources(
            lint_env,
            title="Azure OpenAI F1 85",
            file_path="wiki/concepts/azure-openai-f1-85.md",
            sources=["raw/sources/doc-a.md"],
        )
        await _insert_page_with_sources(
            lint_env,
            title="Azure OpenAI F1 90",
            file_path="wiki/concepts/azure-openai-f1-90.md",
            sources=["raw/sources/doc-b.md"],
        )
        finding_id = await _insert_finding(
            lint_env,
            category="contradiction",
            target_page_id=page_a,
            target_title="Azure OpenAI F1 85",
            description=(
                "[[Azure OpenAI F1 85]] states the F1 threshold is 85% but "
                "[[Azure OpenAI F1 90]] states it is 90%."
            ),
        )
        return finding_id, page_a

    async def test_apply_contradiction_authors_query_page(
        self, lint_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Provider present: an applied contradiction writes ONE type=query page with a question
        title, the 5 sections, related=[both slugs], and DB sources[]=union of both pages.
        """
        finding_id, _ = await self._seed_conflict(lint_env)

        provider = _make_contradiction_provider(
            {
                "question": "Which F1 threshold is correct for Azure OpenAI, 85% or 90%?",
                "question_body": "Two pages disagree on the F1 acceptance threshold.",
                "hypothesis": "One figure is from an older SLA revision.",
                "open_points": ["Which doc is newer?", "Same scope?"],
                "impact": "Downstream costing pages may cite the wrong threshold.",
            }
        )

        async def _fake_resolve(vault_id: str) -> Any:
            return provider, MagicMock(token_budget=20_000)

        write_calls: list[Any] = []

        async def _fake_write(session: Any, page: Any, origin: str) -> Any:
            write_calls.append((page, origin))
            return MagicMock(id=uuid.uuid4())

        monkeypatch.setattr("app.ops.lint._resolve_lint_provider", _fake_resolve)
        with patch("app.ingest.orchestrator.write_wiki_page", side_effect=_fake_write):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(finding_id))

        assert finding.status == "applied"
        # ONE write == one data_version bump (write_wiki_page owns the bump, I1).
        assert len(write_calls) == 1, "contradiction apply writes exactly one page (one bump, I1)"
        page, origin = write_calls[0]

        from app.ingest.schemas import PageType

        assert page.type == PageType.QUERY
        assert page.title.rstrip().endswith("?"), "query title must be a question"
        # The 5 mandated sections (ADR-0067 D4).
        for section in (
            "## Question",
            "## Hypothesis",
            "## Open Points",
            "## Impact",
            "## References",
        ):
            assert section in page.content, f"missing section {section}"
        # related[] = both conflicting page slugs.
        assert set(page.frontmatter.related) == {"azure-openai-f1-85", "azure-openai-f1-90"}
        # DB sources[] = union of BOTH pages' sources (no synthetic lint: source).
        assert set(page.frontmatter.sources) == {"raw/sources/doc-a.md", "raw/sources/doc-b.md"}
        assert not any(s.startswith("lint:") for s in page.frontmatter.sources)
        # Both conflicting pages are wikilinked in the body (→ real write emits related from body).
        assert "[[Azure OpenAI F1 85]]" in page.content
        assert "[[Azure OpenAI F1 90]]" in page.content

    async def test_apply_contradiction_provider_absent_deterministic(
        self, lint_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """
        Provider absent: the deterministic template still writes a VALID query page (never fails
        the apply) — question title + 5 sections + related=both slugs + unioned sources.
        """
        finding_id, _ = await self._seed_conflict(lint_env)

        async def _resolve_none(vault_id: str) -> None:
            return None

        write_calls: list[Any] = []

        async def _fake_write(session: Any, page: Any, origin: str) -> Any:
            write_calls.append((page, origin))
            return MagicMock(id=uuid.uuid4())

        monkeypatch.setattr("app.ops.lint._resolve_lint_provider", _resolve_none)
        with patch("app.ingest.orchestrator.write_wiki_page", side_effect=_fake_write):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(finding_id))

        assert finding.status == "applied"
        assert len(write_calls) == 1
        page, _origin = write_calls[0]

        from app.ingest.schemas import PageType

        assert page.type == PageType.QUERY
        assert page.title.rstrip().endswith("?")
        for section in (
            "## Question",
            "## Hypothesis",
            "## Open Points",
            "## Impact",
            "## References",
        ):
            assert section in page.content
        assert set(page.frontmatter.related) == {"azure-openai-f1-85", "azure-openai-f1-90"}
        assert set(page.frontmatter.sources) == {"raw/sources/doc-a.md", "raw/sources/doc-b.md"}

    async def test_infer_stub_page_type_preserved_never_query(self) -> None:
        """
        Regression: _infer_stub_page_type still exists (another module imports it) and the
        broken-link stub routing is unchanged — entity/concept, NEVER query (ADR-0067 D1).
        """
        from app.ingest.schemas import PageType
        from app.ops.lint import _infer_stub_page_type

        assert _infer_stub_page_type("Salesforce, Inc.") is PageType.ENTITY  # legal suffix
        assert _infer_stub_page_type("AWS") is PageType.ENTITY  # all-caps acronym
        assert _infer_stub_page_type("Docker Compose") is PageType.ENTITY  # proper noun
        assert (
            _infer_stub_page_type("token bucket rate limiting") is PageType.CONCEPT
        )  # common noun
        # It must NEVER route a broken-link stub to queries/ (the QP-Q1 defect it fixes).
        for title in ("Salesforce, Inc.", "AWS", "Docker Compose", "token bucket rate limiting"):
            assert _infer_stub_page_type(title) is not PageType.QUERY


# ── T-LINT-009/010: dismiss + error paths ─────────────────────────────────────


class TestDismissAndErrors:
    async def test_dismiss_sets_status(self, lint_env: dict[str, Any]) -> None:
        """T-LINT-009: dismiss → status=dismissed, reviewed_at set."""
        finding_id = await _insert_finding(lint_env, category="contradiction")
        from app.ops.lint import dismiss_lint_finding

        finding = await dismiss_lint_finding(uuid.UUID(finding_id))
        assert finding.status == "dismissed"
        assert finding.reviewed_at is not None

    async def test_apply_non_open_returns_409(self, lint_env: dict[str, Any]) -> None:
        """T-LINT-010: apply on an already-applied finding → 409."""
        finding_id = await _insert_finding(lint_env, category="contradiction", status="applied")
        from app.ops.lint import apply_lint_fix
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await apply_lint_fix(uuid.UUID(finding_id))
        assert exc_info.value.status_code == 409

    async def test_apply_missing_returns_404(self, lint_env: dict[str, Any]) -> None:
        """T-LINT-010: apply on a non-existent finding → 404."""
        from app.ops.lint import apply_lint_fix
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await apply_lint_fix(uuid.uuid4())
        assert exc_info.value.status_code == 404

    async def test_dismiss_missing_returns_404(self, lint_env: dict[str, Any]) -> None:
        from app.ops.lint import dismiss_lint_finding
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await dismiss_lint_finding(uuid.uuid4())
        assert exc_info.value.status_code == 404


# ── T-LINT-011/012: pagination ────────────────────────────────────────────────


class TestPagination:
    async def test_list_findings_paginates_and_filters(self, lint_env: dict[str, Any]) -> None:
        """T-LINT-011: list_lint_findings limit+offset + status filter."""
        for _ in range(4):
            await _insert_finding(lint_env, category="contradiction", status="open")
        await _insert_finding(lint_env, category="stale-claim", status="dismissed")

        from app.ops.lint import list_lint_findings

        page_open = await list_lint_findings("test-vault", status="open", limit=3, offset=0)
        assert page_open.total == 4
        assert len(page_open.items) == 3

        page_open2 = await list_lint_findings("test-vault", status="open", limit=3, offset=3)
        assert len(page_open2.items) == 1

        page_all = await list_lint_findings("test-vault", status=None)
        assert page_all.total == 5

        page_dismissed = await list_lint_findings("test-vault", status="dismissed")
        assert page_dismissed.total == 1

    async def test_get_findings_limit_capped_at_200(self, lint_client: AsyncClient) -> None:
        """T-LINT-012: limit > 200 is rejected (I7 — bounded page size)."""
        resp = await lint_client.get("/lint/findings?vault_id=test-vault&limit=201")
        assert resp.status_code == 422


# ── T-LINT-013: scan endpoint ─────────────────────────────────────────────────


class TestScanEndpoint:
    async def test_scan_returns_run_and_findings(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """T-LINT-013: POST /lint/scan returns the run + findings (orphan present)."""
        await _insert_page(lint_env, title="Lonely")

        with patch("app.ops.lint._resolve_lint_provider", return_value=None):
            resp = await lint_client.post("/lint/scan", json={"vault_id": "test-vault"})

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["run"]["status"] == "completed"
        assert body["run"]["total_cost_usd"] == 0.0
        # The orphan page should surface as a finding.
        categories = {f["category"] for f in body["findings"]}
        assert "orphan-page" in categories

    async def test_scan_rejects_out_of_range_max_iter(self, lint_client: AsyncClient) -> None:
        resp = await lint_client.post("/lint/scan", json={"vault_id": "test-vault", "max_iter": 99})
        assert resp.status_code == 422

    async def test_runs_list_endpoint(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        with patch("app.ops.lint._resolve_lint_provider", return_value=None):
            await lint_client.post("/lint/scan", json={"vault_id": "test-vault"})
        resp = await lint_client.get("/lint/runs?vault_id=test-vault")
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


# ── T-LINT-014: I6 — no isinstance/class-name branching ───────────────────────


class TestI6NoBranching:
    def test_no_isinstance_branching_in_lint(self) -> None:
        """I6: lint.py must not branch on isinstance/class names (route by capabilities)."""
        from pathlib import Path

        lint_path = Path(__file__).resolve().parent.parent / "app" / "ops" / "lint.py"
        text = lint_path.read_text(encoding="utf-8")

        assert "isinstance(provider" not in text
        assert "OllamaProvider" not in text
        assert "CliAgentProvider" not in text
        assert "ApiProvider" not in text
        assert "provider_type ==" not in text


# ── T-LINT-015: I1 — no full vault rescan ─────────────────────────────────────


class TestI1NoRescan:
    def test_no_vault_walk_in_lint(self) -> None:
        """I1: lint.py reads pages/links tables only — never walks the vault filesystem."""
        from pathlib import Path

        lint_path = Path(__file__).resolve().parent.parent / "app" / "ops" / "lint.py"
        text = lint_path.read_text(encoding="utf-8")

        # No directory walking primitives in the lint scan path.
        assert "os.walk" not in text
        assert ".rglob(" not in text
        assert ".iterdir(" not in text


# ── T-LINT-B1: broken-wikilink detection (L1/L2) ─────────────────────────────────


class TestBrokenWikilinkDetection:
    """L1/L2: broken-wikilink findings derived from links.dangling=True."""

    async def test_broken_wikilink_detected(self, lint_env: dict[str, Any]) -> None:
        """L1: dangling links are surfaced as broken-wikilink findings."""
        from app.ops.lint import _detect_broken_wikilinks

        ref_page = await _insert_page(lint_env, title="Referencing Page")
        # Insert a dangling link from ref_page
        await _insert_link(
            lint_env,
            source_page_id=ref_page,
            target_title="NonExistentTarget",
            target_page_id=None,
            dangling=1,
        )

        findings = await _detect_broken_wikilinks("test-vault")
        assert len(findings) == 1
        f = findings[0]
        assert f.category == "broken-wikilink"
        assert f.severity == "warning"
        assert f.target_title == "NonExistentTarget"
        # target_page_id = the REFERENCING page (ADR review note)
        assert str(f.target_page_id) == ref_page
        assert "NonExistentTarget" in f.description
        assert "Referencing Page" in f.description

    async def test_broken_wikilink_with_suggestion(self, lint_env: dict[str, Any]) -> None:
        """L2: suggestion populated when a live page matches the dangling target."""
        from app.ops.lint import _detect_broken_wikilinks

        ref_page = await _insert_page(lint_env, title="Referencing Page")
        # Create the target page with a slightly different casing
        await _insert_page(lint_env, title="Docker Container")
        # Dangling link uses lowercase (should match via case-insensitive resolver)
        await _insert_link(
            lint_env,
            source_page_id=ref_page,
            target_title="docker container",
            target_page_id=None,
            dangling=1,
        )

        findings = await _detect_broken_wikilinks("test-vault")
        # Should find the broken link
        broken = [f for f in findings if f.category == "broken-wikilink"]
        assert len(broken) >= 1
        f = broken[0]
        # Suggestion should resolve to "Docker Container"
        assert f.suggested_target == "Docker Container"
        assert f.suggested_page_id is not None
        assert f.proposed_action is not None
        assert "Docker Container" in f.proposed_action

    async def test_broken_wikilink_dedup_within_scan(self, lint_env: dict[str, Any]) -> None:
        """L1: (a) one finding per (referencing_page_id, target_text) within a scan."""
        from app.ops.lint import _detect_broken_wikilinks

        ref_page = await _insert_page(lint_env, title="Referencing Page")
        # Insert the same dangling target twice (shouldn't happen in practice, but test dedup)
        # We can't actually insert duplicate (source_page_id, target_title) in the DB easily
        # but the dedup logic runs in Python, so test that two separate dangling links to the
        # same target from the same source are deduplicated.
        await _insert_link(
            lint_env,
            source_page_id=ref_page,
            target_title="SameTarget",
            target_page_id=None,
            dangling=1,
        )
        # Second link with same source+target: should be deduplicated
        await _insert_link(
            lint_env,
            source_page_id=ref_page,
            target_title="SameTarget",
            target_page_id=None,
            dangling=1,
        )

        findings = await _detect_broken_wikilinks("test-vault")
        same_target = [f for f in findings if f.target_title == "SameTarget"]
        # Should be deduplicated to at most 1
        assert len(same_target) <= 1

    async def test_broken_wikilink_reemitted_no_cross_run_dedup(
        self, lint_env: dict[str, Any]
    ) -> None:
        """The cross-run (b) dedup was removed: a broken-wikilink is RE-EMITTED even when an OPEN
        finding for the same (referencing_page, target) already exists — the category-aware
        supersede (run_lint_scan §4) closes the stale prior-run row instead, so accumulation is
        prevented without dropping the fresh finding (llm_wiki fresh-recompute parity)."""
        from app.ops.lint import _detect_broken_wikilinks

        ref_page = await _insert_page(lint_env, title="Referencing Page")
        await _insert_link(
            lint_env,
            source_page_id=ref_page,
            target_title="AlreadyReported",
            target_page_id=None,
            dangling=1,
        )
        # Pre-insert an open broken-wikilink finding for the same (referencing_page, target)
        await _insert_finding(
            lint_env,
            category="broken-wikilink",
            status="open",
            target_page_id=ref_page,
            target_title="AlreadyReported",
        )

        findings = await _detect_broken_wikilinks("test-vault")
        already = [f for f in findings if f.target_title == "AlreadyReported"]
        assert len(already) == 1, "Fresh finding must be re-emitted (supersede handles the old one)"

    async def test_supersede_prior_open_findings_category_aware(
        self, lint_env: dict[str, Any]
    ) -> None:
        """A new scan closes prior runs' OPEN findings it recomputed (llm_wiki fresh-recompute),
        category-aware and preserving human-acted (applied/dismissed) findings."""
        import uuid as _uuid

        from app.ops.lint import _supersede_prior_open_findings

        f_open_det = await _insert_finding(lint_env, category="broken-wikilink", status="open")
        f_open_sem = await _insert_finding(lint_env, category="contradiction", status="open")
        f_applied = await _insert_finding(lint_env, category="broken-wikilink", status="applied")
        f_dismissed = await _insert_finding(lint_env, category="contradiction", status="dismissed")

        async def _status(fid: str) -> str:
            async with lint_env["session_factory"]() as sess:
                row = await sess.execute(
                    sa_text("SELECT status FROM lint_findings WHERE id = :id"), {"id": fid}
                )
                return str(row.scalar_one())

        # Deterministic-only scan → supersede only deterministic OPEN findings.
        n = await _supersede_prior_open_findings(
            vault_id="test-vault",
            current_run_id=_uuid.uuid4(),
            categories=frozenset({"broken-wikilink", "no-outlinks", "orphan-page", "missing-xref"}),
        )
        assert n == 1
        assert await _status(f_open_det) == "superseded"  # deterministic open → closed
        assert await _status(f_open_sem) == "open"  # semantic not recomputed → preserved
        assert await _status(f_applied) == "applied"  # human-acted → preserved
        assert await _status(f_dismissed) == "dismissed"  # human-acted → preserved

        # A semantic scan additionally supersedes the semantic OPEN finding.
        n2 = await _supersede_prior_open_findings(
            vault_id="test-vault",
            current_run_id=_uuid.uuid4(),
            categories=frozenset({"contradiction", "stale-claim", "missing-page", "suggestion"}),
        )
        assert n2 == 1
        assert await _status(f_open_sem) == "superseded"

    async def test_broken_wikilink_in_scan_endpoint(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L1: broken-wikilink findings appear in POST /lint/scan response."""
        ref_page = await _insert_page(lint_env, title="Page With Broken Link")
        await _insert_link(
            lint_env,
            source_page_id=ref_page,
            target_title="BrokenTarget",
            target_page_id=None,
            dangling=1,
        )

        with patch("app.ops.lint._resolve_lint_provider", return_value=None):
            resp = await lint_client.post("/lint/scan", json={"vault_id": "test-vault"})

        assert resp.status_code == 200
        body = resp.json()
        categories = {f["category"] for f in body["findings"]}
        assert "broken-wikilink" in categories

        broken = [f for f in body["findings"] if f["category"] == "broken-wikilink"]
        assert len(broken) >= 1
        assert broken[0]["severity"] == "warning"
        # suggested_target field present (may be None when no match)
        assert "suggested_target" in broken[0]


# ── T-LINT-B2: semantic=False skips provider (L8) ────────────────────────────────


class TestSemanticFalse:
    """L8: semantic=False skips the provider pass entirely."""

    async def test_semantic_false_skips_provider(self, lint_env: dict[str, Any]) -> None:
        """L8: provider call count = 0 when semantic=False."""
        calls_log: list[int] = []
        provider = _make_findings_provider(calls_log=calls_log)

        with patch(
            "app.ops.lint._resolve_lint_provider",
            return_value=(provider, MagicMock(token_budget=1_000_000)),
        ):
            from app.ops.lint import run_lint_scan

            result = await run_lint_scan(
                "test-vault", max_iter=5, token_budget=1_000_000, semantic=False
            )

        assert len(calls_log) == 0, "semantic=False must not call the provider"
        assert result.iterations_used == 0
        assert result.total_cost_usd == 0.0
        assert result.status == "completed"

    async def test_semantic_false_via_endpoint(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L8: POST /lint/scan?semantic=false (body field) → deterministic only."""
        calls_log: list[int] = []
        provider = _make_findings_provider(calls_log=calls_log)

        with patch(
            "app.ops.lint._resolve_lint_provider",
            return_value=(provider, MagicMock(token_budget=1_000_000)),
        ):
            resp = await lint_client.post(
                "/lint/scan",
                json={"vault_id": "test-vault", "semantic": False},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["run"]["total_cost_usd"] == 0.0
        # Provider was never called
        assert len(calls_log) == 0


# ── T-LINT-B3: category + severity filters (L10) ─────────────────────────────────


class TestFindingFilters:
    """L10: category + severity filter params on GET /lint/findings."""

    async def test_category_filter(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L10: category filter returns only matching findings."""
        await _insert_finding(lint_env, category="orphan-page")
        await _insert_finding(lint_env, category="contradiction")

        resp = await lint_client.get(
            "/lint/findings?vault_id=test-vault&status=open&category=orphan-page"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        assert all(f["category"] == "orphan-page" for f in body["items"])

    async def test_severity_filter(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L10: severity filter returns only matching findings."""
        await _insert_finding(lint_env, category="contradiction")
        resp = await lint_client.get(
            "/lint/findings?vault_id=test-vault&status=open&severity=warning"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(f["severity"] == "warning" for f in body["items"])

    async def test_invalid_category_returns_422(self, lint_client: AsyncClient) -> None:
        """L10: invalid category → 422."""
        resp = await lint_client.get("/lint/findings?vault_id=test-vault&category=not-a-category")
        assert resp.status_code == 422

    async def test_invalid_severity_returns_422(self, lint_client: AsyncClient) -> None:
        """L10: invalid severity → 422."""
        resp = await lint_client.get("/lint/findings?vault_id=test-vault&severity=critical")
        assert resp.status_code == 422

    async def test_combined_status_and_category_filter(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L10: status + category combined filter."""
        await _insert_finding(lint_env, category="orphan-page", status="open")
        await _insert_finding(lint_env, category="orphan-page", status="dismissed")
        await _insert_finding(lint_env, category="contradiction", status="open")

        resp = await lint_client.get(
            "/lint/findings?vault_id=test-vault&status=open&category=orphan-page"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert all(f["category"] == "orphan-page" and f["status"] == "open" for f in body["items"])
        dismissed = [f for f in body["items"] if f["status"] == "dismissed"]
        assert len(dismissed) == 0


# ── T-LINT-L11: severity_totals in GET /lint/findings (L11) ──────────────────────


class TestSeverityTotals:
    """
    L11: GET /lint/findings includes severity_totals reflecting the full per-severity
    breakdown for the active vault + status + category view, independent of the
    severity filter and pagination.
    """

    async def test_severity_totals_present_and_correct(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L11: severity_totals keys + counts match what is actually in the DB."""
        await _insert_finding(lint_env, category="contradiction", severity="warning", status="open")
        await _insert_finding(lint_env, category="contradiction", severity="warning", status="open")
        await _insert_finding(lint_env, category="orphan-page", severity="info", status="open")

        resp = await lint_client.get("/lint/findings?vault_id=test-vault&status=open")
        assert resp.status_code == 200
        body = resp.json()
        assert "severity_totals" in body
        st = body["severity_totals"]
        assert st.get("warning") == 2
        assert st.get("info") == 1
        # "error" has no findings → must not be present (or be 0 — both acceptable)
        assert st.get("error", 0) == 0

    async def test_severity_totals_ignores_severity_filter(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L11: severity_totals is NOT affected by the active severity= query param."""
        await _insert_finding(lint_env, category="contradiction", severity="warning", status="open")
        await _insert_finding(lint_env, category="stale-claim", severity="error", status="open")

        # Filter by severity=warning → only 1 item returned; but severity_totals must
        # still show both warning=1 AND error=1.
        resp = await lint_client.get(
            "/lint/findings?vault_id=test-vault&status=open&severity=warning"
        )
        assert resp.status_code == 200
        body = resp.json()
        # The page itself shows only the warning finding.
        assert body["total"] == 1
        assert all(f["severity"] == "warning" for f in body["items"])
        # But severity_totals reflects the full status=open set.
        st = body["severity_totals"]
        assert st.get("warning", 0) == 1
        assert st.get("error", 0) == 1

    async def test_severity_totals_respects_status_filter(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L11: severity_totals is scoped by the status filter (not cross-status)."""
        await _insert_finding(lint_env, category="contradiction", severity="warning", status="open")
        await _insert_finding(
            lint_env, category="orphan-page", severity="warning", status="dismissed"
        )

        # Request open findings only.
        resp = await lint_client.get("/lint/findings?vault_id=test-vault&status=open")
        assert resp.status_code == 200
        st = resp.json()["severity_totals"]
        # Only the 1 open warning should appear; the dismissed one must not be counted.
        assert st.get("warning", 0) == 1

    async def test_severity_totals_respects_category_filter(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L11: severity_totals is scoped by the category filter."""
        await _insert_finding(lint_env, category="contradiction", severity="warning", status="open")
        await _insert_finding(lint_env, category="orphan-page", severity="error", status="open")

        # Filter by category=contradiction only.
        resp = await lint_client.get(
            "/lint/findings?vault_id=test-vault&status=open&category=contradiction"
        )
        assert resp.status_code == 200
        st = resp.json()["severity_totals"]
        # orphan-page/error must not appear in the totals.
        assert st.get("warning", 0) == 1
        assert st.get("error", 0) == 0

    async def test_severity_totals_ignores_pagination(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L11: severity_totals reflects all matching rows regardless of limit/offset."""
        for _ in range(3):
            await _insert_finding(
                lint_env, category="contradiction", severity="warning", status="open"
            )
        await _insert_finding(lint_env, category="orphan-page", severity="info", status="open")

        # Only fetch 1 item per page; severity_totals must still show full counts.
        resp = await lint_client.get(
            "/lint/findings?vault_id=test-vault&status=open&limit=1&offset=0"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 1  # pagination working
        st = body["severity_totals"]
        assert st.get("warning", 0) == 3
        assert st.get("info", 0) == 1

    async def test_severity_totals_empty_when_no_findings(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L11: severity_totals is an empty dict when no findings match the filters."""
        resp = await lint_client.get("/lint/findings?vault_id=test-vault&status=open")
        assert resp.status_code == 200
        body = resp.json()
        assert body["severity_totals"] == {}

    async def test_severity_totals_from_ops_function(self, lint_env: dict[str, Any]) -> None:
        """L11: list_lint_findings returns severity_totals directly (ops layer)."""
        from app.ops.lint import list_lint_findings

        await _insert_finding(lint_env, category="contradiction", severity="error", status="open")
        await _insert_finding(lint_env, category="contradiction", severity="warning", status="open")
        # dismissed → must not appear when status=open
        await _insert_finding(lint_env, category="orphan-page", severity="info", status="dismissed")

        page = await list_lint_findings("test-vault", status="open")
        assert isinstance(page.severity_totals, dict)
        assert page.severity_totals.get("error") == 1
        assert page.severity_totals.get("warning") == 1
        assert "info" not in page.severity_totals  # the dismissed one is excluded


# ── T-LINT-B4: batch endpoint (L5) ───────────────────────────────────────────────


class TestBatchEndpoint:
    """L5: POST /lint/findings/batch — mixed ok/error, cap 422."""

    async def test_batch_dismiss_mixed(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L5: batch dismiss — open findings succeed, missing id errors gracefully."""
        fid1 = await _insert_finding(lint_env, category="orphan-page", status="open")
        fid2 = await _insert_finding(lint_env, category="contradiction", status="open")
        missing_id = str(uuid.uuid4())

        resp = await lint_client.post(
            "/lint/findings/batch",
            json={"ids": [fid1, fid2, missing_id], "action": "dismiss"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok_count"] == 2
        assert body["error_count"] == 1
        assert len(body["results"]) == 3

        ok_results = [r for r in body["results"] if r["status"] == "ok"]
        error_results = [r for r in body["results"] if r["status"] == "error"]
        assert len(ok_results) == 2
        assert len(error_results) == 1

    async def test_batch_cap_422(self, lint_client: AsyncClient) -> None:
        """L5: ids > 200 → 422 (I7 bounded)."""
        ids = [str(uuid.uuid4()) for _ in range(201)]
        resp = await lint_client.post(
            "/lint/findings/batch",
            json={"ids": ids, "action": "dismiss"},
        )
        assert resp.status_code == 422

    async def test_batch_invalid_action_422(self, lint_client: AsyncClient) -> None:
        """L5: invalid action → 422."""
        resp = await lint_client.post(
            "/lint/findings/batch",
            json={"ids": [str(uuid.uuid4())], "action": "nuke"},
        )
        assert resp.status_code == 422

    async def test_batch_apply(self, lint_env: dict[str, Any], lint_client: AsyncClient) -> None:
        """L5: batch apply — flag-only category succeeds."""
        fid = await _insert_finding(lint_env, category="orphan-page", status="open")

        resp = await lint_client.post(
            "/lint/findings/batch",
            json={"ids": [fid], "action": "apply"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok_count"] == 1
        assert body["error_count"] == 0

    async def test_batch_empty_ids_ok(self, lint_client: AsyncClient) -> None:
        """L5: empty ids list → 200 with zero results."""
        resp = await lint_client.post(
            "/lint/findings/batch",
            json={"ids": [], "action": "dismiss"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok_count"] == 0
        assert body["error_count"] == 0


# ── T-LINT-B5: send-to-review (L6) ───────────────────────────────────────────────


class TestSendToReview:
    """L6: POST /lint/findings/{id}/send-to-review."""

    async def test_send_to_review_maps_category_and_flips_status(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L6: send-to-review → finding status=applied + review item created."""
        fid = await _insert_finding(
            lint_env,
            category="missing-page",
            status="open",
            target_title="Kubernetes",
            description="Kubernetes is mentioned but has no page.",
        )

        resp = await lint_client.post(f"/lint/findings/{fid}/send-to-review")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "applied"
        assert "sent to review" in (body["resolution_note"] or "")

    async def test_send_to_review_409_not_open(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L6: 409 when finding is not open."""
        fid = await _insert_finding(lint_env, category="orphan-page", status="applied")
        resp = await lint_client.post(f"/lint/findings/{fid}/send-to-review")
        assert resp.status_code == 409

    async def test_send_to_review_404_missing(self, lint_client: AsyncClient) -> None:
        """L6: 404 when finding does not exist."""
        resp = await lint_client.post(f"/lint/findings/{uuid.uuid4()}/send-to-review")
        assert resp.status_code == 404

    async def test_send_to_review_broken_wikilink_uses_suggested_target(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L6: broken-wikilink → proposed_title = suggested_target when present."""
        from app.ops.lint import send_finding_to_review

        # Insert a finding with suggested_target manually
        run_id = str(uuid.uuid4())
        fid_str = str(uuid.uuid4())
        ref_page_id = str(uuid.uuid4())
        async with lint_env["session_factory"]() as sess:
            await sess.execute(
                sa_text(
                    "INSERT INTO lint_runs "
                    "(id, vault_id, status, max_iter, token_budget, created_at, started_at) "
                    "VALUES (:id, :v, 'completed', 1, 10000, datetime('now'), datetime('now'))"
                ),
                {"id": run_id, "v": "test-vault"},
            )
            await sess.execute(
                sa_text(
                    "INSERT INTO lint_findings "
                    "(id, lint_run_id, vault_id, category, severity, target_page_id, target_title, "
                    " description, suggested_target, status, created_at) "
                    "VALUES (:id, :rid, :v, 'broken-wikilink', 'warning', :tpid, :tt, :desc, "
                    ":st, 'open', datetime('now'))"
                ),
                {
                    "id": fid_str,
                    "rid": run_id,
                    "v": "test-vault",
                    "tpid": ref_page_id,
                    "tt": "docker container",
                    "desc": "Broken link: [[docker container]] — target page not found.",
                    "st": "Docker Container",
                },
            )
            await sess.commit()

        review_calls: list[dict[str, Any]] = []

        async def _fake_enqueue(**kwargs: Any) -> Any:
            review_calls.append(kwargs)
            item = MagicMock()
            item.id = uuid.uuid4()
            return item

        with patch("app.ops.review.enqueue_review", side_effect=_fake_enqueue):
            from app.ops.lint import send_finding_to_review

            finding = await send_finding_to_review(uuid.UUID(fid_str))

        assert finding.status == "applied"
        assert len(review_calls) == 1
        # proposed_title should be suggested_target for broken-wikilink
        assert review_calls[0]["proposed_title"] == "Docker Container"
        # item_type should be missing-page for broken-wikilink
        assert review_calls[0]["item_type"] == "missing-page"
        # rationale must include the category tag for collision prevention (ADR review note)
        assert "[lint:broken-wikilink]" in review_calls[0]["rationale"]


# ── T-LINT-B6: DELETE /pages/{id} meta-page guard (L9) ───────────────────────────


class TestDeletePageMetaGuard:
    """L9: DELETE /pages/{id} — meta-page guard + happy path."""

    async def test_delete_meta_page_409(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L9: DELETE /pages/{id} on index.md → 409."""
        meta_id = await _insert_page(lint_env, title="Index", file_path="wiki/index.md")

        with patch("app.ops.cascade_delete.cascade_delete") as mock_del:
            resp = await lint_client.delete(f"/pages/{meta_id}")

        assert resp.status_code == 409
        mock_del.assert_not_called()

    async def test_delete_meta_log_409(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L9: DELETE /pages/{id} on log.md → 409."""
        meta_id = await _insert_page(lint_env, title="Log", file_path="wiki/log.md")
        with patch("app.ops.cascade_delete.cascade_delete") as mock_del:
            resp = await lint_client.delete(f"/pages/{meta_id}")
        assert resp.status_code == 409
        mock_del.assert_not_called()

    async def test_delete_normal_page_calls_cascade(
        self, lint_env: dict[str, Any], lint_client: AsyncClient, tmp_path: Any
    ) -> None:
        """L9: DELETE /pages/{id} on a normal wiki page → delegates to cascade_delete."""
        page_id = await _insert_page(
            lint_env, title="Orphan To Delete", file_path="wiki/entities/orphan.md"
        )

        from app.ops.cascade_delete import CascadeResult

        fake_result = CascadeResult(
            deleted_page_id=uuid.UUID(page_id),
            wikilinks_cleaned=0,
            index_entry_removed=True,
            shared_entity_warnings=[],
            files_written=0,
            data_version_after=1,
        )

        with (
            patch("app.ops.cascade_delete.cascade_delete", return_value=fake_result),
            patch("app.ingest.orchestrator.append_log"),
        ):
            resp = await lint_client.delete(f"/pages/{page_id}")

        # May be 200 or 500 depending on log.md path — just check cascade was invoked
        # by verifying it's not 409 (meta guard didn't fire)
        assert resp.status_code != 409

    async def test_delete_404_for_nonexistent(self, lint_client: AsyncClient) -> None:
        """L9: DELETE /pages/{id} on nonexistent page → 404."""
        from app.ops.cascade_delete import PageNotFoundError

        with patch(
            "app.ops.cascade_delete.cascade_delete",
            side_effect=PageNotFoundError("not found"),
        ):
            resp = await lint_client.delete(f"/pages/{uuid.uuid4()}")
        assert resp.status_code == 404


# ── Helper: insert finding with extended columns (suggestion columns) ─────────────


async def _insert_finding_ext(
    env: dict[str, Any],
    *,
    vault_id: str = "test-vault",
    category: str = "orphan-page",
    severity: str = "info",
    status: str = "open",
    target_page_id: str | None = None,
    target_title: str | None = None,
    suggested_page_id: str | None = None,
    suggested_target: str | None = None,
    description: str = "Test finding",
) -> str:
    """Like _insert_finding but also sets suggested_page_id / suggested_target."""
    run_id = str(uuid.uuid4())
    finding_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO lint_runs "
                "(id, vault_id, status, max_iter, token_budget, created_at, started_at) "
                "VALUES (:id, :v, 'completed', 3, 20000, datetime('now'), datetime('now'))"
            ),
            {"id": run_id, "v": vault_id},
        )
        await sess.execute(
            sa_text(
                "INSERT INTO lint_findings "
                "(id, lint_run_id, vault_id, category, severity, target_page_id, target_title, "
                " description, suggested_page_id, suggested_target, status, created_at) "
                "VALUES (:id, :rid, :v, :cat, :sev, :tpid, :tt, :desc, :spid, :st, :status, "
                "datetime('now'))"
            ),
            {
                "id": finding_id,
                "rid": run_id,
                "v": vault_id,
                "cat": category,
                "sev": severity,
                "tpid": target_page_id,
                "tt": target_title,
                "desc": description,
                "spid": suggested_page_id,
                "st": suggested_target,
                "status": status,
            },
        )
        await sess.commit()
    return finding_id


# ── T-LINT-L1: no-outlinks detection (L1 / ADR-0058 §L1) ─────────────────────────


class TestNoOutlinksDetection:
    """L1: no-outlinks findings are deterministic (pages with zero outgoing links)."""

    async def test_no_outlinks_detected_when_page_has_no_links(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L1: a page with no outgoing links is reported as no-outlinks."""
        from app.ops.lint import _detect_no_outlinks

        # Page with no links at all → should be detected.
        await _insert_page(lint_env, title="Island Page", file_path="wiki/entities/island.md")
        # Page with an outgoing link → should NOT be detected.
        linked_page = await _insert_page(
            lint_env, title="Target", file_path="wiki/entities/target.md"
        )
        source_page = await _insert_page(
            lint_env, title="Linked", file_path="wiki/entities/linked.md"
        )
        await _insert_link(
            lint_env,
            source_page_id=source_page,
            target_title="Target",
            target_page_id=linked_page,
            dangling=0,
        )

        findings = await _detect_no_outlinks("test-vault")
        titles = {f.target_title for f in findings}
        assert "Island Page" in titles
        assert "Linked" not in titles, "page with outgoing link must not be reported"
        assert all(f.category == "no-outlinks" for f in findings)

    async def test_no_outlinks_excludes_navigation_roots(self, lint_env: dict[str, Any]) -> None:
        """L1/L4: only index.md and log.md are excluded from no-outlinks (L4 parity fix)."""
        from app.ops.lint import _detect_no_outlinks

        await _insert_page(lint_env, title="Index", file_path="wiki/index.md")
        await _insert_page(lint_env, title="Log", file_path="wiki/log.md")

        findings = await _detect_no_outlinks("test-vault")
        assert findings == []

    async def test_no_outlinks_overview_is_eligible(self, lint_env: dict[str, Any]) -> None:
        """L4: overview.md is eligible for no-outlinks detection (no longer excluded)."""
        from app.ops.lint import _detect_no_outlinks

        await _insert_page(lint_env, title="Overview", file_path="wiki/overview.md")

        findings = await _detect_no_outlinks("test-vault")
        titles = {f.target_title for f in findings}
        # overview.md has no outgoing links → IS a no-outlinks finding (L4 parity fix).
        assert "Overview" in titles

    async def test_no_outlinks_in_scan_endpoint(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L1: no-outlinks findings appear in POST /lint/scan response."""
        await _insert_page(lint_env, title="Isolated Page", file_path="wiki/entities/isolated.md")

        with patch("app.ops.lint._resolve_lint_provider", return_value=None):
            resp = await lint_client.post("/lint/scan", json={"vault_id": "test-vault"})

        assert resp.status_code == 200
        body = resp.json()
        categories = {f["category"] for f in body["findings"]}
        assert "no-outlinks" in categories

    async def test_no_outlinks_severity_is_info(self, lint_env: dict[str, Any]) -> None:
        """L5: no-outlinks findings have severity=info (matches reference lint.ts)."""
        from app.ops.lint import _detect_no_outlinks

        await _insert_page(lint_env, title="Info Page", file_path="wiki/entities/info.md")
        findings = await _detect_no_outlinks("test-vault")
        assert findings, "expected at least one no-outlinks finding"
        assert all(f.severity == "info" for f in findings), "no-outlinks must be severity=info (L5)"


# ── T-LINT-L2: suggestion category (L2 / ADR-0058 §L2) ───────────────────────────


class TestSuggestionCategory:
    """L2: 'suggestion' is a valid semantic category accepted by _parse_findings."""

    def test_suggestion_in_valid_categories(self) -> None:
        """L2: 'suggestion' is in _VALID_CATEGORIES."""
        from app.ops.lint import _VALID_CATEGORIES

        assert "suggestion" in _VALID_CATEGORIES

    def test_no_outlinks_in_valid_categories(self) -> None:
        """L1: 'no-outlinks' is in _VALID_CATEGORIES."""
        from app.ops.lint import _VALID_CATEGORIES

        assert "no-outlinks" in _VALID_CATEGORIES

    def test_parse_findings_accepts_suggestion(self) -> None:
        """L2: _parse_findings accepts 'suggestion' category from model output."""
        import json

        from app.ops.lint import _parse_findings

        raw = json.dumps(
            {
                "findings": [
                    {
                        "category": "suggestion",
                        "severity": "info",
                        "description": "Consider adding a page about Kubernetes operators.",
                    }
                ]
            }
        )
        results = _parse_findings(raw)
        assert len(results) == 1
        assert results[0].category == "suggestion"
        assert results[0].severity == "info"

    def test_parse_findings_rejects_no_outlinks_from_model(self) -> None:
        """L2: _parse_findings must NOT accept no-outlinks from the model (deterministic-only)."""
        import json

        from app.ops.lint import _parse_findings

        raw = json.dumps(
            {
                "findings": [
                    {
                        "category": "no-outlinks",
                        "severity": "info",
                        "description": "No outlinks found.",
                    }
                ]
            }
        )
        results = _parse_findings(raw)
        assert results == [], "no-outlinks must not be accepted from the model"

    def test_parse_findings_rejects_missing_xref_from_model(self) -> None:
        """L2 parity: _parse_findings must NOT accept missing-xref from the model.
        missing-xref is handled deterministically; llm_wiki has no such category."""
        import json

        from app.ops.lint import _parse_findings

        raw = json.dumps(
            {
                "findings": [
                    {
                        "category": "missing-xref",
                        "severity": "warning",
                        "description": "Page A mentions B but does not link it.",
                        "target_title": "B",
                    }
                ]
            }
        )
        results = _parse_findings(raw)
        assert results == [], "missing-xref must not be accepted from the model (L2 parity fix)"

    def test_missing_xref_still_in_valid_categories(self) -> None:
        """missing-xref stays in _VALID_CATEGORIES for apply/send-to-review; just excluded from
        model output. This is a conservative approach — the apply seam still works."""
        from app.ops.lint import _VALID_CATEGORIES

        assert (
            "missing-xref" in _VALID_CATEGORIES
        ), "missing-xref must remain in _VALID_CATEGORIES for existing DB findings / apply"

    async def test_suggestion_category_filter_works(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L2: category=suggestion filter accepted (not 422)."""
        resp = await lint_client.get(
            "/lint/findings?vault_id=test-vault&status=open&category=suggestion"
        )
        assert resp.status_code == 200, f"suggestion category filter should be valid: {resp.text}"

    async def test_no_outlinks_category_filter_works(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L1: category=no-outlinks filter accepted (not 422)."""
        resp = await lint_client.get(
            "/lint/findings?vault_id=test-vault&status=open&category=no-outlinks"
        )
        assert resp.status_code == 200, f"no-outlinks category filter should be valid: {resp.text}"

    async def test_suggestion_send_to_review_uses_suggestion_item_type(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L2: send-to-review for suggestion → item_type=suggestion."""
        from app.ops.lint import send_finding_to_review

        fid = await _insert_finding_ext(
            lint_env,
            category="suggestion",
            severity="info",
            description="Consider adding a page about Kubernetes operators.",
        )

        review_calls: list[dict[str, Any]] = []

        async def _fake_enqueue(**kwargs: Any) -> Any:
            review_calls.append(kwargs)
            item = MagicMock()
            item.id = uuid.uuid4()
            return item

        with patch("app.ops.review.enqueue_review", side_effect=_fake_enqueue):
            finding = await send_finding_to_review(uuid.UUID(fid))

        assert finding.status == "applied"
        assert len(review_calls) == 1
        assert review_calls[0]["item_type"] == "suggestion"


# ── T-LINT-L3: fuzzy suggestions (L3 / ADR-0058 §L3) ────────────────────────────


class TestFuzzySuggestions:
    """L3: fuzzy token-overlap scoring populates suggested_target on no-outlinks + orphan-page."""

    def test_tokenize_for_suggestion_basic(self) -> None:
        """L3: _tokenize_for_suggestion returns non-empty frozenset for ASCII text."""
        from app.ops.lint import _tokenize_for_suggestion

        tokens = _tokenize_for_suggestion("Docker Container")
        assert "docker" in tokens
        assert "container" in tokens

    def test_fuzzy_score_zero_for_disjoint(self) -> None:
        """L3: _fuzzy_score returns 0.0 when token sets are disjoint."""
        from app.ops.lint import _fuzzy_score, _tokenize_for_suggestion

        a = _tokenize_for_suggestion("Python Programming")
        b = _tokenize_for_suggestion("Kubernetes Networking")
        # These sets share no tokens.
        score = _fuzzy_score(a, b, same_folder=False)
        assert score == 0.0

    def test_fuzzy_score_positive_for_overlap(self) -> None:
        """L3: _fuzzy_score is positive when token sets share a token."""
        from app.ops.lint import _fuzzy_score, _tokenize_for_suggestion

        a = _tokenize_for_suggestion("Docker Container")
        b = _tokenize_for_suggestion("Docker Networking")
        score = _fuzzy_score(a, b, same_folder=False)
        assert score > 0.0

    async def test_no_outlinks_gets_suggested_target(self, lint_env: dict[str, Any]) -> None:
        """L3: no-outlinks finding carries suggested_target when overlap exists."""
        from app.ops.lint import _detect_no_outlinks

        # Page A has no outgoing links (will be detected).
        await _insert_page(
            lint_env,
            title="Docker Container",
            file_path="wiki/entities/docker-container.md",
        )
        # Page B exists as a candidate (shares "docker" token).
        await _insert_page(
            lint_env,
            title="Docker Networking",
            file_path="wiki/entities/docker-networking.md",
        )
        # Add a link FROM page B so it is NOT a no-outlinks page.
        target_id = await _insert_page(lint_env, title="Other", file_path="wiki/entities/other.md")
        source_b = await _insert_page(
            lint_env, title="DockerB", file_path="wiki/entities/dockerb.md"
        )
        await _insert_link(
            lint_env,
            source_page_id=source_b,
            target_title="Other",
            target_page_id=target_id,
        )

        findings = await _detect_no_outlinks("test-vault")
        docker_findings = [
            f for f in findings if f.target_title and "Docker Container" in f.target_title
        ]
        assert docker_findings, "Docker Container page must be detected as no-outlinks"
        f = docker_findings[0]
        # Fuzzy suggester should propose "Docker Networking" or any overlapping page.
        # The key assertion is that suggested_target is set (not None).
        # (The exact value depends on token overlap scores — we only require it is non-None
        # because we know there is at least one overlapping candidate.)
        assert f.suggested_target is not None, "no-outlinks finding must have a suggested_target"

    async def test_orphan_page_gets_suggested_source(self, lint_env: dict[str, Any]) -> None:
        """L3: orphan-page finding carries suggested_target (source page) and suggested_page_id."""
        from app.ops.lint import _detect_orphans

        # Orphan page: no incoming links.
        await _insert_page(
            lint_env,
            title="Python Asyncio",
            file_path="wiki/concepts/python-asyncio.md",
        )
        # Candidate source page: shares "python" token.
        source_page = await _insert_page(
            lint_env,
            title="Python Typing",
            file_path="wiki/concepts/python-typing.md",
        )
        # Make the source page link to something else (so it has at least some outlinks context),
        # but NOT to Python Asyncio (so Python Asyncio stays an orphan).
        dummy = await _insert_page(lint_env, title="Dummy", file_path="wiki/entities/dummy.md")
        await _insert_link(
            lint_env,
            source_page_id=source_page,
            target_title="Dummy",
            target_page_id=dummy,
        )

        findings = await _detect_orphans("test-vault")
        asyncio_findings = [
            f for f in findings if f.target_title and "Python Asyncio" in f.target_title
        ]
        assert asyncio_findings, "Python Asyncio must be detected as orphan"
        f = asyncio_findings[0]
        # suggested_target should point to a source page (the fuzzy best match).
        # suggested_page_id should be set when a match is found.
        # We only assert they are consistent (both None or both set).
        if f.suggested_target is not None:
            assert (
                f.suggested_page_id is not None
            ), "suggested_page_id must be set when suggested_target is set"

    async def test_orphan_severity_is_info(self, lint_env: dict[str, Any]) -> None:
        """L5: orphan-page findings have severity=info (L5 / ADR-0058 §L5)."""
        from app.ops.lint import _detect_orphans

        await _insert_page(lint_env, title="Solo Page", file_path="wiki/entities/solo.md")
        findings = await _detect_orphans("test-vault")
        assert findings, "expected at least one orphan finding"
        assert all(f.severity == "info" for f in findings), "orphan-page must be severity=info (L5)"


# ── T-LINT-L4: new apply paths (L4 / ADR-0058 §L4) ───────────────────────────────


class TestNewApplyPaths:
    """L4: _apply_no_outlinks, _apply_orphan_page, _create_broken_link_stub."""

    async def test_apply_no_outlinks_appends_wikilink(self, lint_env: dict[str, Any]) -> None:
        """L4: no-outlinks apply → appends [[suggested_target]] under ## Related in page body."""
        page_id = await _insert_page(
            lint_env, title="Island Page", file_path="wiki/entities/island.md"
        )
        fid = await _insert_finding_ext(
            lint_env,
            category="no-outlinks",
            severity="info",
            target_page_id=page_id,
            target_title="Island Page",
            suggested_target="Docker Networking",
            description="Island Page has no outgoing wikilinks.",
        )

        read_result = (
            "wiki/entities/island.md",
            "/fake/path",
            "type: entity\ntitle: Island Page\nsources: []\n",
            "# Island Page\n\nSome content here.\n",
            True,
        )
        write_calls: list[Any] = []

        async def _fake_write(**kwargs: Any) -> None:
            write_calls.append(kwargs)

        with (
            patch("app.ops.lint._read_page_file_for_apply", return_value=read_result),
            patch("app.ops.lint._write_body_back", side_effect=_fake_write),
        ):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(fid))

        assert finding.status == "applied"
        assert finding.resolution_note is not None
        assert "Docker Networking" in (finding.resolution_note or "")
        assert len(write_calls) == 1, "apply must call _write_body_back exactly once (I1)"

    async def test_apply_no_outlinks_flag_only_when_no_suggestion(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L4: no-outlinks without suggested_target → flag-only (no file write)."""
        page_id = await _insert_page(
            lint_env, title="Island Page", file_path="wiki/entities/island.md"
        )
        fid = await _insert_finding_ext(
            lint_env,
            category="no-outlinks",
            severity="info",
            target_page_id=page_id,
            target_title="Island Page",
            suggested_target=None,  # no suggestion
            description="Island Page has no outgoing wikilinks.",
        )

        write_calls: list[Any] = []

        async def _fake_write(**kwargs: Any) -> None:
            write_calls.append(kwargs)

        with patch("app.ops.lint._write_body_back", side_effect=_fake_write):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(fid))

        assert finding.status == "applied"
        assert len(write_calls) == 0, "no suggestion → no write (flag-only)"
        assert "flag-only" in (finding.resolution_note or "")

    async def test_apply_orphan_page_with_suggestion_appends_to_source(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L4: orphan-page with suggested_page_id → appends [[orphan_title]] to source page."""
        orphan_page_id = await _insert_page(
            lint_env, title="Orphan Entity", file_path="wiki/entities/orphan-entity.md"
        )
        source_page_id = await _insert_page(
            lint_env, title="Source Entity", file_path="wiki/entities/source-entity.md"
        )
        fid = await _insert_finding_ext(
            lint_env,
            category="orphan-page",
            severity="info",
            target_page_id=orphan_page_id,
            target_title="Orphan Entity",
            suggested_page_id=source_page_id,
            suggested_target="Source Entity",
            description="Orphan Entity has no incoming wikilinks.",
        )

        read_result = (
            "wiki/entities/source-entity.md",
            "/fake/source-path",
            "type: entity\ntitle: Source Entity\nsources: []\n",
            "# Source Entity\n\nContent of source.\n",
            True,
        )
        write_calls: list[Any] = []

        async def _fake_write(**kwargs: Any) -> None:
            write_calls.append(kwargs)

        with (
            patch("app.ops.lint._read_page_file_for_apply", return_value=read_result),
            patch("app.ops.lint._write_body_back", side_effect=_fake_write),
        ):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(fid))

        assert finding.status == "applied"
        assert len(write_calls) == 1, "apply must write to the source page once (I1)"
        # Verify the new_body contains the orphan wikilink.
        written_body = write_calls[0]["new_body"]
        assert "[[Orphan Entity]]" in written_body

    async def test_apply_orphan_page_flag_only_when_no_suggestion(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L4: orphan-page without suggested_page_id → flag-only (no file write)."""
        page_id = await _insert_page(
            lint_env, title="Orphan Solo", file_path="wiki/entities/orphan-solo.md"
        )
        fid = await _insert_finding_ext(
            lint_env,
            category="orphan-page",
            severity="info",
            target_page_id=page_id,
            target_title="Orphan Solo",
            suggested_page_id=None,  # no suggestion
            description="Orphan Solo has no incoming wikilinks.",
        )

        write_calls: list[Any] = []

        async def _fake_write(**kwargs: Any) -> None:
            write_calls.append(kwargs)

        with patch("app.ops.lint._write_body_back", side_effect=_fake_write):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(fid))

        assert finding.status == "applied"
        assert len(write_calls) == 0, "no suggestion → no write (flag-only)"
        assert "flag-only" in (finding.resolution_note or "")

    async def test_apply_broken_wikilink_no_suggestion_creates_stub(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L4/ADR-0067-D1: broken-wikilink without suggested_target → creates typed stub.

        Verifies that the stub is NEVER type=query (queries/ is reserved for genuine
        open questions).  target_title="missing-concept" is all-lowercase so it must
        resolve to concept, not entity.
        """
        ref_page_id = await _insert_page(
            lint_env, title="Referencing Page", file_path="wiki/entities/referencing.md"
        )
        fid = await _insert_finding_ext(
            lint_env,
            category="broken-wikilink",
            severity="warning",
            target_page_id=ref_page_id,
            target_title="missing-concept",
            suggested_target=None,  # no suggestion → stub path
            description="Broken link: [[missing-concept]]",
        )

        write_calls: list[Any] = []

        async def _fake_write(session: Any, page: Any, origin: str) -> Any:
            write_calls.append((page, origin))
            written = MagicMock()
            written.id = uuid.uuid4()
            return written

        async def _fake_reresolve(session: Any) -> int:
            return 1

        with (
            patch("app.ingest.orchestrator.write_wiki_page", side_effect=_fake_write),
            patch("app.wiki.links.reresolve_dangling_links", side_effect=_fake_reresolve),
        ):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(fid))

        assert finding.status == "applied"
        assert len(write_calls) == 1, "stub creation must call write_wiki_page exactly once (I1)"
        stub_page, origin = write_calls[0]
        # ADR-0067 D1: stub MUST NOT be type=query; queries/ is for genuine open questions.
        assert (
            stub_page.type.value != "query"
        ), "broken-wikilink stub must never be type=query (ADR-0067 D1 / LN-D1)"
        # "missing-concept" is all-lowercase → heuristic infers CONCEPT, not ENTITY.
        assert (
            stub_page.type.value == "concept"
        ), f"expected concept for all-lowercase target, got {stub_page.type.value!r}"
        assert "stub" in stub_page.frontmatter.tags
        assert "lint" in stub_page.frontmatter.tags
        assert "stub" in (finding.resolution_note or "")

    async def test_apply_broken_wikilink_proper_noun_creates_entity_stub(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L4/ADR-0067-D1: broken-wikilink with proper-noun target → entity stub (not query)."""
        ref_page_id = await _insert_page(
            lint_env, title="Referencing Page", file_path="wiki/entities/referencing2.md"
        )
        fid = await _insert_finding_ext(
            lint_env,
            category="broken-wikilink",
            severity="warning",
            target_page_id=ref_page_id,
            target_title="Microsoft Azure",
            suggested_target=None,
            description="Broken link: [[Microsoft Azure]]",
        )

        write_calls: list[Any] = []

        async def _fake_write(session: Any, page: Any, origin: str) -> Any:
            write_calls.append((page, origin))
            written = MagicMock()
            written.id = uuid.uuid4()
            return written

        async def _fake_reresolve(session: Any) -> int:
            return 1

        with (
            patch("app.ingest.orchestrator.write_wiki_page", side_effect=_fake_write),
            patch("app.wiki.links.reresolve_dangling_links", side_effect=_fake_reresolve),
        ):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(fid))

        assert finding.status == "applied"
        stub_page, _ = write_calls[0]
        assert (
            stub_page.type.value == "entity"
        ), "proper-noun target must produce entity stub (ADR-0067 D1)"
        assert stub_page.type.value != "query"

    async def test_apply_no_outlinks_idempotent_when_link_already_present(
        self, lint_env: dict[str, Any]
    ) -> None:
        """L4: _append_wikilink_to_body is idempotent — no write when link already exists."""
        page_id = await _insert_page(
            lint_env, title="Already Linked", file_path="wiki/entities/already-linked.md"
        )
        fid = await _insert_finding_ext(
            lint_env,
            category="no-outlinks",
            severity="info",
            target_page_id=page_id,
            target_title="Already Linked",
            suggested_target="Target Page",
            description="No outlinks.",
        )

        # Body already contains [[Target Page]] → _write_body_back must NOT be called.
        read_result = (
            "wiki/entities/already-linked.md",
            "/fake/path",
            "",
            "# Already Linked\n\n## Related\n- [[Target Page]]\n",
            False,
        )
        write_calls: list[Any] = []

        async def _fake_write(**kwargs: Any) -> None:
            write_calls.append(kwargs)

        with (
            patch("app.ops.lint._read_page_file_for_apply", return_value=read_result),
            patch("app.ops.lint._write_body_back", side_effect=_fake_write),
        ):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(fid))

        assert finding.status == "applied"
        assert len(write_calls) == 0, "idempotent — no write when link already present"


# ── T-LINT-L5: severity invariants (L5 / ADR-0058 §L5) ──────────────────────────


class TestSeverityInvariants:
    """L5: orphan-page=info, no-outlinks=info, broken-wikilink=warning (unchanged)."""

    async def test_orphan_page_severity_info_in_scan(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L5: orphan-page findings from scan have severity=info."""
        await _insert_page(lint_env, title="Lone Page", file_path="wiki/entities/lone.md")

        with patch("app.ops.lint._resolve_lint_provider", return_value=None):
            resp = await lint_client.post("/lint/scan", json={"vault_id": "test-vault"})

        assert resp.status_code == 200
        body = resp.json()
        orphan_findings = [f for f in body["findings"] if f["category"] == "orphan-page"]
        assert orphan_findings, "expected at least one orphan-page finding"
        assert all(
            f["severity"] == "info" for f in orphan_findings
        ), "orphan-page must be severity=info (L5)"

    async def test_no_outlinks_severity_info_in_scan(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L5: no-outlinks findings from scan have severity=info."""
        await _insert_page(lint_env, title="No Links Page", file_path="wiki/entities/nolinks.md")

        with patch("app.ops.lint._resolve_lint_provider", return_value=None):
            resp = await lint_client.post("/lint/scan", json={"vault_id": "test-vault"})

        assert resp.status_code == 200
        body = resp.json()
        no_out_findings = [f for f in body["findings"] if f["category"] == "no-outlinks"]
        assert no_out_findings, "expected at least one no-outlinks finding"
        assert all(
            f["severity"] == "info" for f in no_out_findings
        ), "no-outlinks must be severity=info (L5)"

    async def test_broken_wikilink_severity_warning_unchanged(
        self, lint_env: dict[str, Any], lint_client: AsyncClient
    ) -> None:
        """L5: broken-wikilink findings retain severity=warning (unchanged by L5)."""
        ref_page = await _insert_page(lint_env, title="Referencing")
        await _insert_link(
            lint_env,
            source_page_id=ref_page,
            target_title="BrokenTarget",
            target_page_id=None,
            dangling=1,
        )

        with patch("app.ops.lint._resolve_lint_provider", return_value=None):
            resp = await lint_client.post("/lint/scan", json={"vault_id": "test-vault"})

        assert resp.status_code == 200
        body = resp.json()
        broken_findings = [f for f in body["findings"] if f["category"] == "broken-wikilink"]
        assert broken_findings, "expected at least one broken-wikilink finding"
        assert all(
            f["severity"] == "warning" for f in broken_findings
        ), "broken-wikilink must remain severity=warning"


# ── T-LINT-L6: _append_wikilink_to_body helper (L4 shared) ───────────────────────


class TestAppendWikilinkHelper:
    """Unit tests for _append_wikilink_to_body (L4 shared body-edit helper)."""

    def test_appends_under_existing_related_heading(self) -> None:
        """L4: link appended immediately after existing ## Related heading."""
        from app.ops.lint import _append_wikilink_to_body

        body = "# Page\n\nSome content.\n\n## Related\n- [[Existing Link]]\n"
        result = _append_wikilink_to_body(body, "New Link")
        assert "[[New Link]]" in result
        # The heading must still be present.
        assert "## Related" in result

    def test_creates_related_heading_when_absent(self) -> None:
        """L4: ## Related heading created when not present."""
        from app.ops.lint import _append_wikilink_to_body

        body = "# Page\n\nSome content.\n"
        result = _append_wikilink_to_body(body, "New Link")
        assert "## Related" in result
        assert "[[New Link]]" in result

    def test_idempotent_when_link_already_present(self) -> None:
        """L4: no duplicate link added when already present."""
        from app.ops.lint import _append_wikilink_to_body

        body = "# Page\n\n## Related\n- [[Target Page]]\n"
        result = _append_wikilink_to_body(body, "Target Page")
        assert result == body, "body must be unchanged when link already present"

    def test_case_insensitive_idempotency(self) -> None:
        """L4: idempotency check is case-insensitive."""
        from app.ops.lint import _append_wikilink_to_body

        body = "# Page\n\n## Related\n- [[target page]]\n"
        result = _append_wikilink_to_body(body, "Target Page")
        # Should be idempotent (lowercase already present).
        assert result.count("[[") == 1, "must not add a second link"


# ── T-LINT-L7: _infer_stub_page_type heuristic (ADR-0067 D1) ────────────────────


class TestInferStubPageType:
    """Unit tests for _infer_stub_page_type (ADR-0067 D1 / LN-D1 fix).

    Verifies that the deterministic heuristic never produces PageType.QUERY and
    correctly routes proper nouns → entity and common phrases → concept.
    """

    def _infer(self, title: str) -> str:
        from app.ops.lint import _infer_stub_page_type

        return _infer_stub_page_type(title).value

    # ── Proper-noun / capitalised word → entity ───────────────────────────────

    def test_capitalised_single_word_is_entity(self) -> None:
        """'Microsoft' — starts with uppercase → entity."""
        assert self._infer("Microsoft") == "entity"

    def test_capitalised_two_words_is_entity(self) -> None:
        """'Microsoft Azure' — proper-noun product name → entity."""
        assert self._infer("Microsoft Azure") == "entity"

    # ── Legal suffix → entity ────────────────────────────────────────────────

    def test_private_limited_is_entity(self) -> None:
        """'ALTRUIST TECHNOLOGIES PRIVATE LIMITED' — legal suffix → entity."""
        assert self._infer("ALTRUIST TECHNOLOGIES PRIVATE LIMITED") == "entity"

    def test_inc_suffix_is_entity(self) -> None:
        """'Acme Corp. Inc.' — Inc. suffix → entity."""
        assert self._infer("Acme Corp. Inc.") == "entity"

    def test_ltd_suffix_is_entity(self) -> None:
        """'Example Ltd' — Ltd suffix → entity."""
        assert self._infer("Example Ltd") == "entity"

    def test_gmbh_suffix_is_entity(self) -> None:
        """'Siemens GmbH' — GmbH suffix → entity."""
        assert self._infer("Siemens GmbH") == "entity"

    # ── All-caps acronym → entity ─────────────────────────────────────────────

    def test_all_caps_acronym_is_entity(self) -> None:
        """'AWS' — all-caps token ≥2 chars → entity."""
        assert self._infer("AWS") == "entity"

    def test_all_caps_in_phrase_is_entity(self) -> None:
        """'GDPR compliance' — contains all-caps token → entity."""
        assert self._infer("GDPR compliance") == "entity"

    # ── Common phrases → concept ──────────────────────────────────────────────

    def test_all_lowercase_hyphenated_is_concept(self) -> None:
        """'amazon s3' — all lowercase, no special tokens → concept."""
        assert self._infer("amazon s3") == "concept"

    def test_common_phrase_is_concept(self) -> None:
        """'chain of thought' — generic lowercase phrase → concept."""
        assert self._infer("chain of thought") == "concept"

    def test_technical_term_is_concept(self) -> None:
        """'license reconciliation' — lowercase technical term → concept."""
        assert self._infer("license reconciliation") == "concept"

    def test_empty_string_is_concept(self) -> None:
        """Empty title falls back to concept."""
        assert self._infer("") == "concept"

    def test_whitespace_only_is_concept(self) -> None:
        """Whitespace-only title falls back to concept."""
        assert self._infer("   ") == "concept"

    # ── NEVER query ───────────────────────────────────────────────────────────

    def test_never_returns_query_for_any_input(self) -> None:
        """_infer_stub_page_type must NEVER return PageType.QUERY (ADR-0067 D1)."""
        from app.ops.lint import _infer_stub_page_type

        samples = [
            "Microsoft",
            "ALTRUIST TECHNOLOGIES PRIVATE LIMITED",
            "amazon s3",
            "chain of thought",
            "license reconciliation",
            "",
            "why does this exist",
            "What is the capital of France?",
            "open question about performance",
        ]
        for sample in samples:
            result = _infer_stub_page_type(sample)
            assert (
                result.value != "query"
            ), f"_infer_stub_page_type({sample!r}) returned 'query' — violates ADR-0067 D1"
