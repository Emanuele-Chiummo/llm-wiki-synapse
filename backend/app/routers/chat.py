"""
Per-domain APIRouter: /conversations/* + /chat/* endpoints.

Covers:
  GET    /conversations                — list conversations
  POST   /conversations                — create conversation
  GET    /conversations/{id}/messages  — message history
  DELETE /conversations/{id}           — soft-delete conversation
  PATCH  /conversations/{id}           — rename conversation
  POST   /chat/stream                  — bounded streaming chat turn
  POST   /chat/save-to-wiki            — save answer to wiki/queries/
"""

from __future__ import annotations

import logging
import re as _re
import sys as _sys
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.engine import CursorResult

from app.chat.stream import ChatStreamError, run_chat_stream
from app.config import settings
from app.ingest.schemas import Message
from app.models import ChatMessage, Conversation
from app.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()


class _LazyMain:
    """Lazy proxy to app.main; enables test patches via app.main.* to propagate."""

    __slots__ = ()

    def __getattr__(self, name: str) -> Any:
        return getattr(_sys.modules["app.main"], name)

    def __setattr__(self, name: str, value: object) -> None:
        setattr(_sys.modules["app.main"], name, value)


_m = _LazyMain()

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


# ── POST /chat/save-to-wiki (G-P0-1) ──────────────────────────────────────────
# Mirror of nashsu/llm_wiki F6 "Save to Wiki": routes a chat assistant answer into
# wiki/queries/<slug>.md as a typed "query" page (I1/I5/I6/I7):
#   I1 — single write via write_wiki_page (one data_version bump, no rescan)
#   I5 — Obsidian-valid frontmatter (type=query, sources=[])
#   I6 — NO provider call; pure DB/file write
#   I7 — no loop; single bounded operation


_THINK_BLOCK_RE = _re.compile(r"<think>.*?</think>", _re.DOTALL | _re.IGNORECASE)
_CITED_TRAILER_RE = _re.compile(r"<!--\s*cited:.*?-->", _re.DOTALL | _re.IGNORECASE)


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


class SaveToWikiRequest(BaseModel):
    """
    Request body for POST /chat/save-to-wiki (G-P0-1).

    Saves a cleaned chat assistant answer as a wiki/queries/<slug>.md page.
    No inference provider is called (I6); this is a pure DB+file write (I7).
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
    """201 response for POST /chat/save-to-wiki (G-P0-1)."""

    page_id: uuid.UUID = Field(..., description="UUID of the created/updated wiki/queries page")
    file_path: str = Field(..., description="Relative path in the vault (wiki/queries/<slug>.md)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "page_id": "00000000-0000-0000-0000-000000000001",
                "file_path": "wiki/queries/what-is-bge-m3.md",
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
    async with _m.get_session() as session:
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
    async with _m.get_session() as session:
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
    async with _m.get_session() as session:
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

    async with _m.get_session() as session:
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

    async with _m.get_session() as session:
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
    summary="Save a chat answer to the wiki as a query page (G-P0-1)",
    description=(
        "Mirrors nashsu/llm_wiki F6 'Save to Wiki': saves a cleaned assistant answer as "
        "wiki/queries/<slug(title)>.md with type=query frontmatter (I5). "
        "Strips <think>…</think> and <!-- cited: … --> transport artifacts before saving. "
        "Persists via the single write_wiki_page seam (I1 — one data_version bump, no rescan). "
        "No inference provider is called (I6/I7). "
        "Returns {page_id, file_path}. 422 if title/content is missing."
    ),
    responses={
        201: {"description": "Page created or updated"},
        422: {"description": "title or content is missing / empty"},
    },
)
async def save_chat_to_wiki(body: SaveToWikiRequest) -> SaveToWikiResponse:
    """
    POST /chat/save-to-wiki — save a chat answer as a wiki/queries page (G-P0-1).

    Invariant compliance:
      I1 — single write_wiki_page call → one data_version bump, no rescan.
      I5 — Obsidian-valid YAML frontmatter (type=query, sources list, lang=en).
      I6 — NO provider call; pure DB/file write.
      I7 — no loop; single bounded operation.
    """
    from app.ingest.orchestrator import write_wiki_page
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage

    # Clean transport artifacts from content before writing to the wiki (G-P0-1)
    cleaned_content = _clean_chat_content(body.content)
    if not cleaned_content:
        raise HTTPException(
            status_code=422,
            detail="content is empty after stripping think/citation blocks",
        )

    # Build sources list (conversation provenance as a pseudo-source reference)
    sources: list[str] = list(body.sources) if body.sources else []
    if body.conversation_id:
        conv_ref = f"conversation/{body.conversation_id}"
        if conv_ref not in sources:
            sources.append(conv_ref)
    # Ensure at least one source so WikiFrontmatter validator passes (F3 traceability)
    if not sources:
        sources = ["chat"]

    fm = WikiFrontmatter(
        type=PageType.QUERY,
        title=body.title,
        sources=sources,
        lang="en",
    )
    wiki_page = WikiPage(
        title=body.title,
        type=PageType.QUERY,
        content=cleaned_content,
        frontmatter=fm,
    )

    # Single write seam (I1): persists file, Postgres row, Qdrant vector, links, index.md,
    # bumps data_version once. No provider call (I6/I7).
    persisted = await write_wiki_page(None, wiki_page, "")

    return SaveToWikiResponse(
        page_id=persisted.id,
        file_path=persisted.file_path,
    )
