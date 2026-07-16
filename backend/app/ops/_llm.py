"""
Shared LLM plumbing for ops modules (I6/I7 — BE-DUP-1/BE-QUAL-2).

All ops modules that call an InferenceProvider for a single bounded turn share the same
pattern: resolve config → build provider → collect stream → parse JSON → log cost.
This module extracts that pattern once so the 7+ local copies can be removed.

Public API
----------
``resolve_operation_provider(vault_id, operation)``
    Resolve the InferenceProvider for a given operation (I6: never hardcodes a backend).
    Returns ``(provider, config_row)`` or ``None`` when no provider_config row resolves.

``bounded_chat_collect(provider, instruction, *, use_complete, max_tokens)``
    ONE capability-agnostic provider call.  Routes through ``provider.chat()`` by default;
    ``use_complete=True`` uses ``provider.complete()`` instead (avoids the agentic CLI
    provider's chat loop hanging on one-shot judging calls — ADR-0076).

``loads_json_lenient(raw)``
    Best-effort JSON parse tolerant of ```json fences / surrounding prose.

``clean_str(value)``
    Stripped non-empty string or None.

``clean_str_list(value, *, cap)``
    Bounded, deduped list of stripped non-empty strings.

``coerce_int(raw, fallback)``
    Coerce a provider-row field (possibly None/Any) to int with a fallback.

Invariants respected
--------------------
I6 — No hardcoded backend; no isinstance/type-branch anywhere in this module.
I7 — ``bounded_chat_collect`` carries a ``max_tokens`` cap when using ``complete()``;
     callers wrap ``chat()`` calls with ``asyncio.wait_for`` at their own call site.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.ingest.provider.base import InferenceProvider
from app.provider_config_service import OperationT

logger = logging.getLogger(__name__)

# Default max_tokens for complete()-mode calls (mirrors the historical constant in review.py).
_DEFAULT_COMPLETE_MAX_TOKENS = 2048


async def resolve_operation_provider(
    vault_id: str,
    operation: OperationT = "ingest",
) -> tuple[InferenceProvider, Any] | None:
    """
    Resolve the InferenceProvider for *operation* (I6).

    Returns ``(provider, config_row)`` or ``None`` when:
    - no ``provider_config`` row resolves (``ConfigNotFoundError``), or
    - DB is unavailable, or
    - ``resolve_provider`` raises.

    NEVER hardcodes a backend; NEVER branches on isinstance/type/class-name (I6).
    Mirrors the resolution previously duplicated across ops/review.py,
    ops/lint.py, ops/enrich_wikilinks.py, ops/synthesize.py,
    ops/reclassify_types.py, ops/backfill_domains.py, ops/deep_research.py.
    """
    from app.ingest.provider import resolve_provider  # noqa: PLC0415
    from app.provider_config_service import (  # noqa: PLC0415
        ConfigNotFoundError,
        resolve_provider_config,
    )

    try:
        config_row = await resolve_provider_config(operation, vault_id)
    except ConfigNotFoundError:
        # Normal path — no provider configured for this vault/operation.
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "resolve_operation_provider: provider resolution failed" " (vault=%s, op=%s): %s",
            vault_id,
            operation,
            exc,
        )
        return None

    try:
        provider = resolve_provider(config_row)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "resolve_operation_provider: provider build failed" " (vault=%s, op=%s): %s",
            vault_id,
            operation,
            exc,
        )
        return None

    return provider, config_row


async def bounded_chat_collect(
    provider: InferenceProvider,
    instruction: str,
    *,
    use_complete: bool = False,
    max_tokens: int | None = None,
) -> str:
    """
    ONE capability-agnostic provider call, returning the collected text (I6/I7).

    ``use_complete=False`` (default)
        Routes through ``provider.chat()`` (streaming, no tools).  Apply
        ``asyncio.wait_for`` at the call site for timeout bounding.

    ``use_complete=True``
        Routes through ``provider.complete()`` (single-turn, no tools).
        Used by the review seam to avoid the CLI provider's chat loop
        hanging on one-shot judging calls (ADR-0076).  ``max_tokens`` caps
        the output; a belt-and-suspenders ``cap * 4`` char truncation is applied.

    Backend-neutral: no isinstance/type-branch (I6).  Cost flows through
    the provider's bound UsageAccumulator.
    """
    from app.ingest.schemas import Message  # noqa: PLC0415

    if use_complete:
        cap = max_tokens if max_tokens else _DEFAULT_COMPLETE_MAX_TOKENS
        raw = await provider.complete(
            instruction,
            "Respond now, following the instructions above exactly. "
            "Output only what was requested — no preamble, no chain-of-thought.",
            max_tokens=cap,
        )
        text = str(raw).strip()
        char_cap = cap * 4
        if len(text) > char_cap:
            text = text[:char_cap]
        return text

    chunks: list[str] = []
    stream = await provider.chat(
        messages=[Message(role="user", content=instruction)],
        retrieval_context="",
    )
    async for chunk in stream:
        chunks.append(chunk)
    return "".join(chunks).strip()


def loads_json_lenient(raw: str) -> Any | None:
    """
    Best-effort JSON parse tolerant of ```json fences / surrounding prose.

    Returns the parsed value (dict/list/…) or ``None`` on failure.
    Never raises — degrade-safe for all AI seams.
    """
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    # Try object slice, then array slice.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start, end = text.find(open_ch), text.rfind(close_ch)
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                continue
    return None


def clean_str(value: Any) -> str | None:
    """Return a stripped non-empty string, or ``None``."""
    if isinstance(value, str):
        s = value.strip()
        return s or None
    return None


def clean_str_list(value: Any, *, cap: int) -> list[str]:
    """
    Tolerant parse of a JSON list into a bounded list of stripped non-empty strings (ADR-0044).

    Drops non-strings and empties; de-dups preserving order; truncates to *cap* (I7).
    Anything that is not a list → [].  Never raises — degrade-safe for the AI seam.
    """
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for entry in value:
        s = clean_str(entry)
        if s is None or s in seen:
            continue
        seen.add(s)
        out.append(s)
        if len(out) >= cap:
            break
    return out


def coerce_int(raw: Any, fallback: int) -> int:
    """
    Coerce a provider-row field (possibly ``None``/``Any``) to ``int``, else *fallback*.

    A coerced value of zero is treated as absent → returns *fallback* (zero token-budget
    or zero max-tokens would be silent-broken, so we treat it the same as missing).
    """
    if raw is None:
        return fallback
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value or fallback
