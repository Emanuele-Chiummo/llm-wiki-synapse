"""
AC-F5-8 — retrieval_context passed to all 3 provider backends (ADR-0022 §2.7).

The story (S-F5-1) requires that, when the chat endpoint processes a user message,
the assembled RetrievalContext from F5 is passed to the active provider's
`chat(messages, retrieval_context)` call for ALL THREE backends:
  - OllamaProvider (local)
  - ApiProvider (api)
  - CliAgentProvider (cli)

This test builds a minimal chat_app-style fixture three times — once per provider type —
and, for each, asserts that the `retrieval_context` argument received by `provider.chat()`
is NOT None AND matches the assembled retrieval text from the mocked retrieve() call.

Infra-free: SQLite in-memory, all external calls mocked.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from app.rag.retrieval import Citation, PageRef, RetrievalContext
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


# ── Shared mock infrastructure ───────────────────────────────────────────────


class _RecordingProvider:
    """Mock InferenceProvider that records the retrieval_context it received."""

    def __init__(self, provider_type: str) -> None:
        self.provider_type = provider_type
        self.received_retrieval_context: str | None = None
        self._acc: Any = None

    def bind_accumulator(self, acc: Any) -> None:
        self._acc = acc

    async def chat(self, messages: list[Any], retrieval_context: str) -> AsyncIterator[str]:
        from app.ingest.schemas import Usage

        self.received_retrieval_context = retrieval_context
        yield "answer"
        if self._acc is not None:
            self._acc.add(Usage(input_tokens=5, output_tokens=3, total_cost_usd=0.0))


def _make_config_row(provider_type: str) -> Any:
    class _Row:
        pass

    row = _Row()
    row.provider_type = provider_type  # type: ignore[attr-defined]
    row.model_id = "test-model"  # type: ignore[attr-defined]
    row.base_url = None  # type: ignore[attr-defined]
    row.token_budget = 60_000  # type: ignore[attr-defined]
    row.timeout_seconds = 30.0  # type: ignore[attr-defined]
    row.is_fallback = False  # type: ignore[attr-defined]
    return row


_MOCK_RETRIEVAL_TEXT = "[1] Test Source\nMock passage about widgets.\n"
_MOCK_CITATION = Citation(
    n=1,
    ref=PageRef(id="00000000-0000-0000-0000-000000000099", title="Test Source", slug="test-source"),
    score=0.85,
    phase="vector",
)


@pytest.fixture()
async def provider_under_test() -> dict[str, Any]:
    """Holder so the fixture can inject a specific provider type via the test parameter."""
    return {"provider": None}


async def _make_chat_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_type: str,
) -> tuple[AsyncClient, _RecordingProvider]:
    """
    Build an isolated FastAPI + SQLite + mocked-provider environment for one chat turn.
    Returns (client, recording_provider) so the test can assert on received_retrieval_context.
    """
    from app import config as cfg

    vault_root = tmp_path / "vault"
    (vault_root / "wiki").mkdir(parents=True)
    (vault_root / "purpose.md").write_text("Test vault goal.", encoding="utf-8")
    monkeypatch.setattr(cfg.settings, "vault_id", "test-vault")
    monkeypatch.setattr(type(cfg.settings), "vault_root", property(lambda self: vault_root))

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
    monkeypatch.setattr("app.rag.retrieval.get_session", patched_get_session)
    monkeypatch.setattr("app.provider_config_service.get_session", patched_get_session)

    # Wire provider resolution to return the recording provider of the requested type.
    recording = _RecordingProvider(provider_type)
    config_row = _make_config_row(provider_type)

    async def fake_resolve_config(operation: str, vault_id: str | None = None, *, session: Any = None) -> Any:  # noqa: ARG001
        return config_row

    monkeypatch.setattr("app.chat.stream.resolve_provider_config", fake_resolve_config)
    monkeypatch.setattr("app.chat.stream.resolve_provider", lambda row: recording)  # type: ignore[arg-type]

    # Mock retrieve() so the test asserts on what reaches the provider (not on Qdrant).
    async def fake_retrieve(  # type: ignore[no-untyped-def]
        query: str, *, vault_id: str, context_window: int, **kwargs: Any
    ) -> RetrievalContext:
        return RetrievalContext(
            query=query,
            text=_MOCK_RETRIEVAL_TEXT,
            citations=[_MOCK_CITATION],
            token_budget=6_553,
            approx_tokens=len(_MOCK_RETRIEVAL_TEXT) // 4,
            data_version=0,
        )

    monkeypatch.setattr("app.chat.stream.retrieve", fake_retrieve)

    from app.main import app
    from fastapi import FastAPI

    @asynccontextmanager
    async def test_lifespan(app: FastAPI):  # type: ignore[override]
        yield

    app.router.lifespan_context = test_lifespan  # type: ignore[assignment]

    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    return client, recording


# ── AC-F5-8 tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", ["local", "api", "cli"])
async def test_ac_f5_8_all_providers_receive_retrieval_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_type: str,
) -> None:
    """
    AC-F5-8 — for each of the 3 provider backends, assert that provider.chat() receives a
    retrieval_context string that is NOT None AND contains the assembled retrieval text from
    retrieve() (ADR-0022 §2.7, I3 — retrieve() called once before streaming, not per-token).

    This test catches any regression where a provider path skips the retrieval injection
    (e.g., a per-type branch in run_chat_stream that forgets to pass the context).
    """
    client, recording = await _make_chat_client(tmp_path, monkeypatch, provider_type)
    async with client:
        resp = await client.post(
            "/chat/stream",
            json={"messages": [{"role": "user", "content": "tell me about widgets"}]},
        )

    assert resp.status_code == 200, (
        f"provider_type={provider_type!r}: expected 200, got {resp.status_code}: {resp.text}"
    )

    # The recording provider must have been called with a non-None retrieval_context.
    assert recording.received_retrieval_context is not None, (
        f"provider_type={provider_type!r}: provider.chat() was never called "
        f"(received_retrieval_context is None — retrieval context not injected)"
    )

    # The retrieval text from the mocked retrieve() must appear in the context passed to the provider.
    assert _MOCK_RETRIEVAL_TEXT in recording.received_retrieval_context, (
        f"provider_type={provider_type!r}: provider.chat() received retrieval_context that does NOT "
        f"contain the assembled retrieval text.\n"
        f"Expected substring: {_MOCK_RETRIEVAL_TEXT!r}\n"
        f"Received context (truncated): {recording.received_retrieval_context[:200]!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_type", ["local", "api", "cli"])
async def test_ac_f5_8_done_event_carries_citations_for_all_providers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider_type: str,
) -> None:
    """
    AC-F5-8 + ADR-0022 §2.4: the done NDJSON event must carry the citations field for all 3
    provider types (not just the local mock used in test_chat.py).
    """
    client, _ = await _make_chat_client(tmp_path, monkeypatch, provider_type)
    async with client:
        resp = await client.post(
            "/chat/stream",
            json={"messages": [{"role": "user", "content": "hi"}]},
        )

    assert resp.status_code == 200, f"provider_type={provider_type!r}: {resp.status_code}"
    events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
    done = next((e for e in events if e.get("type") == "done"), None)
    assert done is not None, f"provider_type={provider_type!r}: no 'done' event in stream"
    assert "citations" in done, (
        f"provider_type={provider_type!r}: done event missing 'citations' field (ADR-0022 §2.4)"
    )
    assert isinstance(done["citations"], list), (
        f"provider_type={provider_type!r}: done.citations is not a list"
    )
    assert len(done["citations"]) == 1, (
        f"provider_type={provider_type!r}: expected 1 citation in done event, "
        f"got {len(done['citations'])}"
    )
