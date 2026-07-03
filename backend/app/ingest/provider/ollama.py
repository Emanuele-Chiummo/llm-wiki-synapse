"""
OllamaProvider — Local backend (F17, I9). Reuses the already-running Ollama on the RTX 3060
via its `/api/chat` endpoint with `format=json` for structured output.

Invariants:
  - I6: model id comes from ProviderSettings (provider_config), NEVER hardcoded. The Ollama
    base URL comes from OLLAMA_URL in the environment, confined to this module.
  - I9: reuses Ollama; no new inference service.
  - ADR-0009: Usage.input_tokens = prompt_eval_count, output_tokens = eval_count,
    total_cost_usd = 0.0 always (zero marginal cost, local GPU).
  - capabilities(): supports_agentic_loop=False → orchestrated route.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

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

# Ollama endpoint from env only (I6/I9) — never a literal URL in app code.
_OLLAMA_URL_ENV = "OLLAMA_URL"
_DEFAULT_MAX_CONTEXT = 8192

# R8-2 / F12: Ollama vision support is model-dependent. A pulled model advertises vision only if
# its name matches a known vision family OR is listed in OLLAMA_VISION_MODELS (comma-separated,
# substring match, case-insensitive). Default OFF for an arbitrary text model (PM safety).
_OLLAMA_VISION_MODELS_ENV = "OLLAMA_VISION_MODELS"
_DEFAULT_VISION_MODEL_MARKERS: tuple[str, ...] = ("llava", "bakllava", "vision", "minicpm-v")


def _model_supports_vision(model_id: str) -> bool:
    """
    True if *model_id* looks like an Ollama vision model (R8-2). Matches the built-in family
    markers (llava / bakllava / vision / minicpm-v) OR any comma-separated token in the
    OLLAMA_VISION_MODELS env (substring, case-insensitive). Env-sourced, never a literal-model
    list baked into a routing decision (I6 — this only advertises a capability).
    """
    name = (model_id or "").lower()
    markers = list(_DEFAULT_VISION_MODEL_MARKERS)
    extra = os.environ.get(_OLLAMA_VISION_MODELS_ENV, "")
    markers.extend(tok.strip().lower() for tok in extra.split(",") if tok.strip())
    return any(marker and marker in name for marker in markers)


# ── num_ctx derivation (BUG A1) ──────────────────────────────────────────────────
# Ollama defaults options.num_ctx to 4096 and SILENTLY truncates anything longer — long
# sources + vault context + retry augmentation get cut, causing non-convergence on small
# models. We therefore pass an explicit options.num_ctx derived from the configured context
# window. These are named bounds, not magic literals scattered around.
#
#   _NUM_CTX_FLOOR   — never request less than this (keeps room for source + context + retries).
#   _NUM_CTX_DEFAULT — used when the provider_config gives no usable context/budget hint.
#   _NUM_CTX_CEILING — clamp so a misconfigured huge budget cannot blow out VRAM (RTX 3060, 12GB).
_NUM_CTX_FLOOR = 8192
_NUM_CTX_DEFAULT = 32768
_NUM_CTX_CEILING = 131072


def _derive_num_ctx(config: ProviderSettings) -> int:
    """
    Derive Ollama ``options.num_ctx`` from the provider config (BUG A1).

    Prefers the configured ``token_budget`` (the orchestrated-loop context window the user
    actually selected, e.g. 60000). Falls back to ``_NUM_CTX_DEFAULT`` when unset/non-positive,
    then clamps into ``[_NUM_CTX_FLOOR, _NUM_CTX_CEILING]`` so we never under-provision (the
    4096 truncation bug) nor over-provision past the model ceiling.
    """
    configured = int(getattr(config, "token_budget", 0) or 0)
    n = configured if configured > 0 else _NUM_CTX_DEFAULT
    return max(_NUM_CTX_FLOOR, min(n, _NUM_CTX_CEILING))


class OllamaProvider(InferenceProvider):
    """Local Ollama backend using /api/chat with format=json (orchestrated route)."""

    def __init__(self, config: ProviderSettings) -> None:
        self._config = config
        # base_url precedence: explicit provider_config base_url → OLLAMA_URL env.
        self._base_url = (config.base_url or os.environ.get(_OLLAMA_URL_ENV, "")).rstrip("/")
        if not self._base_url:
            raise ValueError(
                "OllamaProvider requires a base_url (provider_config) or OLLAMA_URL env (I6/I9)"
            )
        self._model = config.model_id  # from provider_config — never hardcoded (I6)
        self._timeout = config.timeout
        # Explicit context window for every Ollama call (BUG A1) — derived from config,
        # NOT Ollama's silent 4096 default. Shared by analyze()/generate()/chat().
        self._num_ctx = _derive_num_ctx(config)

    # ── Capabilities ─────────────────────────────────────────────────────────────

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="local",
            supports_tools=False,  # model-dependent; conservatively False until /api/show check
            supports_agentic_loop=False,
            max_context=_DEFAULT_MAX_CONTEXT,
            name="OllamaProvider",
            # R8-2: vision only if the configured model is a known/declared vision model.
            supports_vision=_model_supports_vision(self._model),
        )

    # ── LLM calls ────────────────────────────────────────────────────────────────

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        raw = await self._chat_json(
            system=ANALYZE_SYSTEM,
            user=build_analyze_prompt(source_text, vault_context),
        )
        return parse_analysis(raw)

    async def generate(self, analysis: Analysis, retrieval_context: str) -> list[WikiPage]:
        raw = await self._chat_json(
            system=GENERATE_SYSTEM,
            user=build_generate_prompt(analysis, retrieval_context),
        )
        return parse_pages(raw)

    async def chat(self, messages: list[Message], retrieval_context: str) -> AsyncIterator[str]:
        """
        Stream a chat turn via Ollama /api/chat with stream=true (F6, ADR-0019 §2.4 transport).

        `retrieval_context` is the light system context (purpose.md + overview.md) built by the
        chat service (ADR-0019 §2.3); it is injected as a leading system message. Yields raw
        content deltas verbatim (NO server-side parse — I3); the chat service runs the <think>
        scanner over them. Usage (prompt_eval_count / eval_count) is recorded out of band when
        Ollama emits the terminal done object (total_cost_usd = 0.0, local, ADR-0009).
        """
        return self._chat_stream(messages, retrieval_context)

    async def _chat_stream(
        self, messages: list[Message], retrieval_context: str
    ) -> AsyncIterator[str]:
        ollama_messages: list[dict[str, str]] = []
        if retrieval_context.strip():
            ollama_messages.append({"role": "system", "content": retrieval_context})
        ollama_messages.extend({"role": m.role, "content": m.content} for m in messages)

        body = {
            "model": self._model,  # from provider_config — never hardcoded (I6)
            "stream": True,
            "messages": ollama_messages,
            # Explicit context window (BUG A1) — without this Ollama truncates to 4096.
            "options": {"num_ctx": self._num_ctx},
        }

        in_tok = 0
        out_tok = 0
        # R7-10(a): reasoning models served by Ollama (e.g. DeepSeek/Qwen) stream their
        # chain-of-thought in message.thinking BEFORE message.content. Wrap it in <think>…</think>
        # so the shared server-side ThinkScanner routes it to the ThinkBlock (no per-token parse —
        # I3; the scanner runs downstream). Open on first thinking delta, close when content begins.
        think_open = False
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream("POST", f"{self._base_url}/api/chat", json=body) as resp:
                    resp.raise_for_status()
                    async for raw_line in resp.aiter_lines():
                        if not raw_line.strip():
                            continue
                        try:
                            obj = json.loads(raw_line)
                        except json.JSONDecodeError:
                            logger.warning("Ollama chat: skipping non-JSON stream line")
                            continue
                        message = obj.get("message", {})
                        thinking = message.get("thinking", "")
                        if thinking:
                            if not think_open:
                                think_open = True
                                yield "<think>"
                            yield thinking
                        delta = message.get("content", "")
                        if delta:
                            if think_open:
                                think_open = False
                                yield "</think>"
                            yield delta
                        if obj.get("done"):
                            in_tok = int(obj.get("prompt_eval_count", 0) or 0)
                            out_tok = int(obj.get("eval_count", 0) or 0)
            # Close a still-open think span if the stream ended with only thinking (defensive).
            if think_open:
                yield "</think>"
        finally:
            # Record whatever usage we observed, even on early aclose() (token_budget cap) or
            # mid-stream error — keeps the I7 cost ledger truthful (cost 0.0, local).
            self._record_usage(
                Usage(input_tokens=in_tok, output_tokens=out_tok, total_cost_usd=0.0)
            )

    # ── Vision (R8-2 / F12) ────────────────────────────────────────────────────

    async def caption_image(self, path_or_bytes: str | Path | bytes, context: str) -> str:
        """
        Caption an image via Ollama /api/chat with a base64 `images` field (R8-2). One bounded
        non-streaming call; Usage recorded out of band (cost 0.0 local, I7). Raises
        NotImplementedError when the configured model is not a vision model so the orchestrator
        falls back to the placeholder (R8-2).
        """
        if not _model_supports_vision(self._model):
            raise NotImplementedError(
                f"Ollama model {self._model!r} is not a vision model; pull a vision model "
                "(llava/minicpm-v/…) or set OLLAMA_VISION_MODELS to enable captioning (R8-2)"
            )
        data, _media_type = resolve_image_bytes_and_media_type(path_or_bytes)
        b64 = encode_image_base64(data)
        prompt = f"{context}\n\n{CAPTION_INSTRUCTION}" if context.strip() else CAPTION_INSTRUCTION
        body = {
            "model": self._model,  # from provider_config (I6)
            "stream": False,
            "messages": [{"role": "user", "content": prompt, "images": [b64]}],
            "options": {"num_ctx": self._num_ctx},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/api/chat", json=body)
            resp.raise_for_status()
            payload = resp.json()
        self._record_usage(
            Usage(
                input_tokens=int(payload.get("prompt_eval_count", 0) or 0),
                output_tokens=int(payload.get("eval_count", 0) or 0),
                total_cost_usd=0.0,
            )
        )
        content = payload.get("message", {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama vision returned an empty caption")
        return content.strip()

    # ── Internal: /api/chat with format=json + Usage accounting ─────────────────

    async def _chat_json(self, *, system: str, user: str) -> str:
        body = {
            "model": self._model,
            "format": "json",
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Explicit context window (BUG A1) — applies to analyze() and generate(); without
            # this Ollama silently caps context at 4096 and truncates long sources + retries.
            "options": {"num_ctx": self._num_ctx},
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(f"{self._base_url}/api/chat", json=body)
            resp.raise_for_status()
            payload = resp.json()

        # Usage from native Ollama fields (ADR-0009). total_cost_usd = 0.0 (local).
        self._record_usage(
            Usage(
                input_tokens=int(payload.get("prompt_eval_count", 0) or 0),
                output_tokens=int(payload.get("eval_count", 0) or 0),
                total_cost_usd=0.0,
            )
        )
        content = payload.get("message", {}).get("content", "")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Ollama returned an empty message content")
        return content
