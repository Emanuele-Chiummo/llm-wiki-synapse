"""
ADR-0030 Feature B — Embeddings toggle + lexical degrade tests.

Infra-free: SQLite+aiosqlite in-memory DB (mirrors test_retrieval.py fixture shape),
FakeEmbeddingClient, monkeypatched settings. No live Postgres / Qdrant / Ollama.

Coverage:
  ADR-0030 AC-1  embeddings_enabled=False → startup validation SKIPPED (tested via
                 the config path, not lifespan; lifespan guard tested in test_api.py).
  ADR-0030 AC-3  /search with embeddings_enabled=False returns lexical results with
                 contiguous [n] citations and does NOT 500.
  ADR-0030 AC-4  Phase 2 graph-expansion still runs on lexical seeds.
  ADR-0030 AC-6  GET /config/embedding includes embeddings_enabled field.
  ADR-0030 AC-7  Lexical body scan is k-bounded (N≫k pages — only k returned).
  LEX-1          retrieve() with embeddings_enabled=False does NOT call embedding
                 client or Qdrant.
  LEX-2          Lexical search returns pages whose title matches query tokens.
  LEX-3          Pages with no token match are excluded even when embeddings=False.
  LEX-4          retrieve() signature is unchanged (call contract preserved).
  LEX-5          embeddings_enabled=True still calls Qdrant (regression guard).
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
from app.rag.retrieval import RetrievalContext, retrieve
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── Mocked Qdrant (same shape as test_retrieval.py) ────────────────────────────


class _FakePoint:
    def __init__(self, point_id: str, score: float) -> None:
        self.id = point_id
        self.score = score
        self.payload: dict[str, Any] = {}


class _FakeQueryResponse:
    def __init__(self, points: list[_FakePoint]) -> None:
        self.points = points


class _CountingQdrant:
    """Records how many times query_points was called (should be 0 when lexical)."""

    def __init__(self, hits: list[tuple[str, float]] | None = None) -> None:
        self._points = [_FakePoint(pid, score) for pid, score in (hits or [])]
        self.call_count: int = 0

    async def query_points(
        self,
        *,
        collection_name: str,
        query: list[float],
        limit: int,
        with_payload: bool,
    ) -> _FakeQueryResponse:
        self.call_count += 1
        return _FakeQueryResponse(self._points[:limit])


# ── SQLite fixture (minimal schema matching test_retrieval.py) ──────────────────


def _uid(tag: int) -> str:
    return f"00000000-0000-0000-0001-{tag:012d}"


async def _setup_sqlite(engine: Any) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            sa_text(
                """
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
        """
            )
        )
        await conn.execute(
            sa_text(
                """
            CREATE TABLE links (
                id TEXT PRIMARY KEY,
                source_page_id TEXT NOT NULL,
                target_title TEXT NOT NULL,
                target_page_id TEXT,
                dangling INTEGER NOT NULL DEFAULT 0
            )
        """
            )
        )
        await conn.execute(
            sa_text(
                """
            CREATE TABLE edges (
                id TEXT PRIMARY KEY,
                vault_id TEXT NOT NULL,
                source_page_id TEXT NOT NULL,
                target_page_id TEXT NOT NULL,
                weight REAL NOT NULL
            )
        """
            )
        )
        await conn.execute(
            sa_text(
                """
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
                cli_oauth_token TEXT
            )
        """
            )
        )


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
VAULT = "lex-test-vault"


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
    def __init__(self, factory: Any, vault_root: Path) -> None:
        self.factory = factory
        self.vault_root = vault_root


@pytest.fixture()
async def lex_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    """
    In-memory SQLite + tmp vault_root; embeddings_enabled=False; patches retrieval settings.
    Restores all module-level globals on teardown (zero cross-test state).
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await _setup_sqlite(engine)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )

    (tmp_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(retrieval_mod.settings, "vault_path", str(tmp_path))
    # Disable embeddings — the ADR-0030 toggle.
    monkeypatch.setattr(retrieval_mod.settings, "embeddings_enabled", False)

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


# ── LEX-1 — no embedding client or Qdrant call when embeddings=False ──────────


async def test_lex1_no_qdrant_call_when_embeddings_disabled(lex_env: _Env) -> None:
    """
    With embeddings_enabled=False, retrieve() MUST NOT call the Qdrant client
    or the embedding client (ADR-0030 §2.3 — 'no embedding client or Qdrant call').
    """
    p1 = _uid(1)
    async with lex_env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=1)
        await _insert_page(
            sess, page_id=p1, vault_id=VAULT, file_path="wiki/concepts/a.md", title="Alpha Query"
        )
        await sess.commit()
    _write_source(lex_env.vault_root, "wiki/concepts/a.md", "Alpha body content.")

    # Install a counting Qdrant; if it's called the test fails.
    counting_qdrant = _CountingQdrant([(p1, 0.99)])
    retrieval_mod.get_qdrant_client = lambda: counting_qdrant  # type: ignore[assignment]

    # Also monitor the embedding client.
    fake_emb = FakeEmbeddingClient(dim=4)
    embed_calls: list[str] = []
    original_embed = fake_emb.embed

    async def _tracked_embed(text: str) -> list[float]:
        embed_calls.append(text)
        return await original_embed(text)

    fake_emb.embed = _tracked_embed  # type: ignore[method-assign]
    set_embedding_client(fake_emb)

    async with lex_env.factory() as sess:
        ctx = await retrieve("Alpha", vault_id=VAULT, context_window=10_000, k=8, session=sess)

    # Qdrant was not touched.
    assert counting_qdrant.call_count == 0, (
        f"Qdrant.query_points called {counting_qdrant.call_count} times — "
        "should be 0 in lexical mode"
    )
    # Embedding client was not called.
    assert embed_calls == [], f"embed() called {embed_calls} times — should be 0 in lexical mode"
    # But we still get a result.
    assert isinstance(ctx, RetrievalContext)


# ── LEX-2 — title match returns page with [n] citation ─────────────────────────


async def test_lex2_title_match_returns_citation(lex_env: _Env) -> None:
    """
    Pages whose title contains the query token appear in the RetrievalContext with
    correct contiguous [n] citations (ADR-0030 AC-3, AC-F5 shared contract).
    """
    p1 = _uid(1)
    async with lex_env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=2)
        await _insert_page(
            sess,
            page_id=p1,
            vault_id=VAULT,
            file_path="wiki/entities/widget.md",
            title="Widget Overview",
        )
        await sess.commit()
    _write_source(lex_env.vault_root, "wiki/entities/widget.md", "Widget body text here.")

    async with lex_env.factory() as sess:
        ctx = await retrieve("widget", vault_id=VAULT, context_window=10_000, session=sess)

    assert len(ctx.citations) == 1
    assert ctx.citations[0].ref.id == p1
    assert ctx.citations[0].ref.title == "Widget Overview"
    assert "[1]" in ctx.text
    _assert_citation_authority(ctx)


# ── LEX-3 — no match when tokens absent from title ─────────────────────────────


async def test_lex3_no_match_for_unrelated_query(lex_env: _Env) -> None:
    """
    A query with no token overlap against any live page title yields empty results
    (the lexical ILIKE filter correctly excludes them).
    """
    p1 = _uid(1)
    async with lex_env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        await _insert_page(
            sess, page_id=p1, vault_id=VAULT, file_path="wiki/concepts/dog.md", title="Dog Care"
        )
        await sess.commit()
    _write_source(lex_env.vault_root, "wiki/concepts/dog.md", "Dog care body.")

    async with lex_env.factory() as sess:
        ctx = await retrieve(
            "zzz_unrelated_xyz", vault_id=VAULT, context_window=10_000, session=sess
        )

    assert ctx.text == ""
    assert ctx.citations == []
    _assert_citation_authority(ctx)


# ── ADR-0030 AC-7 — lexical query is k-bounded ─────────────────────────────────


async def test_adr0030_ac7_lexical_k_bounded(lex_env: _Env) -> None:
    """
    With N≫k pages all matching the query, lexical Phase 1 MUST return at most k results.
    (ADR-0030 §2.3 bounding guarantee — NEVER loads every page body, I7.)
    """
    N = 30
    k = 5
    ids = [_uid(i) for i in range(1, N + 1)]
    async with lex_env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        for i, pid in enumerate(ids):
            fp = f"wiki/concepts/pg{i}.md"
            await _insert_page(
                sess,
                page_id=pid,
                vault_id=VAULT,
                file_path=fp,
                title=f"Alpha Page {i}",  # ALL titles contain "alpha"
            )
            _write_source(lex_env.vault_root, fp, f"Content of page {i}.")
        await sess.commit()

    async with lex_env.factory() as sess:
        ctx = await retrieve("alpha", vault_id=VAULT, context_window=500_000, k=k, session=sess)

    # MUST return at most k results even though N=30 pages match.
    assert len(ctx.citations) <= k, f"Expected ≤{k} citations (k-bounded), got {len(ctx.citations)}"
    _assert_citation_authority(ctx)


# ── ADR-0030 AC-4 — graph expansion still runs on lexical seeds ────────────────


async def test_adr0030_ac4_graph_expansion_on_lexical_seeds(lex_env: _Env) -> None:
    """
    Phase 2 graph-expansion (BFS over edges) runs UNCHANGED even when Phase 1 used
    lexical search (ADR-0030 §2.3 'Phases 2–4 run UNCHANGED on lexical seeds').
    """
    seed, neighbour = _uid(1), _uid(2)
    async with lex_env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        await _insert_page(
            sess,
            page_id=seed,
            vault_id=VAULT,
            file_path="wiki/entities/seed.md",
            title="Kernel Config",
        )
        await _insert_page(
            sess,
            page_id=neighbour,
            vault_id=VAULT,
            file_path="wiki/entities/neigh.md",
            title="Neighbour Page",
        )
        await _insert_edge(sess, vault_id=VAULT, src=seed, tgt=neighbour, weight=7.0)
        await sess.commit()
    _write_source(lex_env.vault_root, "wiki/entities/seed.md", "Kernel config body.")
    _write_source(lex_env.vault_root, "wiki/entities/neigh.md", "Neighbour body.")

    async with lex_env.factory() as sess:
        ctx = await retrieve(
            "kernel", vault_id=VAULT, context_window=50_000, k=8, expansion_depth=2, session=sess
        )

    cited_ids = {c.ref.id for c in ctx.citations}
    assert seed in cited_ids, "Seed page (lexical hit) should be cited"
    assert neighbour in cited_ids, "Neighbour (graph expansion) should be cited — Phase 2 must run"
    # Graph expansion phase label is "expansion".
    phases = {c.phase for c in ctx.citations}
    assert "expansion" in phases
    _assert_citation_authority(ctx)


# ── LEX-4 — retrieve() signature is unchanged ──────────────────────────────────


def test_lex4_retrieve_signature_unchanged() -> None:
    """
    retrieve() must have the exact call signature from ADR-0022 §2.1 (FROZEN).
    Adding an internal branch must NOT change the public interface.
    """
    import inspect

    sig = inspect.signature(retrieve)
    params = list(sig.parameters)
    assert params == [
        "query",
        "vault_id",
        "context_window",
        "k",
        "expansion_depth",
        "session",
    ], f"retrieve() signature changed: {params}"
    # k=8, expansion_depth=2, session=None are the canonical defaults.
    defaults = {
        name: p.default
        for name, p in sig.parameters.items()
        if p.default is not inspect.Parameter.empty
    }
    assert defaults["k"] == 8
    assert defaults["expansion_depth"] == 2
    assert defaults["session"] is None


# ── LEX-5 — embeddings_enabled=True still calls Qdrant (regression guard) ──────


async def test_lex5_qdrant_called_when_embeddings_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Regression: when embeddings_enabled=True (default), Qdrant IS called.
    Ensures the new branch does not break the enabled path.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await _setup_sqlite(engine)
    factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )

    (tmp_path / "raw" / "sources").mkdir(parents=True)
    monkeypatch.setattr(retrieval_mod.settings, "vault_path", str(tmp_path))
    # embeddings ENABLED.
    monkeypatch.setattr(retrieval_mod.settings, "embeddings_enabled", True)

    original_get_qdrant = retrieval_mod.get_qdrant_client
    set_embedding_client(FakeEmbeddingClient(dim=4))

    p1 = _uid(99)
    async with factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        await _insert_page(
            sess, page_id=p1, vault_id=VAULT, file_path="wiki/entities/z.md", title="Zeta"
        )
        await sess.commit()

    qdrant = _CountingQdrant([(p1, 0.9)])
    retrieval_mod.get_qdrant_client = lambda: qdrant  # type: ignore[assignment]

    async with factory() as sess:
        await retrieve("q", vault_id=VAULT, context_window=10_000, session=sess)

    assert (
        qdrant.call_count == 1
    ), f"Qdrant.query_points should be called once in enabled mode, got {qdrant.call_count}"

    retrieval_mod.get_qdrant_client = original_get_qdrant  # type: ignore[assignment]
    await engine.dispose()


# ── ADR-0030 AC-6 — GET /config/embedding includes embeddings_enabled ──────────


async def test_adr0030_ac6_config_embedding_includes_enabled_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    GET /config/embedding MUST include ``embeddings_enabled`` field (ADR-0030 §2.6, §3).
    Tested directly against the response model to avoid spinning up the full app lifespan.
    """
    import app.main as main_mod

    # Verify the response model has the field (static check).
    from app.main import EmbeddingConfigResponse

    fields = EmbeddingConfigResponse.model_fields
    assert (
        "embeddings_enabled" in fields
    ), "EmbeddingConfigResponse is missing 'embeddings_enabled' field (ADR-0030 §3)"

    # Exercise the handler directly.
    monkeypatch.setattr(main_mod.settings, "embeddings_enabled", False)
    response = await main_mod.get_embedding_config()
    assert response.embeddings_enabled is False

    monkeypatch.setattr(main_mod.settings, "embeddings_enabled", True)
    response = await main_mod.get_embedding_config()
    assert response.embeddings_enabled is True


# ── LEX data_version unchanged in lexical mode ─────────────────────────────────


async def test_lex_data_version_unchanged_in_lexical_mode(lex_env: _Env) -> None:
    """
    data_version must be read-only in lexical mode (AC-F5-5 — unchanged contract).
    """
    p1 = _uid(10)
    async with lex_env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=42)
        await _insert_page(
            sess, page_id=p1, vault_id=VAULT, file_path="wiki/entities/v.md", title="Version Test"
        )
        await sess.commit()
    _write_source(lex_env.vault_root, "wiki/entities/v.md", "Version test body.")

    async with lex_env.factory() as sess:
        ctx = await retrieve("version", vault_id=VAULT, context_window=10_000, session=sess)

    assert ctx.data_version == 42

    # Verify the DB was not modified.
    async with lex_env.factory() as sess:
        result = await sess.execute(
            sa_text("SELECT data_version FROM vault_state WHERE vault_id = :v").bindparams(v=VAULT)
        )
        assert int(result.first()[0]) == 42


# ── LEX multi-token scoring ─────────────────────────────────────────────────────


async def test_lex_multi_token_higher_score_ranks_first(lex_env: _Env) -> None:
    """
    Pages matching more query tokens rank above those matching fewer (score = term overlap).
    """
    p_both = _uid(20)  # title matches BOTH tokens
    p_one = _uid(21)  # title matches only ONE token
    async with lex_env.factory() as sess:
        await _set_data_version(sess, vault_id=VAULT, version=0)
        await _insert_page(
            sess,
            page_id=p_both,
            vault_id=VAULT,
            file_path="wiki/concepts/both.md",
            title="Alpha Beta Overview",
        )
        await _insert_page(
            sess,
            page_id=p_one,
            vault_id=VAULT,
            file_path="wiki/concepts/one.md",
            title="Alpha Reference",
        )
        await sess.commit()
    _write_source(lex_env.vault_root, "wiki/concepts/both.md", "Both tokens body.")
    _write_source(lex_env.vault_root, "wiki/concepts/one.md", "One token body.")

    async with lex_env.factory() as sess:
        ctx = await retrieve("alpha beta", vault_id=VAULT, context_window=50_000, k=8, session=sess)

    assert len(ctx.citations) == 2
    # p_both (2 token hits) should be cited first (n=1).
    assert ctx.citations[0].ref.id == p_both, "Page with more token matches should rank first"
    assert ctx.citations[0].score >= ctx.citations[1].score
    _assert_citation_authority(ctx)
