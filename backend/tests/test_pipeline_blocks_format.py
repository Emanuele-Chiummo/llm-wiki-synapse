"""Integration tests for the ingest_pipeline_format rollback lever (ADR-0076).

Drives run_ingest_pipeline against a real SQLite in-memory DB + a temporary vault + a fake
provider, proving:

  • ingest_pipeline_format="blocks" (config override) runs the block-based orchestrated path:
    FILE blocks land at their block paths, a CUSTOM type (thesis) persists as pages.type, body
    [[wikilinks]] are persisted, and the source-summary fallback still fires,
  • with the flag UNSET (default "json") the existing JSON loop path is unchanged and still
    writes pages.

Only the ingest write/persistence surface is under test — the fire-and-forget post-write hooks
(overview regen, review proposals, wikilink enrichment, …) are stubbed to keep the test focused
and deterministic (they have their own suites). The ingest_runs lifecycle helpers are stubbed the
same way tests/test_provider_routing.py does.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import app.config_overrides as config_overrides
import app.ingest.orchestrator as orch
import pytest
from app.ingest.provider.base import InferenceProvider
from app.ingest.schemas import (
    Analysis,
    Message,
    ProviderCapabilities,
    SuggestedPage,
    Usage,
    WikiFrontmatter,
    WikiPage,
)
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    Text,
    select,
)
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

ORIGIN = "raw/sources/doc.md"
ABS_SOURCE = "/abs/raw/sources/doc.md"

SCHEMA_MD = """# Project Schema

## Page Types

| Type | Directory |
| --- | --- |
| entity | wiki/entities |
| concept | wiki/concepts |
| thesis | wiki/thesis |
| source | wiki/sources |
"""

ANALYSIS = "## Recommendations\n- Create a thesis page and an entity page for Acme Corp."

GEN_BLOCKS = """---FILE: wiki/thesis/core-thesis.md---
---
type: thesis
title: Core Thesis
created: 2026-07-14
updated: 2026-07-14
sources: [doc.md]
---

# Core Thesis

The central claim links [[Acme Corp]] to measurable market outcomes.
---END FILE---

---FILE: wiki/entities/acme.md---
---
type: entity
title: Acme Corp
created: 2026-07-14
updated: 2026-07-14
sources: [doc.md]
---

# Acme Corp

Acme Corp is central to the [[Core Thesis]].
---END FILE---
"""


class _FakeQdrant:
    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}

    async def upsert(self, collection_name: str, points: list[Any]) -> None:
        for pt in points:
            self.points[str(pt.id)] = pt.payload or {}

    async def delete(self, collection_name: str, points_selector: Any) -> None:
        for pid in points_selector.points:
            self.points.pop(str(pid), None)


class _BlockProvider(InferenceProvider):
    """Orchestrated (non-agentic) provider that scripts complete() for the block path."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="local",
            supports_tools=False,
            supports_agentic_loop=False,
            max_context=8192,
            name="BlockFake",
        )

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:  # pragma: no cover
        raise NotImplementedError

    async def generate(  # pragma: no cover
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        raise NotImplementedError

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError

    async def complete(self, system: str, prompt: str, *, max_tokens: int) -> str:
        self._record_usage(Usage(input_tokens=10, output_tokens=5, total_cost_usd=0.0))
        return self._responses.pop(0) if self._responses else ""


class _JsonProvider(InferenceProvider):
    """Orchestrated (non-agentic) provider that produces one JSON WikiPage (default path)."""

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="local",
            supports_tools=False,
            supports_agentic_loop=False,
            max_context=8192,
            name="JsonFake",
        )

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        self._record_usage(Usage(input_tokens=10, output_tokens=5, total_cost_usd=0.0))
        return Analysis(
            topics=["widgets"],
            entities=["Acme Corp"],
            language="en",
            suggested_pages=[SuggestedPage(title="Widget Platform", type="concept")],
            summary="A short summary.",
        )

    async def generate(
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        self._record_usage(Usage(input_tokens=20, output_tokens=10, total_cost_usd=0.0))
        return [
            WikiPage(
                title="Widget Platform",
                type="concept",
                content="# Widget Platform\n\nBuilt by [[Acme Corp]].",
                frontmatter=WikiFrontmatter(
                    type="concept", title="Widget Platform", sources=[ORIGIN], lang="en"
                ),
            )
        ]

    async def chat(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:  # pragma: no cover
        raise NotImplementedError


async def _anoop(*_a: Any, **_k: Any) -> None:
    return None


@pytest.fixture()
async def pipeline_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict[str, Any]]:
    """SQLite (pages/vault_state/links) + temp vault + fake embedding/qdrant + stubbed queue,
    ingest_runs lifecycle and fire-and-forget hooks (mirrors test_provider_routing +
    test_ingest_incremental)."""
    from app import config as cfg
    from app.embeddings import FakeEmbeddingClient, set_embedding_client

    vault_root = tmp_path / "vault"
    (vault_root / "raw" / "sources").mkdir(parents=True)
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "log.md").write_text(
        "---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n", encoding="utf-8"
    )
    (vault_root / "schema.md").write_text(SCHEMA_MD, encoding="utf-8")
    (vault_root / "purpose.md").write_text("# Purpose\n\nStudy Acme Corp.\n", encoding="utf-8")

    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(
        type(cfg.settings), "log_md_path", property(lambda self: wiki_dir / "log.md")
    )

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
        Column("tags", Text, nullable=True),
        Column("generation_key", Text, nullable=True),
        Column("content_hash", String(64), nullable=False),
        Column("source_mtime_ns", BigInteger, nullable=True),
        Column("qdrant_point_id", String(36), nullable=True),
        Column("x", Float, nullable=True),
        Column("y", Float, nullable=True),
        Column("community", Integer, nullable=True),
        Column("pinned", Integer, nullable=False, server_default=sa_text("0")),
        Column("deleted_at", Text, nullable=True),
        Column("created_at", Text, nullable=False),
        Column("updated_at", Text, nullable=False),
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
        Column("cli_oauth_token", Text, nullable=True),
        Column("cli_oauth_token_encrypted", LargeBinary, nullable=True),
        Column("web_search_api_keys_encrypted", LargeBinary, nullable=True),
        Column("searxng_url_db", Text, nullable=True),
        Column("searxng_categories_db", Text, nullable=True),
        Column("searxng_max_queries_db", Integer, nullable=True),
        Column("output_language", Text, nullable=True),
        Column("updated_at", Text, nullable=False),
    )
    Table(
        "links",
        meta,
        Column("id", String(36), primary_key=True),
        Column("source_page_id", String(36), nullable=False),
        Column("target_title", Text, nullable=False),
        Column("target_page_id", String(36), nullable=True),
        Column("alias", Text, nullable=True),
        Column("dangling", Boolean, nullable=False, server_default=sa_text("0")),
        Column("created_at", Text, nullable=False),
    )

    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with session_factory() as session:
        await session.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                "VALUES (:id, :vault_id, 0, datetime('now'))"
            ),
            {"id": str(uuid.uuid4()), "vault_id": "test-vault"},
        )
        await session.commit()

    set_embedding_client(FakeEmbeddingClient(dim=8))
    fake_qdrant = _FakeQdrant()

    from contextlib import asynccontextmanager

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
    monkeypatch.setattr("app.ingest.orchestrator.get_session", patched_get_session)
    monkeypatch.setattr(
        "app.ingest.orchestrator.upsert_point",
        lambda **kwargs: fake_qdrant.upsert(
            "synapse_pages",
            [
                type(
                    "Pt",
                    (),
                    {
                        "id": str(kwargs["page_id"]),
                        "vector": kwargs["vector"],
                        "payload": {"file_path": kwargs["file_path"], "title": kwargs["title"]},
                    },
                )()
            ],
        ),
    )

    # ── Stub the ingest_runs lifecycle + queue (like test_provider_routing) ────────
    import asyncio as _asyncio

    runs: list[dict[str, Any]] = []

    async def fake_open_ingest_run(**_kwargs: Any) -> uuid.UUID:
        return uuid.uuid4()

    async def fake_finalize_ingest_run(**kwargs: Any) -> None:
        runs.append(kwargs)

    monkeypatch.setattr(orch, "_open_ingest_run", fake_open_ingest_run)
    monkeypatch.setattr(orch, "_finalize_ingest_run", fake_finalize_ingest_run)

    from app.ingest.queue_manager import IngestQueueManager

    class _FakeHandle:
        cancel_event = _asyncio.Event()
        written_page_ids: list[Any] = []

    fake_queue = IngestQueueManager.__new__(IngestQueueManager)
    fake_queue.open_run = lambda run_id, source_path: _FakeHandle()  # type: ignore[attr-defined]
    fake_queue.finalize = lambda *a, **kw: None  # type: ignore[attr-defined]
    fake_queue.get_retry_count = lambda path: 0  # type: ignore[attr-defined]
    fake_queue.record_written = lambda *a, **kw: None  # type: ignore[attr-defined]
    fake_queue.set_route = lambda *a, **kw: None  # type: ignore[attr-defined]
    fake_queue.set_phase = lambda *a, **kw: None  # type: ignore[attr-defined]
    # BE-QUEUE-1/2 (1.9.4 W3): run_ingest_pipeline now gates on the capability semaphore and
    # touches the rate-limit ladder on both terminal paths — stub them as no-ops.

    async def _noop_acquire_capability_slot(mode: str) -> None:  # type: ignore[no-untyped-def]
        return None

    fake_queue.acquire_capability_slot = _noop_acquire_capability_slot  # type: ignore[attr-defined]
    fake_queue.release_capability_slot = lambda *a, **kw: None  # type: ignore[attr-defined]
    fake_queue.pause_for_rate_limit = lambda *a, **kw: 0.0  # type: ignore[attr-defined]
    fake_queue.reset_rate_limit_backoff = lambda *a, **kw: None  # type: ignore[attr-defined]
    monkeypatch.setattr(orch, "ingest_queue", fake_queue)

    # ── Stub the fire-and-forget post-write hooks (own suites cover them) ─────────
    monkeypatch.setattr(orch, "_update_overview", _anoop)
    monkeypatch.setattr(orch, "_index_index_and_log_files", _anoop)
    monkeypatch.setattr(orch, "_auto_tag_written_pages", _anoop)
    import app.ops.enrich_wikilinks as _enrich_mod
    import app.ops.review as _review_mod

    monkeypatch.setattr(_review_mod, "propose_reviews", _anoop)
    monkeypatch.setattr(_review_mod, "sweep_reviews", _anoop)
    monkeypatch.setattr(_review_mod, "generate_purpose_suggestion", _anoop)
    monkeypatch.setattr(_review_mod, "generate_schema_suggestion", _anoop)
    monkeypatch.setattr(_enrich_mod, "enrich_wikilinks", _anoop)

    yield {"session_factory": session_factory, "vault_root": vault_root, "runs": runs}

    set_embedding_client(None)  # type: ignore[arg-type]


async def _load_page(env: dict[str, Any], rel_path: str) -> Any:
    from app.models import Page

    async with env["session_factory"]() as session:
        row = await session.execute(
            select(Page).where(Page.file_path == rel_path, Page.deleted_at.is_(None))
        )
        return row.scalar_one_or_none()


async def test_blocks_format_writes_custom_type_and_wikilinks(
    pipeline_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models import Link

    provider = _BlockProvider([ANALYSIS, GEN_BLOCKS])
    monkeypatch.setattr(orch, "resolve_provider", lambda _row: provider)
    # ingest_pipeline_format="blocks" via the config-override cache (auto-reverted after the test).
    monkeypatch.setitem(config_overrides._cache, "ingest_pipeline_format", "blocks")

    result = await orch.run_ingest_pipeline(
        provider_config_row=object(),
        source_text="The Acme Corp report describes measurable market outcomes.",
        origin_source=ORIGIN,
        abs_source=ABS_SOURCE,
    )

    assert result.route == "orchestrated"
    assert result.converged is True
    # thesis + entity block pages + the guaranteed source summary.
    assert result.pages_written == 3

    # The CUSTOM type persists as the raw pages.type string (NOT constrained to PageType).
    thesis = await _load_page(pipeline_env, "wiki/thesis/core-thesis.md")
    assert thesis is not None
    assert thesis.page_type == "thesis"
    assert ORIGIN in (thesis.sources or [])
    assert (pipeline_env["vault_root"] / "wiki" / "thesis" / "core-thesis.md").is_file()

    entity = await _load_page(pipeline_env, "wiki/entities/acme.md")
    assert entity is not None and entity.page_type == "entity"

    # The source-summary fallback still fires (F3) even though the model omitted a source block.
    source = await _load_page(pipeline_env, "wiki/sources/doc.md")
    assert source is not None and source.page_type == "source"

    # Body [[wikilinks]] were persisted for the thesis page (K5).
    async with pipeline_env["session_factory"]() as session:
        rows = list(
            (await session.execute(select(Link).where(Link.source_page_id == thesis.id)))
            .scalars()
            .all()
        )
    assert {r.target_title for r in rows} == {"Acme Corp"}


async def test_blocks_format_persists_diagnostics_on_convergence(
    pipeline_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """1.9.1 W5 (NC-1): a converged block-route run persists diagnostics on ingest_runs."""
    provider = _BlockProvider([ANALYSIS, GEN_BLOCKS])
    monkeypatch.setattr(orch, "resolve_provider", lambda _row: provider)
    monkeypatch.setitem(config_overrides._cache, "ingest_pipeline_format", "blocks")

    result = await orch.run_ingest_pipeline(
        provider_config_row=object(),
        source_text="The Acme Corp report describes measurable market outcomes.",
        origin_source=ORIGIN,
        abs_source=ABS_SOURCE,
    )
    assert result.converged is True

    finalize_kwargs = pipeline_env["runs"][-1]
    diagnostics = finalize_kwargs["diagnostics"]
    assert diagnostics is not None
    assert diagnostics["stop_reason"] == "converged"
    assert diagnostics["iterations"] == 1
    assert diagnostics["last_errors"] == []
    assert diagnostics["token_budget"] == 60_000  # default block-loop token_budget


async def test_blocks_format_persists_diagnostics_on_nonconvergence(
    pipeline_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """1.9.1 W5 (NC-1, live finding): a converged_false run's diagnostics carries the LAST
    iteration's validation errors + stop_reason so the UI can explain itself instead of a bare
    "Non convergito" label. Reproduces the observed scenario: max_iter exhausted because the
    provider never emits a valid FILE block.
    """
    provider = _BlockProvider(
        [ANALYSIS, "no file blocks", "still nothing", "prose only, no blocks"]
    )
    monkeypatch.setattr(orch, "resolve_provider", lambda _row: provider)
    monkeypatch.setitem(config_overrides._cache, "ingest_pipeline_format", "blocks")

    result = await orch.run_ingest_pipeline(
        provider_config_row=object(),
        source_text="The Acme Corp report describes measurable market outcomes.",
        origin_source=ORIGIN,
        abs_source=ABS_SOURCE,
    )
    assert result.converged is False

    finalize_kwargs = pipeline_env["runs"][-1]
    diagnostics = finalize_kwargs["diagnostics"]
    assert diagnostics is not None
    assert diagnostics["stop_reason"] == "max_iter"
    assert diagnostics["iterations"] == 3  # default block-loop max_iter
    assert diagnostics["last_errors"] != []
    assert any("FILE blocks" in e for e in diagnostics["last_errors"])


async def test_default_format_is_blocks_without_override(
    pipeline_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1.7.0 flip (config.py): with NO ingest_pipeline_format override the DEFAULT is now "blocks".
    # A provider with complete() runs the block loop, so the blocks-only custom "thesis" type lands.
    # This guards the default flip itself (the JSON loop would never write a `thesis` page).
    assert "ingest_pipeline_format" not in config_overrides._cache

    provider = _BlockProvider([ANALYSIS, GEN_BLOCKS])
    monkeypatch.setattr(orch, "resolve_provider", lambda _row: provider)

    result = await orch.run_ingest_pipeline(
        provider_config_row=object(),
        source_text="The Acme Corp report describes measurable market outcomes.",
        origin_source=ORIGIN,
        abs_source=ABS_SOURCE,
    )

    assert result.route == "orchestrated"
    assert result.converged is True
    thesis = await _load_page(pipeline_env, "wiki/thesis/core-thesis.md")
    assert thesis is not None and thesis.page_type == "thesis"


async def test_json_format_rollback_still_writes_pages(
    pipeline_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # ingest_pipeline_format="json" (the 1.7.0 rollback lever; "blocks" is now the default) →
    # the legacy JSON loop path runs unchanged.
    monkeypatch.setitem(config_overrides._cache, "ingest_pipeline_format", "json")

    provider = _JsonProvider()
    monkeypatch.setattr(orch, "resolve_provider", lambda _row: provider)

    result = await orch.run_ingest_pipeline(
        provider_config_row=object(),
        source_text="The Acme Corp report describes measurable market outcomes.",
        origin_source=ORIGIN,
        abs_source=ABS_SOURCE,
    )

    assert result.route == "orchestrated"
    assert result.converged is True
    # concept page + the guaranteed source summary (the JSON path is unchanged).
    assert result.pages_written == 2

    concept = await _load_page(pipeline_env, "wiki/concepts/widget-platform.md")
    assert concept is not None and concept.page_type == "concept"
    source = await _load_page(pipeline_env, "wiki/sources/doc.md")
    assert source is not None and source.page_type == "source"


# ── D3 WS-C: REVIEW block enqueue (ADR-0079 §3) ──────────────────────────────

# A generation response that includes one REVIEW block after the FILE blocks.
_GEN_BLOCKS_WITH_REVIEW = GEN_BLOCKS + """
---REVIEW: missing-page | Missing Entity Acme Supplier---
The report references an upstream supplier that is not in the wiki yet.
SEARCH: Acme Corp supplier network
---END REVIEW---
"""


@pytest.fixture()
async def pipeline_env_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict[str, Any]]:
    """pipeline_env extended with a review_items table for D3/WS-C tests (ADR-0079)."""
    import asyncio as _asyncio
    from contextlib import asynccontextmanager

    from app import config as cfg
    from app.embeddings import FakeEmbeddingClient, set_embedding_client

    vault_root = tmp_path / "vault"
    (vault_root / "raw" / "sources").mkdir(parents=True)
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "log.md").write_text(
        "---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n", encoding="utf-8"
    )
    (vault_root / "schema.md").write_text(SCHEMA_MD, encoding="utf-8")
    (vault_root / "purpose.md").write_text("# Purpose\n\nStudy Acme Corp.\n", encoding="utf-8")

    monkeypatch.setattr(cfg.settings, "vault_path", str(vault_root))
    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))
    monkeypatch.setattr(type(cfg.settings), "wiki_dir", property(lambda self: wiki_dir))
    monkeypatch.setattr(
        type(cfg.settings), "log_md_path", property(lambda self: wiki_dir / "log.md")
    )

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _meta = MetaData()
    Table(
        "pages",
        _meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("file_path", Text, nullable=False),
        Column("title", Text, nullable=True),
        Column("type", Text, nullable=True),
        Column("sources", Text, nullable=True),
        Column("tags", Text, nullable=True),
        Column("generation_key", Text, nullable=True),
        Column("content_hash", String(64), nullable=False),
        Column("source_mtime_ns", BigInteger, nullable=True),
        Column("qdrant_point_id", String(36), nullable=True),
        Column("x", Float, nullable=True),
        Column("y", Float, nullable=True),
        Column("community", Integer, nullable=True),
        Column("pinned", Integer, nullable=False, server_default=sa_text("0")),
        Column("deleted_at", Text, nullable=True),
        Column("created_at", Text, nullable=False),
        Column("updated_at", Text, nullable=False),
    )
    Table(
        "vault_state",
        _meta,
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
        Column("cli_oauth_token", Text, nullable=True),
        Column("cli_oauth_token_encrypted", LargeBinary, nullable=True),
        Column("web_search_api_keys_encrypted", LargeBinary, nullable=True),
        Column("searxng_url_db", Text, nullable=True),
        Column("searxng_categories_db", Text, nullable=True),
        Column("searxng_max_queries_db", Integer, nullable=True),
        Column("output_language", Text, nullable=True),
        Column("updated_at", Text, nullable=False),
    )
    Table(
        "links",
        _meta,
        Column("id", String(36), primary_key=True),
        Column("source_page_id", String(36), nullable=False),
        Column("target_title", Text, nullable=False),
        Column("target_page_id", String(36), nullable=True),
        Column("alias", Text, nullable=True),
        Column("dangling", Boolean, nullable=False, server_default=sa_text("0")),
        Column("created_at", Text, nullable=False),
    )
    # ADR-0079 §3: review_items table for block review enqueue.
    Table(
        "review_items",
        _meta,
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

    async with engine.begin() as conn:
        await conn.run_sync(_meta.create_all)

    sf = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with sf() as session:
        await session.execute(
            sa_text(
                "INSERT INTO vault_state (id, vault_id, data_version, updated_at) "
                "VALUES (:id, :vault_id, 0, datetime('now'))"
            ),
            {"id": str(uuid.uuid4()), "vault_id": "test-vault"},
        )
        await session.commit()

    set_embedding_client(FakeEmbeddingClient(dim=8))
    fake_qdrant = _FakeQdrant()

    @asynccontextmanager
    async def patched_get_session() -> AsyncIterator[AsyncSession]:
        async with sf() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    monkeypatch.setattr("app.db.get_session", patched_get_session)
    monkeypatch.setattr("app.ingest.orchestrator.get_session", patched_get_session)
    monkeypatch.setattr("app.ops.review.get_session", patched_get_session)
    monkeypatch.setattr(
        "app.ingest.orchestrator.upsert_point",
        lambda **kwargs: fake_qdrant.upsert(
            "synapse_pages",
            [
                type(
                    "Pt",
                    (),
                    {
                        "id": str(kwargs["page_id"]),
                        "vector": kwargs["vector"],
                        "payload": {"file_path": kwargs["file_path"], "title": kwargs["title"]},
                    },
                )()
            ],
        ),
    )

    runs: list[dict[str, Any]] = []

    async def fake_open_ingest_run(**_kwargs: Any) -> uuid.UUID:
        return uuid.uuid4()

    async def fake_finalize_ingest_run(**kwargs: Any) -> None:
        runs.append(kwargs)

    monkeypatch.setattr(orch, "_open_ingest_run", fake_open_ingest_run)
    monkeypatch.setattr(orch, "_finalize_ingest_run", fake_finalize_ingest_run)

    from app.ingest.queue_manager import IngestQueueManager

    class _FakeHandle2:
        cancel_event = _asyncio.Event()
        written_page_ids: list[Any] = []

    fake_queue = IngestQueueManager.__new__(IngestQueueManager)
    fake_queue.open_run = lambda run_id, source_path: _FakeHandle2()  # type: ignore[attr-defined]
    fake_queue.finalize = lambda *a, **kw: None  # type: ignore[attr-defined]
    fake_queue.get_retry_count = lambda path: 0  # type: ignore[attr-defined]
    fake_queue.record_written = lambda *a, **kw: None  # type: ignore[attr-defined]
    fake_queue.set_route = lambda *a, **kw: None  # type: ignore[attr-defined]
    fake_queue.set_phase = lambda *a, **kw: None  # type: ignore[attr-defined]
    # BE-QUEUE-1/2 (1.9.4 W3): run_ingest_pipeline now gates on the capability semaphore and
    # touches the rate-limit ladder on both terminal paths — stub them as no-ops.

    async def _noop_acquire_capability_slot(mode: str) -> None:  # type: ignore[no-untyped-def]
        return None

    fake_queue.acquire_capability_slot = _noop_acquire_capability_slot  # type: ignore[attr-defined]
    fake_queue.release_capability_slot = lambda *a, **kw: None  # type: ignore[attr-defined]
    fake_queue.pause_for_rate_limit = lambda *a, **kw: 0.0  # type: ignore[attr-defined]
    fake_queue.reset_rate_limit_backoff = lambda *a, **kw: None  # type: ignore[attr-defined]
    monkeypatch.setattr(orch, "ingest_queue", fake_queue)

    monkeypatch.setattr(orch, "_update_overview", _anoop)
    monkeypatch.setattr(orch, "_index_index_and_log_files", _anoop)
    monkeypatch.setattr(orch, "_auto_tag_written_pages", _anoop)
    import app.ops.enrich_wikilinks as _enrich_mod
    import app.ops.review as _review_mod

    monkeypatch.setattr(_review_mod, "propose_reviews", _anoop)
    monkeypatch.setattr(_review_mod, "sweep_reviews", _anoop)
    monkeypatch.setattr(_review_mod, "generate_purpose_suggestion", _anoop)
    monkeypatch.setattr(_review_mod, "generate_schema_suggestion", _anoop)
    monkeypatch.setattr(_enrich_mod, "enrich_wikilinks", _anoop)

    yield {"session_factory": sf, "vault_root": vault_root, "runs": runs}

    set_embedding_client(None)  # type: ignore[arg-type]


async def test_blocks_format_enqueues_review_blocks(
    pipeline_env_review: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    T-D3-001 (WS-C, ADR-0079 §3): REVIEW blocks returned by the block loop are enqueued
    as ReviewItems (proposal_origin='ai', status='pending', content_key dedup).

    The file pages still land normally (no regression on the write path). The REVIEW block
    enqueue is fire-and-forget: failures are non-fatal (I7).
    """
    provider = _BlockProvider([ANALYSIS, _GEN_BLOCKS_WITH_REVIEW])
    monkeypatch.setattr(orch, "resolve_provider", lambda _row: provider)
    monkeypatch.setitem(config_overrides._cache, "ingest_pipeline_format", "blocks")

    result = await orch.run_ingest_pipeline(
        provider_config_row=object(),
        source_text="The Acme Corp report references an unindexed supplier.",
        origin_source=ORIGIN,
        abs_source=ABS_SOURCE,
    )

    # File blocks still land.
    assert result.converged is True
    assert result.pages_written >= 2  # thesis + entity + optional source summary

    # REVIEW block must appear in review_items as a pending proposal (ADR-0079 §3).
    async with pipeline_env_review["session_factory"]() as session:
        rows = list(
            (
                await session.execute(
                    sa_text(
                        "SELECT item_type, proposed_title, proposal_origin, status FROM review_items"
                    )
                )
            ).fetchall()
        )

    assert (
        len(rows) >= 1
    ), "at least one ReviewItem must be enqueued from the REVIEW block (ADR-0079 §3)"
    row = rows[0]
    assert row.item_type == "missing-page"
    assert row.proposed_title == "Missing Entity Acme Supplier"
    assert row.proposal_origin == "ai"
    assert row.status == "pending"
