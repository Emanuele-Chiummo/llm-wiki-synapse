"""Unit tests for the block-based FILE writer (ADR-0076, app.ingest.block_writer).

Exercises write_block_page against a real SQLite in-memory DB + a temporary vault (the same
infra-free pattern as tests/test_ingest_incremental.py), proving:

  • a CUSTOM-typed page (type=thesis) persists with pages.type == "thesis" (the raw string —
    write_block_page must NOT go through the strict PageType-enum writer),
  • the body's [[wikilinks]] are parsed into links rows (K5),
  • the active origin source is guaranteed present in pages.sources,
  • a mis-routed page (type=entity at wiki/thesis/…) is DROPPED (returns None),
  • an app-managed aggregate (wiki/index.md) is DROPPED (returns None),
  • page-history backups are created on overwrite and capped.

No provider, no network — write_block_page writes the block verbatim.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
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

ROUTING = {"thesis": "thesis", "entity": "entities", "concept": "concepts", "source": "sources"}
ORIGIN = "raw/sources/doc.md"


class _FakeQdrant:
    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}

    async def upsert(self, collection_name: str, points: list[Any]) -> None:
        for pt in points:
            self.points[str(pt.id)] = pt.payload or {}

    async def delete(self, collection_name: str, points_selector: Any) -> None:
        for pid in points_selector.points:
            self.points.pop(str(pid), None)


@pytest.fixture()
async def block_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict[str, Any]]:
    """SQLite (pages/vault_state/links) + temp vault + fake embedding/qdrant, wired for
    write_block_page (mirrors test_ingest_incremental.ingest_env, trimmed)."""
    from app import config as cfg
    from app.embeddings import FakeEmbeddingClient, set_embedding_client

    vault_root = tmp_path / "vault"
    (vault_root / "raw" / "sources").mkdir(parents=True)
    wiki_dir = vault_root / "wiki"
    wiki_dir.mkdir()
    (wiki_dir / "log.md").write_text(
        "---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n", encoding="utf-8"
    )

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
        Column("summary", Text, nullable=True),
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

    yield {"session_factory": session_factory, "vault_root": vault_root, "wiki_dir": wiki_dir}

    set_embedding_client(None)  # type: ignore[arg-type]


def _thesis_content(title: str = "Central Thesis", body_extra: str = "") -> str:
    return (
        "---\n"
        "type: thesis\n"
        f"title: {title}\n"
        "created: 2026-07-14\n"
        "updated: 2026-07-14\n"
        "sources: []\n"
        "---\n\n"
        f"# {title}\n\n"
        "The argument connects to [[Some Concept]] and [[Another Idea]].\n"
        f"{body_extra}"
    )


async def _load_page(env: dict[str, Any], rel_path: str) -> Any:
    from app.models import Page

    async with env["session_factory"]() as session:
        row = await session.execute(
            select(Page).where(Page.file_path == rel_path, Page.deleted_at.is_(None))
        )
        return row.scalar_one_or_none()


async def test_custom_type_thesis_page_persists_raw_type(block_env: dict[str, Any]) -> None:
    from app.ingest.block_writer import write_block_page

    page = await write_block_page(
        rel_path="wiki/thesis/central.md",
        content=_thesis_content(),
        origin_source=ORIGIN,
        routing=ROUTING,
    )

    assert page is not None
    # The raw custom type string persists (NOT constrained to the PageType enum).
    assert page.page_type == "thesis"
    assert page.title == "Central Thesis"
    # The active origin source is guaranteed present (F3 traceability).
    assert ORIGIN in (page.sources or [])
    # The file was written on disk.
    assert (block_env["vault_root"] / "wiki" / "thesis" / "central.md").is_file()

    # DB row matches what write_block_page returned.
    row = await _load_page(block_env, "wiki/thesis/central.md")
    assert row is not None and row.page_type == "thesis"


async def test_body_wikilinks_persisted_as_links(block_env: dict[str, Any]) -> None:
    from app.ingest.block_writer import write_block_page
    from app.models import Link

    page = await write_block_page(
        rel_path="wiki/thesis/central.md",
        content=_thesis_content(),
        origin_source=ORIGIN,
        routing=ROUTING,
    )
    assert page is not None

    async with block_env["session_factory"]() as session:
        rows = list(
            (await session.execute(select(Link).where(Link.source_page_id == page.id)))
            .scalars()
            .all()
        )
    targets = {r.target_title for r in rows}
    assert targets == {"Some Concept", "Another Idea"}
    # No target pages exist yet, so both edges are dangling (K5 warn-not-error).
    assert all(r.dangling for r in rows)


async def test_misrouted_entity_in_thesis_dir_is_dropped(block_env: dict[str, Any]) -> None:
    from app.ingest.block_writer import write_block_page

    content = (
        "---\n"
        "type: entity\n"
        "title: Wrong Home\n"
        "created: 2026-07-14\n"
        "updated: 2026-07-14\n"
        "sources: []\n"
        "---\n\n"
        "# Wrong Home\n\nBody.\n"
    )
    page = await write_block_page(
        rel_path="wiki/thesis/wrong.md",
        content=content,
        origin_source=ORIGIN,
        routing=ROUTING,
    )
    assert page is None  # type=entity may not live under wiki/thesis/ → dropped (llm_wiki parity)
    assert not (block_env["vault_root"] / "wiki" / "thesis" / "wrong.md").exists()
    assert await _load_page(block_env, "wiki/thesis/wrong.md") is None


async def test_app_managed_index_block_is_dropped(block_env: dict[str, Any]) -> None:
    from app.ingest.block_writer import write_block_page

    content = "---\ntype: index\ntitle: Index\n---\n\n# Index\n\n- entries\n"
    page = await write_block_page(
        rel_path="wiki/index.md",
        content=content,
        origin_source=ORIGIN,
        routing=ROUTING,
    )
    assert page is None
    assert await _load_page(block_env, "wiki/index.md") is None


async def test_app_managed_log_block_is_dropped(block_env: dict[str, Any]) -> None:
    """log.md is code-appended (append_log), never model-written. A log.md block is DROPPED so it
    cannot overwrite the code-managed log (parity regression: it used to destroy the frontmatter and
    mix a second, schema-described format into the file)."""
    from app.ingest.block_writer import write_block_page

    log_path = block_env["vault_root"] / "wiki" / "log.md"
    before = log_path.read_text(encoding="utf-8")
    content = (
        "---\ntype: log\ntitle: Log\n---\n\n## 2026-07-15\n\n"
        "- 12:00:00Z · indexed · concept · [[X]] — wiki/concepts/x.md\n"
    )
    page = await write_block_page(
        rel_path="wiki/log.md", content=content, origin_source=ORIGIN, routing=ROUTING
    )
    assert page is None
    # The code-managed log.md is untouched (frontmatter + scaffold preserved).
    assert log_path.read_text(encoding="utf-8") == before


async def test_source_page_appends_exactly_one_log_line(block_env: dict[str, Any]) -> None:
    """The block path logs ONE '## [date] ingest | <title>' line — for the SOURCE page only, not
    once per generated page (llm_wiki parity: one log entry per source ingest)."""
    from app.ingest.block_writer import write_block_page

    log_path = block_env["vault_root"] / "wiki" / "log.md"
    before = log_path.read_text(encoding="utf-8")
    content = (
        "---\ntype: source\ntitle: My Source Doc\ncreated: 2026-07-15\n"
        "updated: 2026-07-15\nsources: []\n---\n\n# My Source Doc\n\nBody.\n"
    )
    page = await write_block_page(
        rel_path="wiki/sources/my-source.md", content=content, origin_source=ORIGIN, routing=ROUTING
    )
    assert page is not None and page.page_type == "source"
    added = log_path.read_text(encoding="utf-8")[len(before) :]
    entries = [ln for ln in added.splitlines() if ln.startswith("## [")]
    assert len(entries) == 1
    assert "ingest | My Source Doc" in entries[0]


async def test_non_source_page_appends_no_log_line(block_env: dict[str, Any]) -> None:
    """A generated (non-source) page must NOT add a log line — only the source page does."""
    from app.ingest.block_writer import write_block_page

    log_path = block_env["vault_root"] / "wiki" / "log.md"
    before = log_path.read_text(encoding="utf-8")
    content = (
        "---\ntype: concept\ntitle: A Concept\ncreated: 2026-07-15\n"
        "updated: 2026-07-15\nsources: []\n---\n\n# A Concept\n\nBody.\n"
    )
    page = await write_block_page(
        rel_path="wiki/concepts/a-concept.md",
        content=content,
        origin_source=ORIGIN,
        routing=ROUTING,
    )
    assert page is not None and page.page_type == "concept"
    assert log_path.read_text(encoding="utf-8") == before  # no new log line


async def test_page_history_backup_created_and_capped(
    block_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import config as cfg
    from app.ingest.block_writer import write_block_page

    monkeypatch.setattr(cfg.settings, "ingest_page_history_max_per_page", 2)

    history_dir = block_env["vault_root"] / ".synapse" / "page-history"

    # First write: the file does not exist yet → NO backup.
    await write_block_page(
        rel_path="wiki/thesis/hist.md",
        content=_thesis_content("Rev 1"),
        origin_source=ORIGIN,
        routing=ROUTING,
    )
    assert not history_dir.exists() or list(history_dir.glob("wiki__thesis__hist-*.md")) == []

    # Overwrite four more times → each overwrite backs up the prior bytes; cap = 2.
    for rev in range(2, 6):
        await write_block_page(
            rel_path="wiki/thesis/hist.md",
            content=_thesis_content(f"Rev {rev}"),
            origin_source=ORIGIN,
            routing=ROUTING,
        )

    backups = list(history_dir.glob("wiki__thesis__hist-*.md"))
    # At least one backup exists and the retention cap is honoured (oldest pruned).
    assert 1 <= len(backups) <= 2
    assert len(backups) == 2
    # The live file still holds the latest revision.
    live = (block_env["vault_root"] / "wiki" / "thesis" / "hist.md").read_text(encoding="utf-8")
    assert "Rev 5" in live
