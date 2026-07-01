"""
CliAgentProvider auth-mode resolution tests (§12, ADR-0008 §3).

The CLI backend may be driven by EITHER a pay-per-token ANTHROPIC_API_KEY OR the Claude Code
(Pro/Max) subscription, signalled by CLAUDE_CODE_OAUTH_TOKEN (container-friendly token from
`claude setup-token`) OR CLAUDE_CODE_USE_SUBSCRIPTION (ambient host login). The auth gate:
  - api-key set (non-empty)                 → "api-key"      (billed)
  - else OAuth token set (non-empty)        → "subscription" ($0 marginal cost)
  - else subscription flag truthy           → "subscription" ($0 marginal cost)
  - else                                    → raise ValueError naming ALL THREE options (Do-NOT #9)

An empty ANTHROPIC_API_KEY is treated as "unset" so it cannot silently override the subscription.
The token VALUE is never read/forwarded — only its presence gates. os.environ is never mutated.

Infra-free: the claude-agent-sdk is faked via sys.modules where a method is actually driven; the
helper tests need no SDK at all. No network call, no DB session.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from app.ingest.provider.base import UsageAccumulator
from app.ingest.provider.cli import CliAgentProvider, _resolve_cli_auth_mode
from app.ingest.provider.config import ProviderSettings


def _settings() -> ProviderSettings:
    return ProviderSettings(
        provider_type="cli",
        model_id="claude-sonnet-4-6",  # from provider_config in real runs — never hardcoded
        base_url=None,
        token_budget=100_000,
    )


class _FakeResultMessage:
    def __init__(self, total_cost_usd: float | None) -> None:
        self.total_cost_usd = total_cost_usd
        self.content: list[Any] = []


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch, response_messages: list[Any]) -> None:
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


# ── Helper: precedence & truthiness ──────────────────────────────────────────────


def test_auth_mode_api_key_takes_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-tok")
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", "true")
    assert _resolve_cli_auth_mode() == "api-key"


def test_auth_mode_subscription_via_flag_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", "true")
    assert _resolve_cli_auth_mode() == "subscription"


def test_auth_mode_subscription_via_oauth_token_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Container-friendly path: only CLAUDE_CODE_OAUTH_TOKEN set → subscription, gate passes."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-xxxx")
    assert _resolve_cli_auth_mode() == "subscription"


def test_auth_mode_empty_oauth_token_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    with pytest.raises(ValueError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        _resolve_cli_auth_mode()


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on", "  On  "])
def test_auth_mode_subscription_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", value)
    assert _resolve_cli_auth_mode() == "subscription"


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_auth_mode_subscription_non_truthy_raises(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", value)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        _resolve_cli_auth_mode()


def test_auth_mode_empty_api_key_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty ANTHROPIC_API_KEY must NOT override the subscription (would bill per token)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", "true")
    assert _resolve_cli_auth_mode() == "subscription"


def test_auth_mode_none_raises_naming_all_three_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    with pytest.raises(ValueError) as excinfo:
        _resolve_cli_auth_mode()
    msg = str(excinfo.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "CLAUDE_CODE_OAUTH_TOKEN" in msg
    assert "CLAUDE_CODE_USE_SUBSCRIPTION" in msg


def test_auth_mode_does_not_mutate_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", "true")
    import os

    before = dict(os.environ)
    _resolve_cli_auth_mode()
    assert dict(os.environ) == before  # no os.environ mutation


# ── delegate_ingest: gate behaviour ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delegate_ingest_subscription_flag_does_not_raise_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    CLAUDE_CODE_USE_SUBSCRIPTION=true + no API key → delegate_ingest passes the auth gate and runs
    the (faked) SDK loop, recording $0.00 by convention. It does NOT raise the auth ValueError.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", "yes")
    _install_fake_sdk(monkeypatch, [_FakeResultMessage(total_cost_usd=None)])

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)

    result = await provider.delegate_ingest(
        source_text="hello",
        system_prompt="schema+purpose",
        vault_dir="/tmp/vault",
        mcp_server=object(),  # non-None so the I1/I5 guard passes
    )

    assert result.usage.total_cost_usd == 0.0  # subscription → $0 by convention
    assert acc.total_cost_usd == 0.0


@pytest.mark.asyncio
async def test_delegate_ingest_oauth_token_does_not_raise_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Container path: only CLAUDE_CODE_OAUTH_TOKEN set → delegate_ingest passes the auth gate and
    records $0.00 by convention. The token VALUE is never forwarded into ClaudeAgentOptions.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-xxxx")
    _install_fake_sdk(monkeypatch, [_FakeResultMessage(total_cost_usd=None)])

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)

    result = await provider.delegate_ingest(
        source_text="hello",
        system_prompt="schema+purpose",
        vault_dir="/tmp/vault",
        mcp_server=object(),
    )

    assert result.usage.total_cost_usd == 0.0
    assert acc.total_cost_usd == 0.0


@pytest.mark.asyncio
async def test_delegate_ingest_no_auth_raises_naming_all_three_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No API key, no OAuth token, no flag → clean pre-loop ValueError naming all three options."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    _install_fake_sdk(monkeypatch, [_FakeResultMessage(total_cost_usd=None)])

    provider = CliAgentProvider(_settings())
    provider.bind_accumulator(UsageAccumulator())

    with pytest.raises(ValueError) as excinfo:
        await provider.delegate_ingest(
            source_text="hello",
            system_prompt="schema+purpose",
            vault_dir="/tmp/vault",
            mcp_server=object(),
        )
    msg = str(excinfo.value)
    assert "ANTHROPIC_API_KEY" in msg
    assert "CLAUDE_CODE_OAUTH_TOKEN" in msg
    assert "CLAUDE_CODE_USE_SUBSCRIPTION" in msg


@pytest.mark.asyncio
async def test_delegate_ingest_api_key_mode_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """api-key mode is unchanged: gate passes, real SDK cost recorded truthfully."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    _install_fake_sdk(monkeypatch, [_FakeResultMessage(total_cost_usd=0.05)])

    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)

    result = await provider.delegate_ingest(
        source_text="hello",
        system_prompt="schema+purpose",
        vault_dir="/tmp/vault",
        mcp_server=object(),
    )

    assert result.usage.total_cost_usd == pytest.approx(0.05)
    assert acc.total_cost_usd == pytest.approx(0.05)
