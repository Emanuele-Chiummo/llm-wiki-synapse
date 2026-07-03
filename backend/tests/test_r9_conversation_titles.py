"""
UXB-1 — conversation auto-titles + list preview snippet ([F6][F16], UXA-02).

CI-safe (no live infra): an in-memory SQLite DB with the chat tables + a MOCKED
InferenceProvider. Covers:

  1. Title generated after the FIRST completed assistant turn (mock provider).
  2. Title NOT regenerated on the second exchange.
  3. Provider failure → existing title kept (fallback intact).
  4. Bounded params: single provider.chat() call; ~60-token output cap enforced.
  5. GET /conversations preview present + stripped of <think> tags.

The bounded / no-retry contract (AC-UXB1-4) is asserted by counting chat() calls and by
feeding an over-long stream and asserting the collected title respects the char cap.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

# ── Mocks ──────────────────────────────────────────────────────────────────────


class _MockConfigRow:
    provider_type = "local"
    model_id = "qwen2.5:3b"
    base_url = None
    token_budget = 60000
    is_fallback = False


class _CountingProvider:
    """Deterministic chat() provider that counts calls (to assert single-call / no-retry)."""

    def __init__(self, deltas: list[str], *, fail: bool = False) -> None:
        self._deltas = deltas
        self._fail = fail
        self.calls = 0
        self._acc: Any = None

    def bind_accumulator(self, acc: Any) -> None:
        self._acc = acc

    async def chat(
        self, messages: list[Any], retrieval_context: str
    ) -> AsyncIterator[str]:
        from app.ingest.schemas import Usage

        self.calls += 1
        if self._fail:
            raise RuntimeError("provider down")
        for d in self._deltas:
            yield d
        if self._acc is not None:
            self._acc.add(Usage(input_tokens=8, output_tokens=4, total_cost_usd=0.0))


# ── Fixture: in-memory DB + patched provider/session seams ──────────────────────


@pytest.fixture()
async def title_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[dict[str, Any]]:
    from app import config as cfg

    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")

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

    # Provider resolution seam used by autotitle (I6) — never touches the network.
    provider_holder: dict[str, _CountingProvider] = {
        "provider": _CountingProvider(["My Great ", "Title"])
    }

    async def fake_resolve_config(operation, vault_id=None, *, session=None):  # type: ignore[no-untyped-def]
        return _MockConfigRow()

    def fake_resolve_provider(row):  # type: ignore[no-untyped-def]
        return provider_holder["provider"]

    monkeypatch.setattr("app.chat.autotitle.resolve_provider_config", fake_resolve_config)
    monkeypatch.setattr("app.chat.autotitle.resolve_provider", fake_resolve_provider)

    yield {
        "session_factory": session_factory,
        "provider_holder": provider_holder,
        "Conversation": Conversation,
        "ChatMessage": ChatMessage,
    }


async def _seed_conversation(
    env: dict[str, Any],
    *,
    title: str | None,
    user_msg: str,
    assistant_msgs: list[str],
) -> uuid.UUID:
    """Insert one conversation with a first user message + N assistant messages."""
    Conversation = env["Conversation"]
    ChatMessage = env["ChatMessage"]
    async with env["session_factory"]() as sess:
        conv = Conversation(vault_id="test-vault", title=title)
        sess.add(conv)
        await sess.flush()
        base = datetime.now(UTC)
        sess.add(
            ChatMessage(
                conversation_id=conv.id,
                role="user",
                content=user_msg,
                citations=[],
                created_at=base,
            )
        )
        for i, a in enumerate(assistant_msgs, start=1):
            sess.add(
                ChatMessage(
                    conversation_id=conv.id,
                    role="assistant",
                    content=a,
                    citations=[],
                    created_at=base + timedelta(seconds=i),
                )
            )
        await sess.commit()
        return conv.id


async def _get_title(env: dict[str, Any], conv_id: uuid.UUID) -> str | None:
    Conversation = env["Conversation"]
    async with env["session_factory"]() as sess:
        from sqlalchemy import select

        row = await sess.execute(select(Conversation.title).where(Conversation.id == conv_id))
        return row.scalar_one()


# ── 1. Title generated after first exchange ─────────────────────────────────────


async def test_title_generated_after_first_exchange(title_env: dict[str, Any]) -> None:
    from app.chat.autotitle import maybe_generate_conversation_title

    # Default title = raw first-message-derived (implicit-create path) → eligible.
    first = "How do I configure the Synapse ingest provider?"
    conv_id = await _seed_conversation(
        title_env, title=first[:80], user_msg=first, assistant_msgs=["Here is how..."]
    )

    await maybe_generate_conversation_title(conv_id, "test-vault")

    new_title = await _get_title(title_env, conv_id)
    assert new_title == "My Great Title"
    assert new_title is not None and len(new_title) <= 60
    # Single provider call (AC-UXB1-4: no retry).
    assert title_env["provider_holder"]["provider"].calls == 1


async def test_title_generated_when_title_is_none(title_env: dict[str, Any]) -> None:
    from app.chat.autotitle import maybe_generate_conversation_title

    conv_id = await _seed_conversation(
        title_env, title=None, user_msg="What is bge-m3?", assistant_msgs=["An embedding model."]
    )
    await maybe_generate_conversation_title(conv_id, "test-vault")
    assert await _get_title(title_env, conv_id) == "My Great Title"


# ── 2. NOT regenerated on second exchange ───────────────────────────────────────


async def test_not_regenerated_on_second_exchange(title_env: dict[str, Any]) -> None:
    from app.chat.autotitle import maybe_generate_conversation_title

    # Already auto-titled + two assistant turns → guard must no-op (no second call).
    conv_id = await _seed_conversation(
        title_env,
        title="My Great Title",
        user_msg="First question",
        assistant_msgs=["answer one", "answer two"],
    )
    await maybe_generate_conversation_title(conv_id, "test-vault")

    assert await _get_title(title_env, conv_id) == "My Great Title"
    assert title_env["provider_holder"]["provider"].calls == 0


async def test_user_edited_title_never_overwritten(title_env: dict[str, Any]) -> None:
    from app.chat.autotitle import maybe_generate_conversation_title

    # A user-set title (R7-3) on the first exchange must be preserved (not default).
    conv_id = await _seed_conversation(
        title_env,
        title="My Custom Name",
        user_msg="anything",
        assistant_msgs=["reply"],
    )
    await maybe_generate_conversation_title(conv_id, "test-vault")

    assert await _get_title(title_env, conv_id) == "My Custom Name"
    assert title_env["provider_holder"]["provider"].calls == 0


# ── 3. Provider failure → fallback intact ───────────────────────────────────────


async def test_provider_failure_keeps_existing_title(title_env: dict[str, Any]) -> None:
    from app.chat.autotitle import maybe_generate_conversation_title

    title_env["provider_holder"]["provider"] = _CountingProvider([], fail=True)
    original = "How do I configure ingest?"
    conv_id = await _seed_conversation(
        title_env, title=original[:80], user_msg=original, assistant_msgs=["ok"]
    )

    # Must not raise (fire-and-forget) and must not retry (single call).
    await maybe_generate_conversation_title(conv_id, "test-vault")

    kept = await _get_title(title_env, conv_id)
    # Existing (non-empty) title is preserved on failure.
    assert kept == original[:80]
    assert title_env["provider_holder"]["provider"].calls == 1


async def test_provider_failure_with_no_title_gets_timestamp(title_env: dict[str, Any]) -> None:
    from app.chat.autotitle import maybe_generate_conversation_title

    title_env["provider_holder"]["provider"] = _CountingProvider([], fail=True)
    conv_id = await _seed_conversation(
        title_env, title=None, user_msg="hello", assistant_msgs=["hi"]
    )
    await maybe_generate_conversation_title(conv_id, "test-vault")

    kept = await _get_title(title_env, conv_id)
    assert kept is not None and kept.startswith("Chat ")


# ── 4. Bounded params (~60 token output cap + single call) ───────────────────────


async def test_output_capped_at_60_tokens(title_env: dict[str, Any]) -> None:
    from app.chat.autotitle import _MAX_TITLE_OUTPUT_CHARS, _generate_title_text

    # Stream far more than the cap; the call site must stop consuming and truncate.
    huge = ["word " * 200]  # ~1000 chars, well over the ~240-char (60-token) cap
    provider = _CountingProvider(huge)
    title = await _generate_title_text(provider, "some first message")

    # Collected text is capped at the ~60-token budget before title trimming.
    assert _MAX_TITLE_OUTPUT_CHARS == 240
    # Final stored title is short by contract (≤ 60 chars).
    assert len(title) <= 60
    assert provider.calls == 1


def test_is_default_title_recognises_patterns() -> None:
    from app.chat.autotitle import is_default_title

    assert is_default_title(None, "hi") is True
    assert is_default_title("", "hi") is True
    # Raw first-message-derived default (stream.py implicit-create path).
    first = "How do I configure the Synapse ingest provider today?"
    assert is_default_title(first[:80], first) is True
    # Our own timestamp fallback is upgradeable.
    assert is_default_title("Chat 2026-07-03 14:30", "hi") is True
    # A user-edited title is NOT default.
    assert is_default_title("My Custom Name", "hi") is False


# ── 5. GET /conversations preview present + stripped of <think> ──────────────────


@pytest.fixture()
async def list_client(
    title_env: dict[str, Any],
) -> AsyncIterator[AsyncClient]:
    from app.main import app
    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):  # type: ignore[override]
        yield

    app.router.lifespan_context = test_lifespan  # type: ignore[assignment]
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_preview_present_and_strips_think(
    title_env: dict[str, Any], list_client: AsyncClient
) -> None:
    # Last message contains <think> reasoning + markdown that must be stripped.
    conv_id = await _seed_conversation(
        title_env,
        title="A Title",
        user_msg="question",
        assistant_msgs=["<think>secret reasoning</think>The **answer** is 42."],
    )

    r = await list_client.get("/conversations", params={"vault_id": "test-vault"})
    assert r.status_code == 200
    items = {i["id"]: i for i in r.json()["items"]}
    item = items[str(conv_id)]

    assert "preview" in item
    preview = item["preview"]
    assert preview is not None
    assert "secret reasoning" not in preview
    assert "<think>" not in preview
    assert "**" not in preview
    assert preview.startswith("The answer is 42")


async def test_preview_none_when_no_messages(
    title_env: dict[str, Any], list_client: AsyncClient
) -> None:
    Conversation = title_env["Conversation"]
    async with title_env["session_factory"]() as sess:
        conv = Conversation(vault_id="test-vault", title="Empty")
        sess.add(conv)
        await sess.commit()
        cid = conv.id

    r = await list_client.get("/conversations", params={"vault_id": "test-vault"})
    items = {i["id"]: i for i in r.json()["items"]}
    assert items[str(cid)]["preview"] is None


async def test_preview_capped_at_80_chars(
    title_env: dict[str, Any], list_client: AsyncClient
) -> None:
    long_body = "x" * 500
    conv_id = await _seed_conversation(
        title_env, title="Long", user_msg="q", assistant_msgs=[long_body]
    )
    r = await list_client.get("/conversations", params={"vault_id": "test-vault"})
    items = {i["id"]: i for i in r.json()["items"]}
    assert len(items[str(conv_id)]["preview"]) <= 80
