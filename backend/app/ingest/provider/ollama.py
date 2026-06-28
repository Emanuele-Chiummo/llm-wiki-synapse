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

import logging
import os
from collections.abc import AsyncIterator

import httpx

from app.ingest.provider._common import (
    ANALYZE_SYSTEM,
    GENERATE_SYSTEM,
    build_analyze_prompt,
    build_generate_prompt,
    parse_analysis,
    parse_pages,
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

    # ── Capabilities ─────────────────────────────────────────────────────────────

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="local",
            supports_tools=False,  # model-dependent; conservatively False until /api/show check
            supports_agentic_loop=False,
            max_context=_DEFAULT_MAX_CONTEXT,
            name="OllamaProvider",
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
        # Stubbed in v0.2 (ADR-0007 §6); real streaming lands in v0.4 (F6).
        raise NotImplementedError("OllamaProvider.chat() is implemented in v0.4 (F6)")

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
