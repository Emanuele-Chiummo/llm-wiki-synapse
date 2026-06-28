"""
Synapse MCP server (FastMCP, stdio transport — ADR-0010 §1).

Exposes four tools to CliAgentProvider and to any external MCP client (e.g. Claude Desktop):
    search_wiki   — vector search via Qdrant + bge-m3 (I9)
    write_page    — validate → slug → write → persist (I1, I5); reuses write_wiki_page
    get_page      — return a page's full content and frontmatter by title
    list_pages    — list live pages with optional type filter

All four tools honour the shared-write-path contract (ADR-0010 §2):
    write_page calls the same write_wiki_page() primitive the orchestrator uses.

Transport: stdio in v0.2 (AQ-v0.2-6). HTTP is deferred to v0.4.
Run entry point: `python -m app.mcp.server`

The `mcp` object is the FastMCP server instance; it is imported by orchestrator._delegate_ingest
and passed to CliAgentProvider.delegate_ingest(mcp_server=...) so the CLI agent uses the
Synapse-managed write path rather than raw filesystem writes (I1/I5, ADR-0010).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from fastmcp import FastMCP

from app.config import settings
from app.embeddings import get_embedding_client
from app.ingest.loop import validate_pages
from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
from app.qdrant_client import get_qdrant_client

logger = logging.getLogger(__name__)

# ── FastMCP server instance ────────────────────────────────────────────────────
# stdio transport (ADR-0010 §1); HTTP is `mcp.run(transport="sse")` if needed later.
mcp = FastMCP(
    name="synapse",
    instructions=(
        "Synapse wiki tools. Use write_page to create or update wiki pages "
        "(validation + frontmatter enforced). Use search_wiki to find relevant pages. "
        "Always include the source path in frontmatter.sources[] for traceability (F3)."
    ),
)


# ── PageRef DTO ────────────────────────────────────────────────────────────────


@dataclass
class PageRef:
    """Minimal page descriptor returned by search_wiki and list_pages (v0.2-architecture §6)."""

    id: str
    title: str | None
    type: str | None
    relevance_score: float = 0.0


# ── Tool: search_wiki ──────────────────────────────────────────────────────────


@mcp.tool()
async def search_wiki(query: str, k: int = 5) -> list[dict[str, Any]]:
    """
    Semantic search over the Synapse wiki using bge-m3 embeddings and Qdrant (I9).

    This is a simple vector lookup, NOT the full 4-phase RAG pipeline (F5, deferred to v0.5).
    Returns up to *k* results sorted by cosine similarity score (normalised to [0,1]).

    Args:
        query: Natural-language search query.
        k:     Maximum number of results to return (default 5).

    Returns:
        list of {id, title, type, relevance_score}.
    """
    if k < 1:
        k = 1
    if k > 50:
        k = 50

    # Embed the query using the existing bge-m3 client (I9 — no new service).
    client = get_embedding_client()
    try:
        vector = await client.embed(query)
    except Exception as exc:  # noqa: BLE001
        logger.error("search_wiki: embedding failed: %s", exc)
        return []

    # Search Qdrant (I9 — reuse synapse_pages collection, ADR-0002).
    # The Qdrant Python client ≥ 1.10 removed .search() in favour of .query_points()
    # which returns a QueryResponse with a .points list of ScoredPoint objects.
    qdrant = get_qdrant_client()
    try:
        query_response = await qdrant.query_points(
            collection_name=settings.qdrant_collection,
            query=vector,
            limit=k,
            with_payload=True,
        )
        hits = query_response.points
    except Exception as exc:  # noqa: BLE001
        logger.error("search_wiki: Qdrant search failed: %s", exc)
        return []

    results: list[dict[str, Any]] = []
    for hit in hits:
        payload = hit.payload or {}
        # Qdrant cosine scores are already in [-1, 1]; normalise to [0, 1].
        score = float(hit.score)
        normalised = max(0.0, min(1.0, (score + 1.0) / 2.0))
        results.append(
            {
                "id": str(hit.id),
                "title": payload.get("title"),
                "type": payload.get("type"),
                "relevance_score": round(normalised, 4),
            }
        )
    return results


# ── Tool: write_page ──────────────────────────────────────────────────────────


@mcp.tool()
async def write_page(
    title: str,
    content: str,
    frontmatter: dict[str, Any],
    origin_source: str = "",
) -> dict[str, Any]:
    """
    Create or update a wiki page through the Synapse ingest seam (I1, I5, ADR-0010 §2).

    Validates frontmatter (type, title, sources[], lang) before writing. Returns a
    structured error dict (not an exception) on missing/invalid fields so the CLI agent
    can retry without crashing (AC-MCP-3).

    The page is written via write_wiki_page() — the SAME primitive the orchestrated loop
    uses — so K5 wikilink parsing, K3 index update, Qdrant upsert, and log append all run
    identically (ADR-0010 §2, single write path).

    Args:
        title:         Page title (non-empty).
        content:       Markdown body WITHOUT a frontmatter block.
        frontmatter:   Dict with at least {type, title, sources, lang}.
        origin_source: Optional origin path injected into sources[] for F3 traceability.

    Returns:
        {"id", "title", "type", "relevance_score": 0.0} on success.
        {"error": "<message>"} on validation failure.
    """
    # ── Validate and construct WikiPage ──────────────────────────────────────
    error = _validate_frontmatter_dict(frontmatter)
    if error:
        return {"error": error}

    # Build typed WikiFrontmatter — raises if the dict is still invalid (defensive).
    try:
        fm = WikiFrontmatter(**frontmatter)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"frontmatter validation failed: {exc}"}

    try:
        page_type = PageType(frontmatter.get("type", ""))
    except ValueError:
        return {
            "error": (
                f"invalid type {frontmatter.get('type')!r}; "
                f"must be one of {sorted(pt.value for pt in PageType)}"
            )
        }

    wiki_page = WikiPage(
        title=title,
        type=page_type,
        content=content,
        frontmatter=fm,
    )

    # ── Run the shared validator (ADR-0007 §5 / ADR-0010 §2 — ONE validator) ──
    errors = validate_pages([wiki_page], origin_source)
    if errors:
        return {"error": "; ".join(errors)}

    # ── Write via the shared seam (ADR-0010 §2) ───────────────────────────────
    try:
        from app.ingest.orchestrator import write_wiki_page

        page_row = await write_wiki_page(None, wiki_page, origin_source)
    except Exception as exc:  # noqa: BLE001
        logger.error("write_page MCP tool: write_wiki_page failed: %s", exc)
        return {"error": f"write failed: {exc}"}

    return {
        "id": str(page_row.id),
        "title": page_row.title,
        "type": page_row.page_type,
        "relevance_score": 0.0,
    }


# ── Tool: get_page ────────────────────────────────────────────────────────────


@mcp.tool()
async def get_page(title: str) -> dict[str, Any]:
    """
    Retrieve a live wiki page by title.

    Returns {title, type, content, frontmatter} on success, or {"error": "..."} if the
    page is not found or has been soft-deleted.

    Args:
        title: Exact page title (case-sensitive).

    Returns:
        {title, type, content, frontmatter} or {"error": "<message>"}.
    """
    from sqlalchemy import select

    from app.db import get_session
    from app.models import Page

    async with get_session() as session:
        row = await session.execute(
            select(Page).where(
                Page.vault_id == settings.vault_id,
                Page.title == title,
                Page.deleted_at.is_(None),
            )
        )
        page = row.scalar_one_or_none()
        if page is not None:
            session.expunge(page)

    if page is None:
        return {"error": f"page not found: {title!r}"}

    # Read the actual file content (the DB stores metadata; content is on disk).
    abs_path = settings.vault_root / page.file_path
    if not abs_path.exists():
        return {"error": f"page file missing on disk: {page.file_path}"}

    import frontmatter as fm_lib

    raw = abs_path.read_text(encoding="utf-8")
    try:
        doc = fm_lib.loads(raw)
        body = doc.content
        meta = dict(doc.metadata)
    except Exception:  # noqa: BLE001
        body = raw
        meta = {}

    return {
        "title": page.title,
        "type": page.page_type,
        "content": body,
        "frontmatter": meta,
    }


# ── Tool: list_pages ──────────────────────────────────────────────────────────


@mcp.tool()
async def list_pages(type: str | None = None) -> list[dict[str, Any]]:
    """
    List live wiki pages, optionally filtered by page type.

    Excludes soft-deleted pages (deleted_at IS NOT NULL). Results are sorted by title.

    Args:
        type: Optional page type filter (entity/concept/source/synthesis/comparison).
              Passing None returns all live pages.

    Returns:
        list of {id, title, type, relevance_score: 0.0}.
    """
    from sqlalchemy import select

    from app.db import get_session
    from app.models import Page

    async with get_session() as session:
        stmt = select(Page.id, Page.title, Page.page_type).where(
            Page.vault_id == settings.vault_id,
            Page.deleted_at.is_(None),
        )
        if type is not None:
            stmt = stmt.where(Page.page_type == type)
        stmt = stmt.order_by(Page.title.asc().nullslast())

        rows = await session.execute(stmt)
        results = rows.all()

    return [
        {
            "id": str(row.id),
            "title": row.title,
            "type": row.page_type,
            "relevance_score": 0.0,
        }
        for row in results
    ]


# ── Internal validation helper ────────────────────────────────────────────────


def _validate_frontmatter_dict(fm: dict[str, Any]) -> str | None:
    """
    Quick-check the frontmatter dict before attempting WikiFrontmatter construction.

    Returns a human-readable error string if invalid, else None.
    This is a pre-validation step; WikiFrontmatter() provides the full Pydantic validation.
    """
    missing: list[str] = []
    for field in ("type", "title", "sources", "lang"):
        if not fm.get(field):
            missing.append(field)
    if missing:
        return f"frontmatter missing required fields: {missing} (ADR-0007 §5, I5)"

    sources = fm.get("sources")
    if not isinstance(sources, list) or not any(isinstance(s, str) and s.strip() for s in sources):
        return "frontmatter.sources[] must be a non-empty list of non-empty strings (F3)"

    valid_types = {pt.value for pt in PageType}
    if fm.get("type") not in valid_types:
        return (
            f"frontmatter.type {fm.get('type')!r} is not a valid PageType; "
            f"expected one of {sorted(valid_types)}"
        )

    return None


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # python -m app.mcp.server — start the MCP server over stdio (ADR-0010 §1).
    mcp.run(transport="stdio")
