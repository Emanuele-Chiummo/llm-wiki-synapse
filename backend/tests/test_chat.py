"""
Chat backend tests (F6/F7, M4 Phase 3, ADR-0019).

Three layers, all CI-safe (no live infra):
  1. ThinkScanner / split_think — the streaming-safe <think> span splitter (§2.4), pure logic.
  2. build_chat_context — light system context builder (§2.3), pure filesystem.
  3. /conversations CRUD + /chat/stream — FastAPI endpoints against an in-memory SQLite DB with
     a MOCKED InferenceProvider (deterministic NDJSON), asserting the frozen event schema (§2.2)
     incl. token/think split, the done/cost event (I7), and persistence.

The live Ollama/qwen2.5:3b path is verified manually (ADR-0019 §1 dev path) — see the engineer
report; it is NOT asserted here (kept deterministic for CI).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from app.chat.context import build_chat_context
from app.chat.think import ThinkScanner, split_think
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── 1. ThinkScanner (§2.4) ────────────────────────────────────────────────────


def _run_scanner(chunks: list[str]) -> list[tuple[str, str]]:
    scanner = ThinkScanner()
    out: list[tuple[str, str]] = []
    for c in chunks:
        out.extend(scanner.feed(c))
    out.extend(scanner.flush())
    return out


class TestThinkScanner:
    def test_plain_text_all_tokens(self) -> None:
        assert _run_scanner(["hello ", "world"]) == [("token", "hello "), ("token", "world")]

    def test_single_chunk_with_think(self) -> None:
        events = _run_scanner(["before<think>reasoning</think>after"])
        assert ("token", "before") in events
        assert ("think", "reasoning") in events
        assert ("token", "after") in events
        # visible text excludes reasoning
        visible = "".join(t for k, t in events if k == "token")
        assert visible == "beforeafter"

    def test_tag_split_across_chunks_open(self) -> None:
        # '<think>' split as '<thi' | 'nk>' must NOT leak as a visible token (Do-NOT safety).
        events = _run_scanner(["vis<thi", "nk>secret</think>done"])
        visible = "".join(t for k, t in events if k == "token")
        think = "".join(t for k, t in events if k == "think")
        assert "<thi" not in visible and "nk>" not in visible
        assert visible == "visdone"
        assert think == "secret"

    def test_close_tag_split_across_chunks(self) -> None:
        events = _run_scanner(["<think>abc</thi", "nk>tail"])
        visible = "".join(t for k, t in events if k == "token")
        think = "".join(t for k, t in events if k == "think")
        assert think == "abc"
        assert visible == "tail"

    def test_per_char_streaming_equivalent(self) -> None:
        raw = "A<think>B</think>C<think>D</think>E"
        per_char = _run_scanner(list(raw))
        visible = "".join(t for k, t in per_char if k == "token")
        think = "".join(t for k, t in per_char if k == "think")
        assert visible == "ACE"
        assert think == "BD"

    def test_split_think_pure_rederivation(self) -> None:
        raw = "ans<think>cot</think>more"
        visible, segments = split_think(raw)
        assert visible == "ansmore"
        assert ("think", "cot") in segments

    def test_unterminated_think_flushes(self) -> None:
        # An unclosed <think> at stream end: held buffer is flushed as think (no data loss).
        events = _run_scanner(["x<think>still thinking"])
        visible = "".join(t for k, t in events if k == "token")
        think = "".join(t for k, t in events if k == "think")
        assert visible == "x"
        assert think == "still thinking"


# ── 2. build_chat_context (§2.3) ──────────────────────────────────────────────


class TestChatContext:
    def test_includes_purpose_and_overview(self, tmp_path: Path) -> None:
        (tmp_path / "purpose.md").write_text("VAULT GOAL HERE", encoding="utf-8")
        wiki = tmp_path / "wiki"
        wiki.mkdir()
        (wiki / "overview.md").write_text("OVERVIEW HERE", encoding="utf-8")
        ctx = build_chat_context(vault_root=tmp_path, context_window=32_768)
        assert "VAULT GOAL HERE" in ctx
        assert "OVERVIEW HERE" in ctx

    def test_missing_files_still_returns_preamble(self, tmp_path: Path) -> None:
        ctx = build_chat_context(vault_root=tmp_path)
        assert "Synapse" in ctx  # preamble present even with no files

    def test_budget_caps_large_purpose(self, tmp_path: Path) -> None:
        (tmp_path / "purpose.md").write_text("x" * 1_000_000, encoding="utf-8")
        ctx = build_chat_context(vault_root=tmp_path, context_window=4096)
        # 20% of 4096 tokens * 4 chars ≈ 3276 chars cap — far below 1M.
        assert len(ctx) < 10_000

    def test_preamble_allows_page_path_mentions(self, tmp_path: Path) -> None:
        """CG-A1: the softened preamble lets the model name page paths in prose, keeping [n]."""
        ctx = build_chat_context(vault_root=tmp_path)
        low = ctx.lower()
        # Bare-number example still present (the resolvable anchor).
        assert "[1]" in ctx
        # Naming the relevant page path in prose is now explicitly permitted.
        assert "path" in low and "prose" in low
        # …but the marker itself must stay a bare number.
        assert "bare number" in low


# ── 3. Endpoint tests with a mocked provider ──────────────────────────────────


class _MockProvider:
    """Deterministic InferenceProvider stand-in: yields fixed deltas + records usage."""

    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas
        self._acc: Any = None

    def bind_accumulator(self, acc: Any) -> None:
        self._acc = acc

    async def chat(self, messages: list[Any], retrieval_context: str) -> AsyncIterator[str]:
        from app.ingest.schemas import Usage

        for d in self._deltas:
            yield d
        if self._acc is not None:
            self._acc.add(Usage(input_tokens=12, output_tokens=7, total_cost_usd=0.0))


class _MockConfigRow:
    provider_type = "local"
    model_id = "qwen2.5:3b"
    base_url = None
    token_budget = 60000
    is_fallback = False


@pytest.fixture()
async def chat_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """FastAPI app wired to in-memory SQLite chat tables + a mocked provider."""
    from app import config as cfg

    vault_root = tmp_path / "vault"
    (vault_root / "wiki").mkdir(parents=True)
    (vault_root / "purpose.md").write_text("Test vault goal.", encoding="utf-8")
    (vault_root / "wiki" / "overview.md").write_text("Test overview.", encoding="utf-8")
    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))

    # In-memory SQLite with only the chat tables (created from the real ORM models).
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from app.models import Base, ChatMessage, Conversation

    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all,
            tables=[Conversation.__table__, ChatMessage.__table__],
        )

    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )

    @asynccontextmanager
    async def patched_get_session():  # type: ignore[return]
        async with session_factory() as sess:
            try:
                yield sess
                await sess.commit()
            except Exception:
                await sess.rollback()
                raise

    monkeypatch.setattr("app.db.get_session", patched_get_session)
    monkeypatch.setattr("app.main.get_session", patched_get_session)
    # Patch all other modules that import get_session via their own module reference
    # (ADR-0022 test-isolation rule: retrieval + provider_config_service have their own refs).
    monkeypatch.setattr("app.rag.retrieval.get_session", patched_get_session)
    monkeypatch.setattr("app.provider_config_service.get_session", patched_get_session)

    # Mock provider resolution (I6 surface) — never touches the network.
    async def fake_resolve_config(operation, vault_id=None, *, session=None):  # type: ignore[no-untyped-def]
        return _MockConfigRow()

    deltas_holder: dict[str, list[str]] = {"deltas": ["Hello", ", world", "!"]}

    def fake_resolve_provider(row):  # type: ignore[no-untyped-def]
        return _MockProvider(deltas_holder["deltas"])

    monkeypatch.setattr("app.chat.stream.resolve_provider_config", fake_resolve_config)
    monkeypatch.setattr("app.chat.stream.resolve_provider", fake_resolve_provider)

    # Neutralise the fire-and-forget auto-title task (UXB-1): on the StaticPool-shared
    # SQLite connection its rollback can land inside a LATER request's transaction and
    # wipe uncommitted rows (CI-only flake in test_regenerate_replaces_last_assistant).
    # Auto-titling has its own awaited suite: test_r9_conversation_titles.py.
    async def _noop_autotitle(conversation_id, vault_id):  # type: ignore[no-untyped-def]
        return None

    monkeypatch.setattr("app.chat.autotitle.maybe_generate_conversation_title", _noop_autotitle)

    # Mock retrieve() so chat tests do not need Qdrant or Postgres (ADR-0022 §2.7 isolation).
    # The mock returns a RetrievalContext with one citation so citation-stamping tests work.
    from app.rag.retrieval import Citation, PageRef, RetrievalContext

    _mock_citations_holder: dict[str, list[Citation]] = {
        "citations": [
            Citation(
                n=1,
                ref=PageRef(
                    id="00000000-0000-0000-0000-000000000001",
                    title="Mock Source",
                    slug="mock-source",
                ),
                score=0.9,
                phase="vector",
            )
        ]
    }

    async def fake_retrieve(  # type: ignore[no-untyped-def]
        query: str, *, vault_id: str, context_window: int, **kwargs: Any
    ) -> RetrievalContext:
        cits = _mock_citations_holder["citations"]
        text = "".join(f"[{c.n}] {c.ref.title}\nMock passage.\n" for c in cits)
        return RetrievalContext(
            query=query,
            text=text,
            citations=cits,
            token_budget=6553,
            approx_tokens=len(text) // 4,
            data_version=0,
        )

    monkeypatch.setattr("app.chat.stream.retrieve", fake_retrieve)

    from app.main import app
    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):  # type: ignore[override]
        yield

    app.router.lifespan_context = test_lifespan  # type: ignore[assignment]
    return {"app": app, "deltas": deltas_holder, "citations": _mock_citations_holder}


@pytest.fixture()
async def client(chat_app: dict[str, Any]) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(
        transport=ASGITransport(app=chat_app["app"]), base_url="http://test"
    ) as c:
        yield c


def _parse_ndjson(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


class TestConversationCrud:
    async def test_create_and_list(self, client: AsyncClient) -> None:
        r = await client.post("/conversations", json={"title": "First"})
        assert r.status_code == 201
        cid = r.json()["id"]
        assert r.json()["title"] == "First"

        r2 = await client.get("/conversations")
        assert r2.status_code == 200
        ids = [c["id"] for c in r2.json()["items"]]
        assert cid in ids

    async def test_messages_404_unknown(self, client: AsyncClient) -> None:
        r = await client.get(f"/conversations/{uuid.uuid4()}/messages")
        assert r.status_code == 404

    async def test_soft_delete(self, client: AsyncClient) -> None:
        cid = (await client.post("/conversations", json={})).json()["id"]
        r = await client.delete(f"/conversations/{cid}")
        assert r.status_code == 204
        # now excluded from list
        listed = (await client.get("/conversations")).json()["items"]
        assert cid not in [c["id"] for c in listed]
        # second delete → 404
        assert (await client.delete(f"/conversations/{cid}")).status_code == 404


class TestChatStream:
    async def test_stream_tokens_and_done(self, client: AsyncClient) -> None:
        r = await client.post(
            "/chat/stream",
            json={"conversation_id": None, "messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/x-ndjson")
        events = _parse_ndjson(r.text)

        tokens = [e["delta"] for e in events if e["type"] == "token"]
        assert "".join(tokens) == "Hello, world!"

        done = [e for e in events if e["type"] == "done"]
        assert len(done) == 1
        d = done[0]
        assert d["input_tokens"] == 12
        assert d["output_tokens"] == 7
        assert d["total_cost_usd"] == 0.0  # local (I7 / ADR-0009)
        assert d["finish_reason"] == "stop"
        assert d["iterations_used"] == 1
        conv_id = d["conversation_id"]

        # persistence: user + assistant messages saved
        msgs = (await client.get(f"/conversations/{conv_id}/messages")).json()["items"]
        roles = [m["role"] for m in msgs]
        assert roles == ["user", "assistant"]
        assert msgs[1]["content"] == "Hello, world!"
        assert msgs[1]["output_tokens"] == 7

    async def test_think_split_in_stream(
        self, client: AsyncClient, chat_app: dict[str, Any]
    ) -> None:
        chat_app["deltas"]["deltas"] = ["ans ", "<think>", "cot", "</think>", "done"]
        r = await client.post(
            "/chat/stream",
            json={"messages": [{"role": "user", "content": "q"}]},
        )
        events = _parse_ndjson(r.text)
        visible = "".join(e["delta"] for e in events if e["type"] == "token")
        think = "".join(e["delta"] for e in events if e["type"] == "think")
        assert visible == "ans done"
        assert think == "cot"

        # RAW content persisted incl. literal <think> (AC-F7-2 / Do-NOT #7)
        conv_id = next(e["conversation_id"] for e in events if e["type"] == "done")
        msgs = (await client.get(f"/conversations/{conv_id}/messages")).json()["items"]
        assert "<think>cot</think>" in msgs[1]["content"]

    async def test_unknown_conversation_404(self, client: AsyncClient) -> None:
        r = await client.post(
            "/chat/stream",
            json={
                "conversation_id": str(uuid.uuid4()),
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert r.status_code == 404

    async def test_no_provider_503(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.provider_config_service import ConfigNotFoundError

        async def boom(operation, vault_id=None, *, session=None):  # type: ignore[no-untyped-def]
            raise ConfigNotFoundError("no chat row")

        monkeypatch.setattr("app.chat.stream.resolve_provider_config", boom)
        r = await client.post(
            "/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}
        )
        assert r.status_code == 503

    async def test_regenerate_replaces_last_assistant(self, client: AsyncClient) -> None:
        # first turn
        r1 = await client.post(
            "/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}
        )
        conv_id = next(e["conversation_id"] for e in _parse_ndjson(r1.text) if e["type"] == "done")
        # regenerate: same conversation, regenerate flag → old assistant deleted, new streamed
        r2 = await client.post(
            "/chat/stream",
            json={
                "conversation_id": conv_id,
                "messages": [{"role": "user", "content": "hi again"}],
                "regenerate": True,
            },
        )
        assert r2.status_code == 200
        msgs = (await client.get(f"/conversations/{conv_id}/messages")).json()["items"]
        # exactly one assistant message remains (the regenerated one)
        assert [m["role"] for m in msgs].count("assistant") == 1


# ── 4. Citation integration tests (ADR-0022 §2.4, AC-F6-3) ───────────────────


class TestChatCitations:
    """
    AC-F6-3 (carry-forward from M4): citations stored in DB + returned in done event.
    The mock retrieve() in chat_app returns one Citation (n=1, "Mock Source").
    """

    async def test_citations_stored_in_assistant_message(
        self, client: AsyncClient, chat_app: dict[str, Any]
    ) -> None:
        """Citations are written to messages.citations JSONB column (ADR-0022 §2.4)."""
        r = await client.post(
            "/chat/stream",
            json={"messages": [{"role": "user", "content": "tell me about the vault"}]},
        )
        assert r.status_code == 200
        events = _parse_ndjson(r.text)
        done = next(e for e in events if e["type"] == "done")
        conv_id = done["conversation_id"]

        msgs = (await client.get(f"/conversations/{conv_id}/messages")).json()["items"]
        assistant = next(m for m in msgs if m["role"] == "assistant")

        # The citations column is returned as a list with the one mock citation.
        cits = assistant.get("citations") or []
        assert len(cits) == 1, f"Expected 1 citation stored in DB, got {len(cits)}: {cits}"
        assert cits[0]["n"] == 1
        assert cits[0]["id"] == "00000000-0000-0000-0000-000000000001"
        assert cits[0]["title"] == "Mock Source"
        assert cits[0]["slug"] == "mock-source"
        # score and phase are stored (not just the compact projection)
        assert "score" in cits[0]
        assert "phase" in cits[0]

    async def test_done_event_has_citations_field(
        self, client: AsyncClient, chat_app: dict[str, Any]
    ) -> None:
        """done event gains an additive compact citations field (ADR-0022 §2.4)."""
        r = await client.post(
            "/chat/stream",
            json={"messages": [{"role": "user", "content": "test"}]},
        )
        assert r.status_code == 200
        events = _parse_ndjson(r.text)
        done = next(e for e in events if e["type"] == "done")

        # Additive field — must be present and be a list.
        assert (
            "citations" in done
        ), f"done event must have 'citations' field; got keys: {list(done)}"
        cits = done["citations"]
        assert isinstance(cits, list), f"citations must be a list; got {type(cits)}"
        assert len(cits) == 1
        c = cits[0]
        # Compact projection: n, id, title, slug (ADR-0022 §2.4 — score/phase NOT streamed)
        assert c["n"] == 1
        assert c["id"] == "00000000-0000-0000-0000-000000000001"
        assert c["title"] == "Mock Source"
        assert c["slug"] == "mock-source"
        assert "score" not in c, "score must NOT be in streamed citation (stored only, §2.4)"
        assert "phase" not in c, "phase must NOT be in streamed citation (stored only, §2.4)"

    async def test_done_event_still_has_all_existing_fields(
        self, client: AsyncClient, chat_app: dict[str, Any]
    ) -> None:
        """citations is ADDITIVE — all pre-existing done fields must still be present."""
        r = await client.post(
            "/chat/stream",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        events = _parse_ndjson(r.text)
        done = next(e for e in events if e["type"] == "done")

        # All M4 done fields must remain.
        for field in (
            "conversation_id",
            "message_id",
            "input_tokens",
            "output_tokens",
            "total_cost_usd",
            "iterations_used",
            "finish_reason",
        ):
            assert (
                field in done
            ), f"done event missing pre-existing field '{field}' after citations added"

    async def test_no_citations_when_retrieve_returns_empty(
        self, client: AsyncClient, chat_app: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When retrieve() returns no citations, done.citations == [] (empty retrieval graceful)."""
        from app.rag.retrieval import RetrievalContext

        async def empty_retrieve(  # type: ignore[no-untyped-def]
            query: str, *, vault_id: str, context_window: int, **kwargs: Any
        ) -> RetrievalContext:
            return RetrievalContext(
                query=query,
                text="",
                citations=[],
                token_budget=6553,
                approx_tokens=0,
                data_version=0,
            )

        monkeypatch.setattr("app.chat.stream.retrieve", empty_retrieve)

        r = await client.post(
            "/chat/stream",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        assert r.status_code == 200
        events = _parse_ndjson(r.text)
        done = next(e for e in events if e["type"] == "done")
        assert done.get("citations") == [], f"No citations expected; got {done.get('citations')}"
