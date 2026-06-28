"""
Bounded chat stream orchestration (ADR-0019 §2.2 / I7).

`run_chat_stream()` is the single async generator behind `POST /chat/stream`. It:

  1. resolves the provider for the "chat" operation (I6 — never hardcoded),
  2. builds the light system context (purpose.md + overview.md, §2.3),
  3. persists the user message immediately (so Regenerate works on failure),
  4. consumes `provider.chat()` deltas through the streaming-safe <think> scanner (F7, §2.4),
  5. enforces TWO bounds (I7 / Do-NOT #5): `token_budget` and `timeout_seconds`, both from the
     resolved provider_config row — never literals,
  6. on success persists the RAW assistant message (incl. literal <think>…) with token/cost
     columns and bumps conversation.updated_at, then yields exactly one `done` event,
  7. on failure yields exactly one `error` event (and does NOT persist the assistant message).

It yields already-serialised NDJSON LINES (str ending in "\n") so `main.py` only has to wrap a
`StreamingResponse` around it. Cost is logged per run (I7) and returned in `done`.

NB: chat does NOT create ingest_runs rows and does NOT create a chat_runs table (Do-NOT #9).
The per-message columns + the structured log line are the durable cost record.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.context import DEFAULT_CONTEXT_WINDOW, build_chat_context
from app.chat.think import ThinkScanner
from app.config import settings
from app.ingest.provider import resolve_provider
from app.ingest.provider.base import UsageAccumulator
from app.ingest.schemas import Message
from app.models import ChatMessage, Conversation
from app.provider_config_service import ConfigNotFoundError, resolve_provider_config

logger = logging.getLogger(__name__)

# F16 chat timeout default (ADR-0019 §2.2): 60s. Used when the provider_config row carries no
# explicit per-call timeout. Pure sizing default — not a model id / endpoint.
DEFAULT_CHAT_TIMEOUT_SECONDS = 60.0

# Coarse chars→tokens heuristic for the running output-token estimate used to enforce the
# token_budget cap when the provider does not surface incremental usage (Ollama reports usage
# only at stream end). Same ~4 chars/token convention as context.py.
_CHARS_PER_TOKEN = 4


class ChatStreamError(Exception):
    """Internal signal carrying an NDJSON error `code` for the terminal error event."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _line(event: dict[str, object]) -> str:
    """Serialise one event dict to an NDJSON line (compact JSON + newline)."""
    return json.dumps(event, separators=(",", ":")) + "\n"


async def _load_conversation(
    session: AsyncSession, conversation_id: uuid.UUID
) -> Conversation | None:
    row = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.deleted_at.is_(None),
        )
    )
    return row.scalar_one_or_none()


async def _delete_last_assistant(session: AsyncSession, conversation_id: uuid.UUID) -> None:
    """Regenerate (AC-F6-4): remove the most recent assistant message for the conversation."""
    row = await session.execute(
        select(ChatMessage)
        .where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.role == "assistant",
        )
        .order_by(ChatMessage.created_at.desc())
        .limit(1)
    )
    last = row.scalar_one_or_none()
    if last is not None:
        await session.delete(last)
        await session.flush()


async def run_chat_stream(
    *,
    conversation_id: uuid.UUID | None,
    messages: list[Message],
    vault_id: str | None,
    context_window: int | None,
    regenerate: bool,
) -> AsyncIterator[str]:
    """
    Async generator yielding NDJSON lines for one bounded chat turn (ADR-0019 §2.2).

    Raises ChatStreamError BEFORE the first yield only for setup failures that must map to an
    HTTP status (unknown conversation → 404, no provider → 503). Once streaming starts, all
    failures are surfaced as a terminal `error` NDJSON event (the HTTP status is already 200).
    """
    effective_vault_id = vault_id or settings.vault_id
    window = context_window or DEFAULT_CONTEXT_WINDOW

    # ── Resolve provider (I6) — a hard config error pre-stream maps to 503 ──────────
    # No session passed: resolve_provider_config opens its own and expunges the row, so the
    # detached ORM row is safe to read after the block (and a test mock needs no expunge).
    try:
        config_row = await resolve_provider_config("chat", effective_vault_id)
    except ConfigNotFoundError as exc:
        raise ChatStreamError("no_provider", str(exc)) from exc

    provider = resolve_provider(config_row)
    accumulator = UsageAccumulator()
    provider.bind_accumulator(accumulator)

    model_id = str(config_row.model_id)
    provider_type = str(config_row.provider_type)
    token_budget = int(getattr(config_row, "token_budget", 0) or 0)
    timeout_seconds = float(
        getattr(config_row, "timeout_seconds", None) or DEFAULT_CHAT_TIMEOUT_SECONDS
    )

    # ── Persist the user turn + ensure a conversation (pre-stream; maps to 404) ─────
    # Imported here (not module-top) so tests can monkeypatch app.db.get_session.
    from app.db import get_session

    last_user = next((m for m in reversed(messages) if m.role == "user"), None)

    async with get_session() as session:
        if conversation_id is not None:
            conv = await _load_conversation(session, conversation_id)
            if conv is None:
                raise ChatStreamError(
                    "not_found", f"conversation_id {conversation_id} not found"
                )
        else:
            conv = Conversation(
                vault_id=effective_vault_id,
                title=(last_user.content[:80] if last_user else None),
            )
            session.add(conv)
            await session.flush()
        conv_id = conv.id

        if regenerate:
            await _delete_last_assistant(session, conv_id)

        if last_user is not None:
            session.add(
                ChatMessage(
                    conversation_id=conv_id,
                    role="user",
                    content=last_user.content,
                    citations=[],
                )
            )
        await session.flush()
    # session committed here — user message durable so Regenerate works on later failure.

    # ── Build the bounded provider call args (I6: provider chooses model; we pass ctx) ──
    system_context = build_chat_context(vault_root=settings.vault_root, context_window=window)
    # The provider's chat() takes (messages, retrieval_context). We prepend the system context
    # as the retrieval_context string (backend-neutral, I6); the provider injects it as system.
    provider_messages = list(messages)

    # ── Stream, bounded by timeout + token_budget (I7) ──────────────────────────────
    scanner = ThinkScanner()
    raw_parts: list[str] = []
    approx_output_chars = 0
    finish_reason = "stop"
    started = time.monotonic()

    async def _consume() -> AsyncIterator[str]:
        nonlocal approx_output_chars, finish_reason
        # The ABC declares `async def chat(...) -> AsyncIterator[str]`: a concrete provider may
        # return the async iterator directly (an async-generator fn) OR be a coroutine that
        # returns one. Support both shapes without isinstance-on-provider (I6-clean): await if
        # we got a coroutine.
        maybe = provider.chat(provider_messages, system_context)
        agen = await maybe if inspect.isawaitable(maybe) else maybe
        async for delta in agen:
            if not delta:
                continue
            raw_parts.append(delta)
            approx_output_chars += len(delta)
            for kind, text in scanner.feed(delta):
                yield _line({"type": kind, "delta": text})
            # token_budget bound (I7 / Do-NOT #5): estimate output tokens; stop when exceeded.
            if token_budget > 0:
                est_total = accumulator.total_tokens + (approx_output_chars // _CHARS_PER_TOKEN)
                if est_total >= token_budget:
                    finish_reason = "length"
                    aclose = getattr(agen, "aclose", None)
                    if aclose is not None:
                        await aclose()
                    break
        for kind, text in scanner.flush():
            yield _line({"type": kind, "delta": text})

    try:
        # Wrap the whole consumption in a single timeout (F16 chat timeout, §2.2).
        gen = _consume()
        while True:
            try:
                remaining = timeout_seconds - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError
                line = await asyncio.wait_for(gen.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                break
            yield line
    except TimeoutError:
        finish_reason = "timeout"
        cost = float(accumulator.total_cost_usd)
        logger.warning(
            "chat timeout after %.1fs (vault=%s model=%s) — cost=$%.4f",
            timeout_seconds,
            effective_vault_id,
            model_id,
            cost,
        )
        yield _line(
            {
                "type": "error",
                "code": "provider_timeout",
                "message": f"chat provider timed out after {timeout_seconds:.0f}s",
                "total_cost_usd": round(cost, 4),
            }
        )
        return
    except Exception as exc:  # provider transport / parse error → terminal error event
        cost = float(accumulator.total_cost_usd)
        logger.exception("chat provider error (vault=%s model=%s)", effective_vault_id, model_id)
        yield _line(
            {
                "type": "error",
                "code": "provider_error",
                "message": f"{type(exc).__name__}: {exc}",
                "total_cost_usd": round(cost, 4),
            }
        )
        return

    # ── Success: persist assistant message (RAW, incl. <think>) + token/cost (I7) ────
    raw_content = "".join(raw_parts)
    snap = accumulator.snapshot()
    cost = round(float(snap.total_cost_usd), 4)

    async with get_session() as session:
        assistant = ChatMessage(
            conversation_id=conv_id,
            role="assistant",
            content=raw_content,
            citations=[],
            provider_type=provider_type,
            model_id=model_id,
            input_tokens=snap.input_tokens,
            output_tokens=snap.output_tokens,
            total_cost_usd=cost,
        )
        session.add(assistant)
        await session.flush()
        message_id = assistant.id
        await session.execute(
            update(Conversation)
            .where(Conversation.id == conv_id)
            .values(updated_at=datetime.now(UTC))
        )

    logger.info(
        "chat turn done: conv=%s model=%s in=%d out=%d cost=$%.4f reason=%s",
        conv_id,
        model_id,
        snap.input_tokens,
        snap.output_tokens,
        cost,
        finish_reason,
    )

    yield _line(
        {
            "type": "done",
            "conversation_id": str(conv_id),
            "message_id": str(message_id),
            "input_tokens": snap.input_tokens,
            "output_tokens": snap.output_tokens,
            "total_cost_usd": cost,
            "iterations_used": 1,
            "finish_reason": finish_reason,
        }
    )
