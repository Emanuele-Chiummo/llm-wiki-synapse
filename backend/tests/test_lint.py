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
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )

    # lint_findings
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
        Column("source_page_id", Text, nullable=True),
        Column("proposed_title", Text, nullable=True),
        Column("proposed_page_type", Text, nullable=True),
        Column("proposed_dir", Text, nullable=True),
        Column("rationale", Text, nullable=True),
        Column("resolution", Text, nullable=True),
        Column("created_page_id", Text, nullable=True),
        Column("deep_research_run_id", String(36), nullable=True),
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
) -> None:
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO links "
                "(id, source_page_id, target_title, target_page_id, dangling, created_at) "
                "VALUES (:id, :src, :tt, :tgt, 0, datetime('now'))"
            ),
            {
                "id": str(uuid.uuid4()),
                "src": source_page_id,
                "tt": target_title,
                "tgt": target_page_id,
            },
        )
        await sess.commit()


async def _insert_finding(
    env: dict[str, Any],
    *,
    vault_id: str = "test-vault",
    category: str = "contradiction",
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
                "VALUES (:id, :rid, :v, :cat, 'warning', :tpid, :tt, :desc, :st, datetime('now'))"
            ),
            {
                "id": finding_id,
                "rid": run_id,
                "v": vault_id,
                "cat": category,
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

    async def test_apply_flag_only_contradiction(self, lint_env: dict[str, Any]) -> None:
        """T-LINT-006: contradiction is flag-only → status=applied, no edit/bump."""
        finding_id = await _insert_finding(lint_env, category="contradiction")

        bump_called: list[int] = []

        async def _fake_bump() -> None:
            bump_called.append(1)

        with patch("app.ingest.orchestrator.bump_version", side_effect=_fake_bump):
            from app.ops.lint import apply_lint_fix

            finding = await apply_lint_fix(uuid.UUID(finding_id))

        assert finding.status == "applied"
        assert finding.resolution_note is not None
        assert bump_called == [], "flag-only apply must not bump data_version"

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
        """T-LINT-008: missing-page apply delegates to _run_generation + write_wiki_page."""
        finding_id = await _insert_finding(
            lint_env,
            category="missing-page",
            target_title="Kubernetes",
            description="Kubernetes is mentioned but has no page.",
        )

        gen_calls: list[Any] = []

        async def _fake_run_generation(**kwargs: Any) -> Any:
            gen_calls.append(kwargs)
            return MagicMock()

        async def _fake_write(session: Any, page: Any, origin: str) -> Any:
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
