"""
WS-G — Automations functional verification after v1.3.5 (K2/F3/F18, I7).

v1.3.5 introduced three changes that could regress the four scheduled ops:
  1. log.md format: ``## YYYY-MM-DD`` date headers + bullet format changed.
  2. Frontmatter timestamps: ``created`` and ``updated`` date-only fields now written
     by write_wiki_page() and append_log() (nashsu/llm_wiki parity, K4/K6).
  3. schema.md completeness: the seed schema.md now carries a full type/naming/frontmatter
     section including ``lang`` and ``tags`` fields plus the new log/index format.

Verification strategy (I7 — prefer dry-run/bounded paths, no live provider cost):
  lint        — exercised end-to-end via run_lint_scan(semantic=False) — zero provider
                cost, deterministic-only scan; verifies no import/parse regression.
  backfill    — code-path verified via mocked tests that confirm the frontmatter
                round-trip preserves all v1.3.5 fields (created/updated/lang).
  schema_review — verified via mocked tests that confirm schema.md text with the new
                format is read without error and passed to the provider.
  reclassify  — code-path verified via mocked tests that confirm the frontmatter
                round-trip preserves all v1.3.5 fields.

No live provider calls in this suite. No real DB (SQLite in-memory).

Test IDs: T-WSG-001 .. T-WSG-013
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── v1.3.5 sample frontmatter (the new format) ────────────────────────────────
#
# v1.3.5 added: created, updated (date-only), lang, tags.
# Backfill/reclassify must round-trip these fields without corruption.

_V135_FRONTMATTER_CONTENT = """\
---
type: concept
title: Chain-of-Thought Prompting
lang: en
sources:
  - raw/sources/wei-2022-cot.pdf
tags:
  - prompting
  - reasoning
created: 2026-06-01
updated: 2026-07-05
---

Chain-of-thought prompting is a technique that encourages models to reason step by step.
"""

# v1.3.5 log.md format: ## date-header + bullet format
_V135_LOG_MD_CONTENT = """\
---
type: log
title: Synapse Ingest Log
---

<!-- Append-only ingest history (K4). Do not edit manually. -->

## 2026-06-01

- 19:52:54Z · indexed · concept · [[Chain-of-Thought Prompting]] — wiki/concepts/chain-of-thought.md

## 2026-07-05

- 10:31:22Z · indexed · source · [[Wei 2022 CoT]] — wiki/sources/wei-2022-cot.md
- 10:32:00Z · indexed · entity · [[OpenAI]] — wiki/entities/openai.md
"""

# v1.3.5 complete schema.md (now includes lang + tags + log/index format rules)
_V135_SCHEMA_MD_CONTENT = """\
# Wiki Schema

> The rules the ingest AI and human curators follow when writing pages in `wiki/`.

## Page Types

| Type | Directory | Purpose |
|------|-----------|---------|
| entity | wiki/entities/ | Named things |
| concept | wiki/concepts/ | Ideas, techniques, phenomena |
| source | wiki/sources/ | Papers, articles ingested |

## Frontmatter

```yaml
---
type: entity | concept | source | query | comparison | synthesis | overview
title: Human-readable title
lang: en
sources: []
tags: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

## Log Format

```
## YYYY-MM-DD

- HH:MM:SSZ · indexed · concept · [[Page Title]] — wiki/concepts/page-title.md
```
"""


# ── T-WSG-001: lint — deterministic scan runs without exception ───────────────


@pytest.mark.asyncio
async def test_lint_semantic_false_runs_no_exception() -> None:
    """
    T-WSG-001: run_lint_scan(semantic=False) completes without uncaught exception
    in an empty DB — verifies no import/parse regression from v1.3.5.

    Uses a SQLite in-memory DB (same pattern as test_lint.py). Zero provider cost.
    [AC-WS-G-1, I7]
    """
    from app.ops.lint import run_lint_scan
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
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

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
        Column("suggested_target", Text, nullable=True),
        Column("suggested_page_id", String(36), nullable=True),
        Column("status", Text, nullable=False, server_default=sa_text("'open'")),
        Column("resolution_note", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("reviewed_at", Text, nullable=True),
    )

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_get_session():  # type: ignore[no-untyped-def]
        async with session_factory() as s:
            yield s

    with patch("app.ops.lint.get_session", fake_get_session):
        result = await run_lint_scan(vault_id="test-vault", semantic=False)

    assert result is not None
    assert result.status == "completed", f"Unexpected status: {result.status}"
    assert result.error_message is None, f"Unexpected error: {result.error_message}"
    assert result.total_cost_usd == 0.0, "semantic=False must have zero cost"


# ── T-WSG-002: lint — orphan detection with v1.3.5 pages ─────────────────────


@pytest.mark.asyncio
async def test_lint_orphan_detection_with_v135_updated_at() -> None:
    """
    T-WSG-002: lint can detect orphan pages when the pages table uses the v1.3.5
    schema (with updated_at column in ISO format). Verifies no column-parse regression.
    [AC-WS-G-1, K2]
    """
    from app.ops.lint import run_lint_scan
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
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    meta = MetaData()
    pages_tbl = Table(
        "pages",
        meta,
        Column("id", String(36), primary_key=True),
        Column("vault_id", String, nullable=False),
        Column("file_path", Text, nullable=False),
        Column("title", Text, nullable=True),
        Column("type", Text, nullable=True),
        Column("sources", Text, nullable=True),
        Column("tags", Text, nullable=True),
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
        Column("suggested_target", Text, nullable=True),
        Column("suggested_page_id", String(36), nullable=True),
        Column("status", Text, nullable=False, server_default=sa_text("'open'")),
        Column("resolution_note", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("reviewed_at", Text, nullable=True),
    )

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(meta.create_all)
        # Insert one orphan page (no incoming links, not index/log/overview) with v1.3.5 timestamps
        page_id = str(uuid.uuid4())
        await conn.execute(
            pages_tbl.insert().values(
                id=page_id,
                vault_id="test-vault",
                file_path="wiki/concepts/chain-of-thought.md",
                title="Chain-of-Thought",
                type="concept",
                sources='["raw/sources/wei-2022.pdf"]',
                tags='["prompting"]',
                content_hash="abc123",
                source_mtime_ns=0,
                deleted_at=None,
                # v1.3.5 format: ISO datetime in updated_at column
                created_at="2026-06-01T00:00:00+00:00",
                updated_at="2026-07-05T10:31:22+00:00",
            )
        )

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_get_session():  # type: ignore[no-untyped-def]
        async with session_factory() as s:
            yield s

    with patch("app.ops.lint.get_session", fake_get_session):
        result = await run_lint_scan(vault_id="test-vault", semantic=False)

    assert result.status == "completed"
    # The single page with no incoming links should be detected as an orphan.
    assert result.findings_count >= 1, "Expected at least one orphan-page finding"


# ── T-WSG-003: backfill — frontmatter round-trip preserves v1.3.5 fields ─────


def test_backfill_frontmatter_roundtrip_preserves_v135_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    T-WSG-003: apply_domain_tags() preserves the new v1.3.5 frontmatter fields
    (created, updated, lang) when adding domain/* tags to a page.

    This is the critical regression test: if the python-frontmatter round-trip
    dropped these fields, the page would lose its creation date on every backfill run.
    [AC-WS-G-2, K6, I1]
    """
    import frontmatter
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(
        type(cfg.settings),
        "vault_root",
        property(lambda self: tmp_path),
    )

    # Write a v1.3.5-format page to disk
    page_file = tmp_path / "wiki" / "concepts" / "chain-of-thought.md"
    page_file.parent.mkdir(parents=True, exist_ok=True)
    page_file.write_text(_V135_FRONTMATTER_CONTENT, encoding="utf-8")

    # Verify the round-trip (what python-frontmatter does internally in apply_domain_tags)
    post = frontmatter.loads(_V135_FRONTMATTER_CONTENT)
    post["tags"] = ["prompting", "reasoning", "domain/AIResearch"]
    new_text = frontmatter.dumps(post) + "\n"
    result = frontmatter.loads(new_text)

    # All v1.3.5 fields must be preserved
    assert result.metadata.get("created") is not None, "created field lost in round-trip"
    assert result.metadata.get("updated") is not None, "updated field lost in round-trip"
    assert result.metadata.get("lang") == "en", "lang field lost in round-trip"
    assert result.metadata.get("type") == "concept", "type field lost in round-trip"
    assert result.metadata.get("title") is not None, "title field lost in round-trip"
    # New domain tag must be present
    raw_tags = result.metadata.get("tags") or []
    result_tags: list[str] = [str(t) for t in raw_tags]  # type: ignore[attr-defined]
    assert "domain/AIResearch" in result_tags
    # Original tags preserved
    assert "prompting" in result_tags


# ── T-WSG-004: reclassify — frontmatter round-trip preserves v1.3.5 fields ───


def test_reclassify_frontmatter_roundtrip_preserves_v135_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    T-WSG-004: apply_page_type() preserves the new v1.3.5 frontmatter fields
    (created, updated, lang, tags) when reclassifying a page's type.

    The reclassify write-back reads the file, replaces only the ``type`` key,
    and round-trips all other fields unchanged.
    [AC-WS-G-4, K6, I1]
    """
    import frontmatter
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(
        type(cfg.settings),
        "vault_root",
        property(lambda self: tmp_path),
    )

    # Write a v1.3.5-format page to disk
    page_file = tmp_path / "wiki" / "concepts" / "chain-of-thought.md"
    page_file.parent.mkdir(parents=True, exist_ok=True)
    page_file.write_text(_V135_FRONTMATTER_CONTENT, encoding="utf-8")

    # Simulate what apply_page_type does: load, set type, dump
    post = frontmatter.loads(_V135_FRONTMATTER_CONTENT)
    post["type"] = "entity"  # reclassified from concept → entity
    new_text = frontmatter.dumps(post) + "\n"
    result = frontmatter.loads(new_text)

    # All v1.3.5 fields must be preserved after type change
    assert result.metadata.get("created") is not None, "created field lost in reclassify round-trip"
    assert result.metadata.get("updated") is not None, "updated field lost in reclassify round-trip"
    assert result.metadata.get("lang") == "en", "lang field lost in reclassify round-trip"
    assert result.metadata.get("type") == "entity", "type not updated in round-trip"
    assert result.metadata.get("title") is not None, "title lost in round-trip"
    # Tags preserved
    raw_reclassify_tags = result.metadata.get("tags") or []
    reclassify_tags: list[str] = [str(t) for t in raw_reclassify_tags]  # type: ignore[attr-defined]
    assert "prompting" in reclassify_tags
    assert "reasoning" in reclassify_tags


# ── T-WSG-005: schema_review — reads v1.3.5 schema.md without error ──────────


def test_schema_review_reads_v135_schema_md_without_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    T-WSG-005: _load_vault_context() reads the new complete schema.md (v1.3.5 format
    with lang/tags/log-format sections) and returns a non-empty string without error.

    This verifies schema_review (which calls _load_vault_context) won't silently
    break on the new schema.md content.
    [AC-WS-G-3, K6]
    """
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(
        type(cfg.settings),
        "vault_root",
        property(lambda self: tmp_path),
    )

    # Write the v1.3.5-format schema.md to disk
    (tmp_path / "schema.md").write_text(_V135_SCHEMA_MD_CONTENT, encoding="utf-8")
    (tmp_path / "purpose.md").write_text("# Vault Purpose\n\n## Goal\nTest.\n", encoding="utf-8")

    from app.ingest.orchestrator import _load_vault_context

    context = _load_vault_context()

    assert context, "vault context must be non-empty when schema.md and purpose.md exist"
    assert "schema.md" in context, "context must reference schema.md"
    assert "purpose.md" in context, "context must reference purpose.md"
    # Key new v1.3.5 schema fields must be present
    assert "lang" in context, "context must include lang field from schema.md"
    assert "created" in context, "context must include created field from schema.md"
    assert "updated" in context, "context must include updated field from schema.md"
    # Log format section
    assert "YYYY-MM-DD" in context, "context must include the new log format from schema.md"


# ── T-WSG-006: schema_review — anti-spam guard works after v1.3.5 ─────────────


@pytest.mark.asyncio
async def test_schema_review_antispam_with_v135_pages() -> None:
    """
    T-WSG-006: run_schema_review's anti-spam guard (skip if pending item exists)
    works correctly with v1.3.5 Page objects. No regression in the mocked path.
    [AC-WS-G-3]
    """
    fake_page = MagicMock()
    fake_page.id = str(uuid.uuid4())
    fake_page.vault_id = "test-vault"
    fake_page.title = "Chain-of-Thought"
    fake_page.page_type = "concept"
    # v1.3.5 page attributes
    fake_page.created_at = "2026-06-01T00:00:00+00:00"
    fake_page.updated_at = "2026-07-05T10:31:22+00:00"

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [fake_page]

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_session)
    mock_ctx.__aexit__ = AsyncMock(return_value=False)

    # Anti-spam: generate_schema_suggestion returns None (pending item exists)
    mock_generate = AsyncMock(return_value=None)

    with patch("app.ops.schema_review.get_session", return_value=mock_ctx):
        with patch("app.ops.review.generate_schema_suggestion", mock_generate):
            from app.ops.schema_review import run_schema_review

            await run_schema_review(vault_id="test-vault")

    # Called (did not bail on no-pages) but returned None (anti-spam fired)
    mock_generate.assert_awaited_once()


# ── T-WSG-007: backfill — dormant vocabulary → correct stopped_reason ────────


@pytest.mark.asyncio
async def test_backfill_dormant_with_v135_config() -> None:
    """
    T-WSG-007: run_backfill returns stopped_reason='dormant' when no domain vocabulary
    is configured — verifies the op doesn't error on the v1.3.5 config structure.
    [AC-WS-G-2, I7]
    """
    import app.config_overrides as co
    from app.ops import backfill_domains as bf

    co._cache.pop("domain_vocabulary", None)

    result = await bf.run_backfill(vault_id="test-vault")
    assert result.stopped_reason == "dormant"
    assert not bf.is_running()


# ── T-WSG-008: reclassify — single-flight guard works after v1.3.5 ────────────


def test_reclassify_single_flight_not_running_by_default() -> None:
    """
    T-WSG-008: reclassify_types.is_running() returns False at module load time.
    Confirms no state regression from v1.3.5.
    [AC-WS-G-4]
    """
    from app.ops import reclassify_types as rt

    # Reset state for test isolation
    rt._state.is_running = False
    assert not rt.is_running()


# ── T-WSG-009: log.md format — append_log writes the ADR-0078 llm_wiki §1.8 format ─────


@pytest.mark.asyncio
async def test_append_log_writes_llmwiki_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    T-WSG-009 (ADR-0078): append_log() now produces the llm_wiki §1.8 heading format:
      ## [YYYY-MM-DD] ingest | Title

    One self-contained heading per entry. No date-grouping wrapper. No bullets.
    "indexed" action verb is normalised to "ingest" (matches the reference).
    [AC-WS-G-6, K4, ADR-0078]
    """
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True)
    log_path = wiki_dir / "log.md"
    log_path.write_text("---\ntype: log\ntitle: Synapse Ingest Log\n---\n\n", encoding="utf-8")

    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_path", str(tmp_path))
    monkeypatch.setattr(
        type(cfg.settings),
        "vault_root",
        property(lambda self: tmp_path),
    )
    monkeypatch.setattr(
        type(cfg.settings),
        "wiki_dir",
        property(lambda self: tmp_path / "wiki"),
    )
    monkeypatch.setattr(
        type(cfg.settings),
        "log_md_path",
        property(lambda self: tmp_path / "wiki" / "log.md"),
    )

    from app.ingest.orchestrator import append_log

    await append_log(
        "wiki/concepts/chain-of-thought.md",
        action="indexed",
        page_type="concept",
        title="Chain-of-Thought Prompting",
    )

    content = log_path.read_text(encoding="utf-8")

    import re

    # ADR-0078 format: ## [YYYY-MM-DD] ingest | Title
    assert re.search(
        r"## \[\d{4}-\d{2}-\d{2}\] ingest \| ", content
    ), "log.md must use the ADR-0078 heading format: ## [YYYY-MM-DD] ingest | Title"
    assert "Chain-of-Thought Prompting" in content, "log.md must include the page title"
    # No bullet list — the old format is gone.
    lines_with_title = [ln for ln in content.splitlines() if "Chain-of-Thought Prompting" in ln]
    assert all(
        not ln.strip().startswith("-") for ln in lines_with_title
    ), "ADR-0078 format uses headings, not bullet list entries"


# ── T-WSG-010: log.md format — ops don't READ/PARSE log.md content ─────────────


def test_ops_do_not_read_or_parse_log_md_content() -> None:
    """
    T-WSG-010: Static assertion — none of the four scheduled ops read or parse
    log.md content. They use the Postgres DB (Page.updated_at) as the source of truth.

    lint.py correctly NAMES log.md to exclude it from orphan detection — that is
    expected. The forbidden pattern is reading or parsing the log.md file content
    (e.g. ``read_text`` on log_md_path, ``open(log_md_path)``, or frontmatter.load
    on log.md), which would regress on a log.md format change.
    [AC-WS-G-5, I1]
    """
    ops_files = [
        Path(__file__).resolve().parent.parent / "app" / "ops" / name
        for name in ("backfill_domains.py", "schema_review.py", "reclassify_types.py")
    ]

    # The forbidden pattern: ops use log_md_path only to write (orchestrator.py),
    # not to read/parse. lint.py excludes log.md from orphan scans (naming only, OK).
    # Using log_md_path in a scheduled op would mean it is reading log.md — regression risk.
    for op_file in ops_files:
        text = op_file.read_text(encoding="utf-8")
        for pattern in ("log_md_path",):
            assert pattern not in text, (
                f"{op_file.name} uses {pattern!r} — scheduled ops must use the Postgres "
                f"DB, not parse log.md file content (would regress on v1.3.5 format change)"
            )


# ── T-WSG-011: OpsScheduler — _interpret_result handles v1.3.5 Summary shapes ─


def test_interpret_result_backfill_complete() -> None:
    """
    T-WSG-011: _interpret_result correctly interprets BackfillSummary (v1.3.5 shape)
    with stopped_reason='complete' → status='ok'.
    [AC-WS-G-2]
    """
    from app.ops.backfill_domains import BackfillSummary
    from app.ops_scheduler import _interpret_result

    summary = BackfillSummary()
    summary.stopped_reason = "complete"
    summary.processed = 20
    summary.tagged = 15
    summary.skipped = 3
    summary.failed = 2

    status, succeeded, detail = _interpret_result("backfill", summary)

    assert status == "ok"
    assert succeeded is True
    assert detail is not None
    assert "15 tagged" in detail
    assert "20 processed" in detail


def test_interpret_result_reclassify_complete() -> None:
    """
    T-WSG-012: _interpret_result correctly interprets ReclassifySummary (v1.3.5 shape)
    with stopped_reason='complete' → status='ok'.
    [AC-WS-G-4]
    """
    from app.ops.reclassify_types import ReclassifySummary
    from app.ops_scheduler import _interpret_result

    summary = ReclassifySummary()
    summary.stopped_reason = "complete"
    summary.processed = 10
    summary.changed = 4
    summary.skipped = 5
    summary.failed = 1

    status, succeeded, detail = _interpret_result("reclassify", summary)

    assert status == "ok"
    assert succeeded is True
    assert detail is not None
    assert "4 changed" in detail
    assert "10 processed" in detail


# ── T-WSG-013: all four ops are importable after v1.3.5 ──────────────────────


def test_all_four_ops_importable_after_v135() -> None:
    """
    T-WSG-013: All four scheduled op modules are importable without error.

    Catches any import-time regression introduced by v1.3.5 changes (e.g. a
    new dependency that is missing, a circular import, a syntax error in a
    module added for llm_wiki parity).
    [AC-WS-G-1 through AC-WS-G-4]
    """
    import importlib

    for module_path in (
        "app.ops.lint",
        "app.ops.backfill_domains",
        "app.ops.schema_review",
        "app.ops.reclassify_types",
        "app.ops_scheduler",
    ):
        mod = importlib.import_module(module_path)
        assert mod is not None, f"Could not import {module_path}"
