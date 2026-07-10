"""
Bounded chat stream orchestration (ADR-0019 §2.2 / I7) + F5 citation integration
(ADR-0022, M5 Phase 1).

`run_chat_stream()` is the single async generator behind `POST /chat/stream`. It:

  1. resolves the provider for the "chat" operation (I6 — never hardcoded),
  2. calls `retrieve()` ONCE before streaming (ADR-0022 §2.7, I3 — NOT per-token),
  3. prepends the light system context header (purpose.md + overview.md) to
     `RetrievalContext.text` and passes the combined string as `retrieval_context`
     to `provider.chat()` — the provider signature is unchanged (I6, Do-NOT #7),
  4. persists the user message immediately (so Regenerate works on failure),
  5. consumes `provider.chat()` deltas through the streaming-safe <think> scanner (F7, §2.4),
  6. enforces TWO bounds (I7 / Do-NOT #5): `token_budget` and `timeout_seconds`, both from the
     resolved provider_config row — never literals,
  7. on success persists the RAW assistant message (incl. literal <think>…) with token/cost
     columns + the serialised Citation list in the `citations` JSONB column (ADR-0022 §2.4),
     bumps conversation.updated_at, then yields exactly one `done` event with an additive
     `citations` field (compact `[{n,id,title,slug}]` — score/phase stored, not streamed),
  8. on failure yields exactly one `error` event (and does NOT persist the assistant message).

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
from collections.abc import AsyncGenerator, AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.chat.context import DEFAULT_CONTEXT_WINDOW, build_chat_context
from app.chat.think import ThinkScanner
from app.chat.web_context import WebContext, build_web_context
from app.config import settings
from app.ingest.provider import resolve_provider
from app.ingest.provider.base import UsageAccumulator
from app.ingest.schemas import Message
from app.models import ChatMessage, Conversation
from app.provider_config_service import ConfigNotFoundError, resolve_provider_config
from app.rag.retrieval import Citation, retrieval_mode_params, retrieve

logger = logging.getLogger(__name__)

# Strong references to fire-and-forget background tasks (auto-title): asyncio keeps only
# weak refs to scheduled tasks, so without this set a task can be GC'd before it runs.
_background_tasks: set[asyncio.Task[None]] = set()

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
    use_web_search: bool = False,
    retrieval_mode: str = "standard",
    use_skills: bool = False,
    use_anytxt: bool = False,
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
                raise ChatStreamError("not_found", f"conversation_id {conversation_id} not found")
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
            # B2-C1: persist images as [{mime, data_base64}] JSONB so regenerate/history works.
            # Content is stored RAW (AC-F7-2). Images are the full payload — kept for
            # history replay; the provider handles/ignores per its supports_vision (I6).
            images_json: list[dict[str, str]] = [
                {"mime": img.mime, "data_base64": img.data_base64}
                for img in (last_user.images or [])
            ]
            session.add(
                ChatMessage(
                    conversation_id=conv_id,
                    role="user",
                    content=last_user.content,
                    citations=[],
                    images=images_json if images_json else None,
                )
            )
        await session.flush()
    # session committed here — user message durable so Regenerate works on later failure.

    # ── F5: call retrieve() ONCE before streaming (ADR-0022 §2.7, I3 — NOT per-token) ──
    # retrieve() is a pure store read (zero inference, zero vault walk — I1/I7).
    # B2-C3: retrieval_mode selects a frozen (k, expansion_depth) preset (I7).
    # We tolerate any retrieval error gracefully: fall back to empty context (no citations).
    retrieval_citations: list[Citation] = []
    retrieval_text = ""
    r_k, r_depth = retrieval_mode_params(retrieval_mode)
    try:
        rctx = await retrieve(
            query=last_user.content if last_user else "",
            vault_id=effective_vault_id,
            context_window=window,
            k=r_k,
            expansion_depth=r_depth,
        )
        retrieval_text = rctx.text
        retrieval_citations = rctx.citations
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "chat: retrieve() failed (vault=%s) — proceeding with empty retrieval context: %s",
            effective_vault_id,
            exc,
        )

    # ── B2-C2: web-search context block (single-shot, [W] namespace, I7/I9) ─────────
    # Fires when use_web_search=True AND (mode != local_first OR wiki hits < threshold).
    # local_first: web is the FALLBACK when wiki retrieval returned < LOCAL_FIRST_MIN_HITS.
    # All other modes: web fires unconditionally when use_web_search=True.
    web_ctx: WebContext | None = None
    if use_web_search:
        local_first_mode = retrieval_mode == "local_first"
        min_hits = settings.local_first_min_hits
        should_web = (not local_first_mode) or (len(retrieval_citations) < min_hits)
        if should_web:
            query_str = last_user.content if last_user else ""
            try:
                web_ctx = await build_web_context(query_str)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "chat: build_web_context failed (vault=%s) — proceeding without web: %s",
                    effective_vault_id,
                    exc,
                )

    # ── F6/P4: log forward-compatible flags (skills + anytxt — no behavior yet) ─────────
    # use_skills and use_anytxt are accepted as request flags for composer parity. Skill
    # execution is deferred to P5 Skills view; AnyTXT is Windows-only / not applicable to
    # this stack. We log at INFO so the operator knows they were requested (I7 cost tracing).
    if use_skills:
        logger.info(
            "chat: use_skills=True requested (vault=%s) — execution deferred to P5",
            effective_vault_id,
        )
    if use_anytxt:
        logger.info(
            "chat: use_anytxt=True requested (vault=%s) — AnyTXT not available on this stack",
            effective_vault_id,
        )

    # ── Build the bounded provider call args (I6: provider chooses model; we pass ctx) ──
    # Prepend light grounding header (purpose.md + overview.md) to the retrieval context text
    # so the provider receives ONE combined context string (I6 signature unchanged, ADR-0022 §2.7).
    # B2-C2: append the [W] web-context block (if any) AFTER wiki context, clearly labelled.
    # The model is instructed to cite wiki pages as [n] and web results as [W1]..[Wn].
    light_header = build_chat_context(vault_root=settings.vault_root, context_window=window)
    system_parts: list[str] = [light_header]
    if retrieval_text:
        system_parts.append("## Retrieved context (cite as [n])\n" + retrieval_text)
    if web_ctx and not web_ctx.empty:
        system_parts.append(
            "## Web search context (cite as [W1], [W2], …)\n"
            "When a statement draws on the web results below, cite it with the matching "
            "[Wn] marker (e.g. [W1]). Keep [Wn] citations distinct from wiki [n] citations.\n\n"
            + web_ctx.text
        )
    system_context = "\n\n".join(system_parts)
    # The provider's chat() takes (messages, retrieval_context). We pass the combined string
    # as retrieval_context (backend-neutral, I6 — signature unchanged, Do-NOT #7).
    provider_messages = list(messages)

    # ── Stream, bounded by timeout + token_budget (I7) ──────────────────────────────
    scanner = ThinkScanner()
    raw_parts: list[str] = []
    approx_output_chars = 0
    finish_reason = "stop"
    started = time.monotonic()

    async def _consume() -> AsyncGenerator[str, None]:
        nonlocal approx_output_chars, finish_reason
        # The ABC declares `async def chat(...) -> AsyncIterator[str]`: a concrete provider may
        # return the async iterator directly (an async-generator fn) OR be a coroutine that
        # returns one. Support both shapes without isinstance-on-provider (I6-clean): await if
        # we got a coroutine.
        maybe = provider.chat(provider_messages, system_context)
        agen = await maybe if inspect.isawaitable(maybe) else maybe
        # B4 fix: always close the provider stream (open httpx connection) on all exit paths —
        # normal, token-budget break, timeout, and error. The finally runs when _consume is
        # closed via gen.aclose() (timeout/error) or exhausted normally (StopAsyncIteration).
        try:
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
                        break  # finally will aclose(agen) — no explicit close needed here
        finally:
            # Guard double-close: aclose() on an already-closed async-gen is a no-op,
            # but this makes the intent explicit (token-budget break → finally → aclose).
            _aclose_fn = getattr(agen, "aclose", None)
            if _aclose_fn is not None:
                await _aclose_fn()
        for kind, text in scanner.flush():
            yield _line({"type": kind, "delta": text})

    # B4 fix: create gen BEFORE the try so it is accessible in the finally.
    gen = _consume()
    try:
        # Wrap the whole consumption in a single timeout (F16 chat timeout, §2.2).
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
    finally:
        # B4 fix: ensure the provider stream (open httpx connection) is closed on ALL exit
        # paths — timeout, error, and normal completion. gen.aclose() sends GeneratorExit into
        # _consume, which triggers _consume's finally → agen.aclose(). No-op after normal
        # StopAsyncIteration (gen is already exhausted). Safe to call multiple times.
        await gen.aclose()

    # ── Success: persist assistant message (RAW, incl. <think>) + token/cost (I7) ────
    raw_content = "".join(raw_parts)
    snap = accumulator.snapshot()
    cost = round(float(snap.total_cost_usd), 4)

    # Serialise citations for the JSONB column (ADR-0022 §2.4 — stored with score/phase).
    # Empty list when no context was retrieved (no migration needed: column already exists).
    citations_json: list[dict[str, object]] = [
        {
            "n": c.n,
            "id": c.ref.id,
            "title": c.ref.title,
            "slug": c.ref.slug,
            "score": c.score,
            "phase": c.phase,
        }
        for c in retrieval_citations
    ]

    async with get_session() as session:
        assistant = ChatMessage(
            conversation_id=conv_id,
            role="assistant",
            content=raw_content,
            citations=citations_json,
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
        "chat turn done: conv=%s model=%s in=%d out=%d cost=$%.4f reason=%s citations=%d",
        conv_id,
        model_id,
        snap.input_tokens,
        snap.output_tokens,
        cost,
        finish_reason,
        len(retrieval_citations),
    )

    # ── UXB-1: conversation auto-title (UXA-02) — fires AFTER stream end, never during ──
    # Schedule a fire-and-forget background task now that the assistant message is persisted
    # (the guard inside runs only for the FIRST assistant turn on a still-default title). It
    # runs after this generator yields the terminal `done` event, so it never contends with
    # streaming (I3). It is bounded (~60 tokens, single call, no retry) and self-logging (I7).
    from app.chat.autotitle import maybe_generate_conversation_title

    # Keep a strong reference until completion: a bare create_task() result can be
    # garbage-collected mid-run (asyncio only holds a weak ref to scheduled tasks).
    _title_task = asyncio.create_task(
        maybe_generate_conversation_title(conv_id, effective_vault_id)
    )
    _background_tasks.add(_title_task)
    _title_task.add_done_callback(_background_tasks.discard)

    # Compact citation projection for the done event (ADR-0022 §2.4 — score/phase stored, not
    # streamed). Additive field → non-breaking for existing clients that ignore unknown keys.
    done_citations = [
        {"n": c.n, "id": c.ref.id, "title": c.ref.title, "slug": c.ref.slug}
        for c in retrieval_citations
    ]

    # B2-C2: web_citations in done event — [{index, title, url}]. Empty list when web off/empty.
    done_web_citations: list[dict[str, object]] = (
        [wc.to_dict() for wc in web_ctx.citations] if web_ctx and not web_ctx.empty else []
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
            "citations": done_citations,
            "web_citations": done_web_citations,
        }
    )
