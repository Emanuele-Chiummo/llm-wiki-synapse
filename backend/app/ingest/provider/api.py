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

_ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"
_OPENAI_KEY_ENV = "OPENAI_API_KEY"
_ANTHROPIC_BASE_ENV = "ANTHROPIC_BASE_URL"
_PRICE_MAP_ENV = "PROVIDER_PRICE_MAP"  # JSON: {model_id: {input: usd_per_tok, output: ...}}
_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_CONTEXT = 200_000
_DEFAULT_MAX_TOKENS = 4096


def _load_price_map() -> dict[str, dict[str, float]]:
    """
    Load the per-model price map from PROVIDER_PRICE_MAP (USD per token, keyed by model_id).

    Prices live in env-sourced config, NEVER as literals in app code (AC-F17-8, ADR-0009).
    Absent/malformed → empty map → cost computed as 0.0 with a one-time warning.
    """
    raw = os.environ.get(_PRICE_MAP_ENV)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        logger.warning("PROVIDER_PRICE_MAP is not valid JSON — cost will be recorded as 0.0")
    return {}


class ApiProvider(InferenceProvider):
    """Anthropic Messages API or OpenAI-compatible endpoint (orchestrated route)."""

    def __init__(self, config: ProviderSettings) -> None:
        self._config = config
        self._model = config.model_id  # from provider_config — never hardcoded (I6)
        self._timeout = config.timeout
        self._price_map = _load_price_map()
        # OpenAI-compatible iff base_url is set; otherwise Anthropic-native.
        self._openai_compatible = bool(config.base_url)
        if self._openai_compatible:
            assert config.base_url is not None
            self._base_url = config.base_url.rstrip("/")
        else:
            self._base_url = os.environ.get(
                _ANTHROPIC_BASE_ENV, "https://api.anthropic.com"
            ).rstrip("/")

    # ── Capabilities ─────────────────────────────────────────────────────────────

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            mode="api",
            supports_tools=True,
            supports_agentic_loop=False,
            max_context=_DEFAULT_MAX_CONTEXT,
            name="ApiProvider",
        )

    # ── LLM calls ────────────────────────────────────────────────────────────────

    async def analyze(self, source_text: str, vault_context: str) -> Analysis:
        raw = await self._complete(
            system=ANALYZE_SYSTEM,
            user=build_analyze_prompt(source_text, vault_context),
        )
        return parse_analysis(raw)

    async def generate(self, analysis: Analysis, retrieval_context: str) -> list[WikiPage]:
        raw = await self._complete(
            system=GENERATE_SYSTEM,
            user=build_generate_prompt(analysis, retrieval_context),
        )
        return parse_pages(raw)

    async def chat(self, messages: list[Message], retrieval_context: str) -> AsyncIterator[str]:
        raise NotImplementedError("ApiProvider.chat() is implemented in v0.4 (F6)")

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
        api_key = os.environ.get(_ANTHROPIC_KEY_ENV)
        if not api_key:
            raise ValueError(f"{_ANTHROPIC_KEY_ENV} not set in environment (§12, ADR-0008)")
        body = {
            "model": self._model,
            "max_tokens": _DEFAULT_MAX_TOKENS,
            "system": system,
            "messages": [{"role": "user", "content": user}],
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
            raise ValueError("Anthropic returned empty content")
        return text

    async def _complete_openai(self, *, system: str, user: str) -> str:
        api_key = os.environ.get(_OPENAI_KEY_ENV)
        if not api_key:
            raise ValueError(f"{_OPENAI_KEY_ENV} not set in environment (§12, ADR-0008)")
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {
            "authorization": f"Bearer {api_key}",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/chat/completions", json=body, headers=headers
            )
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
        return content
