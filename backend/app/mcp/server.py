"""
Synapse MCP server (FastMCP, stdio transport — ADR-0010 §1).

Exposes nine tools to CliAgentProvider and to any external MCP client (e.g. Claude Desktop):

READ-ONLY tools (always registered on both stdio and HTTP surfaces):
    search_wiki            — search via the shared 4-phase retrieval path (degrades to lexical
                             when embeddings are off — ADR-0030 §2.6; no duplicated lexical
                             branch, I9)
    get_page               — return a page's full content and frontmatter by title
    list_pages             — list live pages with optional type filter
    get_graph_neighborhood — page + 1-2 hop neighbors from the links/edges tables (B5/D2)
    list_reviews           — review queue items (id, type, title, status) (B5/D2)
    read_source_file       — text content of a raw/sources/ file (path-safe, cap bytes) (B5/D2)

WRITE tools (stdio: always registered; HTTP: gated behind write_enabled — ADR-0029 §2.3):
    write_page             — validate → slug → write → persist (I1, I5); reuses write_wiki_page
    resolve_review         — resolve one review item (B5/D2)
    trigger_source_rescan  — kick POST /sources/ingest-all (B5/D2)

All write tools honour the shared-write-path contract (ADR-0010 §2).

Transport: stdio (ADR-0010 §1). HTTP surface optionally mounted into FastAPI at /mcp/server
when MCP_AUTH_TOKEN is set (ADR-0029). The HTTP surface is built by build_http_mcp() which
creates a *separate* FastMCP instance that re-registers only the desired tools from the
shared tool-body functions below — so the stdio `mcp` always keeps all nine tools.

Run entry point: `python -m app.mcp.server`

The `mcp` object is the FastMCP server instance; it is imported by orchestrator._delegate_ingest
and passed to CliAgentProvider.delegate_ingest(mcp_server=...) so the CLI agent uses the
Synapse-managed write path rather than raw filesystem writes (I1/I5, ADR-0010).
"""

from __future__ import annotations

import asyncio as _asyncio
import contextvars
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastmcp import FastMCP

from app.config import settings
from app.ingest.loop import validate_pages
from app.ingest.schemas import PageType, WikiFrontmatter, WikiPage
from app.rag.retrieval import retrieve

logger = logging.getLogger(__name__)

# Strong task references — a bare create_task() can be GC'd mid-run (CPython weak-ref).
_bg_tasks: set[_asyncio.Task[Any]] = set()

# ── FastMCP server instance ────────────────────────────────────────────────────
# stdio transport (ADR-0010 §1). NEVER modify tool registrations here — the stdio
# server always exposes all four tools (I6, test_four_tools_registered).
mcp = FastMCP(
    name="synapse",
    instructions=(
        "Synapse wiki tools. Use write_page to create or update wiki pages "
        "(validation + frontmatter enforced). Use search_wiki to find relevant pages. "
        "Use get_graph_neighborhood to explore connections between pages. "
        "Use list_reviews / resolve_review for the HITL review queue (F9). "
        "Use read_source_file to inspect raw source files. "
        "Use trigger_source_rescan to re-index raw/sources/ files. "
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


# ── Multi-vault resolution (W5, ADR-0082 — PF-MCP-VAULT-1) ────────────────────
# Today every MCP tool is hard-wired to settings.vault_id / settings.vault_root (the single
# ACTIVE vault, Model A — see app/projects.py). This helper lets READ-ONLY tools accept an
# OPTIONAL `vault` argument (a project id from the projects registry, app/projects.py) so a
# caller can inspect a vault that is registered but not currently active, without switching
# it. Omitting `vault` (the default, None) preserves EXACT existing behaviour — this is
# strictly additive (non-breaking).
#
# WRITE tools (write_page, resolve_review, trigger_source_rescan) also accept `vault` for
# interface symmetry, but Synapse only serves ONE active vault's filesystem at a time
# (Model A). Attempting to write into a non-active vault would write files under the wrong
# vault_root while the DB continues to key off settings.vault_id — a correctness hazard.
# So those bodies resolve `vault` and, if it names a *different* vault than the currently
# active one, return a structured {"error": ...} instructing the caller to activate it first
# (POST /projects/{id}/activate) rather than silently doing the wrong thing.
def _resolve_vault(vault: str | None) -> tuple[str, Any]:
    """
    Resolve an optional ``vault`` (project id) to ``(vault_id, vault_root)``.

    Falls back to ``(settings.vault_id, settings.vault_root)`` when *vault* is None/blank
    or does not match any known project (read-only tools then behave exactly as before —
    unknown/omitted vault ids are never a hard error for reads).
    """
    if not vault:
        return settings.vault_id, settings.vault_root

    try:
        from app.projects import read_registry as _read_registry

        reg = _read_registry()
        for project in reg.projects:
            if project.id == vault:
                from pathlib import Path as _Path

                return project.id, _Path(project.path)
    except Exception:  # noqa: BLE001 — never let vault resolution crash a read tool
        logger.warning(
            "mcp: could not resolve vault=%r from projects registry", vault, exc_info=True
        )

    logger.info("mcp: vault=%r not found in projects registry; falling back to active vault", vault)
    return settings.vault_id, settings.vault_root


def _vault_write_guard(vault: str | None) -> dict[str, Any] | None:
    """
    Guard for WRITE tools: return a structured error dict if *vault* names a project other
    than the currently active vault, else None (proceed as before).

    Synapse serves one active vault's filesystem at a time (Model A, app/projects.py); cross-
    vault writes are refused rather than risking a file landing under the wrong vault_root.
    """
    if vault and vault != settings.vault_id:
        return {
            "error": (
                f"cannot write to vault={vault!r}: only the active vault "
                f"({settings.vault_id!r}) accepts writes. "
                "Activate it first via POST /projects/{id}/activate, then retry."
            )
        }
    return None


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


async def _search_wiki_body(
    query: str, k: int = 5, vault: str | None = None
) -> list[dict[str, Any]]:
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
        vault: Optional project id (app/projects.py) to search a specific registered vault
               instead of the currently active one (W5, ADR-0082). Omitted/unknown → active
               vault (unchanged behaviour).

    Returns:
        list of {id, title, type, relevance_score}.
    """
    if k < 1:
        k = 1
    if k > 50:
        k = 50

    from app.chat.context import DEFAULT_CONTEXT_WINDOW

    vault_id, _ = _resolve_vault(vault)

    try:
        ctx = await retrieve(
            query,
            vault_id=vault_id,
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
    vault: str | None = None,
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
        vault:         Optional project id (W5, ADR-0082). Synapse only writes to the
                       currently ACTIVE vault's filesystem (Model A) — if *vault* names a
                       different registered vault, this returns a structured error asking
                       the caller to POST /projects/{id}/activate first. Omitted → active
                       vault (unchanged behaviour).

    Returns:
        {"id", "title", "type", "relevance_score": 0.0} on success.
        {"error": "<message>"} on validation failure or cross-vault write attempt.
    """
    guard_error = _vault_write_guard(vault)
    if guard_error is not None:
        return guard_error

    # ── K6/F3/F13 traceability: pre-inject origin_source into sources[] ────────
    # validate_pages() checks that origin_source ∈ fm.sources (F3 guard, loop.py:89-93).
    # write_wiki_page() appends it post-write (orchestrator.py:1041-42) — but validation
    # runs BEFORE write_wiki_page.  When origin_source is non-empty and not already listed,
    # insert it here so the validator passes and the file lands with the correct provenance.
    # Works for both the delegated path (bound origin) and direct callers that supply one.
    # We mutate a local copy so the caller's dict is untouched.
    if origin_source:
        frontmatter = dict(frontmatter)  # shallow copy — never mutate caller's dict
        sources = list(frontmatter.get("sources") or [])
        if origin_source not in sources:
            sources.append(origin_source)
        frontmatter["sources"] = sources

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


async def _get_page_body(title: str, vault: str | None = None) -> dict[str, Any]:
    """
    Retrieve a live wiki page by title.

    Returns {title, type, content, frontmatter} on success, or {"error": "..."} if the
    page is not found or has been soft-deleted.

    Args:
        title: Exact page title (case-sensitive).
        vault: Optional project id (W5, ADR-0082) to read from a specific registered vault
               instead of the currently active one. Omitted/unknown → active vault.

    Returns:
        {title, type, content, frontmatter} or {"error": "<message>"}.
    """
    from sqlalchemy import select

    from app.db import get_session
    from app.models import Page

    vault_id, vault_root = _resolve_vault(vault)

    async with get_session() as session:
        row = await session.execute(
            select(Page).where(
                Page.vault_id == vault_id,
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
    abs_path = vault_root / page.file_path
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


async def _list_pages_body(
    type: str | None = None, vault: str | None = None
) -> list[dict[str, Any]]:
    """
    List live wiki pages, optionally filtered by page type.

    Excludes soft-deleted pages (deleted_at IS NOT NULL). Results are sorted by title.

    Args:
        type: Optional page type filter (entity/concept/source/synthesis/comparison).
              Passing None returns all live pages.
        vault: Optional project id (W5, ADR-0082) to list a specific registered vault
               instead of the currently active one. Omitted/unknown → active vault.

    Returns:
        list of {id, title, type, relevance_score: 0.0}.
    """
    from sqlalchemy import select

    from app.db import get_session
    from app.models import Page

    vault_id, _ = _resolve_vault(vault)

    async with get_session() as session:
        stmt = select(Page.id, Page.title, Page.page_type).where(
            Page.vault_id == vault_id,
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
async def search_wiki(query: str, k: int = 5, vault: str | None = None) -> list[dict[str, Any]]:
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
        vault: Optional project id (app/projects.py) — search a specific registered vault
               instead of the currently active one (W5, ADR-0082). Omitted → active vault
               (unchanged behaviour).

    Returns:
        list of {id, title, type, relevance_score}.
    """
    return await _search_wiki_body(query, k, vault)


# ── Tool: write_page (stdio mcp) ─────────────────────────────────────────────


@mcp.tool()
async def write_page(
    title: str,
    content: str,
    frontmatter: dict[str, Any],
    origin_source: str = "",
    vault: str | None = None,
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
        vault:         Optional project id (W5, ADR-0082). Writes are only accepted for the
                       currently ACTIVE vault (Model A); a different vault id returns a
                       structured error asking the caller to activate it first. Omitted →
                       active vault (unchanged behaviour).

    Returns:
        {"id", "title", "type", "relevance_score": 0.0} on success.
        {"error": "<message>"} on validation failure or cross-vault write attempt.
    """
    return await _write_page_body(title, content, frontmatter, origin_source, vault)


# ── Tool: get_page (stdio mcp) ────────────────────────────────────────────────


@mcp.tool()
async def get_page(title: str, vault: str | None = None) -> dict[str, Any]:
    """
    Retrieve a live wiki page by title.

    Returns {title, type, content, frontmatter} on success, or {"error": "..."} if the
    page is not found or has been soft-deleted.

    Args:
        title: Exact page title (case-sensitive).
        vault: Optional project id (W5, ADR-0082) — read from a specific registered vault
               instead of the currently active one. Omitted → active vault.

    Returns:
        {title, type, content, frontmatter} or {"error": "<message>"}.
    """
    return await _get_page_body(title, vault)


# ── Tool: list_pages (stdio mcp) ──────────────────────────────────────────────


@mcp.tool()
async def list_pages(type: str | None = None, vault: str | None = None) -> list[dict[str, Any]]:
    """
    List live wiki pages, optionally filtered by page type.

    Excludes soft-deleted pages (deleted_at IS NOT NULL). Results are sorted by title.

    Args:
        type: Optional page type filter (entity/concept/source/synthesis/comparison).
              Passing None returns all live pages.
        vault: Optional project id (W5, ADR-0082) — list a specific registered vault
               instead of the currently active one. Omitted → active vault.

    Returns:
        list of {id, title, type, relevance_score: 0.0}.
    """
    return await _list_pages_body(type, vault)


# ── New body functions: graph neighborhood, reviews, source files (B5/D2) ─────

# Depth cap (I7 — never more than 2 hops; avoids BFS fan-out explosion)
_MAX_GRAPH_DEPTH: int = 2

# Limit cap for list_reviews (I7 — bounded list)
_MAX_REVIEW_LIMIT: int = 100

# Read cap for read_source_file (I7 — bounded bytes; 2 MB default)
_SOURCE_FILE_MAX_BYTES: int = 2 * 1024 * 1024


async def _get_graph_neighborhood_body(
    title: str,
    depth: int = 1,
    vault: str | None = None,
) -> dict[str, Any]:
    """
    Return the page matching *title* plus its 1–2 hop neighbors from the links/edges tables.

    READ-ONLY. Depth is capped at 2 (I7 — avoids BFS explosion; I2 — reads PERSISTED edges,
    never triggers FA2 recompute). Reuses the same edges/links tables that Phase 2 of the
    retrieval pipeline reads — no new DB surface (I9).

    Args:
        title: Exact page title (case-sensitive). Error dict if not found.
        depth: BFS hops (1 or 2). Values > 2 are clamped to 2 (I7).
        vault: Optional project id (W5, ADR-0082) — read from a specific registered vault
               instead of the currently active one. Omitted/unknown → active vault.

    Returns:
        {"center": {id, title, type}, "nodes": [{id, title, type}],
         "edges": [{source, target, relation}]}
        or {"error": "..."} if the page is not found.
    """
    from sqlalchemy import text as _sa_text

    from app.db import get_session as _get_session

    depth = max(1, min(depth, _MAX_GRAPH_DEPTH))
    vault_id, _ = _resolve_vault(vault)

    # ── Resolve seed page (portable SQL — Postgres + SQLite, mirrors retrieval.py) ──
    async with _get_session() as session:
        result = await session.execute(
            _sa_text(
                "SELECT CAST(id AS TEXT) AS id, title, type FROM pages "
                "WHERE vault_id = :vid AND title = :t AND deleted_at IS NULL LIMIT 1"
            ).bindparams(vid=vault_id, t=title)
        )
        seed_row = result.first()

    if seed_row is None:
        return {"error": f"page not found: {title!r}"}

    seed_id = str(seed_row._mapping["id"])
    seed_title = seed_row._mapping["title"] or title
    seed_type = seed_row._mapping["type"]

    center = {"id": seed_id, "title": seed_title, "type": seed_type}

    # ── BFS over persisted edges + resolved links (mirrors retrieval._phase2_graph_expansion) ─
    # Reads the edges table directly — NEVER calls GraphEngine or FA2 (I2).
    nodes_by_id: dict[str, dict[str, Any]] = {seed_id: center}
    edge_list: list[dict[str, Any]] = []
    frontier: set[str] = {seed_id}
    visited: set[str] = {seed_id}

    async with _get_session() as session:
        for _ in range(depth):
            if not frontier:
                break
            placeholders = ",".join(f":f{i}" for i in range(len(frontier)))
            binds: dict[str, object] = {f"f{i}": fid for i, fid in enumerate(frontier)}
            binds["vid"] = vault_id

            in_clause = (
                f"(CAST(source_page_id AS TEXT) IN ({placeholders}) "
                f"OR CAST(target_page_id AS TEXT) IN ({placeholders}))"
            )

            # ── Weighted edges (4-signal, persisted by graph engine) ─────────────
            edge_sql = (
                f"SELECT CAST(source_page_id AS TEXT) AS src, "  # noqa: S608
                f"CAST(target_page_id AS TEXT) AS tgt, weight FROM edges "
                f"WHERE vault_id = :vid AND {in_clause}"
            )
            edge_rows = (await session.execute(_sa_text(edge_sql).bindparams(**binds))).all()

            next_frontier: set[str] = set()
            neighbor_ids: set[str] = set()
            for er in edge_rows:
                m = er._mapping
                src, tgt = str(m["src"]), str(m["tgt"])
                if src in frontier and tgt not in visited:
                    neighbor_ids.add(tgt)
                    next_frontier.add(tgt)
                    edge_list.append({"source": src, "target": tgt, "relation": "linked"})
                elif tgt in frontier and src not in visited:
                    neighbor_ids.add(src)
                    next_frontier.add(src)
                    edge_list.append({"source": tgt, "target": src, "relation": "linked"})
                elif src in frontier and tgt in frontier and src != tgt:
                    edge_list.append({"source": src, "target": tgt, "relation": "linked"})

            # ── Resolved wikilinks (direct-link expansion, weight 0.0) ──────────
            # dangling = FALSE check: SQLite stores booleans as integers (0/1),
            # Postgres as booleans. "dangling = 0" is portable.
            link_sql = (
                f"SELECT CAST(source_page_id AS TEXT) AS src, "  # noqa: S608
                f"CAST(target_page_id AS TEXT) AS tgt FROM links "
                f"WHERE dangling = 0 AND target_page_id IS NOT NULL AND {in_clause}"
            )
            link_rows = (await session.execute(_sa_text(link_sql).bindparams(**binds))).all()
            for lr in link_rows:
                m = lr._mapping
                src, tgt = str(m["src"]), str(m["tgt"])
                if src in frontier and tgt not in visited:
                    neighbor_ids.add(tgt)
                    next_frontier.add(tgt)
                    edge_list.append({"source": src, "target": tgt, "relation": "wikilink"})
                elif tgt in frontier and src not in visited:
                    neighbor_ids.add(src)
                    next_frontier.add(src)
                    edge_list.append({"source": tgt, "target": src, "relation": "wikilink"})

            # ── Resolve titles/types for newly discovered neighbors ───────────────
            if neighbor_ids:
                np_placeholders = ",".join(f":np{i}" for i in range(len(neighbor_ids)))
                np_binds: dict[str, object] = {f"np{i}": nid for i, nid in enumerate(neighbor_ids)}
                meta_sql = (
                    f"SELECT CAST(id AS TEXT) AS id, title, type FROM pages "  # noqa: S608
                    f"WHERE deleted_at IS NULL AND CAST(id AS TEXT) IN ({np_placeholders})"
                )
                meta_rows = (await session.execute(_sa_text(meta_sql).bindparams(**np_binds))).all()
                for mr in meta_rows:
                    m = mr._mapping
                    nid = str(m["id"])
                    nodes_by_id[nid] = {"id": nid, "title": m["title"], "type": m["type"]}
                    visited.add(nid)

            frontier = next_frontier

    # De-duplicate edges by (source, target, relation)
    seen_edges: set[tuple[str, str, str]] = set()
    dedup_edges: list[dict[str, Any]] = []
    for e in edge_list:
        key = (e["source"], e["target"], e["relation"])
        if key not in seen_edges:
            seen_edges.add(key)
            dedup_edges.append(e)

    neighbor_nodes = [n for nid, n in nodes_by_id.items() if nid != seed_id]

    return {
        "center": center,
        "nodes": neighbor_nodes,
        "edges": dedup_edges,
    }


async def _list_reviews_body(
    status: str = "open",
    limit: int = 20,
    vault: str | None = None,
) -> list[dict[str, Any]]:
    """
    Return review queue items (id, type, title, status) for the default vault (B5/D2).

    READ-ONLY. Reuses ops.review.list_queue — the same seam the REST endpoint uses (I9).
    Cap limit ≤ 100 (I7).

    Args:
        status: "open" (alias for pending) | "pending" | "all" | other values accepted
                by ops.review.list_queue. Defaults to "open" (pending items).
        limit: Max items to return (default 20, cap 100 — I7).
        vault: Optional project id (W5, ADR-0082) — list a specific registered vault's queue
               instead of the currently active one. Omitted/unknown → active vault.

    Returns:
        list of {id, type, proposed_title, status}.
    """
    from app.ops.review import list_queue as _list_queue

    if limit < 1:
        limit = 1
    if limit > _MAX_REVIEW_LIMIT:
        limit = _MAX_REVIEW_LIMIT

    # "open" is a user-friendly alias for "pending"
    normalized_status = "pending" if status in ("open", "") else status
    vault_id, _ = _resolve_vault(vault)

    queue_page = await _list_queue(vault_id, limit=limit, offset=0, status=normalized_status)
    return [
        {
            "id": str(item.id),
            "type": item.item_type,
            "proposed_title": item.proposed_title,
            "status": item.status,
        }
        for item in queue_page.items
    ]


async def _read_source_file_body(path: str, vault: str | None = None) -> dict[str, Any]:
    """
    Return text content of a raw/sources/ file (B5/D2).

    READ-ONLY. Uses the same path-safety containment check as the REST /sources/content
    endpoint (I9), rooted at the *resolved* vault's raw/sources/ dir when ``vault`` is given
    (W5, ADR-0082) — app.upload.resolve_under_sources is bound to settings.vault_root, so a
    non-active vault's files are resolved locally against that vault's own raw_sources_dir
    using the identical containment logic.
    Caps content at _SOURCE_FILE_MAX_BYTES (2 MB) and returns only text-like files.
    Rejects path traversal (returned as a structured error dict).

    Args:
        path: Relative path from raw/sources/ (e.g. "subdir/file.md"). No leading slashes.
        vault: Optional project id (W5, ADR-0082) — read from a specific registered vault
               instead of the currently active one. Omitted/unknown → active vault.

    Returns:
        {"path": rel, "name": filename, "size_bytes": N, "content": "..."} on success.
        {"error": "..."} on not-found, traversal attempt, or binary file.
    """
    _, vault_root = _resolve_vault(vault)
    raw_dir = (vault_root / "raw" / "sources").resolve()

    # ── Path safety (mirrors app.upload.resolve_under_sources — ADR-0020 §2.2) ─────
    try:
        abs_path = (raw_dir / path).resolve()
        if abs_path != raw_dir and not str(abs_path).startswith(str(raw_dir) + "/"):
            return {
                "error": "unsafe or invalid path: filename escapes raw/sources/ after resolution"
            }
    except Exception as exc:  # noqa: BLE001
        return {"error": f"path resolution failed: {exc}"}

    if not abs_path.exists() or not abs_path.is_file():
        return {"error": f"source file not found: {path!r}"}

    # ── Binary guard — only serve text-like files ──────────────────────────────
    import mimetypes as _mimetypes

    ext = abs_path.suffix.lower()
    _TEXT_EXTS = frozenset(
        {
            ".txt",
            ".md",
            ".markdown",
            ".rst",
            ".tex",
            ".log",
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".ini",
            ".cfg",
            ".sh",
            ".bash",
            ".zsh",
            ".html",
            ".htm",
            ".css",
            ".xml",
            ".csv",
            ".tsv",
        }
    )
    if ext not in _TEXT_EXTS:
        guessed, _ = _mimetypes.guess_type(str(abs_path))
        if not (guessed and guessed.startswith("text/")):
            return {"error": f"file {path!r} is not a text-like source (binary/media not served)"}

    # ── Read with byte cap (I7 — bounded read) ────────────────────────────────
    try:
        raw = abs_path.read_bytes()
    except OSError as exc:
        return {"error": f"could not read file: {exc}"}

    truncated = len(raw) > _SOURCE_FILE_MAX_BYTES
    if truncated:
        raw = raw[:_SOURCE_FILE_MAX_BYTES]

    try:
        content = raw.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return {"error": f"could not decode file as UTF-8: {exc}"}

    return {
        "path": path,
        "name": abs_path.name,
        "size_bytes": abs_path.stat().st_size,
        "truncated": truncated,
        "content": content,
    }


async def _resolve_review_body(
    review_id: str,
    action: str,
    vault: str | None = None,
) -> dict[str, Any]:
    """
    Resolve one review item (WRITE — B5/D2).

    Delegates to ops.review.skip or ops.review.dismiss — the EXACT same functions the
    REST /review/queue/{id}/skip and /dismiss endpoints use (I9, no second writer).

    Action must be one of the exact tokens accepted by ops/review.py:
      "skip"    → status=skipped, resolution=skipped  (considered and declined)
      "dismiss" → status=dismissed, resolution=dismissed  (hide without acting)

    Note: "create" (lazy page generation) requires the AI orchestration loop and is NOT
    supported via MCP (use REST POST /review/queue/{id}/create instead).
    Note: "deep-research" requires SearXNG and fires a background run — use REST.

    Returns a structured dict (not exception) so the agent can retry on error.

    Args:
        review_id: UUID string of the review item.
        action: "skip" | "dismiss" — exact resolution token from ops/review.py.
        vault: Optional project id (W5, ADR-0082). Only the currently ACTIVE vault accepts
               writes (Model A) — a different vault id returns a structured error. Omitted
               → active vault (unchanged behaviour).

    Returns:
        {"id": review_id, "status": new_status, "action": action, "proposed_title": ...}
        {"error": "..."} on unknown action, invalid UUID, item not found, or failure.
    """
    guard_error = _vault_write_guard(vault)
    if guard_error is not None:
        return guard_error

    import uuid as _uuid

    from app.ops.review import dismiss as _dismiss
    from app.ops.review import skip as _skip

    # Accept ONLY the exact status-write actions from ops/review.py (I9 — no invented tokens)
    _ALLOWED_ACTIONS = frozenset({"skip", "dismiss"})
    if action not in _ALLOWED_ACTIONS:
        return {
            "error": (
                f"unknown action {action!r}; resolve_review accepts: "
                f"{sorted(_ALLOWED_ACTIONS)}. "
                "For 'create' use REST POST /review/queue/{id}/create; "
                "for 'deep-research' use REST POST /review/queue/{id}/deep-research."
            )
        }

    # Parse review_id — must be a valid UUID
    try:
        item_uuid = _uuid.UUID(review_id)
    except (ValueError, AttributeError):
        return {"error": f"invalid review_id {review_id!r} — must be a UUID string"}

    try:
        if action == "skip":
            item = await _skip(item_uuid)
        else:  # dismiss
            item = await _dismiss(item_uuid)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"resolve_review failed: {exc}"}

    return {
        "id": str(item.id),
        "status": item.status,
        "action": action,
        "proposed_title": item.proposed_title,
    }


async def _trigger_source_rescan_body(vault: str | None = None) -> dict[str, Any]:
    """
    Kick the incremental sources ingest-all scan (WRITE — B5/D2, I1).

    Delegates to the SAME internal seam that POST /sources/ingest-all uses (I9):
      - Calls _collect_ingest_all_candidates to find new/changed files.
      - Fires _ingest_all_driver as a fire-and-forget asyncio.Task (single-flight).
      - Uses the mtime-then-hash incremental gate in ingest_file — NEVER a full rescan (I1).

    Returns {"started": bool, "candidate_files": N} matching IngestAllResponse.
    Returns {"error": "..."} if already running (single-flight guard) or on any failure.
    The caller should poll GET /sources/ingest-all/status for progress.

    Args:
        vault: Optional project id (W5, ADR-0082). Only the currently ACTIVE vault can be
               rescanned (the watcher/ingest driver runs against the active vault_root) — a
               different vault id returns a structured error. Omitted → active vault
               (unchanged behaviour).

    Returns:
        {"started": bool, "candidate_files": N} on success.
        {"error": "..."} if already running or on any failure.
    """
    guard_error = _vault_write_guard(vault)
    if guard_error is not None:
        return guard_error

    import asyncio as _asyncio

    try:
        import app.sources as _sources
        from app.config import settings as _settings

        if _sources._ingest_all_running:
            return {
                "error": (
                    "ingest-all is already running; "
                    "poll GET /sources/ingest-all/status for progress"
                )
            }

        sources_dir = _settings.raw_sources_dir
        candidates = _sources._collect_ingest_all_candidates(
            sources_dir, _sources.SOURCES_INGEST_ALL_MAX
        )

        if not candidates:
            return {"started": False, "candidate_files": 0}

        # Arm counters before creating the task (same as the REST endpoint)
        _sources._ingest_all_running = True
        _sources._ingest_all_done = 0
        _sources._ingest_all_total = len(candidates)

        # Fire-and-forget (identical to POST /sources/ingest-all)
        _t = _asyncio.create_task(_sources._ingest_all_driver(candidates))
        _bg_tasks.add(_t)
        _t.add_done_callback(_bg_tasks.discard)

        return {"started": True, "candidate_files": len(candidates)}
    except Exception as exc:  # noqa: BLE001
        logger.error("trigger_source_rescan MCP tool: %s", exc)
        return {"error": f"trigger_source_rescan failed: {exc}"}


# ── New tools (stdio mcp): graph neighborhood, reviews, source, rescan (B5/D2) ─


@mcp.tool()
async def get_graph_neighborhood(
    title: str, depth: int = 1, vault: str | None = None
) -> dict[str, Any]:
    """
    Return a wiki page and its 1–2 hop neighbors from the persisted graph (B5/D2, I2).

    READ-ONLY. Reads the pre-computed edges/links tables — never triggers FA2 layout
    recompute (I2). Depth is capped at 2 (I7).

    Args:
        title: Exact page title (case-sensitive).
        depth: BFS hops — 1 (immediate neighbors) or 2 (two-hop expansion). Capped at 2 (I7).
        vault: Optional project id (W5, ADR-0082) — read from a specific registered vault
               instead of the currently active one. Omitted/unknown → active vault.

    Returns:
        {"center": {id, title, type}, "nodes": [{id, title, type}],
         "edges": [{source, target, relation}]}
        or {"error": "..."} if the page is not found.
    """
    return await _get_graph_neighborhood_body(title, depth, vault)


@mcp.tool()
async def list_reviews(
    status: str = "open", limit: int = 20, vault: str | None = None
) -> list[dict[str, Any]]:
    """
    List HITL review queue items (B5/D2, F9).

    READ-ONLY. Reuses the same list_queue seam as GET /review/queue (I9).
    limit capped at 100 (I7 — bounded list).

    Args:
        status: "open"/"pending" (default) | "resolved" | "dismissed" | "all".
        limit: Max items to return (1..100). Default 20.
        vault: Optional project id (W5, ADR-0082) — list a specific registered vault's
               queue instead of the currently active one. Omitted/unknown → active vault.

    Returns:
        list of {id, type, proposed_title, status}.
    """
    return await _list_reviews_body(status, limit, vault)


@mcp.tool()
async def read_source_file(path: str, vault: str | None = None) -> dict[str, Any]:
    """
    Read a raw/sources/ file as text (B5/D2).

    READ-ONLY. Confined to raw/sources/ via the same path-safety resolver as
    GET /sources/content (I9). Binary/media files and path-traversal attempts are rejected.
    Content capped at 2 MB (I7).

    Args:
        path: Relative path from raw/sources/ (e.g. "notes/file.md"). No leading slash.
              Absolute paths, ".." traversal, and paths outside raw/sources/ are rejected.
        vault: Optional project id (W5, ADR-0082) — read from a specific registered vault
               instead of the currently active one. Omitted/unknown → active vault.

    Returns:
        {"path": rel, "name": filename, "size_bytes": N, "truncated": bool, "content": "..."}
        or {"error": "..."} on not-found, traversal, or binary file.
    """
    return await _read_source_file_body(path, vault)


@mcp.tool()
async def resolve_review(review_id: str, action: str, vault: str | None = None) -> dict[str, Any]:
    """
    Resolve one HITL review item (WRITE — B5/D2, F9).

    Routes through the exact ops.review functions that the REST endpoints use (I9).
    Only accepts the two status-write actions from ops/review.py:
      "skip"    → status=skipped (considered and declined)
      "dismiss" → status=dismissed (hide without acting)

    For lazy page generation use REST POST /review/queue/{id}/create.
    For deep-research use REST POST /review/queue/{id}/deep-research.

    Args:
        review_id: UUID string of the review item.
        action: "skip" | "dismiss" — exact token from ops/review.py.
        vault: Optional project id (W5, ADR-0082). Only the currently ACTIVE vault accepts
               writes (Model A) — a different id returns a structured error. Omitted →
               active vault (unchanged behaviour).

    Returns:
        {"id": review_id, "status": new_status, "action": action, "proposed_title": ...}
        or {"error": "..."} on unknown action, invalid UUID, item not found, or failure.
    """
    return await _resolve_review_body(review_id, action, vault)


@mcp.tool()
async def trigger_source_rescan(vault: str | None = None) -> dict[str, Any]:
    """
    Kick the incremental raw/sources/ ingest scan (WRITE — B5/D2, I1).

    Uses the mtime-then-hash incremental gate — never a full rescan (I1).
    Fires a bounded fire-and-forget asyncio.Task; poll GET /sources/ingest-all/status
    for progress. Single-flight: returns error if already running.

    Args:
        vault: Optional project id (W5, ADR-0082). Only the currently ACTIVE vault can be
               rescanned — a different id returns a structured error. Omitted → active
               vault (unchanged behaviour).

    Returns:
        {"started": bool, "candidate_files": N} on success.
        {"error": "..."} if already running or on any failure.
    """
    return await _trigger_source_rescan_body(vault)


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


def build_http_mcp(
    *,
    write_enabled: bool = False,
    write_enabled_getter: Callable[[], bool] | None = None,
) -> FastMCP:
    """
    Build a FastMCP instance for the /mcp/server HTTP surface (ADR-0029 §2.3).

    Returns a *separate* FastMCP instance that registers the read-only tools
    (search_wiki, get_page, list_pages, get_graph_neighborhood, list_reviews,
    read_source_file) always, plus write_page / resolve_review / trigger_source_rescan
    as controlled by the write policy below. All tool bodies delegate to the SAME
    underlying ``_*_body`` functions used by the stdio ``mcp`` — DRY, single write path
    enforced (ADR-0010 §2, I1/I5, B5/D2).

    The stdio ``mcp`` module-level object is NEVER modified by this function.

    Write policy (ADR-0029 §2.3 / ADR-0072 §3):
        write_enabled_getter is not None  →  ALWAYS register write tools; each body
            checks ``write_enabled_getter()`` at call time and returns a structured
            ``{"error": "remote writes are disabled; enable them in Settings → API & MCP"}``
            when it returns False, else delegates to the shared ``_*_body``.
            This is the "always-register-guard" model (ADR-0072 §3): tools are always
            *listed* on the HTTP surface (discovery shows them) but mutate only when the
            runtime flag is ON. ``mcp/server.py`` MUST NOT import ``app.main`` (circular);
            the getter is injected as a closure from ``main.py``.
        write_enabled_getter is None  →  static behaviour: register write tools iff
            ``write_enabled`` is True (backward-compatible legacy path for tests and
            direct callers).

    Args:
        write_enabled: Static write gate — used only when ``write_enabled_getter`` is
                       None (legacy/backward-compat path). Default False.
        write_enabled_getter: Runtime getter injected by ``main.py`` (ADR-0072 §3).
                              When not None, takes precedence; ``write_enabled`` is ignored.

    Returns:
        A configured FastMCP instance ready for ``http_app()`` mounting.
    """
    # Decide instruction text and static-registration mode.
    _use_getter: bool = write_enabled_getter is not None
    # When getter is used: write tools are always listed; instructions always include them.
    # When getter is None: honour write_enabled statically (legacy).
    _include_write_instructions: bool = _use_getter or write_enabled

    http_mcp = FastMCP(
        name="synapse-http",
        instructions=(
            "Synapse remote wiki tools (HTTP/Streamable-HTTP, ADR-0029). "
            "Use search_wiki to find relevant pages. "
            "Use get_page / list_pages for read access. "
            "Use get_graph_neighborhood to explore page connections. "
            "Use list_reviews to inspect the HITL review queue (F9). "
            "Use read_source_file to read raw source files. "
            + (
                "Use write_page to create or update wiki pages "
                "(validation + frontmatter enforced). "
                "Use resolve_review to skip/dismiss a review item. "
                "Use trigger_source_rescan to re-index raw/sources/. "
                if _include_write_instructions
                else ""
            )
        ),
    )

    # ── Read-only tools (always present on the HTTP surface) ──────────────────

    @http_mcp.tool()
    async def search_wiki(
        query: str, k: int = 5, vault: str | None = None
    ) -> list[dict[str, Any]]:  # noqa: F811
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
            vault: Optional project id (W5, ADR-0082) — search a specific registered vault
                   instead of the active one. Omitted → active vault.

        Returns:
            list of {id, title, type, relevance_score}.
        """
        return await _search_wiki_body(query, k, vault)

    @http_mcp.tool()
    async def get_page(title: str, vault: str | None = None) -> dict[str, Any]:  # noqa: F811
        """
        Retrieve a live wiki page by title.

        Returns {title, type, content, frontmatter} on success, or {"error": "..."} if the
        page is not found or has been soft-deleted.

        Args:
            title: Exact page title (case-sensitive).
            vault: Optional project id (W5, ADR-0082) — read from a specific registered
                   vault instead of the active one. Omitted → active vault.

        Returns:
            {title, type, content, frontmatter} or {"error": "<message>"}.
        """
        return await _get_page_body(title, vault)

    @http_mcp.tool()
    async def list_pages(
        type: str | None = None, vault: str | None = None
    ) -> list[dict[str, Any]]:  # noqa: F811
        """
        List live wiki pages, optionally filtered by page type.

        Excludes soft-deleted pages (deleted_at IS NOT NULL). Results are sorted by title.

        Args:
            type: Optional page type filter (entity/concept/source/synthesis/comparison).
                  Passing None returns all live pages.
            vault: Optional project id (W5, ADR-0082) — list a specific registered vault
                   instead of the active one. Omitted → active vault.

        Returns:
            list of {id, title, type, relevance_score: 0.0}.
        """
        return await _list_pages_body(type, vault)

    # ── New read-only tools (always present on the HTTP surface — B5/D2) ────────

    @http_mcp.tool()
    async def get_graph_neighborhood(
        title: str, depth: int = 1, vault: str | None = None
    ) -> dict[str, Any]:  # noqa: F811
        """
        Return a wiki page and its 1–2 hop neighbors from the persisted graph (B5/D2, I2).

        READ-ONLY. Reads pre-computed edges/links — never triggers FA2 recompute (I2).

        Args:
            title: Exact page title (case-sensitive).
            depth: BFS hops (1 or 2). Capped at 2 (I7).
            vault: Optional project id (W5, ADR-0082) — read from a specific registered
                   vault instead of the active one. Omitted → active vault.

        Returns:
            {"center": {id, title, type}, "nodes": [...], "edges": [...]}
            or {"error": "..."} if not found.
        """
        return await _get_graph_neighborhood_body(title, depth, vault)

    @http_mcp.tool()
    async def list_reviews(
        status: str = "open", limit: int = 20, vault: str | None = None
    ) -> list[dict[str, Any]]:  # noqa: F811
        """
        List HITL review queue items (B5/D2, F9). READ-ONLY. limit capped at 100 (I7).

        Args:
            status: "open"/"pending" (default) | "resolved" | "dismissed" | "all".
            limit: Max items (1..100).
            vault: Optional project id (W5, ADR-0082) — list a specific registered vault's
                   queue instead of the active one. Omitted → active vault.

        Returns:
            list of {id, type, proposed_title, status}.
        """
        return await _list_reviews_body(status, limit, vault)

    @http_mcp.tool()
    async def read_source_file(path: str, vault: str | None = None) -> dict[str, Any]:  # noqa: F811
        """
        Read a raw/sources/ file as text (B5/D2). READ-ONLY. Confined to raw/sources/;
        binary files and path-traversal attempts are rejected. Cap 2 MB (I7).

        Args:
            path: Relative path from raw/sources/ (e.g. "notes/file.md").
            vault: Optional project id (W5, ADR-0082) — read from a specific registered
                   vault instead of the active one. Omitted → active vault.

        Returns:
            {"path", "name", "size_bytes", "truncated", "content"}
            or {"error": "..."}.
        """
        return await _read_source_file_body(path, vault)

    # ── Write tools (ADR-0029 §2.3 / ADR-0072 §3) ───────────────────────────────
    #
    # Two registration models:
    #   1. Getter model (_use_getter=True): always register; guard at call time.
    #      write_enabled_getter() is checked inside each body — returns structured
    #      {"error": "..."} when off (consistent with every other tool-body error contract).
    #      mcp/server.py MUST NOT import main.py; getter is injected by main.py.
    #   2. Static model (_use_getter=False): register iff write_enabled (legacy/tests).
    #
    if _use_getter or write_enabled:

        @http_mcp.tool()
        async def write_page(  # noqa: F811
            title: str,
            content: str,
            frontmatter: dict[str, Any],
            origin_source: str = "",
            vault: str | None = None,
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
                vault:         Optional project id (W5, ADR-0082). Only the currently ACTIVE
                       vault accepts writes (Model A) — a different id returns a structured
                       error. Omitted → active vault (unchanged behaviour).

            Returns:
                {"id", "title", "type", "relevance_score": 0.0} on success.
                {"error": "<message>"} on validation failure or when write flag is off.
            """
            # ADR-0072 §3: runtime guard — check getter at call time (not at registration).
            if _use_getter and write_enabled_getter is not None and not write_enabled_getter():
                return {"error": "remote writes are disabled; enable them in Settings → API & MCP"}
            return await _write_page_body(title, content, frontmatter, origin_source, vault)

        @http_mcp.tool()
        async def resolve_review(
            review_id: str, action: str, vault: str | None = None
        ) -> dict[str, Any]:  # noqa: F811
            """
            Resolve one HITL review item (WRITE — B5/D2, F9).

            Action must be "skip" or "dismiss" (exact tokens from ops/review.py).
            For lazy page generation use REST POST /review/queue/{id}/create.

            Args:
                review_id: UUID string of the review item.
                action: "skip" | "dismiss".
                vault: Optional project id (W5, ADR-0082). Only the active vault accepts
                       writes — a different id returns a structured error.

            Returns:
                {"id", "status", "action", "proposed_title"} or {"error": "..."}.
            """
            # ADR-0072 §3: runtime guard — check getter at call time (not at registration).
            if _use_getter and write_enabled_getter is not None and not write_enabled_getter():
                return {"error": "remote writes are disabled; enable them in Settings → API & MCP"}
            return await _resolve_review_body(review_id, action, vault)

        @http_mcp.tool()
        async def trigger_source_rescan(vault: str | None = None) -> dict[str, Any]:  # noqa: F811
            """
            Kick the incremental raw/sources/ ingest scan (WRITE — B5/D2, I1).

            Uses mtime-then-hash incremental gate — never a full rescan (I1). Single-flight.

            Args:
                vault: Optional project id (W5, ADR-0082). Only the active vault can be
                       rescanned — a different id returns a structured error.

            Returns:
                {"started": bool, "candidate_files": N} or {"error": "..."}.
            """
            # ADR-0072 §3: runtime guard — check getter at call time (not at registration).
            if _use_getter and write_enabled_getter is not None and not write_enabled_getter():
                return {"error": "remote writes are disabled; enable them in Settings → API & MCP"}
            return await _trigger_source_rescan_body(vault)

    return http_mcp


# ── In-process SDK MCP server factory (claude-agent-sdk, ADR-0010 §2) ──────────


def build_sdk_mcp_server(origin_source: str = "", generation_key: str | None = None) -> Any:
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

    Args:
        origin_source: When provided (non-empty), this value is bound into the ``write_page``
                       tool as the authoritative origin source path for K6/F3/F13 traceability.
                       The bound value wins over whatever the CLI agent passes in the tool call
                       (``effective = bound_origin_source or tool_arg``).  This prevents the
                       agent from omitting or misdescribing the raw file path — it is stamped
                       server-side.  When empty (default, standalone/global MCP server), the
                       tool-arg behaviour is unchanged.
        generation_key: Optional reserved corpus identity bound by an accepted Review proposal.
                        When present, the server injects it into frontmatter before validation so
                        delegated providers cannot omit or alter the idempotency key.

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
        # K6/F3/F13 traceability: bound origin_source (set at build time from the delegated
        # ingest run) wins over whatever the CLI agent passes.  When no bound value was set
        # (origin_source="" — standalone / global MCP), fall back to the tool-arg so the
        # external-MCP / stdio path is unchanged.
        tool_arg = args.get("origin_source", "") or ""
        effective_origin = origin_source or tool_arg
        effective_frontmatter = dict(args["frontmatter"])
        if generation_key is not None:
            effective_frontmatter["synapse_generation_key"] = generation_key
        return _wrap(
            await _write_page_body(
                args["title"],
                args["content"],
                effective_frontmatter,
                effective_origin,
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
