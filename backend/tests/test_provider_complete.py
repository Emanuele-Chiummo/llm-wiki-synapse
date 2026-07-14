"""
Unit tests for the provider-neutral raw-text ``complete()`` transport (ADR-0076, PR5b).

The block-based ingest loop calls ``provider.complete(system, prompt, max_tokens=...)`` and parses
the raw text with app.ingest.blocks — so ``complete()`` must NOT request JSON mode, must honor
max_tokens, and must record Usage out of band (I7). These tests mock httpx so they run in CI with
no network (the live path is the smoke matrix). The default ABC ``complete()`` must raise so a
provider that cannot do the block loop is never silently routed through it.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.ingest.provider.api import ApiProvider
from app.ingest.provider.base import UsageAccumulator
from app.ingest.provider.config import ProviderSettings
from app.ingest.provider.ollama import OllamaProvider


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:  # pragma: no cover - trivial
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    """Captures the last POSTed json body and returns a canned payload."""

    last_body: dict[str, Any] | None = None
    last_url: str | None = None
    _payload: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(
        self, url: str, *, json: dict[str, Any] | None = None, **kwargs: Any
    ) -> _FakeResponse:
        type(self).last_url = url
        type(self).last_body = json
        return _FakeResponse(type(self)._payload)


def _patch_client(monkeypatch: pytest.MonkeyPatch, module: str, payload: dict[str, Any]) -> None:
    _FakeAsyncClient._payload = payload
    _FakeAsyncClient.last_body = None
    _FakeAsyncClient.last_url = None
    monkeypatch.setattr(f"{module}.httpx.AsyncClient", _FakeAsyncClient)


# ── Ollama ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ollama_complete_text_mode_no_json_and_num_predict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(
        monkeypatch,
        "app.ingest.provider.ollama",
        {
            "message": {"content": "---FILE: wiki/sources/x.md---\nbody\n---END FILE---"},
            "prompt_eval_count": 120,
            "eval_count": 40,
        },
    )
    provider = OllamaProvider(
        ProviderSettings(
            provider_type="local", model_id="qwen2.5:3b", base_url="http://ollama:11434"
        )
    )
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)

    out = await provider.complete("SYS", "PROMPT", max_tokens=8192)

    assert out.startswith("---FILE:")
    body = _FakeAsyncClient.last_body
    assert body is not None
    assert "format" not in body, "block pipeline must NOT request Ollama json mode"
    assert body["stream"] is False
    assert body["options"]["num_predict"] == 8192
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    assert body["messages"][1] == {"role": "user", "content": "PROMPT"}
    assert acc.input_tokens == 120 and acc.output_tokens == 40
    assert acc.total_cost_usd == 0.0


@pytest.mark.asyncio
async def test_ollama_complete_empty_content_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, "app.ingest.provider.ollama", {"message": {"content": "   "}})
    provider = OllamaProvider(
        ProviderSettings(provider_type="local", model_id="m", base_url="http://ollama:11434")
    )
    with pytest.raises(ValueError, match="empty message content"):
        await provider.complete("s", "p", max_tokens=1000)


# ── API — Anthropic-native ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_anthropic_complete_honours_max_tokens_no_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(
        monkeypatch,
        "app.ingest.provider.api",
        {
            "content": [
                {"type": "text", "text": "---FILE: wiki/sources/x.md---\nb\n---END FILE---"}
            ],
            "usage": {"input_tokens": 200, "output_tokens": 60},
            "stop_reason": "end_turn",
        },
    )
    provider = ApiProvider(
        ProviderSettings(provider_type="api", model_id="claude-haiku-4-5-20251001", api_key="k")
    )
    acc = UsageAccumulator()
    provider.bind_accumulator(acc)

    out = await provider.complete("SYS", "PROMPT", max_tokens=24576)

    assert out.startswith("---FILE:")
    body = _FakeAsyncClient.last_body
    assert body is not None
    assert body["max_tokens"] == 24576
    assert "response_format" not in body
    assert body["system"] == "SYS"
    assert acc.input_tokens == 200 and acc.output_tokens == 60


# ── API — OpenAI-compatible ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_api_openai_complete_no_response_format_sets_max_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_client(
        monkeypatch,
        "app.ingest.provider.api",
        {
            "choices": [
                {
                    "message": {"content": "---FILE: wiki/sources/x.md---\nb\n---END FILE---"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 90, "completion_tokens": 33},
        },
    )
    provider = ApiProvider(
        ProviderSettings(
            provider_type="api",
            model_id="gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key="k",
        )
    )
    out = await provider.complete("SYS", "PROMPT", max_tokens=16384)

    assert out.startswith("---FILE:")
    body = _FakeAsyncClient.last_body
    assert body is not None
    assert "response_format" not in body, "block pipeline must NOT request OpenAI json_object mode"
    assert body["max_tokens"] == 16384


@pytest.mark.asyncio
async def test_api_openai_analyze_path_still_json_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: the JSON analyze/generate path is byte-identical (still sets json_object)."""
    _patch_client(
        monkeypatch,
        "app.ingest.provider.api",
        {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "topics": ["t"],
                                "entities": [],
                                "language": "en",
                                "suggested_pages": [{"title": "T", "type": "source"}],
                                "summary": "s",
                            }
                        )
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )
    provider = ApiProvider(
        ProviderSettings(
            provider_type="api",
            model_id="gpt-4o",
            base_url="https://api.openai.com/v1",
            api_key="k",
        )
    )
    await provider.analyze("src", "ctx")
    body = _FakeAsyncClient.last_body
    assert body is not None
    assert body["response_format"] == {"type": "json_object"}
