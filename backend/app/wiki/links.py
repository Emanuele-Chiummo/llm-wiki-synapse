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
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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
    # file_path basename slug → id. The generation prompt mandates bare-slug wikilinks
    # ([[multi-cloud-orchestration]]); a page is FILED under that slug but TITLED descriptively
    # (often in another language), so _slugify(title) never reproduces the linked slug. Indexing the
    # filename slug is what actually reconnects [[slug]] links into graph edges (F4). Defaulted so
    # existing direct constructors / test fakes keep working.
    by_fileslug: dict[str, uuid.UUID] = field(default_factory=dict)


async def _build_resolver_maps(session: AsyncSession, vault_id: str) -> _ResolverMaps:
    """
    Build exact / case-insensitive / slug lookup maps over the live pages OF *vault_id* in ONE
    query.

    VAULT-SCOPED (bugfix): the maps must contain only the target vault's pages. When multiple
    vaults share page slugs (e.g. the same sources ingested into several vaults), a global map
    would resolve ``[[some-slug]]`` to whichever vault's page was inserted first — pointing the
    Link.target_page_id cross-vault, which then produces NO graph edge (the target isn't a node in
    the source vault's graph) and collapses the knowledge graph. Scope to the source vault so the
    link resolves to that vault's own page.

    First-hit-wins for the lossy (lower/slug) maps: when two live titles collapse to the same
    lower/slug key we keep the first seen and do NOT overwrite. This is conservative — it never
    invents an ambiguous edge; the exact map always wins at resolution time anyway.
    """
    result = await session.execute(
        select(Page.id, Page.title, Page.file_path).where(
            Page.vault_id == vault_id,
            Page.deleted_at.is_(None),
            Page.title.is_not(None),
        )
    )
    by_title: dict[str, uuid.UUID] = {}
    by_lower: dict[str, uuid.UUID] = {}
    by_slug: dict[str, uuid.UUID] = {}
    by_fileslug: dict[str, uuid.UUID] = {}
    for row in result.all():
        # Attribute access (row.id/row.title) works for both SQLAlchemy Row and test fakes.
        pid = row.id
        title = row.title
        if title is None:
            continue
        by_title.setdefault(title, pid)
        by_lower.setdefault(title.lower(), pid)
        by_slug.setdefault(_slugify(title), pid)
        # Index the filename slug too — the identifier [[wikilinks]] actually use. getattr keeps
        # test fakes that only expose id/title working (they contribute no fileslug entry).
        file_path = getattr(row, "file_path", None)
        if file_path:
            stem = file_path.rsplit("/", 1)[-1]
            if stem.endswith(".md"):
                stem = stem[:-3]
            fileslug = _slugify(stem)
            if fileslug:
                by_fileslug.setdefault(fileslug, pid)
    return _ResolverMaps(
        by_title=by_title, by_lower=by_lower, by_slug=by_slug, by_fileslug=by_fileslug
    )


def _resolve_target(target: str, maps: _ResolverMaps) -> uuid.UUID | None:
    """
    Resolve a [[Target]] to a live page id using a fixed, conservative precedence:

        1. exact Page.title match                    (unchanged historical behavior)
        2. case-insensitive lower(title) match       (catches "rag" vs "RAG")
        3. filename-slug match (_slugify(file stem) == _slugify(target))  (the slug [[links]] use)
        4. slug match (_slugify(title) == _slugify(target))  (catches punctuation/spacing drift)

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
    # filename-slug match — the model links by the page's file slug ([[multi-cloud-orchestration]]),
    # which _slugify(title) does not reproduce when the title is descriptive / localized (F4).
    hit = maps.by_fileslug.get(_slugify(target))
    if hit is not None:
        return hit
    return maps.by_slug.get(_slugify(target))


# ── Fuzzy broken-link suggestion (L2b — port of lint.ts suggestBrokenTarget) ─────
#
# The exact→case→slug resolver above already ran and MISSED (that is why the link is
# dangling). llm_wiki then offers a *repair suggestion* via a typo-tolerant score
# (Levenshtein over the basename, plus same-basename / substring shortcuts). This is a
# SUGGESTION ONLY — it never creates a graph edge, so a wrong guess cannot pollute the
# graph; it just pre-fills the "Rewrite [[x]] → [[y]]" fix for human review. Threshold and
# scores are copied verbatim from src/lib/lint.ts so behaviour matches 1:1.
_BROKEN_LINK_SUGGESTION_MIN_SCORE: float = 0.74
_SAME_BASENAME_SCORE: float = 0.96
_CONTAINS_TARGET_SCORE: float = 0.82
_FUZZY_MIN_BASENAME_LEN: int = 5  # below this, Levenshtein is too noisy (llm_wiki parity)


def _normalize_link_target(target: str) -> str:
    """Port of lint.ts normalizeLinkTarget: drop a leading ``wiki/`` and ``.md``, lower, trim."""
    value = target.replace("\\", "/").strip()
    value = re.sub(r"^wiki/", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\.md$", "", value, flags=re.IGNORECASE)
    return value.strip().lower()


def _basename(value: str) -> str:
    """Port of path-utils.getFileName over a normalized target (segment after the last ``/``)."""
    return value.split("/")[-1] if value else value


def _levenshtein(a: str, b: str) -> int:
    """Iterative Levenshtein edit distance (two-row) — direct port of lint.ts levenshtein."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previous = list(range(len(b) + 1))
    current = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        current[0] = i
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            current[j] = min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + cost)
        previous = current[:]
    return previous[len(b)]


def _string_similarity(a: str, b: str) -> float:
    """
    Typo-tolerant similarity in ``[0, 1]`` — verbatim port of lint.ts stringSimilarity.

    Exact-normalized → 1.0; same basename → 0.96; substring containment → 0.82; otherwise
    ``1 - levenshtein(base_a, base_b) / max_len`` once both basenames are long enough to trust.
    """
    left = _normalize_link_target(a)
    right = _normalize_link_target(b)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_base = _basename(left)
    right_base = _basename(right)
    if left_base == right_base:
        return _SAME_BASENAME_SCORE
    if right.find(left) != -1 or left.find(right) != -1:
        return _CONTAINS_TARGET_SCORE
    if len(left_base) < _FUZZY_MIN_BASENAME_LEN or len(right_base) < _FUZZY_MIN_BASENAME_LEN:
        return 0.0
    max_len = max(len(left_base), len(right_base))
    if max_len == 0:
        return 0.0
    return 1.0 - _levenshtein(left_base, right_base) / max_len


def _fuzzy_suggest_target(target: str, maps: _ResolverMaps) -> tuple[uuid.UUID, str] | None:
    """
    Best typo-tolerant repair candidate for a dangling *target*, or None below threshold.

    Scores *target* against every live page's title AND its slug (mirroring llm_wiki, which
    scores against slug/shortName/title) and keeps the highest. Returns ``(page_id, title)``
    only when the best score clears ``_BROKEN_LINK_SUGGESTION_MIN_SCORE`` (0.74). Pure/in-memory:
    reuses the maps already built for the exact resolver, so no extra query (I1).
    """
    best_id: uuid.UUID | None = None
    best_title: str | None = None
    best_score = 0.0
    for title, pid in maps.by_title.items():
        score = max(
            _string_similarity(target, title),
            _string_similarity(target, _slugify(title)),
        )
        if score > best_score:
            best_score = score
            best_id = pid
            best_title = title
    if best_id is None or best_title is None or best_score < _BROKEN_LINK_SUGGESTION_MIN_SCORE:
        return None
    return best_id, best_title


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
    #    Scope to the ACTIVE vault (bugfix): a cross-vault slug collision must not steal the target
    #    and drop the graph edge. Ingest/reresolve always run for settings.vault_id.
    maps = await _build_resolver_maps(session, settings.vault_id)

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


# ── Tolerant title resolution (shared helper — L2 / ADR-0037 B1) ─────────────────
#
# Used by both the broken-wikilink scan (to compute suggested_target / suggested_page_id)
# and the reresolve_dangling_links backfill.  Extracted so the caller never needs to rebuild
# the resolver maps independently (I1 — one bulk query, no N+1).


async def resolve_suggested_target(
    target: str,
    session: AsyncSession,
) -> tuple[uuid.UUID, str] | None:
    """
    Resolve *target* to the best-matching live page using the tolerant 3-step matcher
    (exact → case-insensitive → slug), then a typo-tolerant fuzzy fallback (L2b).  Returns
    ``(page_id, matched_title)`` or ``None`` when nothing clears the fuzzy threshold.

    Builds the resolver maps in ONE query (I1 — no N+1).  Scoped to ALL live pages
    (vault-agnostic, matching the behaviour of persist_links and reresolve_dangling_links).

    Used by the broken-wikilink scan (L2) to compute suggested_target/suggested_page_id.
    A dangling link means the exact matcher already missed, so the fuzzy fallback is what
    actually surfaces "did you mean [[Transformer]]?" re-point suggestions (llm_wiki
    suggestBrokenTarget parity) instead of forcing a stub page for every typo.
    """
    maps = await _build_resolver_maps(session, settings.vault_id)
    hit = _resolve_target(target, maps)
    if hit is None:
        # L2b — exact/case/slug all missed; try the typo-tolerant fuzzy repair candidate.
        return _fuzzy_suggest_target(target, maps)
    # Reverse-look-up the canonical title from the exact map (first-hit-wins, conservative).
    for title, pid in maps.by_title.items():
        if pid == hit:
            return hit, title
    # Fallback: scan by_lower and by_slug to find a displayable title when exact missed.
    for title, pid in maps.by_lower.items():
        if pid == hit:
            # Recover the canonical-case title from by_title (same id).
            canonical = next(
                (t for t, p in maps.by_title.items() if p == hit),
                title,
            )
            return hit, canonical
    return hit, target  # last resort — return the raw target as the "title"


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

    maps = await _build_resolver_maps(session, settings.vault_id)

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
