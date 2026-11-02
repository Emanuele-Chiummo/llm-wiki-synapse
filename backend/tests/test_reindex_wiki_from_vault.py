"""
Tests for scripts/reindex_wiki_from_vault.py — disaster-recovery re-index from wiki/ files.

Real files in tmp_path, real SQLite schema (tests._db_fixtures), Qdrant/embedding stubbed.

  T1 — collect_wiki_files skips .obsidian/ and non-.md files.
  T2 — parse_page_file reads type/title/tags/sources and hashes the exact bytes.
  T3 — missing frontmatter falls back to folder type and filename stem.
  T4 — sources gain the raw/sources/<identity> form only when the raw file exists.
  T5 — full run: one row per file, links resolved (incl. forward refs), one version bump,
       one vector per page, files on disk untouched.
  T6 — idempotent: a second run reuses the ids, no duplicate pages or links.
  T7 — dry-run writes nothing.
  T8 — --no-embed skips Qdrant but still writes pages and links.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import app.ingest.orchestrator as orch
import pytest
import scripts.reindex_wiki_from_vault as rw
from app.models import Link, Page, VaultState
from sqlalchemy import func, select

from tests._db_fixtures import make_session_factory, make_sqlite_engine

VAULT_ID = "test"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    _write(
        root / "wiki/concepts/serverless.md",
        "---\ntype: concept\ntitle: Serverless\ntags: [Cloud, faas]\n"
        'sources: ["FinOps/guide.md"]\n---\n\n# Serverless\n\n'
        "Runs on [[aws-cloud]] and links to [[Ghost Page]].\n",
    )
    _write(
        root / "wiki/entities/aws-cloud.md",
        '---\ntype: entity\ntitle: AWS Cloud\nsources: ["FinOps/guide.md"]\n---\n\n'
        "Amazon's cloud, see [[Serverless]].\n",
    )
    _write(root / "wiki/index.md", "---\ntype: index\ntitle: Index\n---\n\n- [[Serverless]]\n")
    _write(root / "wiki/.obsidian/app.md", "not a page")
    _write(root / "wiki/concepts/notes.txt", "not markdown")
    _write(root / "raw/sources/FinOps/guide.md", "raw source")
    return root


@pytest.fixture()
async def db(monkeypatch: pytest.MonkeyPatch) -> Any:
    engine = await make_sqlite_engine()
    factory = make_session_factory(engine)

    @asynccontextmanager
    async def _session() -> AsyncIterator[Any]:
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr(rw, "get_session", _session)
    monkeypatch.setattr(orch, "get_session", _session)
    monkeypatch.setattr(orch.settings, "vault_id", VAULT_ID)
    async with _session() as s:
        s.add(VaultState(vault_id=VAULT_ID, data_version=0))
    yield _session
    await engine.dispose()


@pytest.fixture()
def vectors(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def _fake_upsert_vector(**kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(rw, "upsert_vector", _fake_upsert_vector)
    return calls


async def _counts(session_cm: Any) -> tuple[int, int, int, int]:
    async with session_cm() as s:
        pages = (await s.execute(select(func.count()).select_from(Page))).scalar_one()
        links = (await s.execute(select(func.count()).select_from(Link))).scalar_one()
        dangling = (
            await s.execute(select(func.count()).select_from(Link).where(Link.dangling.is_(True)))
        ).scalar_one()
        version = (
            await s.execute(select(VaultState.data_version).where(VaultState.vault_id == VAULT_ID))
        ).scalar_one()
    return pages, links, dangling, version


def test_collect_skips_hidden_and_non_md(vault: Path) -> None:
    rels = [p.relative_to(vault).as_posix() for p in rw.collect_wiki_files(vault / "wiki")]
    assert rels == [
        "wiki/concepts/serverless.md",
        "wiki/entities/aws-cloud.md",
        "wiki/index.md",
    ]


def test_parse_page_file_reads_frontmatter(vault: Path) -> None:
    path = vault / "wiki/concepts/serverless.md"
    rec = rw.parse_page_file(vault, path)
    assert rec.rel_path == "wiki/concepts/serverless.md"
    assert rec.title == "Serverless"
    assert rec.page_type == "concept"
    assert rec.tags == ["cloud", "faas"]
    assert rec.body.startswith("# Serverless")
    assert rec.summary is not None and "aws-cloud" in rec.summary
    import hashlib

    assert rec.content_hash == hashlib.sha256(path.read_bytes()).hexdigest()


def test_parse_page_file_without_frontmatter(tmp_path: Path) -> None:
    _write(tmp_path / "wiki/entities/bare-page.md", "Just text with [[Other]].\n")
    rec = rw.parse_page_file(tmp_path, tmp_path / "wiki/entities/bare-page.md")
    assert rec.title == "bare-page"
    assert rec.page_type == "entity"
    assert rec.tags is None and rec.sources is None


def test_normalize_sources_adds_raw_form_only_when_file_exists(vault: Path) -> None:
    raw_dir = vault / "raw/sources"
    assert rw.normalize_sources(["FinOps/guide.md"], raw_dir) == [
        "FinOps/guide.md",
        "raw/sources/FinOps/guide.md",
    ]
    assert rw.normalize_sources(["gone.md"], raw_dir) == ["gone.md"]
    assert rw.normalize_sources([], raw_dir) is None


async def test_full_run_rebuilds_pages_links_and_vectors(
    vault: Path, db: Any, vectors: list[dict[str, Any]]
) -> None:
    before = {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()}
    summary = await rw.reindex_vault(vault_root=vault, vault_id=VAULT_ID, log=lambda _m: None)

    assert summary.errors == []
    assert summary.files_found == 3 and summary.pages_written == 3
    pages, links, dangling, version = await _counts(db)
    assert pages == 3
    # serverless → aws-cloud (forward ref, resolved by file slug) + Ghost Page (dangling);
    # aws-cloud → Serverless; index → Serverless.
    assert links == 4 and dangling == 1 and summary.links_dangling == 1
    assert version == 1
    assert len(vectors) == 3
    assert {v["file_path"] for v in vectors} == {
        "wiki/concepts/serverless.md",
        "wiki/entities/aws-cloud.md",
        "wiki/index.md",
    }
    async with db() as s:
        row = (
            await s.execute(select(Page).where(Page.file_path == "wiki/entities/aws-cloud.md"))
        ).scalar_one()
    assert row.page_type == "entity"
    assert row.sources == ["FinOps/guide.md", "raw/sources/FinOps/guide.md"]
    assert {p: p.read_bytes() for p in vault.rglob("*") if p.is_file()} == before


async def test_second_run_is_idempotent(
    vault: Path, db: Any, vectors: list[dict[str, Any]]
) -> None:
    await rw.reindex_vault(vault_root=vault, vault_id=VAULT_ID, log=lambda _m: None)
    async with db() as s:
        first_ids = set((await s.execute(select(Page.id))).scalars().all())
    summary = await rw.reindex_vault(vault_root=vault, vault_id=VAULT_ID, log=lambda _m: None)
    async with db() as s:
        second_ids = set((await s.execute(select(Page.id))).scalars().all())
    assert summary.pages_reused_id == 3
    assert first_ids == second_ids
    pages, links, _dangling, version = await _counts(db)
    assert (pages, links, version) == (3, 4, 2)
    assert all(isinstance(v["page_id"], uuid.UUID) for v in vectors)


async def test_dry_run_writes_nothing(vault: Path, db: Any, vectors: list[dict[str, Any]]) -> None:
    summary = await rw.reindex_vault(
        vault_root=vault, vault_id=VAULT_ID, dry_run=True, log=lambda _m: None
    )
    assert summary.files_found == 3 and summary.pages_written == 0
    assert await _counts(db) == (0, 0, 0, 0)
    assert vectors == []


async def test_no_embed_skips_vectors(vault: Path, db: Any, vectors: list[dict[str, Any]]) -> None:
    summary = await rw.reindex_vault(
        vault_root=vault, vault_id=VAULT_ID, embed=False, log=lambda _m: None
    )
    assert summary.pages_written == 3 and summary.vectors_attempted == 0
    assert vectors == []
    pages, links, _d, _v = await _counts(db)
    assert (pages, links) == (3, 4)
