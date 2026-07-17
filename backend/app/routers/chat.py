"""
Per-domain APIRouter: /conversations/* + /chat/* endpoints.

Covers:
  GET    /conversations                — list conversations
  POST   /conversations                — create conversation
  GET    /conversations/{id}/messages  — message history
  DELETE /conversations/{id}           — soft-delete conversation
  PATCH  /conversations/{id}           — rename conversation
  POST   /chat/stream                  — bounded streaming chat turn
  POST   /chat/save-to-wiki            — save answer to wiki/synthesis/ (or wiki/queries/ if it
                                         is itself an open question)
"""

from __future__ import annotations

import asyncio
import logging
import re as _re
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.engine import CursorResult

from app import runtime_state
from app.chat.stream import ChatStreamError, run_chat_stream
from app.config import settings
from app.ingest.schemas import Message
from app.models import ChatMessage, Conversation
from app.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Chat Pydantic models (F6/F7, ADR-0019 §2.2/§2.5) ──────────────────────────

_VALID_CHAT_ROLES = {"user", "assistant", "system"}

# UXB-1: max chars for the conversation-list preview snippet (last message, stripped).
_CONVERSATION_PREVIEW_CHARS = 80


def _conversation_preview(content: str | None) -> str | None:
    """Derive the list preview from a message body: strip <think>, collapse whitespace, cap.

    Read-only projection for GET /conversations (UXB-1). <think>…</think> reasoning is removed
    (split_think), light markdown markers are dropped, whitespace collapses to single spaces,
    and the result is truncated to ~80 chars. Returns None for empty/whitespace content.
    """
    if not content:
        return None
    from app.chat.think import split_think

    visible, _ = split_think(content)
    # Drop the most common inline markdown markers so the snippet reads as plain text.
    for marker in ("**", "__", "`", "#", ">", "*", "_", "~~"):
        visible = visible.replace(marker, "")
    collapsed = " ".join(visible.split())
    if not collapsed:
        return None
    return collapsed[:_CONVERSATION_PREVIEW_CHARS]


class ConversationResponse(BaseModel):
    """API shape for one conversations row (ADR-0019 §2.5).

    `preview` (UXB-1) is a read-only derivation of the conversation's last message: its first
    ~80 chars with <think>…</think> and light markdown stripped. It is computed in the list
    handler (bounded subquery); there is no schema change. None when the conversation has no
    messages yet.
    """

    id: uuid.UUID
    vault_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    preview: str | None = None

    model_config = {"from_attributes": True}


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]
    total: int
    limit: int
    offset: int


class ConversationCreate(BaseModel):
    """Request body for POST /conversations (ADR-0019 §2.5). vault_id defaults to settings."""

    vault_id: str | None = Field(default=None, description="Defaults to settings.vault_id")
    title: str | None = Field(default=None, description="Optional initial title")


class ChatMessageResponse(BaseModel):
    """
    API shape for one messages row (ADR-0019 §2.5). `content` is RAW incl. literal
    <think>… (AC-F7-2); the client re-derives think-vs-content with the same split.
    """

    id: uuid.UUID
    conversation_id: uuid.UUID
    role: str
    content: str
    citations: list[Any] | None = Field(default=None, description="[] in M4 (M5 reserved)")
    provider_type: str | None
    model_id: str | None
    input_tokens: int
    output_tokens: int
    total_cost_usd: float = Field(description="0.0 for local/cli (I7); serialised as number")
    created_at: datetime

    @field_validator("total_cost_usd", mode="before")
    @classmethod
    def _decimal_to_float(cls, v: Any) -> float:
        return float(v) if v is not None else 0.0

    model_config = {"from_attributes": True}


class ChatMessageListResponse(BaseModel):
    items: list[ChatMessageResponse]
    total: int


class ChatImageIn(BaseModel):
    """
    One image attachment for a chat message (B2-C1, F17/I6 vision surface).

    `mime` is the IANA media type (e.g. ``"image/png"``). `data_base64` is the
    base64-encoded payload WITHOUT a ``data:...;base64,`` URI prefix.
    Size must be capped by the client before sending; the backend passes this through
    into the provider-neutral :class:`~app.ingest.schemas.MessageImage` DTO without
    re-validating size (the provider layer handles/ignores per its ``supports_vision``
    capability — B2-C1 cross-layer contract).
    """

    mime: str = Field(..., min_length=1, description="IANA media type, e.g. 'image/png'")
    data_base64: str = Field(
        ..., min_length=1, description="base64 image payload WITHOUT a data-URI prefix"
    )


class ChatMessageIn(BaseModel):
    """One turn in a ChatRequest. Mirrors the backend-neutral Message shape (I6)."""

    role: str = Field(..., description="user | assistant | system")
    content: str = Field(..., min_length=1)
    images: list[ChatImageIn] | None = Field(
        default=None,
        description=(
            "B2-C1: optional image attachments for this message turn. Passed through to the "
            "provider's chat() only when the active provider advertises supports_vision=True. "
            "Non-vision providers silently drop these (defense-in-depth, I6)."
        ),
    )

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        if v not in _VALID_CHAT_ROLES:
            raise ValueError(f"role must be one of {sorted(_VALID_CHAT_ROLES)}, got {v!r}")
        return v


class ChatRequest(BaseModel):
    """
    Request body for POST /chat/stream (ADR-0019 §2.2, B2-C1/C2/C3).

    The server NEVER accepts provider_type / model_id (I6 / Do-NOT #4): the backend resolves
    `resolve_provider_config("chat", vault_id)`. `operation` is fixed to "chat" so the same
    abstraction can route ingest-vs-chat differently.

    B2 additions (additive, non-breaking for existing clients):
      - ``use_web_search``: trigger a single-shot SearXNG fetch (C2, I9). [W] namespace.
      - ``retrieval_mode``: one of four frozen presets controlling k + expansion_depth (C3, I7).
    """

    conversation_id: uuid.UUID | None = Field(
        default=None, description="null = start a new conversation (id returned in done event)"
    )
    messages: list[ChatMessageIn] = Field(..., min_length=1)
    vault_id: str | None = Field(default=None, description="Defaults to settings.vault_id")
    context_window: int | None = Field(
        default=None,
        ge=4096,
        le=1_000_000,
        description="F14 window override (4096..1_000_000); null → provider/32K default",
    )
    operation: Literal["chat"] = Field(default="chat", description="Fixed to 'chat'")
    regenerate: bool = Field(
        default=False,
        description="AC-F6-4: delete the last assistant message before re-streaming",
    )
    use_web_search: bool = Field(
        default=False,
        description=(
            "B2-C2: when True, make ONE bounded SearXNG search and append a [W]-namespaced "
            "web-context block to the retrieval context (I9 — SearXNG only, no loop). "
            "For local_first retrieval_mode, web search fires only when wiki hits < "
            "LOCAL_FIRST_MIN_HITS. web_citations in the done event."
        ),
    )
    retrieval_mode: Literal["fast", "standard", "deep", "local_first"] = Field(
        default="standard",
        description=(
            "B2-C3: frozen retrieval preset. "
            "fast=k4/depth0 · standard=k8/depth2 (default) · "
            "deep=k12/depth2 · local_first=k8/depth2+web-gate. "
            "expansion_depth is always clamped to ≤2 (I7)."
        ),
    )
    use_skills: bool = Field(
        default=False,
        description=(
            "F6/P4: forward-compatible flag — when True, the client requests skill execution "
            "for this chat turn. Accepted and logged; actual execution deferred to P5 Skills view. "
            "No behavior change in the current release."
        ),
    )
    use_anytxt: bool = Field(
        default=False,
        description=(
            "F6/P4: forward-compatible flag — when True, the client requests AnyTXT Searcher "
            "local-file search for this chat turn. Accepted and logged; AnyTXT is a Windows-only "
            "local-search tool not applicable to the current Mac/TrueNAS/Docker stack. "
            "No behavior change in the current release."
        ),
    )


class ConversationRenameRequest(BaseModel):
    """Request body for PATCH /conversations/{id} (R7-3, AC-R7-3-1)."""

    title: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="New conversation title (1–200 characters)",
    )


class ConversationRenameResponse(BaseModel):
    """Response for PATCH /conversations/{id}."""

    id: uuid.UUID
    title: str


# ── POST /chat/save-to-wiki (G-P0-1; CG-A2/A3/A5, ADR-0067 P3-3) ──────────────
# Mirror of nashsu/llm_wiki F6 "Save to Wiki": files a durable chat assistant answer into the
# vault (I1/I5/I6/I7). ADR-0067 P3-3 fixes CG-D2/D3 (audit): the type is no longer hard-coded to
# `query`. A lightweight classifier routes an OPEN QUESTION → wiki/queries/ (type=query) and any
# ANSWER/ANALYSIS (the common case) → wiki/synthesis/ (type=synthesis), matching LLM Wiki, so
# saved answers stop inflating queries/ and start seeding synthesis/. A bounded, fire-and-forget
# wikilink-enrichment pass then links the saved page to existing pages so it is graph-connected
# (real F4 edges), not an isolated node.
#   I1 — single write via write_wiki_page (one data_version bump, no rescan)
#   I5 — Obsidian-valid frontmatter (ADR-0067 D2 shape: type/title/created/updated/tags/related)
#   I6 — the write itself makes NO provider call; the OPTIONAL enrichment pass routes through the
#        ingest provider (fire-and-forget, degrade-safe — no provider → skip, never a default)
#   I7 — no orchestrated loop; the enrichment pass is a single bounded call (own caps + cost log)


_THINK_BLOCK_RE = _re.compile(r"<think>.*?</think>", _re.DOTALL | _re.IGNORECASE)
_CITED_TRAILER_RE = _re.compile(r"<!--\s*cited:.*?-->", _re.DOTALL | _re.IGNORECASE)

# Strong refs to fire-and-forget enrichment tasks: asyncio holds only WEAK refs to scheduled
# tasks, so without this set a task can be garbage-collected before it runs (mirrors stream.py).
_background_tasks: set[asyncio.Task[Any]] = set()

# CG-A2/A5: interrogative sentence leads (EN + IT — the vault is IT/EN, F16). Used only as a
# secondary signal on a SHORT, single-clause title lacking a trailing '?'; the trailing '?' on
# the title or first content line is the primary, language-agnostic signal.
_INTERROGATIVE_LEADS: frozenset[str] = frozenset(
    # English
    "what why how when where who which whose whom "
    "is are am do does did can could should would "
    "will shall may might was were has have had "
    # Italian (vault is IT/EN, F16)
    "cosa come perche perché quando dove chi quale quali "
    "quanto quanti quanta quante è sono puo può dovrebbe".split()
)

# Leading word of a title (letters + accents + apostrophe), used for the interrogative-lead check.
_LEAD_WORD_RE = _re.compile(r"[a-zàèéìòù']+", _re.IGNORECASE | _re.UNICODE)


def _clean_chat_content(content: str) -> str:
    """
    Strip <think>…</think> blocks and <!-- cited: … --> trailers from a chat
    assistant message before saving it to the wiki (G-P0-1).

    Both patterns are injected server-side during streaming (F7/F5) and MUST NOT
    appear in the saved wiki page (they are transport artifacts, not human-readable
    content — I5 Obsidian-valid frontmatter / body rule).
    """
    cleaned = _THINK_BLOCK_RE.sub("", content)
    cleaned = _CITED_TRAILER_RE.sub("", cleaned)
    return cleaned.strip()


def _first_meaningful_line(content: str) -> str:
    """First non-empty content line with common markdown lead markers (#, >, -, *) stripped."""
    for line in content.splitlines():
        stripped = line.strip().lstrip("#>-*").strip()
        if stripped:
            return stripped
    return ""


def _is_open_question(title: str, content: str) -> bool:
    """
    CG-A2/A5: is the saved page itself an OPEN QUESTION (→ queries/) rather than an
    answer/analysis (→ synthesis/)?

    Signals, cheapest first:
      1. Trailing '?' on the title OR the first content line — the strongest,
         language-agnostic signal ("What is bge-m3?", "Perché scala il modello?").
      2. An interrogative lead word (EN/IT) on a SHORT, single-clause title: no ':'
         (so 'Topic: analysis' headings stay analytical) and ≤ 12 words (so a
         declarative sentence is not misread as a question).

    Defaults to False (→ synthesis) — the common 'save an answer' case.
    """
    title_s = title.strip()
    first_line = _first_meaningful_line(content)
    if title_s.endswith("?") or first_line.endswith("?"):
        return True
    if ":" not in title_s and len(title_s.split()) <= 12:
        lead = _LEAD_WORD_RE.match(title_s.lower())
        if lead is not None and lead.group(0) in _INTERROGATIVE_LEADS:
            return True
    return False


def _schedule_wikilink_enrichment(page: Any) -> None:
    """
    CG-A3 (ADR-0067 P3-3): fire-and-forget bounded wikilink-enrichment on the just-saved page,
    reusing the SAME ``ops/enrich_wikilinks`` seam the ingest post-hook uses (import, don't
    reinvent). It injects ``[[wikilinks]]`` to existing pages into the saved body; the enrich
    reindex re-derives the K5 links, so the saved answer gains real F4 direct-link (×3) edges and
    stops being an isolated graph node.

    NEVER fails the save: ``enrich_wikilinks`` is itself degrade-safe (no provider → skip; any
    error → EnrichResult), and this wrapper additionally swallows scheduling/runtime errors.
    """

    async def _run() -> None:
        try:
            from app.ops.enrich_wikilinks import enrich_wikilinks

            vault_id = getattr(page, "vault_id", None) or settings.vault_id
            await enrich_wikilinks([page], str(vault_id))
        except Exception as exc:  # noqa: BLE001 — fire-and-forget; never surface to the caller
            logger.warning("save-to-wiki: wikilink enrichment failed (non-fatal): %s", exc)

    try:
        task = asyncio.create_task(_run())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except RuntimeError as exc:  # pragma: no cover — no running loop (never under FastAPI)
        logger.debug("save-to-wiki: could not schedule enrichment (%s)", exc)


class SaveToWikiRequest(BaseModel):
    """
    Request body for POST /chat/save-to-wiki (G-P0-1; CG-A2/A5, ADR-0067 P3-3).

    Saves a cleaned chat assistant answer to the vault. A lightweight classifier routes an open
    QUESTION to ``wiki/queries/<slug>.md`` (type=query) and any answer/analysis — the common case
    — to ``wiki/synthesis/<slug>.md`` (type=synthesis). The write itself calls no inference
    provider (I6); a bounded fire-and-forget wikilink-enrichment pass may run afterwards to
    graph-connect the saved page (CG-A3).
    """

    vault_id: str | None = Field(default=None, description="Defaults to settings.vault_id")
    title: str = Field(..., min_length=1, description="Page title (required)")
    content: str = Field(..., min_length=1, description="Page content (required)")
    sources: list[str] | None = Field(
        default=None,
        description="Optional source references to attach to the page frontmatter",
    )
    conversation_id: str | None = Field(
        default=None,
        description="Optional conversation UUID for provenance reference",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "What is bge-m3?",
                "content": "bge-m3 is a multi-lingual dense embedding model...",
                "sources": ["raw/sources/chat-123.md"],
                "conversation_id": "00000000-0000-0000-0000-000000000001",
            }
        }
    }


class SaveToWikiResponse(BaseModel):
    """201 response for POST /chat/save-to-wiki (G-P0-1; CG-A2/A5, ADR-0067 P3-3)."""

    page_id: uuid.UUID = Field(..., description="UUID of the created/updated wiki page")
    file_path: str = Field(
        ...,
        description=(
            "Relative path in the vault. Usually wiki/synthesis/<slug>.md (an answer/analysis); "
            "wiki/queries/<slug>.md when the saved content is itself an open question."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "page_id": "00000000-0000-0000-0000-000000000001",
                "file_path": "wiki/synthesis/bge-m3-embedding-tradeoffs.md",
            }
        }
    }


# ── Chat: conversations CRUD + streaming turn (F6/F7, ADR-0019) ───────────────


@router.get(
    "/conversations",
    response_model=ConversationListResponse,
    summary="List chat conversations for a vault",
    description=(
        "Returns live (non-soft-deleted) conversations for a vault, ordered updated_at DESC "
        "(drives last-active restore, AC-F6-1). Paginated (limit 1..100, offset >=0). F6."
    ),
)
async def list_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    vault_id: str | None = Query(default=None, description="Defaults to settings.vault_id"),
) -> ConversationListResponse:
    effective_vault_id = vault_id or settings.vault_id
    async with runtime_state.get_session() as session:
        base = select(Conversation).where(
            Conversation.vault_id == effective_vault_id,
            Conversation.deleted_at.is_(None),
        )
        total_row = await session.execute(select(func.count()).select_from(base.subquery()))
        total: int = total_row.scalar_one()
        rows = await session.execute(
            base.order_by(Conversation.updated_at.desc()).offset(offset).limit(limit)
        )
        convs = list(rows.scalars().all())

        # UXB-1: last-message preview per conversation (read-only derivation, no schema change).
        # Bounded: one extra query over ONLY the paged conversation IDs (≤ limit ≤ 100). We
        # fetch each conversation's most-recent message content, then strip <think> + light
        # markdown in Python and cap at 80 chars. Portable SQL: latest = max(created_at) per
        # conversation via a grouped subquery joined back to messages (SQLite + Postgres).
        conv_ids = [c.id for c in convs]
        previews: dict[uuid.UUID, str | None] = {}
        if conv_ids:
            latest = (
                select(
                    ChatMessage.conversation_id.label("cid"),
                    func.max(ChatMessage.created_at).label("mx"),
                )
                .where(ChatMessage.conversation_id.in_(conv_ids))
                .group_by(ChatMessage.conversation_id)
                .subquery()
            )
            msg_rows = await session.execute(
                select(ChatMessage.conversation_id, ChatMessage.content).join(
                    latest,
                    (ChatMessage.conversation_id == latest.c.cid)
                    & (ChatMessage.created_at == latest.c.mx),
                )
            )
            for cid, content in msg_rows.all():
                # Guard against a same-timestamp tie yielding two rows: keep the first.
                if cid not in previews:
                    previews[cid] = _conversation_preview(content)

    items: list[ConversationResponse] = []
    for c in convs:
        resp = ConversationResponse.model_validate(c)
        resp.preview = previews.get(c.id)
        items.append(resp)
    return ConversationListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=201,
    summary="Create an empty chat conversation",
    description="Create a conversation {vault_id?, title?}. Also implicitly created by "
    "/chat/stream when conversation_id is null. F6 (ADR-0019 §2.5).",
)
async def create_conversation(body: ConversationCreate) -> ConversationResponse:
    effective_vault_id = body.vault_id or settings.vault_id
    async with runtime_state.get_session() as session:
        conv = Conversation(vault_id=effective_vault_id, title=body.title)
        session.add(conv)
        await session.flush()
        await session.refresh(conv)
        result = ConversationResponse.model_validate(conv)
    return result


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ChatMessageListResponse,
    summary="Get ordered message history for a conversation",
    description="Messages ordered created_at ASC. content is RAW incl. literal <think>… "
    "(AC-F7-2). 404 if the conversation is unknown/soft-deleted. F6.",
    responses={404: {"description": "Conversation not found"}},
)
async def get_conversation_messages(conversation_id: uuid.UUID) -> ChatMessageListResponse:
    async with runtime_state.get_session() as session:
        conv_row = await session.execute(
            select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.deleted_at.is_(None),
            )
        )
        if conv_row.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        rows = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
        )
        msgs = list(rows.scalars().all())
    items = [ChatMessageResponse.model_validate(m) for m in msgs]
    return ChatMessageListResponse(items=items, total=len(items))


@router.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    summary="Soft-delete a conversation",
    description="Sets deleted_at (ADR-0005 pattern). 404 if unknown/already deleted. F6.",
    responses={204: {"description": "Soft-deleted"}, 404: {"description": "Not found"}},
)
async def delete_conversation(conversation_id: uuid.UUID) -> None:
    from sqlalchemy import update as sa_update

    async with runtime_state.get_session() as session:
        result = await session.execute(
            sa_update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.deleted_at.is_(None),
            )
            .values(deleted_at=datetime.now(UTC))
        )
        affected = cast("CursorResult[Any]", result).rowcount
    if affected == 0:
        raise HTTPException(status_code=404, detail="conversation not found")


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationRenameResponse,
    summary="Rename a conversation",
    description=(
        "Update the title of a live (non-deleted) conversation (R7-3, AC-R7-3-1, [F6]). "
        "404 if the conversation is unknown or soft-deleted. "
        "body: {title: str 1..200}."
    ),
    responses={
        200: {"description": "Conversation renamed"},
        404: {"description": "Conversation not found or deleted"},
        422: {"description": "Validation error (title empty or too long)"},
    },
)
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: ConversationRenameRequest,
) -> ConversationRenameResponse:
    """
    PATCH /conversations/{conversation_id} — R7-3 rename [F6].

    Sets conversations.title to body.title for the given live conversation.
    Returns 404 if the conversation is unknown or has been soft-deleted.
    """
    from sqlalchemy import update as sa_update

    async with runtime_state.get_session() as session:
        result = await session.execute(
            sa_update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.deleted_at.is_(None),
            )
            .values(title=body.title)
            .returning(Conversation.id, Conversation.title)
        )
        row = result.first()

    if row is None:
        raise HTTPException(status_code=404, detail="conversation not found")

    m = row._mapping
    return ConversationRenameResponse(id=m["id"], title=m["title"])


@router.post(
    "/chat/stream",
    summary="Stream a chat turn (NDJSON)",
    description=(
        "Bounded chat turn (F6/F7, I6/I7, ADR-0019 §2.2). Returns 200 with "
        "application/x-ndjson: one JSON event per line (token | think | done | error). "
        "Routes via resolve_provider_config('chat', vault_id) — never a hardcoded provider "
        "(I6). Bounded by token_budget + timeout (I7); total_cost_usd in the done event. "
        "404 if conversation_id is unknown; 503 if no chat provider resolves. "
        "429 if per-IP rate limit exceeded (R13-9)."
    ),
    responses={
        200: {"content": {"application/x-ndjson": {}}, "description": "NDJSON event stream"},
        404: {"description": "conversation_id provided but unknown"},
        422: {"description": "Body validation failure"},
        429: {"description": "Per-IP rate limit exceeded (R13-9)"},
        503: {"description": "No chat provider_config resolves (I6)"},
    },
    dependencies=[Depends(rate_limit)],
)
async def chat_stream(body: ChatRequest) -> StreamingResponse:
    """
    POST /chat/stream — the NDJSON streaming chat turn (ADR-0019 §2.2, B2-C1/C2/C3).

    Setup failures that must map to an HTTP status (unknown conversation → 404, no provider →
    503) are raised by run_chat_stream BEFORE the first yield; we surface them here. Once the
    stream starts (HTTP 200), all later failures are terminal `error` NDJSON events.
    """
    from app.ingest.schemas import MessageImage

    domain_messages = [
        Message(
            role=m.role,
            content=m.content,
            images=[
                MessageImage(mime=img.mime, data_base64=img.data_base64) for img in (m.images or [])
            ],
        )
        for m in body.messages
    ]

    agen = run_chat_stream(
        conversation_id=body.conversation_id,
        messages=domain_messages,
        vault_id=body.vault_id,
        context_window=body.context_window,
        regenerate=body.regenerate,
        use_web_search=body.use_web_search,
        retrieval_mode=body.retrieval_mode,
        use_skills=body.use_skills,
        use_anytxt=body.use_anytxt,
    )

    # Pull the first line eagerly so pre-stream setup errors (404/503) become real HTTP codes
    # rather than a 200 stream that immediately errors.
    try:
        first_line = await agen.__anext__()
    except ChatStreamError as exc:
        if exc.code == "not_found":
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if exc.code == "no_provider":
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except StopAsyncIteration:  # pragma: no cover - generator always yields
        first_line = ""

    async def _body() -> AsyncGenerator[str, None]:
        if first_line:
            yield first_line
        async for line in agen:
            yield line

    return StreamingResponse(
        _body(),
        media_type="application/x-ndjson",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@router.post(
    "/chat/save-to-wiki",
    response_model=SaveToWikiResponse,
    status_code=201,
    summary="Save a chat answer to the wiki (synthesis by default, query if it is a question)",
    description=(
        "Mirrors nashsu/llm_wiki F6 'Save to Wiki' (CG-A2/A5, ADR-0067 P3-3): saves a cleaned "
        "assistant answer as wiki/synthesis/<slug(title)>.md with type=synthesis frontmatter — "
        "OR wiki/queries/<slug>.md with type=query when the saved content is itself an open "
        "question (title/first line ends with '?' or is interrogative). "
        "Strips <think>…</think> and <!-- cited: … --> transport artifacts before saving. "
        "Persists via the single write_wiki_page seam (I1 — one data_version bump, no rescan; "
        "ADR-0067 D2 frontmatter + resolved related). The write makes no provider call (I6); a "
        "bounded fire-and-forget wikilink-enrichment pass may follow to graph-connect the page "
        "(CG-A3, degrade-safe). "
        "Returns {page_id, file_path}. 422 if title/content is missing."
    ),
    responses={
        201: {"description": "Page created or updated"},
        422: {"description": "title or content is missing / empty"},
    },
)
async def save_chat_to_wiki(body: SaveToWikiRequest) -> SaveToWikiResponse:
    """
    POST /chat/save-to-wiki — file a chat answer into the wiki (G-P0-1; CG-A2/A3/A5).

    Invariant compliance:
      I1 — single write_wiki_page call → one data_version bump, no rescan.
      I5 — Obsidian-valid YAML frontmatter (ADR-0067 D2 shape, emitted by write_wiki_page).
      I6 — the write itself makes NO provider call; the optional enrichment pass routes through
           the ingest provider (fire-and-forget, degrade-safe — no provider → skip).
      I7 — no orchestrated loop; enrichment is a single bounded call with its own caps + cost log.
    """
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
    from app.ingest.writer import write_wiki_page

    # Clean transport artifacts from content before writing to the wiki (G-P0-1)
    cleaned_content = _clean_chat_content(body.content)
    if not cleaned_content:
        raise HTTPException(
            status_code=422,
            detail="content is empty after stripping think/citation blocks",
        )

    # CG-A2/A5: classify the saved content. An open question stays a query (queries/); any
    # answer/analysis — the common case — files as synthesis (synthesis/), matching LLM Wiki and
    # ending the queries/ inflation + synthesis starvation the audit flagged (CG-D2/D3).
    page_type = (
        PageType.QUERY if _is_open_question(body.title, cleaned_content) else PageType.SYNTHESIS
    )

    # Build sources list (conversation provenance as a pseudo-source reference). NB (ADR-0067 D2):
    # sources are carried on the object + written to Postgres for F3 traceability; they are no
    # longer emitted in the .md by write_wiki_page.
    sources: list[str] = list(body.sources) if body.sources else []
    if body.conversation_id:
        conv_ref = f"conversation/{body.conversation_id}"
        if conv_ref not in sources:
            sources.append(conv_ref)
    if not sources:
        sources = ["chat"]

    fm = WikiFrontmatter(
        type=page_type,
        title=body.title,
        sources=sources,
        lang="en",
    )
    wiki_page = WikiPage(
        title=body.title,
        type=page_type,
        content=cleaned_content,
        frontmatter=fm,
    )

    # Single write seam (I1): persists file, Postgres row, Qdrant vector, links, index.md,
    # bumps data_version once. No provider call here (I6/I7).
    persisted = await write_wiki_page(None, wiki_page, "")

    # CG-A3: fire-and-forget graph-connect the saved page (reuse the ingest enrich seam). Never
    # blocks or fails the save; degrade-safe when no ingest provider resolves (I6/I7).
    _schedule_wikilink_enrichment(persisted)

    return SaveToWikiResponse(
        page_id=persisted.id,
        file_path=persisted.file_path,
    )
