"""
1.8.1 regression tests — CLI provider resilience.

Covers the DORA-production failures around ``CliAgentProvider.complete()``:
  - empty output is now TYPED — ProviderTransientError (error result: 429 / overloaded / execution
    error) vs ProviderEmptyOutput (clean no-op) — so the loop can retry vs surface (I6);
  - usage/cost is recorded even when the SDK raises mid-stream (I7 try/finally);
  - the block loop retries a transient complete() with bounded backoff instead of aborting the
    whole document, and treats an empty GENERATION as a zero-block attempt.

Infra-free: the claude-agent-sdk is faked via sys.modules (no SDK install, no network).
"""

from __future__ import annotations

import sys
import types
from typing import Any

import app.ingest.block_loop as block_loop_mod
import pytest
from app.ingest.block_loop import _complete_with_retry
from app.ingest.provider.base import (
    ProviderEmptyOutput,
    ProviderTransientError,
    UsageAccumulator,
)
from app.ingest.provider.cli import CliAgentProvider, _sdk_result_error
from app.ingest.provider.config import ProviderSettings


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeUsageMsg:
    def __init__(self) -> None:
        self.usage = _FakeUsage(100, 50)
        self.content: list[Any] = []


class _FakeResult:
    """Terminal ResultMessage with optional error signals + no text."""

    def __init__(
        self,
        *,
        api_error_status: str | None = None,
        is_error: bool = False,
        subtype: str = "success",
        total_cost_usd: float | None = None,
    ) -> None:
        self.api_error_status = api_error_status
        self.is_error = is_error
        self.subtype = subtype
        self.total_cost_usd = total_cost_usd
        self.content: list[Any] = []


def _install_fake_sdk(
    monkeypatch: pytest.MonkeyPatch, messages: list[Any], *, raise_after: int | None = None
) -> None:
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
            for i, msg in enumerate(messages):
                if raise_after is not None and i == raise_after:
                    raise RuntimeError("SDK stream dropped (simulated 429/connection)")
                yield msg
            if raise_after is not None and raise_after >= len(messages):
                raise RuntimeError("SDK stream dropped (simulated 429/connection)")

    fake = types.ModuleType("claude_agent_sdk")
    fake.ClaudeSDKClient = _FakeClient  # type: ignore[attr-defined]
    fake.ClaudeAgentOptions = lambda **kw: dict(kw)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake)


def _settings() -> ProviderSettings:
    return ProviderSettings(
        provider_type="cli",
        model_id="claude-sonnet-4-6",
        base_url=None,
        token_budget=100_000,
        subscription_token="test-sub-token",  # → auth_mode "subscription" (no env needed)
    )


# ── _sdk_result_error classification (pure) ──────────────────────────────────────


def test_sdk_result_error_flags_api_error_status() -> None:
    assert _sdk_result_error(_FakeResult(api_error_status="429")) == "api_error_status=429"


def test_sdk_result_error_flags_is_error_and_error_subtype() -> None:
    assert "is_error" in (_sdk_result_error(_FakeResult(is_error=True)) or "")
    assert _sdk_result_error(_FakeResult(subtype="error_during_execution")) == (
        "subtype=error_during_execution"
    )


def test_sdk_result_error_none_for_clean_result() -> None:
    assert _sdk_result_error(_FakeResult(subtype="success")) is None
    assert _sdk_result_error(None) is None


# ── complete() typed empty classification ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_complete_empty_with_error_result_raises_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_sdk(monkeypatch, [_FakeResult(api_error_status="429")])
    provider = CliAgentProvider(_settings())
    provider.bind_accumulator(UsageAccumulator())
    with pytest.raises(ProviderTransientError):
        await provider.complete("sys", "prompt", max_tokens=1000)


@pytest.mark.asyncio
async def test_complete_clean_empty_raises_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_sdk(monkeypatch, [_FakeResult(subtype="success")])
    provider = CliAgentProvider(_settings())
    provider.bind_accumulator(UsageAccumulator())
    with pytest.raises(ProviderEmptyOutput):
        await provider.complete("sys", "prompt", max_tokens=1000)


@pytest.mark.asyncio
async def test_complete_records_usage_even_when_sdk_raises_midstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Yield one usage-bearing message, then the SDK stream raises before the terminal result.
    _install_fake_sdk(monkeypatch, [_FakeUsageMsg()], raise_after=1)
    provider = CliAgentProvider(_settings())
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)
    with pytest.raises(RuntimeError):
        await provider.complete("sys", "prompt", max_tokens=1000)
    # I7: the partial usage accrued before the raise must still be on the ledger.
    assert acc.total_tokens == 150


# ── _complete_with_retry (block loop) ─────────────────────────────────────────────


class _FakeProvider:
    def __init__(self, script: list[Any]) -> None:
        self._script = script
        self.calls = 0

    async def complete(self, system: str, user: str, *, max_tokens: int) -> str:
        item = self._script[self.calls]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop(_seconds: float) -> None:
        return None

    monkeypatch.setattr(block_loop_mod.asyncio, "sleep", _noop)


@pytest.mark.asyncio
async def test_retry_recovers_from_transient_then_success() -> None:
    provider = _FakeProvider([ProviderTransientError("429"), "ok text"])
    out = await _complete_with_retry(
        provider,  # type: ignore[arg-type]
        "sys",
        "user",
        max_tokens=1000,
        accumulator=UsageAccumulator(),
        token_budget=100_000,
        label="generation",
        empty_ok=True,
    )
    assert out == "ok text"
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_retry_exhausts_and_raises_transient() -> None:
    provider = _FakeProvider([ProviderTransientError("429")] * 5)
    with pytest.raises(ProviderTransientError):
        await _complete_with_retry(
            provider,  # type: ignore[arg-type]
            "sys",
            "user",
            max_tokens=1000,
            accumulator=UsageAccumulator(),
            token_budget=100_000,
            label="analysis",
            empty_ok=False,
        )
    assert provider.calls == block_loop_mod._COMPLETE_MAX_ATTEMPTS


@pytest.mark.asyncio
async def test_empty_output_is_swallowed_when_empty_ok_else_raised() -> None:
    # generation: empty_ok=True → "" so the loop's zero-block augment-retry handles it.
    p1 = _FakeProvider([ProviderEmptyOutput("empty")])
    out = await _complete_with_retry(
        p1,  # type: ignore[arg-type]
        "s",
        "u",
        max_tokens=100,
        accumulator=UsageAccumulator(),
        token_budget=100_000,
        label="generation",
        empty_ok=True,
    )
    assert out == ""
    # analysis: empty_ok=False → propagate (empty analysis is fatal).
    p2 = _FakeProvider([ProviderEmptyOutput("empty")])
    with pytest.raises(ProviderEmptyOutput):
        await _complete_with_retry(
            p2,  # type: ignore[arg-type]
            "s",
            "u",
            max_tokens=100,
            accumulator=UsageAccumulator(),
            token_budget=100_000,
            label="analysis",
            empty_ok=False,
        )
