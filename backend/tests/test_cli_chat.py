"""
CliAgentProvider.chat() tests (S-F17-1, AC-F17-CHAT-1..3 / ADR-0022 §2.7).

The CLI provider's chat() delegates a READ-ONLY streaming chat to the claude-agent-sdk
(mirroring delegate_ingest) and must:
  - AC-F17-CHAT-1: not raise NotImplementedError; inject retrieval_context as the SDK
    system_prompt; be bounded (here: CHAT_AGENT_MAX_TURNS → SDK max_turns);
  - AC-F17-CHAT-2: yield text deltas in the SAME shape OllamaProvider/ApiProvider use
    (chat() is an awaitable returning an async generator of str), so chat/stream.py needs
    no special-casing;
  - AC-F17-CHAT-3: record cost per NB-4 — real SDK cost when present, else Decimal("0.00")
    convention ($0.00 float) with a WARNING, never raising when SDK metadata is absent.

Infra-free: the claude-agent-sdk is faked via sys.modules, so these run in CI without the SDK
installed and without any network call. No DB session is opened by cli.py (chat() records Usage
out of band via the bound UsageAccumulator), so no get_session patching is needed here.
"""

from __future__ import annotations

import inspect
import logging
import sys
import types
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.ingest.provider.base import UsageAccumulator
from app.ingest.provider.cli import CliAgentProvider, _build_chat_prompt
from app.ingest.provider.config import ProviderSettings
from app.ingest.schemas import Message

# ── Fake SDK message shapes ──────────────────────────────────────────────────────


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeAssistantMessage:
    """An assistant message carrying text block(s) + token usage, but no cost."""

    def __init__(self, *texts: str, input_tokens: int = 120, output_tokens: int = 80) -> None:
        self.content = [_FakeTextBlock(t) for t in texts]
        self.usage = _FakeUsage(input_tokens=input_tokens, output_tokens=output_tokens)


class _FakeResultMessage:
    """The terminal ResultMessage carrying the run's cumulative total_cost_usd."""

    def __init__(self, total_cost_usd: float | None) -> None:
        self.total_cost_usd = total_cost_usd
        self.content: list[Any] = []  # no text blocks


# ── Fake SDK installed into sys.modules ──────────────────────────────────────────


class _Recorder:
    """Captures the options the provider built so tests can assert on them."""

    def __init__(self) -> None:
        self.options: dict[str, Any] | None = None
        self.prompt: str | None = None


def _install_fake_sdk(
    monkeypatch: pytest.MonkeyPatch,
    response_messages: list[Any],
    recorder: _Recorder,
) -> None:
    """Install a fake `claude_agent_sdk` exposing the two symbols cli.py imports."""

    class _FakeClient:
        def __init__(self, options: Any) -> None:
            recorder.options = options

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def query(self, prompt: str) -> None:
            recorder.prompt = prompt

        async def receive_response(self):  # type: ignore[no-untyped-def]
            for msg in response_messages:
                yield msg

    def _fake_options(**kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    fake = types.ModuleType("claude_agent_sdk")
    fake.ClaudeSDKClient = _FakeClient  # type: ignore[attr-defined]
    fake.ClaudeAgentOptions = _fake_options  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake)


def _settings(subscription_token: str | None = None) -> ProviderSettings:
    return ProviderSettings(
        provider_type="cli",
        model_id="claude-sonnet-4-6",  # from provider_config in real runs — never hardcoded
        base_url=None,
        token_budget=100_000,
        subscription_token=subscription_token,
    )


async def _drain(agen: AsyncIterator[str]) -> list[str]:
    return [delta async for delta in agen]


# ── AC-F17-CHAT-1: streams + injects context + bounded by max_turns ──────────────


@pytest.mark.asyncio
async def test_chat_streams_text_deltas_and_injects_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [
            _FakeAssistantMessage("Hello ", "world"),
            _FakeResultMessage(total_cost_usd=0.012),
        ],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)

    # chat() must NOT raise NotImplementedError (AC-F17-CHAT-1a).
    maybe = provider.chat(
        [Message(role="user", content="hi")],
        retrieval_context="PURPOSE+RETRIEVED-CONTEXT-MARKER",
    )
    agen = await maybe if inspect.isawaitable(maybe) else maybe
    deltas = await _drain(agen)

    # Streamed verbatim text deltas, in order.
    assert deltas == ["Hello ", "world"]
    # retrieval_context injected as the SDK system_prompt (AC-F17-CHAT-1b).
    assert recorder.options is not None
    assert recorder.options["system_prompt"] == "PURPOSE+RETRIEVED-CONTEXT-MARKER"
    # READ-ONLY chat: no write_page / fs-write tools granted.
    assert recorder.options["allowed_tools"] == []
    # model from provider_config (I6), never hardcoded.
    assert recorder.options["model"] == "claude-sonnet-4-6"


@pytest.mark.asyncio
async def test_chat_bounded_by_chat_agent_max_turns_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CHAT_AGENT_MAX_TURNS env bound is passed to the SDK as max_turns (I7, AC-F17-CHAT-1c)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CHAT_AGENT_MAX_TURNS", "3")
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [_FakeAssistantMessage("ok"), _FakeResultMessage(total_cost_usd=0.0)],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    provider.bind_accumulator(UsageAccumulator())
    agen = await provider.chat([Message(role="user", content="hi")], "ctx")
    await _drain(agen)

    assert recorder.options is not None
    assert recorder.options["max_turns"] == 3


@pytest.mark.asyncio
async def test_chat_default_max_turns_is_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default CHAT_AGENT_MAX_TURNS is 8 when the env is unset (ADR-0022 §2.7)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("CHAT_AGENT_MAX_TURNS", raising=False)
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [_FakeAssistantMessage("ok"), _FakeResultMessage(total_cost_usd=0.0)],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    provider.bind_accumulator(UsageAccumulator())
    await _drain(await provider.chat([Message(role="user", content="hi")], "ctx"))

    assert recorder.options is not None
    assert recorder.options["max_turns"] == 8


@pytest.mark.asyncio
async def test_chat_invalid_max_turns_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-positive / malformed CHAT_AGENT_MAX_TURNS never yields an unbounded loop (I7)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("CHAT_AGENT_MAX_TURNS", "0")
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [_FakeAssistantMessage("ok"), _FakeResultMessage(total_cost_usd=0.0)],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    provider.bind_accumulator(UsageAccumulator())
    await _drain(await provider.chat([Message(role="user", content="hi")], "ctx"))

    assert recorder.options is not None
    assert recorder.options["max_turns"] == 8  # fell back to default


# ── AC-F17-CHAT-2: shape compatible with the other providers ─────────────────────


@pytest.mark.asyncio
async def test_chat_returns_async_iterator_of_strings(monkeypatch: pytest.MonkeyPatch) -> None:
    """chat() resolves to an async iterator of str — same shape as Ollama/Api (no special case)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [_FakeAssistantMessage("a", "b"), _FakeResultMessage(total_cost_usd=0.0)],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    provider.bind_accumulator(UsageAccumulator())

    maybe = provider.chat([Message(role="user", content="hi")], "ctx")
    agen = await maybe if inspect.isawaitable(maybe) else maybe
    # It is an async iterator (has __anext__), exactly how stream.py consumes it.
    assert hasattr(agen, "__anext__")
    deltas = await _drain(agen)
    assert all(isinstance(d, str) for d in deltas)
    assert deltas == ["a", "b"]


def test_build_chat_prompt_drops_system_and_tags_roles() -> None:
    """The system turn belongs to system_prompt; user/assistant turns are role-tagged."""
    prompt = _build_chat_prompt(
        [
            Message(role="system", content="SHOULD-NOT-APPEAR"),
            Message(role="user", content="question one"),
            Message(role="assistant", content="answer one"),
            Message(role="user", content="question two"),
        ]
    )
    assert "SHOULD-NOT-APPEAR" not in prompt
    assert "user: question one" in prompt
    assert "assistant: answer one" in prompt
    assert "user: question two" in prompt


# ── AC-F17-CHAT-3: cost per NB-4 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_records_real_sdk_cost_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK reports total_cost_usd > 0 (API-key billing) → recorded truthfully on the accumulator."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [_FakeAssistantMessage("hi"), _FakeResultMessage(total_cost_usd=0.042)],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)
    await _drain(await provider.chat([Message(role="user", content="hi")], "ctx"))

    assert acc.total_cost_usd == pytest.approx(0.042)
    # Token counts carried through the Usage normalization contract.
    assert acc.input_tokens == 120
    assert acc.output_tokens == 80


@pytest.mark.asyncio
async def test_chat_falls_back_to_zero_cost_with_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No SDK cost (subscription/OAuth) → $0.00 + WARNING, no exception (NB-4, AC-F17-CHAT-3)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [_FakeAssistantMessage("hi"), _FakeResultMessage(total_cost_usd=None)],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)

    with caplog.at_level(logging.WARNING):
        await _drain(await provider.chat([Message(role="user", content="hi")], "ctx"))

    assert acc.total_cost_usd == 0.0
    assert any("by the build-time-credit convention" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_chat_no_cost_metadata_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ResultMessage at all (absent cost metadata) → $0.00, no exception (AC-F17-CHAT-3)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [_FakeAssistantMessage("only text, no result message")],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)
    deltas = await _drain(await provider.chat([Message(role="user", content="hi")], "ctx"))

    assert deltas == ["only text, no result message"]
    assert acc.total_cost_usd == 0.0  # NB-4 fallback, no crash


# ── No auth configured: clean pre-stream config error (Do-NOT #9) ────────────────


@pytest.mark.asyncio
async def test_chat_no_auth_raises_clean_config_error_not_fake_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    With NEITHER ANTHROPIC_API_KEY nor CLAUDE_CODE_OAUTH_TOKEN nor CLAUDE_CODE_USE_SUBSCRIPTION,
    chat() raises a clean ValueError BEFORE returning a stream — never a fake stream (Do-NOT #9).
    The message names all three options. The raise happens in the awaited coroutine, so it
    surfaces as a normal provider error (not a half-open generator).
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [_FakeAssistantMessage("should never run")],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    provider.bind_accumulator(UsageAccumulator())

    with pytest.raises(ValueError) as excinfo:
        await provider.chat([Message(role="user", content="hi")], "ctx")
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)
    assert "CLAUDE_CODE_OAUTH_TOKEN" in str(excinfo.value)
    assert "CLAUDE_CODE_USE_SUBSCRIPTION" in str(excinfo.value)

    # The SDK client was never constructed — no fake stream was opened.
    assert recorder.options is None


@pytest.mark.asyncio
async def test_chat_subscription_mode_does_not_raise_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    With CLAUDE_CODE_USE_SUBSCRIPTION=true and NO API key, chat() must NOT raise the auth
    ValueError — it proceeds to open the (faked) SDK stream and records $0.00 as intended.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", "true")
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [_FakeAssistantMessage("hi"), _FakeResultMessage(total_cost_usd=None)],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)

    deltas = await _drain(await provider.chat([Message(role="user", content="hi")], "ctx"))

    assert deltas == ["hi"]
    assert recorder.options is not None  # the SDK session was opened (auth gate passed)
    assert acc.total_cost_usd == 0.0  # subscription → $0 by convention


@pytest.mark.asyncio
async def test_chat_db_token_scrubs_child_env_and_restores_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ADR-0043 §2.3 on the chat path: a DB subscription_token + ambient ANTHROPIC_API_KEY → the
    (faked) SDK session observes the injected CLAUDE_CODE_OAUTH_TOKEN and NO ANTHROPIC_API_KEY;
    $0.00 recorded (subscription); parent os.environ restored after the stream is drained.
    """
    import os

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    captured: dict[str, Any] = {}

    class _CapturingClient:
        def __init__(self, options: Any) -> None:
            captured["oauth"] = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
            captured["api_key_present"] = "ANTHROPIC_API_KEY" in os.environ

        async def __aenter__(self) -> _CapturingClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def query(self, prompt: str) -> None:
            return None

        async def receive_response(self):  # type: ignore[no-untyped-def]
            yield _FakeAssistantMessage("hi")
            yield _FakeResultMessage(total_cost_usd=None)

    def _fake_options(**kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    fake = types.ModuleType("claude_agent_sdk")
    fake.ClaudeSDKClient = _CapturingClient  # type: ignore[attr-defined]
    fake.ClaudeAgentOptions = _fake_options  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake)

    before = dict(os.environ)
    provider = CliAgentProvider(_settings(subscription_token="sk-ant-oat01-db-value"))
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)

    deltas = await _drain(await provider.chat([Message(role="user", content="hi")], "ctx"))

    assert deltas == ["hi"]
    assert captured["oauth"] == "sk-ant-oat01-db-value"  # injected DB token seen by the child
    assert captured["api_key_present"] is False  # scrubbed (§2.3 crux)
    assert acc.total_cost_usd == 0.0  # subscription → $0 by convention
    assert dict(os.environ) == before  # parent os.environ restored exactly
