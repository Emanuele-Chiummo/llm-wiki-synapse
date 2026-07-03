"""
Conversation auto-titling (UXB-1 / UXA-02, [F6][F16]).

After a conversation's FIRST completed assistant turn, if its title is still the
default/untitled pattern, Synapse generates a short human-readable title from the first
user message with ONE bounded provider call. Root cause it fixes: the conversation list
is useless for navigation when every entry is "Untitled" or a raw first-message dump
(UX-AUDIT-2026-07.md UXA-02).

Design constraints (PM-locked, AC-UXB1-1/4, invariants I3/I7):
  - Fires AFTER the chat stream ends — never during streaming (I3). The caller schedules
    this as a fire-and-forget background task once the `done` event has been produced.
  - Bounded: ONE provider.chat() call, no retry; output is capped to ~60 tokens at the
    call site (the chat() ABC takes no max_tokens arg, so we stop consuming once the
    estimated output crosses the cap and close the generator).
  - Cost logged (I7): the run-scoped UsageAccumulator is read after the call and the
    per-turn cost is emitted as a single INFO line with total_cost_usd.
  - On ANY failure the existing title is kept; the caller falls back to a timestamp title
    only when the conversation had no meaningful title to begin with.

Nothing here is provider- or model-specific (I6): the provider is resolved through the
normal config for operation "chat"; no model id / endpoint / key appears in this module.
"""

from __future__ import annotations

import inspect
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update

from app.chat.think import split_think
from app.ingest.provider import resolve_provider
from app.ingest.provider.base import UsageAccumulator
from app.ingest.schemas import Message
from app.models import ChatMessage, Conversation
from app.provider_config_service import resolve_provider_config

logger = logging.getLogger(__name__)

# ~60-token output bound (AC-UXB1-4). The chat() seam streams text, so we enforce the cap at
# the call site: stop consuming once the accumulated output would exceed this many tokens.
# Same coarse ~4 chars/token heuristic used across the chat stream (context.py / stream.py).
_MAX_TITLE_TOKENS = 60
_CHARS_PER_TOKEN = 4
_MAX_TITLE_OUTPUT_CHARS = _MAX_TITLE_TOKENS * _CHARS_PER_TOKEN

# The first user message is truncated before being embedded in the prompt so a huge paste does
# not blow the (small) title generation budget.
_FIRST_MESSAGE_PROMPT_CHARS = 500

# Stored title is short by contract (AC-UXB1-1): trim to a sane single-line length.
_MAX_TITLE_CHARS = 60

_TITLE_PROMPT = (
    "Generate a 3-6 word title in the same language as this exchange. "
    "Reply with ONLY the title, no quotes, no punctuation at the end.\n\n"
    "First message:\n{first_message}"
)


def _timestamp_fallback_title(now: datetime | None = None) -> str:
    """Timestamp-based fallback used when generation fails or is not applicable.

    Mirrors the existing default (`Chat YYYY-MM-DD HH:mm`) so the list never shows a raw
    provider error and stays consistent with pre-UXB-1 behaviour.
    """
    ts = now or datetime.now(UTC)
    return f"Chat {ts.strftime('%Y-%m-%d %H:%M')}"


def _clean_title(raw: str) -> str:
    """Normalise a raw provider title: single line, strip <think>, quotes, trailing punct."""
    visible, _ = split_think(raw)
    # Collapse to the first non-empty line (a chatty model may add a preamble line).
    line = next((ln.strip() for ln in visible.splitlines() if ln.strip()), "")
    line = line.strip().strip("\"'` ").strip()
    # Drop a trailing sentence terminator the model may append despite the instruction.
    line = line.rstrip(".!?;:").strip()
    return line[:_MAX_TITLE_CHARS]


def is_default_title(title: str | None, first_user_message: str | None) -> bool:
    """True when *title* is still the un-curated default and may be auto-replaced.

    A conversation title is "default" when it is:
      - None / empty (POST /conversations with no title), OR
      - the raw first-message-derived default set by the implicit-create path in stream.py
        (`last_user.content[:80]`), OR
      - a previously-set timestamp fallback (`Chat YYYY-MM-DD HH:mm`).

    A user-edited title (R7-3 PATCH) is NOT default and is never overwritten.
    """
    if title is None or not title.strip():
        return True
    stripped = title.strip()
    if first_user_message is not None and stripped == first_user_message.strip()[:80]:
        return True
    # Recognise our own timestamp fallback so a retry can still upgrade it to a real title.
    if stripped.startswith("Chat ") and len(stripped) == len("Chat YYYY-MM-DD HH:MM"):
        return True
    return False


async def _generate_title_text(provider: object, first_user_message: str) -> str:
    """ONE bounded provider.chat() turn → cleaned title string (may raise on provider error).

    The output is capped at ~60 tokens at the call site by closing the async generator once the
    accumulated text crosses the char budget. No retry.
    """
    prompt = _TITLE_PROMPT.format(first_message=first_user_message[:_FIRST_MESSAGE_PROMPT_CHARS])
    # Support both chat() shapes (async-gen fn OR coroutine returning one) — same as stream.py.
    maybe = provider.chat(  # type: ignore[attr-defined]
        messages=[Message(role="user", content=prompt)],
        retrieval_context="",
    )
    agen = await maybe if inspect.isawaitable(maybe) else maybe

    parts: list[str] = []
    collected = 0
    try:
        async for delta in agen:
            if not delta:
                continue
            parts.append(delta)
            collected += len(delta)
            if collected >= _MAX_TITLE_OUTPUT_CHARS:
                break
    finally:
        aclose = getattr(agen, "aclose", None)
        if aclose is not None:
            await aclose()

    return _clean_title("".join(parts))


async def maybe_generate_conversation_title(
    conversation_id: uuid.UUID,
    vault_id: str,
) -> None:
    """Fire-and-forget: generate a title for *conversation_id* after its first exchange.

    Idempotent and self-guarding. It:
      1. loads the conversation + its message count,
      2. no-ops unless this is the FIRST completed assistant turn AND the title is still
         a default/untitled pattern (never overwrites a user-edited or already-generated
         title — so a second exchange does not regenerate),
      3. calls the resolved chat provider ONCE (bounded ~60 tokens, no retry),
      4. persists the cleaned title + bumps updated_at,
      5. on ANY exception keeps the existing title (falling back to a timestamp title only
         when the conversation had no usable title at all).

    This never raises — it is scheduled as a background task and must not crash the loop.
    """
    try:
        from app.db import get_session

        # ── Guard: first assistant turn + still-default title (read-only pre-check) ──
        async with get_session() as session:
            conv = (
                await session.execute(
                    select(Conversation).where(
                        Conversation.id == conversation_id,
                        Conversation.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if conv is None:
                return

            assistant_count = (
                await session.execute(
                    select(func.count())
                    .select_from(ChatMessage)
                    .where(
                        ChatMessage.conversation_id == conversation_id,
                        ChatMessage.role == "assistant",
                    )
                )
            ).scalar_one()

            first_user = (
                await session.execute(
                    select(ChatMessage.content)
                    .where(
                        ChatMessage.conversation_id == conversation_id,
                        ChatMessage.role == "user",
                    )
                    .order_by(ChatMessage.created_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()

            current_title = conv.title

        # Only after the FIRST assistant turn; never regenerate on later exchanges.
        if int(assistant_count) != 1:
            return
        if not is_default_title(current_title, first_user):
            return
        if not first_user or not first_user.strip():
            return

        # ── Bounded provider call (I6/I7) ────────────────────────────────────────────
        config_row = await resolve_provider_config("chat", vault_id)
        provider = resolve_provider(config_row)
        accumulator = UsageAccumulator()
        provider.bind_accumulator(accumulator)

        model_id = str(getattr(config_row, "model_id", "?"))
        title = await _generate_title_text(provider, first_user)

        if not title:
            # Empty generation → treat like a failure: keep a usable default.
            title = _timestamp_fallback_title()

        # ── Persist (existing update path) + bump updated_at ─────────────────────────
        async with get_session() as session:
            await session.execute(
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.deleted_at.is_(None),
                )
                .values(title=title, updated_at=datetime.now(UTC))
            )

        snap = accumulator.snapshot()
        logger.info(
            "conversation auto-title: conv=%s model=%s in=%d out=%d "
            "total_cost_usd=%.4f title=%r",
            conversation_id,
            model_id,
            snap.input_tokens,
            snap.output_tokens,
            float(snap.total_cost_usd),
            title,
        )
    except Exception as exc:  # noqa: BLE001 — fire-and-forget: never crash the caller (I7)
        logger.warning(
            "conversation auto-title failed (conv=%s) — keeping existing title: %s",
            conversation_id,
            exc,
        )
        # Fallback: only stamp a timestamp title if the conversation truly had none, so we
        # never leave the list showing a raw error or a blank entry.
        try:
            from app.db import get_session

            async with get_session() as session:
                conv = (
                    await session.execute(
                        select(Conversation).where(
                            Conversation.id == conversation_id,
                            Conversation.deleted_at.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                if conv is not None and (conv.title is None or not conv.title.strip()):
                    await session.execute(
                        update(Conversation)
                        .where(Conversation.id == conversation_id)
                        .values(
                            title=_timestamp_fallback_title(),
                            updated_at=datetime.now(UTC),
                        )
                    )
        except Exception:  # noqa: BLE001
            logger.warning(
                "conversation auto-title fallback also failed (conv=%s)", conversation_id
            )
