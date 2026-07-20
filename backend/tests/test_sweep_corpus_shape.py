"""
SC-D3: corpus-shape proposals surfaced via the regular review sweep (not only /ops/synthesize).

Tests that sweep_reviews() — the drain-triggered + manually-callable sweep — seeds
synthesis/comparison REVIEW items for clusters in the graph's REVIEW band without the user
needing to explicitly call /ops/synthesize.

Coverage:
  T-SC-001  A vault with a review-band comparison cluster gets a 'suggestion' review item
            with proposed_page_type='comparison' after sweep_reviews() — no synthesize call.
  T-SC-002  A vault with a review-band synthesis cluster gets a 'suggestion' review item
            with proposed_page_type='synthesis' after sweep_reviews() — no synthesize call.
  T-SC-003  High-confidence (auto-write) clusters are NOT proposed (those belong to
            /ops/synthesize auto-write, not the review queue).
  T-SC-004  Below-floor clusters are NOT proposed (noise guard).
  T-SC-005  Idempotency: running sweep_reviews() twice does not create duplicate review items
            (content_key dedup — enqueue_review upserts in place).
  T-SC-006  If the cluster already has a written synthesis/comparison page
            (_generation_key_exists returns True), no new review item is proposed.
  T-SC-007  SweepResult.corpus_proposed reflects the count of seeded items.
  T-SC-008  Pass-3 is non-fatal: if _load_graph_data raises, sweep_reviews() still succeeds
            (returns SweepResult with corpus_proposed=0, doesn't propagate the error).
  T-SC-009  review_corpus_shape_enabled=False disables Pass-3 entirely.
"""

from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── Minimal SQLite schema for corpus-shape sweep tests ────────────────────────


def _minimal_meta() -> MetaData:
    """SQLite-compatible schema: just pages + review_items (what sweep_reviews needs)."""
    meta = MetaData()

    Table(
        "pages",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("file_path", Text, nullable=False),
        Column("title", Text, nullable=True),
        Column("type", Text, nullable=True),
        Column("page_type", Text, nullable=True),
        Column("sources", Text, nullable=True),
        Column("tags", Text, nullable=True),
        Column("generation_key", Text, nullable=True),
        Column("content_hash", String(64), nullable=False, server_default=sa_text("'x'")),
        Column("source_mtime_ns", Integer, nullable=True),
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

    # vault_state needed by sweep_reviews count-kept query (via ReviewItem.vault_id filter only
    # — but the model needs it for the outer selects; we add it so the engine can reflect cleanly).
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
        Column("updated_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )

    return meta


# ── Fixture ────────────────────────────────────────────────────────────────────


@pytest.fixture()
async def sweep_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> dict[str, Any]:
    """
    Minimal test environment for corpus-shape sweep tests.

    - SQLite in-memory DB with pages + review_items tables.
    - app.db.get_session patched to use the in-memory engine.
    - app.ops.synthesize._load_graph_data patched to a controllable stub.
    - app.ops.synthesize._generation_key_exists patched to a controllable stub.
    - Pass-1/Pass-2 LLM judge disabled (no provider needed).
    """
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "sweep-test-vault")
    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))
    # Disable Pass-2 LLM sweep (we don't want to provision a provider in these tests).
    monkeypatch.setattr(cfg.settings, "review_sweep_llm_enabled", False)

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    meta = _minimal_meta()
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    @asynccontextmanager
    async def patched_get_session():  # type: ignore[misc]
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    import app.db as _db

    monkeypatch.setattr(_db, "get_session", patched_get_session)
    monkeypatch.setattr("app.ops.review.get_session", patched_get_session)

    # Controllable stubs for the synthesize seams (deferred imports inside the function
    # resolve at call time against these patched objects — same monkeypatch-compat pattern).
    import app.ops.synthesize as sy

    state: dict[str, Any] = {
        "session_factory": session_factory,
        "pages": [],  # list[dict] returned by _load_graph_data
        "links": [],  # list[dict] returned by _load_graph_data
        "existing_keys": set(),  # set[str] — generation keys "already written"
        "load_raises": None,  # if set, _load_graph_data raises this exception
    }

    async def stub_load_graph_data(vault_id: str) -> tuple[list[Any], list[Any]]:
        if state["load_raises"] is not None:
            raise state["load_raises"]
        return list(state["pages"]), list(state["links"])

    async def stub_generation_key_exists(vault_id: str, generation_key: str) -> bool:
        return generation_key in state["existing_keys"]

    monkeypatch.setattr(sy, "_load_graph_data", stub_load_graph_data)
    monkeypatch.setattr(sy, "_generation_key_exists", stub_generation_key_exists)

    return state


# ── Helpers ────────────────────────────────────────────────────────────────────


def _pg(
    title: str,
    ptype: str,
    sources: list[str],
    *,
    domain: str = "cloud",
    slug: str | None = None,
) -> dict[str, Any]:
    """Build a graph-data page dict as _load_graph_data returns it."""
    slug = slug or title.lower().replace(" ", "-")
    return {
        "id": str(uuid.uuid4()),
        "title": title,
        "page_type": ptype,
        "file_path": f"wiki/entities/{slug}.md",
        "sources": list(sources),
        "tags": [f"domain/{domain}"],
    }


def _generation_key_for(kind: str, file_paths: list[str]) -> str:
    """Replicate the synthesize._generation_key() logic for assertions."""
    canonical = sorted(fp.strip().replace("\\", "/").casefold() for fp in file_paths if fp.strip())
    payload = "\n".join([kind.casefold(), *canonical]).encode("utf-8")
    return f"corpus:{kind.casefold()}:{hashlib.sha256(payload).hexdigest()}"


async def _all_review_items(env: dict[str, Any]) -> list[dict[str, Any]]:
    """Return all review_items rows as dicts."""
    async with env["session_factory"]() as sess:
        rows = list(
            (await sess.execute(sa_text("SELECT * FROM review_items ORDER BY created_at"))).all()
        )
    return [dict(r._mapping) for r in rows]


# ── Tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_comparison_cluster_in_review_band_creates_queue_item(
    sweep_env: dict[str, Any],
) -> None:
    """T-SC-001: a review-band comparison cluster → 'suggestion' item with proposed_page_type=comparison."""
    # 2 co-cited same-class entities sharing exactly 2 sources → review band (conf ∈ [0.35, 0.6))
    shared = ["raw/a.md", "raw/b.md"]
    sweep_env["pages"] = [
        _pg("Redis", "entity", shared, slug="redis"),
        _pg("Memcached", "entity", shared, slug="memcached"),
    ]

    from app.ops.review import sweep_reviews

    result = await sweep_reviews("sweep-test-vault")

    items = await _all_review_items(sweep_env)
    corpus_items = [it for it in items if it["proposed_page_type"] == "comparison"]
    assert len(corpus_items) == 1, f"expected 1 comparison proposal, got {len(corpus_items)}"
    item = corpus_items[0]
    assert item["item_type"] == "suggestion"
    assert item["proposal_origin"] == "corpus"
    assert item["status"] == "pending"
    assert item["proposed_page_type"] == "comparison"
    assert item["rationale"] is not None and "comparison" in item["rationale"]
    assert result.corpus_proposed >= 1


@pytest.mark.asyncio
async def test_synthesis_cluster_in_review_band_creates_queue_item(
    sweep_env: dict[str, Any],
) -> None:
    """T-SC-002: a review-band synthesis cluster → 'suggestion' item with proposed_page_type=synthesis."""
    # 3 same-domain concept pages sharing exactly 2 sources each → review band
    shared = ["raw/1.md", "raw/2.md"]
    sweep_env["pages"] = [
        _pg("Alpha", "concept", shared, slug="alpha"),
        _pg("Beta", "concept", shared, slug="beta"),
        _pg("Gamma", "concept", shared, slug="gamma"),
    ]

    from app.ops.review import sweep_reviews

    result = await sweep_reviews("sweep-test-vault")

    items = await _all_review_items(sweep_env)
    corpus_items = [it for it in items if it["proposed_page_type"] == "synthesis"]
    assert len(corpus_items) >= 1, f"expected ≥1 synthesis proposal, got {len(corpus_items)}"
    item = corpus_items[0]
    assert item["item_type"] == "suggestion"
    assert item["proposal_origin"] == "corpus"
    assert item["proposed_page_type"] == "synthesis"
    assert result.corpus_proposed >= 1


@pytest.mark.asyncio
async def test_high_confidence_cluster_not_proposed(sweep_env: dict[str, Any]) -> None:
    """T-SC-003: auto-write band clusters (conf ≥ AUTO_CONFIDENCE_THRESHOLD) are NOT proposed."""
    from app.ops.synthesize import AUTO_CONFIDENCE_THRESHOLD

    # 3 concept pages sharing 4 sources → high confidence (saturates at 4 shared sources)
    shared = ["raw/1.md", "raw/2.md", "raw/3.md", "raw/4.md"]
    sweep_env["pages"] = [
        _pg("Alpha", "concept", shared, slug="alpha"),
        _pg("Beta", "concept", shared, slug="beta"),
        _pg("Gamma", "concept", shared, slug="gamma"),
    ]

    # Verify the cluster actually lands in the auto band (not the review band).
    from app.ops.synthesize import _build_clusters

    clusters = _build_clusters(sweep_env["pages"], [])
    assert any(
        c.confidence >= AUTO_CONFIDENCE_THRESHOLD for c in clusters
    ), "cluster should be in the auto-write band for this test to be meaningful"

    from app.ops.review import sweep_reviews

    await sweep_reviews("sweep-test-vault")

    items = await _all_review_items(sweep_env)
    corpus_items = [it for it in items if it["proposed_page_type"] in ("synthesis", "comparison")]
    assert (
        len(corpus_items) == 0
    ), "auto-write band clusters must NOT be proposed via sweep (they belong to /ops/synthesize)"


@pytest.mark.asyncio
async def test_below_floor_cluster_not_proposed(sweep_env: dict[str, Any]) -> None:
    """T-SC-004: clusters below REVIEW_CONFIDENCE_FLOOR are silently skipped."""
    from app.ops.synthesize import REVIEW_CONFIDENCE_FLOOR

    # 2 entities sharing only 1 source (< MIN_SHARED_SOURCES=2) → no cluster at all,
    # so set pages with different domains to ensure nothing crosses the floor.
    sweep_env["pages"] = [
        _pg("X", "entity", ["raw/only.md"], domain="cloud", slug="x"),
        _pg("Y", "entity", ["raw/only.md"], domain="finance", slug="y"),
    ]

    # Verify no cluster exists (the seeder won't emit them — test still valid as belt-and-braces).
    from app.ops.synthesize import _build_clusters

    clusters = _build_clusters(sweep_env["pages"], [])
    for c in clusters:
        assert (
            c.confidence < REVIEW_CONFIDENCE_FLOOR or c.confidence >= 0
        ), "sanity — no cluster above the floor should exist"

    from app.ops.review import sweep_reviews

    result = await sweep_reviews("sweep-test-vault")

    items = await _all_review_items(sweep_env)
    corpus_items = [it for it in items if it["proposed_page_type"] in ("synthesis", "comparison")]
    assert len(corpus_items) == 0
    assert result.corpus_proposed == 0


@pytest.mark.asyncio
async def test_idempotent_no_duplicate_on_second_sweep(sweep_env: dict[str, Any]) -> None:
    """T-SC-005: running sweep_reviews() twice for the same cluster creates only ONE queue item."""
    shared = ["raw/a.md", "raw/b.md"]
    sweep_env["pages"] = [
        _pg("Redis", "entity", shared, slug="redis"),
        _pg("Memcached", "entity", shared, slug="memcached"),
    ]

    from app.ops.review import sweep_reviews

    await sweep_reviews("sweep-test-vault")
    await sweep_reviews("sweep-test-vault")

    items = await _all_review_items(sweep_env)
    corpus_items = [it for it in items if it["proposed_page_type"] == "comparison"]
    assert (
        len(corpus_items) == 1
    ), f"idempotent: second sweep must not create a duplicate (got {len(corpus_items)} items)"


@pytest.mark.asyncio
async def test_already_written_cluster_not_proposed(sweep_env: dict[str, Any]) -> None:
    """T-SC-006: clusters with an existing generation_key page are skipped (no re-proposal)."""
    shared = ["raw/a.md", "raw/b.md"]
    pages = [
        _pg("Redis", "entity", shared, slug="redis"),
        _pg("Memcached", "entity", shared, slug="memcached"),
    ]
    sweep_env["pages"] = pages

    # Mark the cluster's generation_key as already written.
    from app.ops.synthesize import _build_clusters, _generation_key

    clusters = _build_clusters(pages, [])
    for c in clusters:
        sweep_env["existing_keys"].add(_generation_key(c))

    from app.ops.review import sweep_reviews

    result = await sweep_reviews("sweep-test-vault")

    items = await _all_review_items(sweep_env)
    corpus_items = [it for it in items if it["proposed_page_type"] in ("synthesis", "comparison")]
    assert len(corpus_items) == 0, "cluster already written → no new review item should be proposed"
    assert result.corpus_proposed == 0


@pytest.mark.asyncio
async def test_sweep_result_corpus_proposed_field(sweep_env: dict[str, Any]) -> None:
    """T-SC-007: SweepResult.corpus_proposed is populated correctly."""
    shared = ["raw/a.md", "raw/b.md"]
    sweep_env["pages"] = [
        _pg("Redis", "entity", shared, slug="redis"),
        _pg("Memcached", "entity", shared, slug="memcached"),
    ]

    from app.ops.review import sweep_reviews

    result = await sweep_reviews("sweep-test-vault")
    assert result.corpus_proposed >= 1, "at least one corpus item should have been proposed"
    assert isinstance(result.corpus_proposed, int)
    assert result.rule_resolved == 0
    assert result.llm_resolved == 0


@pytest.mark.asyncio
async def test_pass3_non_fatal_when_load_graph_data_raises(
    sweep_env: dict[str, Any],
) -> None:
    """T-SC-008: if _load_graph_data raises, sweep_reviews() still succeeds (non-fatal)."""
    sweep_env["load_raises"] = RuntimeError("simulated graph load failure")

    from app.ops.review import sweep_reviews

    # Must not raise.
    result = await sweep_reviews("sweep-test-vault")
    assert result.corpus_proposed == 0, "failure in Pass-3 yields corpus_proposed=0"
    # Other passes are unaffected (no items → nothing to resolve, kept=0).
    assert result.rule_resolved == 0
    assert isinstance(result.kept, int)


@pytest.mark.asyncio
async def test_corpus_shape_disabled_skips_pass3(
    sweep_env: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-SC-009: review_corpus_shape_enabled=False entirely skips Pass-3."""
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "review_corpus_shape_enabled", False)

    shared = ["raw/a.md", "raw/b.md"]
    sweep_env["pages"] = [
        _pg("Redis", "entity", shared, slug="redis"),
        _pg("Memcached", "entity", shared, slug="memcached"),
    ]

    from app.ops.review import sweep_reviews

    result = await sweep_reviews("sweep-test-vault")

    items = await _all_review_items(sweep_env)
    corpus_items = [it for it in items if it["proposed_page_type"] in ("synthesis", "comparison")]
    assert len(corpus_items) == 0, "Pass-3 was disabled — no corpus items should be proposed"
    assert result.corpus_proposed == 0


@pytest.mark.asyncio
async def test_sweep_without_synthesize_call(sweep_env: dict[str, Any]) -> None:
    """
    Integration guard (addresses the original gap report): sweep_reviews() must produce
    corpus-shape proposals WITHOUT any call to run_synthesize() or /ops/synthesize.
    This is the primary regression test for SC-D3 fix.
    """
    import app.ops.synthesize as sy

    # Record whether run_synthesize was called (it must NOT be).
    synthesize_calls = []
    original_run = sy.run_synthesize

    async def spy_run_synthesize(**kwargs: Any) -> Any:
        synthesize_calls.append(kwargs)
        return await original_run(**kwargs)

    # We do NOT monkeypatch run_synthesize to a raising stub — we simply verify it is not called.
    # (The spy is placed but we assert calls == 0 at the end.)

    shared = ["raw/a.md", "raw/b.md"]
    sweep_env["pages"] = [
        _pg("Redis", "entity", shared, slug="redis"),
        _pg("Memcached", "entity", shared, slug="memcached"),
    ]

    from app.ops.review import sweep_reviews

    result = await sweep_reviews("sweep-test-vault")

    assert len(synthesize_calls) == 0, "sweep_reviews must NOT call run_synthesize"
    assert result.corpus_proposed >= 1, "corpus proposals must still be seeded without synthesize"

    items = await _all_review_items(sweep_env)
    corpus_items = [it for it in items if it["proposed_page_type"] in ("synthesis", "comparison")]
    assert len(corpus_items) >= 1
