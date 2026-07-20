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
from pathlib import Path
from typing import Any

import pytest
from app.ingest.provider.base import UsageAccumulator
from app.ingest.provider.cli import (
    CliAgentProvider,
    _build_chat_prompt,
    _build_chat_stream_options,
    _extract_partial_text_deltas,
)
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


class _FakeStreamEvent:
    """
    A claude-agent-sdk partial StreamEvent (include_partial_messages=True). Carries the raw
    Anthropic streaming event on `.event`; a text delta looks like
    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "<piece>"}}.
    """

    def __init__(self, event: dict[str, Any]) -> None:
        self.event = event

    @classmethod
    def text_delta(cls, text: str) -> _FakeStreamEvent:
        return cls({"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}})

    @classmethod
    def other(cls, event_type: str) -> _FakeStreamEvent:
        """A non-text StreamEvent (message_start, content_block_start/stop, ping, ...)."""
        return cls({"type": event_type})


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
        # 2.1.5: system_prompt is now a SystemPromptFile ({"type": "file", "path": ...}), not a
        # raw string (E2BIG/argv guard) — the temp file is unlinked as soon as the provider's
        # `with _system_prompt_file(...)` block exits, so its content must be read out HERE, at
        # _FakeClient construction time, while the file still exists.
        self.system_prompt_text: str | None = None


def _install_fake_sdk(
    monkeypatch: pytest.MonkeyPatch,
    response_messages: list[Any],
    recorder: _Recorder,
) -> None:
    """Install a fake `claude_agent_sdk` exposing the two symbols cli.py imports."""

    class _FakeClient:
        def __init__(self, options: Any) -> None:
            recorder.options = options
            sp = options.get("system_prompt")
            if isinstance(sp, dict) and sp.get("type") == "file":
                recorder.system_prompt_text = Path(sp["path"]).read_text(encoding="utf-8")

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
    # retrieval_context injected as the SDK system_prompt (AC-F17-CHAT-1b), routed through a temp
    # file rather than raw argv text (2.1.5, E2BIG guard) — see _Recorder above.
    assert recorder.options is not None
    assert recorder.options["system_prompt"]["type"] == "file"
    assert recorder.system_prompt_text == "PURPOSE+RETRIEVED-CONTEXT-MARKER"
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


# ── v1.3.10: token-by-token partial-message streaming (no duplication) ───────────


def test_extract_partial_text_deltas_reads_text_delta_events() -> None:
    """A content_block_delta/text_delta StreamEvent yields its incremental text piece."""
    assert _extract_partial_text_deltas(_FakeStreamEvent.text_delta("Hel")) == ["Hel"]
    # Non-text StreamEvents carry no assistant text.
    assert _extract_partial_text_deltas(_FakeStreamEvent.other("message_start")) == []
    assert _extract_partial_text_deltas(_FakeStreamEvent.other("content_block_stop")) == []
    # A complete AssistantMessage is NOT a StreamEvent → [] (its text is handled separately).
    assert _extract_partial_text_deltas(_FakeAssistantMessage("full text")) == []
    # ResultMessage → [].
    assert _extract_partial_text_deltas(_FakeResultMessage(total_cost_usd=0.0)) == []
    # Dict-shaped event is tolerated too (defensive, R3).
    assert _extract_partial_text_deltas(
        {"event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "x"}}}
    ) == ["x"]


def test_build_chat_stream_options_enables_partials_when_supported() -> None:
    """When ClaudeAgentOptions accepts the kwarg, include_partial_messages=True is set."""

    def _options(**kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    opts = _build_chat_stream_options(_options, {"model": "m", "allowed_tools": []})
    assert opts["include_partial_messages"] is True
    assert opts["model"] == "m"


def test_build_chat_stream_options_degrades_when_kwarg_unsupported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An older SDK whose options reject the kwarg → fall back WITHOUT it (no hard crash)."""

    def _old_options(**kwargs: Any) -> dict[str, Any]:
        if "include_partial_messages" in kwargs:
            raise TypeError("unexpected keyword argument 'include_partial_messages'")
        return dict(kwargs)

    with caplog.at_level(logging.WARNING):
        opts = _build_chat_stream_options(_old_options, {"model": "m"})
    assert "include_partial_messages" not in opts
    assert opts["model"] == "m"
    assert any("message-granularity chat streaming" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_chat_streams_partials_incrementally_without_duplication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    v1.3.10 core: a sequence of partial text_delta StreamEvents followed by the COMPLETE
    AssistantMessage (repeating the full text) + ResultMessage → the pieces are yielded
    INCREMENTALLY (multiple deltas) and the final AssistantMessage text is NOT re-yielded
    (no duplication). Concatenation equals the full answer exactly once.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [
            _FakeStreamEvent.other("message_start"),
            _FakeStreamEvent.other("content_block_start"),
            _FakeStreamEvent.text_delta("The "),
            _FakeStreamEvent.text_delta("quick "),
            _FakeStreamEvent.text_delta("brown fox"),
            _FakeStreamEvent.other("content_block_stop"),
            # The complete AssistantMessage repeats the FULL text — must be SKIPPED.
            _FakeAssistantMessage("The quick brown fox"),
            _FakeResultMessage(total_cost_usd=0.01),
        ],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)
    deltas = await _drain(await provider.chat([Message(role="user", content="hi")], "ctx"))

    # Streamed incrementally as separate pieces (more than one delta) …
    assert deltas == ["The ", "quick ", "brown fox"]
    # … and NO duplication: the final AssistantMessage text was not re-yielded.
    assert "".join(deltas) == "The quick brown fox"
    assert deltas.count("The quick brown fox") == 0
    # partial streaming was requested on the options (v1.3.10).
    assert recorder.options is not None
    assert recorder.options["include_partial_messages"] is True
    # Usage/cost still recorded off the AssistantMessage + ResultMessage.
    assert acc.total_cost_usd == pytest.approx(0.01)
    assert acc.input_tokens == 120
    assert acc.output_tokens == 80


@pytest.mark.asyncio
async def test_chat_backcompat_complete_message_only_yields_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Old-SDK / no-partial path: only a complete AssistantMessage is yielded (no StreamEvents) →
    the full text is still yielded exactly once (back-compat preserved).
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    recorder = _Recorder()
    _install_fake_sdk(
        monkeypatch,
        [
            _FakeAssistantMessage("Hello ", "world"),
            _FakeResultMessage(total_cost_usd=0.02),
        ],
        recorder,
    )

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)
    deltas = await _drain(await provider.chat([Message(role="user", content="hi")], "ctx"))

    assert deltas == ["Hello ", "world"]  # yielded once, as before
    assert acc.total_cost_usd == pytest.approx(0.02)
    assert acc.input_tokens == 120
    assert acc.output_tokens == 80
