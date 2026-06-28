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

    # 2. Bulk-resolve target titles in one query.
    target_titles = [pl.target for pl in parsed_links]
    result = await session.execute(
        select(Page.id, Page.title).where(
            Page.title.in_(target_titles),
            Page.deleted_at.is_(None),
        )
    )
    title_to_id: dict[str, uuid.UUID] = {row.title: row.id for row in result.all()}

    now = datetime.now(UTC)
    dangling_count = 0

    for pl in parsed_links:
        target_page_id = title_to_id.get(pl.target)
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
