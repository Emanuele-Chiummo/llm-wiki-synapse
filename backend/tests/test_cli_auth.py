"""
CliAgentProvider auth-mode resolution tests (§12, ADR-0008 §3, amended by ADR-0043 §2.3).

The CLI backend may be driven by a DB-set Claude subscription OAuth token (UI, ADR-0043 — highest
precedence), OR a pay-per-token ANTHROPIC_API_KEY, OR the Claude Code (Pro/Max) subscription
signalled by CLAUDE_CODE_OAUTH_TOKEN (container-friendly token from `claude setup-token`) OR
CLAUDE_CODE_USE_SUBSCRIPTION (ambient host login). The auth gate:
  - subscription_token (DB, non-empty)      → "subscription" (ADR-0043, DB WINS over env)
  - else api-key set (non-empty)            → "api-key"      (billed)
  - else OAuth token set (non-empty)        → "subscription" ($0 marginal cost)
  - else subscription flag truthy           → "subscription" ($0 marginal cost)
  - else                                    → raise ValueError naming ALL THREE env options

Empty = unset at every tier (empty subscription_token OR empty ANTHROPIC_API_KEY) so neither can
silently outrank the subscription. On the DB-token path the token VALUE is injected into the
spawned CLI env (CLAUDE_CODE_OAUTH_TOKEN) and ANTHROPIC_API_KEY is scrubbed from the CHILD env; the
parent os.environ is never PERMANENTLY mutated (scoped, restored-in-finally override, ADR-0043
§2.4). For the env tiers only PRESENCE gates — the value is never read/forwarded.

Infra-free: the claude-agent-sdk is faked via sys.modules where a method is actually driven; the
helper tests need no SDK at all. No network call, no DB session.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from app.ingest.provider.base import UsageAccumulator
from app.ingest.provider.cli import (
    CliAgentProvider,
    _build_cli_child_env,
    _cli_subscription_env_override,
    _cli_subscription_env_scope,
    _resolve_cli_auth_mode,
)
from app.ingest.provider.config import ProviderSettings


def _settings(subscription_token: str | None = None) -> ProviderSettings:
    return ProviderSettings(
        provider_type="cli",
        model_id="claude-sonnet-4-6",  # from provider_config in real runs — never hardcoded
        base_url=None,
        token_budget=100_000,
        subscription_token=subscription_token,
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
    assert _resolve_cli_auth_mode(None) == "api-key"


def test_auth_mode_subscription_via_flag_when_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", "true")
    assert _resolve_cli_auth_mode(None) == "subscription"


def test_auth_mode_subscription_via_oauth_token_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Container-friendly path: only CLAUDE_CODE_OAUTH_TOKEN set → subscription, gate passes."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-xxxx")
    assert _resolve_cli_auth_mode(None) == "subscription"


def test_auth_mode_empty_oauth_token_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    with pytest.raises(ValueError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        _resolve_cli_auth_mode(None)


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "Yes", "on", "  On  "])
def test_auth_mode_subscription_truthy_values(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", value)
    assert _resolve_cli_auth_mode(None) == "subscription"


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_auth_mode_subscription_non_truthy_raises(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", value)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        _resolve_cli_auth_mode(None)


def test_auth_mode_empty_api_key_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty ANTHROPIC_API_KEY must NOT override the subscription (would bill per token)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", "true")
    assert _resolve_cli_auth_mode(None) == "subscription"


def test_auth_mode_none_raises_naming_all_three_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    with pytest.raises(ValueError) as excinfo:
        _resolve_cli_auth_mode(None)
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
    _resolve_cli_auth_mode(None)
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


# ── ADR-0043: DB subscription token — precedence, child-env scrub, parent-env safety ─────────


def test_auth_mode_db_token_wins_over_env_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    ADR-0043 §2.3 crux: a DB-set subscription_token OUTRANKS an env ANTHROPIC_API_KEY. Even with
    the API key set (and the env subscription signals set too), the DB token forces "subscription".
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-oauth")
    monkeypatch.setenv("CLAUDE_CODE_USE_SUBSCRIPTION", "true")
    assert _resolve_cli_auth_mode("sk-ant-oat01-db-value") == "subscription"


def test_auth_mode_empty_db_token_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    An empty/whitespace-free empty DB token is treated as unset (ADR-0043 §2.3 empty=unset), so
    resolution falls back to the ADR-0042 env precedence — here the API key wins.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    assert _resolve_cli_auth_mode("") == "api-key"


def test_auth_mode_none_db_token_preserves_env_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    """subscription_token=None reproduces the ADR-0042 env-only precedence exactly."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-oauth")
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)
    assert _resolve_cli_auth_mode(None) == "subscription"


def test_build_cli_child_env_injects_token_and_scrubs_api_key() -> None:
    """
    The child-env builder (ADR-0043 §2.3): adds CLAUDE_CODE_OAUTH_TOKEN=<db token> and REMOVES
    ANTHROPIC_API_KEY from the child dict, leaving all other keys intact. Pure — the input base
    dict is not mutated.
    """
    base = {
        "ANTHROPIC_API_KEY": "sk-env-key",
        "PATH": "/usr/bin",
        "CLAUDE_CODE_OAUTH_TOKEN": "stale-env-oauth",
    }
    child = _build_cli_child_env(base, "sk-ant-oat01-db-value")

    assert child["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-db-value"  # injected DB value
    assert "ANTHROPIC_API_KEY" not in child  # scrubbed (§2.3 safety crux)
    assert child["PATH"] == "/usr/bin"  # unrelated keys preserved
    # Input dict untouched (pure function).
    assert base["ANTHROPIC_API_KEY"] == "sk-env-key"
    assert base["CLAUDE_CODE_OAUTH_TOKEN"] == "stale-env-oauth"


def test_env_override_scrubs_child_env_and_restores_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Inside _cli_subscription_env_override the child sees the injected OAuth token and NO
    ANTHROPIC_API_KEY; after the scope the parent os.environ is restored EXACTLY (the API key is
    back, the OAuth token reverts to its prior value).
    """
    import os

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "prior-oauth")
    before = dict(os.environ)

    with _cli_subscription_env_override("sk-ant-oat01-db-value"):
        # Child env (== os.environ at spawn time) has the injected token and no API key.
        assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-db-value"
        assert "ANTHROPIC_API_KEY" not in os.environ

    assert dict(os.environ) == before  # parent restored exactly (both keys back to prior state)


def test_env_override_restores_absent_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Restore is exact even when keys were ABSENT before: a pre-absent ANTHROPIC_API_KEY /
    CLAUDE_CODE_OAUTH_TOKEN stays absent after the scope (they are re-removed, not left set).
    """
    import os

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    before = dict(os.environ)

    with _cli_subscription_env_override("sk-ant-oat01-db-value"):
        assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-db-value"
        assert "ANTHROPIC_API_KEY" not in os.environ

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ  # re-removed (was absent before)
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert dict(os.environ) == before


def test_env_override_restores_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scoped override restores the parent os.environ even when the body raises (finally)."""
    import os

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    before = dict(os.environ)

    with pytest.raises(RuntimeError, match="boom"):
        with _cli_subscription_env_override("sk-ant-oat01-db-value"):
            assert "ANTHROPIC_API_KEY" not in os.environ
            raise RuntimeError("boom")

    assert dict(os.environ) == before  # restored despite the exception


def test_env_scope_noop_when_token_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The call-site wrapper is a NO-OP for env-sourced subscription / api-key (token None or empty):
    os.environ is untouched, so the SDK session inherits the ambient env unchanged.
    """
    import os

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-oauth")
    before = dict(os.environ)

    for token in (None, ""):
        with _cli_subscription_env_scope(token):
            assert dict(os.environ) == before  # no injection, no scrub
        assert dict(os.environ) == before


@pytest.mark.asyncio
async def test_delegate_ingest_db_token_scrubs_child_env_and_restores_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    End-to-end on delegate_ingest (ADR-0043 §2.3, DoD #4/#5): with a DB subscription_token AND an
    ambient ANTHROPIC_API_KEY, the (faked) SDK session observes the injected CLAUDE_CODE_OAUTH_TOKEN
    and NO ANTHROPIC_API_KEY in the child env, records $0.00 (subscription), and the parent
    os.environ is fully restored afterwards.
    """
    import os

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-env-key")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_USE_SUBSCRIPTION", raising=False)

    captured: dict[str, Any] = {}

    class _CapturingClient:
        def __init__(self, options: Any) -> None:
            # Snapshot os.environ at "spawn" — this is what the real SDK reads to build child env.
            captured["oauth"] = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
            captured["api_key_present"] = "ANTHROPIC_API_KEY" in os.environ

        async def __aenter__(self) -> _CapturingClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def query(self, prompt: str) -> None:
            return None

        async def receive_response(self):  # type: ignore[no-untyped-def]
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

    result = await provider.delegate_ingest(
        source_text="hello",
        system_prompt="schema+purpose",
        vault_dir="/tmp/vault",
        mcp_server=object(),
    )

    # Child env (at SDK spawn) had the injected DB token and NO API key (scrub proven).
    assert captured["oauth"] == "sk-ant-oat01-db-value"
    assert captured["api_key_present"] is False
    # Subscription → $0 by convention (ADR-0009).
    assert result.usage.total_cost_usd == 0.0
    assert acc.total_cost_usd == 0.0
    # Parent os.environ restored exactly (API key back, no leaked OAuth token).
    assert dict(os.environ) == before
