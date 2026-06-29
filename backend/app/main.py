"""
Synapse FastAPI service — v0.5 (M5 Phase 1: F5 retrieval + citations).

Endpoints:
  GET  /status                — vault_id, data_version, started_at, uptime
  GET  /pages                 — paginated list of live pages
  GET  /pages/{id}            — single page by UUID
  POST /ingest/trigger        — sync ingest; HTTP 202 (typed IngestTriggerResponse, AC-D4u)
  POST /ingest/upload         — multipart file upload → ingest; 202 (ADR-0020 Feature U)
  POST /ingest/from-text      — inline text → raw/sources/ + ingest; 202 (ADR-0019 §2.7)
  GET  /ingest/runs           — paginated ingest run history (ADR-0018 §7, AC-BE-IR-1..5)
  GET  /search                — 4-phase RAG retrieval (F5, ADR-0022); read-only (AC-F5-5/6)
  GET  /provider/config       — list effective + raw provider_config rows (F17)
  POST /provider/config       — create/update a provider_config row (F17, §12 — no api key)
  DELETE /provider/config/{id} — delete a provider_config row by UUID
  GET  /graph                 — precomputed graph coords + edges (F4, I2, ADR-0014)
  PATCH /pages/{id}/position — persist manual node drag position; pin the node (Feature A)
  GET  /conversations         — list chat conversations (F6, ADR-0019)
  POST /conversations         — create an empty conversation (F6)
  GET  /conversations/{id}/messages — ordered message history (F6)
  DELETE /conversations/{id}  — soft-delete a conversation (F6)
  POST /chat/stream           — bounded NDJSON streaming chat turn (F6/F7, I6/I7, ADR-0019/0022)
  GET  /import-schedule       — scheduled folder import config + last-run (ADR-0020 Feature S)
  PUT  /import-schedule       — upsert import schedule config (Feature S)
  POST /import-schedule/run-now — trigger one bounded scan immediately (Feature S)
  GET  /config/embedding        — current embedding config (EMBEDDING_URL/MODEL/DIM env vars)

Startup sequence (ordered, per v0.1-architecture §2.5):
  1. Vault skeleton bootstrap (vault.py) — AC-K7-1, I5
  2. Seed vault_state (idempotent) — ADR-0005, AC-F16dv-1
  3. Validate EMBEDDING_DIM vs live bge-m3 + ensure synapse_pages collection — ADR-0004
  4. Start watchdog observer — watcher.py
  5. Start GraphCache background debounce loop — ADR-0014
  6. Start ImportScheduler asyncio background task — ADR-0020 §4.5
  7. Emit AQ-3 INFO line if raw/sources/ is non-empty — ADR-0006

OpenAPI: auto-served at /openapi.json; `make openapi` snapshots to docs/api/openapi.json (D4).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.engine import CursorResult

from app.chat.stream import ChatStreamError, run_chat_stream
from app.config import settings
from app.db import dispose_engine, get_session
from app.embeddings import EmbeddingError, get_embedding_client
from app.graph.cache import GraphCache
from app.graph.engine import GraphEngine
from app.import_scheduler import ImportScheduler, load_schedule, upsert_schedule
from app.ingest.orchestrator import IngestResult, ingest_file
from app.ingest.schemas import Message
from app.models import (
    ChatMessage,
    Conversation,
    ImportSchedule,
    IngestRun,
    Page,
    ProviderConfig,
    VaultState,
)
from app.qdrant_client import ensure_collection
from app.upload import resolve_under_sources, safe_source_name
from app.vault import bootstrap_vault
from app.watcher import start_watcher, stop_watcher

# ── Module-level singletons (initialised in lifespan) ─────────────────────────
_graph_cache: GraphCache | None = None
_import_scheduler: ImportScheduler | None = None

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# ── Startup timestamp ──────────────────────────────────────────────────────────
_started_at: datetime = datetime.now(UTC)


# ── Lifespan ───────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:  # noqa: ARG001
    """
    FastAPI lifespan: startup → yield → shutdown.

    Ordered startup sequence per v0.1-architecture §2.5 + v0.3 graph cache + M4-EXT scheduler.
    """
    global _started_at, _graph_cache, _import_scheduler
    _started_at = datetime.now(UTC)

    # 1. Vault skeleton (K1, I5, AC-K7-1)
    bootstrap_vault()

    # 2. Seed vault_state (ADR-0005, AC-F16dv-1)
    await _seed_vault_state()

    # 3. Validate EMBEDDING_DIM vs live bge-m3 + ensure collection (ADR-0004)
    await _validate_embedding_and_collection()

    # 4. Start watcher (I1)
    loop = asyncio.get_running_loop()
    start_watcher(loop)

    # 5. Initialise GraphCache + background debounce loop (I2, ADR-0014)
    _graph_cache = GraphCache(
        engine=GraphEngine(),
        vault_id=settings.vault_id,
    )
    _graph_cache.start_background_loop()
    logger.info("GraphCache initialised and background loop started")

    # 6. Start ImportScheduler asyncio task (ADR-0020 §4.5; after watcher so copies are seen)
    _import_scheduler = ImportScheduler()
    _import_scheduler.start()
    logger.info("ImportScheduler started")

    yield

    # ── Shutdown ───────────────────────────────────────────────────────────────
    if _import_scheduler is not None:
        _import_scheduler.stop()
    if _graph_cache is not None:
        _graph_cache.stop_background_loop()
    stop_watcher()
    await dispose_engine()


# ── FastAPI app ────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Synapse",
    version="0.5.0",
    description=(
        "Self-organising wiki backend — M5 Phase 1 (F5 4-phase retrieval + [n] citations). "
        "4-signal knowledge graph (F4): direct×3 + source-overlap×4 + Adamic-Adar×1.5 + type×1. "
        "FA2 server-side via igraph (I2); coords persisted in Postgres; "
        "dataVersion-debounced GraphCache; GET /graph precomputed coords (ADR-0014). "
        "Pluggable inference provider (F17): Local/Ollama, API/Anthropic-compatible, "
        "CLI/claude-agent-sdk. Bounded orchestrated ingest loop (I7). "
        "POST /ingest/upload: multipart upload → ingest (ADR-0020 Feature U). "
        "POST /ingest/from-text: inline text → ingest (ADR-0019 §2.7, AC-F6-5 save-to-wiki). "
        "GET /search: F5 4-phase RAG retrieval (ADR-0022, AC-F5-6). "
        "GET|PUT /import-schedule + POST /import-schedule/run-now: scheduled folder import "
        "(ADR-0020 Feature S). "
        "Karpathy LLM Wiki pattern [K1–K8]."
    ),
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# ── CORS ────────────────────────────────────────────────────────────────────────
# Allow the browser frontend (Vite dev server / PWA / Tauri) to call the API.
# Origins come from CORS_ALLOW_ORIGINS (env) — never hardcoded in prod (§12).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Graph-Cache"],  # so the viewer can read cache hit/miss (ADR-0014)
)


# ── Pydantic response models ───────────────────────────────────────────────────


class StatusResponse(BaseModel):
    vault_id: str
    data_version: int
    started_at: datetime
    uptime_seconds: float

    model_config = {
        "json_schema_extra": {
            "example": {
                "vault_id": "default",
                "data_version": 3,
                "started_at": "2026-06-28T10:00:00Z",
                "uptime_seconds": 42.7,
            }
        }
    }


class PageResponse(BaseModel):
    id: uuid.UUID
    vault_id: str
    file_path: str
    title: str | None
    page_type: str | None = Field(None, serialization_alias="type")
    sources: list[str] | None
    content_hash: str
    qdrant_point_id: uuid.UUID | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"populate_by_name": True, "from_attributes": True}


class PageListResponse(BaseModel):
    items: list[PageResponse]
    total: int
    limit: int
    offset: int


class IngestTriggerRequest(BaseModel):
    file_path: str = Field(..., description="Relative path under vault/raw/sources/")


class IngestTriggerResponse(BaseModel):
    """
    Typed 202 body for POST /ingest/trigger (AC-D4u — task_id appears in OpenAPI schema).

    task_id is None in v0.2 (synchronous path); v0.3 fills it with a real async task UUID.
    status: "completed" | "skipped" (I1 fast-path) | "queued"/"running" (async, v0.3+).
    """

    task_id: uuid.UUID | None = Field(
        default=None,
        description="Async task UUID (None in v0.2 synchronous mode; filled in v0.3+)",
    )
    status: str = Field(
        ...,
        description='"completed" or "skipped" (I1 mtime/hash fast-path)',
    )
    page_id: uuid.UUID = Field(..., description="UUID of the ingested page row")

    model_config = {
        "json_schema_extra": {
            "example": {
                "task_id": None,
                "status": "completed",
                "page_id": "00000000-0000-0000-0000-000000000001",
            }
        }
    }


# ── Provider config Pydantic models (F17 CRUD — §12: NO api_key field) ────────

_VALID_PROVIDER_TYPES = {"local", "api", "cli"}
_VALID_SCOPES = {"global", "vault", "operation"}
_VALID_OPERATIONS = {"ingest", "chat", "lint"}


class ProviderConfigCreate(BaseModel):
    """
    Request body for POST /provider/config (F17).

    Stores NO API key (§12 / ADR-0008 §3). Keys are env-only inside provider/.
    model_id must be provided explicitly — no hardcoded defaults in app code (AC-F17-8).
    """

    scope: str = Field(..., description="global | vault | operation")
    operation: str | None = Field(
        default=None,
        description="ingest | chat | lint; required when scope='operation'",
    )
    vault_id: str | None = Field(
        default=None,
        description="Required when scope='vault' or 'operation'",
    )
    provider_type: str = Field(..., description="local | api | cli")
    model_id: str = Field(
        ...,
        description="Model name (e.g. claude-sonnet-4-6); lives only in DB rows (AC-F17-8)",
    )
    base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible endpoint; NULL for Anthropic/local default",
    )
    max_iter: int = Field(default=3, ge=1, le=20, description="Orchestrated-loop cap (I7)")
    token_budget: int = Field(
        default=60000,
        ge=1000,
        le=1_000_000,
        description="Loop token budget (I7)",
    )
    is_fallback: bool = Field(default=False, description="Marks the single fallback row")

    @field_validator("provider_type")
    @classmethod
    def _valid_provider_type(cls, v: str) -> str:
        if v not in _VALID_PROVIDER_TYPES:
            raise ValueError(
                f"provider_type must be one of {sorted(_VALID_PROVIDER_TYPES)}, got {v!r}"
            )
        return v

    @field_validator("scope")
    @classmethod
    def _valid_scope(cls, v: str) -> str:
        if v not in _VALID_SCOPES:
            raise ValueError(f"scope must be one of {sorted(_VALID_SCOPES)}, got {v!r}")
        return v

    @field_validator("operation")
    @classmethod
    def _valid_operation(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_OPERATIONS:
            raise ValueError(
                f"operation must be one of {sorted(_VALID_OPERATIONS)} or null, got {v!r}"
            )
        return v


class ProviderConfigResponse(BaseModel):
    """API response shape for a provider_config row (§12: no api_key field)."""

    id: uuid.UUID
    scope: str
    operation: str | None
    vault_id: str | None
    provider_type: str
    model_id: str
    base_url: str | None
    max_iter: int
    token_budget: int
    is_fallback: bool
    created_at: Any
    updated_at: Any

    model_config = {"from_attributes": True}


class ProviderConfigListResponse(BaseModel):
    items: list[ProviderConfigResponse]
    total: int


# ── Ingest run Pydantic models (ADR-0018 §7, AC-BE-IR-1) ──────────────────────


class IngestRunResponse(BaseModel):
    """
    API response shape for one ingest_runs row (ADR-0018 §7, AC-BE-IR-1).

    Column aliases (no DB rename — ADR-0018 §7 decision):
      max_iter_used  → iterations_used
      finished_at    → completed_at
    total_cost_usd serialised as a float; frontend formats to exactly 4dp (I7).
    """

    id: uuid.UUID
    vault_id: str
    status: str = Field(description="running | completed | failed | converged_false (ADR-0018 §7)")
    provider_type: str = Field(description="local | api | cli")
    pages_created: int = Field(description="Wiki pages persisted during this run")
    iterations_used: int = Field(
        description="Iterations consumed (aliases max_iter_used; 0 for delegated)"
    )
    total_cost_usd: float = Field(
        description="Total cost in USD; 0.0 for local/cli; serialised as number (I7)"
    )
    started_at: datetime
    completed_at: datetime | None = Field(
        description="Run finish time (aliases finished_at); null for running rows"
    )
    error_message: str | None = Field(description="Error detail for failed runs; null otherwise")

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,
        "json_schema_extra": {
            "example": {
                "id": "00000000-0000-0000-0000-000000000001",
                "vault_id": "default",
                "status": "completed",
                "provider_type": "api",
                "pages_created": 3,
                "iterations_used": 2,
                "total_cost_usd": 0.0042,
                "started_at": "2026-06-28T10:00:00Z",
                "completed_at": "2026-06-28T10:00:05Z",
                "error_message": None,
            }
        },
    }


class IngestRunListResponse(BaseModel):
    """
    Paginated list response for GET /ingest/runs (ADR-0018 §7, AC-BE-IR-1).
    Ordered started_at DESC (AC-BE-IR-3).
    """

    items: list[IngestRunResponse]
    total: int
    limit: int
    offset: int


# ── Upload Pydantic models (Feature U, ADR-0020 §2.1) ─────────────────────────


class UploadResponse(BaseModel):
    """
    202 response body for POST /ingest/upload (ADR-0020 §2.1, M4-EXT non-blocking).

    file_path:  saved path relative to vault_root (e.g. "raw/sources/notes.md")
    status:     always "queued" — the watcher picks up the file asynchronously.
    overwritten: true if a same-name file already existed and was replaced on disk.

    page_id is not returned because ingest is async (watcher-driven); poll GET /ingest/runs
    or GET /pages to confirm the page exists after ingest completes (~15-30s).
    """

    file_path: str = Field(
        ...,
        description='Saved path relative to vault_root, e.g. "raw/sources/notes.md"',
    )
    status: str = Field(
        ...,
        description='"queued" — file saved to raw/sources/; watcher ingests asynchronously.',
    )
    overwritten: bool = Field(
        ...,
        description="True if a same-name file already existed and was replaced on disk",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "file_path": "raw/sources/notes.md",
                "status": "queued",
                "overwritten": False,
            }
        }
    }


# ── GET /search Pydantic models (F5, ADR-0022 §2.5) ──────────────────────────


class SearchResultItem(BaseModel):
    """
    One citation entry in the GET /search response (ADR-0022 §2.5, AC-F5-6).

    Maps to Citation.{n, ref.id, ref.title, ref.slug, score, phase}.
    """

    n: int = Field(..., description="1-based citation index, contiguous from 1")
    id: str = Field(..., description="UUID of the pages row (== Qdrant point id, ADR-0002)")
    title: str = Field(..., description="Frontmatter title or filename stem (never empty, §2.6)")
    slug: str = Field(..., description="slugify(title) — derived, not a DB column (§2.6)")
    score: float = Field(..., description="Cosine similarity (vector) or edge weight (expansion)")
    phase: str = Field(..., description='"vector" | "expansion"')

    model_config = {
        "json_schema_extra": {
            "example": {
                "n": 1,
                "id": "00000000-0000-0000-0000-000000000001",
                "title": "Homelab Setup",
                "slug": "homelab-setup",
                "score": 0.87,
                "phase": "vector",
            }
        }
    }


class SearchResponse(BaseModel):
    """
    GET /search response (ADR-0022 §2.5, AC-F5-6).

    read-only — never bumps data_version (AC-F5-5).
    0-hit → 200 with empty results + empty context (AC-F5-7a).
    """

    query: str
    context: str = Field(
        ...,
        description="Assembled context string with inline [n] markers (≤ token_budget, ADR-0022)",
    )
    results: list[SearchResultItem] = Field(
        ...,
        description="Citations in rank order (vector seeds first, then expansions by edge weight)",
    )
    data_version: int = Field(
        ...,
        description="Snapshot read BEFORE assembly — proves the call is read-only (AC-F5-5)",
    )
    approx_tokens: int = Field(..., description="char/4 estimate of context length")
    token_budget: int = Field(..., description="20% of context_window used as the retrieval slice")

    model_config = {
        "json_schema_extra": {
            "example": {
                "query": "homelab docker services",
                "context": "[1] Homelab Setup\nDocker Compose ...\n",
                "results": [
                    {
                        "n": 1,
                        "id": "00000000-0000-0000-0000-000000000001",
                        "title": "Homelab Setup",
                        "slug": "homelab-setup",
                        "score": 0.87,
                        "phase": "vector",
                    }
                ],
                "data_version": 5,
                "approx_tokens": 512,
                "token_budget": 6553,
            }
        }
    }


# ── POST /ingest/from-text Pydantic models (ADR-0019 §2.7, AC-F6-5) ──────────


class IngestFromTextRequest(BaseModel):
    """
    Request body for POST /ingest/from-text (ADR-0019 §2.7, AC-F6-5 save-to-wiki).

    Writes ``text`` to ``vault/raw/sources/chat-{message_id}.md`` (or a derived name)
    and runs the same ``ingest_file`` seam (ADR-0003).  No new ingest logic — only a
    file-materialisation step.
    """

    text: str = Field(
        ...,
        min_length=1,
        description="Raw text to ingest (e.g. an assistant message)",
    )
    source_hint: str | None = Field(
        default=None,
        description=(
            "Optional hint for the output filename stem, e.g. a message_id or short slug. "
            "Sanitised to basename; falls back to 'chat-<uuid>' when omitted or unsafe."
        ),
    )
    vault_id: str | None = Field(default=None, description="Defaults to settings.vault_id")

    model_config = {
        "json_schema_extra": {
            "example": {
                "text": "# Homelab notes\nDocker services on TrueNAS...",
                "source_hint": "chat-homelab-notes",
                "vault_id": None,
            }
        }
    }


class IngestFromTextResponse(BaseModel):
    """202 response for POST /ingest/from-text (ADR-0019 §2.7)."""

    file_path: str = Field(..., description="Path written relative to vault_root")
    status: str = Field(..., description='"queued" — watcher ingests asynchronously')
    page_id: uuid.UUID | None = Field(
        default=None,
        description=(
            "Page UUID when ingest completes synchronously (trigger path); "
            "null when async (watcher path)."
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "file_path": "raw/sources/chat-homelab-notes.md",
                "status": "queued",
                "page_id": None,
            }
        }
    }


# ── Import-schedule Pydantic models (Feature S, ADR-0020 §4.6) ────────────────

_VALID_FREQUENCIES = {"15m", "1h", "6h", "daily"}


class ImportScheduleResponse(BaseModel):
    """
    GET /import-schedule response body (ADR-0020 §4.6).

    Returns the current config + last-run status for the vault's import schedule.
    Returns sane defaults (enabled=false, frequency="1h") if no row exists yet.
    """

    enabled: bool = Field(default=False, description="Scheduler is enabled")
    source_dir: str | None = Field(
        default=None,
        description="Container-visible absolute path to scan (e.g. /import)",
    )
    frequency: str = Field(
        default="1h",
        description="'15m' | '1h' | '6h' | 'daily'",
    )
    last_run_at: datetime | None = Field(
        default=None,
        description="Timestamp of the last completed scan; null if never run",
    )
    last_status: str | None = Field(
        default=None,
        description="ok | error | running | skipped_disabled | dir_missing | null",
    )
    last_imported_count: int = Field(
        default=0,
        description="Files copied (new/changed) during the last scan",
    )
    last_error: str | None = Field(
        default=None,
        description="Error detail from the last failed scan; null on success",
    )

    model_config = {"from_attributes": True}


class ImportSchedulePutBody(BaseModel):
    """Request body for PUT /import-schedule (ADR-0020 §4.6)."""

    enabled: bool | None = Field(default=None, description="Enable or disable the scheduler")
    source_dir: str | None = Field(
        default=None,
        description="Container-visible path (e.g. /import); null to clear",
    )
    frequency: str | None = Field(
        default=None,
        description="'15m' | '1h' | '6h' | 'daily'",
    )

    @field_validator("frequency")
    @classmethod
    def _valid_frequency(cls, v: str | None) -> str | None:
        if v is not None and v not in _VALID_FREQUENCIES:
            raise ValueError(f"frequency must be one of {sorted(_VALID_FREQUENCIES)}, got {v!r}")
        return v


class ImportSchedulePutResponse(ImportScheduleResponse):
    """
    PUT /import-schedule response body (ADR-0020 §4.6).

    Extends ImportScheduleResponse with dir validation fields (save-then-warn).
    """

    dir_ok: bool = Field(
        default=True,
        description="False if source_dir does not exist/is not readable inside the container",
    )
    dir_message: str | None = Field(
        default=None,
        description="Warning message when dir_ok is False; null when ok",
    )


class RunNowResponse(BaseModel):
    """202 response body for POST /import-schedule/run-now (ADR-0020 §4.6)."""

    status: str = Field(default="started", description="'started' — scan running in background")


# ── Chat Pydantic models (F6/F7, ADR-0019 §2.2/§2.5) ──────────────────────────

_VALID_CHAT_ROLES = {"user", "assistant", "system"}


class ConversationResponse(BaseModel):
    """API shape for one conversations row (ADR-0019 §2.5)."""

    id: uuid.UUID
    vault_id: str
    title: str | None
    created_at: datetime
    updated_at: datetime

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


class ChatMessageIn(BaseModel):
    """One turn in a ChatRequest. Mirrors the backend-neutral Message shape (I6)."""

    role: str = Field(..., description="user | assistant | system")
    content: str = Field(..., min_length=1)

    @field_validator("role")
    @classmethod
    def _valid_role(cls, v: str) -> str:
        if v not in _VALID_CHAT_ROLES:
            raise ValueError(f"role must be one of {sorted(_VALID_CHAT_ROLES)}, got {v!r}")
        return v


class ChatRequest(BaseModel):
    """
    Request body for POST /chat/stream (ADR-0019 §2.2).

    The server NEVER accepts provider_type / model_id (I6 / Do-NOT #4): the backend resolves
    `resolve_provider_config("chat", vault_id)`. `operation` is fixed to "chat" so the same
    abstraction can route ingest-vs-chat differently.
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


# ── GET /status ────────────────────────────────────────────────────────────────


@app.get(
    "/status",
    response_model=StatusResponse,
    summary="Service health + data_version",
    description=(
        "Returns vault_id, current data_version (monotonic ingest counter), "
        "service started_at, and uptime_seconds. (AC-REST-1, AC-F16dv-3)"
    ),
)
async def get_status() -> StatusResponse:
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        data_version = state.data_version if state is not None else 0

    now = datetime.now(UTC)
    uptime = (now - _started_at).total_seconds()
    return StatusResponse(
        vault_id=settings.vault_id,
        data_version=data_version,
        started_at=_started_at,
        uptime_seconds=uptime,
    )


# ── GET /pages ─────────────────────────────────────────────────────────────────


@app.get(
    "/pages",
    response_model=PageListResponse,
    summary="List live pages",
    description=(
        "Paginated list of pages where deleted_at IS NULL. " "Supports limit/offset. (AC-REST-2)"
    ),
)
async def list_pages(
    limit: int = Query(default=50, ge=1, le=500, description="Max rows to return"),
    offset: int = Query(default=0, ge=0, description="Row offset for pagination"),
) -> PageListResponse:
    async with get_session() as session:
        total_row = await session.execute(
            select(func.count())
            .select_from(Page)
            .where(
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
        )
        total: int = total_row.scalar_one()

        rows = await session.execute(
            select(Page)
            .where(
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
            .order_by(Page.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        pages = rows.scalars().all()

    return PageListResponse(
        items=[_page_to_response(p) for p in pages],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── GET /pages/{id} ────────────────────────────────────────────────────────────


@app.get(
    "/pages/{page_id}",
    response_model=PageResponse,
    summary="Get a single page by UUID",
    description=(
        "Returns full page metadata; 404 if unknown or deleted; 422 on invalid UUID. "
        "(AC-REST-3, AC-REST-6)"
    ),
)
async def get_page(page_id: uuid.UUID) -> PageResponse:
    async with get_session() as session:
        row = await session.execute(
            select(Page).where(
                Page.id == page_id,
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
        )
        page = row.scalar_one_or_none()

    if page is None:
        raise HTTPException(status_code=404, detail=f"Page {page_id} not found")

    return _page_to_response(page)


# ── PATCH /pages/{id}/position ────────────────────────────────────────────────


class PatchPositionRequest(BaseModel):
    """Body for PATCH /pages/{page_id}/position (Feature A)."""

    x: float = Field(..., description="New x coordinate (FR space)")
    y: float = Field(..., description="New y coordinate (FR space)")


class PatchPositionResponse(BaseModel):
    """Response for PATCH /pages/{page_id}/position (Feature A)."""

    id: str
    x: float
    y: float
    pinned: bool


@app.patch(
    "/pages/{page_id}/position",
    response_model=PatchPositionResponse,
    summary="Persist a manual node drag position and pin the node",
    description=(
        "Updates pages.x/y and sets pages.pinned=true so the node stays at the dropped "
        "position across FR recomputes.  Also patches the live GraphCache snapshot in place "
        "so the next GET /graph HIT reflects the new position immediately. "
        "Does NOT trigger FR, does NOT bump data_version — O(1). (Feature A, I2)"
    ),
    responses={
        200: {"description": "Position updated and node pinned"},
        404: {"description": "Page not found"},
    },
)
async def patch_node_position(
    page_id: uuid.UUID,
    body: PatchPositionRequest,
) -> PatchPositionResponse:
    """
    PATCH /pages/{page_id}/position — persist a manual drag position (Feature A).

    1. UPDATE pages SET x=:x, y=:y, pinned=true WHERE id=:id and vault_id=:vid.
    2. Patch the live GraphCache snapshot in-memory so HIT path returns new coords.
    3. Return 200 {id, x, y, pinned: true}.

    Does NOT bump data_version; does NOT trigger FR recompute (I2).
    404 if the page is missing or soft-deleted.
    """
    from sqlalchemy import text as sa_text

    async with get_session() as session:
        result = await session.execute(
            sa_text(
                "UPDATE pages "
                "SET x = :x, y = :y, pinned = true "
                "WHERE id = CAST(:page_id AS uuid) "
                "  AND vault_id = :vault_id "
                "  AND deleted_at IS NULL "
                "RETURNING id"
            ).bindparams(
                x=body.x,
                y=body.y,
                page_id=str(page_id),
                vault_id=settings.vault_id,
            )
        )
        row = result.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Page {page_id} not found or deleted",
        )

    # Patch the live snapshot so the next HIT already has the new coords (Feature A).
    node_id_str = str(page_id)
    if _graph_cache is not None:
        found = _graph_cache.patch_node_position(node_id_str, body.x, body.y)
        logger.debug(
            "patch_node_position: cache patch %s for node_id=%s",
            "succeeded" if found else "no-op (no snapshot yet)",
            node_id_str,
        )

    return PatchPositionResponse(id=node_id_str, x=body.x, y=body.y, pinned=True)


# ── POST /ingest/trigger ───────────────────────────────────────────────────────


@app.post(
    "/ingest/trigger",
    response_model=IngestTriggerResponse,
    status_code=202,
    summary="Manually trigger ingest of a single file",
    description=(
        "Synchronously ingests the file at file_path through the seam. "
        "Returns HTTP 202 with typed {task_id, status, page_id} (ADR-0006, AC-REST-4, AC-D4u). "
        "status is 'completed' or 'skipped' (I1 fast-path)."
    ),
    responses={
        202: {"description": "Ingest accepted and completed"},
        422: {"description": "Validation error (missing file_path, bad format, or file not found)"},
    },
)
async def trigger_ingest(body: IngestTriggerRequest) -> IngestTriggerResponse:
    """
    Trigger incremental ingest of a single file (K2 partial, ADR-0006, AC-D4u).

    Resolves the file path under vault_root if relative.
    Runs ingest_file through the seam (ADR-0003); never touches DB/Qdrant directly.
    Returns 202 per ADR-0006 contract with a typed schema so task_id appears in OpenAPI (AC-D4u).
    """
    from pathlib import Path

    # Resolve relative or absolute path
    path = Path(body.file_path)
    if not path.is_absolute():
        path = settings.vault_root / path

    if not path.exists():
        raise HTTPException(
            status_code=422,
            detail=f"File not found: {body.file_path}",
        )

    result: IngestResult = await ingest_file(path)

    return IngestTriggerResponse(
        task_id=None,
        status=result.status,
        page_id=result.page_id,
    )


# ── POST /ingest/upload ────────────────────────────────────────────────────────


@app.post(
    "/ingest/upload",
    response_model=UploadResponse,
    status_code=202,
    summary="Upload a document for async watcher-driven ingest",
    description=(
        "Feature U (ADR-0020 §2, M4-EXT non-blocking). "
        "Accepts a single text/markdown file (.md/.txt/.markdown) via multipart/form-data. "
        "Writes to vault/raw/sources/<name> (I5) and returns 202 immediately. "
        "The watcher observes the write and ingests asynchronously (~15-30s). "
        "Strict basename-only path sanitization (ADR-0020 §2.2). "
        "413 on oversize (MAX_UPLOAD_BYTES). 415 for non-text (names F12/M5). "
        "422 for unsafe filename. 202 {file_path, status:'queued', overwritten}."
    ),
    responses={
        202: {"description": "File saved; watcher will ingest asynchronously"},
        413: {"description": "File exceeds MAX_UPLOAD_BYTES"},
        415: {
            "description": (
                "Only .md/.txt/.markdown accepted in v0.4; " "multi-format (F12) is planned for M5"
            )
        },
        422: {"description": "Filename is empty or unsafe after sanitization"},
    },
)
async def upload_ingest(
    file: UploadFile = File(..., description="The document to upload"),
) -> UploadResponse:
    """
    POST /ingest/upload — non-blocking multipart upload (ADR-0020 Feature U, §2).

    1. Validate extension (hard) + Content-Type (soft advisory) → 415 on non-text.
    2. Stream body to a temp file, abort at MAX_UPLOAD_BYTES              → 413.
    3. safe_source_name(filename)                                          → 422 on unsafe.
    4. resolve_under_sources(name) containment check                       → 422 on escape.
    5. overwritten = dst.exists()
    6. Atomically move temp file to dst (same-fs rename inside /vault).
    7. Return 202 {file_path, status:"queued", overwritten} immediately.

    The WATCHER observes the vault/raw/sources/ write and ingests asynchronously.
    This is the same path Feature S (scheduled copy) uses — no double-ingest (I9).
    Poll GET /ingest/runs or GET /pages to confirm ingest completion (~15-30s).

    Security: basename-only; no caller-controlled path segments; containment-checked.
    I1: watcher's mtime/hash gate deduplicates re-uploads of unchanged content.
    I5: writes ONLY to vault/raw/sources/ — never to wiki/ or .obsidian/.
    """
    import tempfile

    max_bytes: int = settings.max_upload_bytes

    # ── Extension check (authoritative; MIME is advisory) ────────────────────
    # Do this BEFORE reading bytes (fail fast)
    raw_name: str = file.filename or ""
    # safe_source_name raises 415 for non-text extensions, 422 for unsafe
    name = safe_source_name(raw_name)

    # ── Stream body with byte cap (I7) ───────────────────────────────────────
    raw_sources = settings.raw_sources_dir
    raw_sources.mkdir(parents=True, exist_ok=True)

    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(raw_sources), suffix=".upload_tmp")
    bytes_read = 0
    try:
        with open(tmp_fd, "wb") as tmp_file:
            chunk_size = 65_536  # 64 KB chunks
            while True:
                chunk = await file.read(chunk_size)
                if not chunk:
                    break
                bytes_read += len(chunk)
                if bytes_read > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit."),
                    )
                tmp_file.write(chunk)
    except HTTPException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    except Exception as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Upload read error: {exc}") from exc
    finally:
        await file.close()

    # ── Containment check ────────────────────────────────────────────────────
    try:
        dst = resolve_under_sources(name)
    except HTTPException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

    # ── Atomic move (same-fs: rename within /vault/raw/sources/) ────────────
    overwritten: bool = dst.exists()
    try:
        Path(tmp_name).replace(dst)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to write file: {exc}") from exc

    # ── Return 202 immediately — watcher ingests asynchronously ──────────────
    rel_path = str(dst.relative_to(settings.vault_root))
    logger.info("upload_ingest: saved %s (%d bytes) — watcher will ingest", name, bytes_read)
    return UploadResponse(
        file_path=rel_path,
        status="queued",
        overwritten=overwritten,
    )


# ── POST /ingest/from-text ────────────────────────────────────────────────────


@app.post(
    "/ingest/from-text",
    response_model=IngestFromTextResponse,
    status_code=202,
    summary="Write inline text to raw/sources/ and queue watcher-driven ingest",
    description=(
        "Save-to-wiki seam (ADR-0019 §2.7, AC-F6-5). "
        "Materialises ``text`` to ``vault/raw/sources/chat-<hint>.md`` and returns 202 "
        "immediately. The watcher picks up the file and runs the full ingest pipeline "
        "(no new ingest logic — ADR-0003 guarantee, I1/I6). "
        "``source_hint`` is sanitised to a safe basename; falls back to ``chat-<uuid>`` when "
        "omitted or unsafe. 422 on empty text."
    ),
    responses={
        202: {"description": "Text saved; watcher will ingest asynchronously"},
        422: {"description": "Validation error (text empty or too long)"},
    },
)
async def ingest_from_text(body: IngestFromTextRequest) -> IngestFromTextResponse:
    """
    POST /ingest/from-text — materialise inline text to raw/sources/ and enqueue watcher.

    1. Derive a safe filename from source_hint (basename-only, slug-safe fallback).
    2. Write the text to vault/raw/sources/<name>.md (atomically via temp → rename).
    3. Return 202 {file_path, status:'queued'} — watcher ingests asynchronously.

    I1: watcher's mtime/hash gate deduplicates re-posts of identical content.
    I5: writes ONLY to vault/raw/sources/ — never to wiki/ or .obsidian/.
    I6: inference goes through the existing ingest pipeline (ADR-0003, no shortcut).
    """
    import re as _re
    import tempfile as _tempfile

    _SLUG_RE_MAIN = _re.compile(r"[^a-z0-9_-]+")

    # Derive a safe filename stem from the hint (or a fresh UUID).
    raw_hint = (body.source_hint or "").strip()
    if raw_hint:
        stem = _SLUG_RE_MAIN.sub("-", raw_hint.lower()).strip("-")[:80]
        if not stem:
            stem = f"chat-{uuid.uuid4().hex[:8]}"
    else:
        stem = f"chat-{uuid.uuid4().hex[:8]}"
    filename = f"{stem}.md"

    raw_sources = settings.raw_sources_dir
    raw_sources.mkdir(parents=True, exist_ok=True)
    dst = raw_sources / filename

    # Atomic write via temp → rename (same approach as upload_ingest).
    tmp_fd, tmp_name = _tempfile.mkstemp(dir=str(raw_sources), suffix=".fromtext_tmp")
    try:
        with open(tmp_fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(body.text)
    except Exception as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to write text: {exc}") from exc

    try:
        Path(tmp_name).replace(dst)
    except OSError as exc:
        Path(tmp_name).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Failed to persist file: {exc}") from exc

    rel_path = str(dst.relative_to(settings.vault_root))
    logger.info(
        "ingest_from_text: saved %s (%d chars) — watcher will ingest",
        filename,
        len(body.text),
    )
    return IngestFromTextResponse(file_path=rel_path, status="queued", page_id=None)


# ── GET /search ───────────────────────────────────────────────────────────────


@app.get(
    "/search",
    response_model=SearchResponse,
    summary="4-phase RAG retrieval (F5, ADR-0022)",
    description=(
        "Run the F5 4-phase retrieval pipeline (ADR-0022 §2.2, AC-F5-6) and return a grounded "
        "context string + citation list. "
        "Phase 1: dense vector search via bge-m3 (Qdrant, top-k). "
        "Phase 2: BFS graph-expansion over the `edges` table (depth ≤ 2). "
        "Phase 3: token-budget allocation (20% of context_window, F14). "
        "Phase 4: context assembly with inline [n] markers. "
        "0-hit query → 200 with empty results + empty context (AC-F5-7a). "
        "READ-ONLY — never bumps data_version (AC-F5-5). "
        "Documented in openapi.json (I8, AC-F5-6)."
    ),
    responses={
        200: {"description": "Retrieval result (0-hit → empty results array)"},
        422: {"description": "Validation error (k out of range or missing q)"},
    },
)
async def search(
    q: str = Query(..., min_length=1, description="The query string to retrieve context for"),
    vault_id: str | None = Query(
        default=None,
        description="Vault scope; defaults to settings.vault_id",
    ),
    k: int = Query(
        default=8,
        ge=1,
        le=50,
        description="Dense top-k for the vector phase (1..50); default 8 (ADR-0022 §2.1)",
    ),
    context_window: int | None = Query(
        default=None,
        ge=4096,
        le=1_000_000,
        description="Context window override (4096..1_000_000); null → 32 768 default (F14)",
    ),
) -> SearchResponse:
    """
    GET /search — F5 4-phase retrieval (ADR-0022, AC-F5-6).

    Single bounded pass (I7): Qdrant bge-m3 dense search → edges BFS expansion → budget
    allocation → context assembly. Zero inference calls, zero vault walk (I1). Read-only
    — data_version is unchanged (AC-F5-5).
    """
    from app.chat.context import DEFAULT_CONTEXT_WINDOW as _DEFAULT_WINDOW
    from app.rag.retrieval import retrieve

    effective_vault_id = vault_id or settings.vault_id
    window = context_window or _DEFAULT_WINDOW

    rctx = await retrieve(
        query=q,
        vault_id=effective_vault_id,
        context_window=window,
        k=k,
    )

    results: list[SearchResultItem] = [
        SearchResultItem(
            n=c.n,
            id=c.ref.id,
            title=c.ref.title,
            slug=c.ref.slug,
            score=c.score,
            phase=c.phase,
        )
        for c in rctx.citations
    ]

    return SearchResponse(
        query=rctx.query,
        context=rctx.text,
        results=results,
        data_version=rctx.data_version,
        approx_tokens=rctx.approx_tokens,
        token_budget=rctx.token_budget,
    )


# ── GET /ingest/runs ───────────────────────────────────────────────────────────


@app.get(
    "/ingest/runs",
    response_model=IngestRunListResponse,
    summary="List ingest run history",
    description=(
        "Returns a paginated, started_at DESC list of ingest_runs rows. "
        "Exposes the I7 cost ledger to the user (AC-BE-IR-1..5, ADR-0018 §7). "
        "limit: 1..100 default 20; offset: >=0 default 0; vault_id: optional UUID filter. "
        "Column aliases: max_iter_used→iterations_used, finished_at→completed_at. "
        "total_cost_usd serialised as a number; frontend formats to exactly 4dp (I7)."
    ),
    responses={
        200: {"description": "Paginated ingest run list"},
        422: {"description": "Validation error (limit out of 1..100 or offset < 0)"},
    },
)
async def list_ingest_runs(
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
        description="Max rows to return (1..100); 422 on out-of-range (AC-BE-IR-2)",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="Row offset for pagination (>=0); 422 on negative (AC-BE-IR-2)",
    ),
    vault_id: str | None = Query(
        default=None,
        description="Optional vault_id filter; omit to list all vaults (AC-BE-IR-2)",
    ),
) -> IngestRunListResponse:
    """
    GET /ingest/runs — paginated ingest run history (ADR-0018 §7, AC-BE-IR-1..5).

    Plain read query — no heavy computation (pure SELECT, ORDER BY, LIMIT/OFFSET).
    Filters by vault_id when provided.
    Orders by started_at DESC (AC-BE-IR-3).
    422 enforced by Query(ge=1, le=100) / Query(ge=0) validators (AC-BE-IR-5).
    """
    async with get_session() as session:
        # COUNT query (filtered)
        count_stmt = select(func.count()).select_from(IngestRun)
        if vault_id is not None:
            count_stmt = count_stmt.where(IngestRun.vault_id == vault_id)
        total_row = await session.execute(count_stmt)
        total: int = total_row.scalar_one()

        # Data query (filtered, ordered, paginated)
        data_stmt = select(IngestRun)
        if vault_id is not None:
            data_stmt = data_stmt.where(IngestRun.vault_id == vault_id)
        data_stmt = data_stmt.order_by(IngestRun.started_at.desc()).offset(offset).limit(limit)
        rows = await session.execute(data_stmt)
        runs = list(rows.scalars().all())

    items = [_ingest_run_to_response(r) for r in runs]
    return IngestRunListResponse(items=items, total=total, limit=limit, offset=offset)


def _ingest_run_to_response(run: IngestRun) -> IngestRunResponse:
    """
    Map IngestRun ORM row → IngestRunResponse.

    Applies the two ADR-0018 §7 aliases:
      max_iter_used  → iterations_used
      finished_at    → completed_at
    total_cost_usd converted from Decimal (Numeric column) to float for JSON serialisation.
    completed_at is None when status == 'running' (run still in progress).
    """
    completed_at: datetime | None = None if run.status == "running" else run.finished_at
    return IngestRunResponse(
        id=run.id,
        vault_id=run.vault_id,
        status=run.status,
        provider_type=run.provider_type,
        pages_created=run.pages_created,
        iterations_used=run.max_iter_used,
        total_cost_usd=float(run.total_cost_usd),
        started_at=run.started_at,
        completed_at=completed_at,
        error_message=run.error_message,
    )


# ── GET /provider/config ───────────────────────────────────────────────────────


@app.get(
    "/provider/config",
    response_model=ProviderConfigListResponse,
    summary="List provider_config rows",
    description=(
        "Returns all raw provider_config rows. "
        "No API key field is stored or returned (§12). (F17, AC-F17-6)"
    ),
)
async def list_provider_configs(
    scope: str | None = Query(default=None, description="Filter by scope (global|vault|operation)"),
    vault_id: str | None = Query(default=None, description="Filter by vault_id"),
) -> ProviderConfigListResponse:
    async with get_session() as session:
        stmt = select(ProviderConfig)
        if scope is not None:
            stmt = stmt.where(ProviderConfig.scope == scope)
        if vault_id is not None:
            stmt = stmt.where(ProviderConfig.vault_id == vault_id)
        stmt = stmt.order_by(ProviderConfig.created_at.asc())
        rows = await session.execute(stmt)
        configs = list(rows.scalars().all())
        total = len(configs)
        items = [ProviderConfigResponse.model_validate(c) for c in configs]

    return ProviderConfigListResponse(items=items, total=total)


# ── POST /provider/config ──────────────────────────────────────────────────────


@app.post(
    "/provider/config",
    response_model=ProviderConfigResponse,
    status_code=201,
    summary="Create or update a provider_config row",
    description=(
        "Create a new provider_config row. "
        "provider_type must be one of: local | api | cli. "
        "NO api_key field is accepted or stored — keys are env-only (§12). (F17, ADR-0008)"
    ),
    responses={
        201: {"description": "Row created"},
        422: {"description": "Validation error (invalid provider_type, scope, or operation)"},
    },
)
async def create_provider_config(body: ProviderConfigCreate) -> ProviderConfigResponse:
    """
    Create a new provider_config row for F17 provider selection (ADR-0008).

    Scope validation: if scope='operation', operation must be non-null.
    No API key field: keys live in environment only (§12, ADR-0008 §3).
    """
    if body.scope == "operation" and body.operation is None:
        raise HTTPException(
            status_code=422,
            detail="operation must be provided when scope='operation'",
        )
    if body.scope in {"vault", "operation"} and not body.vault_id:
        raise HTTPException(
            status_code=422,
            detail=f"vault_id must be provided when scope={body.scope!r}",
        )

    async with get_session() as session:
        row = ProviderConfig(
            id=uuid.uuid4(),
            scope=body.scope,
            operation=body.operation,
            vault_id=body.vault_id,
            provider_type=body.provider_type,
            model_id=body.model_id,
            base_url=body.base_url,
            max_iter=body.max_iter,
            token_budget=body.token_budget,
            is_fallback=body.is_fallback,
        )
        session.add(row)
        await session.flush()
        response = ProviderConfigResponse.model_validate(row)

    return response


# ── DELETE /provider/config/{id} ───────────────────────────────────────────────


@app.delete(
    "/provider/config/{config_id}",
    status_code=204,
    summary="Delete a provider_config row by UUID",
    description="Hard-delete the provider_config row with the given id. (F17)",
    responses={
        204: {"description": "Row deleted"},
        404: {"description": "Row not found"},
    },
)
async def delete_provider_config(config_id: uuid.UUID) -> None:
    """Delete a provider_config row (F17). 404 if not found."""
    from sqlalchemy import delete as sa_delete

    async with get_session() as session:
        result = await session.execute(
            sa_delete(ProviderConfig).where(ProviderConfig.id == config_id)
        )
        deleted = cast("CursorResult[Any]", result).rowcount

    if deleted == 0:
        raise HTTPException(
            status_code=404,
            detail=f"provider_config {config_id} not found",
        )


# ── GET /config/embedding ─────────────────────────────────────────────────────


class EmbeddingConfigResponse(BaseModel):
    embedding_url: str = Field(description="HTTP endpoint for embeddings (EMBEDDING_URL env)")
    embedding_model: str = Field(description="Model name for embeddings (EMBEDDING_MODEL env)")
    embedding_dim: int = Field(description="Vector dimension (EMBEDDING_DIM env)")


@app.get(
    "/config/embedding",
    response_model=EmbeddingConfigResponse,
    summary="Get current embedding configuration",
    description=(
        "Returns the active embedding config read from environment variables "
        "(EMBEDDING_URL, EMBEDDING_MODEL, EMBEDDING_DIM). Read-only — edit .env to change. (I9)"
    ),
)
async def get_embedding_config() -> EmbeddingConfigResponse:
    """Return current embedding settings (F17 / I9)."""
    return EmbeddingConfigResponse(
        embedding_url=settings.embedding_url,
        embedding_model=settings.embedding_model,
        embedding_dim=settings.embedding_dim,
    )


# ── Import schedule REST (Feature S, ADR-0020 §4.6) ───────────────────────────


def _schedule_to_response(schedule: ImportSchedule | None) -> ImportScheduleResponse:
    """Convert an ImportSchedule ORM row to the API response shape (or return defaults)."""
    if schedule is None:
        return ImportScheduleResponse()
    return ImportScheduleResponse(
        enabled=schedule.enabled,
        source_dir=schedule.source_dir,
        frequency=schedule.frequency,
        last_run_at=schedule.last_run_at,
        last_status=schedule.last_status,
        last_imported_count=schedule.last_imported_count,
        last_error=schedule.last_error,
    )


@app.get(
    "/import-schedule",
    response_model=ImportScheduleResponse,
    summary="Get scheduled folder import config + last-run status",
    description=(
        "Returns the current import schedule configuration and last-run status for the vault. "
        "Returns sane defaults (enabled=false, frequency='1h') if no row has been configured yet. "
        "Feature S (ADR-0020 §4.6)."
    ),
)
async def get_import_schedule() -> ImportScheduleResponse:
    """GET /import-schedule — current config + last-run status (ADR-0020 §4.6)."""
    schedule = await load_schedule(settings.vault_id)
    return _schedule_to_response(schedule)  # type: ignore[arg-type]


@app.put(
    "/import-schedule",
    response_model=ImportSchedulePutResponse,
    summary="Upsert import schedule configuration",
    description=(
        "Create or update the import schedule for the vault. "
        "Validates source_dir: if the directory does not exist inside the container, "
        "the row is still saved but dir_ok=false + dir_message is returned (save-then-warn). "
        "frequency must be one of '15m' | '1h' | '6h' | 'daily'. "
        "Config changes take effect on the next scheduler tick without a restart. "
        "Feature S (ADR-0020 §4.6)."
    ),
    responses={
        200: {"description": "Config saved (dir_ok may be false if mount is missing)"},
        422: {"description": "Invalid frequency value"},
    },
)
async def put_import_schedule(body: ImportSchedulePutBody) -> ImportSchedulePutResponse:
    """
    PUT /import-schedule — upsert schedule config with save-then-warn dir validation.

    If body.source_dir is provided, validate it exists & is readable inside the container.
    Persist regardless of dir_ok (operator may add the mount later; next tick picks it up).
    """
    # Build update kwargs
    update_kwargs: dict[str, object] = {}
    if body.enabled is not None:
        update_kwargs["enabled"] = body.enabled
    if body.source_dir is not None:
        update_kwargs["source_dir"] = body.source_dir
    if body.frequency is not None:
        update_kwargs["frequency"] = body.frequency
    update_kwargs["updated_at"] = datetime.now(UTC)

    await upsert_schedule(settings.vault_id, **update_kwargs)

    # Reload the freshly persisted row
    schedule = await load_schedule(settings.vault_id)

    # Dir validation (save-then-warn — ADR-0020 §4.6)
    dir_ok = True
    dir_message: str | None = None
    source_dir_val: str | None = getattr(schedule, "source_dir", None) if schedule else None
    if source_dir_val is not None:
        import os as _os

        if not _os.path.isdir(source_dir_val):
            dir_ok = False
            dir_message = (
                f"Directory '{source_dir_val}' is not visible inside the backend container. "
                "Add a mount (e.g. - ./import:/import:ro in docker-compose.yml) and set "
                "source_dir to the CONTAINER path — see DEPLOY.md."
            )

    base = _schedule_to_response(schedule)  # type: ignore[arg-type]
    return ImportSchedulePutResponse(
        enabled=base.enabled,
        source_dir=base.source_dir,
        frequency=base.frequency,
        last_run_at=base.last_run_at,
        last_status=base.last_status,
        last_imported_count=base.last_imported_count,
        last_error=base.last_error,
        dir_ok=dir_ok,
        dir_message=dir_message,
    )


@app.post(
    "/import-schedule/run-now",
    response_model=RunNowResponse,
    status_code=202,
    summary="Trigger one bounded import scan immediately",
    description=(
        "Trigger one bounded scan of source_dir immediately (same bounds as the scheduler: "
        "IMPORT_SCAN_MAX_FILES + IMPORT_SCAN_MAX_SECONDS, I7). "
        "The scan runs in the background; poll GET /import-schedule for the result. "
        "409 if a scan is already in-flight. 400 if disabled or source_dir unset/missing. "
        "Feature S (ADR-0020 §4.6)."
    ),
    responses={
        202: {"description": "Scan started in the background"},
        400: {"description": "Schedule is disabled, source_dir not set, or directory missing"},
        409: {"description": "A scan is already in-flight (I7 — no overlap)"},
    },
)
async def run_import_now() -> RunNowResponse:
    """
    POST /import-schedule/run-now — trigger one bounded scan immediately (ADR-0020 §4.6).

    Uses the module-level ImportScheduler singleton started in the lifespan.
    Falls back to creating a temporary scheduler if the lifespan singleton is absent
    (e.g. test environments that bypass lifespan).
    """
    global _import_scheduler

    scheduler = _import_scheduler
    if scheduler is None:
        # Graceful degradation: create an ephemeral scheduler (test / direct-startup scenario)
        scheduler = ImportScheduler()

    if scheduler.scan_in_flight:
        raise HTTPException(
            status_code=409,
            detail=(
                "A scan is already in-flight. "
                "Wait for it to finish or poll GET /import-schedule."
            ),
        )

    # Kick off the scan as a background task
    async def _run() -> None:
        try:
            await scheduler.run_now()
        except (ValueError, RuntimeError) as exc:
            logger.warning("run_import_now: scan failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.error("run_import_now: unhandled error in background scan: %s", exc)

    try:
        # Validate preconditions before starting the background task (so we get 400 synchronously)
        cfg = await load_schedule(settings.vault_id)
        if cfg is None or not getattr(cfg, "enabled", False):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Schedule is disabled or not configured. " "Enable it and set source_dir first."
                ),
            )
        source_dir = getattr(cfg, "source_dir", None)
        if not source_dir:
            raise HTTPException(
                status_code=400,
                detail="source_dir is not set. Configure a container-visible path first.",
            )
        import os as _os

        if not _os.path.isdir(str(source_dir)):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Directory '{source_dir}' is not accessible inside the container. "
                    "Add a mount (e.g. - ./import:/import:ro) and set "
                    "source_dir to the container path."
                ),
            )
    except HTTPException:
        raise

    asyncio.create_task(_run())
    return RunNowResponse(status="started")


# ── Chat: conversations CRUD + streaming turn (F6/F7, ADR-0019) ───────────────


@app.get(
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
    async with get_session() as session:
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
    return ConversationListResponse(
        items=[ConversationResponse.model_validate(c) for c in convs],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=201,
    summary="Create an empty chat conversation",
    description="Create a conversation {vault_id?, title?}. Also implicitly created by "
    "/chat/stream when conversation_id is null. F6 (ADR-0019 §2.5).",
)
async def create_conversation(body: ConversationCreate) -> ConversationResponse:
    effective_vault_id = body.vault_id or settings.vault_id
    async with get_session() as session:
        conv = Conversation(vault_id=effective_vault_id, title=body.title)
        session.add(conv)
        await session.flush()
        await session.refresh(conv)
        result = ConversationResponse.model_validate(conv)
    return result


@app.get(
    "/conversations/{conversation_id}/messages",
    response_model=ChatMessageListResponse,
    summary="Get ordered message history for a conversation",
    description="Messages ordered created_at ASC. content is RAW incl. literal <think>… "
    "(AC-F7-2). 404 if the conversation is unknown/soft-deleted. F6.",
    responses={404: {"description": "Conversation not found"}},
)
async def get_conversation_messages(conversation_id: uuid.UUID) -> ChatMessageListResponse:
    async with get_session() as session:
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


@app.delete(
    "/conversations/{conversation_id}",
    status_code=204,
    summary="Soft-delete a conversation",
    description="Sets deleted_at (ADR-0005 pattern). 404 if unknown/already deleted. F6.",
    responses={204: {"description": "Soft-deleted"}, 404: {"description": "Not found"}},
)
async def delete_conversation(conversation_id: uuid.UUID) -> None:
    from sqlalchemy import update as sa_update

    async with get_session() as session:
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


@app.post(
    "/chat/stream",
    summary="Stream a chat turn (NDJSON)",
    description=(
        "Bounded chat turn (F6/F7, I6/I7, ADR-0019 §2.2). Returns 200 with "
        "application/x-ndjson: one JSON event per line (token | think | done | error). "
        "Routes via resolve_provider_config('chat', vault_id) — never a hardcoded provider "
        "(I6). Bounded by token_budget + timeout (I7); total_cost_usd in the done event. "
        "404 if conversation_id is unknown; 503 if no chat provider resolves."
    ),
    responses={
        200: {"content": {"application/x-ndjson": {}}, "description": "NDJSON event stream"},
        404: {"description": "conversation_id provided but unknown"},
        422: {"description": "Body validation failure"},
        503: {"description": "No chat provider_config resolves (I6)"},
    },
)
async def chat_stream(body: ChatRequest) -> StreamingResponse:
    """
    POST /chat/stream — the NDJSON streaming chat turn (ADR-0019 §2.2).

    Setup failures that must map to an HTTP status (unknown conversation → 404, no provider →
    503) are raised by run_chat_stream BEFORE the first yield; we surface them here. Once the
    stream starts (HTTP 200), all later failures are terminal `error` NDJSON events.
    """
    domain_messages = [Message(role=m.role, content=m.content) for m in body.messages]

    agen = run_chat_stream(
        conversation_id=body.conversation_id,
        messages=domain_messages,
        vault_id=body.vault_id,
        context_window=body.context_window,
        regenerate=body.regenerate,
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


# ── GET /graph ─────────────────────────────────────────────────────────────────


class GraphNodeResponse(BaseModel):
    """
    One graph node in the GET /graph response (ADR-0014 §6, AC-F4-3, ADR-0016 §4).

    Required: id, title, type, x, y.
    Optional rendering hints (derived server-side): size, degree.
    """

    id: str
    title: str | None
    type: str | None
    x: float
    y: float
    size: float = Field(
        default=1.0,
        description="BASE + GROWTH·sqrt(structural_degree); BASE=1.0, GROWTH=2.5 (ADR-0016 §2)",
    )
    degree: int = Field(
        default=0,
        description=(
            "Structural degree: count of distinct incident structural edges "
            "(direct-link or shared-source); drives size (ADR-0016 §2/§4)"
        ),
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": "00000000-0000-0000-0000-000000000001",
                "title": "Alpha",
                "type": "entity",
                "x": 1.23,
                "y": -0.45,
                "size": 2.1,
                "degree": 3,
            }
        }
    }


class GraphEdgeResponse(BaseModel):
    """
    One graph edge in the GET /graph response (ADR-0014 §6, AC-F4-3, ADR-0016 §4).

    source/target are page-id strings (UUID). Undirected — emitted once per pair.
    kind: structural edge discriminator — "link" (wikilink exists) or "source"
          (shared-source provenance only). ADR-0016 §4.
    """

    source: str
    target: str
    weight: float
    kind: str = Field(
        default="link",
        description=(
            'Structural edge kind: "link" (direct wikilink) | '
            '"source" (shared provenance). ADR-0016 §4'
        ),
    )


class GraphResponse(BaseModel):
    """
    GET /graph response payload (ADR-0014 §6, AC-F4-3, AC-D4v3-1).

    cached: true on a HIT (no FA2 this request), false on a MISS (FA2 ran inline).
    Header X-Graph-Cache: hit|miss mirrors cached (ADR-0014 §5).
    """

    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]
    data_version: int
    cached: bool

    model_config = {
        "json_schema_extra": {
            "example": {
                "nodes": [
                    {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "title": "Alpha",
                        "type": "entity",
                        "x": 1.23,
                        "y": -0.45,
                        "size": 2.1,
                        "degree": 3,
                    }
                ],
                "edges": [
                    {
                        "source": "00000000-0000-0000-0000-000000000001",
                        "target": "00000000-0000-0000-0000-000000000002",
                        "weight": 11.0,
                        "kind": "link",
                    }
                ],
                "data_version": 7,
                "cached": True,
            }
        }
    }


@app.get(
    "/graph",
    response_model=GraphResponse,
    summary="Precomputed knowledge graph (nodes + edges with FA2 coordinates)",
    description=(
        "Returns the precomputed graph with FA2 layout coordinates (I2, F4, ADR-0014). "
        "HIT (X-Graph-Cache: hit): pure read from persisted coords + edges — no FA2. "
        "MISS (X-Graph-Cache: miss): one inline synchronous recompute, then return. "
        "Synchronous 200 — never 202 (AQ-v0.3-3). "
        "A second request at the same data_version is always a HIT (G2)."
    ),
    responses={
        200: {
            "description": "Graph payload with precomputed coords",
            "headers": {
                "X-Graph-Cache": {
                    "description": "hit|miss — mirrors the cached field (ADR-0014 §5)",
                    "schema": {"type": "string"},
                }
            },
        }
    },
)
async def get_graph() -> Response:
    """
    GET /graph — precomputed knowledge graph with FA2 layout coords (F4, I2, ADR-0014).

    I2 compliance:
      - HIT path: pure read, no FA2 (X-Graph-Cache: hit).
      - MISS path: one inline synchronous recompute (X-Graph-Cache: miss).
      - The background debounce (GraphCache) keeps the common case a HIT.
      - Coords are precomputed server-side via igraph (R9, I9) — never on the client.
    """
    global _graph_cache

    # Read the current data_version (lightweight SELECT)
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        state = row.scalar_one_or_none()
        current_version: int = state.data_version if state is not None else 0

    # Initialise cache lazily (e.g. in test environments that bypass lifespan)
    if _graph_cache is None:
        _graph_cache = GraphCache(
            engine=GraphEngine(),
            vault_id=settings.vault_id,
        )

    snapshot, cached = await _graph_cache.get_graph(current_version)

    # Build response payload (ADR-0014 §6)
    nodes: list[GraphNodeResponse] = [
        GraphNodeResponse(
            id=n.id,
            title=n.title,
            type=n.page_type,
            x=n.x,
            y=n.y,
            size=n.size,
            degree=n.degree,
        )
        for n in snapshot.nodes
    ]
    edges: list[GraphEdgeResponse] = [
        GraphEdgeResponse(source=e.source, target=e.target, weight=e.weight, kind=e.kind)
        for e in snapshot.edges
    ]
    payload = GraphResponse(
        nodes=nodes,
        edges=edges,
        data_version=current_version,
        cached=cached,
    )

    cache_header = "hit" if cached else "miss"
    return Response(
        content=payload.model_dump_json(),
        media_type="application/json",
        headers={"X-Graph-Cache": cache_header},
    )


# ── Startup helpers ────────────────────────────────────────────────────────────


async def _seed_vault_state() -> None:
    """
    Insert vault_state row for VAULT_ID with data_version=0 if absent (ADR-0005, AQ-4).

    Idempotent — safe to call on every restart.
    """
    async with get_session() as session:
        row = await session.execute(
            select(VaultState).where(VaultState.vault_id == settings.vault_id)
        )
        if row.scalar_one_or_none() is None:
            state = VaultState(
                vault_id=settings.vault_id,
                data_version=0,
                updated_at=datetime.now(UTC),
            )
            session.add(state)
            logger.info("vault_state seeded for vault_id=%r", settings.vault_id)
        else:
            logger.info("vault_state already exists for vault_id=%r — no change", settings.vault_id)


async def _validate_embedding_and_collection() -> None:
    """
    Validate EMBEDDING_DIM against the live bge-m3 service and ensure the
    synapse_pages Qdrant collection (ADR-0004, AC-QD-1).

    Fails fast on mismatch (ADR-0004 — the running service is the authority).
    Allows a FakeEmbeddingClient to be injected in CI without TrueNAS (GAP-4).
    """
    client = get_embedding_client()
    try:
        live_dim = await client.probe_dimension()
    except EmbeddingError as exc:
        logger.error("Cannot reach embedding service: %s", exc)
        raise RuntimeError(
            f"Embedding service at {settings.embedding_url} is unreachable at startup. "
            "Set EMBEDDING_URL to a reachable endpoint or inject a FakeEmbeddingClient "
            "for CI (GAP-4)."
        ) from exc

    if live_dim != settings.embedding_dim:
        raise RuntimeError(
            f"EMBEDDING_DIM={settings.embedding_dim} but the live bge-m3 service "
            f"returned vectors of length {live_dim}. Update EMBEDDING_DIM to match "
            "the running service (ADR-0004)."
        )

    logger.info("Embedding dimension validated: %d", live_dim)
    await ensure_collection(dim=live_dim)


# ── Model serialisation helper ─────────────────────────────────────────────────


def _page_to_response(page: Page) -> PageResponse:
    return PageResponse(
        id=page.id,
        vault_id=page.vault_id,
        file_path=page.file_path,
        title=page.title,
        page_type=page.page_type,
        sources=page.sources,
        content_hash=page.content_hash,
        qdrant_point_id=page.qdrant_point_id,
        deleted_at=page.deleted_at,
        created_at=page.created_at,
        updated_at=page.updated_at,
    )
