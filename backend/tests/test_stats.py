"""
Tests for R12-1 dashboard stats API (ADR-0054 §5) and S9 domain_vocabulary config key.

Coverage:
  T-STATS-001  GET /stats/overview — shape, all fields present, correct types
  T-STATS-002  GET /stats/overview — counts reflect seeded pages/links/review/lint rows
  T-STATS-003  GET /stats/overview — monthly_cost_usd == /costs/summary monthly_total (AC-R12-1-3)
  T-STATS-004  GET /stats/sections — vocabulary order + untagged bucket always last
  T-STATS-005  GET /stats/sections — empty/dormant vocabulary → only untagged bucket
  T-STATS-006  GET /stats/sections — tagged pages appear in correct section
  T-STATS-007  GET /status — version field present and semver-ish (ADR-0054 §6)
  T-STATS-008  S9 domain_vocabulary — valid PUT → 204, GET shows key, DELETE reverts
  T-STATS-009  S9 domain_vocabulary — invalid JSON → 422 no write
  T-STATS-010  S9 domain_vocabulary — dedupe case-insensitive, strip, cap 100
  T-STATS-011  S9 domain_vocabulary — empty array "[]" is valid (dormant state)
  T-STATS-012  effective_domain_vocabulary() — unset → []; set → list; malformed → []

Database: SQLite in-memory (portability; same pattern as test_r9_costs.py).
No real DB, no Qdrant, no provider calls.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── SQLite schema (portable subset of Postgres schema) ────────────────────────
# Only the tables referenced by stats.py queries are required.

_CREATE_PAGES = """
CREATE TABLE IF NOT EXISTS pages (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    file_path TEXT NOT NULL DEFAULT '',
    title TEXT,
    type TEXT,
    sources TEXT,
    tags TEXT,
    content_hash TEXT NOT NULL DEFAULT '',
    source_mtime_ns INTEGER,
    qdrant_point_id TEXT,
    x REAL,
    y REAL,
    community INTEGER,
    pinned INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_VAULT_STATE = """
CREATE TABLE IF NOT EXISTS vault_state (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    data_version INTEGER NOT NULL DEFAULT 0,
    remote_mcp_enabled INTEGER NOT NULL DEFAULT 0,
    mcp_access_token_hash TEXT,
    mcp_allow_without_token INTEGER NOT NULL DEFAULT 0,
    clip_enabled_db INTEGER,
    clip_access_token TEXT,
    clip_allowed_origins_db TEXT,
    cli_oauth_token TEXT,
    searxng_url_db TEXT,
    searxng_categories_db TEXT,
    searxng_max_queries_db INTEGER,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_EDGES = """
CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    source_page_id TEXT NOT NULL,
    target_page_id TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    signals TEXT,
    kind TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_REVIEW_ITEMS = """
CREATE TABLE IF NOT EXISTS review_items (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    item_type TEXT NOT NULL DEFAULT 'missing-page',
    status TEXT NOT NULL DEFAULT 'pending',
    page_id TEXT,
    source_page_id TEXT,
    proposed_title TEXT,
    proposed_page_type TEXT,
    proposed_dir TEXT,
    rationale TEXT,
    content_key TEXT,
    referenced_page_ids TEXT,
    search_queries TEXT,
    resolution TEXT,
    created_page_id TEXT,
    deep_research_run_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    reviewed_by TEXT
)
"""

_CREATE_LINT_FINDINGS = """
CREATE TABLE IF NOT EXISTS lint_findings (
    id TEXT PRIMARY KEY,
    lint_run_id TEXT NOT NULL,
    vault_id TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'orphan-page',
    severity TEXT NOT NULL DEFAULT 'warning',
    target_page_id TEXT,
    target_title TEXT,
    description TEXT NOT NULL DEFAULT '',
    proposed_action TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

# Cost tables needed by get_monthly_cost_usd (same schema subset as test_r9_costs.py)
_CREATE_INGEST_RUNS = """
CREATE TABLE IF NOT EXISTS ingest_runs (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    page_id TEXT,
    provider_name TEXT NOT NULL DEFAULT 'test',
    provider_type TEXT NOT NULL DEFAULT 'api',
    model_id TEXT NOT NULL DEFAULT 'test-model',
    route TEXT NOT NULL DEFAULT 'orchestrated',
    max_iter_used INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    converged INTEGER NOT NULL DEFAULT 0,
    cost_anomaly INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status TEXT NOT NULL DEFAULT 'completed',
    pages_created INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    source_path TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0
)
"""

_CREATE_MESSAGES = """
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'assistant',
    content TEXT NOT NULL DEFAULT '',
    citations TEXT,
    provider_type TEXT,
    model_id TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_CREATE_CONVERSATIONS = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    title TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT
)
"""

_CREATE_DEEP_RESEARCH_RUNS = """
CREATE TABLE IF NOT EXISTS deep_research_runs (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    max_iter INTEGER NOT NULL DEFAULT 3,
    token_budget INTEGER NOT NULL DEFAULT 60000,
    iterations_used INTEGER NOT NULL DEFAULT 0,
    queries_used TEXT NOT NULL DEFAULT '[]',
    sources_fetched INTEGER NOT NULL DEFAULT 0,
    converged INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    synthesis_text TEXT,
    synthesis_page_id TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    error_message TEXT
)
"""

_CREATE_LINT_RUNS = """
CREATE TABLE IF NOT EXISTS lint_runs (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'completed',
    max_iter INTEGER NOT NULL DEFAULT 1,
    token_budget INTEGER NOT NULL DEFAULT 10000,
    iterations_used INTEGER NOT NULL DEFAULT 0,
    findings_count INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0.0,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_ALL_CREATES = [
    _CREATE_PAGES,
    _CREATE_VAULT_STATE,
    _CREATE_EDGES,
    _CREATE_REVIEW_ITEMS,
    _CREATE_LINT_FINDINGS,
    _CREATE_INGEST_RUNS,
    _CREATE_MESSAGES,
    _CREATE_CONVERSATIONS,
    _CREATE_DEEP_RESEARCH_RUNS,
    _CREATE_LINT_RUNS,
]

# ── Engine + session factory helpers ──────────────────────────────────────────

VAULT_ID = "test-vault"


def _make_engine() -> Any:
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _make_session_factory(engine: Any) -> Any:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def _setup_schema(engine: Any) -> None:
    async with engine.begin() as conn:
        for ddl in _ALL_CREATES:
            await conn.execute(sa_text(ddl))
        # Seed vault_state row
        await conn.execute(
            sa_text("INSERT INTO vault_state (id, vault_id, data_version) VALUES (:id, :vid, :dv)"),
            {"id": str(uuid.uuid4()), "vid": VAULT_ID, "dv": 5},
        )


# ── Mini FastAPI test app mirroring main.py wiring ────────────────────────────


def _make_test_app(engine: Any) -> FastAPI:
    """
    Build a minimal FastAPI instance that wires stats router against a given SQLite engine.
    The session factory and settings are monkey-patched at test time.
    """
    from contextlib import asynccontextmanager as acm

    from fastapi import FastAPI

    @acm
    async def _test_lifespan(app_: FastAPI) -> AsyncIterator[None]:
        yield

    from app.costs import router as costs_router
    from app.stats import router as stats_router

    test_app = FastAPI(title="StatsTest", lifespan=_test_lifespan)
    test_app.include_router(stats_router)
    test_app.include_router(costs_router)
    return test_app


# ── Fixtures and helpers ──────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _month_start_iso() -> str:
    now = datetime.now(tz=UTC)
    return datetime(now.year, now.month, 1, tzinfo=UTC).isoformat()


# ── T-STATS-001 / T-STATS-002: /stats/overview shape and counts ──────────────


@pytest.mark.asyncio
async def test_stats_overview_shape_and_counts() -> None:
    """T-STATS-001/002: shape is correct; counts match seeded data."""
    engine = _make_engine()
    await _setup_schema(engine)
    factory = _make_session_factory(engine)

    # Seed some pages, a review item (pending), and a lint finding (open)
    async with factory() as sess:
        pid1 = str(uuid.uuid4())
        pid2 = str(uuid.uuid4())
        rid = str(uuid.uuid4())
        fid = str(uuid.uuid4())
        eid = str(uuid.uuid4())
        now = _now_iso()

        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title, type, tags, content_hash, updated_at, created_at) "
                "VALUES (:id, :vid, 'p1.md', 'Page One', 'concept', :tags, 'h1', :ts, :ts)"
            ),
            {"id": pid1, "vid": VAULT_ID, "tags": json.dumps(["domain/ServiceNow"]), "ts": now},
        )
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title, type, tags, content_hash, updated_at, created_at) "
                "VALUES (:id, :vid, 'p2.md', 'Page Two', 'entity', :tags, 'h2', :ts, :ts)"
            ),
            {"id": pid2, "vid": VAULT_ID, "tags": json.dumps([]), "ts": now},
        )
        await sess.execute(
            sa_text(
                "INSERT INTO edges (id, vault_id, source_page_id, target_page_id) "
                "VALUES (:id, :vid, :s, :t)"
            ),
            {"id": eid, "vid": VAULT_ID, "s": pid1, "t": pid2},
        )
        await sess.execute(
            sa_text(
                "INSERT INTO review_items (id, vault_id, status) VALUES (:id, :vid, 'pending')"
            ),
            {"id": rid, "vid": VAULT_ID},
        )
        # Add a lint_run so we can insert a lint_finding
        lid = str(uuid.uuid4())
        await sess.execute(
            sa_text(
                "INSERT INTO lint_runs (id, vault_id, max_iter, token_budget) VALUES (:id, :vid, 1, 1000)"
            ),
            {"id": lid, "vid": VAULT_ID},
        )
        await sess.execute(
            sa_text(
                "INSERT INTO lint_findings (id, lint_run_id, vault_id, description, status) "
                "VALUES (:id, :lid, :vid, 'x', 'open')"
            ),
            {"id": fid, "lid": lid, "vid": VAULT_ID},
        )
        await sess.commit()

    test_app = _make_test_app(engine)

    with (
        patch("app.stats.settings") as mock_settings,
        patch("app.stats.get_session") as mock_gs,
        patch("app.costs.get_session") as mock_costs_gs,
        patch("app.costs.settings") as mock_cost_settings,
    ):
        mock_settings.vault_id = VAULT_ID
        mock_cost_settings.vault_id = VAULT_ID
        mock_cost_settings.cost_alert_threshold_usd = 5.0

        @asynccontextmanager
        async def _sess_ctx() -> AsyncIterator[AsyncSession]:
            async with factory() as s:
                yield s

        mock_gs.return_value = _sess_ctx()
        mock_costs_gs.return_value = _sess_ctx()

        # Reset stats cache
        import app.stats as stats_mod

        stats_mod._overview_cache = None

        # We need multiple calls to get_session within one handler — patch as a factory
        def _make_ctx() -> Any:
            return _sess_ctx()

        mock_gs.side_effect = _make_ctx
        mock_costs_gs.side_effect = _make_ctx

        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            resp = await client.get("/stats/overview")

    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Required fields (ADR-0054 §5.1)
    for field in [
        "pages_total",
        "pages_by_type",
        "links_total",
        "communities_count",
        "review_pending",
        "lint_open",
        "monthly_cost_usd",
        "data_version",
        "recent_activity",
    ]:
        assert field in body, f"Missing field {field!r} in /stats/overview"

    assert body["pages_total"] == 2
    assert body["links_total"] == 1
    assert body["review_pending"] == 1
    assert body["lint_open"] == 1
    assert body["data_version"] == 5
    assert isinstance(body["monthly_cost_usd"], float)
    assert isinstance(body["recent_activity"], list)
    assert len(body["recent_activity"]) <= 10

    # pages_by_type keys
    pbt = body["pages_by_type"]
    assert pbt.get("concept", 0) == 1
    assert pbt.get("entity", 0) == 1

    # recent_activity items have required sub-fields
    for item in body["recent_activity"]:
        assert "page_id" in item
        assert "title" in item
        assert "slug" in item
        assert "updated_at" in item


# ── T-STATS-003: monthly_cost_usd == /costs/summary monthly_total (AC-R12-1-3) ─


@pytest.mark.asyncio
async def test_stats_overview_monthly_cost_parity() -> None:
    """T-STATS-003: AC-R12-1-3 — identical monthly_total from /stats/overview and /costs/summary."""
    engine = _make_engine()
    await _setup_schema(engine)
    factory = _make_session_factory(engine)

    now = _now_iso()
    async with factory() as sess:
        # Seed an ingest run with a known cost in the current month
        await sess.execute(
            sa_text(
                "INSERT INTO ingest_runs (id, vault_id, total_cost_usd, started_at, finished_at) "
                "VALUES (:id, :vid, 1.2500, :ts, :ts)"
            ),
            {"id": str(uuid.uuid4()), "vid": VAULT_ID, "ts": now},
        )
        # Seed a conversation + message cost
        conv_id = str(uuid.uuid4())
        await sess.execute(
            sa_text("INSERT INTO conversations (id, vault_id) VALUES (:id, :vid)"),
            {"id": conv_id, "vid": VAULT_ID},
        )
        await sess.execute(
            sa_text(
                "INSERT INTO messages (id, conversation_id, role, total_cost_usd, created_at) "
                "VALUES (:id, :cid, 'assistant', 0.3300, :ts)"
            ),
            {"id": str(uuid.uuid4()), "cid": conv_id, "ts": now},
        )
        await sess.commit()

    import app.stats as stats_mod

    stats_mod._overview_cache = None

    def _make_ctx() -> Any:
        @asynccontextmanager
        async def _ctx() -> AsyncIterator[AsyncSession]:
            async with factory() as s:
                yield s

        return _ctx()

    with (
        patch("app.stats.settings") as mock_settings,
        patch("app.stats.get_session", side_effect=_make_ctx),
        patch("app.costs.get_session", side_effect=_make_ctx),
        patch("app.costs.settings") as mock_cost_settings,
        patch("app.config_overrides.effective_float", return_value=5.0),
    ):
        mock_settings.vault_id = VAULT_ID
        mock_cost_settings.vault_id = VAULT_ID
        mock_cost_settings.cost_alert_threshold_usd = 5.0

        test_app = _make_test_app(engine)
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            stats_resp = await client.get("/stats/overview")
            costs_resp = await client.get("/costs/summary")

    assert stats_resp.status_code == 200
    assert costs_resp.status_code == 200

    stats_body = stats_resp.json()
    costs_body = costs_resp.json()

    stats_monthly = stats_body["monthly_cost_usd"]
    costs_monthly = costs_body["monthly_total_usd"]

    assert abs(stats_monthly - costs_monthly) < 0.0001, (
        f"monthly_cost_usd mismatch: /stats/overview={stats_monthly} "
        f"/costs/summary={costs_monthly} (AC-R12-1-3)"
    )


# ── T-STATS-004/005/006: /stats/sections ─────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_sections_dormant_vocabulary() -> None:
    """T-STATS-005: dormant vocabulary ([]) → only untagged bucket."""
    import app.stats as stats_mod
    from app.config_overrides import _cache, _cache_lock

    # Clear vocabulary
    async with _cache_lock:
        _cache.pop("domain_vocabulary", None)

    stats_mod._sections_cache = None

    engine = _make_engine()
    await _setup_schema(engine)
    factory = _make_session_factory(engine)

    async with factory() as sess:
        pid = str(uuid.uuid4())
        now = _now_iso()
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title, type, tags, content_hash, updated_at, created_at) "
                "VALUES (:id, :vid, 'p.md', 'Any Page', 'concept', '[]', 'h', :ts, :ts)"
            ),
            {"id": pid, "vid": VAULT_ID, "ts": now},
        )
        await sess.commit()

    def _make_ctx() -> Any:
        @asynccontextmanager
        async def _ctx() -> AsyncIterator[AsyncSession]:
            async with factory() as s:
                yield s

        return _ctx()

    with (
        patch("app.stats.settings") as mock_settings,
        patch("app.stats.get_session", side_effect=_make_ctx),
    ):
        mock_settings.vault_id = VAULT_ID

        test_app = _make_test_app(engine)
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            resp = await client.get("/stats/sections")

    assert resp.status_code == 200
    body = resp.json()
    sections = body["sections"]
    # Dormant: only untagged bucket
    assert len(sections) == 1
    assert sections[0]["domain"] == "untagged"
    assert sections[0]["pages_total"] == 1


@pytest.mark.asyncio
async def test_stats_sections_vocabulary_order_and_untagged() -> None:
    """T-STATS-004/006: vocabulary order; tagged pages in correct section; untagged last."""
    import app.stats as stats_mod
    from app.config_overrides import _cache, _cache_lock

    vocab = ["ServiceNow", "SAM"]
    async with _cache_lock:
        _cache["domain_vocabulary"] = json.dumps(vocab)

    stats_mod._sections_cache = None

    engine = _make_engine()
    await _setup_schema(engine)
    factory = _make_session_factory(engine)

    async with factory() as sess:
        now = _now_iso()
        pid1 = str(uuid.uuid4())
        pid2 = str(uuid.uuid4())
        pid3 = str(uuid.uuid4())

        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title, type, tags, content_hash, updated_at, created_at) "
                "VALUES (:id, :vid, 'sn.md', 'SN Page', 'concept', :tags, 'h1', :ts, :ts)"
            ),
            {"id": pid1, "vid": VAULT_ID, "tags": json.dumps(["domain/ServiceNow"]), "ts": now},
        )
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title, type, tags, content_hash, updated_at, created_at) "
                "VALUES (:id, :vid, 'sam.md', 'SAM Page', 'entity', :tags, 'h2', :ts, :ts)"
            ),
            {"id": pid2, "vid": VAULT_ID, "tags": json.dumps(["domain/SAM"]), "ts": now},
        )
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title, type, tags, content_hash, updated_at, created_at) "
                "VALUES (:id, :vid, 'bare.md', 'Bare Page', 'concept', '[]', 'h3', :ts, :ts)"
            ),
            {"id": pid3, "vid": VAULT_ID, "ts": now},
        )
        await sess.commit()

    def _make_ctx() -> Any:
        @asynccontextmanager
        async def _ctx() -> AsyncIterator[AsyncSession]:
            async with factory() as s:
                yield s

        return _ctx()

    with (
        patch("app.stats.settings") as mock_settings,
        patch("app.stats.get_session", side_effect=_make_ctx),
    ):
        mock_settings.vault_id = VAULT_ID

        test_app = _make_test_app(engine)
        async with AsyncClient(
            transport=ASGITransport(app=test_app), base_url="http://test"
        ) as client:
            resp = await client.get("/stats/sections")

    assert resp.status_code == 200
    body = resp.json()
    sections = body["sections"]

    # 3 sections: ServiceNow, SAM, untagged
    assert len(sections) == 3

    # Vocabulary order: ServiceNow first, SAM second, untagged last
    assert sections[0]["domain"] == "ServiceNow"
    assert sections[1]["domain"] == "SAM"
    assert sections[2]["domain"] == "untagged"

    # Counts
    assert sections[0]["pages_total"] == 1
    assert sections[1]["pages_total"] == 1
    assert sections[2]["pages_total"] == 1

    # pages_by_type
    assert sections[0]["pages_by_type"].get("concept", 0) == 1
    assert sections[1]["pages_by_type"].get("entity", 0) == 1

    # Each section has required sub-fields
    for sec in sections:
        assert "domain" in sec
        assert "pages_total" in sec
        assert "pages_by_type" in sec
        assert "last_activity" in sec
        assert "top_pages" in sec
        assert len(sec["top_pages"]) <= 5


# ── T-STATS-007: /status.version ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_version_field_present_and_semver() -> None:
    """T-STATS-007: GET /status response includes 'version' field; value is semver-ish."""
    import re
    from contextlib import asynccontextmanager as acm

    from app.main import app

    @acm
    async def _noop_lifespan(app_: Any) -> AsyncIterator[None]:
        yield

    app.router.lifespan_context = _noop_lifespan

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.main.get_session") as mock_gs:
            mock_row = MagicMock()
            mock_row.scalar_one_or_none.return_value = MagicMock(data_version=7)
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock(return_value=mock_row)
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_ctx = MagicMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_gs.return_value = mock_ctx
            resp = await client.get("/status")

    assert resp.status_code == 200
    body = resp.json()
    assert "version" in body, "/status must include a 'version' field (ADR-0054 §6)"
    version_val = body["version"]
    # Acceptable: semver like "1.2.0" or "dev" (when package not installed)
    assert isinstance(version_val, str) and len(version_val) > 0
    semver_pattern = re.compile(r"^\d+\.\d+\.\d+.*$")
    is_semver = semver_pattern.match(version_val) is not None
    is_dev = version_val == "dev"
    assert (
        is_semver or is_dev
    ), f"version must be semver (e.g. '1.2.0') or 'dev', got {version_val!r} (ADR-0054 §6)"


# ── T-STATS-008: S9 domain_vocabulary PUT/GET/DELETE ─────────────────────────


@pytest.mark.asyncio
async def test_domain_vocabulary_put_get_delete() -> None:
    """T-STATS-008: PUT domain_vocabulary → 204; config/app lists it; DELETE reverts."""
    import app.config_overrides as co

    # Clear any prior state
    async with co._cache_lock:
        co._cache.pop("domain_vocabulary", None)

    from contextlib import asynccontextmanager as acm

    from app.main import app

    @acm
    async def _noop(app_: Any) -> AsyncIterator[None]:
        yield

    app.router.lifespan_context = _noop

    # Mock the DB session for upsert
    mock_session = AsyncMock()
    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_scalar)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.main.get_session", return_value=mock_ctx):
            put_resp = await client.put(
                "/config/app/domain_vocabulary",
                json={"value": '["ServiceNow","SAM","Procurement"]'},
            )

    assert put_resp.status_code == 204

    # Cache was refreshed — effective_domain_vocabulary should return the list
    vocab = co.effective_domain_vocabulary()
    assert "ServiceNow" in vocab
    assert "SAM" in vocab
    assert "Procurement" in vocab

    # GET /config/app must list domain_vocabulary
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        get_resp = await client.get("/config/app")
    assert get_resp.status_code == 200
    keys = [s["key"] for s in get_resp.json()["settings"]]
    assert "domain_vocabulary" in keys

    # DELETE reverts
    del_ctx = MagicMock()
    del_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    del_ctx.__aexit__ = AsyncMock(return_value=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        with patch("app.main.get_session", return_value=del_ctx):
            del_resp = await client.delete("/config/app/domain_vocabulary")

    assert del_resp.status_code == 204
    assert co.effective_domain_vocabulary() == []


# ── T-STATS-009: S9 validate_value — invalid JSON → 422 ──────────────────────


def test_domain_vocabulary_invalid_json_rejected() -> None:
    """T-STATS-009: non-JSON value → validate_value returns an error string."""
    from app.config_overrides import validate_value

    assert validate_value("domain_vocabulary", "not-json") is not None
    assert validate_value("domain_vocabulary", "{bad}") is not None


def test_domain_vocabulary_non_list_json_rejected() -> None:
    """T-STATS-009: JSON but not a list → rejected."""
    from app.config_overrides import validate_value

    assert validate_value("domain_vocabulary", '"just-a-string"') is not None
    assert validate_value("domain_vocabulary", '{"a": 1}') is not None


def test_domain_vocabulary_empty_string_element_rejected() -> None:
    """T-STATS-009: list with empty string element → rejected."""
    from app.config_overrides import validate_value

    assert validate_value("domain_vocabulary", '["", "SAM"]') is not None


# ── T-STATS-010: S9 normalisation (dedupe, strip, cap) ───────────────────────


@pytest.mark.asyncio
async def test_domain_vocabulary_dedupe_and_strip() -> None:
    """T-STATS-010: set_override dedupes case-insensitively and strips whitespace."""
    import app.config_overrides as co

    mock_session = AsyncMock()
    mock_scalar = MagicMock()
    mock_scalar.scalar_one_or_none.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_scalar)
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()

    # Input: duplicates + different cases + whitespace
    raw_input = '["ServiceNow", " servicenow ", "SAM", "sam"]'
    await co.set_override(mock_session, "domain_vocabulary", raw_input)

    vocab = co.effective_domain_vocabulary()
    # Dedupe: only first occurrence kept (case-insensitive)
    names_lower = [v.lower() for v in vocab]
    assert names_lower.count("servicenow") == 1
    assert names_lower.count("sam") == 1
    assert len(vocab) == 2


@pytest.mark.asyncio
async def test_domain_vocabulary_cap_100() -> None:
    """T-STATS-010: more than 100 elements → validate_value rejects."""
    from app.config_overrides import validate_value

    over_100 = json.dumps([f"domain_{i}" for i in range(101)])
    result = validate_value("domain_vocabulary", over_100)
    assert result is not None, "Expected rejection for > 100 elements"


# ── T-STATS-011: empty array "[]" is valid (dormant) ─────────────────────────


def test_domain_vocabulary_empty_array_is_valid() -> None:
    """T-STATS-011: '[]' is a valid value (explicit dormant state)."""
    from app.config_overrides import validate_value

    assert validate_value("domain_vocabulary", "[]") is None, "[] must be valid (dormant state)"


# ── T-STATS-012: effective_domain_vocabulary() typed accessor ─────────────────


@pytest.mark.asyncio
async def test_effective_domain_vocabulary_unset_returns_empty() -> None:
    """T-STATS-012a: key not in cache → []."""
    import app.config_overrides as co

    async with co._cache_lock:
        co._cache.pop("domain_vocabulary", None)

    assert co.effective_domain_vocabulary() == []


@pytest.mark.asyncio
async def test_effective_domain_vocabulary_set_returns_list() -> None:
    """T-STATS-012b: valid JSON array in cache → list[str]."""
    import app.config_overrides as co

    async with co._cache_lock:
        co._cache["domain_vocabulary"] = '["Alpha","Beta"]'

    result = co.effective_domain_vocabulary()
    assert result == ["Alpha", "Beta"]

    async with co._cache_lock:
        co._cache.pop("domain_vocabulary", None)


@pytest.mark.asyncio
async def test_effective_domain_vocabulary_malformed_returns_empty() -> None:
    """T-STATS-012c: malformed stored value → [] (fail-closed)."""
    import app.config_overrides as co

    async with co._cache_lock:
        co._cache["domain_vocabulary"] = "not-valid-json!!"

    result = co.effective_domain_vocabulary()
    assert result == []

    async with co._cache_lock:
        co._cache.pop("domain_vocabulary", None)


# ── T-STATS-013: GET /config/app returns 9 settings with domain_vocabulary last ─


@pytest.mark.asyncio
async def test_get_config_app_has_9_settings_with_domain_vocabulary() -> None:
    """T-STATS-013: GET /config/app returns 9 settings; domain_vocabulary is last."""
    import app.config_overrides as co
    from app.config_overrides import ORDERED_KEYS

    async with co._cache_lock:
        co._cache.clear()

    from contextlib import asynccontextmanager as acm

    from app.main import app

    @acm
    async def _noop(app_: Any) -> AsyncIterator[None]:
        yield

    app.router.lifespan_context = _noop

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/config/app")

    assert resp.status_code == 200
    body = resp.json()
    settings_list = body["settings"]
    # Now 9 keys (S1..S9)
    assert len(settings_list) == 9, f"Expected 9 settings, got {len(settings_list)}"
    keys = [s["key"] for s in settings_list]
    assert keys == ORDERED_KEYS, f"Keys out of order: {keys}"
    assert keys[-1] == "domain_vocabulary", "domain_vocabulary must be the last key (S9)"
