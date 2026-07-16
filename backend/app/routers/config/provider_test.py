"""Per-domain config router: /provider/vendors + /provider/test/* probes (W1).

Split out of the monolithic app.routers.config (BE-REFAC-1). Same paths/contract.
"""

from __future__ import annotations

import logging
import os
import time

import httpx
from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app import cli_auth as _cli_auth
from app import runtime_state, secrets_crypto
from app.models import ProviderConfig
from app.provider_vendors import VENDORS
from app.schemas.config import (
    _VALID_PROVIDER_TYPES,
    ProviderTestRequest,
    ProviderTestResponse,
    VendorListResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# W1 (F17): bounded provider-test knobs (I7). Short wall-clock timeout + tiny token cap so a
# connection/function probe can never run away. Both overridable via env for slow gateways.
_PROVIDER_TEST_TIMEOUT_S = float(os.environ.get("PROVIDER_TEST_TIMEOUT_SECONDS", "15"))

_PROVIDER_TEST_MAX_TOKENS = int(os.environ.get("PROVIDER_TEST_MAX_TOKENS", "16"))

_ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"

_OPENAI_KEY_ENV = "OPENAI_API_KEY"

_OLLAMA_URL_ENV = "OLLAMA_URL"

_ANTHROPIC_BASE_ENV = "ANTHROPIC_BASE_URL"

_ANTHROPIC_VERSION = "2023-06-01"


@router.get(
    "/provider/vendors",
    response_model=VendorListResponse,
    summary="List the provider vendor catalog",
    description=(
        "Returns the curated one-row-per-vendor catalog for the Settings 'LLM Models' UI (W1). "
        "Each entry carries id, display_name, provider_type (api|local|cli), default_base_url, "
        "needs_api_key, model_presets, and notes. Static — no secrets, no DB. (F17, W1)"
    ),
)
async def list_provider_vendors() -> VendorListResponse:
    """GET /provider/vendors — the static vendor catalog (W1). No secrets, no DB read."""
    return VendorListResponse(vendors=list(VENDORS))


async def _resolve_probe_target(
    body: ProviderTestRequest,
) -> tuple[str, str | None, str, str | None]:
    """
    Resolve (provider_type, base_url, model, api_key) for a probe from body/config_id.

    Inline body fields win over the stored row. Key precedence: inline api_key > decrypted
    stored key > env-var key (ANTHROPIC/OPENAI by path). Raises HTTP 422 when neither a
    resolvable config_id nor an inline {provider_type, model} is supplied. NEVER logs the key.
    """
    provider_type = body.provider_type
    base_url = body.base_url
    model = body.model
    stored_encrypted: bytes | None = None

    if body.config_id is not None:
        async with runtime_state.get_session() as session:
            result = await session.execute(
                select(ProviderConfig).where(ProviderConfig.id == body.config_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"provider_config {body.config_id} not found"
                )
            provider_type = provider_type or row.provider_type
            base_url = base_url if body.base_url is not None else row.base_url
            model = model or row.model_id
            stored_encrypted = row.api_key_encrypted

    if not provider_type or not model:
        raise HTTPException(
            status_code=422,
            detail="provide a config_id, or inline provider_type + model",
        )
    if provider_type not in _VALID_PROVIDER_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"provider_type must be one of {sorted(_VALID_PROVIDER_TYPES)}",
        )

    api_key = _resolve_probe_key(provider_type, base_url, body.api_key, stored_encrypted)
    return provider_type, base_url, model, api_key


def _resolve_probe_key(
    provider_type: str, base_url: str | None, inline_key: str | None, stored: bytes | None
) -> str | None:
    """Key precedence for a probe: inline > decrypted stored > env-var. NEVER logged."""
    if inline_key:
        return inline_key
    if stored:
        try:
            return secrets_crypto.decrypt(bytes(stored))
        except (secrets_crypto.SecretsNotConfiguredError, secrets_crypto.InvalidToken):
            pass
    if provider_type == "api":
        return os.environ.get(_OPENAI_KEY_ENV if base_url else _ANTHROPIC_KEY_ENV)
    return None


async def _one_shot_chat(
    provider_type: str, base_url: str | None, model: str, api_key: str | None, instruction: str
) -> str:
    """
    Perform ONE bounded chat call and return the assistant text (W1, I7).

    Token-capped (_PROVIDER_TEST_MAX_TOKENS) and timeout-bounded (_PROVIDER_TEST_TIMEOUT_S).
    Dispatch by provider_type: api+base_url ⇒ OpenAI-compatible; api ⇒ Anthropic-native;
    local ⇒ Ollama. NEVER logs or returns the key.
    """
    timeout = _PROVIDER_TEST_TIMEOUT_S
    messages = [{"role": "user", "content": instruction}]

    if provider_type == "api" and base_url:
        if not api_key:
            raise ValueError("no API key resolved (inline, stored, or env)")
        req_body = {"model": model, "messages": messages, "max_tokens": _PROVIDER_TEST_MAX_TOKENS}
        headers = {"authorization": f"Bearer {api_key}", "content-type": "application/json"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base_url.rstrip('/')}/chat/completions", json=req_body, headers=headers
            )
            resp.raise_for_status()
            payload = resp.json()
        choices = payload.get("choices", [])
        return str(choices[0].get("message", {}).get("content", "")) if choices else ""

    if provider_type == "api":
        if not api_key:
            raise ValueError("no API key resolved (inline, stored, or env)")
        anthropic_base = os.environ.get(_ANTHROPIC_BASE_ENV, "https://api.anthropic.com").rstrip(
            "/"
        )
        req_body = {"model": model, "max_tokens": _PROVIDER_TEST_MAX_TOKENS, "messages": messages}
        headers = {
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{anthropic_base}/v1/messages", json=req_body, headers=headers
            )
            resp.raise_for_status()
            payload = resp.json()
        blocks = payload.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    if provider_type == "local":
        ollama_base = (base_url or os.environ.get(_OLLAMA_URL_ENV, "")).rstrip("/")
        if not ollama_base:
            raise ValueError("no Ollama base URL (set base_url or OLLAMA_URL)")
        req_body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"num_predict": _PROVIDER_TEST_MAX_TOKENS},
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{ollama_base}/api/chat", json=req_body)
            resp.raise_for_status()
            payload = resp.json()
        return str(payload.get("message", {}).get("content", ""))

    # Should be unreachable (cli handled before this call).
    raise ValueError(f"unsupported provider_type for probe: {provider_type!r}")


async def _run_probe(
    body: ProviderTestRequest, *, instruction: str, require_ok: bool
) -> ProviderTestResponse:
    """
    Shared bounded probe for the connection/function endpoints (W1, I7).

    connection (require_ok=False): ok iff the endpoint returned a successful response.
    function   (require_ok=True):  ok iff the reply contains "ok" (case-insensitive).
    CLI is not live-probed (cheap posture check via cli_auth). NEVER echoes the key.
    """
    provider_type, base_url, model, api_key = await _resolve_probe_target(body)

    if provider_type == "cli":
        configured = _cli_auth._cli_auth_config_cache.token_configured()
        return ProviderTestResponse(
            ok=configured,
            latency_ms=0,
            detail=(
                "CLI credentials present (no live probe run for the agentic CLI backend)"
                if configured
                else "no CLI credentials configured (set the CLI subscription token or env)"
            ),
        )

    start = time.monotonic()
    try:
        text = await _one_shot_chat(provider_type, base_url, model, api_key, instruction)
    except httpx.TimeoutException:
        elapsed = int((time.monotonic() - start) * 1000)
        return ProviderTestResponse(
            ok=False, latency_ms=elapsed, detail=f"timeout after {_PROVIDER_TEST_TIMEOUT_S:.0f}s"
        )
    except httpx.HTTPStatusError as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return ProviderTestResponse(
            ok=False, latency_ms=elapsed, detail=f"HTTP {exc.response.status_code} from endpoint"
        )
    except (httpx.HTTPError, ValueError) as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        # exc messages here originate from our own code / httpx — never contain the key.
        return ProviderTestResponse(ok=False, latency_ms=elapsed, detail=str(exc))

    elapsed = int((time.monotonic() - start) * 1000)
    if require_ok:
        ok = "ok" in text.strip().lower()
        detail = "model followed the instruction" if ok else "model reply did not match 'OK'"
    else:
        ok = True
        detail = "endpoint responded"
    return ProviderTestResponse(ok=ok, latency_ms=elapsed, detail=detail)


@router.post(
    "/provider/test/connection",
    response_model=ProviderTestResponse,
    summary="Bounded provider connection probe (W1)",
    description=(
        "One bounded, token-capped call (timeout _PROVIDER_TEST_TIMEOUT_S) to verify the "
        "provider endpoint responds. Accepts a config_id (uses the stored, decrypted key) or an "
        "inline {provider_type, model, base_url?, api_key?}. Returns {ok, latency_ms, detail}; "
        "NEVER echoes the key. CLI backend is posture-checked, not live-probed. (F17, W1, I7)"
    ),
)
async def provider_test_connection(body: ProviderTestRequest) -> ProviderTestResponse:
    """POST /provider/test/connection — bounded connectivity probe (W1)."""
    return await _run_probe(body, instruction="Reply with the single word: OK", require_ok=False)


@router.post(
    "/provider/test/function",
    response_model=ProviderTestResponse,
    summary="Bounded provider instruction-follow probe (W1)",
    description=(
        "One bounded, token-capped call asking the model to reply exactly 'OK'; ok=true iff the "
        "reply contains 'OK'. Same input contract and safety as /provider/test/connection. "
        "(F17, W1, I7)"
    ),
)
async def provider_test_function(body: ProviderTestRequest) -> ProviderTestResponse:
    """POST /provider/test/function — bounded instruction-follow probe (W1)."""
    return await _run_probe(
        body,
        instruction="Reply with exactly the two characters: OK. No other text.",
        require_ok=True,
    )
