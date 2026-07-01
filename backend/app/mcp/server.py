"""
Synapse MCP server (FastMCP, stdio transport — ADR-0010 §1).

Exposes four tools to CliAgentProvider and to any external MCP client (e.g. Claude Desktop):
    search_wiki   — search via the shared 4-phase retrieval path (degrades to lexical when
                    embeddings are off — ADR-0030 §2.6; no duplicated lexical branch, I9)
    write_page    — validate → slug → write → persist (I1, I5); reuses write_wiki_page
    get_page      — return a page's full content and frontmatter by title
    list_pages    — list live pages with optional type filter

All four tools honour the shared-write-path contract (ADR-0010 §2):
    write_page calls the same write_wiki_page() primitive the orchestrator uses.

Transport: stdio (ADR-0010 §1). HTTP surface optionally mounted into FastAPI at /mcp/server
when MCP_AUTH_TOKEN is set (ADR-0029). The HTTP surface is built by build_http_mcp() which
creates a *separate* FastMCP instance that re-registers only the desired tools from the
shared tool-body functions below — so the stdio `mcp` always keeps all four tools.

Run entry point: `python -m app.mcp.server`

The `mcp` object is the FastMCP server instance; it is imported by orchestrator._delegate_ingest
and passed to CliAgentProvider.delegate_ingest(mcp_server=...) so the CLI agent uses the
Synapse-managed write path rather than raw filesystem writes (I1/I5, ADR-0010).
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass, field
from typing import Any

from fastmcp import FastMCP

from app.config import settings
from app.ingest.loop import validate_pages
from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
from app.rag.retrieval import retrieve

logger = logging.getLogger(__name__)

# ── FastMCP server instance ────────────────────────────────────────────────────
# stdio transport (ADR-0010 §1). NEVER modify tool registrations here — the stdio
# server always exposes all four tools (I6, test_four_tools_registered).
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


# ── Delegated-run write-record (ADR-0044 §4.2, Phase E) ──────────────────────────
# A pure SIDE-RECORD of the pages write_page writes DURING one delegated (CLI) ingest run, so
# the orchestrator can enumerate them afterward and drive the SAME propose_reviews seam (no new
# table, no new agent loop, no provider branch — I6). Keyed per run via a contextvar so
# concurrent delegated runs never clash; a run that never enters the context records nothing
# (stdio / external MCP clients are unaffected — the write-record is opt-in by construction).


@dataclass
class DelegatedWriteRecord:
    """Titles + ids write_page wrote during the current delegated run (ADR-0044 §4.2)."""

    ids: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)

    def record(self, page_id: str, title: str | None) -> None:
        if page_id and page_id not in self.ids:
            self.ids.append(page_id)
            self.titles.append(title or "")


_delegated_write_record: contextvars.ContextVar[DelegatedWriteRecord | None] = (
    contextvars.ContextVar("synapse_delegated_write_record", default=None)
)


class delegated_write_capture:
    """
    Context manager that installs a fresh DelegatedWriteRecord for a delegated ingest run.

    Usage (orchestrator._delegate_ingest, ADR-0044 §4.2):

        with delegated_write_capture() as record:
            ... run the delegated agent (it writes via write_page) ...
        # record.ids / record.titles now hold what the agent wrote through write_page.

    Nesting-safe: the previous record (if any) is restored on exit. No global mutable state.
    """

    def __init__(self) -> None:
        self._token: contextvars.Token[DelegatedWriteRecord | None] | None = None
        self.record = DelegatedWriteRecord()

    def __enter__(self) -> DelegatedWriteRecord:
        self._token = _delegated_write_record.set(self.record)
        return self.record

    def __exit__(self, *exc: object) -> None:
        if self._token is not None:
            _delegated_write_record.reset(self._token)
            self._token = None


# ─────────────────────────────────────────────────────────────────────────────
# Shared tool-body functions (DRY — used by both the stdio `mcp` and the HTTP
# FastMCP instance returned by build_http_mcp()).  All business logic lives here;
# the @mcp.tool() / @http_mcp.tool() decorators below are thin wrappers.
# ─────────────────────────────────────────────────────────────────────────────


async def _search_wiki_body(query: str, k: int = 5) -> list[dict[str, Any]]:
    """
    Search the Synapse wiki via the SHARED retrieval path (F5, ADR-0022 / ADR-0030 §2.6).

    Routes through ``rag.retrieval.retrieve()`` — the single 4-phase pipeline used by
    ``/search`` and ``/chat`` — rather than calling the embedding client / Qdrant directly.
    This means it degrades automatically: when ``EMBEDDINGS_ENABLED=false`` (ADR-0030),
    ``retrieve()`` internally swaps dense Phase 1 for a Postgres lexical match, so this tool
    returns keyword hits instead of erroring. No lexical branch is duplicated here (I9).

    Returns up to *k* results derived from the retrieval citations, ranked by score.

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

    from app.chat.context import DEFAULT_CONTEXT_WINDOW

    try:
        ctx = await retrieve(
            query,
            vault_id=settings.vault_id,
            context_window=DEFAULT_CONTEXT_WINDOW,
            k=k,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("search_wiki: retrieve() failed: %s", exc)
        return []

    # Map citations → PageRef descriptors, highest score first, capped at k.
    ordered = sorted(ctx.citations, key=lambda c: c.score, reverse=True)
    results: list[dict[str, Any]] = []
    for cit in ordered[:k]:
        results.append(
            {
                "id": cit.ref.id,
                "title": cit.ref.title,
                # PageRef carries no page_type; the retrieval layer is the source of truth
                # for citable refs. Type is left None (consumers treat it as optional).
                "type": None,
                "relevance_score": round(float(cit.score), 4),
            }
        )
    return results


async def _write_page_body(
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
        content:       The markdown body ONLY — do NOT include a YAML frontmatter
                       block or a leading `---` fence. Frontmatter fields go in the
                       `frontmatter` argument. A stray leading block is stripped
                       defensively, but relying on that is a contract violation.
        frontmatter:   Dict with at least {type, title, sources, lang}. SHOULD also
                       include `tags`: 3–6 concise, lowercase, reusable navigation tags
                       (list[str]); they are trimmed/deduped/capped to 12 automatically.
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

    # ── ADR-0044 §4.2: side-record the write for the current delegated run (if any) ──
    # Pure record; no behavior change. Only populated when a delegated_write_capture() is
    # active (CLI delegated ingest). stdio / external MCP callers → no active record → no-op.
    _record = _delegated_write_record.get()
    if _record is not None:
        _record.record(str(page_row.id), page_row.title)

    return {
        "id": str(page_row.id),
        "title": page_row.title,
        "type": page_row.page_type,
        "relevance_score": 0.0,
    }


async def _get_page_body(title: str) -> dict[str, Any]:
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


async def _list_pages_body(type: str | None = None) -> list[dict[str, Any]]:
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


# ── Tool: search_wiki (stdio mcp) ─────────────────────────────────────────────


@mcp.tool()
async def search_wiki(query: str, k: int = 5) -> list[dict[str, Any]]:
    """
    Search the Synapse wiki via the SHARED retrieval path (F5, ADR-0022 / ADR-0030 §2.6).

    Routes through ``rag.retrieval.retrieve()`` — the single 4-phase pipeline used by
    ``/search`` and ``/chat`` — rather than calling the embedding client / Qdrant directly.
    This means it degrades automatically: when ``EMBEDDINGS_ENABLED=false`` (ADR-0030),
    ``retrieve()`` internally swaps dense Phase 1 for a Postgres lexical match, so this tool
    returns keyword hits instead of erroring. No lexical branch is duplicated here (I9).

    Returns up to *k* results derived from the retrieval citations, ranked by score.

    Args:
        query: Natural-language search query.
        k:     Maximum number of results to return (default 5).

    Returns:
        list of {id, title, type, relevance_score}.
    """
    return await _search_wiki_body(query, k)


# ── Tool: write_page (stdio mcp) ─────────────────────────────────────────────


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
        content:       The markdown body ONLY — do NOT include a YAML frontmatter
                       block or a leading `---` fence. Frontmatter fields go in the
                       `frontmatter` argument. A stray leading block is stripped
                       defensively, but relying on that is a contract violation.
        frontmatter:   Dict with at least {type, title, sources, lang}. SHOULD also
                       include `tags`: 3–6 concise, lowercase, reusable navigation tags
                       (list[str]); they are trimmed/deduped/capped to 12 automatically.
        origin_source: Optional origin path injected into sources[] for F3 traceability.

    Returns:
        {"id", "title", "type", "relevance_score": 0.0} on success.
        {"error": "<message>"} on validation failure.
    """
    return await _write_page_body(title, content, frontmatter, origin_source)


# ── Tool: get_page (stdio mcp) ────────────────────────────────────────────────


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
    return await _get_page_body(title)


# ── Tool: list_pages (stdio mcp) ──────────────────────────────────────────────


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
    return await _list_pages_body(type)


# ── Internal validation helper ────────────────────────────────────────────────


def _validate_frontmatter_dict(fm: dict[str, Any]) -> str | None:
    """
    Quick-check the frontmatter dict before attempting WikiFrontmatter construction.

    Returns a human-readable error string if invalid, else None.
    This is a pre-validation step; WikiFrontmatter() provides the full Pydantic validation.
    """
    missing: list[str] = []
    for required_key in ("type", "title", "sources", "lang"):
        if not fm.get(required_key):
            missing.append(required_key)
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


# ── HTTP MCP factory (ADR-0029 §2.3) ──────────────────────────────────────────


def build_http_mcp(*, write_enabled: bool) -> FastMCP:
    """
    Build a FastMCP instance for the /mcp/server HTTP surface (ADR-0029 §2.3).

    Returns a *separate* FastMCP instance that registers ONLY the read-only tools
    (search_wiki, get_page, list_pages) by default, plus write_page iff write_enabled.
    All tool bodies delegate to the SAME underlying ``_*_body`` functions used by the
    stdio ``mcp`` — DRY, single write path enforced (ADR-0010 §2, I1/I5).

    The stdio ``mcp`` module-level object is NEVER modified by this function.

    Args:
        write_enabled: If True, write_page is also registered on the HTTP surface.

    Returns:
        A configured FastMCP instance ready for ``http_app()`` mounting.
    """
    http_mcp = FastMCP(
        name="synapse-http",
        instructions=(
            "Synapse remote wiki tools (HTTP/Streamable-HTTP, ADR-0029). "
            "Use search_wiki to find relevant pages. "
            "Use get_page / list_pages for read access. "
            + (
                "Use write_page to create or update wiki pages "
                "(validation + frontmatter enforced). "
                if write_enabled
                else ""
            )
        ),
    )

    # ── Read-only tools (always present on the HTTP surface) ──────────────────

    @http_mcp.tool()
    async def search_wiki(query: str, k: int = 5) -> list[dict[str, Any]]:  # noqa: F811
        """
        Search the Synapse wiki via the SHARED retrieval path (F5, ADR-0022 / ADR-0030 §2.6).

        Routes through ``rag.retrieval.retrieve()`` — the single 4-phase pipeline used by
        ``/search`` and ``/chat`` — rather than calling the embedding client / Qdrant directly.
        This means it degrades automatically: when ``EMBEDDINGS_ENABLED=false`` (ADR-0030),
        ``retrieve()`` internally swaps dense Phase 1 for a Postgres lexical match, so this tool
        returns keyword hits instead of erroring. No lexical branch is duplicated here (I9).

        Returns up to *k* results derived from the retrieval citations, ranked by score.

        Args:
            query: Natural-language search query.
            k:     Maximum number of results to return (default 5).

        Returns:
            list of {id, title, type, relevance_score}.
        """
        return await _search_wiki_body(query, k)

    @http_mcp.tool()
    async def get_page(title: str) -> dict[str, Any]:  # noqa: F811
        """
        Retrieve a live wiki page by title.

        Returns {title, type, content, frontmatter} on success, or {"error": "..."} if the
        page is not found or has been soft-deleted.

        Args:
            title: Exact page title (case-sensitive).

        Returns:
            {title, type, content, frontmatter} or {"error": "<message>"}.
        """
        return await _get_page_body(title)

    @http_mcp.tool()
    async def list_pages(type: str | None = None) -> list[dict[str, Any]]:  # noqa: F811
        """
        List live wiki pages, optionally filtered by page type.

        Excludes soft-deleted pages (deleted_at IS NOT NULL). Results are sorted by title.

        Args:
            type: Optional page type filter (entity/concept/source/synthesis/comparison).
                  Passing None returns all live pages.

        Returns:
            list of {id, title, type, relevance_score: 0.0}.
        """
        return await _list_pages_body(type)

    # ── write_page — only when explicitly opted-in (ADR-0029 §2.3) ───────────

    if write_enabled:

        @http_mcp.tool()
        async def write_page(  # noqa: F811
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
                content:       The markdown body ONLY — do NOT include a YAML frontmatter
                       block or a leading `---` fence. Frontmatter fields go in the
                       `frontmatter` argument. A stray leading block is stripped
                       defensively, but relying on that is a contract violation.
                frontmatter:   Dict with at least {type, title, sources, lang}. SHOULD
                       also include `tags`: 3–6 concise, lowercase, reusable navigation
                       tags (list[str]); trimmed/deduped/capped to 12 automatically.
                origin_source: Optional origin path injected into sources[] for F3 traceability.

            Returns:
                {"id", "title", "type", "relevance_score": 0.0} on success.
                {"error": "<message>"} on validation failure.
            """
            return await _write_page_body(title, content, frontmatter, origin_source)

    return http_mcp


# ── In-process SDK MCP server factory (claude-agent-sdk, ADR-0010 §2) ──────────


def build_sdk_mcp_server() -> Any:
    """
    Build an IN-PROCESS SDK MCP server for the CLI delegated ingest path (F17, ADR-0010 §2).

    This is a DIFFERENT surface from the FastMCP `mcp` object / build_http_mcp() above: the
    claude-agent-sdk (0.2.x) does NOT accept a FastMCP object as an in-process server. Passing
    one to ClaudeAgentOptions(mcp_servers=...) makes the SDK try to JSON-serialize it as an
    EXTERNAL server config → `Object of type FastMCP is not JSON serializable`. Instead the SDK
    expects a `McpSdkServerConfig` dict {"type":"sdk","name":str,"instance":<McpServer>} built by
    `create_sdk_mcp_server(name, version, tools=[SdkMcpTool, ...])`.

    Each SDK tool's async handler receives a SINGLE `args: dict`, delegates to the shared
    ``_*_body`` functions (so I1/I5 hold identically — one write path, ADR-0010 §2), and returns
    the SDK content shape {"content":[{"type":"text","text": <string>}]} (dict/list results are
    json.dumps-encoded). Tool names are the BARE names in MCP_TOOL_NAMES; the SDK namespaces them
    to the model as ``mcp__synapse__<toolname>`` (cli.py builds allowed_tools accordingly).

    The claude-agent-sdk import is LAZY (kept inside this function) so importing app.mcp.server
    without the SDK installed still works (the stdio/HTTP FastMCP paths need no SDK). A clear
    RuntimeError is raised if the SDK is missing.

    Returns the McpSdkServerConfig dict from create_sdk_mcp_server (name="synapse").
    """
    import json

    try:
        from claude_agent_sdk import create_sdk_mcp_server, tool
    except ImportError as exc:  # pragma: no cover - exercised only without the SDK installed
        raise RuntimeError(
            "claude-agent-sdk is not installed; the in-process SDK MCP server (CLI delegated "
            "ingest) requires it (R3). Install it in the backend environment."
        ) from exc

    def _wrap(result: Any) -> dict[str, Any]:
        """Wrap a shared-body result into the SDK content shape (json for dict/list)."""
        text = result if isinstance(result, str) else json.dumps(result, default=str)
        return {"content": [{"type": "text", "text": text}]}

    @tool(
        "search_wiki",
        "Search the Synapse wiki via the shared 4-phase retrieval path (F5). "
        "Returns up to k ranked {id, title, type, relevance_score} results.",
        {"query": str, "k": int},
    )
    async def _sdk_search_wiki(args: dict[str, Any]) -> dict[str, Any]:
        return _wrap(await _search_wiki_body(args["query"], int(args.get("k", 5) or 5)))

    @tool(
        "write_page",
        "Create or update a wiki page through the Synapse ingest seam (I1/I5, ADR-0010 §2). "
        "Validates frontmatter (type, title, sources[], lang) before writing. "
        "Include 3–6 concise, lowercase, reusable `tags` (list[str]) in the frontmatter for "
        "navigation (auto trimmed/deduped/capped to 12). "
        "content MUST be the markdown body ONLY — do NOT include a YAML frontmatter block "
        "or a leading `---` fence; frontmatter fields go in the `frontmatter` argument.",
        {"title": str, "content": str, "frontmatter": dict, "origin_source": str},
    )
    async def _sdk_write_page(args: dict[str, Any]) -> dict[str, Any]:
        return _wrap(
            await _write_page_body(
                args["title"],
                args["content"],
                args["frontmatter"],
                args.get("origin_source", "") or "",
            )
        )

    @tool(
        "get_page",
        "Retrieve a live wiki page by exact title. Returns {title, type, content, frontmatter} "
        "or {error}.",
        {"title": str},
    )
    async def _sdk_get_page(args: dict[str, Any]) -> dict[str, Any]:
        return _wrap(await _get_page_body(args["title"]))

    @tool(
        "list_pages",
        "List live wiki pages, optionally filtered by page type. Returns "
        "[{id, title, type, relevance_score}].",
        {"type": str},
    )
    async def _sdk_list_pages(args: dict[str, Any]) -> dict[str, Any]:
        return _wrap(await _list_pages_body(args.get("type") or None))

    return create_sdk_mcp_server(
        name="synapse",
        version="1.0.0",
        tools=[_sdk_search_wiki, _sdk_write_page, _sdk_get_page, _sdk_list_pages],
    )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # python -m app.mcp.server — start the MCP server over stdio (ADR-0010 §1).
    mcp.run(transport="stdio")
