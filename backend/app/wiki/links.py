"""
K5 — wikilink parser and persistence layer (ADR-0008 §5, CLAUDE.md §K5).

Provides:
    parse_wikilinks(markdown)  →  list[ParsedLink]
    persist_links(session, source_page_id, parsed_links)  →  None

Parser handles:
    [[Target]]            → ParsedLink(target="Target", alias=None)
    [[Target|alias]]      → ParsedLink(target="Target", alias="alias")
    [[Target#section]]    → ParsedLink(target="Target", alias=None)  (section stripped)
    Nested brackets and malformed syntax are ignored gracefully.

Persistence (I1 — incremental, not a rescan):
    - Deletes all existing Link rows for source_page_id.
    - Re-inserts one Link row per parsed wikilink.
    - Sets dangling=True when no live Page with title==target_title exists.
    - Dangling links do NOT invalidate a batch (AQ-v0.2-7, ADR-0007 §5).

Called by write_wiki_page() in orchestrator.py after the Page row is committed (K5).
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.orchestrator import _slugify
from app.models import Link, Page

logger = logging.getLogger(__name__)

# Regex: match [[...]] — non-greedy inner match, exclude nested [[.
# Captures the full inner text (e.g. "Target", "Target|alias", "Target#section|alias").
_WIKILINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")


# ── Public DTO ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedLink:
    """One resolved wikilink from a markdown page."""

    target: str  # the title part of [[Target|alias]] (section stripped)
    alias: str | None  # the alias part, or None if absent


# ── Parser ─────────────────────────────────────────────────────────────────────


def parse_wikilinks(markdown: str) -> list[ParsedLink]:
    """
    Extract all [[wikilinks]] from *markdown* and return ParsedLink objects.

    Syntax handled:
        [[Target]]            → target="Target", alias=None
        [[Target|alias]]      → target="Target", alias="alias"
        [[Target#section]]    → target="Target" (section stripped), alias=None
        [[Target#sec|alias]]  → target="Target", alias="alias"
        Empty or blank-only targets are silently skipped (defensive).

    The parser is read-only; it never touches Postgres.
    """
    results: list[ParsedLink] = []
    seen: set[tuple[str, str | None]] = set()

    for m in _WIKILINK_RE.finditer(markdown):
        inner = m.group(1).strip()
        if not inner:
            continue

        # Split on | for alias
        if "|" in inner:
            target_part, alias_part = inner.split("|", 1)
            alias: str | None = alias_part.strip() or None
        else:
            target_part = inner
            alias = None

        # Strip #section fragment from target
        target = target_part.split("#", 1)[0].strip()
        if not target:
            continue

        # Deduplicate within this page (same target+alias pair)
        key = (target, alias)
        if key in seen:
            continue
        seen.add(key)

        results.append(ParsedLink(target=target, alias=alias))

    return results


# ── Tolerant target → page resolution (F3/K3 cross-ingest connectivity) ─────────


@dataclass(frozen=True)
class _ResolverMaps:
    """Three lookup maps over live pages, built in one bulk query (avoids N+1)."""

    by_title: dict[str, uuid.UUID]  # exact Page.title → id
    by_lower: dict[str, uuid.UUID]  # lower(title) → id (first-hit-wins)
    by_slug: dict[str, uuid.UUID]  # _slugify(title) → id (first-hit-wins)


async def _build_resolver_maps(session: AsyncSession) -> _ResolverMaps:
    """
    Build exact / case-insensitive / slug lookup maps over ALL live pages in ONE query.

    First-hit-wins for the lossy (lower/slug) maps: when two live titles collapse to the same
    lower/slug key we keep the first seen and do NOT overwrite. This is conservative — it never
    invents an ambiguous edge; the exact map always wins at resolution time anyway.
    """
    result = await session.execute(
        select(Page.id, Page.title).where(
            Page.deleted_at.is_(None),
            Page.title.is_not(None),
        )
    )
    by_title: dict[str, uuid.UUID] = {}
    by_lower: dict[str, uuid.UUID] = {}
    by_slug: dict[str, uuid.UUID] = {}
    for row in result.all():
        # Attribute access (row.id/row.title) works for both SQLAlchemy Row and test fakes.
        pid = row.id
        title = row.title
        if title is None:
            continue
        by_title.setdefault(title, pid)
        by_lower.setdefault(title.lower(), pid)
        by_slug.setdefault(_slugify(title), pid)
    return _ResolverMaps(by_title=by_title, by_lower=by_lower, by_slug=by_slug)


def _resolve_target(target: str, maps: _ResolverMaps) -> uuid.UUID | None:
    """
    Resolve a [[Target]] to a live page id using a fixed, conservative precedence:

        1. exact Page.title match                    (unchanged historical behavior)
        2. case-insensitive lower(title) match       (catches "rag" vs "RAG")
        3. slug match (_slugify(title) == _slugify(target))  (catches punctuation/spacing drift)

    Exact-first is deliberate: it guarantees we never demote a real title to a fuzzy match. We
    stop at the first hit and only fall through to the looser maps when the stricter one misses,
    so unrelated pages are never linked (over-linking would create false graph edges). Returns
    None when none of the three match → caller marks the link dangling.
    """
    hit = maps.by_title.get(target)
    if hit is not None:
        return hit
    hit = maps.by_lower.get(target.lower())
    if hit is not None:
        return hit
    return maps.by_slug.get(_slugify(target))


# ── Persistence ────────────────────────────────────────────────────────────────


async def persist_links(
    session: AsyncSession,
    source_page_id: uuid.UUID,
    parsed_links: list[ParsedLink],
) -> None:
    """
    Upsert wikilink rows for *source_page_id* (incremental, I1).

    Algorithm:
        1. DELETE existing Link rows for source_page_id (clean slate per write event).
        2. For each ParsedLink, resolve target_page_id by title lookup in live pages.
        3. INSERT Link rows; dangling=True when target_page_id is None.

    Dangling links are stored and logged at DEBUG level — they do NOT raise or invalidate
    (AQ-v0.2-7 / ADR-0007 §5). The session must be flushed/committed by the caller
    (write_wiki_page in orchestrator.py manages its own session).
    """
    # 1. Delete previous link rows for this page (idempotent per write event).
    await session.execute(delete(Link).where(Link.source_page_id == source_page_id))

    if not parsed_links:
        return

    # 2. Bulk-build the tolerant resolver maps in ONE query over live pages (F3/K3, no N+1).
    #    Resolution precedence is exact → case-insensitive → slug (see _resolve_target). This
    #    catches near-miss titles the ingest LLM invents so cross-ingest links form real edges
    #    instead of dangling — while staying conservative (exact-first, first-hit-wins).
    maps = await _build_resolver_maps(session)

    now = datetime.now(UTC)
    dangling_count = 0

    for pl in parsed_links:
        target_page_id = _resolve_target(pl.target, maps)
        dangling = target_page_id is None
        if dangling:
            dangling_count += 1

        session.add(
            Link(
                id=uuid.uuid4(),
                source_page_id=source_page_id,
                target_title=pl.target,
                target_page_id=target_page_id,
                alias=pl.alias,
                dangling=dangling,
                created_at=now,
            )
        )

    if dangling_count:
        logger.debug(
            "persist_links: %d dangling wikilinks from page %s (warn-not-error, K5)",
            dangling_count,
            source_page_id,
        )


# ── Backfill: re-resolve historical dangling links (F3/K3) ──────────────────────


async def reresolve_dangling_links(session: AsyncSession) -> int:
    """
    Re-resolve every dangling Link against the CURRENT live pages using the same tolerant
    matcher as persist_links (exact → case-insensitive → slug). For any dangling link whose
    target_title now maps to a live page, set target_page_id and clear dangling.

    Returns the number of links reconnected. Bounded single pass (I7): one query for the
    dangling rows + one query to build the resolver maps; no per-row DB round-trips. The caller
    commits and bumps the graph (main.py POST /links/reresolve).
    """
    result = await session.execute(select(Link).where(Link.dangling.is_(True)))
    dangling_links = list(result.scalars().all())
    if not dangling_links:
        return 0

    maps = await _build_resolver_maps(session)

    reconnected = 0
    for link in dangling_links:
        if not link.target_title:
            continue
        target_page_id = _resolve_target(link.target_title, maps)
        if target_page_id is not None:
            link.target_page_id = target_page_id
            link.dangling = False
            reconnected += 1

    logger.info(
        "reresolve_dangling_links: reconnected %d of %d dangling links (F3/K3 backfill)",
        reconnected,
        len(dangling_links),
    )
    return reconnected
