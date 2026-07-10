"""
F5 4-phase retrieval unit tests (ADR-0022, S-F5-1).

Infra-free: SQLite+aiosqlite in-memory DB, a mocked Qdrant client (returns a QueryResponse-
shaped object with ``.points``), and a FakeEmbeddingClient. No live Postgres / Qdrant / Ollama.

Coverage map (AC → test):
  AC-F5-1  four phases execute in order, RetrievalContext returned ........ test_ac_f5_1_*
  AC-F5-2  [n] markers + PageRef{id,title,slug}; len(citations)==n_passages  test_ac_f5_2_*
  AC-F5-4  budget respected; lowest-ranked dropped; approx_tokens<=budget .. test_ac_f5_4_*
  AC-F5-5  data_version unchanged across the call (read-only) ............... test_ac_f5_5_*
  AC-F5-7  (a) 0-hit empty; (b) single-hit; (c) multi-hit expansion;
           (d) overflow drops lowest-ranked .............................. test_ac_f5_7_*

Every assembly test asserts len(citations) == distinct [n] count in text (single authority).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import app.embeddings as embeddings_mod
import app.rag.retrieval as retrieval_mod
import pytest
from app.embeddings import FakeEmbeddingClient, set_embedding_client
from app.rag.retrieval import RetrievalContext, retrieve, slugify
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── Mocked Qdrant ───────────────────────────────────────────────────────────────


class _FakePoint:
    """ScoredPoint stand-in: id + score (payload unused by retrieve())."""

    def __init__(self, point_id: str, score: float) -> None:
        self.id = point_id
        self.score = score
        self.payload: dict[str, Any] = {}


class _FakeQueryResponse:
    """QueryResponse stand-in: exposes .points like the real client."""

    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points


class _FakeQdrant:
    """Records the query_points call and returns a pre-seeded response (ordered by score)."""

    def __init__(self, hits: list[tuple[str, float]]) -> None:
        self._points = [_FakePoint(pid, score) for pid, score in hits]
        self.calls: list[dict[str, Any]] = []

    async def query_points(
        self,
        *,
        collection_name: str,
        query: list[float],
        limit: int,
        with_payload: bool,
    ) -> _FakeQueryResponse:
        self.calls.append(
            {
                "collection_name": collection_name,
                "limit": limit,
                "with_payload": with_payload,
            }
        )
        return _FakeQueryResponse(self._points[:limit])


# ── SQLite fixture (minimal pages / links / edges / vault_state) ────────────────


def _uid(tag: int) -> str:
    return f"00000000-0000-0000-0000-{tag:012d}"


async def _setup_sqlite(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa_text("""
            CREATE TABLE pages (
                id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                title TEXT,
                type TEXT,
                sources TEXT,
                content_hash TEXT NOT NULL DEFAULT '',
                deleted_at TEXT
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE links (
                id TEXT PRIMARY KEY,
                source_page_id TEXT NOT NULL,
                target_title TEXT NOT NULL,
                target_page_id TEXT,
                dangling INTEGER NOT NULL DEFAULT 0
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE edges (
                id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                source_page_id TEXT NOT NULL,
                target_page_id TEXT NOT NULL,
                weight REAL NOT NULL
            )
        """))
        await conn.execute(sa_text("""
            CREATE TABLE vault_state (
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
                cli_oauth_token_encrypted BLOB,
                searxng_url_db TEXT,
                searxng_categories_db TEXT,
                searxng_max_queries_db INTEGER
            )
        """))


async def _insert_page(
    sess: AsyncSession,
    *,
    page_id: str,
    vault_id: str,
    file_path: str,
    title: str | None,
) -> None:
    await sess.execute(
        sa_text(
            "INSERT INTO pages (id, vault_id, file_path, title) " "VALUES (:id, :vid, :fp, :title)"
        ).bindparams(id=page_id, vid=vault_id, fp=file_path, title=title)
    )


async def _insert_edge(
    sess: AsyncSession,
    *,
    vault_id: str,
    src: str,
    tgt: str,
    weight: float,
) -> None:
    await sess.execute(
        sa_text(
            "INSERT INTO edges (id, vault_id, source_page_id, target_page_id, weight) "
            "VALUES (:id, :vid, :src, :tgt, :w)"
        ).bindparams(id=str(uuid.uuid4()), vid=vault_id, src=src, tgt=tgt, w=weight)
    )


async def _set_data_version(sess: AsyncSession, *, vault_id: str, version: int) -> None:
    await sess.execute(
        sa_text(
            "INSERT INTO vault_state (id, vault_id, data_version) VALUES (:id, :vid, :dv)"
        ).bindparams(id=str(uuid.uuid4()), vid=vault_id, dv=version)
    )


# ── Helpers ─────────────────────────────────────────────────────────────────────

_MARKER_RE = re.compile(r"\[(\d+)\]")
VAULT = "test-vault"


def _distinct_markers(text: str) -> set[int]:
    return {int(m) for m in _MARKER_RE.findall(text)}


def _assert_citation_authority(ctx: RetrievalContext) -> None:
    """len(citations) == distinct [n] in text, and ns are contiguous from 1."""
    markers = _distinct_markers(ctx.text)
    assert len(ctx.citations) == len(
        markers
    ), f"citations={len(ctx.citations)} markers={sorted(markers)}"
    ns = [c.n for c in ctx.citations]
    assert ns == list(range(1, len(ns) + 1)), f"ns not contiguous from 1: {ns}"
    assert markers == set(ns)


class _Env:
    """Bundle: a session factory + a vault_root with source files, wired into retrieval."""

    def __init__(self, factory: Any, vault_root: Path) -> None:
        self.factory = factory
        self.vault_root = vault_root


@pytest.fixture()
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    """In-memory SQLite + a tmp vault_root; patches retrieval's settings.vault_root."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await _setup_sqlite(engine)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )

    # Point retrieval's source-file reads at a tmp vault (NEVER the real vault — I1).
    # settings.vault_root is `Path(self.vault_path).resolve()`, so patching vault_path is
    # sufficient and keeps the real property logic intact.
    (tmp_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(retrieval_mod.settings, "vault_path", str(tmp_path))

    # Deterministic embedding (content irrelevant — Qdrant is mocked). Restore the global
    # default on teardown so this fixture leaves zero cross-test state behind.
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


# ── slugify unit ────────────────────────────────────────────────────────────────


def test_slugify_never_empty() -> None:
    assert slugify("Hello World!") == "hello-world"
    assert slugify("  ") == "untitled"
    assert slugify("Café & Co") == "caf-co"


# ── AC-F5-1 — four phases in order ──────────────────────────────────────────────


async def test_ac_f5_1_four_phases_in_order(env: _Env) -> None:
    """Vector hit → graph expansion → budget → assembly; RetrievalContext returned."""
    p1, p2 = _uid(1), _uid(2)
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=3)
        await _insert_page(
            sess, page_id=p1, vault_id=VAULT, file_path="wiki/concepts/a.md", title="Alpha"
        )
        await _insert_page(
            sess, page_id=p2, vault_id=VAULT, file_path="wiki/entities/b.md", title="Beta"
        )
        await _insert_edge(sess, vault_id=VAULT, src=p1, tgt=p2, weight=9.0)
        await sess.commit()
    _write_source(env.vault_root, "wiki/concepts/a.md", "Alpha source body about widgets.")
    _write_source(env.vault_root, "wiki/entities/b.md", "Beta source body about gadgets.")

    qdrant = _FakeQdrant([(p1, 0.91)])
    retrieval_mod.get_qdrant_client = lambda: qdrant  # type: ignore[assignment]

    async with env.factory() as sess:
        ctx = await retrieve("widgets", vault_id=VAULT, context_window=10_000, k=8, session=sess)

    assert isinstance(ctx, RetrievalContext)
    # Phase 1 ran (Qdrant called with the configured limit).
    assert qdrant.calls and qdrant.calls[0]["limit"] == 8
    # Phase 2 ran: p2 reached via the edge from the p1 seed → expansion citation present.
    phases = {c.phase for c in ctx.citations}
    assert "vector" in phases
    assert "expansion" in phases
    # Phase 4: both [n] present in text; authority holds.
    _assert_citation_authority(ctx)
    assert ctx.data_version == 3


async def test_ac_f5_1_vector_seed_ranks_before_expansion(env: _Env) -> None:
    """Rank order: vector seed (n=1) precedes the edge-weight expansion (n=2)."""
    p1, p2 = _uid(1), _uid(2)
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        await _insert_page(
            sess, page_id=p1, vault_id=VAULT, file_path="wiki/concepts/a.md", title="Seed"
        )
        await _insert_page(
            sess, page_id=p2, vault_id=VAULT, file_path="wiki/entities/b.md", title="Neighbour"
        )
        await _insert_edge(sess, vault_id=VAULT, src=p1, tgt=p2, weight=5.0)
        await sess.commit()
    _write_source(env.vault_root, "wiki/concepts/a.md", "Seed body.")
    _write_source(env.vault_root, "wiki/entities/b.md", "Neighbour body.")

    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([(p1, 0.8)])  # type: ignore[assignment]
    async with env.factory() as sess:
        ctx = await retrieve("q", vault_id=VAULT, context_window=10_000, session=sess)

    by_n = {c.n: c for c in ctx.citations}
    assert by_n[1].phase == "vector"
    assert by_n[1].ref.id == p1
    assert by_n[2].phase == "expansion"
    assert by_n[2].ref.id == p2


# ── AC-F5-2 — [n] markers + PageRef{id,title,slug} ──────────────────────────────


async def test_ac_f5_2_pageref_fields_and_markers(env: _Env) -> None:
    p1 = _uid(1)
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=1)
        await _insert_page(
            sess, page_id=p1, vault_id=VAULT, file_path="wiki/concepts/a.md", title="My Title"
        )
        await sess.commit()
    _write_source(env.vault_root, "wiki/concepts/a.md", "Body text.")

    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([(p1, 0.5)])  # type: ignore[assignment]
    async with env.factory() as sess:
        ctx = await retrieve("q", vault_id=VAULT, context_window=10_000, session=sess)

    assert "[1]" in ctx.text
    cit = ctx.citations[0]
    assert cit.ref.id == p1
    assert cit.ref.title == "My Title"
    assert cit.ref.slug == "my-title"
    _assert_citation_authority(ctx)


async def test_ac_f5_2_title_falls_back_to_file_stem(env: _Env) -> None:
    """NULL frontmatter title → filename stem; never empty (§2.6)."""
    p1 = _uid(1)
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        await _insert_page(
            sess, page_id=p1, vault_id=VAULT, file_path="wiki/sources/report-q3.md", title=None
        )
        await sess.commit()
    _write_source(env.vault_root, "wiki/sources/report-q3.md", "Quarterly body.")

    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([(p1, 0.5)])  # type: ignore[assignment]
    async with env.factory() as sess:
        ctx = await retrieve("q", vault_id=VAULT, context_window=10_000, session=sess)

    assert ctx.citations[0].ref.title == "report-q3"
    assert ctx.citations[0].ref.slug == "report-q3"


# ── AC-F5-4 — budget respected; lowest-ranked dropped ───────────────────────────


async def test_ac_f5_4_budget_drops_lowest_ranked(env: _Env) -> None:
    """10 fixture passages, budget ≈ half their total → fewer than 10 cited; ≤ budget."""
    body = "word " * 200  # ~1000 chars per source
    ids = [_uid(i) for i in range(1, 11)]
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        for i, pid in enumerate(ids):
            fp = f"wiki/concepts/s{i}.md"
            await _insert_page(sess, page_id=pid, vault_id=VAULT, file_path=fp, title=f"Page {i}")
            _write_source(env.vault_root, fp, body)
        await sess.commit()

    # All 10 are vector hits with descending scores (no expansion).
    hits = [(pid, 1.0 - i * 0.05) for i, pid in enumerate(ids)]
    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant(hits)  # type: ignore[assignment]

    # context_window=10_000 → budget_tokens=2_000 → budget_chars=8_000 ≈ half of ~10_400 total.
    async with env.factory() as sess:
        ctx = await retrieve("q", vault_id=VAULT, context_window=10_000, k=10, session=sess)

    assert 0 < len(ctx.citations) < 10, f"expected drops, got {len(ctx.citations)}"
    assert len(ctx.text) <= ctx.token_budget * 4
    assert ctx.approx_tokens <= ctx.token_budget
    # lowest-ranked dropped first: cited ids are a prefix of the ranked order.
    cited_ids = [c.ref.id for c in ctx.citations]
    assert cited_ids == ids[: len(cited_ids)]
    _assert_citation_authority(ctx)


# ── AC-F5-5 — data_version unchanged (read-only) ────────────────────────────────


async def test_ac_f5_5_data_version_unchanged(env: _Env) -> None:
    p1 = _uid(1)
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=7)
        await _insert_page(
            sess, page_id=p1, vault_id=VAULT, file_path="wiki/concepts/a.md", title="A"
        )
        await sess.commit()
    _write_source(env.vault_root, "wiki/concepts/a.md", "Body.")

    async def _read_dv() -> int:
        async with env.factory() as sess:
            r = await sess.execute(
                sa_text("SELECT data_version FROM vault_state WHERE vault_id = :v").bindparams(
                    v=VAULT
                )
            )
            return int(r.first()[0])

    before = await _read_dv()
    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([(p1, 0.9)])  # type: ignore[assignment]
    async with env.factory() as sess:
        ctx = await retrieve("q", vault_id=VAULT, context_window=10_000, session=sess)
    after = await _read_dv()

    assert before == after == 7
    assert ctx.data_version == 7


# ── AC-F5-7 — (a) 0-hit  (b) single  (c) multi-expansion  (d) overflow ──────────


async def test_ac_f5_7a_zero_hit_empty_context(env: _Env) -> None:
    """0-result query → empty context, no citations."""
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=2)
        await sess.commit()
    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([])  # type: ignore[assignment]
    async with env.factory() as sess:
        ctx = await retrieve("q", vault_id=VAULT, context_window=10_000, session=sess)

    assert ctx.text == ""
    assert ctx.citations == []
    assert ctx.approx_tokens == 0
    assert ctx.data_version == 2
    _assert_citation_authority(ctx)


async def test_ac_f5_7b_single_hit(env: _Env) -> None:
    """Single result, no edges → exactly one citation."""
    p1 = _uid(1)
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        await _insert_page(
            sess, page_id=p1, vault_id=VAULT, file_path="wiki/concepts/a.md", title="Solo"
        )
        await sess.commit()
    _write_source(env.vault_root, "wiki/concepts/a.md", "Solo body.")

    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([(p1, 0.77)])  # type: ignore[assignment]
    async with env.factory() as sess:
        ctx = await retrieve("q", vault_id=VAULT, context_window=10_000, session=sess)

    assert len(ctx.citations) == 1
    assert ctx.citations[0].ref.id == p1
    assert ctx.citations[0].phase == "vector"
    _assert_citation_authority(ctx)


async def test_ac_f5_7c_multi_page_expansion(env: _Env) -> None:
    """Seed pages with known links/edges → linked pages surface in the expansion phase."""
    seed, e1, e2, far = _uid(1), _uid(2), _uid(3), _uid(4)
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        for pid, name, fp in [
            (seed, "Seed", "wiki/entities/seed.md"),
            (e1, "Edge1", "wiki/entities/e1.md"),
            (e2, "Edge2", "wiki/entities/e2.md"),
            (far, "Far", "wiki/entities/far.md"),
        ]:
            await _insert_page(sess, page_id=pid, vault_id=VAULT, file_path=fp, title=name)
            _write_source(env.vault_root, fp, f"{name} body content here.")
        # seed → e1 (edge), seed → e2 (edge); e2 → far (depth-2 reach)
        await _insert_edge(sess, vault_id=VAULT, src=seed, tgt=e1, weight=8.0)
        await _insert_edge(sess, vault_id=VAULT, src=seed, tgt=e2, weight=6.0)
        await _insert_edge(sess, vault_id=VAULT, src=e2, tgt=far, weight=4.0)
        await sess.commit()

    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([(seed, 0.95)])  # type: ignore[assignment]
    async with env.factory() as sess:
        ctx = await retrieve(
            "q", vault_id=VAULT, context_window=50_000, k=8, expansion_depth=2, session=sess
        )

    cited_ids = {c.ref.id for c in ctx.citations}
    assert seed in cited_ids
    assert e1 in cited_ids and e2 in cited_ids  # depth-1 neighbours
    assert far in cited_ids  # depth-2 neighbour
    # expansion ordering: higher edge weight ranks earlier among expansions
    exp = [c for c in ctx.citations if c.phase == "expansion"]
    exp_scores = [c.score for c in exp]
    assert exp_scores == sorted(exp_scores, reverse=True)
    _assert_citation_authority(ctx)


async def test_ac_f5_7c_expansion_depth_hard_capped_at_2(env: _Env) -> None:
    """A depth-3 chain page is NOT reached even when expansion_depth=5 is requested."""
    seed, d1, d2, d3 = _uid(1), _uid(2), _uid(3), _uid(4)
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        for pid, fp in [
            (seed, "wiki/entities/s.md"),
            (d1, "wiki/entities/d1.md"),
            (d2, "wiki/entities/d2.md"),
            (d3, "wiki/entities/d3.md"),
        ]:
            await _insert_page(sess, page_id=pid, vault_id=VAULT, file_path=fp, title=fp)
            _write_source(env.vault_root, fp, "body")
        await _insert_edge(sess, vault_id=VAULT, src=seed, tgt=d1, weight=9.0)
        await _insert_edge(sess, vault_id=VAULT, src=d1, tgt=d2, weight=9.0)
        await _insert_edge(sess, vault_id=VAULT, src=d2, tgt=d3, weight=9.0)  # depth 3
        await sess.commit()

    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([(seed, 0.9)])  # type: ignore[assignment]
    async with env.factory() as sess:
        ctx = await retrieve(
            "q", vault_id=VAULT, context_window=50_000, expansion_depth=5, session=sess
        )

    cited = {c.ref.id for c in ctx.citations}
    assert d1 in cited and d2 in cited  # depth 1 and 2
    assert d3 not in cited  # depth 3 — HARD cap blocked it


async def test_ac_f5_7c_resolved_links_expansion(env: _Env) -> None:
    """Resolved links.target_page_id are followed even with no backing edge."""
    seed, tgt = _uid(1), _uid(2)
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        await _insert_page(
            sess, page_id=seed, vault_id=VAULT, file_path="wiki/entities/s.md", title="Seed"
        )
        await _insert_page(
            sess, page_id=tgt, vault_id=VAULT, file_path="wiki/entities/t.md", title="Linked"
        )
        _write_source(env.vault_root, "wiki/entities/s.md", "seed body")
        _write_source(env.vault_root, "wiki/entities/t.md", "linked body")
        await sess.execute(
            sa_text(
                "INSERT INTO links (id, source_page_id, target_title, target_page_id, dangling) "
                "VALUES (:id, :src, 'Linked', :tgt, 0)"
            ).bindparams(id=str(uuid.uuid4()), src=seed, tgt=tgt)
        )
        await sess.commit()

    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([(seed, 0.9)])  # type: ignore[assignment]
    async with env.factory() as sess:
        ctx = await retrieve("q", vault_id=VAULT, context_window=50_000, session=sess)

    cited = {c.ref.id for c in ctx.citations}
    assert tgt in cited


async def test_ac_f5_7d_overflow_drops_until_satisfied(env: _Env) -> None:
    """Many large passages, tiny budget → only as many as fit are cited; ≤ budget."""
    ids = [_uid(i) for i in range(1, 9)]
    big = "x" * 4000
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        for i, pid in enumerate(ids):
            fp = f"wiki/synthesis/big{i}.md"
            await _insert_page(sess, page_id=pid, vault_id=VAULT, file_path=fp, title=f"Big {i}")
            _write_source(env.vault_root, fp, big)
        await sess.commit()

    hits = [(pid, 1.0 - i * 0.1) for i, pid in enumerate(ids)]
    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant(hits)  # type: ignore[assignment]

    # context_window=4_000 → budget_tokens=800 → budget_chars=3_200 (smaller than one source).
    async with env.factory() as sess:
        ctx = await retrieve("q", vault_id=VAULT, context_window=4_000, k=8, session=sess)

    assert len(ctx.citations) < 8
    assert len(ctx.text) <= ctx.token_budget * 4
    assert ctx.approx_tokens <= ctx.token_budget
    _assert_citation_authority(ctx)


async def test_soft_deleted_page_not_cited(env: _Env) -> None:
    """A soft-deleted hit page (deleted_at set) is skipped, never cited."""
    p1, p2 = _uid(1), _uid(2)
    async with env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        await _insert_page(
            sess, page_id=p1, vault_id=VAULT, file_path="wiki/concepts/a.md", title="Live"
        )
        await _insert_page(
            sess, page_id=p2, vault_id=VAULT, file_path="wiki/entities/b.md", title="Dead"
        )
        await sess.execute(
            sa_text("UPDATE pages SET deleted_at = '2026-01-01' WHERE id = :id").bindparams(id=p2)
        )
        await sess.commit()
    _write_source(env.vault_root, "wiki/concepts/a.md", "live body")
    _write_source(env.vault_root, "wiki/entities/b.md", "dead body")

    retrieval_mod.get_qdrant_client = lambda: _FakeQdrant([(p1, 0.9), (p2, 0.8)])  # type: ignore[assignment]
    async with env.factory() as sess:
        ctx = await retrieve("q", vault_id=VAULT, context_window=10_000, session=sess)

    cited = {c.ref.id for c in ctx.citations}
    assert p1 in cited
    assert p2 not in cited
    _assert_citation_authority(ctx)
