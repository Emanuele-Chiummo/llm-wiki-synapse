"""
ApiProvider — API backend (F17): Anthropic Messages API OR any OpenAI-compatible endpoint
(Google Gemini, etc.) selected purely by `base_url` from provider_config (I6 — a backend is
config, not a new class).

Invariants:
  - I6: model id from ProviderSettings (provider_config), NEVER hardcoded; endpoint via
    base_url; provider selection never branches on class/type.
  - §12 / ADR-0008 §3: the API key is read from the ENVIRONMENT inside THIS module only
    (ANTHROPIC_API_KEY for the Anthropic path, OPENAI_API_KEY for the OpenAI-compatible path).
    No key in code, config, or DB.
  - ADR-0009: Usage from response.usage; total_cost_usd computed from a price map keyed by
    model_id sourced from the PROVIDER_PRICE_MAP env var (never a literal in app code).
  - capabilities(): supports_agentic_loop=False, supports_tools=True → orchestrated route.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.ingest.provider._common import (
    ANALYZE_SYSTEM,
    CAPTION_INSTRUCTION,
    GENERATE_SYSTEM,
    build_analyze_prompt,
    build_generate_prompt,
    encode_image_base64,
    parse_analysis,
    parse_pages,
    resolve_image_bytes_and_media_type,
)
from app.ingest.provider.base import InferenceProvider
from app.ingest.provider.config import ProviderSettings
from app.ingest.schemas import (
    Analysis,
    Message,
    ProviderCapabilities,
    Usage,
    WikiPage,
)

logger = logging.getLogger(__name__)

_ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
_OPENAI_KEY_ENV = "OPENAI_API_KEY"
_ANTHROPIC_BASE_ENV = "ANTHROPIC_BASE_URL"
_PRICE_MAP_ENV = "PROVIDER_PRICE_MAP"  # JSON: {model_id: {input: usd_per_tok, output: ...}}
_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_CONTEXT = 200_000
# Max OUTPUT tokens per API call. The generation step returns a JSON object with the full text of
# several wiki pages in ONE response; too low a cap truncates it mid-JSON on rich sources → a
# downstream "Expecting ',' delimiter" parse error. max_tokens is a CAP, not a target — a higher
# value costs nothing unless the model actually emits more — so we default generously. 16384 fits
# comfortably under Claude Haiku 4.5's 64K native output cap (no beta header needed on Claude 4+
# models). Env-tunable via PROVIDER_MAX_OUTPUT_TOKENS for very large sources (Haiku accepts up to
# 64000) or capped down for OpenAI-compatible models with smaller output windows. Clamped ≥1024.
_DEFAULT_MAX_TOKENS = max(1024, int(os.environ.get("PROVIDER_MAX_OUTPUT_TOKENS", "16384")))

# W1 (F17): reasoning/thinking effort → backend-native knobs. auto/off/custom/None ⇒ no override
# (degrade-safe; existing behaviour). Only applied when the user explicitly opts into a level, so
# endpoints that don't support it are never sent an unknown field by default.
_ANTHROPIC_THINKING_BUDGET = {"low": 1024, "medium": 4096, "high": 8192, "max": 16000}
_OPENAI_REASONING_EFFORT = {"low": "low", "medium": "medium", "high": "high", "max": "high"}


# ── Azure OpenAI wire (nashsu/llm_wiki azure-openai.ts parity) ─────────────────────────────────
# Azure OpenAI is OpenAI-compatible but differs on the wire: the deployment lives in the URL path,
# auth uses an `api-key` header (NOT `Authorization: Bearer`), an `?api-version=` query param is
# required, and the request body OMITS `model`. We detect Azure from the base_url hostname — the
# SAME signal llm_wiki uses (isAzureOpenAiEndpoint) — so no vendor id needs threading through the
# provider layer. Before this, an azure-openai vendor row hit the generic OpenAI path (Bearer, no
# api-version) → 401/404, and with no base_url it silently hit the Anthropic-native path.
_AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")
_AZURE_HOST_SUFFIX = ".openai.azure.com"


def _is_azure_endpoint(base_url: str | None) -> bool:
    """True when *base_url*'s host ends with .openai.azure.com (llm_wiki isAzureOpenAiEndpoint)."""
    if not base_url:
        return False
    raw = base_url.strip()
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        host = urlsplit(candidate).hostname or ""
    except ValueError:
        return False
    return host.lower().endswith(_AZURE_HOST_SUFFIX)


def _azure_chat_url(base_url: str, deployment: str) -> str:
    """
    Build the Azure chat-completions URL (llm_wiki buildAzureOpenAiUrl parity).

    Accepts base_url in any common shape — bare resource host, resource + deployment path, or a
    full chat URL. A deployment embedded in the path wins over *deployment* (the model_id
    fallback). An `?api-version=` already present in base_url wins over the default. Always ends
    with `/chat/completions?api-version=<ver>`.
    """
    version = _AZURE_OPENAI_API_VERSION
    qm = re.search(r"[?&]api-version=([^&]+)", base_url, re.IGNORECASE)
    if qm:
        version = unquote(qm.group(1))
    version = quote(version.strip() or _AZURE_OPENAI_API_VERSION, safe="")

    trimmed = base_url.split("?")[0].rstrip("/")
    with_deployment = re.match(
        r"^(https?://[^/]+\.openai\.azure\.com)/openai/deployments/([^/]+)(?:/chat/completions)?$",
        trimmed,
        re.IGNORECASE,
    )
    if with_deployment:
        resource = with_deployment.group(1)
        dep = quote(unquote(with_deployment.group(2)), safe="")
        return f"{resource}/openai/deployments/{dep}/chat/completions?api-version={version}"

    resource_only = re.match(
        r"^(https?://[^/]+\.openai\.azure\.com)(?:/openai)?$", trimmed, re.IGNORECASE
    )
    resource = resource_only.group(1) if resource_only else trimmed
    dep = quote(deployment, safe="")
    return f"{resource}/openai/deployments/{dep}/chat/completions?api-version={version}"


# One-time guard so the "no price map" warning fires once per process, not once per
# ApiProvider instance (which is constructed per ingest/chat run).
_price_map_warned = False


def _load_price_map() -> dict[str, dict[str, float]]:
    """
    Load the per-model price map from PROVIDER_PRICE_MAP (USD per token, keyed by model_id).

    Prices live in env-sourced config, NEVER as literals in app code (AC-F17-8, ADR-0009).
    Absent/malformed → empty map → cost computed as 0.0 with a one-time warning. Because
    this is the *billed* provider path, a missing map means every ingest/chat records
    ``total_cost_usd=0.0`` and the I7 cost-anomaly gate can never fire — so warn loudly
    (once) rather than degrade silently.
    """
    global _price_map_warned
    raw = os.environ.get(_PRICE_MAP_ENV)
    if not raw:
        if not _price_map_warned:
            logger.warning(
                "PROVIDER_PRICE_MAP is unset — ApiProvider is a billed backend but "
                "total_cost_usd will be recorded as 0.0 for every run, disabling the I7 "
                "cost-anomaly gate. Set PROVIDER_PRICE_MAP to price per-model tokens."
            )
            _price_map_warned = True
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        logger.warning("PROVIDER_PRICE_MAP is not valid JSON — cost will be recorded as 0.0")
    return {}


# ── Multimodal content builders (B2-C1 / F17 vision) ────────────────────────────


def _anthropic_content(message: Message, vision: bool) -> object:
    """
    Build the Anthropic Messages `content` for one turn (B2-C1).

    Text-only (no images, or images dropped) → the plain string content (unchanged wire shape).
    With images AND vision → a content-block list: one `{"type":"image", "source":{base64}}` block
    per image, followed by a single `{"type":"text"}` block. Images are DROPPED (with a debug log,
    never the base64 payload) when `vision` is False — belt-and-suspenders on top of the frontend
    gate.
    """
    if not message.images:
        return message.content
    if not vision:
        logger.debug(
            "Anthropic chat: dropping %d image(s) — instance is not vision-capable (B2-C1)",
            len(message.images),
        )
        return message.content
    blocks: list[dict[str, object]] = [
        {
            "type": "image",
            "source": {"type": "base64", "media_type": img.mime, "data": img.data_base64},
        }
        for img in message.images
    ]
    blocks.append({"type": "text", "text": message.content})
    return blocks


def _openai_content(message: Message, vision: bool) -> object:
    """
    Build the OpenAI-compatible `content` for one turn (B2-C1).

    Text-only (no images, or images dropped) → the plain string content (unchanged wire shape).
    With images AND vision → a parts list: one `{"type":"image_url","image_url":{"url":<data URI>}}`
    part per image (the data URI is assembled here from mime + base64), followed by a single
    `{"type":"text"}` part. Images are DROPPED (debug log, never the base64 payload) when `vision`
    is False.
    """
    if not message.images:
        return message.content
    if not vision:
        logger.debug(
            "OpenAI-compatible chat: dropping %d image(s) — instance is not vision-capable (B2-C1)",
            len(message.images),
        )
        return message.content
    parts: list[dict[str, object]] = [
        {"type": "image_url", "image_url": {"url": f"data:{img.mime};base64,{img.data_base64}"}}
        for img in message.images
    ]
    parts.append({"type": "text", "text": message.content})
    return parts


class ApiProvider(InferenceProvider):
    """Anthropic Messages API or OpenAI-compatible endpoint (orchestrated route)."""

    def __init__(self, config: ProviderSettings) -> None:
        self._config = config
        self._model = config.model_id  # from provider_config — never hardcoded (I6)
        self._timeout = config.timeout
        self._reasoning_effort = config.reasoning_effort  # W1 (F17); None/auto/off ⇒ no override
        self._price_map = _load_price_map()
        # OpenAI-compatible iff base_url is set; otherwise Anthropic-native.
        self._openai_compatible = bool(config.base_url)
        # Azure OpenAI is detected from the base_url host (llm_wiki parity) and routed on a
        # distinct wire (api-key header, /openai/deployments/<dep>/chat/completions?api-version).
        self._is_azure = self._openai_compatible and _is_azure_endpoint(config.base_url)
        self._azure_base_url_raw = config.base_url or ""
        if self._openai_compatible:
            assert config.base_url is not None
            self._base_url = config.base_url.rstrip("/")
        else:
            self._base_url = os.environ.get(
                _ANTHROPIC_BASE_ENV, "https://api.anthropic.com"
            ).rstrip("/")

    # ── OpenAI-compatible wire helpers (generic Bearer OR Azure api-key) ──────────

    def _openai_post_url(self) -> str:
        """chat/completions URL — Azure deployment URL when the endpoint is Azure, else generic."""
        if self._is_azure:
            return _azure_chat_url(self._azure_base_url_raw, self._model)
        return f"{self._base_url}/chat/completions"

    def _openai_headers(self, api_key: str) -> dict[str, str]:
        """Auth headers — Azure uses `api-key`; every other OpenAI-compatible host uses Bearer."""
        if self._is_azure:
            return {"api-key": api_key, "content-type": "application/json"}
        return {"authorization": f"Bearer {api_key}", "content-type": "application/json"}

    def _finalize_openai_body(self, body: dict[str, object]) -> dict[str, object]:
        """Azure carries the deployment in the URL and rejects a body `model` — drop it there."""
        if self._is_azure:
            body.pop("model", None)
        return body

    # ── Capabilities ─────────────────────────────────────────────────────────────

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="api",
            supports_tools=True,
            supports_agentic_loop=False,
            max_context=_DEFAULT_MAX_CONTEXT,
            name="ApiProvider",
            supports_vision=self._supports_vision(),
        )

    def _supports_vision(self) -> bool:
        """
        R8-2: the Anthropic Messages API always supports image content blocks (True). For an
        OpenAI-compatible endpoint (base_url set) vision is model-dependent, so it is opt-in via
        the provider_config `supports_vision` flag (default False when unset).
        """
        if not self._openai_compatible:
            return True
        return bool(self._config.supports_vision)

    # ── Secrets (W1 / F17, §12 amendment) ────────────────────────────────────────

    def _anthropic_key(self) -> str:
        """
        Resolve the Anthropic key: DECRYPTED UI key from provider_config wins, else the
        ANTHROPIC_API_KEY env (I6 — all 3 backends keep working; §12 amendment). NEVER logged.
        """
        key = self._config.api_key or os.environ.get(_ANTHROPIC_KEY_ENV)
        if not key:
            raise ValueError(
                f"No Anthropic API key: set one in provider_config (UI) or {_ANTHROPIC_KEY_ENV} "
                "in the environment (§12, ADR-0008)"
            )
        return key

    def _openai_key(self) -> str:
        """
        Resolve the OpenAI-compatible key: DECRYPTED UI key from provider_config wins, else the
        OPENAI_API_KEY env (I6; §12 amendment). NEVER logged.
        """
        key = self._config.api_key or os.environ.get(_OPENAI_KEY_ENV)
        if not key:
            raise ValueError(
                "No OpenAI-compatible API key: set one in provider_config (UI) or "
                f"{_OPENAI_KEY_ENV} in the environment (§12, ADR-0008)"
            )
        return key

    # ── Reasoning/thinking (W1 / F17) ────────────────────────────────────────────

    def _apply_reasoning(self, body: dict[str, object], *, anthropic: bool) -> None:
        """
        Thread the per-provider reasoning_effort into the request body where supported.

        Degrade-safe: None/"auto"/"off"/"custom" ⇒ no-op (request unchanged, so endpoints that
        do not support reasoning are never sent an unknown field). Anthropic ⇒ extended-thinking
        block (max_tokens bumped above the thinking budget). OpenAI-compatible ⇒ reasoning_effort.
        """
        effort = self._reasoning_effort
        if not effort or effort in {"auto", "off", "custom"}:
            return
        if anthropic:
            budget = _ANTHROPIC_THINKING_BUDGET.get(effort)
            if budget is None:
                return
            body["thinking"] = {"type": "enabled", "budget_tokens": budget}
            raw_max = body.get("max_tokens", _DEFAULT_MAX_TOKENS)
            current_max = raw_max if isinstance(raw_max, int) else _DEFAULT_MAX_TOKENS
            if current_max <= budget:
                body["max_tokens"] = budget + 1024
        else:
            mapped = _OPENAI_REASONING_EFFORT.get(effort)
            if mapped is not None:
                body["reasoning_effort"] = mapped

    # ── LLM calls ────────────────────────────────────────────────────────────────

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        raw = await self._complete(
            system=ANALYZE_SYSTEM,
            user=build_analyze_prompt(source_text, vault_context),
        )
        return parse_analysis(raw)

    async def generate(
        self, analysis: Analysis, retrieval_context: str, source_text: str = ""
    ) -> list[WikiPage]:
        raw = await self._complete(
            system=GENERATE_SYSTEM,
            # D1 (ADR-0063 §9): thread the budget-trimmed source into generation (I6 — the source
            # travels via the shared provider-neutral builder, not provider-branching code).
            user=build_generate_prompt(analysis, retrieval_context, source_text),
        )
        return parse_pages(raw)

    async def chat(self, messages: list[Message], retrieval_context: str) -> AsyncIterator[str]:
        """
        Stream a chat turn (F6, ADR-0019). Anthropic Messages SSE OR OpenAI-compatible SSE,
        chosen by base_url (I6 — a backend is config). `retrieval_context` is the light system
        context (purpose.md + overview.md, §2.3), injected as the system prompt. Yields raw text
        deltas verbatim (NO server-side parse — I3); usage recorded out of band at stream end.

        NB: not runnable in dev (no ANTHROPIC_API_KEY) — Local/Ollama is the working dev path
        (ADR-0019 §1). Built per ADR build-order step 3 so the API path is parity-complete.
        """
        if self._openai_compatible:
            return self._chat_stream_openai(messages, retrieval_context)
        return self._chat_stream_anthropic(messages, retrieval_context)

    async def _chat_stream_anthropic(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:
        api_key = self._anthropic_key()
        # B2-C1: images are carried only when vision-capable (Anthropic path is always True);
        # otherwise dropped. NEVER log the base64 payload.
        vision = self._supports_vision()
        # Anthropic takes system as a top-level field; user/assistant turns in messages[].
        anthropic_messages = [
            {"role": m.role, "content": _anthropic_content(m, vision)}
            for m in messages
            if m.role != "system"
        ]
        body: dict[str, object] = {
            "model": self._model,  # from provider_config (I6)
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "stream": True,
            "messages": anthropic_messages,
        }
        if retrieval_context.strip():
            body["system"] = retrieval_context
        self._apply_reasoning(body, anthropic=True)  # W1 (F17); no-op unless opted in
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        in_tok = 0
        out_tok = 0
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST", f"{self._base_url}/v1/messages", json=body, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    async for sse_line in resp.aiter_lines():
                        if not sse_line.startswith("data:"):
                            continue
                        data = sse_line[len("data:") :].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            evt = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        etype = evt.get("type")
                        if etype == "message_start":
                            usage = evt.get("message", {}).get("usage", {})
                            in_tok = int(usage.get("input_tokens", 0) or 0)
                        elif etype == "content_block_delta":
                            delta = evt.get("delta", {})
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    yield text
                        elif etype == "message_delta":
                            usage = evt.get("usage", {})
                            out_tok = int(usage.get("output_tokens", out_tok) or out_tok)
        finally:
            self._record_usage(
                Usage(
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    total_cost_usd=self._cost(in_tok, out_tok),
                )
            )

    async def _chat_stream_openai(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:
        api_key = self._openai_key()
        # B2-C1: images are carried only when this instance is vision-capable (config flag on the
        # OpenAI-compatible path); otherwise dropped. NEVER log the base64 payload.
        vision = self._supports_vision()
        openai_messages: list[dict[str, object]] = []
        if retrieval_context.strip():
            openai_messages.append({"role": "system", "content": retrieval_context})
        openai_messages.extend(
            {"role": m.role, "content": _openai_content(m, vision)} for m in messages
        )
        body: dict[str, object] = {
            "model": self._model,  # from provider_config (I6)
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        self._apply_reasoning(body, anthropic=False)  # W1 (F17); no-op unless opted in
        self._finalize_openai_body(body)
        headers = self._openai_headers(api_key)

        in_tok = 0
        out_tok = 0
        # R7-10(a): DeepSeek/Qwen-family reasoning models (served OpenAI-compatibly) stream their
        # chain-of-thought in a SEPARATE delta field — DeepSeek uses `reasoning_content`, Qwen uses
        # `reasoning` — BEFORE the visible `content`. We wrap that reasoning in <think>…</think> so
        # the shared server-side ThinkScanner routes it to the ThinkBlock exactly like a native
        # <think> tag (no per-token parse here — I3; the scanner runs downstream). We open the
        # span on the first reasoning delta and close it when visible content begins.
        think_open = False
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST", self._openai_post_url(), json=body, headers=headers
                ) as resp:
                    resp.raise_for_status()
                    async for sse_line in resp.aiter_lines():
                        if not sse_line.startswith("data:"):
                            continue
                        data = sse_line[len("data:") :].strip()
                        if not data or data == "[DONE]":
                            continue
                        try:
                            evt = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = evt.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            # Vendor reasoning field (DeepSeek: reasoning_content, Qwen: reasoning).
                            reasoning = delta.get("reasoning_content")
                            if reasoning is None:
                                reasoning = delta.get("reasoning")
                            if reasoning:
                                if not think_open:
                                    think_open = True
                                    yield "<think>"
                                yield reasoning
                            text = delta.get("content", "")
                            if text:
                                if think_open:
                                    think_open = False
                                    yield "</think>"
                                yield text
                        usage = evt.get("usage")
                        if isinstance(usage, dict):
                            in_tok = int(usage.get("prompt_tokens", in_tok) or in_tok)
                            out_tok = int(usage.get("completion_tokens", out_tok) or out_tok)
            # Close a still-open think span if the stream ended with only reasoning (defensive).
            if think_open:
                yield "</think>"
        finally:
            self._record_usage(
                Usage(
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    total_cost_usd=self._cost(in_tok, out_tok),
                )
            )

    # ── Vision (R8-2 / F12) ────────────────────────────────────────────────────

    async def caption_image(self, path_or_bytes: str | Path | bytes, context: str) -> str:
        """
        Caption an image via the Anthropic Messages API image block (base64) or an OpenAI-compatible
        vision endpoint (image_url data URI), chosen by base_url (I6). One bounded non-streaming
        call; Usage recorded out of band (I7). Raises NotImplementedError when this instance is not
        vision-capable so the orchestrator falls back to the placeholder (R8-2).
        """
        if not self._supports_vision():
            raise NotImplementedError(
                "ApiProvider (OpenAI-compatible) has supports_vision=False; set the "
                "provider_config supports_vision flag to enable captioning (R8-2)"
            )
        data, media_type = resolve_image_bytes_and_media_type(path_or_bytes)
        b64 = encode_image_base64(data)
        prompt = f"{context}\n\n{CAPTION_INSTRUCTION}" if context.strip() else CAPTION_INSTRUCTION
        if self._openai_compatible:
            return await self._caption_openai(b64, media_type, prompt)
        return await self._caption_anthropic(b64, media_type, prompt)

    async def _caption_anthropic(self, b64: str, media_type: str, prompt: str) -> str:
        api_key = self._anthropic_key()
        body = {
            "model": self._model,  # from provider_config (I6)
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/v1/messages", json=body, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        usage = payload.get("usage", {})
        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
        self._record_usage(
            Usage(
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_cost_usd=self._cost(in_tok, out_tok),
            )
        )
        blocks = payload.get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not text.strip():
            raise ValueError("Anthropic vision returned empty caption")
        return text.strip()

    async def _caption_openai(self, b64: str, media_type: str, prompt: str) -> str:
        api_key = self._openai_key()
        data_uri = f"data:{media_type};base64,{b64}"
        body = {
            "model": self._model,  # from provider_config (I6)
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        }
        self._finalize_openai_body(body)
        headers = self._openai_headers(api_key)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._openai_post_url(), json=body, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
        usage = payload.get("usage", {})
        in_tok = int(usage.get("prompt_tokens", 0) or 0)
        out_tok = int(usage.get("completion_tokens", 0) or 0)
        self._record_usage(
            Usage(
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_cost_usd=self._cost(in_tok, out_tok),
            )
        )
        choices = payload.get("choices", [])
        if not choices:
            raise ValueError("OpenAI-compatible vision endpoint returned no choices")
        content = choices[0].get("message", {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("OpenAI-compatible vision endpoint returned empty caption")
        return content.strip()

    # ── Cost ─────────────────────────────────────────────────────────────────────

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        prices = self._price_map.get(self._model)
        if not prices:
            return 0.0
        return input_tokens * float(prices.get("input", 0.0)) + output_tokens * float(
            prices.get("output", 0.0)
        )

    # ── Transport ────────────────────────────────────────────────────────────────

    async def _complete(self, *, system: str, user: str) -> str:
        if self._openai_compatible:
            return await self._complete_openai(system=system, user=user)
        return await self._complete_anthropic(system=system, user=user)

    async def _complete_anthropic(self, *, system: str, user: str) -> str:
        api_key = self._anthropic_key()
        body: dict[str, object] = {
            "model": self._model,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        self._apply_reasoning(body, anthropic=True)  # W1 (F17); no-op unless opted in
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/v1/messages", json=body, headers=headers)
            resp.raise_for_status()
            payload = resp.json()

        usage = payload.get("usage", {})
        in_tok = int(usage.get("input_tokens", 0) or 0)
        out_tok = int(usage.get("output_tokens", 0) or 0)
        self._record_usage(
            Usage(
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_cost_usd=self._cost(in_tok, out_tok),
            )
        )
        blocks = payload.get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if not text.strip():
            raise ValueError("Anthropic returned empty content")
        # Truncation detection: when the model hits the output cap it stops with
        # stop_reason="max_tokens" and the JSON body is cut off mid-object → the
        # downstream json.loads surfaces a cryptic "Expecting ',' delimiter". Turn
        # that into an ACTIONABLE error naming the exact remedy (Haiku 4.5 accepts
        # up to 64000). Checked AFTER the empty guard so a truncated-but-nonempty
        # body still reports truncation rather than being parsed.
        if payload.get("stop_reason") == "max_tokens":
            raise ValueError(
                f"generation truncated at max_tokens={_DEFAULT_MAX_TOKENS} — the source is too "
                f"rich to fit one response. Raise PROVIDER_MAX_OUTPUT_TOKENS "
                f"(Claude Haiku 4.5 accepts up to 64000)."
            )
        return text

    async def _complete_openai(self, *, system: str, user: str) -> str:
        api_key = self._openai_key()
        body: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        self._apply_reasoning(body, anthropic=False)  # W1 (F17); no-op unless opted in
        self._finalize_openai_body(body)
        headers = self._openai_headers(api_key)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._openai_post_url(), json=body, headers=headers)
            resp.raise_for_status()
            payload = resp.json()

        usage = payload.get("usage", {})
        in_tok = int(usage.get("prompt_tokens", 0) or 0)
        out_tok = int(usage.get("completion_tokens", 0) or 0)
        self._record_usage(
            Usage(
                input_tokens=in_tok,
                output_tokens=out_tok,
                total_cost_usd=self._cost(in_tok, out_tok),
            )
        )
        choices = payload.get("choices", [])
        if not choices:
            raise ValueError("OpenAI-compatible endpoint returned no choices")
        content = choices[0].get("message", {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("OpenAI-compatible endpoint returned empty content")
        # Truncation detection (OpenAI wire): finish_reason="length" means the output
        # cap was hit and the JSON body is cut off → actionable error instead of the
        # cryptic downstream json.loads failure.
        if choices[0].get("finish_reason") == "length":
            raise ValueError(
                f"generation truncated at max_tokens={_DEFAULT_MAX_TOKENS} — the source is too "
                f"rich to fit one response. Raise PROVIDER_MAX_OUTPUT_TOKENS."
            )
        return content
