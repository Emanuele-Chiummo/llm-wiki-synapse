"""
B2-C1 — image attach in chat: the InferenceProvider vision surface (F17 / I6).

The pinned contract (do NOT deviate):
  - Message gains an additive `images: list[MessageImage]` (default []), backward-compatible.
  - capabilities() advertises `supports_vision`; callers gate on it (no isinstance/class-name).
  - chat() carries Message.images into the provider-native multimodal payload ONLY when
    capabilities().supports_vision is True; drops them (debug log) otherwise. Text path unchanged.

Coverage:
  T-B2C1-01  MessageImage + Message.images shape (defaults, backward-compat).
  T-B2C1-02  every provider's capabilities() includes supports_vision.
  T-B2C1-03  Ollama vision model → images[] base64 in the /api/chat body.
  T-B2C1-04  Ollama non-vision model → images dropped; text path unchanged.
  T-B2C1-05  Anthropic ApiProvider (vision) → image content blocks (type=image, base64 source).
  T-B2C1-06  OpenAI-compatible ApiProvider with supports_vision=False → images dropped.
  T-B2C1-07  OpenAI-compatible ApiProvider with supports_vision=True → image_url data URIs.
  T-B2C1-08  CLI provider (vision) → base64 images materialized to temp files; prompt references
             them; scoped Read tool granted; temp files cleaned up.
  T-B2C1-09  _build_chat_prompt with no images is byte-identical to the text-only prompt.

Infra-free: httpx.MockTransport for Ollama/API (records the wire body); a faked claude_agent_sdk
in sys.modules for CLI. No live APIs.
"""

from __future__ import annotations

import inspect
import json
import sys
import types
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.ingest.provider import api as api_mod
from app.ingest.provider import ollama as ollama_mod
from app.ingest.provider.api import ApiProvider, _anthropic_content, _openai_content
from app.ingest.provider.base import UsageAccumulator
from app.ingest.provider.cli import CliAgentProvider, _build_chat_prompt
from app.ingest.provider.config import ProviderSettings
from app.ingest.provider.ollama import OllamaProvider
from app.ingest.schemas import Message, MessageImage

# A tiny valid base64 payload (the bytes are opaque to the wire assertions).
_B64 = "aGVsbG8="  # base64("hello")


# ── T-B2C1-01: Message / MessageImage shape ──────────────────────────────────────


def test_message_images_default_empty_and_backward_compat() -> None:
    """A text-only Message keeps working; images defaults to []."""
    m = Message(role="user", content="hi")
    assert m.images == []
    # An older-style dict without images validates unchanged (backward-compat).
    m2 = Message.model_validate({"role": "user", "content": "hi"})
    assert m2.images == []


def test_message_image_fields() -> None:
    """MessageImage carries mime + data_base64 (no data-URI prefix)."""
    img = MessageImage(mime="image/png", data_base64=_B64)
    assert img.mime == "image/png"
    assert img.data_base64 == _B64
    m = Message(role="user", content="see this", images=[img])
    assert len(m.images) == 1
    assert m.images[0].mime == "image/png"


# ── T-B2C1-02: capabilities() includes supports_vision on every provider ─────────


def test_all_providers_capabilities_include_supports_vision(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    ollama = OllamaProvider(ProviderSettings(provider_type="local", model_id="llava:13b"))
    api = ApiProvider(ProviderSettings(provider_type="api", model_id="claude-x"))
    cli = CliAgentProvider(ProviderSettings(provider_type="cli", model_id="claude-x"))
    for provider in (ollama, api, cli):
        caps = provider.capabilities()
        assert hasattr(caps, "supports_vision")
        assert isinstance(caps.supports_vision, bool)


# ── httpx MockTransport helpers ──────────────────────────────────────────────────


def _patch_ollama_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.setdefault("transport", transport)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", _factory)


def _patch_api_transport(
    monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]
) -> None:
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.setdefault("transport", transport)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(api_mod.httpx, "AsyncClient", _factory)


async def _drain(agen: AsyncIterator[str]) -> list[str]:
    return [d async for d in agen]


def _img_message() -> Message:
    return Message(
        role="user",
        content="what is in this image?",
        images=[MessageImage(mime="image/png", data_base64=_B64)],
    )


# ── T-B2C1-03/04: Ollama images[] gated on the vision model ──────────────────────


async def test_ollama_vision_model_includes_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """A vision model (llava) → the base64 payload appears in the /api/chat images[] array."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        lines = [
            json.dumps({"message": {"content": "a picture"}, "done": False}),
            json.dumps(
                {"message": {"content": ""}, "done": True, "prompt_eval_count": 5, "eval_count": 2}
            ),
        ]
        return httpx.Response(200, text="\n".join(lines))

    _patch_ollama_transport(monkeypatch, handler)
    provider = OllamaProvider(
        ProviderSettings(provider_type="local", model_id="llava:13b", base_url="http://ollama.test")
    )
    assert provider.capabilities().supports_vision is True
    chunks = await _drain(await provider.chat([_img_message()], "ctx"))

    assert "".join(chunks) == "a picture"
    user_msg = next(m for m in captured["body"]["messages"] if m["role"] == "user")
    assert user_msg["images"] == [_B64]  # base64 payload, no data-URI prefix
    assert user_msg["content"] == "what is in this image?"  # text preserved


async def test_ollama_non_vision_model_drops_images(monkeypatch: pytest.MonkeyPatch) -> None:
    """A text-only model → images are dropped; no images key on the wire; text unchanged."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            text=json.dumps(
                {"message": {"content": "ok"}, "done": True, "prompt_eval_count": 1, "eval_count": 1}
            ),
        )

    _patch_ollama_transport(monkeypatch, handler)
    provider = OllamaProvider(
        ProviderSettings(
            provider_type="local", model_id="qwen2.5:7b", base_url="http://ollama.test"
        )
    )
    assert provider.capabilities().supports_vision is False
    await _drain(await provider.chat([_img_message()], "ctx"))

    user_msg = next(m for m in captured["body"]["messages"] if m["role"] == "user")
    assert "images" not in user_msg  # dropped (defense-in-depth, B2-C1)
    assert user_msg["content"] == "what is in this image?"


# ── T-B2C1-05: Anthropic image content blocks ────────────────────────────────────


def test_anthropic_content_builder_blocks() -> None:
    """The pure builder emits image blocks + a trailing text block for a vision instance."""
    content = _anthropic_content(_img_message(), vision=True)
    assert isinstance(content, list)
    img_block = content[0]
    assert img_block["type"] == "image"
    assert img_block["source"] == {"type": "base64", "media_type": "image/png", "data": _B64}
    assert content[-1] == {"type": "text", "text": "what is in this image?"}


def test_anthropic_content_builder_drops_when_no_vision() -> None:
    """vision False → plain string content (images dropped)."""
    content = _anthropic_content(_img_message(), vision=False)
    assert content == "what is in this image?"


async def test_anthropic_chat_sends_image_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the Anthropic /v1/messages body carries the image content block."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        sse = (
            'data: {"type":"message_start","message":{"usage":{"input_tokens":10}}}\n\n'
            'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"seen"}}\n\n'
            'data: {"type":"message_delta","usage":{"output_tokens":3}}\n\n'
        )
        return httpx.Response(200, text=sse)

    _patch_api_transport(monkeypatch, handler)
    provider = ApiProvider(ProviderSettings(provider_type="api", model_id="claude-x"))
    assert provider.capabilities().supports_vision is True
    chunks = await _drain(await provider.chat([_img_message()], "ctx"))

    assert "".join(chunks) == "seen"
    user_msg = next(m for m in captured["body"]["messages"] if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert user_msg["content"][0]["type"] == "image"
    assert user_msg["content"][0]["source"]["data"] == _B64


# ── T-B2C1-06/07: OpenAI-compatible image_url gated on the config flag ───────────


def test_openai_content_builder_data_uri() -> None:
    """vision True → image_url part with a data URI assembled from mime + base64."""
    content = _openai_content(_img_message(), vision=True)
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
    assert content[0]["image_url"]["url"] == f"data:image/png;base64,{_B64}"
    assert content[-1] == {"type": "text", "text": "what is in this image?"}


def test_openai_content_builder_drops_when_no_vision() -> None:
    content = _openai_content(_img_message(), vision=False)
    assert content == "what is in this image?"


async def test_openai_compat_drops_images_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI-compatible endpoint without supports_vision → images dropped (plain string)."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        sse = (
            'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            'data: {"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=sse)

    _patch_api_transport(monkeypatch, handler)
    provider = ApiProvider(
        ProviderSettings(
            provider_type="api", model_id="gpt-x", base_url="http://localhost:1234/v1"
        )
    )
    assert provider.capabilities().supports_vision is False
    await _drain(await provider.chat([_img_message()], "ctx"))

    user_msg = next(m for m in captured["body"]["messages"] if m["role"] == "user")
    assert user_msg["content"] == "what is in this image?"  # dropped → plain string


async def test_openai_compat_sends_image_url_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """OpenAI-compatible endpoint with supports_vision=True → image_url data URI on the wire."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, text="data: [DONE]\n\n")

    _patch_api_transport(monkeypatch, handler)
    provider = ApiProvider(
        ProviderSettings(
            provider_type="api",
            model_id="gemini-x",
            base_url="http://localhost:1234/v1",
            supports_vision=True,
        )
    )
    assert provider.capabilities().supports_vision is True
    await _drain(await provider.chat([_img_message()], "ctx"))

    user_msg = next(m for m in captured["body"]["messages"] if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert user_msg["content"][0]["type"] == "image_url"
    assert user_msg["content"][0]["image_url"]["url"] == f"data:image/png;base64,{_B64}"


# ── T-B2C1-08: CLI provider materializes images + grants scoped Read ─────────────


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeAssistantMessage:
    def __init__(self, *texts: str) -> None:
        self.content = [_FakeTextBlock(t) for t in texts]


class _FakeResultMessage:
    def __init__(self, total_cost_usd: float | None) -> None:
        self.total_cost_usd = total_cost_usd
        self.content: list[Any] = []


def _install_fake_sdk(monkeypatch: pytest.MonkeyPatch, recorder: dict[str, Any]) -> None:
    class _FakeClient:
        def __init__(self, options: Any) -> None:
            recorder["options"] = options
            # Snapshot the temp-image files the provider wrote, while they still exist.
            cwd = options.get("cwd") if isinstance(options, dict) else None
            recorder["files_present"] = (
                sorted(p.name for p in Path(cwd).iterdir()) if cwd else []
            )

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def query(self, prompt: str) -> None:
            recorder["prompt"] = prompt

        async def receive_response(self):  # type: ignore[no-untyped-def]
            yield _FakeAssistantMessage("described")
            yield _FakeResultMessage(total_cost_usd=None)

    def _fake_options(**kwargs: Any) -> dict[str, Any]:
        return dict(kwargs)

    fake = types.ModuleType("claude_agent_sdk")
    fake.ClaudeSDKClient = _FakeClient  # type: ignore[attr-defined]
    fake.ClaudeAgentOptions = _fake_options  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake)


async def test_cli_chat_materializes_images_and_grants_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    CLI provider (vision) → base64 images written to a scoped temp dir; the prompt references the
    paths; a scoped Read tool is granted (cwd = temp dir); temp files removed after the stream.
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    recorder: dict[str, Any] = {}
    _install_fake_sdk(monkeypatch, recorder)

    provider = CliAgentProvider(ProviderSettings(provider_type="cli", model_id="claude-sonnet-4-6"))
    provider.bind_accumulator(UsageAccumulator())
    assert provider.capabilities().supports_vision is True

    deltas = await _drain(await provider.chat([_img_message()], "ctx"))
    assert deltas == ["described"]

    opts = recorder["options"]
    assert opts["allowed_tools"] == ["Read"]  # scoped Read granted only because images present
    assert "cwd" in opts  # scoped to the temp image dir
    # The image file existed at SDK-session time and the prompt referenced its path.
    assert recorder["files_present"], "expected a materialized image temp file"
    assert "Read tool" in recorder["prompt"]
    assert recorder["files_present"][0] in recorder["prompt"] or opts["cwd"] in recorder["prompt"]
    # Temp dir (files + dir) removed after the stream drained (no leak).
    assert not Path(opts["cwd"]).exists()


async def test_cli_chat_text_only_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """No images → the historical text-only path: allowed_tools=[], no cwd, no Read grant."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    recorder: dict[str, Any] = {}
    _install_fake_sdk(monkeypatch, recorder)

    provider = CliAgentProvider(ProviderSettings(provider_type="cli", model_id="claude-sonnet-4-6"))
    provider.bind_accumulator(UsageAccumulator())
    await _drain(await provider.chat([Message(role="user", content="hi")], "ctx"))

    opts = recorder["options"]
    assert opts["allowed_tools"] == []
    assert "cwd" not in opts


# ── T-B2C1-09: _build_chat_prompt text-only path is unchanged ────────────────────


def test_build_chat_prompt_no_images_unchanged() -> None:
    msgs = [
        Message(role="user", content="q one"),
        Message(role="assistant", content="a one"),
    ]
    assert _build_chat_prompt(msgs) == _build_chat_prompt(msgs, None)
    assert _build_chat_prompt(msgs) == "user: q one\n\nassistant: a one"


def test_chat_returns_awaitable_async_iterator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity: chat() still returns the awaitable → async-iterator shape stream.py expects."""
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.test")
    provider = OllamaProvider(ProviderSettings(provider_type="local", model_id="llava:13b"))
    maybe = provider.chat([_img_message()], "ctx")
    assert inspect.isawaitable(maybe)
    maybe.close()  # do not actually run it here (no transport patched)
