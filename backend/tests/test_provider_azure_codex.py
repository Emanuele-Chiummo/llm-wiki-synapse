"""
F17 provider parity fixes (llm_wiki):

- Azure OpenAI is detected from the base_url host and routed on the Azure wire (api-key header,
  /openai/deployments/<dep>/chat/completions?api-version, no body `model`) — llm_wiki azure-openai.ts.
  Before this, an azure-openai vendor hit the generic OpenAI path (Bearer, wrong URL) → 401/404.
- The CLI backend now rejects a non-Claude model id (e.g. the catalog's codex-cli) instead of
  silently spawning `claude` with a gpt-* id.
"""

from __future__ import annotations

import pytest
from app.ingest.provider.api import (
    ApiProvider,
    _azure_chat_url,
    _is_azure_endpoint,
)
from app.ingest.provider.cli import _assert_claude_model
from app.ingest.provider.config import ProviderSettings

# ── Azure endpoint detection ──────────────────────────────────────────────────


def test_is_azure_endpoint_true_for_azure_hosts() -> None:
    assert _is_azure_endpoint("https://my-res.openai.azure.com") is True
    assert _is_azure_endpoint("https://my-res.openai.azure.com/openai/deployments/gpt4o") is True
    assert _is_azure_endpoint("my-res.openai.azure.com") is True  # no scheme


def test_is_azure_endpoint_false_for_non_azure() -> None:
    assert _is_azure_endpoint("https://api.openai.com/v1") is False
    assert _is_azure_endpoint("http://localhost:11434/v1") is False
    assert _is_azure_endpoint(None) is False
    assert _is_azure_endpoint("") is False
    # A host that merely contains the string but isn't the azure domain must NOT match.
    assert _is_azure_endpoint("https://openai.azure.com.evil.test/v1") is False


# ── Azure URL building (llm_wiki buildAzureOpenAiUrl parity) ───────────────────


def test_azure_url_from_bare_resource_uses_model_as_deployment() -> None:
    url = _azure_chat_url("https://res.openai.azure.com", "gpt-4o")
    assert (
        url
        == "https://res.openai.azure.com/openai/deployments/gpt-4o/chat/completions?api-version=2024-10-21"
    )


def test_azure_url_keeps_embedded_deployment() -> None:
    url = _azure_chat_url("https://res.openai.azure.com/openai/deployments/my-dep", "gpt-4o")
    assert "/openai/deployments/my-dep/chat/completions?api-version=" in url
    assert "gpt-4o" not in url  # embedded deployment wins over the model fallback


def test_azure_url_honours_existing_api_version() -> None:
    url = _azure_chat_url(
        "https://res.openai.azure.com/openai/deployments/d?api-version=2025-01-01", "gpt-4o"
    )
    assert url.endswith("api-version=2025-01-01")


# ── ApiProvider Azure wire ────────────────────────────────────────────────────


def _azure_provider() -> ApiProvider:
    return ApiProvider(
        ProviderSettings(
            provider_type="api",
            model_id="gpt-4o",
            base_url="https://res.openai.azure.com/openai/deployments/my-dep",
        )
    )


def test_apiprovider_azure_wire() -> None:
    p = _azure_provider()
    assert p._is_azure is True
    assert "res.openai.azure.com/openai/deployments/my-dep/chat/completions" in p._openai_post_url()
    headers = p._openai_headers("SECRET")
    assert headers["api-key"] == "SECRET"
    assert "authorization" not in headers
    body = p._finalize_openai_body({"model": "gpt-4o", "messages": []})
    assert "model" not in body  # Azure carries the deployment in the URL


def test_apiprovider_generic_openai_wire_unchanged() -> None:
    p = ApiProvider(
        ProviderSettings(
            provider_type="api", model_id="gpt-4o", base_url="https://api.openai.com/v1"
        )
    )
    assert p._is_azure is False
    assert p._openai_post_url() == "https://api.openai.com/v1/chat/completions"
    headers = p._openai_headers("SECRET")
    assert headers["authorization"] == "Bearer SECRET"
    assert "api-key" not in headers
    body = p._finalize_openai_body({"model": "gpt-4o", "messages": []})
    assert body["model"] == "gpt-4o"  # generic OpenAI keeps the model in the body


# ── CLI codex guard ───────────────────────────────────────────────────────────


def test_cli_rejects_non_claude_model() -> None:
    for bad in ["gpt-5.1", "o3-mini", "codex-mini-latest", ""]:
        with pytest.raises(ValueError, match="non-Claude model id"):
            _assert_claude_model(bad)


def test_cli_accepts_claude_models() -> None:
    for good in ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"]:
        _assert_claude_model(good)  # must not raise


def test_generation_max_output_tokens_headroom() -> None:
    """Regression: the API output cap must be generous so multi-page JSON generation isn't
    truncated mid-object (which surfaced as an 'Expecting , delimiter' JSON parse error).
    16384 gives ~2× headroom over an 8192-token source and stays under Haiku 4.5's 64K cap."""
    from app.ingest.provider import api as api_mod

    assert api_mod._DEFAULT_MAX_TOKENS >= 16384
    # Must stay within the smallest supported model output window (Haiku 4.5 = 64000).
    assert api_mod._DEFAULT_MAX_TOKENS <= 64000


@pytest.mark.asyncio
async def test_anthropic_truncation_raises_actionable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A max_tokens-truncated Anthropic response must raise a clear, actionable error naming
    PROVIDER_MAX_OUTPUT_TOKENS — NOT return the truncated body to be JSON-parsed (which
    produced the cryptic 'Expecting , delimiter' failure)."""
    import httpx
    from app.ingest.provider.config import ProviderSettings

    p = ApiProvider(ProviderSettings(provider_type="api", model_id="claude-haiku-4-5-20251001"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    truncated = {
        "content": [{"type": "text", "text": '{"pages": [{"title": "A", "body": "unterm'}],
        "stop_reason": "max_tokens",
        "usage": {"input_tokens": 10, "output_tokens": 16384},
    }

    async def _fake_post(self: object, url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(200, json=truncated, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    with pytest.raises(ValueError, match="truncated at max_tokens"):
        await p._complete_anthropic(system="s", user="u")


@pytest.mark.asyncio
async def test_anthropic_complete_response_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal (stop_reason=end_turn) response returns the text unchanged — the truncation
    guard must not fire on complete output."""
    import httpx
    from app.ingest.provider.config import ProviderSettings

    p = ApiProvider(ProviderSettings(provider_type="api", model_id="claude-haiku-4-5-20251001"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    complete = {
        "content": [{"type": "text", "text": '{"pages": []}'}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    async def _fake_post(self: object, url: str, **kwargs: object) -> httpx.Response:
        return httpx.Response(200, json=complete, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _fake_post)

    assert await p._complete_anthropic(system="s", user="u") == '{"pages": []}'
