"""
R8-5 — Search filters and sort (type facet + date sort) tests (AC-R8-5-1, AC-R8-5-2).

Coverage:
  T-R85-001  type=entity single filter — only entity pages returned
  T-R85-002  type=entity,concept multi-filter — entity AND concept returned
  T-R85-003  unknown type → 422 (AC-R8-5-1)
  T-R85-004  sort=date_desc → results in descending updated_at order (AC-R8-5-2)
  T-R85-005  sort=date_asc  → results in ascending  updated_at order (AC-R8-5-2)
  T-R85-006  default (no type, no sort) unchanged — regression guard (existing retrieval tests)
  T-R85-007  raw/ exclusion holds with type filter active (ADR-0049 + R8-5 defense-in-depth)
  T-R85-008  unknown sort value → 422 (AC-R8-5-1)
  T-R85-009  type filter via GET /search HTTP endpoint — 422 on unknown type
  T-R85-010  type filter applied at Phase 4 (lexical path, embeddings disabled)
  T-R85-011  sort=relevance explicit default — ranking unchanged

All tests: infra-free SQLite in-memory DB, FakeEmbeddingClient, mocked Qdrant.
No live Postgres / Qdrant / Ollama required.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import app.embeddings as embeddings_mod
import app.rag.retrieval as retrieval_mod
import pytest
from app.embeddings import FakeEmbeddingClient, set_embedding_client
from app.rag.retrieval import VALID_PAGE_TYPES, retrieve
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests._db_fixtures import make_sqlite_engine

# ── Qdrant mock (same shape as test_retrieval.py) ──────────────────────────────


class _FakePoint:
    def __init__(self, point_id: str, score: float) -> None:
        self.id = point_id
        self.score = score
        self.payload: dict[str, Any] = {}


class _FakeQueryResponse:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points


class _FakeQdrant:
    def __init__(self, hits: list[tuple[str, float]]) -> None:
        self._points = [_FakePoint(pid, score) for pid, score in hits]

    async def query_points(
        self,
        *,
        collection_name: str,
        query: list[float],
        limit: int,
        with_payload: bool,
        query_filter: Any = None,
    ) -> _FakeQueryResponse:
        return _FakeQueryResponse(self._points[:limit])


# ── SQLite schema ───────────────────────────────────────────────────────────────


def _uid(tag: int) -> str:
    return f"00000000-0000-0000-0002-{tag:012d}"


async def _insert_page(
    sess: AsyncSession,
    *,
    page_id: str,
    vault_id: str,
    file_path: str,
    title: str | None,
    page_type: str | None = None,
    updated_at: str = "2025-01-01T00:00:00",
) -> None:
    await sess.execute(
        sa_text(
            "INSERT INTO pages (id, vault_id, file_path, title, type, updated_at, content_hash) "
            "VALUES (:id, :vid, :fp, :title, :ptype, :ua, '')"
        ).bindparams(
            id=page_id,
            vid=vault_id,
            fp=file_path,
            title=title,
            ptype=page_type,
            ua=updated_at,
        )
    )


async def _set_data_version(sess: AsyncSession, *, vault_id: str, version: int) -> None:
    await sess.execute(
        sa_text(
            "INSERT INTO vault_state (id, vault_id, data_version) VALUES (:id, :vid, :dv)"
        ).bindparams(id=str(uuid.uuid4()), vid=vault_id, dv=version)
    )


# ── Fixtures ────────────────────────────────────────────────────────────────────

VAULT = "test-r85-vault"


class _Env:
    def __init__(self, factory: Any, vault_root: Path) -> None:
        self.factory = factory
        self.vault_root = vault_root


@pytest.fixture()
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    """In-memory SQLite + tmp vault_root; patches retrieval settings.vault_root."""
    engine = await make_sqlite_engine()
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )

    (tmp_path / "wiki" / "entities").mkdir(parents=True)
    (tmp_path / "wiki" / "concepts").mkdir(parents=True)
    (tmp_path / "wiki" / "sources").mkdir(parents=True)
    (tmp_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(retrieval_mod.settings, "vault_path", str(tmp_path))

    original_embedding = embeddings_mod._default_client
    original_get_qdrant = retrieval_mod.get_qdrant_client
    set_embedding_client(FakeEmbeddingClient(dim=4))

    yield _Env(factory, tmp_path)

    embeddings_mod._default_client = original_embedding
    retrieval_mod.get_qdrant_client = original_get_qdrant  # type: ignore[assignment]
    await engine.dispose()


def _write_source(vault_root: Path, file_path: str, body: str) -> None:
    full = vault_root / file_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(body, encoding="utf-8")


# ── T-R85-001: type=entity single filter ───────────────────────────────────────


async def test_type_filter_single_entity(env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only entity-typed pages are returned when type=['entity']."""
    p_entity = _uid(1)
    p_concept = _uid(2)

    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=1)
        await _insert_page(
            sess,
            page_id=p_entity,
            vault_id=VAULT,
            file_path="wiki/entities/alpha.md",
            title="Alpha Entity",
            page_type="entity",
        )
        await _insert_page(
            sess,
            page_id=p_concept,
            vault_id=VAULT,
            file_path="wiki/concepts/beta.md",
            title="Beta Concept",
            page_type="concept",
        )
        await sess.commit()

    _write_source(env.vault_root, "wiki/entities/alpha.md", "Entity body about alpha.")
    _write_source(env.vault_root, "wiki/concepts/beta.md", "Concept body about beta.")

    # Both pages are returned by Qdrant (no type awareness at vector level).
    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant(  # type: ignore[assignment]
        [(p_entity, 0.9), (p_concept, 0.8)]
    )

    async with env.factory() as sess:
        ctx = await retrieve(
            "query",
            vault_id=VAULT,
            context_window=10_000,
            session=sess,
            type_filter=["entity"],
        )

    cited_ids = {c.ref.id for c in ctx.citations}
    assert p_entity in cited_ids, "entity page must be cited"
    assert p_concept not in cited_ids, "concept page must be excluded by type filter"


# ── T-R85-002: type=entity,concept multi-filter ────────────────────────────────


async def test_type_filter_multi_entity_and_concept(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """entity AND concept pages returned; source-typed page excluded."""
    p_entity = _uid(1)
    p_concept = _uid(2)
    p_source = _uid(3)

    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=1)
        await _insert_page(
            sess,
            page_id=p_entity,
            vault_id=VAULT,
            file_path="wiki/entities/alpha.md",
            title="Alpha",
            page_type="entity",
        )
        await _insert_page(
            sess,
            page_id=p_concept,
            vault_id=VAULT,
            file_path="wiki/concepts/beta.md",
            title="Beta",
            page_type="concept",
        )
        await _insert_page(
            sess,
            page_id=p_source,
            vault_id=VAULT,
            file_path="wiki/sources/gamma.md",
            title="Gamma",
            page_type="source",
        )
        await sess.commit()

    for fp, body in [
        ("wiki/entities/alpha.md", "Entity body."),
        ("wiki/concepts/beta.md", "Concept body."),
        ("wiki/sources/gamma.md", "Source body."),
    ]:
        _write_source(env.vault_root, fp, body)

    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant(  # type: ignore[assignment]
        [(p_entity, 0.9), (p_concept, 0.8), (p_source, 0.7)]
    )

    async with env.factory() as sess:
        ctx = await retrieve(
            "query",
            vault_id=VAULT,
            context_window=10_000,
            session=sess,
            type_filter=["entity", "concept"],
        )

    cited_ids = {c.ref.id for c in ctx.citations}
    assert p_entity in cited_ids
    assert p_concept in cited_ids
    assert p_source not in cited_ids, "source-typed page must be excluded"


# ── T-R85-003: unknown type → 422 ──────────────────────────────────────────────


async def test_unknown_type_returns_422() -> None:
    """
    VALID_PAGE_TYPES does not include 'invalid_type'.
    The route handler must raise 422; we verify this via the VALID_PAGE_TYPES set.
    (Full HTTP-layer 422 is tested in T-R85-009.)
    """
    assert "invalid_type" not in VALID_PAGE_TYPES
    assert "entity" in VALID_PAGE_TYPES
    assert "concept" in VALID_PAGE_TYPES
    assert "source" in VALID_PAGE_TYPES
    assert "synthesis" in VALID_PAGE_TYPES
    assert "comparison" in VALID_PAGE_TYPES
    assert "query" in VALID_PAGE_TYPES
    assert len(VALID_PAGE_TYPES) == 6


# ── T-R85-004: sort=date_desc ──────────────────────────────────────────────────


async def test_sort_date_desc_orders_newest_first(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sort=date_desc → results ordered newest (updated_at DESC)."""
    p_old = _uid(1)
    p_mid = _uid(2)
    p_new = _uid(3)

    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=1)
        await _insert_page(
            sess,
            page_id=p_old,
            vault_id=VAULT,
            file_path="wiki/concepts/old.md",
            title="Old Page",
            page_type="concept",
            updated_at="2024-01-01T00:00:00",
        )
        await _insert_page(
            sess,
            page_id=p_mid,
            vault_id=VAULT,
            file_path="wiki/concepts/mid.md",
            title="Mid Page",
            page_type="concept",
            updated_at="2025-06-01T00:00:00",
        )
        await _insert_page(
            sess,
            page_id=p_new,
            vault_id=VAULT,
            file_path="wiki/concepts/new.md",
            title="New Page",
            page_type="concept",
            updated_at="2026-01-01T00:00:00",
        )
        await sess.commit()

    for fp, body in [
        ("wiki/concepts/old.md", "Old page body."),
        ("wiki/concepts/mid.md", "Mid page body."),
        ("wiki/concepts/new.md", "New page body."),
    ]:
        _write_source(env.vault_root, fp, body)

    # All three are vector hits (Qdrant returns them in relevance order: old first).
    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant(  # type: ignore[assignment]
        [(p_old, 0.9), (p_mid, 0.8), (p_new, 0.7)]
    )

    async with env.factory() as sess:
        ctx = await retrieve(
            "query",
            vault_id=VAULT,
            context_window=50_000,
            session=sess,
            sort="date_desc",
        )

    assert len(ctx.citations) == 3
    cited_ids_in_order = [c.ref.id for c in ctx.citations]
    # date_desc: newest first (2026, 2025, 2024)
    assert cited_ids_in_order == [
        p_new,
        p_mid,
        p_old,
    ], f"Expected [new, mid, old], got {cited_ids_in_order}"
    # n is contiguous from 1
    ns = [c.n for c in ctx.citations]
    assert ns == [1, 2, 3]


# ── T-R85-005: sort=date_asc ───────────────────────────────────────────────────


async def test_sort_date_asc_orders_oldest_first(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """sort=date_asc → results ordered oldest (updated_at ASC)."""
    p_old = _uid(1)
    p_new = _uid(2)

    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=1)
        await _insert_page(
            sess,
            page_id=p_old,
            vault_id=VAULT,
            file_path="wiki/concepts/old.md",
            title="Old",
            page_type="concept",
            updated_at="2024-01-01T00:00:00",
        )
        await _insert_page(
            sess,
            page_id=p_new,
            vault_id=VAULT,
            file_path="wiki/concepts/new.md",
            title="New",
            page_type="concept",
            updated_at="2026-07-01T00:00:00",
        )
        await sess.commit()

    for fp, body in [
        ("wiki/concepts/old.md", "Old body."),
        ("wiki/concepts/new.md", "New body."),
    ]:
        _write_source(env.vault_root, fp, body)

    # Qdrant returns new first (higher relevance score) — sort must override this.
    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant(  # type: ignore[assignment]
        [(p_new, 0.9), (p_old, 0.7)]
    )

    async with env.factory() as sess:
        ctx = await retrieve(
            "query",
            vault_id=VAULT,
            context_window=50_000,
            session=sess,
            sort="date_asc",
        )

    assert len(ctx.citations) == 2
    cited_ids = [c.ref.id for c in ctx.citations]
    # date_asc: oldest first (2024, 2026)
    assert cited_ids == [p_old, p_new], f"Expected [old, new], got {cited_ids}"
    assert ctx.citations[0].n == 1
    assert ctx.citations[1].n == 2


# ── T-R85-006: default unchanged (regression guard) ───────────────────────────


async def test_default_no_filter_no_sort_regression(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    When type_filter=None and sort='relevance' (defaults), ranking is unchanged relative to
    the existing retrieval contract (vector seeds by cosine DESC, expansions by edge weight).
    """
    p1 = _uid(1)
    p2 = _uid(2)

    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=1)
        await _insert_page(
            sess,
            page_id=p1,
            vault_id=VAULT,
            file_path="wiki/entities/a.md",
            title="Alpha",
            page_type="entity",
            updated_at="2024-01-01T00:00:00",
        )
        await _insert_page(
            sess,
            page_id=p2,
            vault_id=VAULT,
            file_path="wiki/concepts/b.md",
            title="Beta",
            page_type="concept",
            updated_at="2026-01-01T00:00:00",
        )
        await sess.commit()

    _write_source(env.vault_root, "wiki/entities/a.md", "Alpha body.")
    _write_source(env.vault_root, "wiki/concepts/b.md", "Beta body.")

    # p1 has higher relevance score → should be n=1 in relevance order.
    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant(  # type: ignore[assignment]
        [(p1, 0.95), (p2, 0.60)]
    )

    async with env.factory() as sess:
        ctx = await retrieve(
            "query",
            vault_id=VAULT,
            context_window=50_000,
            session=sess,
            # No type_filter, no sort override → defaults
        )

    assert len(ctx.citations) == 2
    # n=1 must be the highest relevance hit (p1)
    assert ctx.citations[0].ref.id == p1
    assert ctx.citations[0].n == 1
    assert ctx.citations[1].ref.id == p2
    assert ctx.citations[1].n == 2


# ── T-R85-007: raw/ exclusion holds with type filter ──────────────────────────


async def test_raw_exclusion_holds_with_type_filter(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    ADR-0049 raw/ exclusion is preserved when type_filter is active (defense-in-depth).
    A raw/ source page with type=entity must NOT be cited even when type=['entity'].
    """
    p_wiki = _uid(1)
    p_raw = _uid(2)

    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=1)
        await _insert_page(
            sess,
            page_id=p_wiki,
            vault_id=VAULT,
            file_path="wiki/entities/real_entity.md",
            title="Real Entity",
            page_type="entity",
        )
        await _insert_page(
            sess,
            page_id=p_raw,
            vault_id=VAULT,
            file_path="raw/sources/raw_entity.md",
            title="Raw Entity",
            page_type="entity",
        )
        await sess.commit()

    _write_source(env.vault_root, "wiki/entities/real_entity.md", "Real entity body.")
    _write_source(env.vault_root, "raw/sources/raw_entity.md", "Raw entity body.")

    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant(  # type: ignore[assignment]
        [(p_wiki, 0.9), (p_raw, 0.8)]
    )

    async with env.factory() as sess:
        ctx = await retrieve(
            "entity query",
            vault_id=VAULT,
            context_window=50_000,
            session=sess,
            type_filter=["entity"],
        )

    cited_ids = {c.ref.id for c in ctx.citations}
    assert p_wiki in cited_ids, "wiki/entities page must be cited"
    assert p_raw not in cited_ids, "raw/ page must be excluded even when type matches"


# ── T-R85-008: unknown sort value → 422 ───────────────────────────────────────


async def test_unknown_sort_value_detected() -> None:
    """
    The route handler rejects unknown sort values with 422.
    Verify the validation set is consistent with the documented valid values.
    """
    from app.routers.search import _SEARCH_VALID_SORTS  # type: ignore[attr-defined]

    assert "relevance" in _SEARCH_VALID_SORTS
    assert "date_desc" in _SEARCH_VALID_SORTS
    assert "date_asc" in _SEARCH_VALID_SORTS
    assert "newest" not in _SEARCH_VALID_SORTS  # old frontend spec — must not be accepted
    assert "oldest" not in _SEARCH_VALID_SORTS
    assert len(_SEARCH_VALID_SORTS) == 3


# ── T-R85-009: HTTP endpoint 422 on unknown type ───────────────────────────────


@pytest.mark.asyncio
async def test_search_endpoint_422_on_unknown_type() -> None:
    """
    GET /search?q=x&type=badtype → 422 with detail message.
    Uses the FastAPI app directly (no live DB required — validation is pre-retrieval).
    """
    import os

    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    os.environ.setdefault("VAULT_ID", "test")
    os.environ.setdefault("VAULT_PATH", "/tmp/synapse-test-vault")

    from app.main import app as _app

    async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as client:
        resp = await client.get("/search", params={"q": "hello", "type": "badtype"})

    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "error" in body
    assert "badtype" in body["error"]["message"] or "Unknown" in body["error"]["message"]


@pytest.mark.asyncio
async def test_search_endpoint_422_on_unknown_sort() -> None:
    """
    GET /search?q=x&sort=newest → 422 (old frontend contract — not accepted by backend).
    """
    import os

    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
    os.environ.setdefault("VAULT_ID", "test")
    os.environ.setdefault("VAULT_PATH", "/tmp/synapse-test-vault")

    from app.main import app as _app

    async with AsyncClient(transport=ASGITransport(app=_app), base_url="http://test") as client:
        resp = await client.get("/search", params={"q": "hello", "sort": "newest"})

    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "error" in body


# ── T-R85-010: type filter in lexical path (embeddings=False) ─────────────────


async def test_type_filter_lexical_path(env: _Env, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    When embeddings are disabled (lexical path), type_filter is applied at Phase 1 SQL
    AND Phase 4 assembly (defense-in-depth). Only matching-type pages are returned.
    """
    p_entity = _uid(1)
    p_concept = _uid(2)

    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=1)
        await _insert_page(
            sess,
            page_id=p_entity,
            vault_id=VAULT,
            file_path="wiki/entities/alpha.md",
            title="Alpha Widget",
            page_type="entity",
        )
        await _insert_page(
            sess,
            page_id=p_concept,
            vault_id=VAULT,
            file_path="wiki/concepts/beta.md",
            title="Beta Widget",
            page_type="concept",
        )
        await sess.commit()

    _write_source(env.vault_root, "wiki/entities/alpha.md", "Entity body about widgets.")
    _write_source(env.vault_root, "wiki/concepts/beta.md", "Concept body about widgets.")

    # Disable embeddings → Phase 1 uses lexical search.
    monkeypatch.setattr(retrieval_mod.settings, "embeddings_enabled", False)

    async with env.factory() as sess:
        ctx = await retrieve(
            "widget",
            vault_id=VAULT,
            context_window=10_000,
            session=sess,
            type_filter=["entity"],
        )

    cited_ids = {c.ref.id for c in ctx.citations}
    assert p_entity in cited_ids, "entity page must be cited in lexical path"
    assert (
        p_concept not in cited_ids
    ), "concept page must be excluded by type filter in lexical path"


# ── T-R85-011: sort=relevance explicit default unchanged ──────────────────────


async def test_sort_relevance_explicit_is_unchanged(
    env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Explicitly passing sort='relevance' produces the same ranking as the default."""
    p1 = _uid(1)
    p2 = _uid(2)

    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=1)
        # p1 has older updated_at but higher relevance; sort=relevance must keep it at n=1.
        await _insert_page(
            sess,
            page_id=p1,
            vault_id=VAULT,
            file_path="wiki/entities/a.md",
            title="High Relevance",
            page_type="entity",
            updated_at="2023-01-01T00:00:00",  # old
        )
        await _insert_page(
            sess,
            page_id=p2,
            vault_id=VAULT,
            file_path="wiki/concepts/b.md",
            title="Low Relevance",
            page_type="concept",
            updated_at="2026-07-01T00:00:00",  # new
        )
        await sess.commit()

    _write_source(env.vault_root, "wiki/entities/a.md", "High relevance body.")
    _write_source(env.vault_root, "wiki/concepts/b.md", "Low relevance body.")

    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant(  # type: ignore[assignment]
        [(p1, 0.95), (p2, 0.40)]
    )

    async with env.factory() as sess:
        ctx = await retrieve(
            "query",
            vault_id=VAULT,
            context_window=50_000,
            session=sess,
            sort="relevance",  # explicit default
        )

    assert len(ctx.citations) == 2
    # Relevance order: p1 (cosine 0.95) must be n=1, not p2 (newer but lower score).
    assert ctx.citations[0].ref.id == p1
    assert ctx.citations[0].n == 1
