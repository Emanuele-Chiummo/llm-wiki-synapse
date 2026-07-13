"""
ADR-0067 D2 + D5 — LLM Wiki 1:1 generation-semantics parity.

D2 — Frontmatter mirrors LLM Wiki byte-shape (`type, title, created, updated, tags, related`,
     `sort_keys=False`); `sources`/`lang` are NO LONGER emitted in the .md, but F3 provenance is
     preserved in Postgres (`pages.sources`, origin injected). `related` = resolvable outbound
     wikilink slugs (never a ghost slug).
D5 — Entity canonicalisation: an EXACT canonical-key match reuses the existing entity page (one
     file, unioned sources); never a silent fuzzy merge (Deloitte vs Deloitte Italia stay apart).

All tests use the SQLite + FakeQdrant/FakeEmbedding `api_env` fixture from test_api.py — no live
infra. Portable SQL only (ORM column comparisons; green SQLite ≠ valid Postgres, but these use no
raw SQL).
"""

from __future__ import annotations

from typing import Any

import frontmatter as fm_lib
import pytest
from sqlalchemy import select

# Re-use the shared SQLite + FakeQdrant/FakeEmbedding fixture (auto-discovered by conftest).
from tests.test_api import api_env  # noqa: F401


def _entity(title: str, source: str, *, content: str = "Body.", body: str | None = None) -> Any:
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

    return WikiPage(
        title=title,
        type=PageType.ENTITY,
        content=body if body is not None else content,
        frontmatter=WikiFrontmatter(type=PageType.ENTITY, title=title, sources=[source], lang="en"),
    )


# ── D2: serializer byte-shape ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serializer_llmwiki_shape_no_sources_or_lang(api_env: dict[str, Any]) -> None:
    """type is FIRST; created/updated follow title; NO sources:/lang: keys; round-trips."""
    from app.config import settings
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

    page = WikiPage(
        title="Photosynthesis",
        type=PageType.CONCEPT,
        content="Body about photosynthesis.\n",
        frontmatter=WikiFrontmatter(
            type=PageType.CONCEPT,
            title="Photosynthesis",
            sources=["raw/sources/bio.md"],
            lang="en",
            tags=["biology"],
        ),
    )
    await write_wiki_page(None, page, "raw/sources/bio.md")

    abs_path = settings.vault_root / "wiki" / "concepts" / "photosynthesis.md"
    text = abs_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # `type` is the FIRST frontmatter key (byte-shape parity, sort_keys=False).
    assert lines[0] == "---"
    assert lines[1].startswith("type:")

    parsed = fm_lib.loads(text)
    keys = list(parsed.metadata.keys())
    assert keys[0] == "type"
    assert keys[:4] == ["type", "title", "created", "updated"]
    # `tags` follows the date pair; sources / lang are absent from the file.
    assert "tags" in parsed.metadata
    assert "sources" not in parsed.metadata
    assert "lang" not in parsed.metadata
    assert parsed.metadata["type"] == "concept"


@pytest.mark.asyncio
async def test_related_populated_from_resolved_wikilinks(api_env: dict[str, Any]) -> None:
    """related = slugs of live pages the body links to; an unresolved target is dropped."""
    from app.config import settings
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

    # Target entity must exist first so the wikilink resolves to its slug.
    await write_wiki_page(None, _entity("Chloroplast", "raw/sources/bio.md"), "raw/sources/bio.md")

    linker = WikiPage(
        title="Light Reactions",
        type=PageType.CONCEPT,
        content="Occur in the [[Chloroplast]]. Unlike [[Nonexistent Thing]] which is absent.\n",
        frontmatter=WikiFrontmatter(
            type=PageType.CONCEPT,
            title="Light Reactions",
            sources=["raw/sources/bio.md"],
            lang="en",
        ),
    )
    await write_wiki_page(None, linker, "raw/sources/bio.md")

    abs_path = settings.vault_root / "wiki" / "concepts" / "light-reactions.md"
    parsed = fm_lib.loads(abs_path.read_text(encoding="utf-8"))
    # Only the resolvable slug is emitted (ghost target dropped).
    assert parsed.metadata.get("related") == ["chloroplast"]


# ── D2: DB traceability preserved even when the model omits sources ────────────────


@pytest.mark.asyncio
async def test_db_sources_populated_when_model_omits_sources(api_env: dict[str, Any]) -> None:
    """
    A page whose model-frontmatter OMITTED sources still lands with a non-empty pages.sources in
    Postgres (origin injected) — F3 preserved in the DB — while the .md carries no sources key.
    """
    from app.config import settings
    from app.db import get_session
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
    from app.models import Page

    page = WikiPage(
        title="Ribosome",
        type=PageType.ENTITY,
        content="A ribosome translates mRNA.\n",
        # NO sources, NO lang — legal now (ADR-0067 D2).
        frontmatter=WikiFrontmatter(type=PageType.ENTITY, title="Ribosome"),
    )
    row = await write_wiki_page(None, page, "raw/sources/cell.md")

    async with get_session() as sess:
        db = (await sess.execute(select(Page).where(Page.id == row.id))).scalar_one()
    assert db.sources, "pages.sources must be non-empty even when the model omitted sources"
    assert "raw/sources/cell.md" in db.sources

    text = (settings.vault_root / "wiki" / "entities" / "ribosome.md").read_text(encoding="utf-8")
    assert "sources" not in fm_lib.loads(text).metadata


# ── D5: canonical entity key (pure) ────────────────────────────────────────────────


def test_resolve_canonical_entity_key_folds_and_strips() -> None:
    from app.ingest.orchestrator import _resolve_canonical_entity_key as key

    # Acronym / longform / parenthetical / legal-suffix all collapse to one key.
    assert (
        key("AWS")
        == key("Amazon Web Services")
        == key("Amazon Web Services (AWS)")
        == key("amazon web services inc.")
    )
    # Other fold-map pairs.
    assert key("Azure") == key("Microsoft Azure")
    assert key("GCP") == key("Google Cloud Platform")
    # Legal-suffix stripping without nuking the name.
    assert key("Acme Ltd.") == key("Acme")
    assert key("Globex Corp") == key("Globex")
    # Two genuinely different entities MUST NOT collide.
    assert key("Deloitte") != key("Deloitte Italia")
    # An entity literally named a suffix token is never stripped to empty.
    assert key("Inc") == "inc"


# ── D5: write_wiki_page entity canonical merge ─────────────────────────────────────


@pytest.mark.asyncio
async def test_write_wiki_page_entity_canonical_merge(api_env: dict[str, Any]) -> None:
    """
    Writing "Amazon Web Services (AWS)" after "AWS" exists reuses the SAME page id (one file) and
    unions sources — an exact canonical-key merge, not a new ghost entity.
    """
    from app.db import get_session
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType
    from app.models import Page

    row1 = await write_wiki_page(None, _entity("AWS", "raw/sources/a.md"), "raw/sources/a.md")
    row2 = await write_wiki_page(
        None, _entity("Amazon Web Services (AWS)", "raw/sources/b.md"), "raw/sources/b.md"
    )

    # Same id → merged into the pre-existing canonical entity page.
    assert row1.id == row2.id

    async with get_session() as sess:
        rows = (
            (
                await sess.execute(
                    select(Page).where(
                        Page.page_type == PageType.ENTITY.value,
                        Page.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1, "canonical merge must leave exactly one entity file"
    srcs = rows[0].sources or []
    assert "raw/sources/a.md" in srcs
    assert "raw/sources/b.md" in srcs


@pytest.mark.asyncio
async def test_write_wiki_page_distinct_entities_do_not_merge(api_env: dict[str, Any]) -> None:
    """Deloitte vs Deloitte Italia are different canonical keys → two separate pages (no merge)."""
    from app.db import get_session
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType
    from app.models import Page

    r1 = await write_wiki_page(None, _entity("Deloitte", "raw/sources/a.md"), "raw/sources/a.md")
    r2 = await write_wiki_page(
        None, _entity("Deloitte Italia", "raw/sources/b.md"), "raw/sources/b.md"
    )
    assert r1.id != r2.id

    async with get_session() as sess:
        rows = (
            (
                await sess.execute(
                    select(Page).where(
                        Page.page_type == PageType.ENTITY.value,
                        Page.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 2


# ── Six-type source-grounded direct ingest (v1.6.0) ────────────────────────────────


def test_single_doc_ingest_allows_source_grounded_derived_types() -> None:
    """
    v1.6.0 permits direct query/comparison/synthesis generation when the current source supports
    it. The shared scaffold keeps this provider-neutral and retains canonical naming.
    """
    from app.ingest.provider._common import GENERATE_SYSTEM, GENERATION_SCAFFOLD

    assert "entity|concept|source|query|synthesis|comparison" in GENERATE_SYSTEM
    lowered = GENERATE_SYSTEM.lower()
    assert "directly supported by this source" in lowered
    assert "do not create synthesis or comparison pages during ingest" not in lowered
    # Canonical-naming rule present (D5) but additive.
    assert "canonical short name" in GENERATION_SCAFFOLD.lower()


@pytest.mark.asyncio
async def test_corpus_generation_key_reuses_identity_when_title_changes(
    api_env: dict[str, Any],
) -> None:
    """Forced regeneration updates one keyed page/file even if the model changes its title."""
    from app.config import settings
    from app.db import get_session
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
    from app.models import Page

    generation_key = "corpus:synthesis:" + "c" * 64

    def synthesis(title: str, body: str) -> WikiPage:
        return WikiPage(
            title=title,
            type=PageType.SYNTHESIS,
            content=body,
            frontmatter=WikiFrontmatter(
                type=PageType.SYNTHESIS,
                title=title,
                sources=["wiki/concepts/a.md", "wiki/concepts/b.md"],
                synapse_generation_key=generation_key,
            ),
        )

    first = await write_wiki_page(None, synthesis("Initial synthesis", "First body."), "")
    second = await write_wiki_page(None, synthesis("Renamed synthesis", "Replacement body."), "")

    assert first.id == second.id
    assert first.file_path == second.file_path
    assert first.file_path == f"wiki/synthesis/synthesis-{'c' * 20}.md"

    async with get_session() as sess:
        rows = (
            (
                await sess.execute(
                    select(Page).where(
                        Page.vault_id == settings.vault_id,
                        Page.generation_key == generation_key,
                        Page.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    assert rows[0].title == "Renamed synthesis"

    markdown = (settings.vault_root / first.file_path).read_text(encoding="utf-8")
    parsed = fm_lib.loads(markdown)
    assert parsed.metadata["synapse_generation_key"] == generation_key
    assert parsed.content.strip() == "Replacement body."
