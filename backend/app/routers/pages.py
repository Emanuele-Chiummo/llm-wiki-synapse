"""
Per-domain APIRouter: /pages/* endpoints + cascade-delete.

Covers:
  GET  /pages                         — paginated page list
  POST /pages                         — create a page
  GET  /pages/{id}                    — single page
  GET  /pages/{id}/related            — top-N related pages
  GET  /pages/{id}/content            — raw markdown
  PUT  /pages/{id}/content            — update content
  PATCH /pages/{id}/position          — persist manual drag position
  POST /pages/{id}/cascade-delete/preview — dry-run plan
  DELETE /pages/{id}                  — cascade delete
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy import text as sa_text

from app import runtime_state
from app.config import settings
from app.config_overrides import effective_domain_vocabulary
from app.models import Page

logger = logging.getLogger(__name__)

router = APIRouter()


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
    # Additive fields (backward-compatible — nullable, absent on old responses)
    domain: str | None = Field(
        None,
        description=(
            "Dominant vocabulary domain derived server-side from 'domain/<name>' tags. "
            "null when the page is untagged or no domain vocabulary is configured. "
            "Reuses the same derivation logic as GET /stats/sections (stats.py)."
        ),
    )
    community: int | None = Field(
        None,
        description=(
            "Louvain community id persisted by GraphEngine.recompute(). "
            "null until the first graph recompute after migration 0020 (G-P0-2, I2)."
        ),
    )

    model_config = {"populate_by_name": True, "from_attributes": True}


class PageListResponse(BaseModel):
    items: list[PageResponse]
    total: int
    limit: int
    offset: int


class PageContentResponse(BaseModel):
    """
    Response for GET /pages/{id}/content (F1-content-read).

    Additive extension (backward-compatible): page_type (serialised as "type"), sources, and
    tags are included so the reader can render a type badge, sources list, and navigation
    tags without a second call to GET /pages/{id}.  All three are nullable (NULL when absent
    from YAML frontmatter).  tags is the K6 navigation list (nashsu/llm_wiki parity), mirroring
    the sources column.
    """

    id: uuid.UUID
    title: str | None
    file_path: str
    content: str
    content_hash: str
    updated_at: datetime
    # Frontmatter fields (additive — backward-compatible)
    page_type: str | None = Field(
        None, serialization_alias="type", description="Frontmatter 'type'; NULL if absent (K6)"
    )
    sources: list[str] | None = Field(
        None, description="Frontmatter 'sources[]'; NULL if absent (K6)"
    )
    tags: list[str] | None = Field(
        None, description="Frontmatter 'tags[]' navigation tags; NULL if absent (K6)"
    )

    model_config = {"populate_by_name": True, "from_attributes": True}


class PageContentPutRequest(BaseModel):
    """Request body for PUT /pages/{id}/content (F1-content-write, ADR-0035)."""

    content: str = Field(..., min_length=1, description="Full UTF-8 markdown content to write")
    expected_hash: str | None = Field(
        default=None,
        description=(
            "Optimistic concurrency guard — sha256 hex of the content the client last read. "
            "When provided and it does NOT match the current on-disk hash, 409 is returned "
            "so the editor can warn about a stale edit."
        ),
    )


# Maximum body size for PUT /pages/{id}/content (ADR-0035). 4 MB covers any realistic
# markdown page; larger bodies are rejected with 413 before any disk write.
_MAX_PAGE_CONTENT_BYTES = 4 * 1024 * 1024  # 4 MB


class PageContentPutResponse(BaseModel):
    """Response for PUT /pages/{id}/content (F1-content-write)."""

    id: uuid.UUID
    content_hash: str
    updated_at: datetime


# ── Domain derivation helper ───────────────────────────────────────────────────
# Same logic as GET /stats/sections in app/stats.py — reuses effective_domain_vocabulary()
# from app.config_overrides (ADR-0054 §2.1).  A page's dominant domain is the first tag
# that starts with "domain/" whose suffix is present in the controlled vocabulary.
# Returns None when untagged, when the vocabulary is empty, or when the page has no
# matching domain/* tag.

_DOMAIN_PREFIX = "domain/"


def _derive_domain(tags: list[str] | None, vocab_set: frozenset[str]) -> str | None:
    """Return the first matching vocabulary domain from a page's tags, or None."""
    if not tags or not vocab_set:
        return None
    for tag in tags:
        if tag.startswith(_DOMAIN_PREFIX):
            candidate = tag[len(_DOMAIN_PREFIX) :]
            if candidate in vocab_set:
                return candidate
    return None


# ── Model serialisation helper ─────────────────────────────────────────────────


def _page_to_response(
    page: Page,
    vocab_set: frozenset[str] | None = None,
) -> PageResponse:
    """Serialise a Page ORM row into a PageResponse.

    vocab_set: the current domain vocabulary as a frozenset (pre-computed per
    request so effective_domain_vocabulary() is called ONCE, not per-page).
    Pass None (or omit) to skip domain derivation — e.g. for single-page endpoints
    where the caller does not need domain context.
    """
    domain = _derive_domain(page.tags, vocab_set if vocab_set is not None else frozenset())
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
        domain=domain,
        community=page.community,
    )


# ── GET /pages ─────────────────────────────────────────────────────────────────


@router.get(
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
    page_type: str | None = Query(
        default=None,
        alias="type",
        description="Optional frontmatter type filter (e.g. 'query'); server-side, avoids "
        "over-fetching + client-side filtering (FE-PERF-2).",
    ),
) -> PageListResponse:
    async with runtime_state.get_session() as session:
        filters = [
            Page.vault_id == settings.vault_id,
            Page.deleted_at.is_(None),
        ]
        if page_type is not None:
            filters.append(Page.page_type == page_type)

        total_row = await session.execute(select(func.count()).select_from(Page).where(*filters))
        total: int = total_row.scalar_one()

        rows = await session.execute(
            select(Page)
            .where(*filters)
            .order_by(Page.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        pages = rows.scalars().all()

    # Derive domain vocabulary ONCE per request (O(1) from ADR-0053 cache).
    # Passed to _page_to_response so effective_domain_vocabulary() is not called per-page.
    vocab_set = frozenset(effective_domain_vocabulary())

    return PageListResponse(
        items=[_page_to_response(p, vocab_set) for p in pages],
        total=total,
        limit=limit,
        offset=offset,
    )


# ── POST /pages ────────────────────────────────────────────────────────────────
# R7-2 backend: create a new wiki page from the UI (AC-R7-2-2, [F1]).
# Reuses write_wiki_page() — the shared ingest seam (I1).

_ALLOWED_PAGE_DIRS: frozenset[str] = frozenset(
    {"entities", "concepts", "sources", "queries", "synthesis", "comparisons"}
)


class PageCreateRequest(BaseModel):
    """
    Request body for POST /pages (R7-2, AC-R7-2-2, [F1]).

    Creates a new wiki page with minimal frontmatter via the shared write_wiki_page seam (I1).
    A 409 is returned when a live page with the same (vault_id, file_path) already exists.
    """

    title: str = Field(..., min_length=1, max_length=500, description="Page title (required)")
    page_type: str = Field(
        ...,
        description="Wiki page type; one of entity|concept|source|synthesis|comparison|query",
    )
    dir: str | None = Field(
        default=None,
        description=(
            "Target subdirectory under wiki/ (optional; derived from page_type when omitted). "
            f"Allowed values: {sorted(_ALLOWED_PAGE_DIRS)}"
        ),
    )
    content: str = Field(default="", description="Initial markdown body (empty is valid)")


class PageCreateResponse(BaseModel):
    """Response for POST /pages (201)."""

    id: uuid.UUID
    file_path: str
    title: str
    page_type: str


@router.post(
    "/pages",
    response_model=PageCreateResponse,
    status_code=201,
    summary="Create a new wiki page from the UI",
    description=(
        "Create a new wiki page via the shared write_wiki_page seam (I1, R7-2). "
        "Derives slug and subdirectory from title + page_type. "
        "409 if a live page with the same path already exists. "
        "Bumps data_version on success. [F1, AC-R7-2-2]"
    ),
    responses={
        201: {"description": "Page created"},
        409: {"description": "A live page with the same path already exists"},
        422: {"description": "Validation error (invalid type or dir)"},
    },
)
async def create_page(body: PageCreateRequest) -> PageCreateResponse:
    """
    POST /pages — R7-2 new-page-from-UI backend [F1].

    Validates page_type against the PageType enum, optionally validates dir against
    the allowed wiki/ subdirectories, then delegates to write_wiki_page() (I1 — the
    single write seam shared by orchestrator, MCP, and save-to-wiki).

    A minimal WikiPage is constructed with:
      - frontmatter.sources = ["manual"] (no raw source; manually authored page)
      - frontmatter.lang = "en" (default; UI can be extended with a lang field later)
      - content = body.content (empty string is valid — stub page)

    409 when a live (non-deleted) page with the same (vault_id, file_path) already exists.
    """
    from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
    from app.ingest.writer import write_wiki_page

    # Validate page_type
    try:
        pt = PageType(body.page_type)
    except ValueError as exc:
        valid = sorted(pv.value for pv in PageType)
        raise HTTPException(
            status_code=422,
            detail=f"Invalid page_type {body.page_type!r}; must be one of {valid}",
        ) from exc

    # Validate dir if provided
    if body.dir is not None and body.dir not in _ALLOWED_PAGE_DIRS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid dir {body.dir!r}; must be one of {sorted(_ALLOWED_PAGE_DIRS)} "
                f"(or omit to derive from page_type)"
            ),
        )

    # Derive expected file_path to check for 409 before writing
    import re as _re_page

    from app.ingest.schemas import type_subdir

    _SLUG_RE_PAGE = _re_page.compile(r"[^a-z0-9]+")
    slug = _SLUG_RE_PAGE.sub("-", body.title.strip().lower()).strip("-") or "untitled"
    subdir = body.dir if body.dir is not None else type_subdir(pt)
    rel_path = f"wiki/{subdir}/{slug}.md"

    # 409 pre-check: live page with the same path already exists
    async with runtime_state.get_session() as session:
        existing = await session.execute(
            select(Page).where(
                Page.vault_id == settings.vault_id,
                Page.file_path == rel_path,
                Page.deleted_at.is_(None),
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=409,
                detail=f"A live page already exists at {rel_path}",
            )

    # Build minimal WikiPage — manually authored, so source is "manual"
    fm = WikiFrontmatter(
        type=pt,
        title=body.title,
        sources=["manual"],
        lang="en",
    )
    wiki_page = WikiPage(
        title=body.title,
        type=pt,
        content=body.content if body.content.strip() else "<!-- New page -->",
        frontmatter=fm,
    )

    try:
        page_row = await write_wiki_page(None, wiki_page, "")
    except Exception as exc:  # noqa: BLE001
        logger.error("POST /pages: write_wiki_page failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Page write failed: {exc}") from exc

    return PageCreateResponse(
        id=page_row.id,
        file_path=page_row.file_path,
        title=page_row.title or body.title,
        page_type=page_row.page_type or body.page_type,
    )


# ── GET /pages/by-slug/{slug} ─────────────────────────────────────────────────
# v1.3.3: chat citations carry a derived slug (rag.retrieval.slugify(title) —
# NOT a DB column), while every /pages/{page_id} route demands a UUID. The UI
# used to feed the slug straight into the content endpoint → 422. This route is
# the single resolution point, using the SAME slugify as retrieval.
# NOTE: declared BEFORE /pages/{page_id} so the literal segment wins routing.


@router.get(
    "/pages/by-slug/{slug}",
    response_model=PageResponse,
    summary="Resolve a derived citation slug to a page",
    description=(
        "Resolves a slug as produced by the retrieval citations "
        "(slugify(title), not stored in the DB) to the live page with that title. "
        "404 if no live page slugifies to it. (v1.3.3, F5/F6 citation click-through)"
    ),
)
async def get_page_by_slug(slug: str) -> PageResponse:
    from app.rag.retrieval import slugify

    async with runtime_state.get_session() as session:
        rows = await session.execute(
            select(Page).where(
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
        )
        pages = rows.scalars().all()

    wanted = slug.strip().lower()
    for page in pages:
        if page.title and slugify(page.title) == wanted:
            return _page_to_response(page)

    raise HTTPException(status_code=404, detail=f"No live page for slug '{slug}'")


# ── GET /pages/{id} ────────────────────────────────────────────────────────────


@router.get(
    "/pages/{page_id}",
    response_model=PageResponse,
    summary="Get a single page by UUID",
    description=(
        "Returns full page metadata; 404 if unknown or deleted; 422 on invalid UUID. "
        "(AC-REST-3, AC-REST-6)"
    ),
)
async def get_page(page_id: uuid.UUID) -> PageResponse:
    async with runtime_state.get_session() as session:
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


# ── GET /pages/{id}/related ────────────────────────────────────────────────────

_RELATED_MAX_LIMIT = 50  # hard cap: never return more than this many related pages


class RelatedPageItem(BaseModel):
    """
    One entry in the GET /pages/{id}/related response.

    score is the stored 4-signal edge weight (ADR-0012):
      3·direct_link_count + 4·shared_source_count + 1.5·adamic_adar + 1·same_type
    Reuses the persisted edges table — no recompute (I1/I2).
    """

    page_id: uuid.UUID = Field(..., description="UUID of the related page")
    title: str | None = Field(None, description="YAML frontmatter title; NULL if absent")
    type: str | None = Field(None, description="YAML frontmatter type; NULL if absent")
    score: float = Field(..., description="4-signal edge weight (higher = more related)")


class RelatedPagesResponse(BaseModel):
    """Response for GET /pages/{id}/related."""

    items: list[RelatedPageItem]
    total: int = Field(..., description="Total related pages found (before limit)")

    model_config = {
        "json_schema_extra": {
            "example": {
                "items": [
                    {
                        "page_id": "00000000-0000-0000-0000-000000000002",
                        "title": "Beta Concept",
                        "type": "concept",
                        "score": 11.5,
                    }
                ],
                "total": 1,
            }
        }
    }


@router.get(
    "/pages/{page_id}/related",
    response_model=RelatedPagesResponse,
    summary="Top-N related pages ranked by 4-signal edge weight",
    description=(
        "Returns the top *limit* pages most related to *page_id*, ranked by the stored "
        "4-signal edge weight (direct ×3, source-overlap ×4, Adamic-Adar ×1.5, "
        "type-affinity ×1 — ADR-0012). "
        "Reads the persisted *edges* table directly: no graph recompute, no FA2 (I1/I2). "
        "Empty list (200) if the page has no edges yet. "
        "404 if the page is unknown or soft-deleted. "
        "limit is capped at 50; default 10."
    ),
    responses={
        200: {"description": "Related pages list (may be empty if no edges yet)"},
        404: {"description": "Page not found"},
        422: {"description": "Invalid page_id UUID or limit out of range"},
    },
)
async def get_related_pages(
    page_id: uuid.UUID,
    limit: int = Query(default=10, ge=1, le=_RELATED_MAX_LIMIT, description="Max results (1–50)"),
) -> RelatedPagesResponse:
    """
    GET /pages/{page_id}/related — top-N related pages from the persisted edges table.

    I1/I2 compliance: reads edges + pages tables only. Never triggers a graph recompute.
    Edges are stored canonically (smaller UUID first) but are undirected, so we match
    both endpoints. Raw SQL used for the dual-endpoint join to guarantee identical
    behaviour on SQLite (tests) and Postgres (production).
    CAST(… AS TEXT) used for UUID columns for cross-DB portability (memory note:
    raw-sql-sqlite-tests-vs-postgres-runtime).
    """
    # On Postgres, UUID columns are native (UUID type) and comparisons are type-safe;
    # the text cast still works correctly.  On SQLite (tests), SQLAlchemy stores UUID
    # columns as 32-char hex strings (no hyphens), while str(uuid.UUID(...)) produces
    # the hyphenated form.  REPLACE(..., '-', '') on both sides normalises the comparison
    # to format-agnostic hex matching — portable across both engines (memory note:
    # raw-sql-sqlite-tests-vs-postgres-runtime).
    pid_str = str(page_id)  # standard hyphenated form, e.g. "abc-..."; stripped in SQL
    vault = settings.vault_id

    async with runtime_state.get_session() as session:
        # 1. Verify the page exists and is live (ORM — clean, portable)
        page_row = await session.execute(
            select(Page).where(
                Page.id == page_id,
                Page.vault_id == vault,
                Page.deleted_at.is_(None),
            )
        )
        page = page_row.scalar_one_or_none()
        if page is None:
            raise HTTPException(status_code=404, detail=f"Page {page_id} not found")

        # 2. Query edges — undirected, so match either endpoint.
        #    UNION ALL of two directional selects is the simplest portable pattern:
        #    one leg where we are the source, one where we are the target.
        #    REPLACE(CAST(col AS TEXT), '-', '') strips hyphens from both UUID columns
        #    and the parameter so the comparison is format-agnostic on SQLite (test)
        #    and Postgres (production).  The neighbour_id is returned as-cast (no
        #    strip needed — uuid.UUID() handles both formats).
        neighbours_sql = sa_text("""
            SELECT e.weight,
                   CAST(p.id AS TEXT)  AS neighbour_id,
                   p.title             AS neighbour_title,
                   p.type              AS neighbour_type
            FROM edges e
            JOIN pages p
              ON REPLACE(CAST(e.target_page_id AS TEXT), '-', '')
               = REPLACE(CAST(p.id            AS TEXT), '-', '')
             AND p.deleted_at IS NULL
            WHERE e.vault_id = :vault_id
              AND REPLACE(CAST(e.source_page_id AS TEXT), '-', '')
                = REPLACE(:page_id, '-', '')

            UNION ALL

            SELECT e.weight,
                   CAST(p.id AS TEXT)  AS neighbour_id,
                   p.title             AS neighbour_title,
                   p.type              AS neighbour_type
            FROM edges e
            JOIN pages p
              ON REPLACE(CAST(e.source_page_id AS TEXT), '-', '')
               = REPLACE(CAST(p.id            AS TEXT), '-', '')
             AND p.deleted_at IS NULL
            WHERE e.vault_id = :vault_id
              AND REPLACE(CAST(e.target_page_id AS TEXT), '-', '')
                = REPLACE(:page_id, '-', '')

            ORDER BY weight DESC
            LIMIT :lim
            """).bindparams(vault_id=vault, page_id=pid_str, lim=limit)

        result = await session.execute(neighbours_sql)
        rows = result.all()

        # 3. Count total related (before limit) — same UNION, wrapped in COUNT
        count_sql = sa_text("""
            SELECT COUNT(*) FROM (
                SELECT 1
                FROM edges e
                JOIN pages p
                  ON REPLACE(CAST(e.target_page_id AS TEXT), '-', '')
                   = REPLACE(CAST(p.id            AS TEXT), '-', '')
                 AND p.deleted_at IS NULL
                WHERE e.vault_id = :vault_id
                  AND REPLACE(CAST(e.source_page_id AS TEXT), '-', '')
                    = REPLACE(:page_id, '-', '')

                UNION ALL

                SELECT 1
                FROM edges e
                JOIN pages p
                  ON REPLACE(CAST(e.source_page_id AS TEXT), '-', '')
                   = REPLACE(CAST(p.id            AS TEXT), '-', '')
                 AND p.deleted_at IS NULL
                WHERE e.vault_id = :vault_id
                  AND REPLACE(CAST(e.target_page_id AS TEXT), '-', '')
                    = REPLACE(:page_id, '-', '')
            ) AS _related
            """).bindparams(vault_id=vault, page_id=pid_str)

        total_result = await session.execute(count_sql)
        total: int = total_result.scalar_one()

    items = [
        RelatedPageItem(
            page_id=uuid.UUID(row.neighbour_id),
            title=row.neighbour_title,
            type=row.neighbour_type,
            score=row.weight,
        )
        for row in rows
    ]
    return RelatedPagesResponse(items=items, total=total)


# ── GET /pages/{id}/content ────────────────────────────────────────────────────


def _resolve_page_path(file_path: str) -> Path:
    """
    Resolve a page's file_path (relative to vault_root) to an absolute Path.

    Raises HTTPException 400 if the resolved path escapes the vault root (path
    traversal guard). The check uses Path.resolve() so symlinks and ``..`` components
    cannot be used to escape. Used by GET /pages/{id}/content.
    """
    vault_root = settings.vault_root.resolve()
    candidate = (vault_root / file_path).resolve()
    try:
        candidate.relative_to(vault_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Path {file_path!r} resolves outside the vault root — " "path traversal rejected."
            ),
        ) from exc
    return candidate


def _resolve_wiki_page_path(file_path: str) -> Path:
    """
    Resolve a page's file_path for editing (PUT /pages/{id}/content, ADR-0035).

    Two-level guard (ADR-0035):
      1. Traversal: resolved path must stay inside vault_root → 400.
      2. Wiki-only: PUT only touches vault/wiki/ pages (never raw/sources/) → 403.
         Attempting to overwrite a sources file via this endpoint is rejected to prevent
         inadvertent replacement of immutable raw inputs (K1 vault layer separation, I5).

    Returns the absolute resolved Path on success.
    """
    abs_path = _resolve_page_path(file_path)  # raises 400 on traversal
    wiki_root = settings.vault_root.resolve() / "wiki"
    try:
        abs_path.relative_to(wiki_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Path {file_path!r} is not inside vault/wiki/. "
                "PUT /pages/{{id}}/content only edits wiki pages, "
                "not raw/sources/ files (K1 layer separation)."
            ),
        ) from exc
    return abs_path


@router.get(
    "/pages/{page_id}/content",
    response_model=PageContentResponse,
    summary="Read raw markdown content of a wiki page",
    description=(
        "Returns the raw UTF-8 markdown (including YAML frontmatter) for the page "
        "identified by *page_id*. The content is read directly from the vault filesystem; "
        "no caching layer is applied so callers always get the latest committed bytes. "
        "404 if the page row is unknown or soft-deleted; 410 if the row exists but the "
        "file is absent on disk (watcher has not yet re-indexed a deletion in flight); "
        "400 on path-traversal attempt. (F1-content-read, I1, I5)"
    ),
    responses={
        200: {"description": "Page content returned"},
        400: {"description": "Path traversal rejected"},
        404: {"description": "Page not found in index"},
        410: {"description": "Page row exists but file missing on disk"},
    },
)
async def get_page_content(page_id: uuid.UUID) -> PageContentResponse:
    async with runtime_state.get_session() as session:
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

    abs_path = _resolve_page_path(page.file_path)

    if not abs_path.exists():
        raise HTTPException(
            status_code=410,
            detail=(
                f"Page {page_id} row exists (file_path={page.file_path!r}) "
                "but the file is not present on disk. "
                "The watcher will remove the row when the deletion event is processed."
            ),
        )

    raw_bytes = await asyncio.get_event_loop().run_in_executor(None, abs_path.read_bytes)
    content = raw_bytes.decode("utf-8", errors="replace")

    # content_hash is the optimistic-lock token (ADR-0035): it MUST hash the exact bytes returned
    # here, so PUT's on-disk comparison succeeds iff the file is unchanged between GET and PUT.
    # We recompute from the file bytes rather than returning page.content_hash, which can lag the
    # file (the DB row reflects the last index, not necessarily the current disk state).
    content_hash = hashlib.sha256(raw_bytes).hexdigest()

    return PageContentResponse(
        id=page.id,
        title=page.title,
        file_path=page.file_path,
        content=content,
        content_hash=content_hash,
        updated_at=page.updated_at,
        page_type=page.page_type,
        sources=page.sources,
        tags=page.tags,
    )


# ── PUT /pages/{id}/content ────────────────────────────────────────────────────


@router.put(
    "/pages/{page_id}/content",
    response_model=PageContentPutResponse,
    summary="Overwrite the markdown content of a wiki page",
    description=(
        "Atomically overwrites the markdown file for *page_id* with the supplied content. "
        "Only edits pages inside vault/wiki/ — raw/sources/ files are rejected with 403 "
        "(K1 vault layer separation). "
        "Write is done via a temp-file + os.replace so a crash mid-write does not corrupt "
        "the vault. A trailing newline is enforced (Obsidian / git convention, I5). "
        "\n\n"
        "Validation (ADR-0035): "
        "(a) body > 4 MB → 413; "
        "(b) YAML frontmatter must parse cleanly → 422 (protects Obsidian vault validity, I5). "
        "\n\n"
        "Optimistic concurrency: when *expected_hash* is supplied and does not match the "
        "current sha256 of the on-disk file, 409 Conflict is returned — the caller should "
        "reload the page and present the diff to the user before retrying. "
        "\n\n"
        "Re-indexing (I1/ADR-0035): the watcher observes vault/raw/sources/ only, NOT "
        "vault/wiki/. Therefore this endpoint calls reindex_wiki_page_body() INLINE after "
        "writing so the Postgres row (content_hash, updated_at, wikilinks) and Qdrant point "
        "are updated synchronously before the response is returned. reindex_wiki_page_body() "
        "is the purpose-built single-page re-index primitive (ADR-0036 §2.1): it updates "
        "content_hash, re-embeds the body into Qdrant, re-derives K5 wikilinks, and bumps "
        "data_version ONCE so the debounced GraphCache recompute fires (I2). It does NOT "
        "invoke the LLM analyze→generate pipeline — preserving the user's exact edit (I5). "
        "This is a single-page update, never a full rescan (I1). "
        "(F1-content-write, I1, I5, ADR-0035)"
    ),
    responses={
        200: {"description": "Content written; new hash returned"},
        400: {"description": "Path traversal rejected"},
        403: {"description": "Path is not inside vault/wiki/ (K1 layer separation)"},
        404: {"description": "Page not found"},
        409: {"description": "Stale expected_hash — content was modified since last read"},
        410: {"description": "Page row exists but file missing (cannot overwrite)"},
        413: {"description": "Content body exceeds _MAX_PAGE_CONTENT_BYTES (4 MB)"},
        422: {"description": "YAML frontmatter is invalid — Obsidian vault would break (I5)"},
    },
)
async def put_page_content(
    page_id: uuid.UUID,
    body: PageContentPutRequest,
) -> PageContentPutResponse:
    import tempfile

    # ── Body size guard (ADR-0035, I7) ───────────────────────────────────────
    if len(body.content.encode("utf-8")) > _MAX_PAGE_CONTENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Content body exceeds the maximum allowed size of "
                f"{_MAX_PAGE_CONTENT_BYTES // (1024 * 1024)} MB."
            ),
        )

    # ── YAML frontmatter validation (ADR-0035, I5) ────────────────────────────
    # Reject content that python-frontmatter cannot parse to protect Obsidian
    # vault validity (I5). An absent frontmatter block is NOT an error (K6 — tolerant).
    try:
        import frontmatter as _fm

        _fm.loads(body.content)
    except Exception as _fm_exc:
        raise HTTPException(
            status_code=422,
            detail=(
                f"YAML frontmatter parse error: {_fm_exc}. "
                "Fix the frontmatter before writing (Obsidian vault validity, I5)."
            ),
        ) from _fm_exc

    # ── Enforce trailing newline (Obsidian / git convention, I5) ─────────────
    # Compute new content before opening the session so _write() can be defined
    # as a closure inside the session scope (B10 fix requires abs_path to be known).
    new_content = body.content if body.content.endswith("\n") else body.content + "\n"
    new_bytes = new_content.encode("utf-8")
    new_hash = hashlib.sha256(new_bytes).hexdigest()

    # ── SELECT FOR UPDATE + hash check + atomic write (B10 fix) ──────────────
    # Holding the page-row lock for the duration makes the hash check atomic with the
    # disk write: a concurrent PUT sees a different lock state and serialises behind us,
    # so it cannot both pass the expected_hash check AND overwrite our write (the previous
    # non-atomic check-then-write allowed silent last-writer-wins).  On SQLite (tests)
    # with_for_update() is a no-op (SQLite silently ignores FOR UPDATE), so tests remain
    # unaffected.  The lock is released when the session commits at the end of the block.
    async with runtime_state.get_session() as session:
        row = await session.execute(
            select(Page)
            .where(
                Page.id == page_id,
                Page.vault_id == settings.vault_id,
                Page.deleted_at.is_(None),
            )
            .with_for_update()  # serialise concurrent PUTs to the same page (B10 fix)
        )
        page = row.scalar_one_or_none()

        if page is None:
            raise HTTPException(status_code=404, detail=f"Page {page_id} not found")

        # ── Path safety + wiki-only guard (ADR-0035) ──────────────────────────
        abs_path = _resolve_wiki_page_path(page.file_path)

        if not abs_path.exists():
            raise HTTPException(
                status_code=410,
                detail=(
                    f"Page {page_id} row exists (file_path={page.file_path!r}) "
                    "but the file is not present on disk."
                ),
            )

        # ── Optimistic concurrency check — under the row lock (B10 fix) ──────
        if body.expected_hash is not None:
            on_disk_bytes = await asyncio.get_event_loop().run_in_executor(
                None, abs_path.read_bytes
            )
            on_disk_hash = hashlib.sha256(on_disk_bytes).hexdigest()
            if on_disk_hash != body.expected_hash:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Content hash mismatch: expected {body.expected_hash!r} but the "
                        f"current on-disk hash is {on_disk_hash!r}. "
                        "The page was modified since you last read it. "
                        "Reload the page before retrying."
                    ),
                )

        # ── Atomic write: tmp file in same dir + os.replace — under the row lock ──
        def _write() -> None:
            tmp_fd, tmp_name = tempfile.mkstemp(
                dir=str(abs_path.parent),
                suffix=".content_tmp",
            )
            try:
                import os

                os.write(tmp_fd, new_bytes)
                os.close(tmp_fd)
                Path(tmp_name).replace(abs_path)
            except Exception:  # noqa: BLE001
                try:
                    os.close(tmp_fd)
                except Exception:  # noqa: BLE001, S110
                    pass
                try:
                    Path(tmp_name).unlink(missing_ok=True)
                except Exception:  # noqa: BLE001, S110
                    pass
                raise

        await asyncio.get_event_loop().run_in_executor(None, _write)
    # Session commits here → FOR UPDATE lock released; page (expire_on_commit=False) is usable

    # ── Inline incremental re-index (I1, ADR-0035) ───────────────────────────
    # The watcher observes vault/raw/sources/ ONLY — not vault/wiki/. We use the
    # purpose-built reindex_wiki_page_body() primitive here (ADR-0035 / ADR-0036 §2.1):
    #   - atomic-write the new bytes (already done above via _write)
    #   - refreshes content_hash in Postgres (preserve existing title/type/sources — I5)
    #   - re-embeds the body into Qdrant (bge-m3) — skipped when embeddings disabled
    #   - re-derives K5 wikilinks from the new body (→ F4 direct-link ×3 edges)
    #   - bumps data_version ONCE → GraphCache debounce fires → FA2 recomputes (I2)
    # This satisfies I1 (single-page incremental update) and I2 (data_version bump,
    # no inline FA2). Do NOT use ingest_file() here: ingest_file() calls
    # _resolve_ingest_provider_config() and, when a provider is configured, invokes
    # run_ingest_pipeline() (analyze→generate loop) on the wiki content — which would
    # regenerate and overwrite the user's manual edit (data-loss bug, ADR-0035 gap).
    # reindex_wiki_page_body() skips the provider entirely (it is a pure re-index
    # primitive, not a content-generation primitive). Do NOT add a watcher for wiki/
    # (rejected in ADR-0026 §5).
    # Extract the body (sans frontmatter) for embedding and wikilink parsing.
    # _fm.loads() already ran above for validation; re-run cheaply for body extraction.
    import frontmatter as _fm_body  # noqa: PLC0415

    from app.ingest.orchestrator import reindex_wiki_page_body  # noqa: PLC0415

    _doc = _fm_body.loads(new_content)
    body_for_embedding = _doc.content  # the markdown body without the YAML block

    await reindex_wiki_page_body(
        page=page,
        new_file_text=new_content,
        body_for_embedding=body_for_embedding,
        bump=True,
    )

    # ── Return updated_at from the freshly committed row ─────────────────────
    async with runtime_state.get_session() as session:
        row2 = await session.execute(select(Page).where(Page.id == page_id))
        updated_page = row2.scalar_one_or_none()

    updated_at = updated_page.updated_at if updated_page is not None else datetime.now(UTC)

    return PageContentPutResponse(
        id=page_id,
        content_hash=new_hash,
        updated_at=updated_at,
    )


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


@router.patch(
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

    async with runtime_state.get_session() as session:
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
    _gc = runtime_state.graph_cache()
    if _gc is not None:
        found = _gc.patch_node_position(node_id_str, body.x, body.y)
        logger.debug(
            "patch_node_position: cache patch %s for node_id=%s",
            "succeeded" if found else "no-op (no snapshot yet)",
            node_id_str,
        )

    return PatchPositionResponse(id=node_id_str, x=body.x, y=body.y, pinned=True)


# ── F13 Cascade Delete REST (ADR-0026, AC-F13-5/7) ───────────────────────────


class CascadePreviewResponse(BaseModel):
    """
    POST /pages/{id}/cascade-delete/preview response (ADR-0026 §6.1, DRY-RUN).

    Read-only: mutates nothing — no soft-delete, no Qdrant delete, no file write,
    no data_version bump.  Returns the full CascadePlan as JSON.
    """

    target_page_id: uuid.UUID
    target_title: str | None = None
    target_file_path: str
    will_delete: list[uuid.UUID]
    will_preserve_with_pruned_source: list[uuid.UUID]
    wikilinks_to_rewrite: list[dict[str, Any]]
    index_entry_will_be_removed: bool
    raw_source_to_delete: str | None = None
    shared_entity_warnings: list[str]
    match_methods_used: dict[str, str]


class CascadeDeleteResponse(BaseModel):
    """
    DELETE /pages/{id} response (ADR-0026 §6.1, AC-F13-5 / L9).

    deleted_page_id: the page that was deleted.
    wikilinks_cleaned: total [[Target]] spans neutralised.
    index_entry_removed: True when index.md was successfully regenerated.
    shared_entity_warnings: advisory list of source-overlap pages.
    cleaned_references: alias for wikilinks_cleaned (L9 contract).
    """

    deleted_page_id: uuid.UUID
    wikilinks_cleaned: int
    index_entry_removed: bool
    shared_entity_warnings: list[str]
    cleaned_references: int = Field(
        default=0,
        description=(
            "Number of referencing pages whose dead wikilinks were neutralised. "
            "Mirrors wikilinks_cleaned (L9 — same value). "
            "Returned as a convenience alias for the lint batch L9 consumer."
        ),
    )


@router.post(
    "/pages/{page_id}/cascade-delete/preview",
    response_model=CascadePreviewResponse,
    summary="Dry-run preview of cascade delete (read-only)",
    description=(
        "F13 Cascade Delete — mandatory dry-run (ADR-0026 §6, AC-F13-5). "
        "Computes the full deletion plan WITHOUT mutating any store or file: "
        "no soft-delete, no Qdrant delete, no file write, no data_version bump. "
        "Returns will_delete, wikilinks_to_rewrite, shared_entity_warnings, match_methods_used. "
        "404 if the page does not exist or is already soft-deleted. "
        "Call this before DELETE /pages/{id} to populate a confirmation modal (AC-F13-6)."
    ),
    responses={
        200: {"description": "Cascade plan computed (read-only)"},
        404: {"description": "Page not found or already deleted"},
    },
)
async def cascade_delete_preview(page_id: uuid.UUID) -> CascadePreviewResponse:
    """
    POST /pages/{page_id}/cascade-delete/preview — dry-run plan (ADR-0026 §6, AC-F13-5).

    Read-only: plan_cascade_delete() never mutates any store or file.
    404 on unknown / already-soft-deleted page (PageNotFoundError).
    """
    from app.ops.cascade_delete import PageNotFoundError, plan_cascade_delete

    try:
        plan = await plan_cascade_delete(page_id)
    except PageNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return CascadePreviewResponse(
        target_page_id=plan.target_page_id,
        target_title=plan.target_title,
        target_file_path=plan.target_file_path,
        will_delete=plan.will_delete,
        will_preserve_with_pruned_source=plan.will_preserve_with_pruned_source,
        wikilinks_to_rewrite=[
            {
                "source_page_id": str(r.source_page_id),
                "file_path": r.file_path,
                "target_title": r.target_title,
                "occurrences": r.occurrences,
            }
            for r in plan.wikilinks_to_rewrite
        ],
        index_entry_will_be_removed=plan.index_entry_will_be_removed,
        raw_source_to_delete=plan.raw_source_to_delete,
        shared_entity_warnings=plan.shared_entity_warnings,
        match_methods_used=plan.match_methods_used,
    )


@router.delete(
    "/pages/{page_id}",
    response_model=CascadeDeleteResponse,
    summary="Cascade-delete a wiki page and clean up dead wikilinks",
    description=(
        "F13 Cascade Delete (ADR-0026, AC-F13-1..7) + L9 (lint-initiated delete). "
        "wiki/ pages only — 409 for meta pages (index.md, log.md, overview.md). "
        "Single-pass, inference-free operation: "
        "soft-deletes the page (deleted_at=now()); hard-deletes its Qdrant point; "
        "rewrites all dead [[Target]] wikilinks to plain text (body-only, frontmatter-safe, I5); "
        "removes the index.md catalogue entry; deletes the raw/sources/ file (AQ-v0.5-5); "
        "appends 'DELETED | path' to log.md (K4); "
        "bumps data_version EXACTLY ONCE (I2); fires the debounced graph recompute (I2). "
        "Makes ZERO inference calls, ZERO FA2 calls. "
        "404 on non-existent or already-soft-deleted page (idempotent double-delete, AC-F13-5c). "
        "Use POST /pages/{id}/cascade-delete/preview first (ADR-0026 §6 — mandatory dry-run). "
        "NEVER called from any automated path (K8 — explicit human action only)."
    ),
    responses={
        200: {"description": "Page deleted; dead wikilinks cleaned; index.md updated"},
        404: {"description": "Page not found or already deleted (AC-F13-5c)"},
        409: {
            "description": (
                "Meta page (index.md / log.md / overview.md) cannot be deleted via this endpoint"
            )
        },
    },
)
async def delete_page(page_id: uuid.UUID) -> CascadeDeleteResponse:
    """
    DELETE /pages/{page_id} — cascade delete (ADR-0026 / L9).

    Single pass; zero inference; zero FA2 (I7/I2/I6). data_version +1 EXACTLY ONCE.
    404 on double-delete (PageNotFoundError from plan_cascade_delete).
    409 for meta pages (index.md / log.md / overview.md).
    Appends 'DELETED | path' to log.md (K4).
    NEVER called from any automated path (K8).
    """
    from app.ops.cascade_delete import PageNotFoundError, cascade_delete

    # ── L9: meta-page guard (409 for navigation roots — never deletable via this endpoint) ──
    # Use raw SQL for portability with the SQLite test schema (avoids JSONB column issues).
    _META_PAGE_BASES = frozenset({"index.md", "log.md", "overview.md"})
    async with runtime_state.get_session() as session:
        meta_row = await session.execute(
            sa_text(
                "SELECT file_path FROM pages "
                "WHERE CAST(id AS TEXT) = :pid AND deleted_at IS NULL"
            ).bindparams(pid=str(page_id))
        )
        meta_fp_row = meta_row.first()

    if meta_fp_row is not None:
        meta_fp: str = str(meta_fp_row[0] or "")
        base = meta_fp.rsplit("/", 1)[-1].lower()
        if base in _META_PAGE_BASES:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Meta page '{base}' (file_path={meta_fp!r}) cannot be "
                    "deleted via this endpoint. It is a navigation root (L9 guard)."
                ),
            )

    try:
        result = await cascade_delete(page_id)
    except PageNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # ── L9: log.md append (K4 — append-only; NEVER truncated) ───────────────────
    try:
        from app.ingest.orchestrator import append_log

        # Resolve the file_path from the now soft-deleted row (no deleted_at filter).
        async with runtime_state.get_session() as del_sess:
            del_row = await del_sess.execute(
                sa_text("SELECT file_path FROM pages WHERE CAST(id AS TEXT) = :pid").bindparams(
                    pid=str(page_id)
                )
            )
            fp_row = del_row.first()
        deleted_fp = str(fp_row[0]) if fp_row and fp_row[0] else str(page_id)
        # Single source of truth for the log format (K4 narrative, date-grouped).
        await append_log(deleted_fp, action="deleted")
    except Exception as exc:  # noqa: BLE001
        logger.warning("delete_page: log.md append failed: %s", exc)

    return CascadeDeleteResponse(
        deleted_page_id=result.deleted_page_id,
        wikilinks_cleaned=result.wikilinks_cleaned,
        index_entry_removed=result.index_entry_removed,
        shared_entity_warnings=result.shared_entity_warnings,
        cleaned_references=result.wikilinks_cleaned,
    )
