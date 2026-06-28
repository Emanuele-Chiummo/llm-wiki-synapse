"""
CliAgentProvider cost-logging tests (NB-4, ADR-0009 §2 as amended).

The CLI provider must record the REAL cost the claude-agent-sdk reports on its terminal
ResultMessage when the run was billed via an API key (total_cost_usd > 0). It falls back to the
$0.00 build-time-credit convention (with a WARNING) only when the SDK reports no billable cost
(subscription / OAuth auth). The Usage normalization contract is unchanged (input/output tokens
+ total_cost_usd).

Infra-free: the claude-agent-sdk is faked via sys.modules, so these run in CI without the SDK
installed and without any network call.
"""

from __future__ import annotations

import logging
import sys
import types
from typing import Any

import pytest
from app.ingest.provider.base import UsageAccumulator
from app.ingest.provider.cli import CliAgentProvider, _extract_sdk_cost
from app.ingest.provider.config import ProviderSettings

# ── Fake SDK message shapes ──────────────────────────────────────────────────────


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeBlock:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeAssistantMessage:
    """An assistant message with a write_page tool call + token usage, but no cost."""

    def __init__(self) -> None:
        self.content = [_FakeBlock("write_page")]
        self.usage = _FakeUsage(input_tokens=120, output_tokens=80)


class _FakeResultMessage:
    """The terminal ResultMessage carrying the run's cumulative total_cost_usd."""

    def __init__(self, total_cost_usd: float | None) -> None:
        self.total_cost_usd = total_cost_usd
        self.content = []  # no tool calls


# ── Fake SDK client / options installed into sys.modules ─────────────────────────


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch, response_messages: list[Any]) -> None:
    """Install a fake `claude_agent_sdk` module exposing the two symbols cli.py imports."""

    class _FakeClient:
        def __init__(self, options: Any) -> None:
            self.options = options

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def query(self, prompt: str) -> None:
            return None

        async def receive_response(self):  # type: ignore[no-untyped-def]
            for msg in response_messages:
                yield msg

    def _fake_options(**kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    fake = types.ModuleType("claude_agent_sdk")
    fake.ClaudeSDKClient = _FakeClient  # type: ignore[attr-defined]
    fake.ClaudeAgentOptions = _fake_options  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake)


def _settings() -> ProviderSettings:
    return ProviderSettings(
        provider_type="cli",
        model_id="claude-sonnet-4-6",  # from provider_config in real runs — never hardcoded
        base_url=None,
        token_budget=100_000,
    )


# ── Tests ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cli_logs_real_sdk_cost_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the SDK reports total_cost_usd > 0 (API-key billing), it is recorded truthfully."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _install_fake_sdk(
        monkeypatch,
        [_FakeAssistantMessage(), _FakeResultMessage(total_cost_usd=0.0731)],
    )

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)

    result = await provider.delegate_ingest(
        source_text="hello",
        system_prompt="schema+purpose",
        vault_dir="/tmp/vault",
        mcp_server=object(),  # non-None so the I1/I5 guard passes
    )

    # Truthful cost surfaced on the Usage and pushed to the run accumulator.
    assert result.usage.total_cost_usd == pytest.approx(0.0731)
    assert acc.total_cost_usd == pytest.approx(0.0731)
    # Usage normalization contract intact: tokens still carried through.
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 80
    assert result.pages_written == 1
    assert result.converged is True


@pytest.mark.asyncio
async def test_cli_falls_back_to_zero_when_sdk_reports_no_cost(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """No SDK cost (subscription/OAuth) → $0.00 convention with a WARNING (ADR-0009)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _install_fake_sdk(
        monkeypatch,
        [_FakeAssistantMessage(), _FakeResultMessage(total_cost_usd=None)],
    )

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)

    with caplog.at_level(logging.WARNING):
        result = await provider.delegate_ingest(
            source_text="hello",
            system_prompt="schema+purpose",
            vault_dir="/tmp/vault",
            mcp_server=object(),
        )

    assert result.usage.total_cost_usd == 0.0
    assert acc.total_cost_usd == 0.0
    assert any("by the build-time-credit convention" in r.message for r in caplog.records)


def test_extract_sdk_cost_reads_object_and_dict_shapes() -> None:
    """_extract_sdk_cost reads total_cost_usd from object and dict messages; None otherwise."""
    assert _extract_sdk_cost(_FakeResultMessage(total_cost_usd=0.5)) == pytest.approx(0.5)
    assert _extract_sdk_cost({"total_cost_usd": 0.25}) == pytest.approx(0.25)
    assert _extract_sdk_cost(_FakeResultMessage(total_cost_usd=None)) is None
    assert _extract_sdk_cost(_FakeAssistantMessage()) is None  # no cost field
    assert _extract_sdk_cost({"total_cost_usd": "not-a-number"}) is None
