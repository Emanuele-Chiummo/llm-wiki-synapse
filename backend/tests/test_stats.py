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
    remote_mcp_write_enabled INTEGER,
    mcp_access_token_hash TEXT,
    mcp_allow_without_token INTEGER NOT NULL DEFAULT 0,
    clip_enabled_db INTEGER,
    clip_access_token TEXT,
    clip_allowed_origins_db TEXT,
    cli_oauth_token TEXT,
    cli_oauth_token_encrypted BLOB,
    web_search_api_keys_encrypted BLOB,
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


# ── T-STATS-013: GET /config/app returns 11 settings; backfill_schedule is last ─


@pytest.mark.asyncio
async def test_get_config_app_has_9_settings_with_domain_vocabulary() -> None:
    """T-STATS-013: GET /config/app returns 11 settings (S1..S11); order matches ORDERED_KEYS."""
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
    # Now 20 keys (S1..S20; S19/S20 = Image Captioning keys, v1.5 P3-a)
    assert len(settings_list) == 23, f"Expected 23 settings, got {len(settings_list)}"
    keys = [s["key"] for s in settings_list]
    assert keys == ORDERED_KEYS, f"Keys out of order: {keys}"
    assert keys[-1] == "web_search_provider", "web_search_provider must be last (S23)"


# ── T-STATS-014..018: GET /stats/groups (A1 amendment) ───────────────────────


@pytest.mark.asyncio
async def test_stats_groups_grouping_ordering_and_cap() -> None:
    """
    T-STATS-014: groups ordered by pages_total DESC; capped at 12; shape correct.

    Seeds 13 communities of sizes 13, 12, 11, ..., 1 → response must contain exactly
    12 groups, ordered by pages_total desc (community 0 first with 13 pages, community 1
    second with 12 pages, ..., community 11 twelfth with 2 pages; community 12 dropped).
    """
    import app.stats as stats_mod

    stats_mod._groups_cache = None

    engine = _make_engine()
    await _setup_schema(engine)
    factory = _make_session_factory(engine)

    # Seed 13 communities: community k has (13-k) pages
    async with factory() as sess:
        now = _now_iso()
        for community_id in range(13):
            page_count = 13 - community_id
            for i in range(page_count):
                pid = str(uuid.uuid4())
                await sess.execute(
                    sa_text(
                        "INSERT INTO pages "
                        "(id, vault_id, file_path, title, type, tags, content_hash, "
                        "updated_at, created_at, community) "
                        "VALUES (:id, :vid, :fp, :title, 'concept', '[]', :ch, :ts, :ts, :comm)"
                    ),
                    {
                        "id": pid,
                        "vid": VAULT_ID,
                        "fp": f"c{community_id}_p{i}.md",
                        "title": f"Comm{community_id} Page{i}",
                        "ch": f"h{community_id}{i}",
                        "ts": now,
                        "comm": community_id,
                    },
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
            resp = await client.get("/stats/groups")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "groups" in body
    groups = body["groups"]

    # Capped at 12
    assert len(groups) == 12, f"Expected 12 groups (cap), got {len(groups)}"

    # Ordered by pages_total DESC
    totals = [g["pages_total"] for g in groups]
    assert totals == sorted(totals, reverse=True), f"Not DESC order: {totals}"

    # Largest group has 13 pages
    assert groups[0]["pages_total"] == 13

    # Community with 1 page (community_id=12) must be excluded (outside cap)
    for g in groups:
        assert g["pages_total"] >= 2, "Group with 1 page should have been cut by cap=12"

    # Required fields on each group
    for g in groups:
        assert "community" in g
        assert "label" in g
        assert "pages_total" in g
        assert "pages_by_type" in g
        assert "top_pages" in g
        assert "last_activity" in g
        assert len(g["top_pages"]) <= 5


@pytest.mark.asyncio
async def test_stats_groups_label_from_top_degree_page_and_truncation() -> None:
    """
    T-STATS-015: label = title of highest-degree page in community, truncated to 48 chars.

    Hub page has degree=2 (source of 2 edges); leaf pages have degree=1 each (target of 1
    edge). The hub is therefore the highest-degree node and must become the label.
    Degree is counted as total incident edges (source + target), consistent with how
    stats.py builds the degree_map.
    """
    import app.stats as stats_mod

    stats_mod._groups_cache = None

    engine = _make_engine()
    await _setup_schema(engine)
    factory = _make_session_factory(engine)

    async with factory() as sess:
        now = _now_iso()
        pid_hub = str(uuid.uuid4())
        pid_leaf1 = str(uuid.uuid4())
        pid_leaf2 = str(uuid.uuid4())
        eid1 = str(uuid.uuid4())
        eid2 = str(uuid.uuid4())

        long_title = "X" * 60  # 60 chars — must be truncated to 48
        # hub page: source of 2 edges → degree=2
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, tags, content_hash, "
                "updated_at, created_at, community) "
                "VALUES (:id, :vid, 'hub.md', :title, 'concept', '[]', 'hhub', :ts, :ts, 0)"
            ),
            {"id": pid_hub, "vid": VAULT_ID, "title": long_title, "ts": now},
        )
        # leaf pages: target of 1 edge each → degree=1
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, tags, content_hash, "
                "updated_at, created_at, community) "
                "VALUES (:id, :vid, 'leaf1.md', 'Leaf Page One', 'concept', '[]', 'hl1', :ts, :ts, 0)"
            ),
            {"id": pid_leaf1, "vid": VAULT_ID, "ts": now},
        )
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, tags, content_hash, "
                "updated_at, created_at, community) "
                "VALUES (:id, :vid, 'leaf2.md', 'Leaf Page Two', 'concept', '[]', 'hl2', :ts, :ts, 0)"
            ),
            {"id": pid_leaf2, "vid": VAULT_ID, "ts": now},
        )
        # hub → leaf1 (hub degree as source +1, leaf1 as target +1)
        await sess.execute(
            sa_text(
                "INSERT INTO edges (id, vault_id, source_page_id, target_page_id) "
                "VALUES (:id, :vid, :s, :t)"
            ),
            {"id": eid1, "vid": VAULT_ID, "s": pid_hub, "t": pid_leaf1},
        )
        # hub → leaf2 (hub degree as source +1, leaf2 as target +1)
        await sess.execute(
            sa_text(
                "INSERT INTO edges (id, vault_id, source_page_id, target_page_id) "
                "VALUES (:id, :vid, :s, :t)"
            ),
            {"id": eid2, "vid": VAULT_ID, "s": pid_hub, "t": pid_leaf2},
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
            resp = await client.get("/stats/groups")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    groups = body["groups"]

    assert len(groups) == 1
    g = groups[0]
    assert g["pages_total"] == 3

    # label must come from the hub (degree=2, highest) and be truncated to 48
    assert len(g["label"]) == 48, f"Expected label truncated to 48 chars, got {len(g['label'])!r}"
    assert g["label"] == long_title[:48]

    # top_pages: hub first (degree=2 > leaf degree=1)
    assert len(g["top_pages"]) == 3
    assert g["top_pages"][0]["id"] == pid_hub
    assert g["top_pages"][0]["degree"] == 2
    # Both leaves have degree=1
    leaf_degrees = {g["top_pages"][1]["degree"], g["top_pages"][2]["degree"]}
    assert leaf_degrees == {1}


@pytest.mark.asyncio
async def test_stats_groups_meta_hub_excluded_from_label() -> None:
    """v1.5.2: index.md/log.md are graph nodes (D4) and, linking to everything, are the highest-
    degree community members — but they must NOT label the group ('Synapse Index'/'Synapse Log').
    The label + top_pages come from the highest-degree CONTENT page; the meta page stays counted."""
    import app.stats as stats_mod

    stats_mod._groups_cache = None
    engine = _make_engine()
    await _setup_schema(engine)
    factory = _make_session_factory(engine)

    async with factory() as sess:
        now = _now_iso()
        pid_index = str(uuid.uuid4())
        pid_c1 = str(uuid.uuid4())
        pid_c2 = str(uuid.uuid4())
        # index meta hub: type='index', source of 2 edges → degree=2 (highest)
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title, type, tags, content_hash, "
                "updated_at, created_at, community) "
                "VALUES (:id, :vid, 'index.md', 'Synapse Index', 'index', '[]', 'hidx', :ts, :ts, 0)"
            ),
            {"id": pid_index, "vid": VAULT_ID, "ts": now},
        )
        for pid, fp, title, h in (
            (pid_c1, "c1.md", "Real Concept One", "hc1"),
            (pid_c2, "c2.md", "Real Concept Two", "hc2"),
        ):
            await sess.execute(
                sa_text(
                    "INSERT INTO pages (id, vault_id, file_path, title, type, tags, content_hash, "
                    "updated_at, created_at, community) "
                    "VALUES (:id, :vid, :fp, :title, 'concept', '[]', :h, :ts, :ts, 0)"
                ),
                {"id": pid, "vid": VAULT_ID, "fp": fp, "title": title, "h": h, "ts": now},
            )
        for s, t in ((pid_index, pid_c1), (pid_index, pid_c2)):
            await sess.execute(
                sa_text(
                    "INSERT INTO edges (id, vault_id, source_page_id, target_page_id) "
                    "VALUES (:id, :vid, :s, :t)"
                ),
                {"id": str(uuid.uuid4()), "vid": VAULT_ID, "s": s, "t": t},
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
            resp = await client.get("/stats/groups")

    assert resp.status_code == 200, resp.text
    g = resp.json()["groups"][0]
    assert g["pages_total"] == 3, "the index meta page is still counted as a member"
    # The meta hub must NOT label the group, even though it is the highest-degree node.
    assert g["label"] != "Synapse Index"
    assert g["label"] in ("Real Concept One", "Real Concept Two")
    # ...and it must not appear in the top_pages preview.
    assert "Synapse Index" not in [p["title"] for p in g["top_pages"]]


@pytest.mark.asyncio
async def test_stats_groups_unassigned_excluded() -> None:
    """
    T-STATS-016: pages with community NULL or -1 are excluded from groups.
    """
    import app.stats as stats_mod

    stats_mod._groups_cache = None

    engine = _make_engine()
    await _setup_schema(engine)
    factory = _make_session_factory(engine)

    async with factory() as sess:
        now = _now_iso()
        # community=NULL (never assigned)
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, tags, content_hash, "
                "updated_at, created_at, community) "
                "VALUES (:id, :vid, 'null_comm.md', 'Null Comm', 'concept', '[]', 'hn', :ts, :ts, NULL)"
            ),
            {"id": str(uuid.uuid4()), "vid": VAULT_ID, "ts": now},
        )
        # community=-1 (unassigned sentinel)
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, tags, content_hash, "
                "updated_at, created_at, community) "
                "VALUES (:id, :vid, 'neg_comm.md', 'Neg Comm', 'concept', '[]', 'hm', :ts, :ts, -1)"
            ),
            {"id": str(uuid.uuid4()), "vid": VAULT_ID, "ts": now},
        )
        # one assigned page
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, tags, content_hash, "
                "updated_at, created_at, community) "
                "VALUES (:id, :vid, 'good.md', 'Good Page', 'concept', '[]', 'hg', :ts, :ts, 0)"
            ),
            {"id": str(uuid.uuid4()), "vid": VAULT_ID, "ts": now},
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
            resp = await client.get("/stats/groups")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    groups = body["groups"]

    # Only 1 group (community=0); null and -1 excluded
    assert len(groups) == 1, f"Expected 1 group, got {len(groups)}: {groups}"
    assert groups[0]["community"] == 0
    assert groups[0]["pages_total"] == 1


@pytest.mark.asyncio
async def test_stats_groups_no_groups_when_no_assigned_community() -> None:
    """
    T-STATS-016b: all pages unassigned → groups list is empty.
    """
    import app.stats as stats_mod

    stats_mod._groups_cache = None

    engine = _make_engine()
    await _setup_schema(engine)
    factory = _make_session_factory(engine)

    async with factory() as sess:
        now = _now_iso()
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, tags, content_hash, "
                "updated_at, created_at, community) "
                "VALUES (:id, :vid, 'p.md', 'Page', 'concept', '[]', 'h', :ts, :ts, NULL)"
            ),
            {"id": str(uuid.uuid4()), "vid": VAULT_ID, "ts": now},
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
            resp = await client.get("/stats/groups")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["groups"] == [], f"Expected empty groups list, got {body['groups']}"


@pytest.mark.asyncio
async def test_stats_groups_memo_invalidated_on_data_version_bump() -> None:
    """
    T-STATS-017: cache is invalidated when data_version bumps.
    """
    import app.stats as stats_mod

    stats_mod._groups_cache = None

    engine = _make_engine()
    await _setup_schema(engine)
    factory = _make_session_factory(engine)

    async with factory() as sess:
        now = _now_iso()
        await sess.execute(
            sa_text(
                "INSERT INTO pages "
                "(id, vault_id, file_path, title, type, tags, content_hash, "
                "updated_at, created_at, community) "
                "VALUES (:id, :vid, 'pg.md', 'Init Page', 'concept', '[]', 'h0', :ts, :ts, 0)"
            ),
            {"id": str(uuid.uuid4()), "vid": VAULT_ID, "ts": now},
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
            resp1 = await client.get("/stats/groups")

    assert resp1.status_code == 200
    assert len(resp1.json()["groups"]) == 1
    # Cache is populated
    assert stats_mod._groups_cache is not None
    old_key = stats_mod._groups_cache[0]
    assert old_key == 5  # data_version seeded as 5 in _setup_schema

    # Simulate a data_version bump (direct DB update)
    async with factory() as sess:
        await sess.execute(
            sa_text("UPDATE vault_state SET data_version = 6 WHERE vault_id = :vid"),
            {"vid": VAULT_ID},
        )
        await sess.commit()

    # Request again with bumped version — cache should be rebuilt
    def _make_ctx2() -> Any:
        @asynccontextmanager
        async def _ctx2() -> AsyncIterator[AsyncSession]:
            async with factory() as s:
                yield s

        return _ctx2()

    with (
        patch("app.stats.settings") as mock_settings,
        patch("app.stats.get_session", side_effect=_make_ctx2),
    ):
        mock_settings.vault_id = VAULT_ID
        test_app2 = _make_test_app(engine)
        async with AsyncClient(
            transport=ASGITransport(app=test_app2), base_url="http://test"
        ) as client:
            resp2 = await client.get("/stats/groups")

    assert resp2.status_code == 200
    assert stats_mod._groups_cache is not None
    new_key = stats_mod._groups_cache[0]
    assert new_key == 6, f"Expected cache key 6 after version bump, got {new_key}"
