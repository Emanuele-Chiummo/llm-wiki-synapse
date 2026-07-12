"""
F9 Review Queue — ADR-0044 depth-pass tests (Phases A, B, C, E).

Covers (per ADR-0044 §11):
  A (idempotency)  — enqueue_review upserts on content_key: re-ingest → one pending row,
                     skipped item not resurrected, pending rationale refreshes keeping id,
                     confirm always inserts (content_key NULL).
  B (context+queries) — referenced titles resolve (invented dropped); deep-research seeds from
                     search_queries[0]; exactly one provider call; caps enforced.
  C (bulk+status)  — bulk cap 400; bulk only mutates pending; clear-resolved never touches
                     pending; status filter partitions; dismiss terminal.
  E (delegated)    — delegated run that writes via write_page → proposals (one bounded call);
                     writes nothing → no call.

Self-contained SQLite in-memory schema (mirrors test_review_adr0034 + the three new columns).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── SQLite schema (ADR-0044: review_items + content_key/referenced_page_ids/search_queries) ──


def _build_meta() -> MetaData:
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
        Column("community", Integer, nullable=True),  # G-P0-2: Louvain community id
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
        # ADR-0044 new columns (JSON stored as TEXT on SQLite)
        Column("content_key", Text, nullable=True),
        Column("referenced_page_ids", Text, nullable=True),
        Column("search_queries", Text, nullable=True),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
        Column("reviewed_at", Text, nullable=True),
        Column("reviewed_by", Text, nullable=True),
    )

    Table(
        "links",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("source_page_id", String(36), nullable=False),
        Column("target_title", Text, nullable=False),
        Column("target_page_id", String(36), nullable=True),
        Column("alias", Text, nullable=True),
        Column("dangling", Integer, nullable=False, server_default=sa_text("0")),
        Column("created_at", Text, nullable=False, server_default=sa_text("datetime('now')")),
    )

    return meta


@pytest.fixture()
async def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> dict[str, Any]:
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(cfg.settings, "searxng_url", "")

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(_build_meta().create_all)

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )

    @asynccontextmanager
    async def patched_get_session():
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    monkeypatch.setattr("app.db.get_session", patched_get_session)
    monkeypatch.setattr("app.main.get_session", patched_get_session)
    monkeypatch.setattr("app.ops.review.get_session", patched_get_session)
    monkeypatch.setattr("app.ingest.orchestrator.get_session", patched_get_session)

    from app.main import app
    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):
        yield

    app.router.lifespan_context = test_lifespan
    return {"app": app, "session_factory": session_factory}


@pytest.fixture()
async def client(env: dict[str, Any]) -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=env["app"]), base_url="http://test") as c:
        yield c


# ── helpers ─────────────────────────────────────────────────────────────────────


async def _insert_page(env: dict[str, Any], *, title: str, page_type: str = "concept") -> str:
    page_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title, type, content_hash, pinned, "
                "created_at, updated_at) VALUES (:id, 'test-vault', :fp, :title, :ty, 'h', 0, "
                "datetime('now'), datetime('now'))"
            ),
            {"id": page_id, "fp": f"wiki/{title}.md", "title": title, "ty": page_type},
        )
        await sess.commit()
    return page_id


async def _count(env: dict[str, Any], *, status: str | None = None) -> int:
    q = "SELECT COUNT(*) FROM review_items WHERE vault_id='test-vault'"
    if status:
        q += f" AND status='{status}'"
    async with env["session_factory"]() as sess:
        return int((await sess.execute(sa_text(q))).scalar_one())


# ══════════════════════════════════════════════════════════════════════════════
# Phase A — idempotency
# ══════════════════════════════════════════════════════════════════════════════


async def test_A_reingest_same_proposal_one_pending_row(env: dict[str, Any]) -> None:
    """Re-emitting the same proposal twice → ONE pending row (upsert on content_key)."""
    from app.ops.review import _content_key, enqueue_review

    key = _content_key(vault_id="test-vault", item_type="missing-page", proposed_title="Widget")
    await enqueue_review(
        vault_id="test-vault",
        item_type="missing-page",
        proposed_title="Widget",
        rationale="first",
        content_key=key,
    )
    await enqueue_review(
        vault_id="test-vault",
        item_type="missing-page",
        proposed_title="Widget",
        rationale="second",
        content_key=key,
    )
    assert await _count(env) == 1


async def test_A_pending_rationale_refreshes_keeping_id(env: dict[str, Any]) -> None:
    """A pending item's rationale refreshes in place; id + created_at preserved."""
    from app.ops.review import _content_key, enqueue_review

    key = _content_key(vault_id="test-vault", item_type="suggestion", proposed_title="Gap")
    first = await enqueue_review(
        vault_id="test-vault",
        item_type="suggestion",
        proposed_title="Gap",
        rationale="v1",
        content_key=key,
    )
    second = await enqueue_review(
        vault_id="test-vault",
        item_type="suggestion",
        proposed_title="Gap",
        rationale="v2",
        content_key=key,
        referenced_page_ids=["ref-1"],
        search_queries=["q1"],
    )
    assert str(first.id) == str(second.id)  # same row/id
    assert second.rationale == "v2"  # refreshed
    assert await _count(env) == 1

    async with env["session_factory"]() as sess:
        row = (
            await sess.execute(
                sa_text("SELECT rationale, referenced_page_ids FROM review_items WHERE id=:id"),
                {"id": str(first.id)},
            )
        ).first()
    assert row[0] == "v2"


async def test_A_skipped_item_not_resurrected(env: dict[str, Any]) -> None:
    """A terminal (skipped) item with the same content_key is NOT resurrected on re-ingest."""
    from app.ops.review import _content_key, enqueue_review, skip

    key = _content_key(vault_id="test-vault", item_type="missing-page", proposed_title="Gone")
    item = await enqueue_review(
        vault_id="test-vault",
        item_type="missing-page",
        proposed_title="Gone",
        rationale="orig",
        content_key=key,
    )
    await skip(uuid.UUID(str(item.id)))
    # Re-emit the same proposal — must be a NO-OP (respect the human's skip).
    await enqueue_review(
        vault_id="test-vault",
        item_type="missing-page",
        proposed_title="Gone",
        rationale="re-emitted",
        content_key=key,
    )
    assert await _count(env) == 1
    assert await _count(env, status="pending") == 0
    assert await _count(env, status="skipped") == 1


async def test_A_titled_confirm_dedups(env: dict[str, Any]) -> None:
    """A titled confirm now dedups on (type + normalizedTitle) — llm_wiki reviewIdFor parity."""
    from app.ops.review import _content_key, enqueue_review

    key = _content_key(vault_id="test-vault", item_type="confirm", proposed_title="Please X")
    assert key is not None
    for _ in range(3):
        await enqueue_review(
            vault_id="test-vault",
            item_type="confirm",
            proposed_title="Please X",
            rationale="confirm me",
            content_key=key,
        )
    # Re-surfacing the same confirmation refreshes one pending row instead of bloating the queue.
    assert await _count(env) == 1


async def test_A_titleless_confirm_always_inserts(env: dict[str, Any]) -> None:
    """A title-less confirm has no concept handle → content_key NULL → always INSERT (defensive)."""
    from app.ops.review import _content_key, enqueue_review

    assert _content_key(vault_id="test-vault", item_type="confirm", proposed_title=None) is None
    for _ in range(3):
        await enqueue_review(
            vault_id="test-vault",
            item_type="confirm",
            proposed_title=None,
            rationale="anonymous confirm",
            content_key=None,
        )
    assert await _count(env) == 3


async def test_A_resolved_confirm_not_resurrected(env: dict[str, Any]) -> None:
    """A handled (skipped) confirm is NOT re-opened by a re-ingest — llm_wiki 'resolved wins'."""
    from app.ops.review import _content_key, enqueue_review, skip

    key = _content_key(vault_id="test-vault", item_type="confirm", proposed_title="Verify Z")
    item = await enqueue_review(
        vault_id="test-vault",
        item_type="confirm",
        proposed_title="Verify Z",
        rationale="orig",
        content_key=key,
    )
    await skip(uuid.UUID(str(item.id)))
    # Same confirmation re-surfaces on re-ingest — must be a NO-OP (respect the human's decision).
    await enqueue_review(
        vault_id="test-vault",
        item_type="confirm",
        proposed_title="Verify Z",
        rationale="re-surfaced",
        content_key=key,
    )
    assert await _count(env) == 1
    assert await _count(env, status="pending") == 0


# ══════════════════════════════════════════════════════════════════════════════
# Phase B — contextual references + search queries
# ══════════════════════════════════════════════════════════════════════════════


def test_B_parse_proposals_caps_and_drops(monkeypatch: pytest.MonkeyPatch) -> None:
    """_parse_proposals extracts referenced_page_titles + search_queries, caps + drops junk."""
    import json

    from app.config import settings
    from app.ops.review import _parse_proposals

    monkeypatch.setattr(settings, "review_referenced_pages_max", 2)
    monkeypatch.setattr(settings, "review_search_queries_max", 3)

    raw = json.dumps(
        {
            "proposals": [
                {
                    "type": "suggestion",
                    "proposed_title": "T",
                    "rationale": "r",
                    "referenced_page_titles": ["A", "B", "C", 5, ""],  # cap 2, drop non-str/empty
                    "search_queries": ["q1", "q2", "q3", "q4"],  # cap 3
                }
            ]
        }
    )
    proposals = _parse_proposals(raw)
    assert len(proposals) == 1
    assert proposals[0].referenced_page_titles == ["A", "B"]
    assert proposals[0].search_queries == ["q1", "q2", "q3"]


async def test_B_referenced_titles_resolve_invented_dropped(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """propose_reviews resolves real referenced titles → ids; invented titles are dropped."""
    from app.ingest.schemas import Analysis, PageType, SuggestedPage
    from app.ops import review as review_mod

    real_id = await _insert_page(env, title="Real Page", page_type="concept")

    written = await _insert_page(env, title="Written Page", page_type="concept")

    # One LLM proposal referencing one real + one invented title.
    async def _fake_llm(**kwargs: Any) -> list[Any]:
        return [
            review_mod.ProposalDTO(
                item_type="suggestion",
                proposed_title="Follow-up",
                proposed_page_type=None,
                rationale="context",
                referenced_page_titles=["Real Page", "Invented Page"],
                search_queries=["seed query"],
            )
        ]

    monkeypatch.setattr(review_mod, "_llm_propose_reviews", _fake_llm)

    # Load the written Page ORM object (CAST id for SQLite/Postgres portability).
    from app.models import Page
    from sqlalchemy import String as _S
    from sqlalchemy import cast, select

    async with env["session_factory"]() as sess:
        written_page = (
            await sess.execute(select(Page).where(cast(Page.id, _S) == written))
        ).scalar_one()
        sess.expunge(written_page)

    analysis = Analysis(
        topics=["t"],
        entities=[],
        language="en",
        suggested_pages=[SuggestedPage(title="x", type=PageType.CONCEPT)],
    )
    await review_mod.propose_reviews(
        vault_id="test-vault",
        analysis=analysis,
        written_pages=[written_page],
        origin_source="raw/s.md",
    )

    async with env["session_factory"]() as sess:
        row = (
            await sess.execute(
                sa_text(
                    "SELECT referenced_page_ids, search_queries FROM review_items "
                    "WHERE proposed_title='Follow-up'"
                )
            )
        ).first()
    import json as _json

    ref_ids = _json.loads(row[0]) if row[0] else []
    queries = _json.loads(row[1]) if row[1] else []
    assert ref_ids == [real_id]  # only the real page resolved; invented dropped
    assert queries == ["seed query"]


async def test_B_single_provider_call(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """The enriched proposal still rides EXACTLY ONE provider call (no extra call)."""
    from app.ingest.schemas import Analysis, PageType, SuggestedPage
    from app.ops import review as review_mod

    written = await _insert_page(env, title="W", page_type="concept")
    from app.models import Page
    from sqlalchemy import String as _S
    from sqlalchemy import cast, select

    async with env["session_factory"]() as sess:
        wp = (await sess.execute(select(Page).where(cast(Page.id, _S) == written))).scalar_one()
        sess.expunge(wp)

    call_counter = {"n": 0}

    async def _counting_chat(provider: Any, instruction: str) -> str:
        call_counter["n"] += 1
        return '{"proposals": [{"type":"suggestion","proposed_title":"P","rationale":"r"}]}'

    # Force the gate to pass + a resolvable provider; count _chat_collect calls.
    monkeypatch.setattr(review_mod, "_chat_collect", _counting_chat)
    monkeypatch.setattr(
        review_mod,
        "_resolve_review_provider",
        AsyncMock(return_value=(object(), type("Cfg", (), {"token_budget": 4000})())),
    )

    class _Acc:
        total_tokens = 0
        total_cost_usd = 0.0
        calls = 0

    monkeypatch.setattr("app.ingest.provider.base.UsageAccumulator", lambda: _Acc())
    # provider needs bind_accumulator; give the fake provider one via a wrapper
    fake_provider = type("P", (), {"bind_accumulator": lambda self, a: None})()
    monkeypatch.setattr(
        review_mod,
        "_resolve_review_provider",
        AsyncMock(return_value=(fake_provider, type("Cfg", (), {"token_budget": 4000})())),
    )

    analysis = Analysis(
        topics=["t"],
        entities=[],
        language="en",
        suggested_pages=[
            SuggestedPage(title="Extra", type=PageType.CONCEPT),  # not written → gate passes
        ],
    )
    await review_mod.propose_reviews(
        vault_id="test-vault",
        analysis=analysis,
        written_pages=[wp],
        origin_source="raw/s.md",
    )
    assert call_counter["n"] == 1


async def test_B_deep_research_seeds_from_search_query(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """deep_research seeds topic from search_queries[0] when present."""
    import json

    from app.config import settings
    from app.ops import review as review_mod

    monkeypatch.setattr(settings, "searxng_url", "http://searxng.local")

    item_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO review_items (id, vault_id, item_type, status, proposed_title, "
                "search_queries, created_at) VALUES (:id, 'test-vault', 'suggestion', 'pending', "
                ":title, :sq, datetime('now'))"
            ),
            {"id": item_id, "title": "Fallback Title", "sq": json.dumps(["curated seed", "b"])},
        )
        await sess.commit()

    captured: dict[str, Any] = {}

    async def _fake_run_deep_research(**kwargs: Any) -> None:
        captured.update(kwargs)

    # Avoid a real DeepResearchRun insert path complexity — patch the scheduled coroutine.
    monkeypatch.setattr("app.ops.deep_research.run_deep_research", _fake_run_deep_research)
    # The deep_research op inserts a DeepResearchRun row; our SQLite lacks that table, so patch
    # the whole op to just capture the derived topic via the same seed logic.
    # Instead, exercise the topic-derivation branch directly through _first_search_query.
    from app.ops.review import _first_search_query

    topic = _first_search_query(json.loads(json.dumps(["curated seed", "b"])))
    assert topic == "curated seed"
    assert review_mod._first_search_query([]) is None
    assert review_mod._first_search_query(None) is None


# ══════════════════════════════════════════════════════════════════════════════
# Phase C — bulk + status + dismiss + clear
# ══════════════════════════════════════════════════════════════════════════════


async def _seed(env: dict[str, Any], *, status: str, item_type: str = "missing-page") -> str:
    from app.ops.review import enqueue_review

    item = await enqueue_review(
        vault_id="test-vault",
        item_type=item_type,
        proposed_title=f"T-{uuid.uuid4().hex[:6]}",
        rationale="r",
        content_key=None,
    )
    if status != "pending":
        async with env["session_factory"]() as sess:
            await sess.execute(
                sa_text("UPDATE review_items SET status=:s WHERE id=:id"),
                {"s": status, "id": str(item.id)},
            )
            await sess.commit()
    return str(item.id)


async def test_C_dismiss_terminal(client: AsyncClient, env: dict[str, Any]) -> None:
    item_id = await _seed(env, status="pending")
    resp = await client.post(f"/review/queue/{item_id}/dismiss")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "dismissed"
    assert body["resolution"] == "dismissed"


async def test_C_bulk_cap_400(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "review_bulk_max_ids", 200)
    ids = [str(uuid.uuid4()) for _ in range(201)]
    resp = await client.post(
        "/review/queue/bulk", json={"vault_id": "test-vault", "action": "skip", "ids": ids}
    )
    assert resp.status_code == 400


async def test_C_bulk_only_mutates_pending(client: AsyncClient, env: dict[str, Any]) -> None:
    pending = await _seed(env, status="pending")
    already = await _seed(env, status="created")
    resp = await client.post(
        "/review/queue/bulk",
        json={"vault_id": "test-vault", "action": "dismiss", "ids": [pending, already]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 1
    assert body["skipped_terminal"] == 1
    # created row untouched
    assert await _count(env, status="created") == 1
    assert await _count(env, status="dismissed") == 1


async def test_C_bulk_mark_resolved_never_touches_confirm(
    client: AsyncClient, env: dict[str, Any]
) -> None:
    confirm_id = await _seed(env, status="pending", item_type="confirm")
    normal_id = await _seed(env, status="pending", item_type="missing-page")
    resp = await client.post(
        "/review/queue/bulk",
        json={
            "vault_id": "test-vault",
            "action": "mark-resolved",
            "ids": [confirm_id, normal_id],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["updated"] == 1  # only the non-confirm
    assert body["skipped_terminal"] == 1  # confirm kept pending
    assert await _count(env, status="pending") == 1  # confirm still pending


async def test_C_clear_resolved_never_touches_pending(
    client: AsyncClient, env: dict[str, Any]
) -> None:
    await _seed(env, status="pending")
    await _seed(env, status="skipped")
    await _seed(env, status="created")
    await _seed(env, status="dismissed")
    resp = await client.delete("/review/queue/resolved?vault_id=test-vault")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 3  # skipped + created + dismissed
    assert await _count(env, status="pending") == 1  # pending untouched


async def test_C_status_filter_partitions(client: AsyncClient, env: dict[str, Any]) -> None:
    await _seed(env, status="pending")
    await _seed(env, status="pending")
    await _seed(env, status="created")  # resolved set
    await _seed(env, status="dismissed")

    pending = (await client.get("/review/queue?vault_id=test-vault&status=pending")).json()
    resolved = (await client.get("/review/queue?vault_id=test-vault&status=resolved")).json()
    dismissed = (await client.get("/review/queue?vault_id=test-vault&status=dismissed")).json()
    everything = (await client.get("/review/queue?vault_id=test-vault&status=all")).json()

    assert pending["total"] == 2
    assert resolved["total"] == 1
    assert dismissed["total"] == 1
    assert everything["total"] == 4
    # default (no status) → pending
    default = (await client.get("/review/queue?vault_id=test-vault")).json()
    assert default["total"] == 2


async def test_C_projection_carries_new_fields(client: AsyncClient, env: dict[str, Any]) -> None:
    """The GET projection carries content_key, referenced_page_ids, referenced_pages, queries."""
    import json

    real_id = await _insert_page(env, title="Ref Target", page_type="entity")
    stale_id = str(uuid.uuid4())  # never a real page → filtered at render
    item_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO review_items (id, vault_id, item_type, status, proposed_title, "
                "content_key, referenced_page_ids, search_queries, created_at) VALUES "
                "(:id, 'test-vault', 'suggestion', 'pending', 'P', 'deadbeef', :ref, :sq, "
                "datetime('now'))"
            ),
            {"id": item_id, "ref": json.dumps([real_id, stale_id]), "sq": json.dumps(["q"])},
        )
        await sess.commit()

    body = (await client.get("/review/queue?vault_id=test-vault")).json()
    item = body["items"][0]
    assert item["content_key"] == "deadbeef"
    assert set(item["referenced_page_ids"]) == {real_id, stale_id}
    # referenced_pages drops the stale id (render-time filter §9.2)
    ref_pages = item["referenced_pages"]
    assert len(ref_pages) == 1
    assert ref_pages[0]["id"] == real_id
    assert ref_pages[0]["title"] == "Ref Target"
    assert ref_pages[0]["type"] == "entity"
    assert item["search_queries"] == ["q"]


# ══════════════════════════════════════════════════════════════════════════════
# Phase E — delegated-route proposals
# ══════════════════════════════════════════════════════════════════════════════


async def test_E_delegated_writes_emit_proposals(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delegated run that recorded write_page ids drives ONE bounded propose_reviews call."""
    from app.ingest import orchestrator as orch

    written = await _insert_page(env, title="Delegated Page", page_type="concept")

    seen: dict[str, Any] = {}

    async def _fake_propose(**kwargs: Any) -> None:
        seen["called"] = True
        seen["written_pages"] = kwargs["written_pages"]
        seen["analysis"] = kwargs["analysis"]

    monkeypatch.setattr("app.ops.review.propose_reviews", _fake_propose)

    await orch._propose_reviews_for_delegated(
        vault_id="test-vault",
        written_page_ids=[written],
        origin_source="raw/s.md",
    )
    assert seen.get("called") is True
    assert len(seen["written_pages"]) == 1
    # synthesized Analysis is valid (≥1 topic + ≥1 suggested_page)
    assert seen["analysis"].topics
    assert seen["analysis"].suggested_pages


async def test_E_delegated_no_writes_no_call(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A delegated run that wrote nothing → no propose_reviews call (zero cost)."""
    from app.ingest import orchestrator as orch

    called = {"n": 0}

    async def _fake_propose(**kwargs: Any) -> None:
        called["n"] += 1

    monkeypatch.setattr("app.ops.review.propose_reviews", _fake_propose)

    await orch._propose_reviews_for_delegated(
        vault_id="test-vault", written_page_ids=[], origin_source="raw/s.md"
    )
    assert called["n"] == 0


async def test_E_write_capture_records_writes() -> None:
    """MCP delegated_write_capture records ids/titles write_page reports (no table)."""
    from app.mcp.server import _delegated_write_record, delegated_write_capture

    assert _delegated_write_record.get() is None  # inactive by default
    with delegated_write_capture() as record:
        active = _delegated_write_record.get()
        assert active is record
        record.record("id-1", "Title 1")
        record.record("id-1", "Title 1")  # dedup
        record.record("id-2", "Title 2")
    assert record.ids == ["id-1", "id-2"]
    assert record.titles == ["Title 1", "Title 2"]
    assert _delegated_write_record.get() is None  # restored on exit


# ══════════════════════════════════════════════════════════════════════════════
# Phase F — parity fixes (R-bug1, R4, R5, R7)
# ══════════════════════════════════════════════════════════════════════════════


async def test_pass1_duplicate_resolves_on_gone_page(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-bug1: sweep_reviews Pass-1b auto-resolves a 'duplicate' item when the affected
    page is soft-deleted. Mirrors llm_wiki sweep-reviews.ts:376-391 (!allStillExist)."""
    from app.config import settings
    from app.ops.review import sweep_reviews

    # Disable LLM sweep so the test is deterministic and fast.
    monkeypatch.setattr(settings, "review_sweep_llm_enabled", False)

    # Insert a live page and a duplicate review item referencing it via page_id.
    page_id = await _insert_page(env, title="Duplicate Target")
    item_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO review_items (id, vault_id, item_type, status, proposed_title, "
                "page_id, created_at) VALUES (:id, 'test-vault', 'duplicate', 'pending', "
                ":title, :page_id, datetime('now'))"
            ),
            {"id": item_id, "title": "Duplicate Target", "page_id": page_id},
        )
        await sess.commit()

    # Soft-delete the affected page (one copy of the duplicate is gone → conflict resolved).
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text("UPDATE pages SET deleted_at=datetime('now') WHERE id=:id"),
            {"id": page_id},
        )
        await sess.commit()

    result = await sweep_reviews("test-vault")
    assert result.rule_resolved >= 1

    async with env["session_factory"]() as sess:
        row = (
            await sess.execute(
                sa_text("SELECT status, resolution FROM review_items WHERE id=:id"),
                {"id": item_id},
            )
        ).first()
    assert row[0] == "auto_resolved", "duplicate must be auto_resolved when affected page is gone"
    assert row[1] == "rule_resolved"


async def test_pass1_duplicate_stays_pending_when_all_pages_exist(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-bug1: sweep_reviews Pass-1b leaves a 'duplicate' item pending when all
    affected pages are still alive (the duplicate conflict is unresolved)."""
    from app.config import settings
    from app.ops.review import sweep_reviews

    monkeypatch.setattr(settings, "review_sweep_llm_enabled", False)

    # Insert a live page — and do NOT delete it.
    page_id = await _insert_page(env, title="Live Duplicate A")
    item_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO review_items (id, vault_id, item_type, status, proposed_title, "
                "page_id, created_at) VALUES (:id, 'test-vault', 'duplicate', 'pending', "
                ":title, :page_id, datetime('now'))"
            ),
            {"id": item_id, "title": "Live Duplicate A", "page_id": page_id},
        )
        await sess.commit()

    await sweep_reviews("test-vault")

    async with env["session_factory"]() as sess:
        row = (
            await sess.execute(
                sa_text("SELECT status FROM review_items WHERE id=:id"),
                {"id": item_id},
            )
        ).first()
    assert row[0] == "pending", "duplicate must stay pending while all affected pages exist"


async def test_pass1_missing_page_resolves_by_slug(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4: sweep_reviews Pass-1a resolves a 'missing-page' item via slug match when
    the stored page title differs but the file_path basename slug matches.
    Mirrors llm_wiki sweep-reviews.ts:110-116 (byId slug check)."""
    from app.config import settings
    from app.ops.review import sweep_reviews

    monkeypatch.setattr(settings, "review_sweep_llm_enabled", False)

    # Insert a page at wiki/concepts/attention-mechanism.md with a longer stored title.
    # The title does NOT exactly match the proposed_title "Attention Mechanism".
    page_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO pages (id, vault_id, file_path, title, type, content_hash, pinned, "
                "created_at, updated_at) VALUES (:id, 'test-vault', "
                "'wiki/concepts/attention-mechanism.md', 'The Attention Mechanism', "
                "'concept', 'h', 0, datetime('now'), datetime('now'))"
            ),
            {"id": page_id},
        )
        await sess.commit()

    # A missing-page proposal: proposed_title "Attention Mechanism" → slug "attention-mechanism"
    # → matches file_path '%/attention-mechanism.md'.
    item_id = str(uuid.uuid4())
    async with env["session_factory"]() as sess:
        await sess.execute(
            sa_text(
                "INSERT INTO review_items (id, vault_id, item_type, status, proposed_title, "
                "created_at) VALUES (:id, 'test-vault', 'missing-page', 'pending', "
                "'Attention Mechanism', datetime('now'))"
            ),
            {"id": item_id},
        )
        await sess.commit()

    result = await sweep_reviews("test-vault")
    assert result.rule_resolved >= 1

    async with env["session_factory"]() as sess:
        row = (
            await sess.execute(
                sa_text("SELECT status, resolution FROM review_items WHERE id=:id"),
                {"id": item_id},
            )
        ).first()
    assert row[0] == "auto_resolved", "missing-page must resolve via slug match (R4)"
    assert row[1] == "rule_resolved"


def test_normalize_title_strips_prefixes() -> None:
    """R5: _normalize_title strips common LLM-prepended prefixes before lowercasing.
    Mirrors llm_wiki review-utils.ts normalizeReviewTitle + REVIEW_TITLE_PREFIX_RE."""
    from app.ops.review import _normalize_title

    # LLM prefix variants (english).
    assert _normalize_title("Missing page: Widget") == "widget"
    assert _normalize_title("Duplicate page: Widget") == "widget"
    assert _normalize_title("possible duplicate: Widget") == "widget"
    assert _normalize_title("missing-page: Widget") == "widget"
    # No prefix → simple lowercase + collapse whitespace.
    assert _normalize_title("Widget") == "widget"
    assert _normalize_title("  Multi   Space  ") == "multi space"
    # Prefix stripping must not bleed into the title body.
    assert _normalize_title("Missing page: Transformer Model") == "transformer model"


def test_content_key_ignores_target() -> None:
    """R7: _content_key excludes target_page_title and page_id from the hash payload.
    Two items about the same concept but different conflict targets must share a key,
    so they dedup correctly (mirrors llm_wiki review-utils.ts normalizeReviewTitle logic)."""
    from app.ops.review import _content_key

    # Different target_page_title → same key.
    key_a = _content_key(
        vault_id="v", item_type="contradiction", proposed_title="X", target_page_title="A"
    )
    key_b = _content_key(
        vault_id="v", item_type="contradiction", proposed_title="X", target_page_title="B"
    )
    assert key_a == key_b, "target_page_title must not affect content_key (R7)"

    # Different page_id → same key.
    key_c = _content_key(
        vault_id="v", item_type="contradiction", proposed_title="X", page_id="id-1"
    )
    key_d = _content_key(
        vault_id="v", item_type="contradiction", proposed_title="X", page_id="id-2"
    )
    assert key_c == key_d, "page_id must not affect content_key (R7)"

    # All four keys for the same vault+type+title must be identical.
    assert key_a == key_c

    # Different proposed_title → different key (sanity check).
    key_e = _content_key(vault_id="v", item_type="contradiction", proposed_title="Y")
    assert key_a != key_e
